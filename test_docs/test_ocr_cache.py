"""Verify the S3-backed OCR cache.

Runs ocr_pdf twice on the same file. First call should MISS (full OCR latency,
then an S3 upload). Second call should HIT (S3 download only, seconds).

Usage:
    python test_docs/test_ocr_cache.py path/to/file.pdf
    python test_docs/test_ocr_cache.py path/to/file.pdf --format plain
    python test_docs/test_ocr_cache.py path/to/file.pdf --provider sarvam

Requires: GEMINI_API_KEY (or SARVAM_API_KEY) + S3_* credentials in .env.
Logs of interest: "[ocr-cache] miss", "[ocr-cache] stored", "[ocr-cache] hit".
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from legal_agent.utils.ocr import ocr_pdf  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify OCR cache hit/miss behaviour.")
    parser.add_argument("pdf", type=Path, help="PDF to OCR")
    parser.add_argument("--format", choices=["markdown", "plain"], default="markdown")
    parser.add_argument("--provider", choices=["gemini", "sarvam"], default=None)
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"error: {args.pdf} not found", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

    pdf_bytes = args.pdf.read_bytes()
    kwargs = {"output_format": args.format}
    if args.provider:
        kwargs["provider"] = args.provider

    print(f"\n=== Call 1 (expect MISS → OCR → store) ===")
    t0 = time.perf_counter()
    text1 = ocr_pdf(pdf_bytes, **kwargs)
    t1 = time.perf_counter() - t0
    print(f"→ {len(text1):,} chars in {t1:.1f}s")

    print(f"\n=== Call 2 (expect HIT → download only) ===")
    t0 = time.perf_counter()
    text2 = ocr_pdf(pdf_bytes, **kwargs)
    t2 = time.perf_counter() - t0
    print(f"→ {len(text2):,} chars in {t2:.1f}s")

    print("\n=== Report ===")
    print(f"Call 1 (miss) : {t1:.1f}s")
    print(f"Call 2 (hit)  : {t2:.1f}s")
    if t1 > 0:
        print(f"Speedup       : {t1 / max(t2, 0.01):.1f}x")
    print(f"Output match  : {'yes' if text1 == text2 else 'NO — cache returned different content'}")
    return 0 if text1 == text2 else 2


if __name__ == "__main__":
    raise SystemExit(main())
