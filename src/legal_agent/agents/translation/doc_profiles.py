"""Document-type registry for the translation pipeline.

A `DocProfile` couples a `DocumentType` to:

- a **layout family** (`court_filing`, `contract`, `letter`, `default`) — selects the
  CSS the renderer loads (see `css_resolver.py`);
- a **system-prompt extension** the translation generator folds into its prompt
  (register, structural cues, anti-bold rules);
- an optional **glossary overlay** that merges on top of the base language
  glossary (e.g. bail-context terms only matter for court filings);
- an optional **template skeleton** (deferred — None for all profiles in MVP).

When a request omits `document_type`, `classify_document(text)` runs a cheap
Gemini-Flash classifier over the first 2000 chars and returns a guess. Misses
fall through to the default profile — output is still readable, just with a
slightly off register.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from legal_agent.models.documents import DocumentType

logger = logging.getLogger(__name__)


LayoutFamily = Literal["court_filing", "contract", "letter", "default"]


@dataclass
class DocProfile:
    layout_family: LayoutFamily
    css_id: str
    system_prompt_extension: str = ""
    glossary_overlay: dict[str, dict[str, str]] = field(default_factory=dict)
    template_skeleton: str | None = None


# ── Layout-family routing ────────────────────────────────────────────────────


_LAYOUT_FAMILY: dict[DocumentType, LayoutFamily] = {
    # Court filings — anything filed before a court.
    DocumentType.BAIL_APPLICATION: "court_filing",
    DocumentType.ANTICIPATORY_BAIL: "court_filing",
    DocumentType.SLP: "court_filing",
    DocumentType.QUASHING_PETITION: "court_filing",
    DocumentType.REVISION_PETITION: "court_filing",
    DocumentType.EXECUTION_PETITION: "court_filing",
    DocumentType.CRIMINAL_APPEAL: "court_filing",
    DocumentType.WRITTEN_STATEMENT: "court_filing",
    DocumentType.WRITTEN_ARGUMENTS: "court_filing",
    DocumentType.PETITION: "court_filing",
    DocumentType.AFFIDAVIT: "court_filing",
    DocumentType.APPLICATION: "court_filing",
    DocumentType.APPLICATION_DRAFT: "court_filing",
    DocumentType.CONSUMER_COMPLAINT: "court_filing",

    # Contracts.
    DocumentType.CONTRACT: "contract",
    DocumentType.AGREEMENT: "contract",

    # Letters / notices.
    DocumentType.LEGAL_NOTICE: "letter",
    DocumentType.DEMAND_NOTICE: "letter",

    # Other / no specific layout.
    DocumentType.PATENT: "default",
}


# ── Per-family system-prompt extensions and glossary overlays ───────────────


_COURT_FILING_PROMPT = """═══ COURT-FILING REGISTER ═══

This is a court filing. Use the formal register a senior advocate would use in
the matching High Court (e.g. for Hindi: माननीय न्यायालय, प्रार्थना है कि — NOT
"अनुरोध है" or conversational forms).

STRUCTURE — emit these markers as the source supports them:

- `# <Cause Title>` — the case caption (court name, parties, case number).
  Centre-aligned in the rendered PDF; place ONLY the caption here, no body text.
- `## FACTS` / `## GROUNDS` / `## PRAYER` / `## VERIFICATION` — major sections,
  each as their own H2 heading. Translate the section name, do NOT keep English.
- Numbered grounds: `1.`, `2.`, `3.` — one per line. Do NOT bold the numbers.
- Verification clause at the end goes inside `> ...` blockquote OR plain text —
  do NOT bold it.

ANTI-BOLD RULES (mandatory):

- DO NOT bold inline citations, FIR numbers, dates, statute references, or
  monetary amounts. `Section 438 CrPC, 1973` is plain text — never `**Section 438 CrPC, 1973**`.
- DO NOT bold party names, court names, advocate names mid-paragraph.
- Reserve `**...**` strictly for genuine emphasis (rare in court filings).

Citation preservation: keep `Section X of <Act>, YYYY`, `(YYYY) V SCC P`, FIR
numbers, and dates EXACTLY as they appear in the source — do not transliterate
or re-format.
"""


_CONTRACT_PROMPT = """═══ CONTRACT / AGREEMENT REGISTER ═══

This is a contract or agreement. Use formal commercial register and consistent
terminology throughout — pick ONE term per concept and reuse it (do not switch
between अनुबंध / समझौता / करार for the same concept).

STRUCTURE:

- `# <Title>` — the contract title (e.g. "EMPLOYMENT AGREEMENT"). Left-aligned.
- `## 1. DEFINITIONS` / `## 2. TERM` / `## 3. PAYMENT` etc. — clause headings
  with the leading number preserved.
- Sub-clauses use nested numbering: `1.1`, `1.1.1` — do NOT bold the numbers.
- Recitals (WHEREAS clauses) appear before clause 1; keep them as paragraphs.

ANTI-BOLD RULES:

- DO NOT bold defined terms inline ("Confidential Information", "Effective Date").
  Capitalisation already signals a defined term.
