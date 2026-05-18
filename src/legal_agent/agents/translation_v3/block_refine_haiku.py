"""Stage 2.5 (v3): Haiku multimodal refinement of Azure block metadata.

Per page, Haiku looks at the rasterized page and the Azure-derived block list
and returns role / font_size_category / bold / italic / underline corrections
by block id. This catches:

- Section headings Azure misclassified as paragraphs (or vice versa).
- Style misdetection from `styleFont` over-firing on small bold fragments.
- Font sizes the bbox-derived estimate got wrong because Azure didn't expose
  `paragraph.spans` / `line.spans` for span-overlap line attribution.

`bbox_norm`, `reading_order`, and `text_en` are NEVER mutated here. Only
typography metadata is refined. The Block schema is preserved, downstream
glossary / translate stages are unchanged.

Per-page Haiku output is content-hashed and cached in the same S3 cache as
the OCR stage (`OCR_CACHE_ENABLED` / `OCR_CACHE_PREFIX`).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Any

from pydantic import BaseModel

from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockRole,
    BlockWeight,
    PageRaster,
    VisionPage,
)
from legal_agent.agents.translation_v3.anthropic_client import call_anthropic_json
from legal_agent.utils.ocr import _cache_key, _cache_lookup, _cache_store

logger = logging.getLogger(__name__)

_CACHE_PROVIDER = "azure_doc_intel_refine_v1"
# Bump when the prompt or category mapping changes so old cached corrections
# don't apply to a different mapping.
_PROMPT_VERSION = "1"

_CATEGORY_TO_PT: dict[str, float] = {
    "xs": 9.0,
    "s": 10.5,
    "m": 12.0,
    "l": 14.0,
    "xl": 18.0,
}

# Roles the LLM may suggest. Positional roles (table_cell, separator) are
# locked — Azure determines them structurally and we don't override.
_ROLE_ALIASES: dict[str, BlockRole] = {
    "title": BlockRole.title,
    "heading": BlockRole.heading,
    "paragraph": BlockRole.paragraph,
    "body": BlockRole.paragraph,
    "header": BlockRole.header,
    "footer": BlockRole.footer,
    "page_number": BlockRole.page_number,
    "pagenumber": BlockRole.page_number,
}

_SYSTEM_PROMPT = """\
You are a layout-classification assistant for an OCR pipeline that translates Indian legal documents.

The pipeline already ran Azure Document Intelligence (`prebuilt-layout`) and built a list of blocks per page, each tagged with a role, an estimated font size, and bold/italic/underline flags. Azure makes mistakes on legal scans: it sometimes mistakes body paragraphs for headings or vice versa, over-fires `styleFont` on small bold runs so a whole paragraph looks bold, and miscomputes font size from padded bboxes.

For each block in the user's JSON list, look at the page image and decide whether the metadata is correct. Return a JSON list of corrections keyed by `id`.

Fields you may correct:
- `role` — one of: `title`, `heading`, `paragraph`, `header`, `footer`, `page_number`.
  - `title` = top-of-document title or large court/section header (e.g. "IN THE HIGH COURT OF MADHYA PRADESH").
  - `heading` = section heading inside the body (e.g. "FACTS OF THE CASE", "GROUNDS", "PRAYER").
  - `paragraph` = body text. Long sentences, numbered points, multiple lines → always `paragraph`.
  - `header` = repeated content at the top of every page (page-header band).
  - `footer` = repeated content at the bottom (page-footer band, signature blocks).
  - `page_number` = a standalone page number.
- `font_size_category` — one of: `xs`, `s`, `m`, `l`, `xl`. These map to ~9pt / ~10.5pt / ~12pt / ~14pt / ~18pt. Body text is almost always `s`. Section headings are `m`. Document titles are `l` or `xl`.
- `bold` — true ONLY if the visible glyphs in the block are predominantly bold. A paragraph that contains one bold word is NOT bold.
- `italic` — true ONLY if the visible glyphs are predominantly italic.
- `underline` — true if there is a visible horizontal rule directly beneath the text.

Rules:
- Only return entries for IDs the user provided. Do not invent blocks.
- If a field is correct as-is, omit it (or use null).
- NEVER change role to `table_cell` or `separator`; those are positional and immutable.
- Be conservative with bold: only flip to bold when the GLYPHS LOOK BOLD. A heading is not bold by definition.
- Be conservative with role flips: if Azure called it `paragraph` and the text is plainly body content, keep `paragraph`.

