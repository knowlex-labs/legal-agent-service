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
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage

from legal_agent.agents.translation.glossary import (
    DocState,
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

# Negative-only rules: every line specifies a wrong choice the LLM tends to make
# on Hindi government/legal documents, plus the correct alternative. Sourced from
# CBIC circulars, Rajbhasha saral-shabdavali, and Legislative Dept Legal Glossary.
# Safe to apply to all Hindi translations — does nothing on docs that don't
# trigger these terms (resumes, contracts, etc.).
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
        self._sem = asyncio.Semaphore(max(1, settings.sarvam_translate_max_concurrency))
        self._glossary = get_glossary()
        self._state = DocState()
        self._state_lock = asyncio.Lock()

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
        batches = _pack([frozen[i] for i in translatable], self._max_chars)
        batches = [[translatable[j] for j in b] for b in batches]

        results: dict[int, str] = {}
        await asyncio.gather(*(
            self._process_batch(b, frozen, sentinel_maps, results, source_code, target_code)
            for b in batches
        ))

        out: list[str] = []
        for i, original in enumerate(texts):
            out.append(results.get(i, original) if prepared[i] else original)
        logger.info(
            "[translator] %s: %d regions → %d batched call(s)",
            self.backend, len(texts), len(batches),
        )
        return out

    async def _process_batch(
        self,
        indices: list[int],
        frozen: list[str],
        sentinel_maps: list[dict[str, str]],
        results: dict[int, str],
        source_code: str,
        target_code: str,
    ) -> None:
        inputs = [frozen[i] for i in indices]
        outputs = await self._call_many(inputs, source_code, target_code)
        if outputs is None or len(outputs) != len(inputs):
            logger.warning(
                "[translator] %s batch mismatch (sent=%d got=%s); per-region fallback",
                self.backend, len(inputs), None if outputs is None else len(outputs),
            )
            outputs = []
            for piece in inputs:
                one = await self._call_many([piece], source_code, target_code)
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
                solo = await self._call_many([frozen[idx]], source_code, target_code)
                piece = (solo[0] if solo else frozen[idx]) or frozen[idx]
            results[idx] = restore(piece or frozen[idx], sentinel_maps[idx])

    async def _call_many(
        self, inputs: list[str], source_code: str, target_code: str
    ) -> list[str] | None:
        """Translate N frozen inputs, return N outputs (or None on failure)."""
        if self._llm is None:
            return await self._call_sarvam(inputs, source_code, target_code)
        return await self._call_llm(inputs)

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

    async def _call_llm(self, inputs: list[str]) -> list[str] | None:
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
