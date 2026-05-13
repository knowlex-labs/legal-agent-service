"""Image-only PDF translation: bbox-aware OCR + in-place text overlay.

Keeps the original page pixels (seals, stamps, signatures, letterhead) and
only replaces text bounding boxes with translated text rendered via MuPDF's
HTML engine — so layout, alignment, and decorative artwork survive.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from html import escape as _esc
from typing import TYPE_CHECKING

from legal_agent.agents.translation.glossary import (
    DocState,
    Glossary,
    freeze,
    localize_units,
    restore,
    strip_pua,
)
from legal_agent.agents.translation.sarvam_translate import (
    SARVAM_LANG_CODES,
    call_sarvam_translate,
    clean_sarvam_translate_output,
)
from legal_agent.config import get_settings

if TYPE_CHECKING:
    from legal_agent.models.requests import CreateTranslationJobRequest

logger = logging.getLogger(__name__)

_RASTER_SCALE = 2.0

_BBOX_PROMPT = (
    "You are a document layout analyser. Detect EVERY block of readable text in this page image. "
    "Be exhaustive — do not skip any line, including headers, footers, page numbers, "
    "signature blocks, address blocks, and form fields.\n"
    "Output ONLY a JSON array. Each item:\n"
    '  {"text": str, "box_2d": [ymin, xmin, ymax, xmax],'
    ' "bold": bool, "italic": bool, "size": "small"|"normal"|"large",'
    ' "align": "left"|"center"|"right"|"justify"}\n'
    "Coordinates are normalised 0-1000 from the top-left of the image.\n"
    "Rules:\n"
    "- GROUP every consecutive line that belongs to the SAME paragraph into ONE item, "
    "with a single bbox spanning all those lines. Do not emit one item per line. "
    "A new item starts at a clear paragraph break (blank line, indent change, numbered marker like \"2.\", \"(a)\", "
    "different alignment, or visibly different font weight/size).\n"
    "- Set bold=true for visually heavier strokes (titles, section headings, emphasised terms).\n"
    "- Set size=\"large\" for titles/headings noticeably bigger than body text; \"small\" for footnotes; "
    "otherwise \"normal\".\n"
    "- align: \"center\" for centered titles, \"right\" for right-flushed dates/numbers, "
    "\"justify\" for any body paragraph spanning more than one line whose lines reach both "
    "left and right margins (formal letters, legal clauses — default for multi-line body text), "
    "\"left\" only for single-line items or short flush-left headers.\n"
    "- Skip seals, stamps, logos, signatures, photographs, graphical icons — do NOT describe them.\n"
    "- Preserve numbers, dates, IDs, citations verbatim.\n"
    "- Preserve original script — do not translate.\n"
    "- No commentary, no markdown fences. Output a valid JSON array only."
)

_PLACEHOLDER_RE = re.compile(
    r"^\s*\[\s*(stamp|seal|image|logo|handwritten|figure|photo|picture|graphic|sign|emblem|signature)\b",
    re.IGNORECASE,
)


def is_image_only_pdf(data: bytes, min_chars_per_page: int = 50) -> bool:
    """True iff every page has < min_chars_per_page native text characters."""
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        for page in doc:
            d = page.get_text("dict", sort=True)
            n = sum(
                len(s.get("text", ""))
                for b in d.get("blocks", []) if b.get("type") == 0
                for ln in b.get("lines", [])
                for s in ln.get("spans", [])
            )
            if n >= min_chars_per_page:
                return False
        return True
    finally:
        doc.close()


def _strip_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _call_gemini(image_bytes: bytes, json_mode: bool) -> str:
    from google import genai
    from google.genai import types

    settings = get_settings()
    client = genai.Client(api_key=settings.gemini_api_key or "")
    config_kwargs: dict = {
        "temperature": 0.0,
        "max_output_tokens": settings.gemini_max_tokens,
    }
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    resp = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            _BBOX_PROMPT,
        ],
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return _strip_fence(resp.text or "")


def _parse_items(raw: str) -> list[dict] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        idx = raw.rfind("}")
        if idx == -1:
            return None
        try:
            parsed = json.loads(raw[: idx + 1] + "]")
        except json.JSONDecodeError:
            return None
    if isinstance(parsed, dict):
        for key in ("items", "regions", "blocks", "data"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return None
    return parsed


def _gemini_bboxes(image_bytes: bytes, page_label: str) -> list[dict]:
    raw = _call_gemini(image_bytes, json_mode=True)
    items = _parse_items(raw)
    if not items:
        logger.warning(
            "[overlay] %s: json_mode yielded %d items (raw_len=%d, head=%r). Retrying free-form.",
            page_label, 0 if items is None else len(items), len(raw), raw[:200],
        )
        raw = _call_gemini(image_bytes, json_mode=False)
        items = _parse_items(raw)
    if not items:
        logger.error(
            "[overlay] %s: both attempts produced no items (raw_len=%d, head=%r)",
            page_label, len(raw), raw[:400],
        )
        return []
    logger.info("[overlay] %s: parsed %d raw items", page_label, len(items))

    cleaned: list[dict] = []
    rejects = {"not_dict": 0, "empty_text": 0, "placeholder": 0, "bad_box": 0, "bad_floats": 0}
    sample_reject: dict | None = None
    for it in items:
        if not isinstance(it, dict):
            rejects["not_dict"] += 1
            sample_reject = sample_reject or {"reason": "not_dict", "item": it}
            continue
        text_val = it.get("text") or it.get("content") or it.get("string") or ""
        text = (text_val if isinstance(text_val, str) else str(text_val)).strip()
        if not text:
            rejects["empty_text"] += 1
            sample_reject = sample_reject or {"reason": "empty_text", "item": it}
            continue
        if _PLACEHOLDER_RE.match(text):
            rejects["placeholder"] += 1
            continue
        box = (
            it.get("box_2d") or it.get("box_d") or it.get("bbox") or it.get("box")
            or it.get("bounding_box") or it.get("bounds")
        )
        if not isinstance(box, list) or len(box) != 4:
            rejects["bad_box"] += 1
            sample_reject = sample_reject or {"reason": "bad_box", "item": it}
            continue
        try:
            box_f = [float(b) for b in box]
        except (TypeError, ValueError):
            rejects["bad_floats"] += 1
            sample_reject = sample_reject or {"reason": "bad_floats", "item": it}
            continue
        align = it.get("align", "left")
        if align not in ("left", "center", "right", "justify"):
            align = "left"
        size = it.get("size", "normal")
        if size not in ("small", "normal", "large"):
            size = "normal"
        cleaned.append({
            "text": text,
            "box": box_f,
            "bold": bool(it.get("bold", False)),
            "italic": bool(it.get("italic", False)),
            "size": size,
            "align": align,
        })
    if not cleaned and items:
        logger.error(
            "[overlay] %s: all %d items rejected | counts=%s | sample=%r",
            page_label, len(items), rejects, sample_reject,
        )
    elif rejects != {"not_dict": 0, "empty_text": 0, "placeholder": 0, "bad_box": 0, "bad_floats": 0}:
        logger.info("[overlay] %s: rejects=%s", page_label, rejects)
    return cleaned


async def translate_pdf_via_overlay(
    source_bytes: bytes,
    filename: str,
    request: "CreateTranslationJobRequest",
    job_id: str,
    debug_dir: str | None = None,
) -> tuple[bytes, dict]:
    import fitz

    settings = get_settings()
    lang = request.target_language.value
    target_code = SARVAM_LANG_CODES.get(lang, "hi-IN")
    source_code = (
        SARVAM_LANG_CODES.get(request.source_language.value, "en-IN")
        if request.source_language else "en-IN"
    )
    api_key = settings.sarvam_api_key
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY not configured")
    tm = settings.sarvam_translate_model
    is_devanagari = target_code.startswith(("hi", "mr", "ne", "sa"))
    glossary = Glossary.load()
    doc_state = DocState()
    state_lock = asyncio.Lock()
    sem = asyncio.Semaphore(max(1, settings.sarvam_translate_max_concurrency))

    async def _translate(text: str) -> str:
        if not text.strip():
            return text
        prepared = strip_pua(text.strip())
        if is_devanagari:
            prepared = localize_units(prepared)
        async with state_lock:
            frozen, sentinels = freeze(prepared, doc_state, glossary)
        async with sem:
            raw = await call_sarvam_translate(frozen, source_code, target_code, api_key, tm)
        cleaned = clean_sarvam_translate_output(raw) or frozen
        return restore(cleaned, sentinels)

    src = fitz.open(stream=source_bytes, filetype="pdf")
    page_count = src.page_count
    page_images: list[bytes] = []
    page_sizes: list[tuple[float, float]] = []
    for i in range(page_count):
        page = src[i]
        page_sizes.append((page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(_RASTER_SCALE, _RASTER_SCALE))
        page_images.append(pix.tobytes("png"))

    def _ocr_one(arg: tuple[int, bytes]) -> tuple[int, list[dict]]:
        idx, img = arg
        return idx, _gemini_bboxes(img, f"page {idx + 1}/{page_count}")

    page_items: list[list[dict]] = [[] for _ in range(page_count)]
    with ThreadPoolExecutor(max_workers=settings.gemini_ocr_concurrency) as pool:
        for idx, items in pool.map(_ocr_one, enumerate(page_images)):
            page_items[idx] = items
            logger.info("[%s] overlay OCR page %d/%d: %d regions", job_id, idx + 1, page_count, len(items))

    flat_tasks: list[asyncio.Task] = []
    flat_index: list[tuple[int, int]] = []
    for pi, items in enumerate(page_items):
        for bi, item in enumerate(items):
            flat_tasks.append(asyncio.create_task(_translate(item["text"])))
            flat_index.append((pi, bi))
    translated_results = await asyncio.gather(*flat_tasks) if flat_tasks else []
    for (pi, bi), t in zip(flat_index, translated_results):
        page_items[pi][bi]["translated"] = t

    family = "Noto Sans Devanagari, Noto Sans, sans-serif" if is_devanagari else "Noto Sans, sans-serif"
    for i in range(page_count):
        page = src[i]
        w, h = page_sizes[i]
        for item in page_items[i]:
            ymin, xmin, ymax, xmax = item["box"]
            rect = fitz.Rect(
                xmin / 1000.0 * w, ymin / 1000.0 * h,
                xmax / 1000.0 * w, ymax / 1000.0 * h,
            )
            page.add_redact_annot(rect, fill=(1, 1, 1))
        try:
            page.apply_redactions(images=getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0))
        except TypeError:
            page.apply_redactions()

        for item in page_items[i]:
            ymin, xmin, ymax, xmax = item["box"]
            rect = fitz.Rect(
                xmin / 1000.0 * w, ymin / 1000.0 * h,
                xmax / 1000.0 * w, ymax / 1000.0 * h,
            )
            text = item.get("translated") or item["text"]
            weight = "700" if item.get("bold") else "400"
            style = "italic" if item.get("italic") else "normal"
            size_em = {"large": "1.25em", "small": "0.85em"}.get(item.get("size", "normal"), "1em")
            css = (
                f"* {{font-family:{family};"
                f"text-align:{item.get('align', 'left')};"
                f"line-height:1.2;font-weight:{weight};font-style:{style};"
                f"font-size:{size_em};}}"
            )
            html = f"<div>{_esc(text)}</div>"
            try:
                page.insert_htmlbox(rect, html, css=css)
            except Exception as exc:
                logger.warning("[%s] insert_htmlbox failed on page %d: %s — falling back to insert_textbox", job_id, i + 1, exc)
                page.insert_textbox(rect, text, fontsize=9, align=0)

    out = src.tobytes(garbage=3, deflate=True)
    src.close()

    total = sum(len(items) for items in page_items)
    return out, {
        "pages": page_count,
        "blocks_total": total,
        "blocks_translated": total,
    }