Output the corrections via the `submit_corrections` tool.
"""


class _BlockCorrection(BaseModel):
    id: str
    role: str | None = None
    font_size_category: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None


class _RefineResponse(BaseModel):
    blocks: list[_BlockCorrection]


def _compact_block_for_prompt(block: Block) -> dict[str, Any]:
    return {
        "id": block.id,
        "role": block.role.value,
        "font_size_pt": round(block.font_size_pt, 1),
        "bold": block.weight == BlockWeight.bold,
        "italic": block.italic,
        "underline": block.underline,
        "text": block.text_en[:240],
    }


def _payload_hash(payload: list[dict[str, Any]], raster_png: bytes) -> bytes:
    h = hashlib.sha256()
    h.update(_PROMPT_VERSION.encode())
    h.update(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    h.update(raster_png)
    return h.digest()


def _apply_corrections(
    page: VisionPage, corrections: list[_BlockCorrection]
) -> VisionPage:
    by_id = {c.id: c for c in corrections}
    new_blocks: list[Block] = []
    for b in page.blocks:
        c = by_id.get(b.id)
        if c is None:
            new_blocks.append(b)
            continue
        # Positional blocks are locked.
        if b.role in (BlockRole.table_cell, BlockRole.separator):
            new_blocks.append(b)
            continue
        updates: dict[str, Any] = {}
        if c.role:
            new_role = _ROLE_ALIASES.get(c.role.lower().strip())
            if new_role is not None:
                updates["role"] = new_role
        if c.font_size_category:
            pt = _CATEGORY_TO_PT.get(c.font_size_category.lower().strip())
            if pt is not None:
                updates["font_size_pt"] = pt
        if c.bold is not None:
            updates["weight"] = BlockWeight.bold if c.bold else BlockWeight.normal
        if c.italic is not None:
            updates["italic"] = c.italic
        if c.underline is not None:
            updates["underline"] = c.underline
        new_blocks.append(b.model_copy(update=updates) if updates else b)
    return page.model_copy(update={"blocks": new_blocks})


async def _refine_one(
    page: VisionPage,
    raster: PageRaster,
    model: str,
    sem: asyncio.Semaphore,
    job_id: str,
) -> VisionPage:
    async with sem:
        # Skip separators — they're positional, not stylistic.
        candidates = [b for b in page.blocks if b.role != BlockRole.separator]
        if not candidates:
            return page

        payload = [_compact_block_for_prompt(b) for b in candidates]
        digest = _payload_hash(payload, raster.png)
        # Reuse the OCR cache_key plumbing (already wired to S3 + env gating).
        sha, cache_key = _cache_key(digest, _CACHE_PROVIDER, "json")
        sha_short = f"{_CACHE_PROVIDER}/{sha[:12]}"
        cached = _cache_lookup(cache_key, sha_short)
        if cached is not None:
            try:
                corr = _RefineResponse.model_validate_json(cached)
                logger.info(
                    "[%s] refine page %d cache hit (%d corrections)",
                    job_id, page.page_no, len(corr.blocks),
                )
                return _apply_corrections(page, corr.blocks)
            except Exception:
                logger.warning(
                    "[%s] refine page %d cache parse failed; re-running",
                    job_id, page.page_no,
                )

        t0 = time.perf_counter()
        image_b64 = base64.b64encode(raster.png).decode("ascii")
        user_content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_b64,
                },
            },
            {
                "type": "text",
                "text": (
                    "Blocks to verify (JSON):\n\n```json\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2)
                    + "\n```"
                ),
            },
        ]
        system_blocks = [
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        try:
            result = await call_anthropic_json(
                model,
                _RefineResponse,
                messages=[{"role": "user", "content": user_content}],
                system=system_blocks,
                tool_name="submit_corrections",
                max_tokens=4096,
                temperature=0.0,
                retries=1,
                context=f"refine page {page.page_no}",
            )
        except Exception as exc:
            logger.warning(
                "[%s] refine page %d failed (%s: %s); keeping Azure output",
                job_id, page.page_no, type(exc).__name__, exc,
            )
            return page
        _cache_store(cache_key, sha_short, result.model_dump_json())
        logger.info(
            "[%s] refine page %d took %.2fs (%d corrections of %d blocks)",
            job_id, page.page_no, time.perf_counter() - t0,
            len(result.blocks), len(candidates),
        )
        return _apply_corrections(page, result.blocks)


async def refine_pages(
    pages: list[VisionPage],
    rasters: list[PageRaster],
    *,
    model: str,
    concurrency: int,
    job_id: str,
) -> list[VisionPage]:
    """Fan out Haiku refinement across pages, bounded by `concurrency`. Pages
    without a matching raster pass through unchanged."""
    if not pages:
        return pages
    raster_by_page = {r.page_no: r for r in rasters}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _run(page: VisionPage) -> VisionPage:
        raster = raster_by_page.get(page.page_no)
        if raster is None:
            return page
        return await _refine_one(page, raster, model, sem, job_id)

    results = await asyncio.gather(*(_run(p) for p in pages))
    return sorted(results, key=lambda p: p.page_no)
