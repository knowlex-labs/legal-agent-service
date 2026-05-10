from __future__ import annotations

import logging
import re
from datetime import date
from typing import Literal

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


SENTINEL_START = "<!-- cause-title:start -->"
SENTINEL_END = "<!-- cause-title:end -->"

# Markdown ref-link comment — renders as nothing but starts with `[` not `<`,
# so the FE's already-HTML check doesn't short-circuit and skip markdown.
LEADING_MARKER = "[//]: # (cause-title-prepended)"

RoleLabel = Literal["Plaintiff", "Defendant", "Petitioner", "Respondent", "Applicant"]


class Party(BaseModel):
    full_name: str | None = Field(
        None, description="Party full name without honorific. Null if absent in source."
    )
    honorific: str | None = Field(
        None, description='Honorific present in source: "Shri", "Smt.", "Mr.", "Ms.", "M/s". Null otherwise.'
    )
    description: str | None = Field(
        None,
        description=(
            'Corporate / firm description following the name, e.g., "a company '
            'incorporated under the Companies Act, 2013". Null for individuals.'
        ),
    )
    age: str | None = Field(None, description='Age as written in source, e.g., "50 yrs".')
    occupation: str | None = Field(
        None, description='Occupation as written, e.g., "Manager Mechanical, Raymonds Ltd. (Denim Division)".'
    )
    address_intro: str = Field(
        "R/o",
        description='"R/o" for individuals; "Having its office at" for corporates / firms.',
    )
    address_lines: list[str] = Field(
        default_factory=list,
        description="Street/area, city/district/state, pincode — split as 1 to 3 lines as in the source.",
    )
    mobile: str | None = Field(None, description="Mobile number as written. Null if absent.")
    role: RoleLabel = Field(..., description="Role label for this party in the filing.")
    ordinal: int | None = Field(
        None,
        description=(
            "1-based ordinal when there are multiple parties of the same role "
            "(e.g., Respondent No. 2). Null when there is only one such party."
        ),
    )


class CauseTitleData(BaseModel):
    court_name: str | None = Field(
        None, description='Court name without "IN THE HON\'BLE" prefix, e.g., "SMALL CAUSES COURT, PUNE".'
    )
    court_seat: str | None = Field(None, description='Sitting location, e.g., "PUNE".')
    case_type: str | None = Field(
        None, description='Case type as in source caption, e.g., "Civil Suit", "Writ Petition", "Criminal Appeal".'
    )
    case_number: str | None = Field(
        None,
        description=(
            'Case number digits only, or literal blanks "___" when the parent matter '
            "is not yet numbered. Do not invent a number."
        ),
    )
    case_year: str | None = Field(None, description='4-digit year of the case caption, e.g., "2022".')
    parties: list[Party] = Field(
        default_factory=list,
        description="Plaintiff(s) / Petitioner(s) / Applicant(s) first, then Defendant(s) / Respondent(s).",
    )
    document_title: str | None = Field(
        None,
        description=(
            "Indian-court legal title for the present filing, derived from the "
            "substantive content (statute invoked + relief sought). Examples: "
            '"APPLICATION FOR TEMPORARY INJUNCTION UNDER ORDER 39 RULES 1 AND 2 '
            'OF THE CODE OF CIVIL PROCEDURE, 1908", '
            '"STAY APPLICATION ON BEHALF OF THE PLAINTIFF", '
            '"APPLICATION UNDER SECTION 9 OF THE ARBITRATION AND CONCILIATION ACT, 1996". '
            "Do NOT copy the user's filename verbatim (e.g., 'Interim Application "
            "Test 12') — it is for reference only."
        ),
    )


_EXTRACT_SYSTEM_PROMPT = """You read Indian legal filings and form input \
and extract structured cause-title data.

Rules:
1. Output ONLY values that are explicitly present in the inputs. Never \
invent court names, party names, ages, addresses, mobile numbers, case \
numbers, or years. Leave a field null when absent in BOTH the form input \
and the reference document.
2. Form input wins when both sources disagree — the form is the user's \
ground-truth intent for THIS filing.
3. Role labels for an interim application inherit from the parent suit \
when the reference shows a parent caption:
   - Reference shows "Plaintiff / Defendant" (civil suit) → use those.
   - Reference shows "Petitioner / Respondent" (writ / statutory petition) → use those.
   - Reference is silent on roles → default to "Applicant / Respondent".
4. Order parties: plaintiff/petitioner/applicant side first, defendant/respondent side second.
5. For multiple parties of the same role, set `ordinal` to 1, 2, 3, … in source order.
   Single party of a role → `ordinal` null.
6. `address_intro`: "Having its office at" for corporates / firms / partnerships; \
   "R/o" otherwise. Default "R/o".
7. `case_number`: keep literal blanks "___" if that is what the source shows. \
   Do NOT replace with a real number.
8. Strip the "IN THE HON'BLE" prefix from `court_name`. The renderer adds it back.
9. Do not output explanations or extra prose — just fill the schema.
10. `document_title`: derive an Indian-court legal title from the substantive \
   content — name the statute / order / rule invoked and the relief sought, \
   in ALL CAPS or Title Case as suits a court filing. Examples: \
   "APPLICATION FOR TEMPORARY INJUNCTION UNDER ORDER 39 RULES 1 AND 2 OF THE \
   CODE OF CIVIL PROCEDURE, 1908", "STAY APPLICATION ON BEHALF OF THE \
   PLAINTIFF", "APPLICATION UNDER SECTION 9 OF THE ARBITRATION AND \
   CONCILIATION ACT, 1996". Do NOT copy the user's filename verbatim (e.g., \
   "Interim Application Test 12") — it is informational only. If the form \
   input and reference are silent on the specific statute, fall back to a \
   neutral title such as "INTERIM APPLICATION" or "APPLICATION ON BEHALF OF \
   THE [Role]".
"""


