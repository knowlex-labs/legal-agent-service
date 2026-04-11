"""Parse structured citations from legal_web_search tool output."""

import re

_BLOCK_SPLIT_RE = re.compile(r"(?=\[W\d+\]\s)")


def parse_legal_web_search_citations(tool_output: str) -> list[dict]:
    """Extract structured citations from legal_web_search tool output using block-based parsing."""
    blocks = _BLOCK_SPLIT_RE.split(tool_output)
    results: list[dict] = []
    for block in blocks:
        block = block.strip()
        id_m = re.match(r"\[W(\d+)\]\s*(.+)", block)
        if not id_m:
            continue
        cid = int(id_m.group(1))
        case_name = id_m.group(2).strip()

        def _field(name: str) -> str | None:
            m = re.search(rf"^{name}:\s*(.+)", block, re.MULTILINE)
            return m.group(1).strip() if m else None

        source = _field("Source") or "Web"
        url = _field("URL") or ""
        snippet = _field("Snippet")
        citation = _field("Citation")
        year_str = _field("Year")
        year = int(year_str) if year_str and year_str.isdigit() else None

        results.append(
            {
                "id": cid,
                "case_name": case_name,
                "source": source,
                "url": url,
                "snippet": snippet,
                "citation": citation,
                "year": year,
            }
        )
    return results
