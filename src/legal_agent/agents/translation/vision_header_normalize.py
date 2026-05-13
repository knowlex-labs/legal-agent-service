"""Rule-based normalization for structured vision translation blocks."""

from __future__ import annotations

from legal_agent.agents.translation.layout_ir import VisionStyledRowBlock, VisionStyledTextBlock


def normalize_government_header_blocks(
    blocks: list[VisionStyledTextBlock | VisionStyledRowBlock],
) -> list[VisionStyledTextBlock | VisionStyledRowBlock]:
    """Light header pass: infer letterhead zone before subject/body; fix row roles."""
    out: list[VisionStyledTextBlock | VisionStyledRowBlock] = []
    seen_anchor = False
    for b in blocks:
        if isinstance(b, VisionStyledTextBlock):
            if not seen_anchor and b.role == "general":
                # Centered blocks in the header band are usually letterhead lines.
                if b.align == "center" and len(b.html) <= 280:
                    b = b.model_copy(update={"role": "letterhead"})
                # Right-aligned DIN / file refs often sit below letterhead.
                elif b.align == "right" and not seen_anchor:
                    if any(
                        tok in b.html.upper()
                        for tok in ("DIN", "डीआईएन", "F.NO", "फा.", "दिनांक", "DATE")
                    ):
                        b = b.model_copy(update={"role": "meta_row"})
            if b.role in ("subject", "body_clause", "signature_block", "footer"):
                seen_anchor = True
        elif isinstance(b, VisionStyledRowBlock):
            if b.role == "general":
                b = b.model_copy(update={"role": "meta_row"})
            if b.role == "meta_row":
                seen_anchor = True
        out.append(b)
    return out
