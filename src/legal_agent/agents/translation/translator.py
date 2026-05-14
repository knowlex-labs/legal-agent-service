"""Translation backend.

Single `Translator` class, dispatched by `settings.translation_model`:
- "sarvam" → Sarvam REST formal translate (cheap, Indic-specialist).
- Any other value → LLM via `langchain.chat_models.init_chat_model`
  (gemini-*, claude-*, gpt-*/o*). On Hindi target, the CBIC/Rajbhasha legal
  prompt is applied unconditionally — its rules are all negative ("use फर्जी,
  not नकली") so they don't degrade non-legal Hindi translations.

Batching: each region is `freeze`d to scope its sentinels, then either packed
into one Sarvam call separated by `[__SEP_NNNN__]` markers, or sent as a JSON
array to the LLM. On either backend a sentinel-parity check after translate
falls back to per-region calls if a `[__NNNN__]` token was dropped.

Three-stage pipeline: when an LLM backend is configured AND a `DocumentContext`
is attached via `set_document_context`, batches are processed sequentially with
a context window (preceding output + look-ahead source) and a strict legal /
administrative system prompt. This is the drift-resistant path used for formal
documents; Sarvam path keeps the original behaviour.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from legal_agent.agents.translation.glossary import (
    DocState,
    GlossaryEntry,
    _SENTINEL_RE,
    freeze,
    get_glossary,
    localize_units,
    restore,
    strip_pua,
)
from legal_agent.agents.translation.sarvam_translate import (
    call_sarvam_translate,
    clean_sarvam_translate_output,
)
from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_SEP_TEMPLATE = "\n\n[__SEP_{:04d}__]\n\n"
_SEP_RE = re.compile(r"\n*\[__SEP_\d{4}__\]\n*")

# Negative-only rules for Indian government / legal Hindi translation, used by
# the legacy LLM fallback path when no DocumentContext is attached (Stage A
# disabled or failed). Each line specifies a wrong choice the LLM tends to make
# on CBIC / Rajbhasha notices plus the correct alternative. The register-aware
# path uses the full _GENERAL_PROMPT_TEMPLATE / _LEGAL_ADMIN_PROMPT_TEMPLATE
# instead.
_LEGAL_HI_PROMPT_RULES = (
    "Target register: formal Devanagari Hindi (CBIC/Rajbhasha conventions for "
    "Indian government/legal/tax notices). Hard rules:\n"
    "- 'fake' in fraud sense → फर्जी (NEVER नकली, which means counterfeit object).\n"
    "- 'tangible material' / 'tangible evidence' → मूर्त साक्ष्य (NEVER मूर्त सामग्री).\n"
    "- 'financial trail' / 'transaction trail' → लेन-देन की कड़ी (NEVER पगडंडी, footpath).\n"
    "- 'syndicate' (fraud context) → रैकेट or गिरोह.\n"
    "- 'counsel' → अधिवक्ता (statutory term).\n"
    "- Address abbreviations: use full forms — प्लॉट संख्या (not प्लॉट नं.), "
    "मकान संख्या, बैंक खाता संख्या.\n"
    "- Transliterate ID labels, keep the alphanumeric ID verbatim: "
    "'DIN-XXXX' → 'डीआईएन-XXXX', 'F.NO.-XXXX' → 'फा.सं.-XXXX'.\n"
    "- Use 'सेवा में,' for the 'To' salutation block in formal notices.\n"
    "- Preserve emails, URLs, phone numbers, account numbers, dd/mm/yyyy dates, "
    "currency figures exactly."
)


# Strict drift-resistant prompt for Indian administrative / legal / government
# sources. Used by the three-stage pipeline (Stage B) when DocumentContext
# register is "government_legal". Direction-agnostic; forces faithfulness
# against the source and binds the per-document glossary from Stage A.
_LEGAL_ADMIN_PROMPT_TEMPLATE = """\
You are a professional translator of formal Indian administrative, legal, and
governmental documents. You translate {source_language} into {target_language}.

Your contract:
1. FAITHFULNESS ABOVE FLUENCY. Translate exactly what the source says. Do not
   summarize, expand, infer, "improve", or fill in. If the source is ambiguous,
   render it ambiguously. If a clause is incomplete, leave it incomplete.
2. NO TOPIC SUBSTITUTION. Never replace a concept with a different but related
   one (e.g. do not turn "Guest Faculty" into "scholarship recipients", do not
   turn "honorarium" into "stipend" or "scholarship"). If you are uncertain of
   a term, transliterate it and keep the original in parentheses on first use.
