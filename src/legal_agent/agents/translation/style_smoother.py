"""Stage C (style) — parallel Haiku style smoother.

Runs concurrently with the fidelity `Reviewer`. Where the reviewer's job is
faithfulness against the source, the smoother's job is target-language polish:

- catch over-Sanskritized constructions (e.g. unnecessary `वादीतिवादी`),
- catch awkward literal English-to-Hindi calques,
- insert missing spaces between fused content-words like `हैंकेवल → हैं केवल`
  — the failure mode that rule-based passes cannot solve without a Hindi
  morphological splitter,
- collapse repeated phrases.

The smoother emits **diff-style edits** (per-region list of `{span, replacement}`
records), not full rewrites. That output shape is what makes the parallel
reviewer/smoother merge composable: the merge layer can drop any smoother
edit whose span overlaps a reviewer correction, without losing the
non-conflicting smoother improvements.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)


_SMOOTHER_PROMPT_LEGAL = """\
You are a Hindi legal-prose style editor. The candidate translation below was
produced by a faithful translator + reviewer pipeline; the FACTS are correct
and should NOT be changed. Your single job is to make it read like modern
professional legal Hindi, not a government circular or a textbook.

Make ONLY these kinds of edits:
  1. Insert a missing space between two fused content-words.
     Example: "हैंकेवल" → "हैं केवल"; "नहींहै" → "नहीं है"; "केसाथ" → "के साथ".
  2. Replace over-Sanskritized words with the plainer modern equivalent
     when the meaning is identical. Example: "वादीतिवादी" → "विवादी"; avoid
     forced तत्सम when a common register word reads more naturally.
  3. Remove an immediately-adjacent duplicated phrase the rule-based dedup
     pass missed (e.g. "इस मामले में इस मामले में" → "इस मामले में").
  4. Fix obvious awkward literal calques (e.g. "लाल पट्टी की एक राशि" → a
     natural rendering) ONLY where you are certain the meaning matches.

You MUST NOT change:
  - Any number, date, currency, identifier, section reference, name, place,
    email, URL, or `[__NNNN__]` / `[__SEP_NNNN__]` / `[__VTAG_NNNN__]` /
    `[__STITCH_NNNN__]` placeholder.
  - Any glossary term in the binding list below.
  - Sentence meaning. If you'd need to rewrite a clause to make it natural,
    leave it.

Output format — JSON array, one entry per input region:
  [{{"index": <int>, "edits": [{{"span": [<start>, <end>], "replacement": "<text>"}}, ...]}}, ...]
- `span` indices are character offsets into the candidate string for that
  region (`[start, end)`).
- An empty `edits` list means no change.
- Return ALL regions in the order given, even those with no edits.
- No prose, no fences.

GLOSSARY (binding — do not rewrite these):
{glossary_lines}

CANDIDATE_TRANSLATION (Hindi):
{numbered_candidate}
"""


_SMOOTHER_PROMPT_GENERAL = """\
You are a Hindi style editor for academic and journalistic prose. The
candidate translation below was produced by a faithful translator + reviewer
pipeline; the FACTS are correct and must not be changed. Your single job is
to make it read like modern professional Hindi suitable for a journal or
researched article, not a textbook or government circular.

Make ONLY these kinds of edits:
  1. Insert a missing space between two fused content-words.
     Example: "हैंकेवल" → "हैं केवल"; "नहींहै" → "नहीं है".
  2. Replace over-Sanskritized vocabulary with the natural modern term
     when meaning is identical.
  3. Remove an immediately-adjacent duplicated phrase the rule-based dedup
     pass missed.
  4. Fix obvious awkward literal calques only where meaning is preserved.

You MUST NOT change:
  - Any number, date, currency, identifier, name, place, email, URL,
    case-name, citation, footnote marker, or sentinel placeholder.
  - Any glossary term in the binding list below.
  - Sentence meaning or structure beyond what these rules require.

Output format — JSON array, one entry per input region:
  [{{"index": <int>, "edits": [{{"span": [<start>, <end>], "replacement": "<text>"}}, ...]}}, ...]
- `span` indices are character offsets into the candidate string for that
  region (`[start, end)`).
- An empty `edits` list means no change.
- Return ALL regions in the order given, even those with no edits.
- No prose, no fences.

GLOSSARY (binding — do not rewrite these):
{glossary_lines}

