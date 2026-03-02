"""FastAPI routes for case summary generation."""

import logging

from fastapi import APIRouter, Header, HTTPException

from legal_agent.summary.generator import SummaryGenerator
from legal_agent.summary.models import CaseSummaryResponse, GenerateSummaryRequest
from legal_agent.summary.store import CaseSummaryStore

logger = logging.getLogger(__name__)
summary_router = APIRouter(prefix="/summaries", tags=["summaries"])

_store: CaseSummaryStore | None = None
_generator: SummaryGenerator | None = None


def set_summary_services(store: CaseSummaryStore, generator: SummaryGenerator) -> None:
    global _store, _generator
    _store = store
    _generator = generator


def _get_services() -> tuple[CaseSummaryStore, SummaryGenerator]:
    if _store is None or _generator is None:
        raise HTTPException(
            status_code=503,
            detail="Summary service is still initializing, please retry shortly",
        )
    return _store, _generator


@summary_router.post("/generate", response_model=CaseSummaryResponse)
async def generate_summary(
    request: GenerateSummaryRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
) -> CaseSummaryResponse:
    """Generate or return a cached case summary."""
    store, generator = _get_services()

    if not request.force_regenerate:
        existing = await store.get(request.case_folder_id)
        if existing:
            logger.info(f"[summary] Returning cached summary for {request.case_folder_id}")
            return CaseSummaryResponse(**existing)

    summary_md = await generator.generate(
        file_ids=request.file_ids,
        user_id=x_user_id,
        drafts=request.drafts,
        chat_highlights=request.chat_highlights,
        model=request.model,
    )

    row = await store.upsert(request.case_folder_id, summary_md)
    logger.info(f"[summary] Stored summary for {request.case_folder_id}")
    return CaseSummaryResponse(**row)


@summary_router.get("/{case_folder_id}", response_model=CaseSummaryResponse)
async def get_summary(case_folder_id: str) -> CaseSummaryResponse:
    """Get a stored case summary."""
    store, _ = _get_services()
    row = await store.get(case_folder_id)
    if not row:
        raise HTTPException(status_code=404, detail="Summary not found")
    return CaseSummaryResponse(**row)


@summary_router.delete("/{case_folder_id}")
async def delete_summary(case_folder_id: str):
    """Delete a stored case summary."""
    store, _ = _get_services()
    existed = await store.delete(case_folder_id)
    if not existed:
        raise HTTPException(status_code=404, detail="Summary not found")
    return {"status": "deleted"}
