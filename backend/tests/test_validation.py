"""File validation and numeric parsing.

These are the two places where a silent mistake is most expensive: a bad file
that reaches the model wastes a paid call, and a mis-parsed number inverts a
sign and breaks every reconciliation downstream.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    FileTooLargeError,
    PageLimitExceededError,
    UnsupportedFileTypeError,
)
from app.services.document_validation_service import DocumentValidationService
from app.utils.files import sanitise_filename, sniff_mime_type
from app.utils.numbers import (
    parse_money,
    safe_sum,
    text_contains_number,
    within_tolerance,
)
from tests.conftest import make_image, make_pdf


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------
@pytest.fixture
def validator() -> DocumentValidationService:
    return DocumentValidationService(max_bytes=5 * 1024 * 1024, max_pages=3)


def test_accepts_single_page_pdf(validator):
    result = validator.validate(make_pdf(1), filename="statement.pdf")
    assert result.status == "PASS"
    assert result.file_type == "application/pdf"
    assert result.page_count == 1
    assert result.is_supported and result.is_readable


@pytest.mark.parametrize("pages", [1, 2, 3])
def test_accepts_pdfs_up_to_the_page_limit(validator, pages):
    assert validator.validate(make_pdf(pages), filename="x.pdf").page_count == pages


def test_rejects_pdf_over_the_page_limit(validator):
    with pytest.raises(PageLimitExceededError) as exc:
        validator.validate(make_pdf(4), filename="long.pdf")
    assert exc.value.code == "PAGE_LIMIT_EXCEEDED"
    assert exc.value.context["page_count"] == 4


@pytest.mark.parametrize("fmt,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png")])
def test_accepts_supported_images(validator, fmt, mime):
    result = validator.validate(make_image(fmt), filename=f"receipt.{fmt.lower()}")
    assert result.file_type == mime
    assert result.page_count == 1


def test_rejects_empty_file(validator):
    with pytest.raises(EmptyFileError):
        validator.validate(b"", filename="empty.pdf")


def test_rejects_unsupported_type(validator):
    with pytest.raises(UnsupportedFileTypeError):
        validator.validate(b"plain text, not a document", filename="notes.txt")


def test_rejects_file_renamed_to_a_supported_extension(validator):
    """The extension says PDF; the bytes say otherwise. The bytes win."""
    with pytest.raises(UnsupportedFileTypeError):
        validator.validate(b"This is not really a PDF at all.", filename="fake.pdf")


def test_rejects_corrupted_pdf(validator):
    truncated = make_pdf(1)[:120]  # header survives, structure does not
    with pytest.raises(CorruptedFileError):
        validator.validate(truncated, filename="broken.pdf")


def test_rejects_corrupted_image(validator):
    payload = bytearray(make_image("PNG"))
    del payload[200:]  # keep the magic bytes, destroy the image data
    with pytest.raises(CorruptedFileError):
        validator.validate(bytes(payload), filename="broken.png")


def test_rejects_oversized_file():
    validator = DocumentValidationService(max_bytes=1024, max_pages=3)
    with pytest.raises(FileTooLargeError) as exc:
        validator.validate(make_pdf(1) + b"\x00" * 4096, filename="big.pdf")
    assert exc.value.status_code == 413


# ---------------------------------------------------------------------------
# Magic bytes and filenames
# ---------------------------------------------------------------------------
def test_sniff_mime_type():
    assert sniff_mime_type(make_pdf(1)) == "application/pdf"
    assert sniff_mime_type(make_image("JPEG")) == "image/jpeg"
    assert sniff_mime_type(make_image("PNG")) == "image/png"
    assert sniff_mime_type(b"GIF89a....") is None
    assert sniff_mime_type(b"") is None


@pytest.mark.parametrize(
    "given,expected",
    [
        ("invoice.pdf", "invoice.pdf"),
        ("../../etc/passwd", "passwd"),
        (r"C:\Users\me\Balance Sheet 2024.pdf", "Balance Sheet 2024.pdf"),
        ("weird\x00name.pdf", "weirdname.pdf"),
        ("", "document"),
        (None, "document"),
    ],
)
def test_sanitise_filename(given, expected):
    assert sanitise_filename(given) == expected


# ---------------------------------------------------------------------------
# Numeric parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1234", 1234.0),
        ("1,234.56", 1234.56),
        ("1,23,456.78", 123456.78),          # Indian digit grouping
        ("(1,234.56)", -1234.56),            # parentheses mean negative
        ("(248,946.13)", -248946.13),
        ("\u20b9 1,234.00", 1234.0),         # rupee sign
        ("` 1,234.00", 1234.0),              # how some PDFs render the rupee
        ("RM 12.30", 12.3),
        ("USD 13,125.00", 13125.0),
        ("1234-", -1234.0),                  # trailing minus
        ("-1234", -1234.0),
        ("\u2212500", -500.0),               # Unicode minus
        (1234, 1234.0),
        (12.5, 12.5),
        # A printed dash or "Nil" is the accounting notation for zero. The
        # document does report it, so it must not become a missing operand.
        ("-", 0.0),
        ("\u2014", 0.0),
        ("Nil", 0.0),
        # These mean the document does not report the value at all.
        ("N/A", None),
        ("not reported", None),
        ("", None),
        (None, None),
        ("not a number", None),
        (True, None),                        # bool is an int subclass; reject it
    ],
)
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


def test_safe_sum_requires_every_operand():
    assert safe_sum([1.0, 2.0, 3.0]) == 6.0
    assert safe_sum([1.0, None, 3.0]) is None, "a missing operand must not become 0"
    assert safe_sum([]) is None


@pytest.mark.parametrize(
    "calculated,reported,expected",
    [
        (100.0, 100.0, True),
        (100.0, 100.5, True),      # inside the absolute tolerance
        (100.0, 103.0, False),
        (1_000_000.0, 1_000_400.0, True),   # inside the relative tolerance
        (1_000_000.0, 1_020_000.0, False),
        (0.0, 0.0, True),
        (0.0, 5.0, False),
    ],
)
def test_within_tolerance(calculated, reported, expected):
    assert (
        within_tolerance(
            calculated, reported, abs_tolerance=1.0, rel_tolerance=0.005
        )
        is expected
    )


def test_text_contains_number():
    assert text_contains_number("Total Amount Due: USD 13,125.00", 13125.0)
    assert text_contains_number("Net cash used in investing (2,236.24)", -2236.24)
    assert not text_contains_number("Total Amount Due: USD 13,125.00", 999.0)
    assert not text_contains_number(None, 1.0)


# ---------------------------------------------------------------------------
# Configuration hygiene
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw",
    [
        " sk-ant-test-value",       # a space after '=' in an env file
        "sk-ant-test-value ",
        "\nsk-ant-test-value\n",    # pasted with a trailing newline
        '"sk-ant-test-value"',      # quoted in the env file
        "'sk-ant-test-value'",
    ],
)
def test_api_key_whitespace_and_quotes_are_stripped(raw, monkeypatch):
    """A key with surrounding whitespace makes an illegal HTTP header.

    The SDK reports that as a bare "Connection error", so the service looks
    healthy and every upload fails with MODEL_UNAVAILABLE. python-dotenv strips
    it; Docker's --env-file and most hosting UIs do not.
    """
    from app.core.config import Settings

    monkeypatch.setenv("ANTHROPIC_API_KEY", raw)
    settings = Settings(_env_file=None)
    assert settings.anthropic_api_key == "sk-ant-test-value"
    assert settings.extraction_enabled


def test_postgres_url_is_normalised_for_sqlalchemy(monkeypatch):
    """Hosting platforms hand out postgres:// URLs that SQLAlchemy rejects."""
    from app.core.config import Settings

    monkeypatch.setenv("DATABASE_URL", " postgres://user:pw@host:5432/db ")
    assert Settings(_env_file=None).database_url == (
        "postgresql+psycopg://user:pw@host:5432/db"
    )
