"""HTML render: page-size CSS, absolute positioning, inline-tag sanitisation."""

from __future__ import annotations

from legal_agent.agents.translation_v2.html_render import (
    _sanitize_inline,
    load_font_face_css,
    render_page_html,
)
from legal_agent.agents.translation_v2.schemas import Block, BlockAlign, BlockWeight, TranslatedPage


def _page(blocks: list[Block]) -> TranslatedPage:
    return TranslatedPage(page_no=1, width_pt=595, height_pt=842, blocks=blocks)


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


def test_render_page_includes_exact_page_size_in_mm():
    block = Block(
        id="p1-b01",
        bbox_norm=(0.1, 0.1, 0.5, 0.2),
        text_en="Hello",
        text_hi="नमस्ते",
        font_size_pt=12.0,
    )
    page_w_mm = 210.0
    page_h_mm = 297.0
    html = render_page_html(_page([block]), page_w_mm, page_h_mm, font_face_css="")
    assert "@page" in html
    assert f"{page_w_mm:.2f}mm {page_h_mm:.2f}mm" in html
    # Block position derived from bbox * page_mm
    assert "left:21.00mm" in html
    assert "top:29.70mm" in html
    # Hindi text is in the body
    assert "नमस्ते" in html


def test_render_applies_bold_italic_underline_classes():
    block = Block(
        id="p1-b01",
        bbox_norm=(0.0, 0.0, 1.0, 0.1),
        text_en="x",
        text_hi="x",
        font_size_pt=12.0,
        weight=BlockWeight.bold,
        italic=True,
        underline=True,
    )
    html = render_page_html(_page([block]), 210.0, 297.0, font_face_css="")
    assert "bold" in html
    assert "italic" in html
    assert "underline" in html


def test_render_applies_alignment_data_attr():
    block = Block(
        id="p1-b01",
        bbox_norm=(0.0, 0.0, 1.0, 0.1),
        text_en="x",
        text_hi="x",
        font_size_pt=12.0,
        align=BlockAlign.right,
    )
    html = render_page_html(_page([block]), 210.0, 297.0, font_face_css="")
    assert 'data-align="right"' in html


def test_render_orders_blocks_by_reading_order():
    a = Block(
        id="p1-b01",
        bbox_norm=(0.0, 0.0, 1.0, 0.05),
        text_en="second",
        text_hi="दूसरा",
        font_size_pt=12.0,
        reading_order=1,
    )
    b = Block(
        id="p1-b02",
        bbox_norm=(0.0, 0.05, 1.0, 0.1),
        text_en="first",
        text_hi="पहला",
        font_size_pt=12.0,
        reading_order=0,
    )
    html = render_page_html(_page([a, b]), 210.0, 297.0, font_face_css="")
    assert html.index("पहला") < html.index("दूसरा")


def test_font_face_css_embeds_two_weights():
    css = load_font_face_css()
    assert "font-weight: 400" in css
    assert "font-weight: 700" in css
    assert "data:font/ttf;base64," in css
