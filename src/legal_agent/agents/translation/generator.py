"""Translation generator — LLM-based legal document translation with strict terminology."""

import logging
import re

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from legal_agent.config import get_settings
from legal_agent.models.documents import LANGUAGE_NATIVE_NAMES, TranslationLanguage

logger = logging.getLogger(__name__)

_PROVIDER_MAX_TOKENS: dict[str, int] = {
    "openai": 16384,
    "anthropic": 16384,
    "google-genai": 16384,
}

# Approximate chars per chunk for splitting long documents.
_CHUNK_MAX_CHARS = 12000

# Simple aliases the frontend can send instead of full model names.
_MODEL_ALIASES: dict[str, str] = {
    "gemini": "gemini-3.1-flash-lite-preview",
    "claude": "claude-haiku-4-5-20251001",
    "openai": "gpt-4.1-mini",
}

# ── Mandatory legal terminology per language ──────────────────────────────────
# When a term appears in the source, the model MUST use the mapped translation.
# Only the target language's terms are injected into the prompt.

_LEGAL_TERMS: dict[str, dict[str, str]] = {
    "hindi": {
        "Petition": "याचिका",
        "Application": "आवेदन",
        "Affidavit": "शपथ पत्र",
        "Applicant": "आवेदक",
        "Respondent": "प्रत्यर्थी",
        "Appellant": "अपीलार्थी",
        "Plaintiff": "वादी",
        "Defendant": "प्रतिवादी",
        "Accused": "अभियुक्त",
        "Bail": "जमानत",
        "Anticipatory Bail": "अग्रिम जमानत",
        "FIR": "प्रथम सूचना रिपोर्ट",
        "Charge Sheet": "आरोप पत्र",
        "Section": "धारा",
        "Act": "अधिनियम",
        "Code": "संहिता",
        "Rule": "नियम",
        "Order": "आदेश",
        "Judgment": "निर्णय",
        "Decree": "डिक्री",
        "Prayer": "प्रार्थना",
        "Grounds": "आधार",
        "Facts": "तथ्य",
        "Witness": "साक्षी",
        "Evidence": "साक्ष्य",
        "Testimony": "गवाही",
        "Hon'ble Court": "माननीय न्यायालय",
        "Advocate": "अधिवक्ता",
        "Judge": "न्यायाधीश",
        "Versus": "विरुद्ध",
        "Jurisdiction": "अधिकार क्षेत्र",
        "Injunction": "व्यादेश",
        "Stay": "स्थगन",
        "Contempt": "अवमानना",
        "Compromise": "समझौता",
        "Settlement": "निपटान",
        "Arbitration": "मध्यस्थता",
        "Power of Attorney": "मुख्तारनामा",
        "Notarized": "नोटरीकृत",
        "Criminal": "आपराधिक",
        "Civil": "दीवानी",
        "Non-compete": "प्रतिस्पर्धा-निषेध",
        "Non-solicitation": "ग्राहक/कर्मचारी अनाकर्षण",
        "Termination": "समाप्ति",
        "Indemnity": "क्षतिपूर्ति",
        "Confidentiality": "गोपनीयता",
        "Force Majeure": "अप्रत्याशित घटना",
        "Liability": "दायित्व",
        "Negligence": "लापरवाही",
        "Probation": "परीक्षाकाल",
        "CrPC": "दण्ड प्रक्रिया संहिता",
        "BNSS": "भारतीय नागरिक सुरक्षा संहिता",
        "BNS": "भारतीय न्याय संहिता",
        "IPC": "भारतीय दण्ड संहिता",
    },
    "bengali": {
        "Petition": "আবেদনপত্র",
        "Application": "দরখাস্ত",
        "Affidavit": "হলফনামা",
        "Applicant": "আবেদনকারী",
        "Respondent": "প্রতিবাদী",
        "Plaintiff": "বাদী",
        "Defendant": "বিবাদী",
        "Accused": "অভিযুক্ত",
        "Bail": "জামিন",
        "Section": "ধারা",
        "Act": "আইন",
        "Order": "আদেশ",
        "Judgment": "রায়",
        "Witness": "সাক্ষী",
        "Hon'ble Court": "মাননীয় আদালত",
        "Advocate": "আইনজীবী",
        "Prayer": "প্রার্থনা",
        "Grounds": "ভিত্তি",
        "Facts": "তথ্য",
    },
    "tamil": {
        "Petition": "மனு",
        "Application": "விண்ணப்பம்",
        "Affidavit": "சத்தியப்பிரமாணம்",
        "Applicant": "மனுதாரர்",
        "Respondent": "எதிர்மனுதாரர்",
        "Plaintiff": "வாதி",
        "Defendant": "பிரதிவாதி",
        "Accused": "குற்றவாளி",
        "Bail": "ஜாமீன்",
        "Section": "பிரிவு",
        "Act": "சட்டம்",
        "Order": "ஆணை",
        "Judgment": "தீர்ப்பு",
        "Witness": "சாட்சி",
        "Hon'ble Court": "மாண்புமிகு நீதிமன்றம்",
        "Advocate": "வழக்கறிஞர்",
        "Prayer": "வேண்டுகோள்",
        "Grounds": "அடிப்படை",
        "Facts": "உண்மைகள்",
    },
    "marathi": {
        "Petition": "याचिका",
        "Application": "अर्ज",
        "Affidavit": "प्रतिज्ञापत्र",
        "Applicant": "अर्जदार",
        "Respondent": "सामनेवाला",
        "Plaintiff": "वादी",
        "Defendant": "प्रतिवादी",
        "Accused": "आरोपी",
        "Bail": "जामीन",
        "Section": "कलम",
        "Act": "अधिनियम",
        "Order": "आदेश",
        "Judgment": "निकालपत्र",
        "Witness": "साक्षीदार",
        "Hon'ble Court": "मा. न्यायालय",
        "Advocate": "अधिवक्ता",
        "Prayer": "प्रार्थना",
        "Grounds": "कारणे",
        "Facts": "वस्तुस्थिती",
    },
    "telugu": {
        "Petition": "పిటిషన్",
        "Application": "దరఖాస్తు",
        "Affidavit": "అఫిడవిట్",
        "Applicant": "దరఖాస్తుదారు",
        "Respondent": "ప్రతివాది",
        "Plaintiff": "వాది",
        "Defendant": "ప్రతివాది",
        "Accused": "నిందితుడు",
        "Bail": "బెయిల్",
        "Section": "సెక్షన్",
        "Act": "చట్టం",
        "Order": "ఉత్తర్వు",
        "Judgment": "తీర్పు",
        "Witness": "సాక్షి",
        "Hon'ble Court": "గౌరవనీయ న్యాయస్థానం",
        "Advocate": "న్యాయవాది",
    },
    "gujarati": {
        "Petition": "અરજી",
        "Application": "અરજી",
        "Affidavit": "સોગંદનામું",
        "Applicant": "અરજદાર",
        "Respondent": "સામાવાળા",
        "Plaintiff": "વાદી",
        "Defendant": "પ્રતિવાદી",
        "Accused": "આરોપી",
        "Bail": "જામીન",
        "Section": "કલમ",
        "Act": "અધિનિયમ",
        "Order": "હુકમ",
        "Judgment": "ચુકાદો",
        "Witness": "સાક્ષી",
        "Hon'ble Court": "માનનીય અદાલત",
        "Advocate": "વકીલ",
    },
    "kannada": {
        "Petition": "ಅರ್ಜಿ",
        "Application": "ಅರ್ಜಿ",
        "Affidavit": "ಪ್ರಮಾಣ ಪತ್ರ",
        "Applicant": "ಅರ್ಜಿದಾರ",
        "Respondent": "ಪ್ರತಿವಾದಿ",
        "Plaintiff": "ವಾದಿ",
        "Defendant": "ಪ್ರತಿವಾದಿ",
        "Accused": "ಆರೋಪಿ",
        "Bail": "ಜಾಮೀನು",
        "Section": "ಕಲಂ",
        "Act": "ಅಧಿನಿಯಮ",
        "Order": "ಆದೇಶ",
        "Judgment": "ತೀರ್ಪು",
        "Witness": "ಸಾಕ್ಷಿ",
        "Hon'ble Court": "ಮಾನ್ಯ ನ್ಯಾಯಾಲಯ",
        "Advocate": "ನ್ಯಾಯವಾದಿ",
    },
    "malayalam": {
        "Petition": "ഹർജി",
        "Application": "അപേക്ഷ",
        "Affidavit": "സത്യവാങ്മൂലം",
        "Applicant": "അപേക്ഷകൻ",
        "Respondent": "എതിർകക്ഷി",
        "Plaintiff": "വാദി",
        "Defendant": "പ്രതിവാദി",
        "Accused": "പ്രതി",
        "Bail": "ജാമ്യം",
        "Section": "വകുപ്പ്",
        "Act": "നിയമം",
        "Order": "ഉത്തരവ്",
        "Judgment": "വിധി",
        "Witness": "സാക്ഷി",
        "Hon'ble Court": "ബഹുമാനപ്പെട്ട കോടതി",
        "Advocate": "അഭിഭാഷകൻ",
    },
    "punjabi": {
        "Petition": "ਅਰਜ਼ੀ",
        "Applicant": "ਅਰਜ਼ੀਕਰਤਾ",
        "Bail": "ਜ਼ਮਾਨਤ",
        "Section": "ਧਾਰਾ",
        "Act": "ਕਾਨੂੰਨ",
        "Order": "ਹੁਕਮ",
        "Judgment": "ਫੈਸਲਾ",
        "Witness": "ਗਵਾਹ",
        "Hon'ble Court": "ਮਾਨਯੋਗ ਅਦਾਲਤ",
        "Advocate": "ਵਕੀਲ",
    },
}

