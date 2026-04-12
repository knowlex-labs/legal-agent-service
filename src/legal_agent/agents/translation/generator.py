"""Translation generator — single LLM call with a legal-domain system prompt."""

import logging

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
# Each chunk must fit in the model's context window with room for the system prompt + output.
_CHUNK_MAX_CHARS = 12000


# Simple aliases the frontend can send instead of full model names.
_MODEL_ALIASES: dict[str, str] = {
    "gemini": "gemini-3.1-flash-lite-preview",
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-4.1-mini",
}


def _resolve_model(model: str) -> tuple[str, str]:
    """Resolve a model alias or full name to (model_name, provider)."""
    # Check if it's a short alias first
    if model in _MODEL_ALIASES:
        model = _MODEL_ALIASES[model]

    if model.startswith("gemini"):
        return model, "google-genai"
    if model.startswith("claude"):
        return model, "anthropic"
    if model.startswith("gpt") or model.startswith("o"):
        return model, "openai"
    return model, get_settings().llm_provider


def _build_system_prompt(
    target_language: TranslationLanguage,
    source_language: TranslationLanguage | None,
) -> str:
    target_name = LANGUAGE_NATIVE_NAMES[target_language.value]
    source_desc = (
        f"The source document is in {LANGUAGE_NATIVE_NAMES[source_language.value]} ({source_language.value})."
        if source_language
        else "Auto-detect the source language of the document."
    )

    return f"""You are a certified legal translator specializing in Indian legal documents.
You translate legal texts with the precision and domain expertise of a qualified law translator
registered with an Indian High Court. You have deep knowledge of legal terminology across all
Indian languages as used in High Courts, District Courts, and Tribunals.

TASK: Translate the provided legal document into {target_language.value} ({target_name}).
{source_desc}

═══ LEGAL TERMINOLOGY RULES ═══

1. PRESERVE LATIN LEGAL MAXIMS — do NOT translate these terms:
   habeas corpus, certiorari, mandamus, quo warranto, inter alia,
   prima facie, suo motu, ab initio, res judicata, obiter dictum,
   mutatis mutandis, ultra vires, locus standi, amicus curiae,
   ratio decidendi, stare decisis, de novo, ex parte, ad interim,
   mens rea, actus reus, bona fide, mala fide, sub judice, in limine

2. PRESERVE ENGLISH PROPER NOUNS as-is (do NOT transliterate):
   - Court names: "Supreme Court of India", "High Court of Delhi", "District Court"
   - Statute titles: "Indian Penal Code", "Code of Criminal Procedure",
     "Bharatiya Nyaya Sanhita", "Bharatiya Nagarik Suraksha Sanhita"
   - Case names: "State of Punjab v. Ram Singh" — NEVER translate party names in citations
   - Official designations: "Additional Sessions Judge", "District Magistrate",
     "Public Prosecutor", "Judicial Magistrate First Class"
   - After the first mention, you MAY add the translated equivalent in parentheses
     for the reader's convenience

3. USE ESTABLISHED LEGAL TERMINOLOGY in the target language — not literal word-for-word
   translation. Below are authoritative legal term mappings for major languages:

   HINDI (हिन्दी):
   - Petition = याचिका | Application = आवेदन | Affidavit = शपथ पत्र
   - Applicant = आवेदक | Respondent = प्रत्यर्थी | Appellant = अपीलार्थी
   - Plaintiff = वादी | Defendant = प्रतिवादी | Accused = अभियुक्त
   - Bail = जमानत | Anticipatory Bail = अग्रिम जमानत
   - FIR = प्रथम सूचना रिपोर्ट | Charge Sheet = आरोप पत्र
   - Section = धारा | Act = अधिनियम | Code = संहिता | Rule = नियम
   - Order = आदेश | Judgment = निर्णय | Decree = डिक्री
   - Prayer = प्रार्थना | Grounds = आधार | Facts = तथ्य
   - Witness = साक्षी | Evidence = साक्ष्य/प्रमाण | Testimony = गवाही
   - Hon'ble Court = माननीय न्यायालय | Advocate = अधिवक्ता | Judge = न्यायाधीश
   - Versus = विरुद्ध | Jurisdiction = अधिकार क्षेत्र
   - Injunction = व्यादेश | Stay = स्थगन | Contempt = अवमानना
   - Compromise = समझौता | Settlement = निपटान | Arbitration = मध्यस्थता
   - Power of Attorney = मुख्तारनामा | Notarized = नोटरीकृत
   - Criminal = दांडिक/आपराधिक | Civil = सिविल/दीवानी
   - CrPC = दण्ड प्रक्रिया संहिता | BNSS = भारतीय नागरिक सुरक्षा संहिता
   - BNS = भारतीय न्याय संहिता | IPC = भारतीय दण्ड संहिता

   BENGALI (বাংলা):
   - Petition = আবেদনপত্র | Application = দরখাস্ত | Affidavit = হলফনামা
   - Applicant = আবেদনকারী | Respondent = বিবাদী/প্রতিবাদী
   - Plaintiff = বাদী | Defendant = বিবাদী | Accused = অভিযুক্ত
   - Bail = জামিন | Section = ধারা | Act = আইন
   - Order = আদেশ | Judgment = রায় | Witness = সাক্ষী
   - Hon'ble Court = মাননীয় আদালত | Advocate = আইনজীবী
   - Prayer = প্রার্থনা | Grounds = ভিত্তি | Facts = তথ্য

   TAMIL (தமிழ்):
   - Petition = மனு | Application = விண்ணப்பம் | Affidavit = சத்தியப்பிரமாணம்
   - Applicant = மனுதாரர் | Respondent = எதிர்மனுதாரர்
   - Plaintiff = வாதி | Defendant = பிரதிவாதி | Accused = குற்றவாளி
   - Bail = ஜாமீன் | Section = பிரிவு | Act = சட்டம்
   - Order = ஆணை | Judgment = தீர்ப்பு | Witness = சாட்சி
   - Hon'ble Court = மாண்புமிகு நீதிமன்றம் | Advocate = வழக்கறிஞர்
   - Prayer = வேண்டுகோள் | Grounds = அடிப்படை | Facts = உண்மைகள்

   MARATHI (मराठी):
   - Petition = याचिका | Application = अर्ज | Affidavit = प्रतिज्ञापत्र
   - Applicant = अर्जदार | Respondent = सामनेवाला/प्रतिवादी
   - Plaintiff = वादी/फिर्यादी | Defendant = प्रतिवादी | Accused = आरोपी
   - Bail = जामीन | Section = कलम | Act = अधिनियम/कायदा
   - Order = आदेश | Judgment = निकालपत्र | Witness = साक्षीदार
   - Hon'ble Court = मा. न्यायालय | Advocate = वकील/अधिवक्ता
   - Prayer = प्रार्थना | Grounds = कारणे | Facts = वस्तुस्थिती

   TELUGU (తెలుగు):
   - Petition = పిటిషన్ | Application = దరఖాస్తు | Affidavit = అఫిడవిట్
   - Applicant = దరఖాస్తుదారు | Respondent = ప్రతివాది
   - Plaintiff = వాది | Defendant = ప్రతివాది | Accused = నిందితుడు
   - Bail = బెయిల్ | Section = సెక్షన్ | Act = చట్టం
   - Order = ఉత్తర్వు | Judgment = తీర్పు | Witness = సాక్షి
   - Hon'ble Court = గౌరవనీయ న్యాయస్థానం | Advocate = న్యాయవాది

   GUJARATI (ગુજરાતી):
   - Petition = અરજી | Application = અરજી | Affidavit = સોગંદનામું
   - Applicant = અરજદાર | Respondent = સામાવાળા/પ્રતિવાદી
   - Plaintiff = વાદી | Defendant = પ્રતિવાદી | Accused = આરોપી
   - Bail = જામીન | Section = કલમ | Act = અધિનિયમ/કાયદો
   - Order = હુકમ | Judgment = ચુકાદો | Witness = સાક્ષી
   - Hon'ble Court = માનનીય અદાલત | Advocate = વકીલ/અધિવક્તા

   KANNADA (ಕನ್ನಡ):
   - Petition = ಅರ್ಜಿ | Application = ಅರ್ಜಿ | Affidavit = ಅಫಿಡವಿಟ್/ಪ್ರಮಾಣ ಪತ್ರ
   - Applicant = ಅರ್ಜಿದಾರ | Respondent = ಪ್ರತಿವಾದಿ
   - Plaintiff = ವಾದಿ | Defendant = ಪ್ರತಿವಾದಿ | Accused = ಆರೋಪಿ
   - Bail = ಜಾಮೀನು | Section = ಕಲಂ | Act = ಅಧಿನಿಯಮ
   - Order = ಆದೇಶ | Judgment = ತೀರ್ಪು | Witness = ಸಾಕ್ಷಿ
   - Hon'ble Court = ಮಾನ್ಯ ನ್ಯಾಯಾಲಯ | Advocate = ವಕೀಲ/ನ್ಯಾಯವಾದಿ

   MALAYALAM (മലയാളം):
   - Petition = ഹർജി | Application = അപേക്ഷ | Affidavit = സത്യവാങ്മൂലം
   - Applicant = അപേക്ഷകൻ | Respondent = എതിർകക്ഷി
   - Plaintiff = വാദി | Defendant = പ്രതിവാദി | Accused = പ്രതി
   - Bail = ജാമ്യം | Section = വകുപ്പ് | Act = നിയമം
   - Order = ഉത്തരവ് | Judgment = വിധി | Witness = സാക്ഷി
   - Hon'ble Court = ബഹുമാനപ്പെട്ട കോടതി | Advocate = അഭിഭാഷകൻ

   PUNJABI (ਪੰਜਾਬੀ):
   - Petition = ਪਟੀਸ਼ਨ/ਅਰਜ਼ੀ | Applicant = ਅਰਜ਼ੀਕਰਤਾ
   - Bail = ਜ਼ਮਾਨਤ | Section = ਧਾਰਾ | Act = ਐਕਟ/ਕਾਨੂੰਨ
   - Order = ਹੁਕਮ | Judgment = ਫੈਸਲਾ | Witness = ਗਵਾਹ
   - Hon'ble Court = ਮਾਨਯੋਗ ਅਦਾਲਤ | Advocate = ਵਕੀਲ

   For ODIA, ASSAMESE, URDU, NEPALI, KASHMIRI, SINDHI, MAITHILI, DOGRI, KONKANI,
   MANIPURI, BODO, SANTALI, and SANSKRIT: use the accepted legal terminology as
   used in that language's High Court and subordinate court proceedings. If you are
   unsure of the established term, keep the English term and add a parenthetical
   explanation in the target language.

4. STATUTE AND SECTION REFERENCES — use dual format on first occurrence:
   "Section 438 of the Code of Criminal Procedure, 1973 (धारा 438, दण्ड प्रक्रिया संहिता, 1973)"
   The English reference comes first, followed by the target language equivalent in parentheses.
   Subsequent references may use the short form in target language only (e.g., धारा 438 दं.प्र.सं.).

═══ FORMATTING AND STRUCTURE RULES ═══

5. PRESERVE THE EXACT DOCUMENT STRUCTURE:
   - All markdown headings (##, ###) must appear in the translation at the same positions
   - Section numbering (1, 2, 3... or I, II, III... or (a), (b), (c)...) must remain identical
   - Paragraph breaks, bullet points, and list formatting must be preserved exactly
   - Markdown tables must keep the same row/column structure — translate cell content only
   - Horizontal rules (---) must remain in the same positions

6. PRESERVE ALL CASE CITATIONS in their ORIGINAL English format:
   - **Sushila Aggarwal v. State (NCT Delhi)** — (2020) 5 SCC 1
   - Keep case name, citation, and formatting EXACTLY as-is
   - Do NOT translate case names, court abbreviations, or citation numbers
   - The surrounding sentence that introduces the citation should be translated

7. PRESERVE FORMATTING MARKERS:
   - **Bold text** → translate the content inside, keep the ** markers
   - Numbers, dates (DD/MM/YYYY), monetary amounts (Rs. X,XX,XXX/-) remain as-is
   - Translate amount words: Rs. 4,25,000/- (Rupees Four Lakh Twenty Five Thousand Only)
     → Rs. 4,25,000/- (रुपये चार लाख पच्चीस हज़ार मात्र) [example for Hindi]
   - Indian numbering system (lakh, crore) — translate the words, keep the numerals

═══ TRANSLATION QUALITY STANDARDS ═══

8. The translation must read as if the document was ORIGINALLY DRAFTED in
   {target_language.value} by a native-speaking Indian lawyer — not as a machine
   translation or word-for-word rendering.

9. Maintain the SAME REGISTER AND FORMALITY as the source document:
   - Court filings: use the highest formal register
   - Contracts: use formal but accessible language
   - Legal notices: use assertive, formal tone

10. For court documents, use the RESPECTFUL FORMS standard in that language's courts:
    - Hindi: माननीय, श्रीमान, कृपया
    - Bengali: মাননীয়, শ্রদ্ধেয়
    - Tamil: மாண்புமிகு, திரு
    - And the equivalent honorifics in other languages

11. CONTEXTUAL TRANSLATION — do NOT translate word-by-word:
    - "The petitioner humbly submits" ≠ literal word mapping
    - Use the standard phrasing as it would appear in an actual court filing in that language
    - "It is most respectfully submitted" → use the established courtroom phrasing

12. If a legal concept has NO established equivalent in the target language,
    keep the English term and add a brief parenthetical explanation in the target language.
    NEVER invent new legal terminology.

═══ SOURCE DOCUMENT CLEANUP ═══

13. The source document may come from OCR or PDF text extraction. CLEAN UP these artifacts:
    - REMOVE garbage strings that are clearly OCR noise (e.g. random alphanumeric IDs at the
      very start/end like "DIN-202603DEESOOOOO0EECC", stray characters, broken encodings)
    - REMOVE repeated page headers and footers (office addresses, phone/fax numbers, email
      addresses, file reference numbers that appear identically on multiple pages)
    - REMOVE stamps, seal descriptions, and "page X of Y" markers
    - FIX obvious OCR errors in names, dates, and legal terms
    - KEEP the substantive legal content, parties, dates, section references, and orders intact
    - When in doubt, KEEP the content rather than removing it

═══ OUTPUT FORMAT ═══

Output ONLY the translated document in clean markdown.
Do NOT add translator notes, comments, preamble, or explanations outside the document.
Do NOT wrap the output in code fences.
Do NOT add a "Translated by" footer or any metadata.
Do NOT include "BEGIN DOCUMENT", "END DOCUMENT", or any wrapper markers in your output.
The output should be the translated document and nothing else."""


def _clean_output(text: str) -> str:
    """Strip wrapper markers the LLM may echo back."""
    import re
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
