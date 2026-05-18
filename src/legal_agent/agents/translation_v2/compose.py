"""Stage 6: render per-page HTML to per-page PDFs, then concatenate.

Reuses v1's render_html_to_pdf_bytes (Playwright/Chromium) read-only.
Per-page PDFs are concatenated via PyMuPDF insert_pdf so the page count of the
final document equals the source page count exactly.
"""

from __future__ import annotations

import asyncio
import logging

from legal_agent.agents.translation.html_pdf_translator import render_html_to_pdf_bytes

logger = logging.getLogger(__name__)


def _render_one_sync(html_doc: str, w_mm: float, h_mm: float) -> bytes:
    pdf_options = {
        "width": f"{w_mm:.2f}mm",
        "height": f"{h_mm:.2f}mm",
        "margin": {"top": "0", "bottom": "0", "left": "0", "right": "0"},
        "print_background": True,
        # Flow-layout content can occasionally exceed the printable area
        # before the page-level autofit shrinks it; force one physical page
        # per render so the source-page count is preserved by construction.
        "page_ranges": "1",
    }
    return render_html_to_pdf_bytes(html_doc, pdf_options=pdf_options)


async def render_pages_to_pdfs(
    htmls: list[str],
    page_sizes_mm: list[tuple[float, float]],
    concurrency: int,
    job_id: str,
) -> list[bytes]:
    """Render each per-page HTML to a 1-page PDF. Bounded concurrency for Playwright."""
    if not htmls:
        return []
    if len(htmls) != len(page_sizes_mm):
        raise ValueError(
            f"htmls/page_sizes_mm length mismatch ({len(htmls)} vs {len(page_sizes_mm)})"
        )

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(idx: int, html_doc: str, size: tuple[float, float]) -> tuple[int, bytes]:
        async with sem:
            try:
                pdf_bytes = await asyncio.to_thread(_render_one_sync, html_doc, size[0], size[1])
            except Exception as exc:
                raise RuntimeError(
                    f"Playwright render failed on page {idx + 1}/{len(htmls)}: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            return idx, pdf_bytes

    results = await asyncio.gather(
        *(_one(i, h, s) for i, (h, s) in enumerate(zip(htmls, page_sizes_mm, strict=True)))
    )
    results.sort(key=lambda t: t[0])
    return [pdf for _, pdf in results]


def _concat_sync(per_page_pdfs: list[bytes]) -> bytes:
    import fitz

    out_doc = fitz.open()
    try:
        for blob in per_page_pdfs:
            src = fitz.open(stream=blob, filetype="pdf")
            try:
                out_doc.insert_pdf(src)
            finally:
                src.close()
        if out_doc.page_count != len(per_page_pdfs):
            raise RuntimeError(
                f"Concat page count mismatch: expected {len(per_page_pdfs)}, "
                f"got {out_doc.page_count}"
            )
        return out_doc.tobytes()
    finally:
        out_doc.close()


async def concat_pdfs(per_page_pdfs: list[bytes]) -> bytes:
    """Merge per-page PDFs into one document, preserving page order and count."""
    if not per_page_pdfs:
        raise ValueError("Cannot concat zero PDFs")
    return await asyncio.to_thread(_concat_sync, per_page_pdfs)