_LATIN_MAXIMS = (
    "habeas corpus, certiorari, mandamus, quo warranto, inter alia, "
    "prima facie, suo motu, ab initio, res judicata, obiter dictum, "
    "mutatis mutandis, ultra vires, locus standi, amicus curiae, "
    "ratio decidendi, stare decisis, de novo, ex parte, ad interim, "
    "mens rea, actus reus, bona fide, mala fide, sub judice, in limine"
)


def _resolve_model(model: str) -> tuple[str, str]:
    """Resolve a model alias or full name to (model_name, provider)."""
    if model in _MODEL_ALIASES:
        model = _MODEL_ALIASES[model]

    if model.startswith("gemini"):
        return model, "google-genai"
    if model.startswith("claude"):
        return model, "anthropic"
    if model.startswith("gpt") or model.startswith("o"):
        return model, "openai"
    return model, get_settings().llm_provider


def _format_term_table(terms: dict[str, str]) -> str:
    """Format terminology dict as a strict mapping block for the prompt."""
    lines = [f"  {eng} → {translated}" for eng, translated in terms.items()]
    return "\n".join(lines)


def _build_system_prompt(
    target_language: TranslationLanguage,
    source_language: TranslationLanguage | None,
) -> str:
    target_name = LANGUAGE_NATIVE_NAMES[target_language.value]
    source_desc = (
        f"Source language: {LANGUAGE_NATIVE_NAMES[source_language.value]} ({source_language.value})."
        if source_language
        else "Auto-detect the source language."
    )

    # Get terminology for target language, or empty dict for unsupported ones
    terms = _LEGAL_TERMS.get(target_language.value, {})
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

    return f"""You are a legal translator registered with an Indian High Court, specializing in {target_language.value} ({target_name}).
TASK: Translate the provided legal document into {target_language.value} ({target_name}).
{source_desc}

{term_section}

═══ STRICTLY FORBIDDEN ═══

- Do NOT transliterate English words into {target_name} script (e.g., "ऑब्जर्वेशन", "टर्मिनेशन", "नॉन-कम्पीट", "कॉन्ट्रैक्ट")
- Do NOT invent new legal terms — if unsure, keep the English term with parenthetical explanation
- Do NOT produce informal or conversational language — maintain formal legal register throughout
- Do NOT mix English and {target_language.value} randomly within sentences
- Do NOT translate word-by-word — use standard legal phrasing as it appears in actual court filings

═══ PRESERVE AS-IS (do NOT translate) ═══

- Latin legal maxims: {_LATIN_MAXIMS}
- Court names: "Supreme Court of India", "High Court of Delhi", etc.
- Statute titles: "Indian Penal Code", "Bharatiya Nyaya Sanhita", etc.
- Case citations: "State of Punjab v. Ram Singh" — (2020) 5 SCC 1 — keep EXACTLY as-is
- Official designations: "Additional Sessions Judge", "District Magistrate", etc.
- Numbers, dates (DD/MM/YYYY), monetary amounts (Rs. X,XX,XXX/-)
- Statute references on first occurrence use dual format: "Section 438 of CrPC, 1973 (धारा 438, दं.प्र.सं., 1973)"

═══ FORMATTING ═══

- Preserve all markdown headings (##, ###), numbering, bullet points, and paragraph breaks exactly
- **Bold text** → translate content inside, keep ** markers
- Tables → translate cell content only, keep structure
- Translate amount words: Rs. 4,25,000/- (Rupees Four Lakh Twenty Five Thousand Only) → Rs. 4,25,000/- + amount in {target_language.value}

═══ EXAMPLES ═══

English → {target_language.value}:

"The petitioner most humbly submits before this Hon'ble Court that the impugned order dated 15/03/2025 is liable to be set aside."
→ Use formal court language with proper honorifics as used in {target_language.value} court filings. NOT a word-for-word mapping.

"The Employee shall be on probation for a period of three (3) months from the date of joining."
→ Use standard contract language in {target_language.value}. The term "probation" must use the mapped legal term, NOT a transliteration.

═══ OCR CLEANUP ═══

The source may come from OCR/PDF extraction. Remove garbage strings, repeated headers/footers, and page markers. Fix obvious OCR errors. Keep all substantive legal content.

═══ SELF-CHECK (do this before finalizing) ═══

1. Verify every legal term uses the MANDATORY mapping above — not a guess or transliteration
2. Verify no English words were transliterated into {target_name} script
3. Verify the document reads as if originally drafted by an Indian lawyer in {target_language.value}

═══ OUTPUT ═══

Output ONLY the translated document in clean markdown. No preamble, notes, code fences, metadata, or wrapper markers."""


def _clean_output(text: str) -> str:
    """Strip wrapper markers the LLM may echo back."""
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
    ) -> str:
        """Translate a legal document. Returns translated markdown text."""
        model = model or "gemini"
        model, provider = _resolve_model(model)
        max_tokens = _PROVIDER_MAX_TOKENS.get(provider, 16384)

        llm = init_chat_model(model, model_provider=provider, max_tokens=max_tokens)
        system_prompt = _build_system_prompt(target_language, source_language)

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
            translated_parts.append(_clean_output(content))

        return "\n\n".join(translated_parts)