3. NO HALLUCINATION. Do not introduce people, amounts, dates, schemes, or
   institutions that are not present in the source segment you are translating.
4. PRESERVE VERBATIM:
   - Personal names, place names, institution names, scheme names.
   - File numbers, DIN/PAN/GST/account/registration identifiers.
   - Section/rule/article numbers (e.g. "Section 138", "Rule 6(2)").
   - Numerals, dates (any format), times, currency figures.
   - Email addresses, URLs, phone numbers.
   - The placeholder tokens `[__NNNN__]` and `[__SEP_NNNN__]` — copy them
     character-for-character into the output, in the same positions.
5. GLOSSARY IS BINDING. The GLOSSARY block below lists source→target term
   pairs extracted from this specific document. You MUST use the listed target
   for every occurrence; deviations are errors.
6. REGISTER. Match the source register. For Hindi government / legal sources,
   output formal English administrative prose ("hereby", "the undersigned",
   "with effect from"), not casual or marketing language. For English → Hindi,
   follow CBIC / Rajbhasha conventions (formal Devanagari, no transliteration
   of common nouns).
7. CONTEXT BLOCKS ARE READ-ONLY. CONTEXT_PREVIOUS_OUTPUT and
   CONTEXT_NEXT_SOURCE are for continuity only. Do NOT include them in your
   output. Translate only what appears under CURRENT_CHUNK.
8. OUTPUT SHAPE. Return ONLY a JSON array of translated strings, one per input
   region, in the same order. No prose, no markdown fences, no commentary.

DOCUMENT_SUBJECT: {subject}

GLOSSARY:
{glossary_lines}

CONTEXT_PREVIOUS_OUTPUT (already translated — do not retranslate):
{prev_output}

CONTEXT_NEXT_SOURCE (upcoming source — do not translate, for disambiguation):
{next_source}

CURRENT_CHUNK (translate these, return JSON array of same length):
{current_chunk_json}
"""


# Same shape as _LEGAL_ADMIN_PROMPT_TEMPLATE but for "general" register —
# academic, journalistic, resume, business, fiction. Keeps the faithfulness
# contract; drops the Indian-administrative framing and CBIC/Rajbhasha clauses.
_GENERAL_PROMPT_TEMPLATE = """\
You are a professional translator. You translate {source_language} into
{target_language} for academic, journalistic, business, or general prose.

Your contract:
1. FAITHFULNESS ABOVE FLUENCY. Translate exactly what the source says. Do not
   summarize, expand, infer, "improve", or fill in. If the source is ambiguous,
   render it ambiguously.
2. NO TOPIC SUBSTITUTION. Never replace a concept with a different but related
   one. If you are uncertain of a term, transliterate it and keep the original
   in parentheses on first use.
3. NO HALLUCINATION. Do not introduce people, amounts, dates, places, or
   institutions that are not present in the source segment you are translating.
4. PRESERVE VERBATIM:
   - Personal names, place names, institution names.
   - Case names and citations (e.g. "Printz v. United States", "117 S. Ct. 2365").
   - Section / article / rule numbers, footnote numbers.
   - Numerals, dates (any format), times, currency figures.
   - Email addresses, URLs, phone numbers.
   - The placeholder tokens `[__NNNN__]` and `[__SEP_NNNN__]` — copy them
     character-for-character into the output, in the same positions.
5. GLOSSARY IS BINDING. The GLOSSARY block below lists source→target term
   pairs extracted from this specific document. You MUST use the listed target
   for every occurrence; deviations are errors.
6. REGISTER. Match the source register. For academic / journal prose, produce
   natural formal target-language prose — neither bureaucratic nor casual.
   Translate every translatable word; do not leave English fragments inline
   (e.g. render "U.S." as "अमेरिकी" / "संयुक्त राज्य अमेरिका" in Hindi, not "U.S").
7. CONTEXT BLOCKS ARE READ-ONLY. CONTEXT_PREVIOUS_OUTPUT and
   CONTEXT_NEXT_SOURCE are for continuity only. Do NOT include them in your
   output. Translate only what appears under CURRENT_CHUNK.
8. OUTPUT SHAPE. Return ONLY a JSON array of translated strings, one per input
   region, in the same order. No prose, no markdown fences, no commentary.

