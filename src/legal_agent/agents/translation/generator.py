"""Translation generator — LLM-based legal document translation with strict terminology."""

from __future__ import annotations

import asyncio
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
# Sarvam has a tighter effective context window; ~2 legal pages keeps input+output within budget.
_SARVAM_CHUNK_MAX_CHARS = 6000
# Chars of previous chunk's source text included as non-output context in each subsequent chunk.
_CONTEXT_TAIL_CHARS = 500

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

# ── Technical term preservation ───────────────────────────────────────────────
# Masked to __T0__ placeholders before Sarvam Translate sees the text, then
# restored verbatim after. Prevents transliteration of brand names, programming
# languages, and abbreviations. ALL_CAPS acronyms are caught by the regex pattern.

_PRESERVE_TERMS: list[str] = sorted([
    # Multi-word first (must match before single-word substrings)
    "Google ADK", "Play Store", "App Store",
    # Tech brand names / frameworks
    "SmartFoxServer", "WebSockets", "WebSocket",
    "PostgreSQL", "Elasticsearch", "LlamaIndex", "PromptQL",
    "MongoDB", "LeetCode", "LinkedIn", "OpenAI", "GitHub", "GitLab",
    "Snapser", "Knative", "Kafka", "Jenkins", "Docker", "Kubernetes",
    "Qdrant", "Neo4j", "Redis", "Unity", "Gemini", "Claude", "Mem0",
    "Python", "Java", "Kotlin", "Swift", "Android",
    "Firecrawl", "Camoufox", "ClinicalOps",
], key=len, reverse=True)

_TECH_TERM_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(t) for t in _PRESERVE_TERMS)
    + r"|C#"                                      # C# — # is not \w
    + r"|[A-Z][A-Z0-9]{1,}(?:[-/][A-Z0-9]+)*"   # ALL_CAPS: API, REST, CI/CD, LLM
    + r")(?!\w)",
    re.UNICODE,
)


def _mask_tech_terms(text: str) -> tuple[str, dict[str, str]]:
    """Replace tech/acronym tokens with {{n}} placeholders before Sarvam.

    Placeholders use curly braces + digits ONLY — no Latin letters that Sarvam
    could transliterate to Devanagari (e.g. T → ट). Returns (masked_text, restore_map).
    """
    restore: dict[str, str] = {}
    counter = 0

    def _replace(m: re.Match) -> str:
        nonlocal counter
        ph = "{{" + str(counter) + "}}"
        restore[ph] = m.group(0)
        counter += 1
        return ph

    return _TECH_TERM_RE.sub(_replace, text), restore


def _restore_tech_terms(text: str, restore: dict[str, str]) -> str:
    for ph, original in restore.items():
        text = text.replace(ph, original)
    return text


def _clean_md_artifacts(text: str) -> str:
    """Fix pymupdf4llm extraction artifacts before translation.

    pymupdf4llm can produce: **[word]** (hyperlink brackets inside bold),
    _◦_ (bullet chars wrapped as italic), and **word1** **word2** (consecutive
    bold spans per-word for the same phrase). These confuse Sarvam and produce
    fragmented or garbled output.
    """
    # [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # **[word]** → **word**
    text = re.sub(r"\*\*\[([^\]]+)\]\*\*", r"**\1**", text)
    # _◦_ / _•_ → ◦  (bullet/special chars should not be italic)
    text = re.sub(r"_([◦•·∙])_", r"\1", text)
    # **word1** **word2** → **word1 word2**  (merge consecutive bold spans)
    for _ in range(8):
        prev = text
        text = re.sub(r"\*\*([^*\n]+)\*\* \*\*([^*\n]+)\*\*", r"**\1 \2**", text)
        if text == prev:
            break
    return text

# ── Sarvam dedicated translate API ───────────────────────────────────────────

_SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
_SARVAM_TRANSLATE_MAX_CHARS = 1800  # under 2000-char limit of sarvam-translate:v1

