"""
Playwright automation to scrape MPHC cause lists.

Navigates to https://mphc.gov.in/causelist, fills the search form,
and extracts cause list entries from the results table.
"""

import logging
import re
import time
from datetime import date, datetime

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

CAUSELIST_URL = "https://mphc.gov.in/causelist"
MPHC_BASE_URL = "https://mphc.gov.in"


def scrape_cause_list(
    bench: str,
    lawyer_name: str,
    date_str: str,
    hearing_type: str = "MOTION",
    search_tab: str = "Lawyer",
) -> list[dict]:
    """
    Scrape MPHC cause list for a given bench, lawyer, date, and hearing type.

    Args:
        bench: One of 'Gwalior', 'Jabalpur', 'Indore'
        lawyer_name: Name to search for
        date_str: Date in ISO format (YYYY-MM-DD)
        hearing_type: One of 'MOTION', 'FINAL', 'LOK ADALAT'
        search_tab: One of 'Lawyer', 'Judge', 'Partyname'

    Returns:
        List of entry dicts with case details and metadata.
    """
    # Convert ISO date to DD-MM-YYYY for the website form
    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    dd_mm_yyyy = date_obj.strftime("%d-%m-%Y")

    entries = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=get_settings().playwright_headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
        )
        stealth = Stealth()
        page = context.new_page()
        stealth.apply_stealth_sync(page)

        logger.info(f"Navigating to {CAUSELIST_URL}")
        page.goto(CAUSELIST_URL, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(3000)

        # Click search tab first (Lawyer / Judge / Partyname)
        logger.info(f"Clicking search tab: {search_tab}")
        tab_link = page.locator(f"a:has-text('{search_tab}'), button:has-text('{search_tab}')").first
        tab_link.click()
        time.sleep(1)

        # Select bench from dropdown (top-right select on the page)
        logger.info(f"Selecting bench: {bench}")
        bench_select = page.locator("select").first
        bench_select.select_option(label=bench)
        time.sleep(1)

        # Fill lawyer/search name
        logger.info(f"Filling name: {lawyer_name}")
        name_input = page.locator("input[type='text']:not([readonly]):visible").first
        name_input.fill(lawyer_name)

        # Select hearing type radio button (MOTION is default, skip if not changed)
        if hearing_type != "MOTION":
            logger.info(f"Selecting hearing type: {hearing_type}")
            page.locator(f"text={hearing_type}").first.click()
            time.sleep(0.5)

        # Fill date via JS (input is readonly so type() won't work)
        logger.info(f"Filling date: {dd_mm_yyyy}")
        date_input = page.locator("input.hasDatepicker:visible").first
        date_input.evaluate(f"el => {{ el.removeAttribute('readonly'); el.value = '{dd_mm_yyyy}'; el.setAttribute('readonly', ''); }}")
        time.sleep(0.5)

        # Click SHOW button
        logger.info("Clicking SHOW button")
        page.locator("input[value='SHOW']:visible, button:has-text('SHOW'):visible").first.click()

        # Wait for results
        logger.info("Waiting for results...")
        page.wait_for_load_state("networkidle", timeout=120000)
        time.sleep(3)

        # Extract entries from the results page
        entries = extract_entries_from_page(page, search_tab)
        logger.info(f"Extracted {len(entries)} entries")

        # Enrich each entry with case details from the case detail page
        for entry in entries:
            case_url = entry.get("case_url")
            if case_url:
                logger.info(f"Scraping case detail for {entry.get('case_number')}")
                entry["case_detail"] = scrape_case_detail(context, case_url, date_str)

        browser.close()

    return entries


def scrape_case_detail(context, case_url: str, today_str: str) -> dict:
    """
    Open the MPHC case detail page and extract structured case information.

    Navigates to the Details tab then the Listing tab to find the next hearing date.
    Returns a dict with cnr, case_status, judge_name, petitioner, respondent, next_hearing_date.
    """
    detail: dict = {}
    stealth = Stealth()
    page = context.new_page()
    stealth.apply_stealth_sync(page)
    try:
        page.goto(case_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        # Build a label → value map from the details table
        label_map: dict[str, str] = {}
        rows = page.query_selector_all("table tr")
        for row in rows:
            cells = row.query_selector_all("td")
            if len(cells) >= 2:
                lbl = (cells[0].inner_text() or "").strip().lower().rstrip(".")
                val = (cells[1].inner_text() or "").strip()
                if lbl:
                    label_map[lbl] = val

        # CNR from "Case No." row
        case_no_raw = label_map.get("case no", "") or label_map.get("case no.", "")
        cnr_match = re.search(r"CNR[:\s]*([A-Z0-9]+)", case_no_raw, re.IGNORECASE)
        detail["cnr"] = cnr_match.group(1) if cnr_match else None

        # Status mapping
        status_raw = (label_map.get("status", "") or "").lower()
        if "disposed" in status_raw:
            detail["case_status"] = "CLOSED"
        elif "pending" in status_raw:
            detail["case_status"] = "PENDING"
        else:
            detail["case_status"] = "ACTIVE"

        # Judge name from "Last Listed On" — extract bracket content containing JUSTICE
        last_listed = label_map.get("last listed on", "")
        judge_match = re.search(r"\[([^\]]*JUSTICE[^\]]*)\]", last_listed, re.IGNORECASE)
        detail["judge_name"] = judge_match.group(1).strip() if judge_match else None

        # Petitioner — first line, strip leading numbering like "1 "
        petitioner_raw = label_map.get("petitioner(s)", "") or label_map.get("petitioners", "")
        first_pet = petitioner_raw.split("\n")[0].strip() if petitioner_raw else ""
        detail["petitioner"] = re.sub(r"^\d+\s+", "", first_pet).strip() or None

        # Respondent — first line, strip leading numbering
        respondent_raw = label_map.get("respondent(s)", "") or label_map.get("respondents", "")
        first_res = respondent_raw.split("\n")[0].strip() if respondent_raw else ""
        detail["respondent"] = re.sub(r"^\d+\s+", "", first_res).strip() or None

        # Next hearing date from Listing tab
        detail["next_hearing_date"] = None
        listing_tab = page.locator("a:has-text('Listing')").first
        if listing_tab.count() > 0:
            listing_tab.click()
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            today = date.fromisoformat(today_str)
            for lrow in page.query_selector_all("table tr"):
                lcells = lrow.query_selector_all("td")
                if lcells:
                    date_text = (lcells[0].inner_text() or "").strip()
                    try:
                        d = datetime.strptime(date_text, "%d-%m-%Y").date()
                        if d > today:
                            detail["next_hearing_date"] = d.isoformat()
                            break
                    except ValueError:
                        pass

    except Exception as e:
        logger.warning("Failed to scrape case detail from %s: %s", case_url, e)
    finally:
        page.close()

    return detail


def extract_entries_from_page(page, search_tab: str = "Lawyer") -> list[dict]:
    """
    Walk the results table DOM and extract cause list entries.

    Tracks contextual state (judge, bench type, court hall, hearing category)
    as it encounters header rows, then extracts data from numbered rows.
    """
    rows = page.query_selector_all("table:not(.ui-datepicker-calendar) tr")
    if not rows:
        logger.warning("No table rows found on results page")
        return []

    entries = []
    current_judge = None
    current_bench_type = None
    current_hall = None
    current_hearing_type = None
    current_category = None

    for row in rows:
        text = (row.inner_text() or "").strip()
        if not text:
            continue

        cells = row.query_selector_all("td")

        # Header section: judge name, bench type, court hall can all be in the same block
        # e.g., "BEFORE HON'BLE SHRI JUSTICE AMIT SETH  SINGLE BENCH  Court Hall No.: 6"
        # Also handles registrar headers like "BEFORE PRINCIPAL REGISTRAR"
        justice_match = re.search(r"JUSTICE\s+(.+?)(?:\s{2,}|$)", text, re.IGNORECASE)
        registrar_match = re.search(r"BEFORE\s+((?:PRINCIPAL\s+)?REGISTRAR)", text, re.IGNORECASE)
        if justice_match or registrar_match:
            if justice_match:
                current_judge = justice_match.group(0).strip()
            else:
                current_judge = registrar_match.group(1).strip().upper()
            # Also check for bench type and court hall in same text
            for bt in ("SINGLE BENCH", "DIVISION BENCH", "FULL BENCH", "LARGER BENCH"):
                if bt in text.upper():
                    current_bench_type = bt
                    break
            hall_match = re.search(r"Court\s*Hall\s*No\.?\s*:?\s*(\d+)", text, re.IGNORECASE)
            if hall_match:
                current_hall = hall_match.group(1)
            continue

        # Bench type header (standalone)
        if text.upper().strip() in ("SINGLE BENCH", "DIVISION BENCH", "FULL BENCH", "LARGER BENCH"):
            current_bench_type = text.strip()
            continue

        # Court Hall header (standalone)
        hall_match = re.search(r"Court\s*Hall\s*No\.?\s*:?\s*(\d+)", text, re.IGNORECASE)
        if hall_match and not re.match(r"^\d+\s", text):
            current_hall = hall_match.group(1)
            continue

        # Hearing type header (MOTION HEARING, FINAL HEARING, etc.)
        if any(ht in text.upper() for ht in ("MOTION HEARING", "FINAL HEARING", "LOK ADALAT")):
            if not re.match(r"^\d+\s", text):
                current_hearing_type = text.strip()
                continue

        # Category header (text in brackets or standalone)
        # e.g., "[FRESH (FOR ADMISSION) - CIVIL CASES]"
        if not re.match(r"^\d+\s", text) and len(text) > 5:
            upper = text.upper().strip().strip("[]")
            if any(
                kw in upper
                for kw in ("FRESH", "AFTER NOTICE", "PART HEARD", "CIVIL", "CRIMINAL", "MISC", "CASES", "WRIT")
            ) and upper == upper.upper():
                current_category = text.strip().strip("[]")
                continue

        # Data rows come in pairs:
        #   Row 1 (serial number): Sr | CL | Case | Petitioner | Petitioner Advocates
        #   Row 2 ("Vs." row):     Vs.| -  | -    | Respondent | Respondent Advocates
        if len(cells) >= 5:
            first_cell_text = (cells[0].inner_text() or "").strip()
            logger.debug(f"Row with {len(cells)} cells, first='{first_cell_text}': {text[:100]}")

            if first_cell_text and first_cell_text.isdigit():
                # Petitioner row — start a new entry
                cell_texts = [(c.inner_text() or "").strip() for c in cells]
                # Capture case detail URL from the link on the case number cell
                case_link = cells[2].query_selector("a") if len(cells) > 2 else None
                case_href = case_link.get_attribute("href") if case_link else None
                if case_href and not case_href.startswith("http"):
                    case_href = MPHC_BASE_URL + case_href
                entry = {
                    "serial_number": int(first_cell_text),
                    "case_number": cell_texts[2] if len(cell_texts) > 2 else None,
                    "case_url": case_href,
                    "judge_name": current_judge,
                    "hearing_type": current_hearing_type,
                    "metadata": {
                        "bench_type": current_bench_type,
                        "court_hall_no": current_hall,
                        "cl_number": cell_texts[1] if len(cell_texts) > 1 else None,
                        "hearing_category": current_category,
                        "petitioner": cell_texts[3] if len(cell_texts) > 3 else None,
                        "respondent": None,
                        "advocates_petitioner": cell_texts[4] if len(cell_texts) > 4 else None,
                        "advocates_respondent": None,
                        "remarks": cell_texts[5] if len(cell_texts) > 5 else None,
                        "search_tab": search_tab,
                    },
                }
                entries.append(entry)

            elif "Vs." in text or "VS." in text or "V/S" in text:
                # Respondent row — merge into the last entry
                if entries:
                    cell_texts = [(c.inner_text() or "").strip() for c in cells]
                    respondent = cell_texts[3] if len(cell_texts) > 3 else None
                    advocates_respondent = cell_texts[4] if len(cell_texts) > 4 else None
                    entries[-1]["metadata"]["respondent"] = respondent
                    entries[-1]["metadata"]["advocates_respondent"] = advocates_respondent

    return entries