DOCUMENT_SUBJECT: {subject}

GLOSSARY:
{glossary_lines}

CONTEXT_PREVIOUS_OUTPUT (already translated — do not retranslate):
{prev_output}

CONTEXT_NEXT_SOURCE (upcoming source — do not translate, for disambiguation):
{next_source}

CURRENT_CHUNK (translate these, return JSON array of same length):
{current_chunk_json}
"""


@dataclass
class DocumentContext:
    """Per-document state for the three-stage pipeline.

    Attached to a `Translator` instance via `set_document_context` before
    `translate_batch` runs. When present and the backend is an LLM, the
    translator switches to sequential context-windowed mode using either
    `_LEGAL_ADMIN_PROMPT_TEMPLATE` (register="government_legal") or
    `_GENERAL_PROMPT_TEMPLATE` (register="general").
    """

    subject: str = ""
    source_language: str = ""
    target_language: str = ""
    # "government_legal" → CBIC/Rajbhasha prompt; "general" → academic/business.
    # Defaults to government_legal for backward compatibility with callers that
    # don't set it (pre-register code paths).
    register: str = "government_legal"
    # Source-language surface forms → target-language renderings. Used both to
    # build the GLOSSARY block in the translator prompt and (verbatim) by the
    # reviewer downstream.
    glossary: dict[str, str] = field(default_factory=dict)

    def glossary_lines(self) -> str:
        if not self.glossary:
            return "(none)"
        return "\n".join(f"- {src} → {tgt}" for src, tgt in self.glossary.items())


def _infer_provider(model: str) -> str:
    """Map a model-name prefix to a langchain provider id."""
    m = model.lower().removeprefix("models/")
    if m.startswith("gemini"):
        return "google-genai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gpt") or m.startswith("o"):
        return "openai"
    raise ValueError(f"Unsupported translation model: {model!r}")


def _pack(frozen: list[str], max_chars: int) -> list[list[int]]:
    """Group indices of `frozen` into batches whose joined length ≤ max_chars."""
    batches: list[list[int]] = []
    current: list[int] = []
    current_len = 0
    sep_len = len(_SEP_TEMPLATE.format(0))
    for i, piece in enumerate(frozen):
        added = len(piece) + (sep_len if current else 0)
        if current and current_len + added > max_chars:
            batches.append(current)
            current, current_len = [i], len(piece)
        else:
            current.append(i)
            current_len += added
    if current:
        batches.append(current)
    return batches


def _build_sarvam_payload(indices: list[int], frozen: list[str]) -> str:
    parts: list[str] = []
    for slot, idx in enumerate(indices):
        if slot > 0:
            parts.append(_SEP_TEMPLATE.format(slot))
        parts.append(frozen[idx])
    return "".join(parts)


def _split_sarvam_response(translated: str, expected_count: int) -> list[str] | None:
    pieces = _SEP_RE.split(translated)
    if len(pieces) != expected_count:
        return None
    return [p.strip() for p in pieces]


class Translator:
    """Single translation backend selected by `settings.translation_model`."""

    def __init__(self, target_lang: str, model: str | None = None) -> None:
        settings = get_settings()
        self._model = (model or settings.translation_llm_model).strip()
        self._target_lang = target_lang.lower()
        self._is_devanagari_target = self._target_lang in {
            "hindi", "marathi", "nepali", "sanskrit",
        }
        self._is_hindi_target = self._target_lang == "hindi"
        self._max_chars = settings.translation_batch_max_chars
        self._chunk_max_chars = settings.translation_chunk_max_chars
        self._context_window = max(0, settings.translation_context_window_regions)
        self._sem = asyncio.Semaphore(max(1, settings.sarvam_translate_max_concurrency))
        self._glossary = get_glossary()
        self._state = DocState()
        self._state_lock = asyncio.Lock()
        self._doc_context: DocumentContext | None = None

        if self._model.lower() == "sarvam":
            if not settings.sarvam_api_key:
                raise RuntimeError("SARVAM_API_KEY not configured")
            self._sarvam_api_key = settings.sarvam_api_key
            self._sarvam_model = settings.sarvam_translate_model
            self._llm = None
        else:
            self._llm = init_chat_model(
                self._model, model_provider=_infer_provider(self._model)
            )

    @property
    def backend(self) -> str:
        return "sarvam" if self._llm is None else self._model

    def set_document_context(
        self,
        context: DocumentContext,
        *,
        dynamic_entries: list[GlossaryEntry] | None = None,
    ) -> None:
        """Attach per-document state for the three-stage pipeline.

        `dynamic_entries` overlay on the static glossary so per-document terms
        flow through the existing freeze()/restore() sentinel mechanism. Only
        meaningful when the backend is an LLM; on Sarvam the context is set
        but the windowed prompt path is skipped (Sarvam has no system prompt).
        """
        self._doc_context = context
        if dynamic_entries:
            self._glossary = self._glossary.with_dynamic_entries(dynamic_entries)

    @property
    def uses_context_pipeline(self) -> bool:
        return self._llm is not None and self._doc_context is not None

    async def translate_batch(
        self, texts: list[str], source_code: str, target_code: str
    ) -> list[str]:
        if not texts:
            return []

        prepared: list[str] = []
        for t in texts:
            s = strip_pua(t.strip()) if t else ""
            if s and self._is_devanagari_target:
                s = localize_units(s)
            prepared.append(s)

        frozen: list[str] = []
        sentinel_maps: list[dict[str, str]] = []
        async with self._state_lock:
            for s in prepared:
                if not s:
                    frozen.append("")
                    sentinel_maps.append({})
                    continue
                f, sm = freeze(s, self._state, self._glossary)
                frozen.append(f)
                sentinel_maps.append(sm)

        translatable = [i for i, p in enumerate(prepared) if p]
        use_context = self.uses_context_pipeline
        max_chars = self._chunk_max_chars if use_context else self._max_chars
        batches = _pack([frozen[i] for i in translatable], max_chars)
        batches = [[translatable[j] for j in b] for b in batches]

        results: dict[int, str] = {}
        if use_context:
            # Sequential so each batch's prev_output reflects prior batches.
            for bi, batch in enumerate(batches):
                prev_output = self._collect_prev_output(batches[:bi], results)
                next_source = self._collect_next_source(batches[bi + 1 :], prepared)
                await self._process_batch(
                    batch, frozen, sentinel_maps, results, source_code, target_code,
                    prepared=prepared,
                    prev_output=prev_output,
                    next_source=next_source,
                )
        else:
            await asyncio.gather(*(
                self._process_batch(b, frozen, sentinel_maps, results, source_code, target_code)
                for b in batches
            ))

        out: list[str] = []
        for i, original in enumerate(texts):
            out.append(results.get(i, original) if prepared[i] else original)
        logger.info(
            "[translator] %s: %d regions → %d batched call(s)%s",
            self.backend, len(texts), len(batches),
            " (context-windowed)" if use_context else "",
        )
        return out

    def _collect_prev_output(
        self, prior_batches: list[list[int]], results: dict[int, str]
    ) -> list[str]:
        """Last N already-translated regions, in document order."""
        if self._context_window <= 0:
            return []
        flat: list[int] = []
        for b in prior_batches:
            flat.extend(b)
        tail = flat[-self._context_window :]
        return [results[i] for i in tail if i in results]

    def _collect_next_source(
        self, future_batches: list[list[int]], prepared: list[str]
    ) -> list[str]:
        """First N upcoming source regions for disambiguation only."""
        if self._context_window <= 0:
            return []
        out: list[str] = []
        for b in future_batches:
            for i in b:
                out.append(prepared[i])
                if len(out) >= self._context_window:
                    return out
        return out

    async def _process_batch(
        self,
        indices: list[int],
        frozen: list[str],
        sentinel_maps: list[dict[str, str]],
        results: dict[int, str],
        source_code: str,
        target_code: str,
        *,
        prepared: list[str] | None = None,
        prev_output: list[str] | None = None,
        next_source: list[str] | None = None,
    ) -> None:
        inputs = [frozen[i] for i in indices]
        outputs = await self._call_many(
            inputs, source_code, target_code,
            prev_output=prev_output, next_source=next_source,
        )
        if outputs is None or len(outputs) != len(inputs):
            logger.warning(
                "[translator] %s batch mismatch (sent=%d got=%s); per-region fallback",
                self.backend, len(inputs), None if outputs is None else len(outputs),
            )
            outputs = []
            for piece in inputs:
                one = await self._call_many(
                    [piece], source_code, target_code,
                    prev_output=prev_output, next_source=next_source,
                )
                outputs.append(one[0] if one else piece)

        for piece, idx in zip(outputs, indices):
            # Sentinel parity check: if the backend silently dropped a [__NNNN__]
            # token, fall back to a single-region call for this index so the
            # protected term isn't lost into mistranslated text.
            expected = set(sentinel_maps[idx].keys())
            if expected - set(_SENTINEL_RE.findall(piece)):
                logger.warning(
                    "[translator] %s dropped sentinels in region %d; retrying solo",
                    self.backend, idx,
                )
                solo = await self._call_many(
                    [frozen[idx]], source_code, target_code,
                    prev_output=prev_output, next_source=next_source,
                )
                piece = (solo[0] if solo else frozen[idx]) or frozen[idx]
            results[idx] = restore(piece or frozen[idx], sentinel_maps[idx])

    async def _call_many(
        self,
        inputs: list[str],
        source_code: str,
        target_code: str,
        *,
        prev_output: list[str] | None = None,
        next_source: list[str] | None = None,
    ) -> list[str] | None:
        """Translate N frozen inputs, return N outputs (or None on failure)."""
        if self._llm is None:
            return await self._call_sarvam(inputs, source_code, target_code)
        return await self._call_llm(
            inputs, prev_output=prev_output, next_source=next_source
        )

    async def _call_sarvam(
        self, inputs: list[str], source_code: str, target_code: str
    ) -> list[str] | None:
        if len(inputs) == 1:
            t = await self._sarvam_one(inputs[0], source_code, target_code)
            return [t]
        payload = _build_sarvam_payload(list(range(len(inputs))), inputs)
        raw = await self._sarvam_one(payload, source_code, target_code)
        return _split_sarvam_response(raw, len(inputs))

    async def _sarvam_one(self, text: str, source_code: str, target_code: str) -> str:
        async with self._sem:
            try:
                raw = await call_sarvam_translate(
                    text, source_code, target_code,
                    self._sarvam_api_key, self._sarvam_model,
                )
            except Exception as exc:
                msg = str(exc)
                if "must be different" in msg or "400" in msg:
                    logger.info(
                        "[translator] sarvam declined (same script as target): %r",
                        text[:60],
                    )
                    return text
                raise
        return clean_sarvam_translate_output(raw) or text

    def _build_context_prompt(
        self,
        inputs: list[str],
        prev_output: list[str] | None,
        next_source: list[str] | None,
    ) -> str:
        ctx = self._doc_context
        assert ctx is not None  # guarded by caller
        prev = "\n".join(f"- {s}" for s in (prev_output or [])) or "(none)"
        nxt = "\n".join(f"- {s}" for s in (next_source or [])) or "(none)"
        template = (
            _GENERAL_PROMPT_TEMPLATE
            if ctx.register == "general"
            else _LEGAL_ADMIN_PROMPT_TEMPLATE
        )
        return template.format(
            source_language=ctx.source_language or "the source language",
            target_language=ctx.target_language or self._target_lang,
            subject=ctx.subject or "(unspecified)",
            glossary_lines=ctx.glossary_lines(),
            prev_output=prev,
            next_source=nxt,
            current_chunk_json=json.dumps(inputs, ensure_ascii=False),
        )

    async def _call_llm(
        self,
        inputs: list[str],
        *,
        prev_output: list[str] | None = None,
        next_source: list[str] | None = None,
    ) -> list[str] | None:
        if self._doc_context is not None:
            prompt = self._build_context_prompt(inputs, prev_output, next_source)
        else:
            rules = "\n\n" + _LEGAL_HI_PROMPT_RULES if self._is_hindi_target else ""
            prompt = (
                f"Translate every string in the JSON array below into {self._target_lang}. "
                "Preserve `[__NNNN__]` and `[__SEP_NNNN__]` tokens verbatim. "
                "Preserve numbers, dates, and identifiers exactly. "
                "Return ONLY a JSON array of the same length, each item a translated string."
                f"{rules}"
                f"\n\nINPUT:\n{json.dumps(inputs, ensure_ascii=False)}"
            )
        async with self._sem:
            try:
                resp = await self._llm.ainvoke([HumanMessage(content=prompt)])
            except Exception as exc:
                logger.error("[translator] %s call failed: %s", self._model, exc)
                return None

        raw = resp.content if isinstance(resp.content, str) else "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in resp.content
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[translator] %s returned non-JSON; head=%r", self._model, raw[:200])
            return None
        if not isinstance(parsed, list):
            return None
        return [str(x) if x is not None else "" for x in parsed]
