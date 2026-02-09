"""Query expansion for Indian legal abbreviations."""

import re

LEGAL_ABBREVIATIONS = {
    "CrPC": "Code of Criminal Procedure",
    "CPC": "Code of Civil Procedure",
    "IPC": "Indian Penal Code",
    "BNS": "Bharatiya Nyaya Sanhita",
    "BNSS": "Bharatiya Nagarik Suraksha Sanhita",
    "BSA": "Bharatiya Sakshya Adhiniyam",
    "SC": "Supreme Court",
    "HC": "High Court",
    "SLP": "Special Leave Petition",
    "PIL": "Public Interest Litigation",
    "FIR": "First Information Report",
    "NI Act": "Negotiable Instruments Act",
    "IT Act": "Information Technology Act",
    "POCSO": "Protection of Children from Sexual Offences",
    "DV Act": "Domestic Violence Act",
    "NDPS": "Narcotic Drugs and Psychotropic Substances",
    "RTI": "Right to Information",
    "SARFAESI": "Securitisation and Reconstruction of Financial Assets and Enforcement of Security Interest",
    "NCLT": "National Company Law Tribunal",
    "NCLAT": "National Company Law Appellate Tribunal",
    "DRT": "Debt Recovery Tribunal",
    "RERA": "Real Estate Regulatory Authority",
    "Art": "Article",
    "Sec": "Section",
    "S.": "Section",
}


def expand_query(query: str) -> str:
    """Append full forms of recognized legal abbreviations in the query."""
    expanded = query
    for abbr, full_form in LEGAL_ABBREVIATIONS.items():
        pattern = r"\b" + re.escape(abbr) + r"\b"
        if re.search(pattern, expanded):
            expanded = re.sub(pattern, f"{abbr} ({full_form})", expanded, count=1)
    return expanded
