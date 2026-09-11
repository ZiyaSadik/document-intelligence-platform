"""Application error hierarchy.

Every failure the API can produce is expressed as an :class:`AppError` carrying
a stable machine-readable ``code``, a user-safe ``message`` and an HTTP status.
Handlers in ``app.main`` render these into the error envelope required by the
specification::

    {"error": {"code": "UNSUPPORTED_FILE_TYPE", "message": "..."}}

Internal detail (stack traces, provider messages, file paths) is attached to
``log_detail`` which is logged but never serialised to the client.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all controlled application failures."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred while processing the request."

    def __init__(
        self,
        message: str | None = None,
        *,
        log_detail: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.__class__.message
        self.log_detail = log_detail
        self.context = context or {}
        super().__init__(self.message)

    def to_envelope(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


# --------------------------------------------------------------------------
# Input / file validation (4xx)
# --------------------------------------------------------------------------
class DocumentValidationError(AppError):
    """Base class for upload-control failures."""

    code = "DOCUMENT_VALIDATION_FAILED"
    status_code = 400
    message = "The uploaded document failed validation."


class UnsupportedFileTypeError(DocumentValidationError):
    code = "UNSUPPORTED_FILE_TYPE"
    status_code = 400
    message = "Only PDF / JPG / PNG documents are supported."


class EmptyFileError(DocumentValidationError):
    code = "EMPTY_FILE"
    status_code = 400
    message = "The uploaded file is empty."


class CorruptedFileError(DocumentValidationError):
    code = "CORRUPTED_FILE"
    status_code = 400
    message = "The uploaded file could not be read; it appears to be corrupted."


class PageLimitExceededError(DocumentValidationError):
    code = "PAGE_LIMIT_EXCEEDED"
    status_code = 400
    message = "The document exceeds the supported page limit."


class FileTooLargeError(DocumentValidationError):
    code = "FILE_TOO_LARGE"
    status_code = 413
    message = "The uploaded file exceeds the maximum allowed size."


class InvalidDocumentTypeError(DocumentValidationError):
    code = "INVALID_DOCUMENT_TYPE"
    status_code = 422
    message = (
        "document_type must be one of: invoice, balance_sheet, "
        "profit_and_loss, cash_flow_statement."
    )


class DocumentNotFoundError(AppError):
    code = "DOCUMENT_NOT_FOUND"
    status_code = 404
    message = "No processed result was found for the requested document name."


# --------------------------------------------------------------------------
# Downstream / processing failures (5xx)
# --------------------------------------------------------------------------
class OcrError(AppError):
    code = "OCR_FAILED"
    status_code = 422
    message = "Text could not be extracted from the document."


class ExtractionError(AppError):
    code = "EXTRACTION_FAILED"
    status_code = 502
    message = "The extraction model could not process this document."


class ModelUnavailableError(ExtractionError):
    code = "MODEL_UNAVAILABLE"
    status_code = 503
    message = "The extraction service is temporarily unavailable. Please retry."


class ModelNotConfiguredError(ExtractionError):
    code = "MODEL_NOT_CONFIGURED"
    status_code = 503
    message = (
        "Extraction is not configured on this deployment: "
        "ANTHROPIC_API_KEY is not set."
    )


class StorageError(AppError):
    code = "STORAGE_FAILED"
    status_code = 503
    message = "The processed result could not be stored. Please retry."
