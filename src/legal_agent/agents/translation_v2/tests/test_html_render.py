"""HTML render (flow layout): semantic emission per role, hanging indents,
inline-tag sanitisation, HTML-table reconstruction from cell metadata."""

from __future__ import annotations

from legal_agent.agents.translation_v2.html_render import (
    NUMBERED_RE,
    _sanitize_inline,
    load_font_face_css,
    render_page_html,
)
from legal_agent.agents.translation_v2.schemas import (
    Block,
    BlockRole,
    BlockWeight,
    TranslatedPage,
)


def _block(
    id_: str,
    role: BlockRole = BlockRole.paragraph,
    *,
    text_en: str = "",
    text_hi: str | None = None,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    reading_order: int = 0,
    table_id: int | None = None,
    row_index: int = 0,
    column_index: int = 0,
    row_span: int = 1,
    column_span: int = 1,
    is_header_cell: bool = False,
) -> Block:
    return Block(
        id=id_,
        role=role,
        bbox_norm=(0.1, 0.1, 0.9, 0.2),
        text_en=text_en,
        text_hi=text_hi,
        weight=BlockWeight.bold if bold else BlockWeight.normal,
        italic=italic,
        underline=underline,
        font_size_pt=11.0,
        reading_order=reading_order,
        table_id=table_id,
        row_index=row_index,
        column_index=column_index,
        row_span=row_span,
        column_span=column_span,
        is_header_cell=is_header_cell,
    )


def _page(blocks: list[Block]) -> TranslatedPage:
    return TranslatedPage(page_no=1, width_pt=595, height_pt=842, blocks=blocks)


# ── Inline sanitisation ────────────────────────────────────────────────────


def test_sanitize_keeps_inline_tags_only():
    html = _sanitize_inline("Hello <b>bold</b> & <script>alert(1)</script><i>italic</i>")
    assert "<b>" in html and "</b>" in html
    assert "<i>" in html and "</i>" in html
    assert "<script>" not in html
    assert "alert(1)" in html  # Content escaped but preserved as text
    assert "&amp;" in html


def test_sanitize_normalizes_strong_em():
    html = _sanitize_inline("<strong>x</strong> and <em>y</em>")
    assert html == "<b>x</b> and <i>y</i>"


# ── Page shell ─────────────────────────────────────────────────────────────


def test_render_includes_exact_page_size_in_mm():
    html = render_page_html(
        _page([_block("p1_b0", text_en="Hello", text_hi="नमस्ते")]),
        210.0, 297.0, font_face_css="",
    )
    assert "@page" in html
    assert "210.00mm 297.00mm" in html
    assert "नमस्ते" in html


def test_render_emits_page_content_wrapper():
    html = render_page_html(
        _page([_block("p1_b0", text_en="x")]),
        210.0, 297.0, font_face_css="",
    )
    assert 'class="page-content"' in html


def test_render_orders_blocks_by_reading_order():
    a = _block("p1_b1", text_en="second", text_hi="दूसरा", reading_order=1)
    b = _block("p1_b2", text_en="first", text_hi="पहला", reading_order=0)
    html = render_page_html(_page([a, b]), 210.0, 297.0, font_face_css="")
    assert html.index("पहला") < html.index("दूसरा")


def test_font_face_css_embeds_two_weights():
    css = load_font_face_css()
    assert "font-weight: 400" in css
    assert "font-weight: 700" in css
    assert "data:font/ttf;base64," in css


# ── Role → semantic tag ────────────────────────────────────────────────────


def test_title_role_emits_h1():
    html = render_page_html(
        _page([_block("p1_b0", role=BlockRole.title, text_en="HIGH COURT OF MP")]),
        210.0, 297.0, font_face_css="",
    )
    assert '<h1 class="title"' in html
    assert "HIGH COURT OF MP" in html


def test_heading_role_emits_h2():
    html = render_page_html(
        _page([_block("p1_b0", role=BlockRole.heading, text_en="FACTS OF THE CASE")]),
        210.0, 297.0, font_face_css="",
    )
    assert '<h2 class="heading"' in html
    assert "FACTS OF THE CASE" in html


def test_paragraph_role_emits_p():
    html = render_page_html(
        _page([_block("p1_b0", text_en="Body text here.")]),
        210.0, 297.0, font_face_css="",
    )
    assert '<p class="paragraph' in html


def test_header_role_emits_page_header_band():
    html = render_page_html(
        _page([_block("p1_b0", role=BlockRole.header, text_en="Court of MP")]),
        210.0, 297.0, font_face_css="",
    )
    assert '<div class="page-header"' in html


def test_footer_and_page_number_emit_page_footer_band():
    html = render_page_html(
        _page([
            _block("p1_b0", role=BlockRole.footer, text_en="Continued..."),
            _block("p1_b1", role=BlockRole.page_number, text_en="3", reading_order=1),
        ]),
        210.0, 297.0, font_face_css="",
    )
    assert html.count('<div class="page-footer"') == 2


