"""Paragraph- and role-aware chunker for the translation pipeline.

Replaces the legacy char-budget `_pack` when `translation_semantic_chunking`
is enabled. Goals:

- A region is never split across chunks unless it alone exceeds the char
  budget (and even then, falls back to sentence-boundary splitting).
- Footnote-role regions chunked into footnote-only chunks — so a body chunk
  is never contaminated by fine-print register.
- Heading / title-role regions chunked 1:1 — they translate cleaner when
  isolated, and it lets the reviewer see them as standalone.
- Body regions packed up to the char budget, but the chunker prefers to
  start a new chunk at a paragraph boundary rather than fuse the last
  sentence of paragraph N with the first sentence of paragraph N+1.

The caller supplies `region_role(idx)` so the chunker stays decoupled from
the IR types.
"""

from __future__ import annotations

from collections.abc import Callable

_ISOLATED_ROLES = {"title", "heading", "page_header", "page_number", "caption"}
_BODY_ROLES = {"body", None, "body_clause", "subject", "general", "letterhead"}


def chunk_regions(
    region_texts: list[str],
    *,
    region_role: Callable[[int], str | None],
    max_chars: int,
) -> list[list[int]]:
    """Pack region indices into chunks honouring role boundaries.

    Returns a list of chunks where each chunk is a list of region indices
    (in document order). Each chunk's total joined char count fits in
    `max_chars`, except when a single region exceeds the budget — that
    region goes into a singleton chunk and the caller is expected to fall
    back to a sentence-boundary splitter for its translate call.
    """
    if not region_texts:
        return []
    chunks: list[list[int]] = []
    current: list[int] = []
    current_chars = 0
    current_role_class: str | None = None

    def flush() -> None:
        nonlocal current, current_chars, current_role_class
        if current:
            chunks.append(current)
        current = []
        current_chars = 0
        current_role_class = None

    for i, text in enumerate(region_texts):
        role = region_role(i)
        # Role classes: isolated (titles, headings, captions go alone),
        # footnote (footnotes packed only with other footnotes), body (rest).
        if role in _ISOLATED_ROLES:
            flush()
            chunks.append([i])
            continue
        role_class = "footnote" if role == "footnote" else "body"
        size = len(text) + 2  # account for `\n\n` join
        if size > max_chars:
            flush()
            chunks.append([i])
            continue
        if current_role_class is not None and current_role_class != role_class:
            flush()
        if current_chars + size > max_chars:
            flush()
        current.append(i)
        current_chars += size
        current_role_class = role_class

    flush()
    return chunks
