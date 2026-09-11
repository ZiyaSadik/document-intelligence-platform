"""Input-control layer.

Runs before any OCR or model call, so a malformed upload costs nothing. This is
file validation, not document classification -- the document type is supplied by
the caller as request metadata.
"""

from __future__ import annotations

import io

import pypdfium2 as pdfium
from PIL import Image

from app.core.config import settings
from app.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    FileTooLargeError,
    PageLimitExceededError,
    UnsupportedFileTypeError,
)
from app.core.logging import get_logger
from app.schemas.document import FileValidation
from app.schemas.enums import ProcessingStatus
from app.utils.files import (
    IMAGE_MIME_TYPES,
    PDF_MIME,
    human_size,
    sniff_mime_type,
)

logger = get_logger(__name__)


class DocumentValidationService:
    """Validates type, integrity, size and page count of an upload."""

    def __init__(
        self,
        *,
        max_bytes: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        self._max_bytes = max_bytes or settings.max_upload_bytes
        self._max_pages = max_pages or settings.max_page_count

    def validate(self, payload: bytes, *, filename: str) -> FileValidation:
        """Return a passing :class:`FileValidation`, or raise a typed error.

        Raising rather than returning ``status=FAILED`` for the failure cases is
        deliberate: the route layer turns the exception into the specified error
        envelope with the right HTTP status, and no partial result is stored.
        """
        size = len(payload)
        if size == 0:
            raise EmptyFileError(
                context={"file_name": filename},
                log_detail="zero-byte upload",
            )

        if size > self._max_bytes:
            raise FileTooLargeError(
                f"The uploaded file is {human_size(size)}; the maximum is "
                f"{human_size(self._max_bytes)}.",
                context={"file_name": filename, "size_bytes": size},
            )

        # The declared content type and the extension are both caller-supplied.
        # Trust only the bytes.
        mime_type = sniff_mime_type(payload)
        if mime_type is None:
            raise UnsupportedFileTypeError(
                context={"file_name": filename},
                log_detail=f"unrecognised magic bytes: {payload[:8]!r}",
            )

        if mime_type == PDF_MIME:
            page_count = self._pdf_page_count(payload, filename=filename)
        elif mime_type in IMAGE_MIME_TYPES:
            self._assert_image_readable(payload, filename=filename)
            page_count = 1
        else:  # pragma: no cover - sniff_mime_type cannot return anything else
            raise UnsupportedFileTypeError(context={"file_name": filename})

        if page_count > self._max_pages:
            raise PageLimitExceededError(
                f"The document has {page_count} pages; the maximum supported is "
                f"{self._max_pages}.",
                context={"file_name": filename, "page_count": page_count},
            )

        logger.info(
            "file validation passed",
            extra={
                "file_name": filename,
                "file_type": mime_type,
                "page_count": page_count,
                "size_bytes": size,
            },
        )
        return FileValidation(
            file_type=mime_type,
            is_supported=True,
            is_readable=True,
            page_count=page_count,
            status=ProcessingStatus.PASS,
        )

    # -- internals ---------------------------------------------------------
    def _pdf_page_count(self, payload: bytes, *, filename: str) -> int:
        try:
            pdf = pdfium.PdfDocument(io.BytesIO(payload))
            try:
                count = len(pdf)
            finally:
                pdf.close()
        except Exception as exc:  # pdfium raises a variety of low-level errors
            raise CorruptedFileError(
                "The PDF could not be opened; it appears to be corrupted or "
                "password protected.",
                context={"file_name": filename},
                log_detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        if count < 1:
            raise CorruptedFileError(
                "The PDF contains no pages.", context={"file_name": filename}
            )
        return count

    def _assert_image_readable(self, payload: bytes, *, filename: str) -> None:
        try:
            # verify() checks structural integrity but leaves the file unusable,
            # so it runs on a throwaway handle.
            Image.open(io.BytesIO(payload)).verify()
            # A second open confirms the data is actually decodable.
            with Image.open(io.BytesIO(payload)) as image:
                image.load()
        except Exception as exc:
            raise CorruptedFileError(
                "The image could not be decoded; it appears to be corrupted.",
                context={"file_name": filename},
                log_detail=f"{type(exc).__name__}: {exc}",
            ) from exc


def failed_validation(
    *, file_type: str, reason: str, page_count: int | None = None
) -> FileValidation:
    """Build the FAILED shape for persisting a rejected upload."""
    return FileValidation(
        file_type=file_type,
        is_supported=file_type in {PDF_MIME} | IMAGE_MIME_TYPES,
        is_readable=False,
        page_count=page_count,
        status=ProcessingStatus.FAILED,
        reason=reason,
    )
