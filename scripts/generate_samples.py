"""Produce sample_outputs/ by running the real pipeline over the sample corpus.

Nothing here is hand-written or hardcoded: each JSON file is exactly what the
API would return for that document. Requires ANTHROPIC_API_KEY.

    python scripts/generate_samples.py                 # the whole matrix
    python scripts/generate_samples.py --only invoice  # one document type
    python scripts/generate_samples.py --list          # show the matrix

The selection deliberately covers the brief's testing requirements:

* all four document types
* a scanned image (a phone photo and a thermal receipt)
* a native-text PDF and a vector-drawn PDF, so both read paths are exercised
* a multi-page statement
* an unsupported file, to capture a rejection
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

SAMPLES = ROOT / "data" / "samples" / "New Dataset"
OUTPUT = ROOT / "sample_outputs"


@dataclass(frozen=True)
class Case:
    slug: str
    path: Path
    document_type: str
    why: str
    expect_rejection: bool = False


def matrix() -> list[Case]:
    return [
        Case(
            "invoice_receipt_scanned",
            SAMPLES / "Invoices" / "X51005361895.jpg",
            "invoice",
            "Scanned thermal receipt; tax-inclusive total, cash and change.",
        ),
        Case(
            "invoice_phone_photo",
            SAMPLES / "Invoices" / "20251118_000612.jpg",
            "invoice",
            "4.4 MB phone photo; exercises EXIF rotation and downscaling.",
        ),
        Case(
            "balance_sheet_vector_pdf",
            SAMPLES / "Balance Sheet" / "Consolidated Balance Sheet 2024.pdf",
            "balance_sheet",
            "No text layer and no embedded image; must be rasterised.",
        ),
        Case(
            "profit_and_loss_vector_pdf",
            SAMPLES / "Profit & Loss" / "Consolidated Profit & Loss 2022.pdf",
            "profit_and_loss",
            "Comparative P&L; income, expenditure and appropriation checks.",
        ),
        Case(
            "cash_flow_native_text_pdf",
            SAMPLES / "Cash Flows" / "Consolidated Cash Flow Statement 2022.pdf",
            "cash_flow_statement",
            "The one PDF with a real text layer; exercises the fast path "
            "(expect ocr_used=false).",
        ),
        Case(
            "cash_flow_two_page_pdf",
            SAMPLES / "Cash Flows" / "Consolidated Cash Flow Statement 2019.pdf",
            "cash_flow_statement",
            "Two pages; closing balances sit on page 2.",
        ),
        Case(
            "unsupported_file_rejected",
            ROOT / "README.md",
            "invoice",
            "Not a PDF/JPG/PNG; captures the rejection envelope.",
            expect_rejection=True,
        ),
    ]


def run(cases: list[Case]) -> int:
    from app.core.config import settings
    from app.core.database import SessionLocal, init_db
    from app.core.exceptions import AppError
    from app.core.logging import configure_logging
    from app.schemas.enums import DocumentType
    from app.services.document_service import DocumentService

    configure_logging()

    if not settings.extraction_enabled:
        print(
            "ANTHROPIC_API_KEY is not set.\n"
            "Add it to .env or export it, then re-run. Sample outputs are the "
            "real pipeline's output and cannot be produced without it.",
            file=sys.stderr,
        )
        return 2

    init_db()
    OUTPUT.mkdir(parents=True, exist_ok=True)

    index: list[dict] = []
    failures = 0

    for case in cases:
        if not case.path.exists():
            print(f"  SKIP  {case.slug}: {case.path} not found", file=sys.stderr)
            failures += 1
            continue

        payload = case.path.read_bytes()
        print(f"\n>>> {case.slug}  ({case.path.name}, {len(payload) // 1024} KB)")
        print(f"    {case.why}")

        session = SessionLocal()
        try:
            service = DocumentService(session=session)
            result = service.process(
                payload,
                filename=case.path.name,
                document_type=DocumentType(case.document_type),
            )
            body = result.model_dump(mode="json")
            status = "PASS"
            checks = result.validation.checks
            summary = {
                "processing_status": result.processing_status.value,
                "validation_status": result.validation.overall_status.value,
                "checks": len(checks),
                "failed": sum(1 for c in checks if c.status.value == "FAIL"),
                "not_applicable": sum(
                    1 for c in checks if c.status.value == "NOT_APPLICABLE"
                ),
                "ocr_used": result.processing_metadata.ocr_used,
                "confidence": result.overall_confidence,
                "ms": result.processing_metadata.processing_time_ms,
            }
            print(
                f"    -> {summary['validation_status']}  "
                f"{summary['checks']} checks "
                f"({summary['failed']} failed, {summary['not_applicable']} n/a), "
                f"ocr_used={summary['ocr_used']}, "
                f"confidence={summary['confidence']}, "
                f"{summary['ms']} ms"
            )
            if case.expect_rejection:
                print("    !! expected a rejection but the document processed")
                failures += 1
        except AppError as exc:
            body = {
                "request": {
                    "file": case.path.name,
                    "document_type": case.document_type,
                },
                "http_status": exc.status_code,
                "response": exc.to_envelope(),
            }
            status = "REJECTED"
            summary = {"http_status": exc.status_code, "code": exc.code}
            print(f"    -> {exc.status_code} {exc.code}: {exc.message}")
            if not case.expect_rejection:
                print("    !! unexpected rejection")
                failures += 1
        finally:
            session.close()

        destination = OUTPUT / f"{case.slug}.json"
        destination.write_text(
            json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        index.append(
            {
                "file": destination.name,
                "source_document": case.path.name,
                "document_type": case.document_type,
                "why_included": case.why,
                "outcome": status,
                "summary": summary,
            }
        )

    (OUTPUT / "index.json").write_text(
        json.dumps(
            {
                "generated_by": "scripts/generate_samples.py",
                "model": settings.anthropic_model,
                "note": (
                    "Real pipeline output. Nothing in these files is "
                    "hand-written or hardcoded."
                ),
                "samples": index,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(f"\nWrote {len(index)} file(s) to {OUTPUT.relative_to(ROOT)}/")
    if failures:
        print(f"{failures} case(s) did not behave as expected.", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="Restrict to one document_type or slug.")
    parser.add_argument(
        "--list", action="store_true", help="Print the matrix and exit."
    )
    args = parser.parse_args()

    cases = matrix()
    if args.list:
        for case in cases:
            print(f"{case.slug:32s} {case.document_type:22s} {case.path.name}")
        return 0
    if args.only:
        cases = [
            c for c in cases if args.only in (c.document_type, c.slug)
        ]
        if not cases:
            print(f"nothing matches {args.only!r}", file=sys.stderr)
            return 2
    return run(cases)


if __name__ == "__main__":
    sys.exit(main())
