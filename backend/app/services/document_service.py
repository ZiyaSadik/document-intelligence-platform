"""Pipeline orchestration.

Owns the order of operations and nothing else - each stage lives in its own
service. Read top to bottom, this function is the architecture diagram:

    validate file -> read pages -> extract fields -> reconcile -> score -> store

Failures are recorded, not swallowed. A rejected or unprocessable document is
persisted with ``processing_status = FAILED`` before the error is re-raised, so
the dashboard shows what was attempted rather than silently dropping it.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppError, DocumentValidationError, OcrError
from app.core.logging import get_logger
from app.models.document import ProcessedDocument
from app.repositories.document_repository import DocumentRepository
from app.schemas.document import (
    DocumentResult,
    FileValidation,
    ProcessingMetadata,
    ValidationResult,
)
from app.schemas.enums import DocumentType, ProcessingStatus, ValidationStatus
from app.services.document_validation_service import (
    DocumentValidationService,
    failed_validation,
)
from app.services.extraction_service import ExtractionService
from app.services.financial_validation_service import FinancialValidationService
from app.services.ocr_service import OcrService
from app.services.result_builder import build_extracted_data, missing_field_names
from app.utils.files import sanitise_filename, sniff_mime_type

logger = get_logger(__name__)


class DocumentService:
    """Coordinates validation, reading, extraction, reconciliation and storage."""

    def __init__(
        self,
        *,
        session: Session,
        validation_service: DocumentValidationService | None = None,
        ocr_service: OcrService | None = None,
        extraction_service: ExtractionService | None = None,
        financial_validation_service: FinancialValidationService | None = None,
    ) -> None:
        self._repository = DocumentRepository(session)
        self._validator = validation_service or DocumentValidationService()
        self._ocr = ocr_service or OcrService()
        self._extractor = extraction_service or ExtractionService()
        self._financials = financial_validation_service or FinancialValidationService()

    # -- write path --------------------------------------------------------
    def process(
        self,
        payload: bytes,
        *,
        filename: str | None,
        document_type: DocumentType,
    ) -> DocumentResult:
        started = time.perf_counter()
        document_name = sanitise_filename(filename)

        logger.info(
            "processing started",
            extra={
                "document_name": document_name,
                "document_type": document_type.value,
                "size_bytes": len(payload),
            },
        )

        try:
            file_validation = self._validator.validate(payload, filename=document_name)
        except DocumentValidationError as exc:
            self._store_failure(
                document_name=document_name,
                document_type=document_type,
                file_validation=failed_validation(
                    file_type=sniff_mime_type(payload) or "unknown",
                    reason=exc.message,
                    page_count=exc.context.get("page_count"),
                ),
                reason=exc.message,
                started=started,
            )
            raise

        try:
            content = self._ocr.extract(
                payload,
                mime_type=file_validation.file_type,
                filename=document_name,
            )
            extraction = self._extractor.extract(
                content, document_type=document_type, filename=document_name
            )
            validation = self._financials.validate(
                extraction.data, document_type=document_type
            )
            extracted_data, confidence = build_extracted_data(
                extraction.data,
                document_type=document_type,
                checks=validation.checks,
                used_ocr=content.ocr_used,
            )
        except (OcrError, AppError) as exc:
            self._store_failure(
                document_name=document_name,
                document_type=document_type,
                file_validation=file_validation,
                reason=exc.message if isinstance(exc, AppError) else str(exc),
                started=started,
            )
            raise

        extracted_data["missing_fields"] = missing_field_names(extracted_data)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result = DocumentResult(
            document_name=document_name,
            document_type=document_type,
            processing_status=ProcessingStatus.PASS,
            overall_confidence=confidence,
            file_validation=file_validation,
            extracted_data=extracted_data,
            validation=validation,
            processing_metadata=ProcessingMetadata(
                ocr_used=content.ocr_used,
                extraction_method=content.extraction_method,
                model=extraction.model,
                pages_processed=content.page_count,
                processed_at=datetime.now(timezone.utc),
                processing_time_ms=elapsed_ms,
            ),
        )

        self._repository.save(
            document_name=document_name,
            document_type=document_type.value,
            processing_status=ProcessingStatus.PASS.value,
            validation_status=validation.overall_status.value,
            overall_confidence=confidence,
            page_count=file_validation.page_count,
            ocr_used=content.ocr_used,
            processing_time_ms=elapsed_ms,
            result_json=result.model_dump(mode="json"),
        )

        logger.info(
            "processing complete",
            extra={
                "document_name": document_name,
                "document_type": document_type.value,
                "validation_status": validation.overall_status.value,
                "confidence": confidence,
                "duration_ms": elapsed_ms,
                "checks": len(validation.checks),
                "failed_checks": len(validation.issues),
            },
        )
        return result

    # -- read path ---------------------------------------------------------
    def get_by_name(self, document_name: str) -> ProcessedDocument | None:
        return self._repository.get_latest_by_name(sanitise_filename(document_name))

    def list_documents(
        self,
        *,
        limit: int,
        offset: int,
        document_type: DocumentType | None = None,
        search: str | None = None,
    ) -> tuple[list[ProcessedDocument], int]:
        return self._repository.list_latest(
            limit=limit,
            offset=offset,
            document_type=document_type.value if document_type else None,
            search=search,
        )

    # -- internals ---------------------------------------------------------
    def _store_failure(
        self,
        *,
        document_name: str,
        document_type: DocumentType,
        file_validation: FileValidation,
        reason: str,
        started: float,
    ) -> None:
        """Record a failed attempt so the dashboard reflects it.

        A storage problem here must not mask the original error, so it is
        logged and swallowed rather than raised.
        """
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        result = DocumentResult(
            document_name=document_name,
            document_type=document_type,
            processing_status=ProcessingStatus.FAILED,
            overall_confidence=None,
            file_validation=file_validation,
            extracted_data={},
            validation=ValidationResult(
                checks=[],
                overall_status=ValidationStatus.FAIL,
                issues=[reason],
                tolerance={
                    "absolute": settings.validation_abs_tolerance,
                    "relative": settings.validation_rel_tolerance,
                },
            ),
            processing_metadata=ProcessingMetadata(
                ocr_used=False,
                extraction_method="none",
                model=None,
                pages_processed=file_validation.page_count or 0,
                processed_at=datetime.now(timezone.utc),
                processing_time_ms=elapsed_ms,
            ),
        )
        try:
            self._repository.save(
                document_name=document_name,
                document_type=document_type.value,
                processing_status=ProcessingStatus.FAILED.value,
                validation_status=None,
                overall_confidence=None,
                page_count=file_validation.page_count,
                ocr_used=False,
                processing_time_ms=elapsed_ms,
                result_json=result.model_dump(mode="json"),
            )
        except Exception:  # pragma: no cover - never mask the original failure
            logger.exception(
                "could not record failed processing attempt",
                extra={"document_name": document_name},
            )

        logger.warning(
            "processing failed",
            extra={
                "document_name": document_name,
                "document_type": document_type.value,
                "reason": reason,
                "duration_ms": elapsed_ms,
            },
        )
