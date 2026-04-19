"""Compare Gemini vs Sarvam OCR on a single PDF.

Usage:
    python test_docs/compare_ocr.py path/to/file.pdf [--out test_docs/ocr_output]

Writes:
    <out>/<basename>.gemini.md
    <out>/<basename>.sarvam.md
    <out>/<basename>.report.md    ← char counts, latency, first-page diff

Requires SARVAM_API_KEY and GEMINI_API_KEY in .env.
"""

from __future__ import annotations

import argparse
import sys
import time
from difflib import unified_diff
from pathlib import Path

# Make `legal_agent` importable when run from the repo root without `uv run`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from legal_agent.utils.ocr import ocr_pdf_with_gemini, ocr_pdf_with_sarvam  # noqa: E402


def _run(label: str, fn, pdf_bytes: bytes) -> tuple[str, float]:
    print(f"[{label}] starting…", flush=True)
    t0 = time.perf_counter()
    text = fn(pdf_bytes, output_format="markdown")
    elapsed = time.perf_counter() - t0
    print(f"[{label}] done in {elapsed:.1f}s → {len(text):,} chars", flush=True)
    return text, elapsed


def _first_page_preview(text: str, n: int = 40) -> str:
    return "\n".join(text.splitlines()[:n])


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Gemini vs Sarvam OCR.")
    parser.add_argument("pdf", type=Path, help="PDF to OCR")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("test_docs/ocr_output"),
        help="Output directory (default: test_docs/ocr_output)",
    )
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"error: {args.pdf} not found", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.pdf.stem
    pdf_bytes = args.pdf.read_bytes()

    gemini_text, gemini_s = _run("gemini", ocr_pdf_with_gemini, pdf_bytes)
    sarvam_text, sarvam_s = _run("sarvam", ocr_pdf_with_sarvam, pdf_bytes)

    gemini_path = args.out / f"{stem}.gemini.md"
    sarvam_path = args.out / f"{stem}.sarvam.md"
    report_path = args.out / f"{stem}.report.md"
    gemini_path.write_text(gemini_text, encoding="utf-8")
    sarvam_path.write_text(sarvam_text, encoding="utf-8")

    diff = "\n".join(
        unified_diff(
            _first_page_preview(gemini_text).splitlines(),
            _first_page_preview(sarvam_text).splitlines(),
            fromfile="gemini (first 40 lines)",
            tofile="sarvam (first 40 lines)",
            lineterm="",
        )
    )

    report = f"""# OCR comparison: {args.pdf.name}

| Backend | Chars | Latency (s) |
|---|---:|---:|
| Gemini | {len(gemini_text):,} | {gemini_s:.1f} |
| Sarvam | {len(sarvam_text):,} | {sarvam_s:.1f} |

- Gemini output: `{gemini_path}`
- Sarvam output: `{sarvam_path}`

## First-page diff (unified, 40 lines)

```diff
{diff or '(identical)'}
```
"""
    report_path.write_text(report, encoding="utf-8")
    print(f"\nwrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
