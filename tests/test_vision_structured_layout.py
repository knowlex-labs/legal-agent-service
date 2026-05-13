"""Tests for scanned-PDF structured vision layout parsing and sanitization."""

from legal_agent.agents.translation.vision_header_normalize import normalize_government_header_blocks
from legal_agent.agents.translation.vision_structured_layout import (
    sanitize_vision_inline_html,
    parse_vision_structured_response,
    vision_structured_page_to_section_html,
)


def test_sanitize_vision_inline_html_strips_unknown_tags():
    raw = 'Hello<script>x</script><strong>B</strong><foo>bar</foo>'
    out = sanitize_vision_inline_html(raw)
    assert "<script>" not in out
    assert "<foo>" not in out
    assert "<strong>B</strong>" in out


def test_parse_vision_structured_response_roundtrip():
    payload = """```json
{
  "page": 1,
  "blocks": [
    {"type": "text", "role": "letterhead", "align": "center", "weight": "bold",
     "size": "large", "line_spacing": "tight", "html": "<strong>TITLE</strong>"},
    {"type": "row", "role": "meta_row", "left_html": "F.NO-X", "right_html": "दिनांक: 01.01.2026"}
  ]
}
```
"""
    page = parse_vision_structured_response(payload)
    assert page is not None
    assert len(page.blocks) == 2
    blocks = normalize_government_header_blocks(list(page.blocks))
    html = vision_structured_page_to_section_html(page.model_copy(update={"blocks": blocks}), 1)
    assert 'class="vision-structured"' in html
    assert "vt-role-letterhead" in html
    assert "vt-row" in html
