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
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from html import escape as _esc
from typing import TYPE_CHECKING

from legal_agent.agents.translation.sarvam_translate import SARVAM_LANG_CODES
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
    "- NEVER merge lines in different scripts (Latin/Devanagari/Arabic) into one item. "
    "Bilingual letterheads with stacked English + Hindi titles must produce SEPARATE items per script, "
    "each with its own bbox.\n"
    "- Set bold=true for visually heavier strokes (titles, section headings, emphasised terms).\n"
    "- Set size=\"large\" for titles/headings noticeably bigger than body text; \"small\" for footnotes; "
    "otherwise \"normal\".\n"
    "- align: \"center\" for centered titles, \"right\" for right-flushed dates/numbers, "
    "\"justify\" for any body paragraph spanning more than one line whose lines reach both "
    "left and right margins (formal letters, legal clauses — default for multi-line body text), "
    "\"left\" only for single-line items or short flush-left headers.\n"
    "- INCLUDE every line of printed/typed text exhaustively: page headers and footers, "
    "page numbers, file numbers, DIN/CBIC IDs, dates, address blocks, subject lines, "
    "numbered clauses, the TYPED NAME / DESIGNATION / OFFICE LINE that appears beneath a "
    "handwritten signature (e.g. \"Shankar Padhan / Addl. Asst. Director / DGGI Bhubaneswar\"), "
    "the closing salutation (\"Your faithfully,\", \"Sincerely,\"), and any \"Copy to:\" / "
    "endorsement lines at the bottom of the page. NONE of these may be skipped.\n"
    "- ONLY skip purely decorative non-text regions: round official seals, inked rubber stamps, "
    "logos, photographs, decorative icons, AND the handwritten signature scribble itself "
    "(the inked stroke — but NOT the typed name printed below it).\n"
    "- Preserve numbers, dates, IDs, citations verbatim.\n"
    "- Preserve original script — do not translate.\n"
    "- No commentary, no markdown fences. Output a valid JSON array only."
)

_PLACEHOLDER_RE = re.compile(
    r"^\s*\[\s*(stamp|seal|image|logo|handwritten|figure|photo|picture|graphic|sign|emblem|signature)\b",
    re.IGNORECASE,
)

# Unicode script ranges per Sarvam language-code prefix. Used to short-circuit
# translation when a region is already in the target script (e.g. Hindi source
# with Hindi target → Sarvam returns 400 "Source and target must differ").
_SCRIPT_RANGES: dict[str, tuple[int, int]] = {
    "hi": (0x0900, 0x097F), "mr": (0x0900, 0x097F),
    "ne": (0x0900, 0x097F), "sa": (0x0900, 0x097F),
    "bn": (0x0980, 0x09FF), "as": (0x0980, 0x09FF),
    "te": (0x0C00, 0x0C7F), "ta": (0x0B80, 0x0BFF),
    "kn": (0x0C80, 0x0CFF), "ml": (0x0D00, 0x0D7F),
    "gu": (0x0A80, 0x0AFF), "pa": (0x0A00, 0x0A7F),
    "or": (0x0B00, 0x0B7F), "ur": (0x0600, 0x06FF),
    "en": (0x0041, 0x007A),
}


def _already_in_target_script(text: str, target_code: str) -> bool:
    prefix = target_code.split("-", 1)[0]
    rng = _SCRIPT_RANGES.get(prefix)
    if not rng:
        return False
    lo, hi = rng
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 3:
        return False
    in_range = sum(1 for c in letters if lo <= ord(c) <= hi)
    return in_range / len(letters) > 0.6


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
        # NFC: Devanagari (and other Indic) has multiple valid encodings per glyph;
        # normalise so glossary lookup and downstream grep match consistent forms.
        text = unicodedata.normalize("NFC", text)
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

    from legal_agent.agents.translation.translator import Translator

    settings = get_settings()
    lang = request.target_language.value
    target_code = SARVAM_LANG_CODES.get(lang, "hi-IN")
    source_code = (
        SARVAM_LANG_CODES.get(request.source_language.value, "en-IN")
        if request.source_language else "en-IN"
    )
    is_devanagari = target_code.startswith(("hi", "mr", "ne", "sa"))
    translator = Translator(lang)

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

    def _run_ocr_pool() -> list[list[dict]]:
        out: list[list[dict]] = [[] for _ in range(page_count)]
        with ThreadPoolExecutor(max_workers=settings.gemini_ocr_concurrency) as pool:
            for idx, items in pool.map(_ocr_one, enumerate(page_images)):
                out[idx] = items
                logger.info(
                    "[%s] overlay OCR page %d/%d: %d regions",
                    job_id, idx + 1, page_count, len(items),
                )
        return out

    # Offload the blocking Gemini Vision pool to a worker thread so FastAPI's
    # event loop stays responsive (status polls, other jobs) while OCR runs.
    page_items = await asyncio.to_thread(_run_ocr_pool)

    for pi, items in enumerate(page_items):
        if not items:
            continue
        max_coord = max(max(it["box"]) for it in items)
        if max_coord <= 1000.0:
            continue
        raster_w = page_sizes[pi][0] * _RASTER_SCALE
        raster_h = page_sizes[pi][1] * _RASTER_SCALE
        logger.info(
            "[%s] page %d: bboxes look pixel-scaled (max=%.0f) — renormalising via raster %0.fx%0.f",
            job_id, pi + 1, max_coord, raster_w, raster_h,
        )
        for it in items:
            ymin, xmin, ymax, xmax = it["box"]
            it["box"] = [
                ymin / raster_h * 1000.0,
                xmin / raster_w * 1000.0,
                ymax / raster_h * 1000.0,
                xmax / raster_w * 1000.0,
            ]

    flat_texts: list[str] = []
    flat_index: list[tuple[int, int]] = []
    for pi, items in enumerate(page_items):
        for bi, item in enumerate(items):
            text = item["text"]
            if not text.strip() or _already_in_target_script(text, target_code):
                page_items[pi][bi]["translated"] = text
                continue
            flat_texts.append(text)
            flat_index.append((pi, bi))
    if flat_texts:
        translated_results = await translator.translate_batch(flat_texts, source_code, target_code)
        for (pi, bi), t in zip(flat_index, translated_results):
            page_items[pi][bi]["translated"] = t

    family = "Noto Sans Devanagari, Noto Sans, sans-serif" if is_devanagari else "Noto Sans, sans-serif"
    _PAD_X = 6.0
    _PAD_Y = 4.0
    for i in range(page_count):
        page = src[i]
        w, h = page_sizes[i]
        for item in page_items[i]:
            ymin, xmin, ymax, xmax = item["box"]
            x0, y0 = xmin / 1000.0 * w, ymin / 1000.0 * h
            x1, y1 = xmax / 1000.0 * w, ymax / 1000.0 * h
            redact = fitz.Rect(
                max(0.0, x0 - _PAD_X), max(0.0, y0 - _PAD_Y),
                min(w, x1 + _PAD_X), min(h, y1 + _PAD_Y),
            )
            page.add_redact_annot(redact, fill=(1, 1, 1))
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
            size_em = {"large": "1.45em", "small": "1em"}.get(item.get("size", "normal"), "1.15em")
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
