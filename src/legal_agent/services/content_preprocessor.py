"""Content preprocessor for fixing spelling, standardizing legal terminology,
and enhancing casual user input into structured legal instructions via LLM."""

import logging
import re

from pydantic_ai import Agent

logger = logging.getLogger(__name__)

# Common legal misspellings and corrections
SPELLING_CORRECTIONS: dict[str, str] = {
    # Legal terms
    "defendent": "defendant",
    "plantiff": "plaintiff",
    "planitiff": "plaintiff",
    "plaintif": "plaintiff",
    "warantee": "warranty",
    "warrantee": "warranty",
    "agrrement": "agreement",
    "agrement": "agreement",
    "agreemnt": "agreement",
    "breech": "breach",
    "liabilty": "liability",
    "liablity": "liability",
    "indemnificaiton": "indemnification",
    "indemnfication": "indemnification",
    "jurisdiciton": "jurisdiction",
    "juridiction": "jurisdiction",
    "arbitraiton": "arbitration",
    "arbitartion": "arbitration",
    "termiantion": "termination",
    "terminaiton": "termination",
    "confedential": "confidential",
    "confidetial": "confidential",
    "represtation": "representation",
    "representaiton": "representation",
    "indeminity": "indemnity",
    "indemniy": "indemnity",
    "excecution": "execution",
    "executoin": "execution",
    "afadavit": "affidavit",
    "affadavit": "affidavit",
    "affidavid": "affidavit",
    "petitoner": "petitioner",
    "petitionor": "petitioner",
    "respondant": "respondent",
    "responedent": "respondent",
    "verfication": "verification",
    "verificaiton": "verification",
    "notarised": "notarized",
    "notarized": "notarized",
    "vakalatnama": "Vakalatnama",
    "vakaltnama": "Vakalatnama",
    # Common words
    "recieve": "receive",
    "reciept": "receipt",
    "occured": "occurred",
    "occurence": "occurrence",
    "occurance": "occurrence",
    "seperate": "separate",
    "seperately": "separately",
    "accomodation": "accommodation",
    "acommodation": "accommodation",
    "harrass": "harass",
    "harrassment": "harassment",
    "harassement": "harassment",
    "maintainance": "maintenance",
    "maintenence": "maintenance",
    "posession": "possession",
    "possesion": "possession",
    "proffesional": "professional",
    "profesional": "professional",
    "commision": "commission",
    "comission": "commission",
    "gurantee": "guarantee",
    "gaurantee": "guarantee",
    "calender": "calendar",
    "calander": "calendar",
    "refered": "referred",
    "reffered": "referred",
    "transfered": "transferred",
    "occassion": "occasion",
    "ocassion": "occasion",
    "neccessary": "necessary",
    "necesary": "necessary",
    "immediatly": "immediately",
    "imediately": "immediately",
    "permanant": "permanent",
    "permenant": "permanent",
    "acknowlegement": "acknowledgement",
    "acknowledgment": "acknowledgement",
    "fullfill": "fulfill",
    "fulfil": "fulfill",
    "cancelled": "canceled",  # Keep Indian English spelling
    "untill": "until",
    "sucessful": "successful",
    "succesful": "successful",
}

# Currency and amount standardizations
CURRENCY_PATTERNS: list[tuple[str, str]] = [
    (r'\brs\.?\s*(\d)', r'Rs. \1'),  # rs 5000 or rs.5000 → Rs. 5000
    (r'\bRs\.?\s*(\d)', r'Rs. \1'),  # Rs5000 → Rs. 5000
    (r'\binr\.?\s*(\d)', r'INR \1'),  # inr 5000 → INR 5000
    (r'\bINR\.?\s*(\d)', r'INR \1'),  # INR5000 → INR 5000
    (r'(\d),(\d{3}),(\d{3})/-', r'\1,\2,\3/-'),  # Keep lakhs format
    (r'(\d+)/-', r'\1/-'),  # Keep /- suffix
]

