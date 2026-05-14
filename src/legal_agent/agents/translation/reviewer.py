"""Stage C — source-grounded translation reviewer.

Receives SOURCE alongside CANDIDATE and the binding glossary; rewrites drift,
hallucinations, glossary violations, and verbatim-token loss against the
source. Style polish is the smoother's job, not this one.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
from dataclasses import dataclass

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from legal_agent.agents.translation._llm_common import (
    extract_json_blob,
    format_glossary_lines,
    format_numbered,
    infer_provider,
    message_content_to_text,
)
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)


_REVIEWER_PROMPT_LEGAL = """\
You are a bilingual reviewer for formal Indian administrative and legal
document translations. Your single job is to catch drift between SOURCE and
CANDIDATE_TRANSLATION and produce a corrected, faithful translation.

A translation is WRONG if any of the following hold:
  (a) It refers to a person, scheme, position, amount, date, or institution
      that does not appear in the corresponding SOURCE segment.
  (b) It substitutes a related-but-different concept (e.g. SOURCE says "Guest
      Faculty / अतिथि विद्वान" but CANDIDATE says "merit scholarship recipients";
      SOURCE says "honorarium / मानदेय" but CANDIDATE says "stipend" or
      "scholarship amount").
  (c) It omits, summarizes, or paraphrases away substantive clauses.
  (d) It violates the GLOSSARY by using a different target for a listed term.
  (e) It alters or drops any number, date, identifier, section reference, name,
      email, URL, or `[__NNNN__]` placeholder.
  (f) Its register is informal/marketing when the source is administrative.
  (g) Its register reads as a textbook / Sanskritized government-circular tone
      when the source is modern legal/administrative prose. Prefer professional
      modern Hindi that an Indian lawyer would actually write — avoid forced
      तत्सम where a common-register word reads more naturally. Common English
      legal terms (plaintiff, defendant, tort, prima facie, mens rea,
      consideration, equity, estoppel, in personam, in rem) may be used inline
      with a Hindi gloss on first occurrence only.

For each numbered region:
  - If the translation is faithful and glossary-compliant, return status "ok"
    and copy the candidate verbatim as `corrected`.
  - Otherwise, return status "fixed" and a corrected translation that resolves
    ALL violations against SOURCE. Do not flag without fixing.

Return ONLY a JSON array of objects, same length and order as the input:
  [{{"index": <int>, "status": "ok"|"fixed", "corrected": "<string>"}}, ...]
No prose, no fences.

DOCUMENT_SUBJECT: {subject}
SOURCE language: {source_language}
TARGET language: {target_language}

GLOSSARY (binding source → target):
{glossary_lines}

SOURCE ({source_language}) — ground truth:
{numbered_source}

CANDIDATE_TRANSLATION ({target_language}):
{numbered_candidate}
"""


_REVIEWER_PROMPT_GENERAL = """\
You are a bilingual reviewer for academic, journalistic, business, and
general-prose translations. Your single job is to catch drift between SOURCE
and CANDIDATE_TRANSLATION and produce a corrected, faithful translation.