- Reserve `**...**` for genuine emphasis only.
"""


_LETTER_PROMPT = """═══ LEGAL NOTICE / LETTER REGISTER ═══

This is a legal notice or formal letter. Keep the conventional structure of an
Indian legal notice: sender block at the top, recipient block, subject line
(`# Subject` or `## Re:`), salutation ("Sir/Madam"), numbered/lettered body
paragraphs, conclusion, sign-off.

ANTI-BOLD RULES:

- DO NOT bold names, addresses, dates, or statute references.
- The subject line keeps its emphasis from the heading marker (`#` / `##`),
  not from `**...**`.
"""


# Devanagari / Hindi overlays. Other languages inherit the base glossary
# unchanged for MVP — extend per-language as needed.
_COURT_FILING_GLOSSARY: dict[str, dict[str, str]] = {
    "hindi": {
        "Bail": "जमानत",
        "Anticipatory Bail": "अग्रिम जमानत",
        "FIR": "प्रथम सूचना रिपोर्ट",
        "Charge Sheet": "आरोप पत्र",
        "Hon'ble Court": "माननीय न्यायालय",
        "Prayer": "प्रार्थना",
        "Grounds": "आधार",
        "Facts": "तथ्य",
        "Verification": "सत्यापन",
        "It is therefore prayed": "अतः प्रार्थना है कि",
    },
}


_CONTRACT_GLOSSARY: dict[str, dict[str, str]] = {
    "hindi": {
        "Agreement": "अनुबंध",
        "Effective Date": "प्रभावी तिथि",
        "Term": "अवधि",
        "Termination": "समाप्ति",
        "Indemnity": "क्षतिपूर्ति",
        "Confidentiality": "गोपनीयता",
        "Governing Law": "शासी विधि",
        "Jurisdiction": "अधिकार क्षेत्र",
        "Force Majeure": "अप्रत्याशित घटना",
    },
}


_LETTER_GLOSSARY: dict[str, dict[str, str]] = {
    "hindi": {
        "Subject": "विषय",
        "Re:": "विषय:",
        "Notice": "नोटिस",
        "Demand": "मांग",
        "Within fifteen days": "पंद्रह दिनों के भीतर",
    },
}


_FAMILY_PROFILES: dict[LayoutFamily, DocProfile] = {
    "court_filing": DocProfile(
        layout_family="court_filing",
        css_id="court_filing.css",
        system_prompt_extension=_COURT_FILING_PROMPT,
        glossary_overlay=_COURT_FILING_GLOSSARY,
    ),
    "contract": DocProfile(
        layout_family="contract",
        css_id="contract.css",
        system_prompt_extension=_CONTRACT_PROMPT,
        glossary_overlay=_CONTRACT_GLOSSARY,
    ),
    "letter": DocProfile(
        layout_family="letter",
        css_id="letter.css",
        system_prompt_extension=_LETTER_PROMPT,
        glossary_overlay=_LETTER_GLOSSARY,
    ),
    "default": DocProfile(
        layout_family="default",
        css_id="default.css",
        system_prompt_extension="",
        glossary_overlay={},
    ),
}


def resolve_profile(document_type: DocumentType | None) -> DocProfile:
    """Return the profile for a document type. None → default profile."""
    if document_type is None:
        return _FAMILY_PROFILES["default"]
    family = _LAYOUT_FAMILY.get(document_type, "default")
    return _FAMILY_PROFILES[family]


# ── Auto-detect classifier ──────────────────────────────────────────────────


_CLASSIFIER_PROMPT = """You classify Indian legal documents. Read the snippet and reply with EXACTLY ONE of these labels (no prose, no quotes, no JSON):

bail_application, anticipatory_bail, criminal_appeal, slp, quashing_petition, revision_petition, execution_petition, written_statement, written_arguments, petition, affidavit, application, application_draft, consumer_complaint, contract, agreement, legal_notice, demand_notice, patent, unknown

Pick the single best match. Reply `unknown` if you cannot tell.
"""


async def classify_document(text: str) -> DocumentType | None:
    """Cheap-LLM classifier for the leading slice of source text.

    Best-effort — never raises. Returns None on classifier failure or `unknown`
    label, letting the caller fall through to the default profile.
    """
    if not text or not text.strip():
        return None

    snippet = text.strip()[:2000]

    try:
        from langchain.chat_models import init_chat_model
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = init_chat_model(
            "gemini-3.1-flash-lite-preview",
            model_provider="google-genai",
            max_tokens=32,
        )
        response = await llm.ainvoke([
            SystemMessage(content=_CLASSIFIER_PROMPT),
            HumanMessage(content=snippet),
        ])
        raw = response.content
        if isinstance(raw, list):
            raw = "".join(
                part if isinstance(part, str) else part.get("text", "")
                for part in raw
            )
        label = raw.strip().lower().strip("`'\"")
        if label == "unknown" or not label:
            return None

        try:
            return DocumentType(label)
        except ValueError:
            logger.debug(f"Classifier returned unknown label: {label!r}")
            return None
    except Exception as exc:
        logger.warning(f"Document-type classification failed: {exc}")
        return None
