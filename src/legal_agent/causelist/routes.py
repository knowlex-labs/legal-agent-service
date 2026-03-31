"""Causelist scrape trigger endpoint."""

import asyncio
import logging
import re
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


class CaseDetail(BaseModel):
    # Cause list table fields
    serial_number: int | None
    cl_number: str | None
    case_number: str
    case_type: str | None
    judge_name: str | None
    hearing_type: str | None
    hearing_category: str | None
    bench_type: str | None
    court_hall_no: str | None
    petitioner: str | None
    respondent: str | None
    advocates_petitioner: str | None
    advocates_respondent: str | None
    remarks: str | None
    # Case detail page fields
    case_title: str | None
    case_status: str  # ACTIVE / PENDING / CLOSED
    cnr: str | None
    next_hearing_date: str | None  # YYYY-MM-DD
    # Common
    court_name: str
    court_location: str | None


class TriggerResponse(BaseModel):
    date: str  # YYYY-MM-DD — the cause list date that was scraped
    total: int
    cases: list[CaseDetail]


def _build_cases(entries: list[dict], bench: str) -> list[CaseDetail]:
    seen: set[str] = set()
    cases: list[CaseDetail] = []
    for entry in entries:
        case_number = entry.get("case_number")
        if not case_number or case_number in seen:
            continue
        seen.add(case_number)

        detail = entry.get("case_detail") or {}
        meta = entry.get("metadata") or {}

        m = re.match(r"^([A-Z_]+)", case_number.strip())
        case_type = m.group(1) if m else None

        petitioner = detail.get("petitioner") or meta.get("petitioner") or ""
        respondent = detail.get("respondent") or meta.get("respondent") or ""
        case_title = f"{petitioner} vs {respondent}" if petitioner and respondent else (petitioner or None)

        cases.append(CaseDetail(
            serial_number=entry.get("serial_number"),
            cl_number=meta.get("cl_number"),
            case_number=case_number,
            case_type=case_type,
            judge_name=entry.get("judge_name") or detail.get("judge_name"),
            hearing_type=entry.get("hearing_type"),
            hearing_category=meta.get("hearing_category"),
            bench_type=meta.get("bench_type"),
            court_hall_no=meta.get("court_hall_no"),
            petitioner=petitioner or None,
            respondent=respondent or None,
            advocates_petitioner=meta.get("advocates_petitioner"),
            advocates_respondent=meta.get("advocates_respondent"),
            remarks=meta.get("remarks"),
            case_title=case_title,
            case_status=detail.get("case_status", "PENDING"),
            cnr=detail.get("cnr"),
            next_hearing_date=detail.get("next_hearing_date"),
            court_name="MPHC",
            court_location=bench,
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
        return TriggerResponse(date=date_str, total=0, cases=[])

    cases = _build_cases(entries, req.bench)
    logger.info("Done user=%s: total=%d cases=%d", req.userId, len(entries), len(cases))
    return TriggerResponse(date=date_str, total=len(entries), cases=cases)


@causelist_router.post("/causelist/trigger", response_model=TriggerResponse)
async def trigger_causelist(req: TriggerRequest):
    """Trigger a cause list scrape for a single user. Runs Playwright in a thread pool."""
    return await asyncio.to_thread(_run_scrape, req)