A translation is WRONG if any of the following hold:
  (a) It refers to a person, place, amount, date, citation, or institution
      that does not appear in the corresponding SOURCE segment.
  (b) It substitutes a related-but-different concept or paraphrases meaning
      away (e.g. SOURCE "lending" rendered as "borrowing"; SOURCE "Chief
      Justice" rendered as "Chief Minister").
  (c) It omits, summarizes, or skips substantive clauses or sentences.
  (d) It violates the GLOSSARY by using a different target for a listed term.
  (e) It alters or drops any number, date, footnote marker, case citation,
      name, email, URL, or `[__NNNN__]` placeholder.
  (f) It leaves untranslated English fragments inline where a natural target
      rendering exists (e.g. "U.S" inside a Hindi sentence should be
      "अमेरिकी" / "संयुक्त राज्य अमेरिका"). Verbatim case names and citations
      (e.g. "Printz v. United States", "117 S. Ct. 2365") are exempt.
  (g) Its register reads as a textbook or government circular when the source
      is modern academic / journalistic prose. Prefer professional modern
      Hindi accessible to Indian researchers — avoid over-Sanskritized
      constructions where a natural-register word communicates the same
      meaning. Common English academic / legal terms (plaintiff, defendant,
      tort, prima facie, mens rea, consideration, equity, estoppel, in
      personam, in rem) may be used inline with a Hindi gloss on first
      occurrence only.

For each numbered region:
  - If the translation is faithful and glossary-compliant, return status "ok"
    and copy the candidate verbatim as `corrected`.
  - Otherwise, return status "fixed" and a corrected translation that resolves
    ALL violations against SOURCE. Do not flag without fixing.

Return ONLY a JSON array of objects, same length and order as the input:
  [{{"index": <int>, "status": "ok"|"fixed", "corrected": "<string>"}}, ...]
No prose, no fences.

DOCUMENT_SUBJECT: {subject}
SOURCE language: {source_language}
TARGET language: {target_language}

GLOSSARY (binding source → target):
{glossary_lines}

SOURCE ({source_language}) — ground truth:
{numbered_source}

CANDIDATE_TRANSLATION ({target_language}):
{numbered_candidate}
"""


@dataclass
class ReviewItem:
    index: int
    status: str  # "ok" | "fixed"
    corrected: str


@dataclass
class ReviewResult:
    items: list[ReviewItem]
    fixed_count: int


def _passthrough_result(candidates: list[str]) -> "ReviewResult":
    return ReviewResult(
        items=[ReviewItem(index=i, status="ok", corrected=c) for i, c in enumerate(candidates)],
        fixed_count=0,
    )


class Reviewer:
    """Per-chunk source-grounded reviewer. Reuses one LangChain client."""

    def __init__(
        self,
        source_language: str,
        target_language: str,
        *,
        register: str = "government_legal",
    ) -> None:
        settings = get_settings()
        self._enabled = settings.translation_reviewer_enabled
        self._model = settings.translation_reviewer_model
        self._source_language = source_language
        self._target_language = target_language
        self._register = register if register in {"government_legal", "general"} else "government_legal"
        self._sem = asyncio.Semaphore(
            max(1, settings.translation_reviewer_max_concurrency)
        )
        self._llm = (
            init_chat_model(self._model, model_provider=infer_provider(self._model))
            if self._enabled
            else None
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def review(
        self,
        *,
        source_regions: list[str],
        candidate_regions: list[str],
        subject: str,
        glossary: dict[str, str] | None,
    ) -> ReviewResult:
        """Return corrected translations. Any failure short-circuits to a
        pass-through so the pipeline never blocks on reviewer issues.
        """
        if not self._enabled or not source_regions or self._llm is None:
            return _passthrough_result(candidate_regions)
        if len(source_regions) != len(candidate_regions):
            logger.warning(
                "[reviewer] length mismatch source=%d candidate=%d; skipping",
                len(source_regions), len(candidate_regions),
            )
            return _passthrough_result(candidate_regions)

        template = (
            _REVIEWER_PROMPT_GENERAL if self._register == "general"
            else _REVIEWER_PROMPT_LEGAL
        )
        prompt = template.format(
            subject=subject or "(unspecified)",
            source_language=self._source_language,
            target_language=self._target_language,
            glossary_lines=format_glossary_lines(glossary),
            numbered_source=format_numbered(source_regions),
            numbered_candidate=format_numbered(candidate_regions),
        )
        try:
            async with self._sem:
                resp = await self._llm.ainvoke([HumanMessage(content=prompt)])
        except Exception as exc:
            logger.warning("[reviewer] %s call failed: %s", self._model, exc)
            return _passthrough_result(candidate_regions)

        cleaned = extract_json_blob(message_content_to_text(resp.content), "[")
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning(
                "[reviewer] %s returned non-JSON; head=%r", self._model, cleaned[:200]
            )
            return _passthrough_result(candidate_regions)
        if not isinstance(parsed, list):
            return _passthrough_result(candidate_regions)

        by_index: dict[int, ReviewItem] = {}
        for entry in parsed:
            if not isinstance(entry, dict):
                continue
            try:
                idx = int(entry.get("index"))
            except (TypeError, ValueError):
                continue
            corrected = entry.get("corrected")
            if not isinstance(corrected, str):
                continue
            status = "fixed" if entry.get("status") == "fixed" else "ok"
            by_index[idx] = ReviewItem(index=idx, status=status, corrected=corrected)

        items: list[ReviewItem] = []
        fixed_count = 0
        for i, candidate in enumerate(candidate_regions):
            item = by_index.get(i, ReviewItem(index=i, status="ok", corrected=candidate))
            if item.status == "fixed" and item.corrected != candidate:
                fixed_count += 1
            items.append(item)
        logger.info(
            "[reviewer] %s reviewed %d regions, %d corrected",
            self._model, len(items), fixed_count,
        )
        return ReviewResult(items=items, fixed_count=fixed_count)


def reviewer_changed_spans(
    candidate: str, corrected: str
) -> list[tuple[int, int]]:
    """Spans in `corrected` (half-open) where content differs from `candidate`.

    Used by the smoother-merge layer to drop smoother edits that overlap a
    reviewer correction. Pure deletions report as zero-width spans so
    adjacent smoother edits at the join point are also dropped.
    """
    if candidate == corrected:
        return []
    sm = difflib.SequenceMatcher(a=candidate, b=corrected, autojunk=False)
    spans: list[tuple[int, int]] = []
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        spans.append((j1, j2) if j1 != j2 else (j1, j1))
    return spans


async def review_in_batches(
    reviewer: Reviewer,
    *,
    source_regions: list[str],
    candidate_regions: list[str],
    subject: str,
    glossary: dict[str, str] | None,
    batch_size: int,
) -> ReviewResult:
    """Chunk the review across batches of `batch_size` regions and merge results.

    Mirrors the translator's chunking so reviewer scope = translator scope.
    """
    if batch_size <= 0 or len(source_regions) <= batch_size:
        return await reviewer.review(
            source_regions=source_regions,
            candidate_regions=candidate_regions,
            subject=subject,
            glossary=glossary,
        )

    batches: list[tuple[int, int]] = []
    for start in range(0, len(source_regions), batch_size):
        batches.append((start, min(start + batch_size, len(source_regions))))

    results = await asyncio.gather(*[
        reviewer.review(
            source_regions=source_regions[s:e],
            candidate_regions=candidate_regions[s:e],
            subject=subject,
            glossary=glossary,
        )
        for s, e in batches
    ])

    merged: list[ReviewItem] = []
    fixed_total = 0
    for (s, _e), result in zip(batches, results):
        for item in result.items:
            merged.append(
                ReviewItem(index=s + item.index, status=item.status, corrected=item.corrected)
            )
        fixed_total += result.fixed_count

    return ReviewResult(items=merged, fixed_count=fixed_total)