def test_separator_role_is_dropped():
    html = render_page_html(
        _page([
            _block("p1_b0", role=BlockRole.separator, text_en=""),
            _block("p1_b1", text_en="real", reading_order=1),
        ]),
        210.0, 297.0, font_face_css="",
    )
    assert "p1_b0" not in html
    assert "real" in html


def test_style_classes_on_paragraph():
    html = render_page_html(
        _page([_block("p1_b0", text_en="x", bold=True, italic=True, underline=True)]),
        210.0, 297.0, font_face_css="",
    )
    assert "bold" in html
    assert "italic" in html
    assert "underline" in html


# ── Hanging-indent numbered items ──────────────────────────────────────────


def test_numbered_re_matches_decimal():
    m = NUMBERED_RE.match("1. That, on 12.03.2026...")
    assert m is not None
    assert m.group(1) == "1."


def test_numbered_re_matches_parenthesised_letter():
    m = NUMBERED_RE.match("(a) Sub-clause text")
    assert m is not None
    assert m.group(1) == "(a)"


def test_numbered_re_matches_parenthesised_roman():
    m = NUMBERED_RE.match("(i) First item")
    assert m is not None
    assert m.group(1) == "(i)"


def test_numbered_re_matches_letter_paren():
    m = NUMBERED_RE.match("a) Body text")
    assert m is not None


def test_numbered_re_matches_devanagari_marker():
    m = NUMBERED_RE.match("क) हिन्दी पाठ")
    assert m is not None


def test_numbered_re_does_not_match_plain_text():
    assert NUMBERED_RE.match("Plain sentence without marker") is None


def test_numbered_re_requires_whitespace_after_marker():
    # "1.abc" with no space — should NOT match (could be filename, version).
    assert NUMBERED_RE.match("1.abc") is None


def test_numbered_paragraph_wraps_marker_in_hanging_indent():
    block = _block(
        "p1_b0",
        text_en="1. That, on 12.03.2026 father of the deceased lodged the report.",
    )
    html = render_page_html(_page([block]), 210.0, 297.0, font_face_css="")
    assert 'class="paragraph numbered"' in html
    assert '<span class="marker">1.</span>' in html
    assert '<span class="body">' in html
    assert "That, on 12.03.2026" in html


def test_plain_paragraph_not_numbered():
    block = _block("p1_b0", text_en="Plain body paragraph without marker.")
    html = render_page_html(_page([block]), 210.0, 297.0, font_face_css="")
    assert "numbered" not in html


# ── HTML table reconstruction from cell metadata ───────────────────────────


def test_table_groups_cells_into_single_html_table():
    blocks = [
        _block("p1_b0", role=BlockRole.table_cell, text_en="S.No.",
               table_id=0, row_index=0, column_index=0,
               is_header_cell=True, reading_order=0),
        _block("p1_b1", role=BlockRole.table_cell, text_en="Page",
               table_id=0, row_index=0, column_index=1,
               is_header_cell=True, reading_order=1),
        _block("p1_b2", role=BlockRole.table_cell, text_en="1.",
               table_id=0, row_index=1, column_index=0, reading_order=2),
        _block("p1_b3", role=BlockRole.table_cell, text_en="1-9",
               table_id=0, row_index=1, column_index=1, reading_order=3),
    ]
    html = render_page_html(_page(blocks), 210.0, 297.0, font_face_css="")
    assert '<table class="legal-table">' in html
    # Header row in <thead><th>
    assert "<thead>" in html
    assert "<th>S.No.</th>" in html
    assert "<th>Page</th>" in html
    # Body in <tbody><td>
    assert "<tbody>" in html
    assert "<td>1.</td>" in html
    assert "<td>1-9</td>" in html
    # Single <table>
    assert html.count("<table") == 1


def test_table_without_header_emits_tbody_only():
    blocks = [
        _block("p1_b0", role=BlockRole.table_cell, text_en="A",
               table_id=0, row_index=0, column_index=0, reading_order=0),
        _block("p1_b1", role=BlockRole.table_cell, text_en="B",
               table_id=0, row_index=0, column_index=1, reading_order=1),
    ]
    html = render_page_html(_page(blocks), 210.0, 297.0, font_face_css="")
    assert "<thead>" not in html
    assert "<tbody>" in html
    assert "<td>A</td>" in html


def test_table_cell_rowspan_and_colspan_emitted():
    blocks = [
        _block("p1_b0", role=BlockRole.table_cell, text_en="Spanning",
               table_id=0, row_index=0, column_index=0,
               row_span=2, column_span=3, reading_order=0),
    ]
    html = render_page_html(_page(blocks), 210.0, 297.0, font_face_css="")
    assert 'rowspan="2"' in html
    assert 'colspan="3"' in html


def test_two_tables_emit_separate_html_tables():
    blocks = [
        _block("p1_b0", role=BlockRole.table_cell, text_en="A",
               table_id=0, row_index=0, column_index=0, reading_order=0),
        _block("p1_b1", role=BlockRole.table_cell, text_en="B",
               table_id=1, row_index=0, column_index=0, reading_order=1),
    ]
    html = render_page_html(_page(blocks), 210.0, 297.0, font_face_css="")
    assert html.count("<table") == 2
