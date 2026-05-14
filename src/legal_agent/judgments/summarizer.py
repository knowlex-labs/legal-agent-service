"""Judgment summarizer — generates structured legal summaries from raw markdown text."""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior Indian legal analyst. You will be given the full text of a court judgment.
Your task is to produce a comprehensive, structured legal summary that a lawyer can use as a
reliable reference. Do NOT skip any legally significant detail. Think as a lawyer, not a journalist."""

_SUMMARY_FORMAT = """
MANDATORY STRUCTURE — follow this exact format:

**[CASE TITLE]**
Court: [Full court name]
Case Number: [e.g., Criminal Revision Application No. 278 of 2024]
Coram: [Judge name(s) and designation]
Date of Judgment: [DD Month YYYY]
Jurisdiction: [Appellate / Revisional / Original / Writ / Reference]

---

**1. PARTIES**
- Petitioner/Appellant/Applicant: [Name, role in proceedings]
- Respondent(s): [Name, role in proceedings]
- Advocates: [Counsel for each party, if mentioned]

---

**2. CASE BACKGROUND**
[Provide a detailed factual narrative in chronological order. Include:]
- Background facts leading to the dispute or offence
- Key dates and events in a timeline
- Procedural history: FIR / complaint / suit / petition filed, lower court/tribunal orders, present proceedings
- Nature of the present proceedings (revision / appeal / writ / reference)

---

**3. CHARGES / CLAIMS**
[List all charges, sections, or legal claims involved:]
- Offences charged (e.g., Section 306 IPC, Section 107 IPC) OR civil claims
- Specific allegations supporting each charge/claim
- Stage of proceedings (framing of charge / trial / discharge / sentencing / execution)

---

**4. ARGUMENTS**
**4a. Petitioner/Appellant/Applicant:**
[Summarise all arguments made, point by point. Include:]
- Factual arguments with specific references to evidence, pages, exhibits
- Legal arguments citing specific sections, articles, precedents
- Any contradictions in prosecution/opposite party evidence that were highlighted

**4b. Respondent/Opposite Party:**
[Summarise all counter-arguments made, point by point.]

---

**5. EVIDENCE EXAMINED**
[List all evidence the court examined, with findings on each:]
- Documentary evidence (FIR, bank statements, call records, WhatsApp chats, medical reports, etc.)
  → What it shows / what it fails to establish
- Witness statements (Section 161 / 164 CrPC statements, affidavits)
  → Key contents and inconsistencies noted
- Physical evidence (seized items, forensic reports, post-mortem reports)
  → Findings and relevance
- Digital evidence (mobile data, CCTV, app records)
  → What was recovered and what was absent

---

**6. LEGAL PROVISIONS APPLIED**
[List every statute, section, article, or rule the court applied or interpreted:]
- Section / Article number — brief statement of what it provides
- How the court interpreted or applied it in this case
Include: IPC/BNS sections, CrPC/BNSS sections, Constitution articles, specific statutes (e.g., POCSO, NI Act, Companies Act, Hindu Marriage Act, Transfer of Property Act), rules, and schedules.

---

**7. PRECEDENTS CITED**
[For EACH case cited by either party or the court, provide:]
- Case name and citation (year, court, SCC/SCR/AIR/SCC Online reference)
- The legal proposition for which it was cited
- Whether the court followed, distinguished, or declined to follow it
- One-line holding relevant to the present case

---

**8. COURT'S ANALYSIS AND FINDINGS**
[Reproduce the court's reasoning in detail:]
- Issue-by-issue analysis (if the court framed issues)
- How the court evaluated each piece of evidence
- Key findings of fact
- Key findings of law
- Whether any lower court findings were upheld, reversed, or modified, and why

---

**9. FINAL ORDER / OPERATIVE PART**
[State the exact outcome:]
- Order granted / dismissed / modified
- Specific reliefs granted (discharge, acquittal, bail, injunction, decree, etc.)
- Any directions to lower courts, parties, or authorities
- Costs, if any
- Any conditions attached to the order

---

**10. SIGNIFICANCE / KEY TAKEAWAY**
[2–4 sentences for a legal professional:]
- The core legal principle settled or reaffirmed by this judgment
- Any important limitation or caveat the court noted
- Practical impact on similar future cases

---

EDGE CASE INSTRUCTIONS (apply silently — do not mention these in output):
- If a section of the judgment text is garbled or missing (scanned PDF artifact), work with what is available and note "[Text partially legible in source]" only if critical information is affected.
- If the court does not frame explicit issues, reconstruct them from the analysis section.
- If no citation is mentioned, write "Citation not available in document."
- If the case involves multiple accused, address each separately under Parties and Charges.
- If it is an interlocutory order (bail, injunction, stay), note "This is an interlocutory order — final merits not decided" under Section 9.
- If a referenced precedent is only partially quoted, still extract its name, citation (if given), and the proposition it was cited for.
- If the judgment is in a language other than English, note the original language and that this summary is based on an English translation/extraction.
- Never fabricate section numbers, case citations, or dates not present in the text.
- If the disposal nature is ambiguous (e.g., "disposed of in terms of the above"), interpret it based on the operative paragraph and state your interpretation.
"""

_USER_TEMPLATE = (
    "Generate a comprehensive structured legal summary for the following judgment text.\n\n"
    + _SUMMARY_FORMAT
    + "\n\n--- JUDGMENT TEXT ---\n\n{text}"
)


async def generate_summary(text: str, model: str | None = None) -> str:
    """Generate a structured 10-section legal summary from raw judgment markdown.

    Args:
        text: Raw markdown/text of the judgment (from S3).
        model: Optional model override (e.g. 'gemini-2.5-flash', 'gpt-4o'). Falls
               back to the service's chat_llm_default_model.

    Returns:
        Structured markdown summary string.
    """
    settings = get_settings()
    model_id = model or settings.chat_llm_default_model

    provider = _infer_provider(model_id)
    llm = init_chat_model(model_id, model_provider=provider)

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=_USER_TEMPLATE.format(text=text[:100_000])),
    ]

    logger.info("Generating judgment summary (model=%s, text_len=%d)", model_id, len(text))
    response = await llm.ainvoke(messages)
    summary = response.content if hasattr(response, "content") else str(response)
    logger.info("Judgment summary generated (%d chars)", len(summary))
    return summary


def _infer_provider(model_id: str) -> str:
    if model_id.startswith("gemini"):
        return "google-genai"
    if model_id.startswith("claude"):
        return "anthropic"
    return "openai"