# Legal section/act standardizations
LEGAL_STANDARDIZATIONS: dict[str, str] = {
    "ipc": "IPC",
    "crpc": "CrPC",
    "cpc": "CPC",
    "bns": "BNS",
    "bnss": "BNSS",
    "section 138": "Section 138",
    "section 420": "Section 420",
    "section 406": "Section 406",
    "article 226": "Article 226",
    "article 32": "Article 32",
}


def preprocess_content(text: str) -> str:
    """
    Fix spelling mistakes and standardize legal terminology.

    Args:
        text: Raw user input text

    Returns:
        Cleaned text with spelling fixes and standardized terminology
    """
    if not text:
        return text

    result = text

    # Step 1: Fix spelling mistakes (case-insensitive)
    for wrong, correct in SPELLING_CORRECTIONS.items():
        # Use word boundaries to avoid partial replacements
        pattern = rf'\b{re.escape(wrong)}\b'
        result = re.sub(pattern, correct, result, flags=re.IGNORECASE)

    # Step 2: Standardize currency formatting
    for pattern, replacement in CURRENCY_PATTERNS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)

    # Step 3: Standardize legal terms (case-sensitive replacement)
    for term, standard in LEGAL_STANDARDIZATIONS.items():
        pattern = rf'\b{re.escape(term)}\b'
        result = re.sub(pattern, standard, result, flags=re.IGNORECASE)

    # Step 4: Fix common punctuation issues
    result = re.sub(r'\s+([.,;:])', r'\1', result)  # Remove space before punctuation
    result = re.sub(r'([.,;:])([^\s\d])', r'\1 \2', result)  # Add space after punctuation
    result = re.sub(r'\s{2,}', ' ', result)  # Collapse multiple spaces

    return result.strip()


def preprocess_title(title: str) -> str:
    """
    Clean and standardize document title.

    Args:
        title: Document title

    Returns:
        Cleaned title
    """
    if not title:
        return title

    # Apply general preprocessing
    result = preprocess_content(title)

    # Title-specific: Capitalize important words
    # But preserve legal formatting like "vs." or "Vs."
    return result


# --- LLM-based Content Enhancement ---

ENHANCE_SYSTEM_PROMPT = """You are a legal writing assistant specializing in Indian law.

Your job is to EXTRACT and STRUCTURE all factual details from informal user input so that
a drafting agent can use them directly without needing placeholders.

CRITICAL: Your output will be used directly for drafting. You MUST:
1. Extract ALL names, dates, amounts, addresses, phone numbers mentioned
2. Structure them clearly so the drafting agent can use them
3. If user mentions "my flat" - describe it as "the property owned by the plaintiff"
4. If user mentions "2 years" - calculate approximate dates/amounts if possible
5. Convert casual references to formal legal references
6. PRESERVE all factual details exactly - do not add facts not in the original
7. Organize information logically and chronologically

VERY IMPORTANT - STRUCTURED PARTY DETAILS:
For court filings (affidavits, petitions, applications), you MUST extract and present
party details in this EXACT structured format:

=== PLAINTIFF/PETITIONER DETAILS ===
Full Name: [Extract full name with title - Shri/Smt/Mr./Ms.]
Age: [XX yrs]
Occupation: [Full occupation description]
Address: [Complete multi-line address]
  Line 1: [House/Flat No., Street]
  Line 2: [Area/Locality]
  Line 3: [City, State - Pincode]
Mobile: [10-digit number]

=== DEFENDANT/RESPONDENT DETAILS ===
Full Name: [Extract full name with title]
Age: [XX yrs]
Occupation: [Occupation]
Address: [Complete multi-line address]
  Line 1: [House/Flat No., Building, Street]
  Line 2: [Area/Locality]
  Line 3: [City/District - Pincode]
Mobile: [10-digit number]

=== COURT DETAILS ===
Court Name: [Full court name]
Location: [City]
Case Number: [If existing, e.g., Civil Suit No. XXX/YYYY]

=== PROPERTY/SUBJECT MATTER ===
Type: [Flat/Land/Goods/Amount]
Description: [Full description - area, survey no., etc.]
Location: [Full address of property]

=== CHRONOLOGICAL FACTS ===
1. [Date/Period]: [Event description]
2. [Date/Period]: [Event description]
...

=== AMOUNTS/CALCULATIONS ===
- [Description]: Rs. X,XX,XXX/- (Amount in words)
- Monthly/Periodic Amount: Rs. X,XXX/-
- Total Due: Rs. X,XX,XXX/- (calculation: X months × Rs. X,XXX)

=== RELIEF SOUGHT ===
1. [Primary relief]
2. [Secondary relief]
...

=== ADVOCATE DETAILS ===
Name: [Advocate name if mentioned]
Credentials: [If mentioned]

If any detail is not provided in the input, write "[NOT PROVIDED - use 'the plaintiff'/'the defendant' etc.]"
for names/roles, or "[NOT PROVIDED]" for other fields.

Output ONLY the structured instructions - no explanations, no preamble."""

