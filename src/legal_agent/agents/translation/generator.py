"""Translation generator — LLM-based legal document translation with strict terminology."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from legal_agent.agents.translation.glossary_loader import get_glossary, merge_overlay
from legal_agent.config import get_settings
from legal_agent.models.documents import LANGUAGE_NATIVE_NAMES, TranslationLanguage

if TYPE_CHECKING:
    from legal_agent.agents.translation.doc_profiles import DocProfile

logger = logging.getLogger(__name__)

_PROVIDER_MAX_TOKENS: dict[str, int] = {
    "openai": 16384,
    "anthropic": 16384,
    "google-genai": 16384,
    "sarvam": 16384,
}

# Approximate chars per chunk for splitting long documents.
_CHUNK_MAX_CHARS = 12000

# Simple aliases the frontend can send instead of full model names.
# The "sarvam" alias resolves at call time from settings.sarvam_chat_model so
# callers can swap sarvam-m ↔ sarvam-30b ↔ sarvam-105b without redeploys.
_MODEL_ALIASES: dict[str, str] = {
    "gemini": "gemini-3.1-flash-lite-preview",
    "claude": "claude-haiku-4-5-20251001",
    "openai": "gpt-4.1-mini",
}

# ── Mandatory legal terminology per language ──────────────────────────────────
# Loaded at module init from `data/glossaries/base.json` via `glossary_loader`.
# Doc-type profiles (see `doc_profiles.py`) layer their own overlay on top in
# `_build_system_prompt`, so a bail filing gets bail-specific terms without
# affecting unrelated translations.


_LATIN_MAXIMS = (
    "habeas corpus, certiorari, mandamus, quo warranto, inter alia, "
    "prima facie, suo motu, ab initio, res judicata, obiter dictum, "
    "mutatis mutandis, ultra vires, locus standi, amicus curiae, "
    "ratio decidendi, stare decisis, de novo, ex parte, ad interim, "
    "mens rea, actus reus, bona fide, mala fide, sub judice, in limine"
)


def _resolve_model(model: str) -> tuple[str, str]:
    """Resolve a model alias or full name to (model_name, provider).

    Provider codes returned here: "openai", "anthropic", "google-genai", "sarvam".
    ("sarvam" is a synthetic marker — actual LangChain init uses the OpenAI
    provider against Sarvam's OpenAI-compatible endpoint; see _init_llm.)
    """
    if model == "sarvam":
        return get_settings().sarvam_chat_model, "sarvam"
    if model in _MODEL_ALIASES:
        model = _MODEL_ALIASES[model]

    if model.startswith("gemini"):
        return model, "google-genai"
    if model.startswith("claude"):
        return model, "anthropic"
    if model.startswith("gpt") or model.startswith("o"):
        return model, "openai"
    if model.startswith("sarvam"):
        return model, "sarvam"
    return model, get_settings().llm_provider


def _init_llm(model: str, provider: str, max_tokens: int):
    """Initialise the LangChain chat model for the given provider.

    Sarvam is served via its OpenAI-compatible endpoint, so we init with
    model_provider="openai" and inject base_url + api_key.
    """
    if provider == "sarvam":
        settings = get_settings()
        if not settings.sarvam_api_key:
            raise RuntimeError(
                "SARVAM_API_KEY is not configured but translation requested provider='sarvam'. "
                "Set SARVAM_API_KEY in .env or pick a different model."
            )
        return init_chat_model(
            model,
            model_provider="openai",
            max_tokens=max_tokens,
            base_url=settings.sarvam_api_base_url,
            api_key=settings.sarvam_api_key,
        )
    return init_chat_model(model, model_provider=provider, max_tokens=max_tokens)


def _format_term_table(terms: dict[str, str]) -> str:
    """Format terminology dict as a strict mapping block for the prompt."""
    lines = [f"  {eng} → {translated}" for eng, translated in terms.items()]
    return "\n".join(lines)


def _build_system_prompt(
    target_language: TranslationLanguage,
    source_language: TranslationLanguage | None,
    profile: "DocProfile | None" = None,
) -> str:
    target_name = LANGUAGE_NATIVE_NAMES[target_language.value]
    source_desc = (
        f"Source language: {LANGUAGE_NATIVE_NAMES[source_language.value]} ({source_language.value})."
        if source_language
        else "Auto-detect the source language."
    )

    # Get terminology for target language; merge any doc-type overlay on top.
    base_terms = get_glossary(target_language.value)
    overlay = (
        profile.glossary_overlay.get(target_language.value, {})
        if profile and profile.glossary_overlay
        else {}
    )
    terms = merge_overlay(base_terms, overlay)
    if terms:
        term_section = f"""═══ MANDATORY TERMINOLOGY ═══

When you encounter ANY of these English legal terms, you MUST use EXACTLY the mapped translation below.
No alternatives. No invention. No transliteration. This is non-negotiable.

{_format_term_table(terms)}

For any legal term NOT in this list: use the established term from {target_language.value} High Court proceedings.
If NO established term exists, keep the English term and add a parenthetical explanation in {target_language.value}."""
    else:
        term_section = f"""═══ TERMINOLOGY ═══

Use established legal terminology as used in {target_language.value} High Court and subordinate court proceedings.
If no established term exists for a concept, keep the English term and add a parenthetical explanation in {target_language.value}."""

    profile_section = (
        f"\n{profile.system_prompt_extension}\n" if profile and profile.system_prompt_extension else ""
    )

    return f"""You are an Indian lawyer who drafts legal documents in {target_language.value} ({target_name}).
You write like a native — simple, precise, formal. NOT like a translator.
TASK: Translate the provided legal document into {target_language.value} ({target_name}).
{source_desc}
{profile_section}
{term_section}

═══ TRANSLATION STYLE ═══

1. Use SIMPLE, STANDARD legal {target_language.value}
   - Avoid overly complex or Sanskrit-heavy words
   - Use commonly understood legal terms, not literary ones
   - Example (Hindi): Use "गोपनीय जानकारी" NOT "गोपनीयता संबंधी सूचना"

2. Be CONSISTENT — pick ONE term for each concept and reuse it throughout
   - Do NOT alternate between synonyms (e.g., "अनुबंध" and "समझौता" for the same thing)

3. Use BILINGUAL format on FIRST occurrence of important legal terms:
   - Non-Compete (प्रतिस्पर्धा-निषेध)
   - Confidential Information (गोपनीय जानकारी)
   - Intellectual Property (बौद्धिक संपदा)
   After first occurrence, use only the {target_language.value} term.

4. Do NOT over-translate terms commonly used in English in Indian contracts:
   - KEEP in English: Work Product, Non-Disparagement, IP, HR, CEO, CTO, etc.
   - KEEP in English: technical terms, company names, product names

5. Remove repetition and unnecessary wording — be concise but formal

═══ STRICTLY FORBIDDEN ═══

- Do NOT transliterate English words into {target_name} script (e.g., "ऑब्जर्वेशन", "टर्मिनेशन", "नॉन-कम्पीट")
- Do NOT invent new legal terms — if unsure, keep the English term with bilingual format
- Do NOT produce informal or conversational language
- Do NOT mix English and {target_language.value} randomly mid-sentence
- Do NOT translate word-by-word — use natural legal phrasing

═══ PRESERVE AS-IS (do NOT translate) ═══

- Latin legal maxims: {_LATIN_MAXIMS}
- Court names, statute titles, case citations — keep EXACTLY as-is in English
- Official designations: "Additional Sessions Judge", "District Magistrate", etc.
- Numbers, dates (DD/MM/YYYY), monetary amounts (Rs. X,XX,XXX/-)
- Statute references on first occurrence: "Section 438 of CrPC, 1973 (धारा 438, दं.प्र.सं., 1973)"

═══ FORMATTING ═══

- Preserve all markdown headings (##, ###), numbering, bullet points, paragraph breaks exactly
- **Bold text** → translate content inside, keep ** markers
- Tables → translate cell content only, keep structure
- Remove repeated headers, footers, and address blocks from OCR artifacts
- Keep clause structure and numbering exactly the same

═══ EXAMPLES ═══

English: "The Employee agrees to maintain confidentiality of all Confidential Information during and after employment."
→ Hindi: "कर्मचारी सहमत है कि वह रोजगार के दौरान और उसके बाद सभी Confidential Information (गोपनीय जानकारी) की गोपनीयता बनाए रखेगा।"

English: "The Employee shall be on probation for a period of three (3) months from the date of joining."
→ Hindi: "कर्मचारी कार्यभार ग्रहण की तिथि से तीन (3) माह की परिवीक्षा अवधि (Probation Period) पर रहेगा।"

═══ OCR CLEANUP ═══

Source may come from OCR/PDF extraction. Remove garbage strings, repeated headers/footers, page markers. Fix obvious OCR errors. Keep all substantive legal content.

═══ SELF-CHECK (do this before finalizing) ═══

1. Every legal term uses the MANDATORY mapping — not a guess or transliteration
2. Bilingual format used on first occurrence of important terms
3. No English words transliterated into {target_name} script
4. Consistent terminology throughout — no synonym switching
5. Reads like drafted by an Indian lawyer, not like a translation

═══ OUTPUT ═══

Output ONLY the translated document in clean markdown. No preamble, notes, code fences, metadata, or wrapper markers."""


def _enforce_glossary(text: str, target_language: str) -> str:
    """Post-process translated text to enforce mandatory legal terminology.

    Scans for English legal terms that should have been translated per the glossary
    and replaces them. Handles case-insensitive matching and common plural/possessive forms.

    Skips replacement when:
    - the term is already followed by a bilingual parenthetical `Term (translation)`
    - the match is a substring of a longer glossary entry, e.g. "Code" inside
      "Code of Civil Procedure" (the longer entry, when present, is handled
      first via length-sorted iteration; short terms then skip if the longer
      form exists in the text).
    """
    terms = get_glossary(target_language)
    if not terms:
        return text

    # Build a set of compound entries that embed other glossary keys as
    # whole words — e.g. {"Code of Civil Procedure"} embeds "Code". When
    # a short key's match falls inside one of these compound phrases, we
    # skip the replacement so "Code of Civil Procedure" is preserved.
    all_keys = list(terms.keys())
    compound_by_short: dict[str, list[str]] = {}
    for short in all_keys:
        for longer in all_keys:
            if longer is short or len(longer) <= len(short):
                continue
            if re.search(rf"\b{re.escape(short)}\b", longer, re.IGNORECASE):
                compound_by_short.setdefault(short, []).append(longer)

    # Process longer keys first so "Code of Civil Procedure" consumes its
    # occurrences before "Code" is considered. This keeps offsets sane and
    # avoids double-translation.
    for eng_term in sorted(all_keys, key=len, reverse=True):
        translated_term = terms[eng_term]
        # Skip if the translated term is already present (LLM got it right)
        if translated_term in text:
            continue

        # Require word boundaries AND reject matches that are followed by
        # an ASCII lowercase letter or underscore — prevents matching the
        # start of a longer English phrase (e.g. "Code" in "Codebook").
        # The `s?` still allows simple plurals.
        pattern = rf"\b{re.escape(eng_term)}s?\b(?![A-Za-z_])"
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        for match in reversed(matches):  # reverse to preserve offsets
            start, end = match.start(), match.end()

            # Skip if the match sits inside a longer glossary compound
            # that also appears in the text. "Code" in "Code of Civil
            # Procedure" must be preserved because the compound has its
            # own canonical translation.
            longer_variants = compound_by_short.get(eng_term, [])
            if longer_variants:
                # Widen the inspection window to the length of the longest
                # compound candidate, centred on the match.
                max_len = max(len(v) for v in longer_variants) + 4
                window_start = max(0, start - max_len)
                window_end = min(len(text), end + max_len)
                window = text[window_start:window_end]
                if any(
                    re.search(rf"\b{re.escape(v)}\b", window, re.IGNORECASE)
                    for v in longer_variants
                ):
                    continue

            # Skip if the term is already presented in bilingual format:
            # `Term (translation)` — tolerate any whitespace before the paren.
            after = text[end:end + 4]
            if re.match(r"\s*\(", after):
                continue

            text = text[:start] + translated_term + text[end:]

    return text


def _clean_output(text: str) -> str:
    """Strip wrapper markers and reasoning traces the LLM may echo back.

    Sarvam-m (and other hybrid reasoning models) emit <think>...</think> blocks
    before the actual answer. Strip them — and if the </think> closer is missing
    (output was truncated mid-thinking), drop everything before the next likely
    content marker as a safety net.
    """
    # Remove closed reasoning blocks (case-insensitive; supports <think> and <thinking>).
    text = re.sub(
        r"<think(?:ing)?>.*?</think(?:ing)?>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove unclosed reasoning block: drop from <think> to either end-of-text
    # or a markdown heading / --- separator that plausibly starts the answer.
    text = re.sub(
        r"<think(?:ing)?>.*?(?=(^#{1,6}\s|^---\s*$|\Z))",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    text = re.sub(r"-{2,}\s*BEGIN\s+DOCUMENT\s*-{2,}", "", text)
    text = re.sub(r"-{2,}\s*END\s+DOCUMENT\s*-{2,}", "", text)
    return text.strip()


def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at paragraph boundaries, respecting max_chars."""
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para) + 2  # +2 for the \n\n separator
        if current_len + para_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _build_user_message(source_text: str, part_info: str = "") -> str:
    extra = f"\n{part_info}" if part_info else ""
    return f"""Translate the following legal document accurately. Preserve all formatting, structure, and citations exactly as specified in your instructions.{extra}

{source_text}"""


class TranslationGenerator:
    """Generates legal document translations via LLM calls.

    Long documents are automatically split into chunks and translated
    sequentially to avoid hitting output token limits.
    """

    def __init__(self) -> None:
        pass

    async def generate(
        self,
        source_text: str,
        target_language: TranslationLanguage,
        source_language: TranslationLanguage | None = None,
        model: str | None = None,
        profile: "DocProfile | None" = None,
    ) -> str:
        """Translate a legal document. Returns translated markdown text.

        `profile` carries the doc-type system-prompt extension and glossary
        overlay (consumed in `_build_system_prompt`). None → default behaviour.
        """
        model = model or "gemini"
        model, provider = _resolve_model(model)
        max_tokens = _PROVIDER_MAX_TOKENS.get(provider, 16384)

        llm = _init_llm(model, provider, max_tokens)
        system_prompt = _build_system_prompt(target_language, source_language, profile)
        # sarvam-m is a hybrid reasoning model — reasoning adds no value for
        # deterministic translation and burns tokens. /no_think disables it.
        if provider == "sarvam":
            system_prompt = "/no_think\n\n" + system_prompt

        chunks = _split_into_chunks(source_text, _CHUNK_MAX_CHARS)

        logger.info(
            f"[translate] {source_language or 'auto'} → {target_language.value} "
            f"| model={model} | input_chars={len(source_text)} | chunks={len(chunks)}"
        )

        translated_parts: list[str] = []
        for i, chunk in enumerate(chunks, 1):
            if len(chunks) > 1:
                user_message = _build_user_message(
                    chunk,
                    part_info=f"This is part {i} of {len(chunks)}. Translate this part completely.",
                )
            else:
                user_message = _build_user_message(chunk)

            logger.info(f"[translate] Translating chunk {i}/{len(chunks)} ({len(chunk)} chars)")

            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_message),
            ])

            content = response.content
            if isinstance(content, list):
                content = "".join(
                    block if isinstance(block, str) else block.get("text", "")
                    for block in content
                )
            cleaned = _clean_output(content)
            logger.info(
                f"[translate] Chunk {i}/{len(chunks)} translated: "
                f"{len(chunk)} → {len(cleaned)} chars "
                f"(ratio {len(cleaned) / max(len(chunk), 1):.2f})"
            )
            # Empty chunk → abort instead of quietly assembling a translation
            # with a hole. An empty response is almost always API failure,
            # content filter trip, or over-aggressive _clean_output stripping.
            if not cleaned.strip():
                raise RuntimeError(
                    f"Translation chunk {i}/{len(chunks)} returned empty output "
                    f"(input was {len(chunk)} chars). Aborting to avoid partial translation. "
                    "Possible causes: model API error, content filter, or <think>-stripping overreach."
                )
            if len(cleaned) < len(chunk) * 0.3:
                logger.warning(
                    f"[translate] Chunk {i}/{len(chunks)} output is <30% of input "
                    f"({len(cleaned)} vs {len(chunk)}) — possible content loss; "
                    "check model output truncation or _clean_output over-stripping."
                )
            translated_parts.append(cleaned)

        result = "\n\n".join(translated_parts)

        # Post-process: enforce legal glossary terms the LLM may have missed
        result = _enforce_glossary(result, target_language.value)

        ratio = len(result) / max(len(source_text), 1)
        logger.info(
            f"[translate] Complete: input={len(source_text)} chars → "
            f"output={len(result)} chars (ratio {ratio:.2f})"
        )
        # Hard bound on translation length ratio. Outside 0.4–3.0 is almost
        # certainly either content loss or runaway generation — fail loudly
        # so the lawyer doesn't silently receive a corrupted document.
        if ratio < 0.4 or ratio > 3.0:
            raise RuntimeError(
                f"Translation length ratio {ratio:.2f} is outside the sanity band "
                f"[0.4, 3.0] (input={len(source_text)} chars → output={len(result)} chars). "
                "Likely content loss or runaway generation. Retry or check model output."
            )

        return result
