"""Stage 1: PDF → per-page 300dpi PNG rasters via PyMuPDF.

Captures source page dimensions in both pt (vision model context) and mm
(HTML page-size — the page count parity guarantee).
"""

from __future__ import annotations

import asyncio
import logging

from legal_agent.agents.translation_v2.schemas import PageRaster

logger = logging.getLogger(__name__)

_PT_TO_MM = 25.4 / 72.0


def _rasterize_sync(pdf_bytes: bytes, dpi: int) -> list[PageRaster]:
    import fitz

    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        out: list[PageRaster] = []
        for idx in range(doc.page_count):
            page = doc[idx]
            rect = page.rect
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            out.append(
                PageRaster(
                    page_no=idx + 1,
                    png=pixmap.tobytes("png"),
                    width_pt=rect.width,
                    height_pt=rect.height,
                    width_mm=rect.width * _PT_TO_MM,
                    height_mm=rect.height * _PT_TO_MM,
                )
            )
        return out
    finally:
        doc.close()


async def rasterize_pdf(pdf_bytes: bytes, dpi: int = 300) -> list[PageRaster]:
    """Rasterize each page of the PDF at the given DPI.

    PyMuPDF is sync C — we run the whole loop in a worker thread so the event
    loop stays responsive for downstream Gemini calls.
    """
    pages = await asyncio.to_thread(_rasterize_sync, pdf_bytes, dpi)
    logger.info(
        "[rasterize] %d pages at %d dpi (%.1f×%.1f mm first page)",
        len(pages),
        dpi,
        pages[0].width_mm if pages else 0.0,
        pages[0].height_mm if pages else 0.0,
    )
    return pages
