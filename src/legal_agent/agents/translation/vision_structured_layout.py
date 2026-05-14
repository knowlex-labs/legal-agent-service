"""Structured JSON layout for scanned-PDF vision translation.

Parse vision-model JSON → sanitize inline HTML → emit HTML section + fidelity metrics.
"""

from __future__ import annotations

import html as html_stdlib
import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from legal_agent.agents.translation.layout_ir import (
    VisionLineSpacing,
    VisionRegionRole,
    VisionStructuredPage,
    VisionStyledRowBlock,
    VisionStyledTextBlock,
)

logger = logging.getLogger(__name__)

_SCRIPT_STYLE_RE = re.compile(
    r"<\s*(script|style)[^>]*>.*?</\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)

_ALLOWED_VOID = frozenset({"br"})
_ALLOWED_PAIR = frozenset({"strong", "em", "u"})


def sanitize_vision_inline_html(fragment: str) -> str:
    """Keep only safe inline tags (strong, em, u, br); strip everything else."""
    if not fragment:
        return ""
    t = _SCRIPT_STYLE_RE.sub("", fragment)
    # Remove comments and stray angle-bracket blobs that break rendering.
    t = re.sub(r"<!--.*?-->", "", t, flags=re.DOTALL)

    out: list[str] = []
    pos = 0
    tag_re = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\s*/?\s*>", re.IGNORECASE)

    for m in tag_re.finditer(t):
        out.append(html_stdlib.escape(t[pos : m.start()], quote=False))
        slash, name = m.group(1), m.group(2).lower()
        if slash:
            if name in _ALLOWED_PAIR:
                out.append(m.group(0))
            # drop disallowed closing tags
        else:
            if name in _ALLOWED_VOID:
                out.append(m.group(0))
            elif name in _ALLOWED_PAIR:
                out.append(m.group(0))
            # drop disallowed opens
        pos = m.end()
    out.append(html_stdlib.escape(t[pos:], quote=False))
    return "".join(out)


_ROLES: frozenset[str] = frozenset({
    # Indian govt / legal.
    "letterhead",
    "meta_row",
    "subject",
    "body_clause",
    "signature_block",
    "footer",
    # Academic / journal / general.
    "title",
    "author",
    "page_header",
    "page_number",
    "body",
    "footnote",
    "block_quote",
    "caption",
    "general",
})


def _coerce_role(v: Any) -> VisionRegionRole:
    if isinstance(v, str) and v in _ROLES:
        return v  # type: ignore[return-value]
    return "general"


def _coerce_align(v: Any) -> str:
    if v in ("left", "center", "right", "justify"):
        return v
    return "left"


def _coerce_weight(v: Any) -> str:
    if v in ("normal", "semibold", "bold"):
        return v
    return "normal"


def _coerce_size(v: Any) -> str:
    if v in ("xs", "small", "normal", "large", "xlarge"):
        return v
    return "normal"


def _coerce_line_spacing(v: Any) -> VisionLineSpacing:
    if v in ("tight", "normal", "relaxed"):
        return v  # type: ignore[return-value]
    return "normal"


def _block_from_dict(d: dict[str, Any]) -> VisionStyledTextBlock | VisionStyledRowBlock | None:
    t = (d.get("type") or "text").lower()
    if t == "row":
        return VisionStyledRowBlock(
            type="row",
            role=_coerce_role(d.get("role", "meta_row")),
            weight=_coerce_weight(d.get("weight")),
            size=_coerce_size(d.get("size")),
            left_html=sanitize_vision_inline_html(str(d.get("left_html") or d.get("left") or "")),
            right_html=sanitize_vision_inline_html(str(d.get("right_html") or d.get("right") or "")),
        )
    if t != "text":
        return None
    return VisionStyledTextBlock(
        type="text",
        role=_coerce_role(d.get("role")),
        align=_coerce_align(d.get("align")),  # type: ignore[arg-type]
        weight=_coerce_weight(d.get("weight")),
        size=_coerce_size(d.get("size")),
        line_spacing=_coerce_line_spacing(d.get("line_spacing")),
        html=sanitize_vision_inline_html(str(d.get("html") or d.get("text") or "")),
    )


def _extract_json_object(s: str) -> str | None:
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    quote = ""
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            in_str = True
            quote = ch
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def parse_vision_structured_response(raw: str) -> VisionStructuredPage | None:
    """Parse model output into VisionStructuredPage, or None if not valid JSON layout."""
    from legal_agent.utils.ocr import _strip_code_fence

    text = _strip_code_fence(raw).strip()
    blob = _extract_json_object(text)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        logger.debug("vision structured: JSON decode failed")
        return None
    if not isinstance(data, dict):
        return None
    blocks_raw = data.get("blocks")
    if not isinstance(blocks_raw, list) or not blocks_raw:
        return None
    blocks: list[VisionStyledTextBlock | VisionStyledRowBlock] = []
    for item in blocks_raw:
        if not isinstance(item, dict):
            continue
        b = _block_from_dict(item)
        if b is None:
            continue
        if isinstance(b, VisionStyledTextBlock) and not b.html.strip():
            continue
        if isinstance(b, VisionStyledRowBlock) and not (
            b.left_html.strip() or b.right_html.strip()
        ):
            continue
        blocks.append(b)
    if not blocks:
        return None
    page_no = data.get("page", 1)
    try:
        page_i = int(page_no)
    except (TypeError, ValueError):
        page_i = 1
    try:
        return VisionStructuredPage(page=page_i, blocks=blocks)
    except ValidationError:
        return None


def _css_classes_text(b: VisionStyledTextBlock) -> str:
    parts = [
        "vt-block",
        f"vt-role-{b.role}",
        f"vt-align-{b.align}",
        f"vt-w-{b.weight}",
        f"vt-sz-{b.size}",
        f"vt-lh-{b.line_spacing}",
    ]
    return " ".join(parts)


def _css_classes_row(b: VisionStyledRowBlock) -> str:
    return " ".join(
        [
            "row",
            "vt-row",
            f"vt-role-{b.role}",
            f"vt-w-{b.weight}",
            f"vt-sz-{b.size}",
        ]
    )


def vision_structured_page_to_section_html(page: VisionStructuredPage, page_no: int) -> str:
    """Emit a single <section> fragment for one page."""
    lines: list[str] = [
        f'<section data-page="{page_no}" class="vision-structured" data-vt-schema="v4">'
    ]
    for b in page.blocks:
        if isinstance(b, VisionStyledRowBlock):
            cls = _css_classes_row(b)
            lines.append(
                f'<div class="{cls}">'
                f'<div class="col-left vt-cell">{b.left_html}</div>'
                f'<div class="col-right vt-cell">{b.right_html}</div>'
                f"</div>"
            )
        else:
            cls = _css_classes_text(b)
            lines.append(f'<div class="{cls}">{b.html}</div>')
    lines.append("</section>")
    return "\n".join(lines)


def vision_fidelity_summary(
    blocks: list[VisionStyledTextBlock | VisionStyledRowBlock],
) -> dict[str, Any]:
    """Lightweight QA payload for logs / job metadata (A/B checklist)."""
    roles: dict[str, int] = {}
    n_boldish = 0
    n_center = 0
    for b in blocks:
        if isinstance(b, VisionStyledTextBlock):
            roles[b.role] = roles.get(b.role, 0) + 1
            if b.weight in ("semibold", "bold"):
                n_boldish += 1
            if b.align == "center":
                n_center += 1
        else:
            roles[b.role] = roles.get(b.role, 0) + 1
    return {
        "vision_fidelity_block_count": len(blocks),
        "vision_fidelity_roles": roles,
        "vision_fidelity_emphasis_blocks": n_boldish,
        "vision_fidelity_centered_text_blocks": n_center,
        "vision_fidelity_checklist": {
            "has_letterhead_role": roles.get("letterhead", 0) > 0,
            "has_meta_or_row": roles.get("meta_row", 0) > 0,
            "has_subject_role": roles.get("subject", 0) > 0,
            "has_body_clause_role": roles.get("body_clause", 0) > 0,
        },
    }