_SARVAM_LANG_CODES: dict[str, str] = {
    "english": "en-IN", "hindi": "hi-IN", "bengali": "bn-IN", "telugu": "te-IN",
    "marathi": "mr-IN", "tamil": "ta-IN", "urdu": "ur-IN", "gujarati": "gu-IN",
    "kannada": "kn-IN", "malayalam": "ml-IN", "odia": "or-IN", "punjabi": "pa-IN",
    "assamese": "as-IN", "maithili": "mai-IN", "santali": "sat-IN", "kashmiri": "ks-IN",
    "nepali": "ne-IN", "sindhi": "sd-IN", "dogri": "doi-IN", "konkani": "kok-IN",
    "manipuri": "mni-IN", "bodo": "brx-IN", "sanskrit": "sa-IN",
}


def _md_to_tagged(text: str) -> str:
    """Replace **bold** → <b>bold</b>, *italic* → <i>italic</i>. Bold first to avoid partial match."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', text, flags=re.DOTALL)
    # Strip any ** or * that weren't part of a matched span. Unmatched markers pass
    # through Sarvam unchanged; mistune then treats the first one as an unclosed
    # bold/italic tag and renders everything after it in bold/italic.
    text = re.sub(r'\*{2,}', '', text)
    text = re.sub(r'(?<!\*)\*(?!\*)', '', text)
    return text


def _tagged_to_md(text: str) -> str:
    """Restore <b>...</b> → **...** and <i>...</i> → *...*."""
    text = re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<i>(.*?)</i>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
    return text


_SARVAM_DICT_WRAPPER_RE = re.compile(
    # Sarvam occasionally emits its JSON-schema reply object as a literal Python
    # dict, e.g.:
    #   {'description': '...', 'title': 'Female', 'type': 'string', 'content': '<translation>'}
    # We extract the actual translation from the 'content' key. The pattern
    # tolerates double or single quotes around the key, escaped quotes inside
    # the value, and an optional trailing brace.
    r"""\{\s*(?:['"]description['"]\s*:\s*['"][^'"]*['"]\s*,\s*)?"""
    r"""(?:['"]title['"]\s*:\s*['"][^'"]*['"]\s*,\s*)?"""
    r"""(?:['"]type['"]\s*:\s*['"][^'"]*['"]\s*,\s*)?"""
    r"""['"]content['"]\s*:\s*['"](?P<content>.*?)['"]\s*\}""",
    re.DOTALL,
)


def _unwrap_sarvam_dict_response(text: str) -> str:
    """If Sarvam returned its schema reply as a dict literal, pull out `content`."""
    if "'content':" not in text and '"content":' not in text:
        return text
    match = _SARVAM_DICT_WRAPPER_RE.search(text)
    if not match:
        return text
    extracted = match.group("content")
    # Replace the matched dict with the unescaped content value so any
    # surrounding context (rare) is preserved.
    unescaped = extracted.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")
    return text[: match.start()] + unescaped + text[match.end() :]


def _clean_sarvam_translate_output(text: str) -> str:
    """Clean wrapper artifacts Sarvam sometimes returns around plain text chunks."""
    text = _clean_output(text)
    text = text.replace("\x00", "")
    # Sarvam occasionally returns its full schema-style response as a Python dict
    # literal (`{'description': '...', 'content': '<translation>'}`). Extract the
    # `content` value so users don't see the metadata in the rendered PDF.
    text = _unwrap_sarvam_dict_response(text)
    # The Translate API examples are plain-text only, but it may still wrap output in
    # markdown fences when the input came from markdown/PDF extraction. Those fences
    # render as literal code blocks in the final PDF, so strip standalone fence lines.
    text = re.sub(r"(?m)^\s*```[\w-]*\s*$", "", text)
    return text.strip()


def _split_for_sarvam(text: str, max_chars: int = _SARVAM_TRANSLATE_MAX_CHARS) -> list[str]:
    """Split plain text at paragraph / word boundaries into chunks ≤ max_chars."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if len(para) > max_chars:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_len = 0
            chunks.extend(_split_long_plain_text(para, max_chars))
            continue
        if current and current_len + len(para) + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return [chunk for chunk in chunks if chunk.strip()] or ([text] if text.strip() else [])


def _split_long_plain_text(text: str, max_chars: int = _SARVAM_TRANSLATE_MAX_CHARS) -> list[str]:
    """Split one oversized paragraph without exceeding Sarvam's 2000-char hard limit."""
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        if len(word) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(word[i:i + max_chars] for i in range(0, len(word), max_chars))
            continue
        next_text = f"{current} {word}".strip()
        if len(next_text) > max_chars and current:
            chunks.append(current)
            current = word
        else:
            current = next_text
    if current:
        chunks.append(current)
    return chunks


async def _call_sarvam_translate(
    text: str,
    source_code: str,
    target_code: str,
    api_key: str,
    model: str = "sarvam-translate:v1",
) -> str:
    # Sarvam rejects empty input with a 400; skip rather than fail the whole job.
    if not text or not text.strip():
        return text
    import httpx

    body: dict = {
        "input": text,
        "source_language_code": source_code,
        "target_language_code": target_code,
        "model": model,
        "mode": "formal",
        "enable_preprocessing": True,
        "numerals_format": "international",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            _SARVAM_TRANSLATE_URL,
            headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
            json=body,
        )
        if not resp.is_success:
            logger.error(
                f"[sarvam-translate] {resp.status_code} (chars={len(text)}): "
                f"{resp.text[:500]} | input_preview={text[:200]!r}"
            )
            resp.raise_for_status()
        # Always run the dict-wrapper unwrap + null-char scrub here so every
        # caller (markdown pipeline AND the layout-PDF block translator) gets a
        # clean string. Markdown-fence stripping stays in the higher-level
        # `_clean_sarvam_translate_output` since it only matters for the
        # markdown render path.
        translated = resp.json()["translated_text"]
        return _unwrap_sarvam_dict_response(translated).replace("\x00", "")


async def _translate_with_sarvam_api(
    source_text: str,
    target_language: TranslationLanguage,
    source_language: TranslationLanguage | None,
    api_key: str,
) -> str:
    """Translate via Sarvam's dedicated translate API with markdown preservation.

    Converts **bold** and *italic* markers to HTML-tag placeholders before sending
    to the translate API (which is trained on web/HTML content and preserves these
    tags). Heading prefixes (##) and bullets (- ) are plain ASCII that pass through
    unchanged. Chunks are translated in parallel via asyncio.gather.
    """
    settings = get_settings()
    translate_model = settings.sarvam_translate_model

    target_code = _SARVAM_LANG_CODES.get(target_language.value, "hi-IN")
    # sarvam-translate:v1 does not support "auto"; default to en-IN for source
    source_code = _SARVAM_LANG_CODES.get(source_language.value, "en-IN") if source_language else "en-IN"

    # pymupdf4llm wraps some spans (table cells, special text) in [[...]].
    # Strip double-bracket notation — it has no markdown/render meaning.
    source_text = re.sub(r'\[\[', '', source_text)
    source_text = re.sub(r'\]\]', '', source_text)

    # Fix pymupdf4llm artifacts (fragmented bold, hyperlink brackets, _◦_).
    source_text = _clean_md_artifacts(source_text)

    # Formal mode translates everything — mask tech terms so brand names / acronyms
    # aren't transliterated. {{n}} placeholders survive Sarvam (digits + braces only).
    _restore: dict[str, str] = {}
    source_text, _restore = _mask_tech_terms(source_text)

    tagged = _md_to_tagged(source_text)
    chunks = _split_for_sarvam(tagged)
    logger.debug(
        f"[sarvam-translate] model={translate_model} "
        f"{len(chunks)} chunk(s) for {len(source_text)} chars"
    )

    translated_chunks: list[str] = await asyncio.gather(*[
        _call_sarvam_translate(chunk, source_code, target_code, api_key, translate_model)
        for chunk in chunks
    ])

    result = _clean_sarvam_translate_output(_tagged_to_md("\n\n".join(translated_chunks)))
    # Strip any <b>/<i> tags Sarvam didn't preserve symmetrically (unpaired leftovers).
    result = re.sub(r'</?[bi]>', '', result, flags=re.IGNORECASE)
    if _restore:
        result = _restore_tech_terms(result, _restore)
    return result


def _chunk_html_blocks(html: str, max_chars: int = 1500) -> list[str]:
    """Split PyMuPDF absolute-positioned HTML at </div> boundaries into chunks ≤ max_chars.

    PyMuPDF get_text("html") produces <div>/<span> layout — no <p> tags — so we
    split on </div> closings, which are the natural element boundaries. A hard guard
    then sub-splits any chunk still over Sarvam's 2000-char limit on whitespace.
    Empty / whitespace-only chunks are filtered out (Sarvam returns 400 on empty input).
    """
    blocks = re.split(r'(?<=</div>)', html)
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(current) + len(block) > max_chars and current:
            chunks.append(current)
            current = block
        else:
            current += block
    if current:
        chunks.append(current)

    # Hard guard: sub-split any chunk still over Sarvam's 2000-char limit on whitespace.
    safe: list[str] = []
    for chunk in chunks:
        if len(chunk) <= 1900:
            safe.append(chunk)
        else:
            words = chunk.split()
            part = ""
            for word in words:
                if len(part) + len(word) + 1 > 1500 and part:
                    safe.append(part)
                    part = word
                else:
                    part = (part + " " + word).strip()
            if part:
                safe.append(part)

    # Drop empty / whitespace-only chunks — Sarvam rejects them with a 400.
    safe = [c for c in safe if c.strip()]
    if not safe:
        return [html] if html.strip() else []
    return safe


async def _translate_page_html(
    page_html: str, source_code: str, target_code: str, api_key: str
) -> str:
    """Translate one page's HTML content, preserving layout structure."""
    # Extract the <body> content from PyMuPDF's full HTML document wrapper
    body_match = re.search(r'<body>(.*?)</body>', page_html, re.DOTALL | re.IGNORECASE)
    content = body_match.group(1).strip() if body_match else page_html

    # Strip [[...]] artifacts from pymupdf4llm
    content = re.sub(r'\[\[|\]\]', '', content)
    # Remove font-family from inline styles so the Indic @font-face declarations
    # injected by our CSS can take over. The original PDF fonts are Latin-only
    # (ArialMT, Helvetica-Bold, etc.) and cannot render Devanagari/Tamil/etc.
    content = re.sub(r'font-family:[^;]+;?\s*', '', content)

    chunks = _chunk_html_blocks(content)
    translated = await asyncio.gather(*[
        _call_sarvam_translate(chunk, source_code, target_code, api_key)
        for chunk in chunks
    ])
    return "".join(translated)


async def _translate_html_with_sarvam_api(
    page_htmls: list[str],
    target_language: TranslationLanguage,
    source_language: TranslationLanguage | None,
    api_key: str,
) -> str:
    """Translate PDF pages (as HTML) via Sarvam, preserving absolute-positioned layout."""
    target_code = _SARVAM_LANG_CODES.get(target_language.value, "hi-IN")
    source_code = _SARVAM_LANG_CODES.get(source_language.value, "en-IN") if source_language else "en-IN"
    logger.debug(f"[sarvam-html] translating {len(page_htmls)} page(s)")
    translated_pages = await asyncio.gather(*[
        _translate_page_html(page_html, source_code, target_code, api_key)
        for page_html in page_htmls
    ])
    return "\n".join(translated_pages)


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
    return model, get_settings().draft_llm_provider


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
- Time references: 10:30 AM, 14:00 hrs, 9:00 a.m., etc.
- Statute references on first occurrence: "Section 438 of CrPC, 1973 (धारा 438, दं.प्र.सं., 1973)"
- Email addresses: advocate@lawfirm.com, court@nic.in, etc.
- URLs and website addresses: https://districts.ecourts.gov.in, www.sci.gov.in, etc.
- Phone and fax numbers: +91-11-23456789, 011-23456789, Fax: 011-XXXXXXXX
- Personal names: parties, witnesses, advocates, judges — keep EXACTLY as written
- Company and firm names: M/s ABC Pvt. Ltd., XYZ Industries, LLP names, etc.
- Case numbers and filing numbers: W.P. (C) No. 1234/2024, Crl. A. No. 56/2023, Diary No. 45678/2024
- Document reference numbers: F.No. 12/34/2024-Judl., Office Order No., Circular No., etc.
- ID numbers: PAN, Aadhaar, CIN, GSTIN, passport number, driving licence number, etc.
- Bank details: account numbers, IFSC codes, MICR codes
- PIN codes and police station codes: 110001, PS: Connaught Place, P.S. Saket, etc.
- Annexure and exhibit labels: Annexure A, Exhibit P-1, Schedule I, Appendix II, etc.
- Signature markers: Sd/-, (Seal), (Thumb Impression), (L.T.I.), etc.
- Full postal addresses — preserve exactly including street names, landmarks, district, state

═══ FORMATTING ═══

- Preserve all markdown headings (#, ##, ###) — if source has a heading, translation MUST have the same heading at the same level
- **Bold text** → translate content inside, keep ** markers EXACTLY — do NOT remove or flatten bold
- *Italic text* → translate content inside, keep * markers EXACTLY
- Indented sub-bullets (  - ) → preserve exact indentation level
- Numbering, bullet points, paragraph breaks → preserve exactly
- Do NOT merge paragraphs or flatten structure — every paragraph break in the source becomes a paragraph break in the output
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


def _group_pages(page_texts: list[str], max_chars: int) -> list[str]:
    """Group pages into translation chunks that fit within max_chars.
    A single page that exceeds max_chars becomes its own chunk."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for page in page_texts:
        page_len = len(page)
        if current_len + page_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = [page]
            current_len = page_len
        else:
            current.append(page)
            current_len += page_len + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks or ["\n\n".join(page_texts)]


def _build_user_message(source_text: str, part_info: str = "", prev_source_tail: str = "") -> str:
    lines = [
        "Translate the following legal document accurately. "
        "Preserve all formatting, structure, and citations exactly as specified in your instructions."
    ]
    if part_info:
        lines.append(part_info)
    if prev_source_tail:
        lines.append(
            "\n[Previous section — already translated. DO NOT include in your output. "
            "Use only to continue mid-sentence fragments and maintain terminology consistency:]\n"
            f"---\n{prev_source_tail}\n---"
        )
    lines.append(f"\n{source_text}")
    return "\n".join(lines)


class TranslationGenerator:
    """Generates legal document translations via LLM calls.

    Long documents are split into page-grouped chunks and translated in parallel.
    Each chunk (after the first) receives the tail of the previous chunk's source
    text as non-output context so the model can continue mid-sentence fragments
    and maintain terminology consistency across chunk boundaries.
    """

    def __init__(self) -> None:
        pass

    async def _translate_chunk(
        self,
        llm,
        system_prompt: str,
        chunk: str,
        i: int,
        total: int,
        prev_source_tail: str = "",
    ) -> str:
        part_info = f"This is part {i} of {total}. Translate this part completely." if total > 1 else ""
        user_message = _build_user_message(chunk, part_info=part_info, prev_source_tail=prev_source_tail)

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
            f"[translate] Chunk {i}/{total}: {len(chunk)} → {len(cleaned)} chars "
            f"(ratio {len(cleaned) / max(len(chunk), 1):.2f})"
        )
        if not cleaned.strip():
            raise RuntimeError(
                f"Translation chunk {i}/{total} returned empty output "
                f"(input was {len(chunk)} chars). Aborting to avoid partial translation. "
                "Possible causes: model API error, content filter, or <think>-stripping overreach."
            )
        if len(cleaned) < len(chunk) * 0.3:
            logger.warning(
                f"[translate] Chunk {i}/{total} output is <30% of input "
                f"({len(cleaned)} vs {len(chunk)}) — possible content loss."
            )
        return cleaned

    async def generate(
        self,
        source_text: str,
        target_language: TranslationLanguage,
        source_language: TranslationLanguage | None = None,
        model: str | None = None,
        profile: "DocProfile | None" = None,
        page_texts: list[str] | None = None,
        page_htmls: list[str] | None = None,
    ) -> str:
        """Translate a legal document. Returns translated markdown or HTML text.

        Sarvam Translate is a plain-text API (per official examples), so the
        Sarvam path translates markdown/plain text and lets our renderer produce
        a clean PDF. Sending PyMuPDF absolute-positioned HTML through Translate
        corrupts attributes/tags and produces broken PDFs.

        `page_texts` is the per-page markdown extraction from PyMuPDF. Used for
        parallel chunking in the LLM path. Each chunk after the first receives
        the last _CONTEXT_TAIL_CHARS of the previous chunk's source text as
        non-output context to handle mid-sentence page breaks.

        `profile` carries the doc-type system-prompt extension and glossary
        overlay (consumed in `_build_system_prompt`). None → default behaviour.
        """
        model = model or get_settings().translation_llm_model
        model, provider = _resolve_model(model)

        if provider == "sarvam":
            settings = get_settings()
            if not settings.sarvam_api_key:
                raise RuntimeError("SARVAM_API_KEY is not configured")
            result = await _translate_with_sarvam_api(
                source_text, target_language, source_language, settings.sarvam_api_key
            )
            result = _enforce_glossary(result, target_language.value)
            ratio = len(result) / max(len(source_text), 1)
            logger.info(
                f"[translate] Complete (sarvam-api): input={len(source_text)} chars → "
                f"output={len(result)} chars (ratio {ratio:.2f})"
            )
            if ratio < 0.25 or ratio > 3.0:
                raise RuntimeError(
                    f"Translation length ratio {ratio:.2f} outside sanity band [0.25, 3.0] "
                    f"(input={len(source_text)} chars → output={len(result)} chars)."
                )
            return result

        max_tokens = _PROVIDER_MAX_TOKENS.get(provider, 16384)

        llm = _init_llm(model, provider, max_tokens)
        system_prompt = _build_system_prompt(target_language, source_language, profile)

        # Clean pymupdf4llm artifacts before the LLM sees fragmented bold/brackets.
        # (The system prompt already instructs the LLM to preserve tech terms, so
        # no placeholder masking needed here.)
        source_text = _clean_md_artifacts(source_text)
        if page_texts:
            page_texts = [_clean_md_artifacts(p) for p in page_texts]

        chunk_max = _CHUNK_MAX_CHARS
        if page_texts:
            chunks = _group_pages(page_texts, chunk_max)
        else:
            chunks = _split_into_chunks(source_text, chunk_max)

        logger.info(
            f"[translate] {source_language or 'auto'} → {target_language.value} "
            f"| model={model} | input_chars={len(source_text)} | chunks={len(chunks)}"
        )

        # Build context tails from source text so all tasks can start in parallel.
        tasks = [
            self._translate_chunk(
                llm,
                system_prompt,
                chunk,
                i + 1,
                len(chunks),
                prev_source_tail=chunks[i - 1][-_CONTEXT_TAIL_CHARS:] if i > 0 else "",
            )
            for i, chunk in enumerate(chunks)
        ]
        translated_parts = list(await asyncio.gather(*tasks))

        result = "\n\n".join(translated_parts)

        # Post-process: enforce legal glossary terms the LLM may have missed
        result = _enforce_glossary(result, target_language.value)

        ratio = len(result) / max(len(source_text), 1)
        logger.info(
            f"[translate] Complete: input={len(source_text)} chars → "
            f"output={len(result)} chars (ratio {ratio:.2f})"
        )
        # Hard bound on translation length ratio. Outside 0.25–3.0 is almost
        # certainly either content loss or runaway generation — fail loudly
        # so the lawyer doesn't silently receive a corrupted document.
        # Lower bound is 0.25: Indic scripts (Hindi, Tamil, etc.) are more compact
        # than English — Devanagari packs more phonetic content per character, so
        # English→Hindi ratios of 0.35–0.45 are normal and expected.
        if ratio < 0.25 or ratio > 3.0:
            raise RuntimeError(
                f"Translation length ratio {ratio:.2f} is outside the sanity band "
                f"[0.25, 3.0] (input={len(source_text)} chars → output={len(result)} chars). "
                "Likely content loss or runaway generation. Retry or check model output."
            )

        return result