_EXTRACT_USER_PROMPT_TEMPLATE = """Today's date: {today}

User filename for reference only (DO NOT copy verbatim — derive a proper \
Indian-court legal title from the substantive content): {document_title}

--- FORM INPUT (from the drafting wizard; PRIORITY source) ---
{instructions}
--- END FORM INPUT ---

--- REFERENCE DOCUMENT (parent matter / source PDF; fill gaps from this) ---
{reference_text}
--- END REFERENCE DOCUMENT ---
"""


_REFERENCE_CHAR_CAP = 20_000


async def extract_cause_title(
    *,
    reference_text: str | None,
    instructions: str,
    document_title: str,
    today: str,
    provider: str,
) -> CauseTitleData:
    from legal_agent.services.content_preprocessor import pick_fast_chat_model

    model_name, lc_provider = pick_fast_chat_model(provider)
    llm = init_chat_model(model_name, model_provider=lc_provider).with_structured_output(
        CauseTitleData
    )

    ref = (reference_text or "").strip()[:_REFERENCE_CHAR_CAP] or "(none provided)"
    user_prompt = _EXTRACT_USER_PROMPT_TEMPLATE.format(
        today=today,
        document_title=document_title or "(unspecified)",
        instructions=(instructions or "").strip() or "(none provided)",
        reference_text=ref,
    )

    messages = [
        SystemMessage(content=_EXTRACT_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]
    result = await llm.ainvoke(messages)
    data = result if isinstance(result, CauseTitleData) else CauseTitleData.model_validate(result)
    # If the LLM failed to derive a content-based title, leave it null so the
    # renderer emits a `[Document Title]` placeholder for the advocate. We do
    # NOT fall back to the user's filename — that's how `Interim Application
    # Test 12` ended up in the cause title.
    return data


# Inline <p> style for cause-title lines. Pins the cause-title block at the
# tight density the user signed off on (≈Tailwind my-1 + leading-snug),
# overriding the editor's looser default `[&_p]:my-2 leading-normal` that we
# want for body paragraphs.
_P_STYLE = "margin:0.25rem 0;line-height:1.375;"


def _placeholder(label: str) -> str:
    return f"[{label}]"


def _party_role_phrase(party: Party) -> str:
    if party.ordinal:
        return f"{party.role} No. {party.ordinal}"
    return party.role


def _render_name_line(party: Party) -> str:
    name = party.full_name or _placeholder(f"{party.role} Full Name")
    if party.honorific:
        full = f"{party.honorific} {name}"
    else:
        full = name
    return f'<p style="{_P_STYLE}"><strong>{full}</strong></p>'


def _render_age_occ_line(party: Party) -> str | None:
    if not party.age and not party.occupation:
        return None
    age = party.age or _placeholder(f"{party.role} Age")
    occ = party.occupation or _placeholder(f"{party.role} Occupation")
    return f'<p style="{_P_STYLE}">Age: {age}, Occ: {occ}</p>'


def _render_address_lines(party: Party) -> list[str]:
    intro = party.address_intro or "R/o"
    lines = [ln.strip() for ln in party.address_lines if ln and ln.strip()]
    if not lines:
        lines = [_placeholder(f"{party.role} Address")]
    out = [f'<p style="{_P_STYLE}">{intro}: {lines[0]}</p>']
    for ln in lines[1:]:
        out.append(f'<p style="{_P_STYLE}">{ln}</p>')
    return out


def _render_mobile_and_role(party: Party) -> list[str]:
    """Render the `Mob.no. NNNN ………Role` line.

    Uses a 1-row 2-cell borderless table so the role tag sits on the SAME
    line as the mobile, right-aligned — matching the canonical court draft
    layout. TipTap preserves table structure; inline `border:0;padding:0`
    overrides the editor's default table CSS.
    """
    role_phrase = _party_role_phrase(party)
    left = f"Mob.no. {party.mobile}" if party.mobile else ""
    right = f"………<strong>{role_phrase}</strong>"
    return [
        '<table class="cause-title-row" style="width:100%;border-collapse:collapse;border:0;margin:0;">',
        "<tbody><tr>",
        f'<td style="border:0;padding:0;text-align:left;">{left}</td>',
        f'<td style="border:0;padding:0;text-align:right;">{right}</td>',
        "</tr></tbody>",
        "</table>",
    ]


def _render_party_block(party: Party, *, ordinal_label: bool) -> list[str]:
    out: list[str] = []
    if ordinal_label and party.ordinal:
        out.append(
            f'<p style="{_P_STYLE}"><strong>{_party_role_phrase(party)}</strong></p>'
        )
    out.append(_render_name_line(party))
    if party.description:
        out.append(f'<p style="{_P_STYLE}">{party.description}</p>')
    age_line = _render_age_occ_line(party)
    if age_line:
        out.append(age_line)
    out.extend(_render_address_lines(party))
    out.extend(_render_mobile_and_role(party))
    return out


_FIRST_SIDE_ROLES: tuple[RoleLabel, ...] = ("Plaintiff", "Petitioner", "Applicant")


def render_cause_title_html(data: CauseTitleData) -> str:
    court_name = data.court_name or _placeholder("Court Name")
    court_seat = data.court_seat or _placeholder("Court Location")
    case_type = data.case_type or _placeholder("Case Type")
    case_number = data.case_number or "______"
    # Year of filing — when extractor returns null (year not present in source),
    # default to the current year. Bail / interim filings always carry the
    # filing year; an unfilled `[Year]` placeholder served no one.
    case_year = data.case_year or str(date.today().year)
    document_title = data.document_title or _placeholder("Document Title")

    lines: list[str] = [SENTINEL_START]
    # Topmost banner (court name) is rendered ALL CAPS via CSS — keeps the
    # underlying data unmutated, just renders uppercase. Indian-court
    # convention; remaining banners stay in their source case.
    lines.append(
        f'<p style="text-align:center;{_P_STYLE}text-transform:uppercase;"><strong><u>IN THE HON\'BLE {court_name}</u></strong></p>'
    )
    lines.append(
        f'<p style="text-align:center;{_P_STYLE}"><strong><u>AT {court_seat}</u></strong></p>'
    )
    lines.append(
        f'<p style="text-align:right;{_P_STYLE}"><strong>{case_type} No. {case_number} / {case_year}</strong></p>'
    )

    first_side = [p for p in data.parties if p.role in _FIRST_SIDE_ROLES]
    second_side = [p for p in data.parties if p.role not in _FIRST_SIDE_ROLES]

    if not first_side and not second_side:
        first_side = [Party(role="Plaintiff")]
        second_side = [Party(role="Defendant")]

    first_multi = len(first_side) > 1
    second_multi = len(second_side) > 1

    for party in first_side:
        lines.extend(_render_party_block(party, ordinal_label=first_multi))

    lines.append(
        f'<p style="text-align:center;{_P_STYLE}"><em><strong>Vs.</strong></em></p>'
    )

    for party in second_side:
        lines.extend(_render_party_block(party, ordinal_label=second_multi))

    lines.append(
        f'<p style="text-align:center;{_P_STYLE}"><strong><u>{document_title}</u></strong></p>'
    )
    lines.append(SENTINEL_END)
    return "\n".join(lines)


_BODY_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LLM_CAUSE_TITLE_HEADING = re.compile(
    r"^##\s+(cause\s*title|in\s+the\b|title\b|caption\b)",
    re.IGNORECASE,
)


def _is_skippable_heading(heading_full: str, heading_text: str, document_title: str | None) -> bool:
    if _LLM_CAUSE_TITLE_HEADING.match(heading_full):
        return True
    title_norm = (document_title or "").strip().lower()
    return bool(title_norm) and heading_text.strip().lower() == title_norm


def _strip_llm_cause_title_prefix(body: str, document_title: str | None) -> str:
    # Only strip if the body actually starts with a `##` heading — otherwise
    # the body is flat numbered prose (interim-application format) and we'd
    # incorrectly chop everything before the first mid-document heading
    # (e.g. `## PRAYER`).
    if not body.lstrip().startswith("##"):
        return body
    for m in _BODY_HEADING.finditer(body):
        if not _is_skippable_heading(m.group(0), m.group(1), document_title):
            return body[m.start():]
    return body


def prepend_cause_title_to_draft(body: str, data: CauseTitleData) -> str:
    if not body:
        return body
    lstripped = body.lstrip()
    if lstripped.startswith(LEADING_MARKER) or lstripped.startswith(SENTINEL_START):
        return body
    stripped = _strip_llm_cause_title_prefix(body, data.document_title).lstrip()
    rendered = render_cause_title_html(data)
    return f"{LEADING_MARKER}\n\n{rendered}\n\n{stripped}"
