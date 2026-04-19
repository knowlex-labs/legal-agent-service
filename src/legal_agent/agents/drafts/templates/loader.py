"""Load per-sub-type template-reference markdown files at draft time."""

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent


@lru_cache(maxsize=64)
def load_template_reference(category: str, sub_type: str | None) -> str | None:
    """Return the raw markdown (including YAML frontmatter, if any) for a
    template-reference file keyed by `category/sub_type.md`.

    Returns None — and logs a warning — if `sub_type` is empty or no matching
    file exists. Callers should treat None as "no template reference for this
    draft" and proceed without the block.
    """
    if not sub_type:
        return None
    path = _TEMPLATES_DIR / category / f"{sub_type}.md"
    if not path.is_file():
        logger.warning(
            "Template reference not found: %s (category=%s, sub_type=%s)",
            path,
            category,
            sub_type,
        )
        return None
    return path.read_text(encoding="utf-8")
