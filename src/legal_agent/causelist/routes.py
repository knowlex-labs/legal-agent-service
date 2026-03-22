"""Causelist scrape trigger endpoint."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel

from legal_agent.causelist.connection import get_connection
from legal_agent.causelist.db import insert_cause_list_entries
from legal_agent.causelist.scraper import scrape_cause_list

logger = logging.getLogger(__name__)

causelist_router = APIRouter(tags=["causelist"])


class TriggerRequest(BaseModel):
    userId: str
    lawyerName: str
    bench: str
    hearingType: str = "MOTION"
    date: str | None = None  # YYYY-MM-DD, defaults to today IST


class TriggerResponse(BaseModel):
    inserted: int
    duplicates: int
    total: int


def _run_scrape(req: TriggerRequest) -> TriggerResponse:
    date_str = req.date or datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

    entries = scrape_cause_list(
        bench=req.bench,
        lawyer_name=req.lawyerName,
        date_str=date_str,
        hearing_type=req.hearingType,
    )

    if not entries:
        logger.info("No entries found for user=%s bench=%s date=%s", req.userId, req.bench, date_str)
        return TriggerResponse(inserted=0, duplicates=0, total=0)

    for entry in entries:
        entry["user_id"] = req.userId
        entry["cause_list_date"] = date_str
        entry["bench"] = req.bench
        entry["court"] = "MPHC"
        entry["lawyer_name"] = req.lawyerName
        entry["court_hall_no"] = entry.get("metadata", {}).get("court_hall_no")

    with get_connection() as conn:
        inserted = insert_cause_list_entries(conn, entries)

    duplicates = len(entries) - inserted
    logger.info("Done user=%s: inserted=%d duplicates=%d total=%d", req.userId, inserted, duplicates, len(entries))
    return TriggerResponse(inserted=inserted, duplicates=duplicates, total=len(entries))


@causelist_router.post("/causelist/trigger", response_model=TriggerResponse)
async def trigger_causelist(req: TriggerRequest):
    """Trigger a cause list scrape for a single user. Runs Playwright in a thread pool."""
    return await asyncio.to_thread(_run_scrape, req)