CANDIDATE_TRANSLATION (Hindi):
{numbered_candidate}
"""


# Sentinels we must never touch — any edit overlapping one of these is dropped.
_SENTINEL_RE = re.compile(r"\[__(?:SEP_|VTAG_|STITCH_)?\d{4}__\]")


@dataclass
class SmootherEdit:
    span: tuple[int, int]
    replacement: str


@dataclass
class SmootherItem:
    index: int
    edits: list[SmootherEdit] = field(default_factory=list)


@dataclass
class SmootherResult:
    items: list[SmootherItem]
    changes: int


def _infer_provider(model: str) -> str:
    m = model.lower().removeprefix("models/")
    if m.startswith("gemini"):
        return "google-genai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gpt") or m.startswith("o"):
        return "openai"
    raise ValueError(f"Unsupported smoother model: {model!r}")


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


def _format_numbered(items: list[str]) -> str:
    return "\n".join(f"[{i}] {t}" for i, t in enumerate(items))


def _format_glossary_lines(glossary: dict[str, str] | None) -> str:
    if not glossary:
        return "(none)"
    return "\n".join(f"- {tgt}" for tgt in glossary.values())


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


def _edit_touches_sentinel(text: str, edit: SmootherEdit) -> bool:
    """Reject an edit whose span overlaps any [__NNNN__]-family sentinel
    in the original candidate. Belt-and-braces against an LLM that ignores
    the prompt rule about sentinels.
    """
    for m in _SENTINEL_RE.finditer(text):
        if _spans_overlap(edit.span, m.span()):
            return True
    return False


def apply_edits(text: str, edits: list[SmootherEdit]) -> str:
    """Apply a list of non-overlapping edits to `text`, right-to-left to keep
    offsets valid. Drops any edit that overlaps a sentinel or another edit
    already accepted.
    """
    if not edits:
        return text
    # Filter sentinel-overlapping edits.
    safe: list[SmootherEdit] = [e for e in edits if not _edit_touches_sentinel(text, e)]
    # Drop overlapping edits — keep the one that appears first.
    safe.sort(key=lambda e: e.span[0])
    accepted: list[SmootherEdit] = []
    for e in safe:
        if not accepted or e.span[0] >= accepted[-1].span[1]:
            accepted.append(e)
    # Sanity: span bounds.
    accepted = [e for e in accepted if 0 <= e.span[0] <= e.span[1] <= len(text)]
    # Apply right-to-left.
    for e in sorted(accepted, key=lambda x: x.span[0], reverse=True):
        text = text[: e.span[0]] + e.replacement + text[e.span[1]:]
    return text


class StyleSmoother:
    """Per-batch Haiku style smoother. Reuses one LangChain client."""

    def __init__(
        self,
        *,
        register: str = "general",
    ) -> None:
        settings = get_settings()
        self._enabled = settings.translation_smoother_enabled
        self._model = settings.translation_smoother_model
        self._register = (
            register if register in {"government_legal", "general"} else "general"
        )
        self._sem = asyncio.Semaphore(
            max(1, settings.translation_smoother_max_concurrency)
        )
        self._llm = (
            init_chat_model(self._model, model_provider=_infer_provider(self._model))
            if self._enabled
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def smooth(
        self,
        *,
        candidate_regions: list[str],
        glossary: dict[str, str] | None,
    ) -> SmootherResult:
        """Return smoother edits per region. On any failure, returns an empty
        edit list for every region so the pipeline never blocks on style.
        """
        empty = SmootherResult(
            items=[SmootherItem(index=i) for i in range(len(candidate_regions))],
            changes=0,
        )
        if not self._enabled or not candidate_regions or self._llm is None:
            return empty

        template = (
            _SMOOTHER_PROMPT_LEGAL
            if self._register == "government_legal"
            else _SMOOTHER_PROMPT_GENERAL
        )
        prompt = template.format(
            glossary_lines=_format_glossary_lines(glossary),
            numbered_candidate=_format_numbered(candidate_regions),
        )
        try:
            async with self._sem:
                resp = await self._llm.ainvoke([HumanMessage(content=prompt)])
        except Exception as exc:
            logger.warning("[smoother] %s call failed: %s", self._model, exc)
            return empty

        raw = resp.content if isinstance(resp.content, str) else "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in resp.content
        )
        cleaned = _strip_fences(raw)
        if not cleaned.startswith("["):
            match = re.search(r"\[.*\]", cleaned, re.DOTALL)
            if match:
                cleaned = match.group(0)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(
                "[smoother] %s returned non-JSON; head=%r", self._model, cleaned[:200]
            )
            return empty

        if not isinstance(parsed, list):
            return empty

        by_index: dict[int, SmootherItem] = {}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("index"))
            except (TypeError, ValueError):
                continue
            edits_raw = entry.get("edits") or []
            if not isinstance(edits_raw, list):
                continue
            edits: list[SmootherEdit] = []
            for e in edits_raw:
                if not isinstance(e, dict):
                    continue
                span = e.get("span")
                replacement = e.get("replacement")
                if (
                    not isinstance(span, list)
                    or len(span) != 2
                    or not isinstance(replacement, str)
                ):
                    continue
                try:
                    s, t = int(span[0]), int(span[1])
                except (TypeError, ValueError):
                    continue
                if s < 0 or t < s:
                    continue
                edits.append(SmootherEdit(span=(s, t), replacement=replacement))
            by_index[idx] = SmootherItem(index=idx, edits=edits)

        items: list[SmootherItem] = []
        changes = 0
        for i in range(len(candidate_regions)):
            item = by_index.get(i, SmootherItem(index=i))
            if item.edits:
                changes += len(item.edits)
            items.append(item)
        logger.info(
            "[smoother] %s reviewed %d regions, %d edit(s)",
            self._model, len(items), changes,
        )
        return SmootherResult(items=items, changes=changes)


async def smooth_in_batches(
    smoother: StyleSmoother,
    *,
    candidate_regions: list[str],
    glossary: dict[str, str] | None,
    batch_size: int,
) -> SmootherResult:
    """Run the smoother across batches of `batch_size` regions concurrently."""
    if batch_size <= 0 or len(candidate_regions) <= batch_size:
        return await smoother.smooth(
            candidate_regions=candidate_regions, glossary=glossary,
        )
    batches: list[tuple[int, int]] = []
    for start in range(0, len(candidate_regions), batch_size):
        batches.append((start, min(start + batch_size, len(candidate_regions))))
    results = await asyncio.gather(*[
        smoother.smooth(
            candidate_regions=candidate_regions[s:e], glossary=glossary,
        )
        for s, e in batches
    ])
    merged: list[SmootherItem] = []
    total_changes = 0
    for (s, _e), result in zip(batches, results):
        for item in result.items:
            merged.append(SmootherItem(index=s + item.index, edits=item.edits))
        total_changes += result.changes
    return SmootherResult(items=merged, changes=total_changes)
