"""Database operations for cause list entries."""

import json
import logging

logger = logging.getLogger(__name__)


def fetch_user_id(conn, lawyer_name: str) -> str | None:
    """Fetch the user ID by matching first_name || ' ' || last_name (case-insensitive)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM public.users WHERE LOWER(first_name || ' ' || last_name) = LOWER(%s)",
            (lawyer_name,),
        )
        row = cur.fetchone()
        return str(row[0]) if row else None


def insert_cause_list_entries(conn, entries: list[dict]) -> int:
    """
    Insert cause list entries into public.cause_lists.

    Uses ON CONFLICT DO NOTHING for safe re-runs.
    Returns the count of actually inserted rows.
    """
    sql = """
        INSERT INTO public.cause_lists (
            user_id, cause_list_date, bench, court, case_number,
            judge_name, hearing_type, lawyer_name, court_hall_no, serial_number, metadata
        ) VALUES (
            %(user_id)s, %(cause_list_date)s, %(bench)s, %(court)s, %(case_number)s,
            %(judge_name)s, %(hearing_type)s, %(lawyer_name)s, %(court_hall_no)s, %(serial_number)s, %(metadata)s
        )
        ON CONFLICT ON CONSTRAINT uq_cause_list_entry DO NOTHING
    """

    inserted = 0
    with conn.cursor() as cur:
        for entry in entries:
            params = {
                "user_id": entry["user_id"],
                "cause_list_date": entry["cause_list_date"],
                "bench": entry["bench"],
                "court": entry.get("court", "MPHC"),
                "case_number": entry.get("case_number"),
                "judge_name": entry.get("judge_name"),
                "hearing_type": entry.get("hearing_type"),
                "lawyer_name": entry["lawyer_name"],
                "court_hall_no": entry.get("court_hall_no"),
                "serial_number": entry.get("serial_number"),
                "metadata": json.dumps(entry.get("metadata", {})),
            }
            cur.execute(sql, params)
            if cur.rowcount > 0:
                inserted += 1

        conn.commit()

    return inserted
