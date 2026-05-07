"""Extract structured form-field suggestions from an uploaded source document.

Powers the drafting page's auto-fill: when an advocate attaches a PDF on the
draft form, this module pulls out party names, court details, FIR numbers,
facts and relief sought so the form can pre-populate. The advocate just
reviews + corrects rather than re-typing.

Lightweight by design — single sync LLM call, fast model, capped input. The
client merges the response into form state non-destructively (only fills
empty fields), so anything we miss or get wrong is harmless.
"""

import asyncio
import logging

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from legal_agent.agents.translation.structure_aware_extractor import (
    extract_for_translation,
)
from legal_agent.clients.decryption import DecryptionService
from legal_agent.clients.s3_client import S3Client

logger = logging.getLogger(__name__)


_TEXT_CHAR_CAP = 30_000  # metadata sits up-front; first 30K is plenty


class _SuggestedFields(BaseModel):
    """Pydantic schema the LLM populates. Every field is optional —
    we only auto-fill what the document actually evidences."""

    applicant: str | None = Field(
        None, description="Applicant / Petitioner / Plaintiff full name with title (Shri/Smt) if mentioned"
    )
    opposite_party: str | None = Field(
        None, description="Opposite party / Respondent / Defendant / State if mentioned"
    )
    appellant: str | None = Field(
        None, description="Appellant name (criminal/civil appeal documents only)"
    )
    respondent: str | None = Field(
        None, description="Respondent name (separate from opposite_party for criminal appeals / writs)"
    )
    petitioner: str | None = Field(
        None, description="Petitioner name (writ petitions / SLPs)"
    )
    court_details: str | None = Field(
        None, description="Court name, location, case number — e.g. 'Hon'ble High Court of Madhya Pradesh, Jabalpur. M.Cr.C. No. 12345/2024'"
    )
    fir_details: str | None = Field(
        None, description="FIR number, date, police station, sections invoked"
    )
    facts: str | None = Field(
        None, description="Chronological summary of relevant facts — 3 to 8 sentences"
    )
    relief_sought: str | None = Field(
        None, description="Specific relief / order / direction prayed for"
    )
    grounds: str | None = Field(
        None, description="Legal grounds being argued, if articulated in the source doc"
    )
    impugned_order: str | None = Field(
        None, description="Details of the order being challenged (court, date, case no.)"
    )
    impugned_judgment: str | None = Field(
        None, description="Details of the judgment being challenged (criminal appeal / SLP)"
    )


_EXTRACT_SYSTEM_PROMPT = """You read Indian legal documents and extract \
structured metadata so a drafting form can pre-populate the fields below.

Rules:
1. Output ONLY values you can quote or directly paraphrase from the document. \
Never invent names, dates, FIR numbers, or court details.
2. If a field is not clearly present in the document, leave it null. Do not \
guess.
3. For names, use the form they appear in the document (titles included).
4. For court_details, combine court name + bench/seat + case number into one \
human-readable string.
5. For facts, summarise 3-8 short sentences in chronological order. Stick to \
facts present in the document.
6. Do not output explanations, headings, or extra prose — just fill the schema.
"""


_EXTRACT_USER_PROMPT_TEMPLATE = """Extract drafting-form metadata from the document below.

--- DOCUMENT ---
{text}
--- END ---
"""


def _resolve_model() -> tuple[str, str]:
    """Pick a fast extraction model. Mirrors enhance_content's choice in
    content_preprocessor.py."""
    from legal_agent.config import get_settings

    provider = get_settings().llm_provider
    fast_models = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-haiku-latest",
        "gemini": "gemini-2.0-flash",
    }
    provider_map = {"openai": "openai", "anthropic": "anthropic", "gemini": "google-genai"}
    return fast_models.get(provider, "gpt-4o-mini"), provider_map.get(provider, provider)


async def extract_draft_fields(
    file_id: str,
    user_id: str,
    s3_client: S3Client,
    decryption: DecryptionService | None,
) -> dict[str, str]:
    """Download → decrypt → parse → LLM-extract. Returns only non-null fields.

    Errors propagate as RuntimeError so the route can map them to HTTP 4xx/5xx.
    Empty / unparseable docs return an empty dict (the FE just leaves the
    form alone)."""
    if not decryption:
        raise RuntimeError(
            "Document decryption is not configured (DOCUMENT_ENCRYPTION_MASTER_KEY missing)."
        )

    encrypted = await s3_client.download_bytes(file_id)
    plaintext = await asyncio.to_thread(decryption.decrypt_file, encrypted, user_id)
    filename = file_id.rsplit("/", 1)[-1]
    text, _ledger = await asyncio.to_thread(
        extract_for_translation, plaintext, filename, None
    )

    if not text or len(text.strip()) < 50:
        logger.info(
            f"[extract_draft_fields] Skipped {file_id}: document text too short "
            f"({len(text) if text else 0} chars)"
        )
        return {}

    if len(text) > _TEXT_CHAR_CAP:
        text = text[:_TEXT_CHAR_CAP]

    model_name, provider = _resolve_model()
    llm = init_chat_model(model_name, model_provider=provider).with_structured_output(
        _SuggestedFields
    )
    try:
        result = await llm.ainvoke([
            SystemMessage(content=_EXTRACT_SYSTEM_PROMPT),
            HumanMessage(content=_EXTRACT_USER_PROMPT_TEMPLATE.format(text=text)),
        ])
    except Exception as exc:
        logger.warning(
            f"[extract_draft_fields] LLM failed for {file_id}: {exc}", exc_info=True
        )
        raise RuntimeError(f"Field extraction failed: {exc}") from exc

    if not isinstance(result, _SuggestedFields):
        logger.warning(
            f"[extract_draft_fields] Unexpected LLM result type {type(result)} for {file_id}"
        )
        return {}

    suggested: dict[str, str] = {}
    for field_name, value in result.model_dump().items():
        if value and isinstance(value, str) and value.strip():
            suggested[field_name] = value.strip()

    logger.info(
        f"[extract_draft_fields] Extracted {len(suggested)} fields from {file_id}"
    )
    return suggested
