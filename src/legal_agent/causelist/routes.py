"""Causelist scrape trigger endpoint."""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel

from legal_agent.causelist.scraper import scrape_cause_list

logger = logging.getLogger(__name__)

causelist_router = APIRouter(tags=["causelist"])


class TriggerRequest(BaseModel):
    userId: str
    lawyerName: str
    bench: str
    hearingType: str = "MOTION"
    date: str | None = None  # YYYY-MM-DD, defaults to today IST


class CauseListEntry(BaseModel):
    cause_list_date: str
    bench: str
    court: str
    case_number: str | None
    judge_name: str | None
    hearing_type: str | None
    court_hall_no: str | None
    serial_number: int | None
    case_status: str
    case_title: str | None
    next_hearing_date: str | None
    cnr: str | None
    metadata: dict


class TriggerResponse(BaseModel):
    total: int
    entries: list[CauseListEntry]


def _build_entries(entries: list[dict], date_str: str, bench: str) -> list[CauseListEntry]:
    result = []
    for entry in entries:
        detail = entry.get("case_detail") or {}
        petitioner = (detail.get("petitioner") or "").strip()
        respondent = (detail.get("respondent") or "").strip()
        if petitioner and respondent:
            case_title = f"{petitioner} vs {respondent}"
        elif petitioner:
            case_title = petitioner
        else:
            case_title = None
        result.append(CauseListEntry(
            cause_list_date=date_str,
            bench=bench,
            court="MPHC",
            case_number=entry.get("case_number"),
            judge_name=entry.get("judge_name"),
            hearing_type=entry.get("hearing_type"),
            court_hall_no=entry.get("metadata", {}).get("court_hall_no"),
            serial_number=entry.get("serial_number"),
            case_status=detail.get("case_status", "PENDING"),
            case_title=case_title,
            next_hearing_date=detail.get("next_hearing_date"),
            cnr=detail.get("cnr"),
            metadata=entry.get("metadata", {}),
        ))
    return result


def _run_scrape(req: TriggerRequest) -> TriggerResponse:
    date_str = req.date or datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

    raw_entries = scrape_cause_list(
        bench=req.bench,
        lawyer_name=req.lawyerName,
        date_str=date_str,
        hearing_type=req.hearingType,
    )

    if not raw_entries:
        logger.info("No entries found for user=%s bench=%s date=%s", req.userId, req.bench, date_str)
        return TriggerResponse(total=0, entries=[])

    entries = _build_entries(raw_entries, date_str, req.bench)
    logger.info("Done user=%s bench=%s date=%s total=%d", req.userId, req.bench, date_str, len(entries))
    return TriggerResponse(total=len(entries), entries=entries)


@causelist_router.post("/causelist/trigger", response_model=TriggerResponse)
async def trigger_causelist(req: TriggerRequest):
    """Trigger a cause list scrape for a single user. Runs Playwright in a thread pool."""
    return await asyncio.to_thread(_run_scrape, req)