ENHANCE_PROMPT_TEMPLATE = """Rewrite the following informal instructions into clear, structured
legal drafting instructions for a {document_type} under Indian law.

--- USER INPUT ---
{user_input}
--- END ---

Rewrite the above into well-structured, formal legal instructions. Preserve ALL facts exactly."""


async def enhance_content(
    text: str,
    document_type: str,
    model: str = "openai:gpt-4o-mini",
) -> str:
    """
    Use a fast LLM to enhance casual user input into structured legal instructions.

    This transforms informal text like:
        "my tenant not paying rent 2 years, flat in pune, want him out"
    Into structured legal language like:
        "The plaintiff is the owner of a residential flat located in Pune.
         The defendant/tenant has defaulted on payment of monthly rent for
         a continuous period of 2 years. The plaintiff seeks eviction of
         the tenant and recovery of rent arrears."

    Args:
        text: Raw user input (possibly casual/informal)
        document_type: Type of legal document being drafted
        model: LLM model to use (should be fast/cheap)

    Returns:
        Enhanced, well-structured legal instructions
    """
    if not text or len(text.strip()) < 10:
        return text

    # Map internal types to readable names for the prompt
    type_labels = {
        "legal_notice": "Legal Notice",
        "demand_notice": "Demand Notice",
        "affidavit": "Affidavit",
        "petition": "Court Petition / Plaint",
        "application": "Court Application",
        "contract": "Contract / Agreement",
        "agreement": "Agreement",
    }
    doc_label = type_labels.get(document_type, document_type.replace("_", " ").title())

    prompt = ENHANCE_PROMPT_TEMPLATE.format(
        document_type=doc_label,
        user_input=text,
    )

    try:
        enhancer = Agent(
            model,
            system_prompt=ENHANCE_SYSTEM_PROMPT,
            output_type=str,
        )
        result = await enhancer.run(prompt)
        enhanced = result.output

        if enhanced and len(enhanced.strip()) > 0:
            logger.debug(
                f"Content enhanced: {len(text)} chars → {len(enhanced)} chars"
            )
            return enhanced

    except Exception as e:
        logger.warning(f"LLM enhancement failed, using rule-based output: {e}")

    # Fallback to original text if LLM fails
    return text


async def preprocess_and_enhance(
    text: str,
    document_type: str,
    model: str = "openai:gpt-4o-mini",
) -> str:
    """
    Full preprocessing pipeline: rule-based fixes + LLM enhancement.

    Args:
        text: Raw user input
        document_type: Type of legal document
        model: LLM model for enhancement

    Returns:
        Cleaned and enhanced text
    """
    # Step 1: Rule-based fixes (spelling, formatting) - instant
    cleaned = preprocess_content(text)

    # Step 2: LLM enhancement (rewrite into legal language)
    enhanced = await enhance_content(cleaned, document_type, model)

    return enhanced
