"""Causelist scrape trigger endpoint."""

import asyncio
import logging
import re
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


class CaseDetail(BaseModel):
    case_number: str
    case_type: str | None
    case_status: str  # ACTIVE / PENDING / CLOSED
    case_title: str | None
    judge_name: str | None
    court_name: str
    court_location: str | None
    next_hearing_date: str | None  # YYYY-MM-DD
    cnr: str | None


class TriggerResponse(BaseModel):
    inserted: int
    duplicates: int
    total: int
    cases: list[CaseDetail]


def _build_cases(entries: list[dict], bench: str) -> list[CaseDetail]:
    seen: set[str] = set()
    cases: list[CaseDetail] = []
    for entry in entries:
        case_number = entry.get("case_number")
        detail = entry.get("case_detail") or {}
        if not case_number or not detail or case_number in seen:
            continue
        seen.add(case_number)

        # Extract case type prefix from e.g. "MCRC - 9947/2026"
        m = re.match(r"^([A-Z_]+)", case_number.strip())
        case_type = m.group(1) if m else None

        # Build case title from petitioner/respondent
        petitioner = detail.get("petitioner") or ""
        respondent = detail.get("respondent") or ""
        if petitioner and respondent:
            case_title = f"{petitioner} vs {respondent}"
        elif petitioner:
            case_title = petitioner
        else:
            case_title = None

        cases.append(CaseDetail(
            case_number=case_number,
            case_type=case_type,
            case_status=detail.get("case_status", "PENDING"),
            case_title=case_title,
            judge_name=detail.get("judge_name"),
            court_name="MPHC",
            court_location=bench,
            next_hearing_date=detail.get("next_hearing_date"),
            cnr=detail.get("cnr"),
        ))
    return cases


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
        return TriggerResponse(inserted=0, duplicates=0, total=0, cases=[])

    for entry in entries:
        entry["user_id"] = req.userId
        entry["cause_list_date"] = date_str
        entry["bench"] = req.bench
        entry["court"] = "MPHC"
        entry["lawyer_name"] = req.lawyerName
        entry["court_hall_no"] = entry.get("metadata", {}).get("court_hall_no")

    with get_connection() as conn:
        inserted = insert_cause_list_entries(conn, entries)

    cases = _build_cases(entries, req.bench)
    duplicates = len(entries) - inserted
    logger.info("Done user=%s: inserted=%d duplicates=%d total=%d cases=%d", req.userId, inserted, duplicates, len(entries), len(cases))
    return TriggerResponse(inserted=inserted, duplicates=duplicates, total=len(entries), cases=cases)


@causelist_router.post("/causelist/trigger", response_model=TriggerResponse)
async def trigger_causelist(req: TriggerRequest):
    """Trigger a cause list scrape for a single user. Runs Playwright in a thread pool."""
    return await asyncio.to_thread(_run_scrape, req)
