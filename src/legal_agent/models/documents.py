"""Document type definitions and schemas."""

from enum import Enum

from pydantic import BaseModel, Field


class Language(str, Enum):
    """Language options for document drafting."""

    ENGLISH = "english"
    HINDI = "hindi"
    BILINGUAL = "bilingual"


class TranslationLanguage(str, Enum):
    """All Indian scheduled languages + English for translation."""

    ENGLISH = "english"
    HINDI = "hindi"
    BENGALI = "bengali"
    TELUGU = "telugu"
    MARATHI = "marathi"
    TAMIL = "tamil"
    URDU = "urdu"
    GUJARATI = "gujarati"
    KANNADA = "kannada"
    MALAYALAM = "malayalam"
    ODIA = "odia"
    PUNJABI = "punjabi"
    ASSAMESE = "assamese"
    MAITHILI = "maithili"
    SANTALI = "santali"
    KASHMIRI = "kashmiri"
    NEPALI = "nepali"
    SINDHI = "sindhi"
    DOGRI = "dogri"
    KONKANI = "konkani"
    MANIPURI = "manipuri"
    BODO = "bodo"
    SANSKRIT = "sanskrit"


LANGUAGE_NATIVE_NAMES: dict[str, str] = {
    "english": "English",
    "hindi": "हिन्दी",
    "bengali": "বাংলা",
    "telugu": "తెలుగు",
    "marathi": "मराठी",
    "tamil": "தமிழ்",
    "urdu": "اردو",
    "gujarati": "ગુજરાતી",
    "kannada": "ಕನ್ನಡ",
    "malayalam": "മലയാളം",
    "odia": "ଓଡ଼ିଆ",
    "punjabi": "ਪੰਜਾਬੀ",
    "assamese": "অসমীয়া",
    "maithili": "मैथिली",
    "santali": "ᱥᱟᱱᱛᱟᱲᱤ",
    "kashmiri": "कॉशुर",
    "nepali": "नेपाली",
    "sindhi": "سنڌي",
    "dogri": "डोगरी",
    "konkani": "कोंकणी",
    "manipuri": "মৈতৈলোন্",
    "bodo": "बड़ो",
    "sanskrit": "संस्कृत",
}


class DocumentType(str, Enum):
    """Types of legal documents that can be drafted."""

    CONTRACT = "contract"
    AGREEMENT = "agreement"
    LEGAL_NOTICE = "legal_notice"
    DEMAND_NOTICE = "demand_notice"
    PETITION = "petition"
    AFFIDAVIT = "affidavit"
    APPLICATION = "application"
    BAIL_APPLICATION = "bail_application"
    CRIMINAL_APPEAL = "criminal_appeal"
    PATENT = "patent"
    APPLICATION_DRAFT = "application_draft"
    WRITTEN_STATEMENT = "written_statement"
    WRITTEN_ARGUMENTS = "written_arguments"
    SLP = "slp"
    QUASHING_PETITION = "quashing_petition"
    ANTICIPATORY_BAIL = "anticipatory_bail"
    REVISION_PETITION = "revision_petition"
    EXECUTION_PETITION = "execution_petition"
    CONSUMER_COMPLAINT = "consumer_complaint"


class DocumentSection(BaseModel):
    """A section within a legal document."""

    title: str = Field(..., description="Section title")
    content: str = Field(..., description="Section content")
    order: int = Field(..., description="Section order in the document")


class GeneratedDocument(BaseModel):
    """Schema for a generated legal document."""

    title: str = Field(..., description="Document title")
    document_type: DocumentType = Field(..., description="Type of document")
    draft: str = Field(..., description="Full document text")
    sections: list[DocumentSection] = Field(
        default_factory=list, description="Structured sections if applicable"
    )
    summary: str | None = Field(None, description="Brief summary of the document")
