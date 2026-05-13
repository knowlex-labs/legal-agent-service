"""Sarvam REST translate API client with retry logic."""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata

from legal_agent.config import get_settings

logger = logging.getLogger(__name__)

_SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"

SARVAM_LANG_CODES: dict[str, str] = {
    "english": "en-IN", "hindi": "hi-IN", "bengali": "bn-IN", "telugu": "te-IN",
    "marathi": "mr-IN", "tamil": "ta-IN", "urdu": "ur-IN", "gujarati": "gu-IN",
    "kannada": "kn-IN", "malayalam": "ml-IN", "odia": "or-IN", "punjabi": "pa-IN",
    "assamese": "as-IN", "maithili": "mai-IN", "santali": "sat-IN", "kashmiri": "ks-IN",
    "nepali": "ne-IN", "sindhi": "sd-IN", "dogri": "doi-IN", "konkani": "kok-IN",
    "manipuri": "mni-IN", "bodo": "brx-IN", "sanskrit": "sa-IN",
}

_SARVAM_DICT_WRAPPER_RE = re.compile(
    r"""\{\s*(?:['"]description['"]\s*:\s*['"][^'"]*['"]\s*,\s*)?"""
    r"""(?:['"]title['"]\s*:\s*['"][^'"]*['"]\s*,\s*)?"""
    r"""(?:['"]type['"]\s*:\s*['"][^'"]*['"]\s*,\s*)?"""
    r"""['"]content['"]\s*:\s*['"](?P<content>.*?)['"]\s*\}""",
    re.DOTALL,
)


def _unwrap_sarvam_dict_response(text: str) -> str:
    if "'content':" not in text and '"content":' not in text:
        return text
    match = _SARVAM_DICT_WRAPPER_RE.search(text)
    if not match:
        return text
    extracted = match.group("content")
    unescaped = extracted.replace("\\'", "'").replace('\\"', '"').replace("\\n", "\n")
    return text[: match.start()] + unescaped + text[match.end():]


def clean_output(text: str) -> str:
    """Strip reasoning traces / wrapper markers from API replies."""
    text = re.sub(
        r"<think(?:ing)?>.*?</think(?:ing)?>",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(
        r"<think(?:ing)?>.*?(?=(^#{1,6}\s|^---\s*$|\Z))",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE | re.MULTILINE,
    )
    text = re.sub(r"-{2,}\s*BEGIN\s+DOCUMENT\s*-{2,}", "", text)
    text = re.sub(r"-{2,}\s*END\s+DOCUMENT\s*-{2,}", "", text)
    return text.strip()


def clean_sarvam_translate_output(text: str) -> str:
    text = clean_output(text)
    text = text.replace("\x00", "").replace("​", "")
    text = _unwrap_sarvam_dict_response(text)
    text = re.sub(r"(?m)^\s*```[\w-]*\s*$", "", text)
    return unicodedata.normalize("NFC", text.strip())


def _retry_wait_seconds(status_code: int, attempt: int, base_delay: float, retry_after_header: str | None) -> float:
    if retry_after_header:
        try:
            return min(120.0, float(retry_after_header.strip()))
        except ValueError:
            pass
    mult = 2.5 if status_code == 429 else 2.0
    return min(60.0, base_delay * (mult**attempt))


async def call_sarvam_translate(
    text: str,
    source_code: str,
    target_code: str,
    api_key: str,
    model: str | None = None,
) -> str:
    if not text or not text.strip():
        return text
    import httpx

    settings = get_settings()
    tm = model or settings.sarvam_translate_model
    max_retries = max(0, settings.sarvam_translate_max_retries)
    base_delay = max(0.1, settings.sarvam_translate_retry_base_seconds)

    body: dict = {
        "input": text,
        "source_language_code": source_code,
        "target_language_code": target_code,
        "model": tm,
        "mode": "formal",
        "enable_preprocessing": True,
        "numerals_format": "international",
    }

    _NETWORK_ERRORS = (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError, httpx.NetworkError)

    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(max_retries + 1):
            try:
                resp = await client.post(
                    _SARVAM_TRANSLATE_URL,
                    headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
                    json=body,
                )
            except _NETWORK_ERRORS as exc:
                if attempt < max_retries:
                    wait = _retry_wait_seconds(0, attempt, base_delay, None)
                    logger.warning(
                        "[sarvam-translate] network error (%s) -- sleeping %.1fs then retry %s/%s (chars=%d)",
                        type(exc).__name__, wait, attempt + 1, max_retries, len(text),
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.error("[sarvam-translate] network error after %s retries: %s", max_retries, exc)
                raise
            if resp.is_success:
                translated = resp.json()["translated_text"]
                return _unwrap_sarvam_dict_response(translated).replace("\x00", "")

            retryable = resp.status_code in (429, 502, 503)
            if retryable and attempt < max_retries:
                wait = _retry_wait_seconds(
                    resp.status_code,
                    attempt,
                    base_delay,
                    resp.headers.get("Retry-After"),
                )
                logger.warning(
                    "[sarvam-translate] HTTP %s -- sleeping %.1fs then retry %s/%s (chars=%d)",
                    resp.status_code,
                    wait,
                    attempt + 1,
                    max_retries,
                    len(text),
                )
                await asyncio.sleep(wait)
                continue

            logger.error(
                "[sarvam-translate] %s (chars=%d): %s | input_preview=%r",
                resp.status_code,
                len(text),
                resp.text[:500],
                text[:200],
            )
            resp.raise_for_status()
