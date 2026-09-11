"""Data access for processed documents.

All SQLAlchemy usage lives here; services and routes never build queries. The
repository translates driver-level failures into :class:`StorageError` so a
database outage produces a controlled 503 rather than a stack trace.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.exceptions import StorageError
from app.core.logging import get_logger
from app.models.document import ProcessedDocument

logger = get_logger(__name__)


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # -- writes ------------------------------------------------------------
    def save(
        self,
        *,
        document_name: str,
        document_type: str,
        processing_status: str,
        validation_status: str | None,
        overall_confidence: float | None,
        page_count: int | None,
        ocr_used: bool,
        processing_time_ms: int | None,
        result_json: dict[str, Any],
    ) -> ProcessedDocument:
        record = ProcessedDocument(
            document_name=document_name,
            document_type=document_type,
            processing_status=processing_status,
            validation_status=validation_status,
            overall_confidence=overall_confidence,
            page_count=page_count,
            ocr_used=ocr_used,
            processing_time_ms=processing_time_ms,
            result_json=result_json,
        )
        try:
            self._session.add(record)
            self._session.commit()
            self._session.refresh(record)
        except SQLAlchemyError as exc:
            self._session.rollback()
            logger.exception(
                "failed to persist processing result",
                extra={"document_name": document_name},
            )
            raise StorageError(log_detail=str(exc)) from exc

        logger.info(
            "processing result stored",
            extra={"document_id": record.id, "document_name": document_name},
        )
        return record

    # -- reads -------------------------------------------------------------
    def get_latest_by_name(self, document_name: str) -> ProcessedDocument | None:
        """Most recent run for a name; case-insensitive on the stored name."""
        stmt = (
            select(ProcessedDocument)
            .where(func.lower(ProcessedDocument.document_name) == document_name.lower())
            .order_by(
                ProcessedDocument.processed_at.desc(), ProcessedDocument.id.desc()
            )
            .limit(1)
        )
        return self._safe_scalar(stmt)

    def get_by_id(self, document_id: int) -> ProcessedDocument | None:
        stmt = select(ProcessedDocument).where(ProcessedDocument.id == document_id)
        return self._safe_scalar(stmt)

    def list_latest(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        document_type: str | None = None,
        search: str | None = None,
    ) -> tuple[list[ProcessedDocument], int]:
        """The dashboard view: newest run per document name, newest first."""
        # Latest row id per name, computed once and joined back.
        latest_ids = select(func.max(ProcessedDocument.id).label("id")).group_by(
            ProcessedDocument.document_name
        )

        stmt = select(ProcessedDocument).where(
            ProcessedDocument.id.in_(latest_ids.scalar_subquery())
        )
        if document_type:
            stmt = stmt.where(ProcessedDocument.document_type == document_type)
        if search:
            pattern = f"%{search.lower()}%"
            stmt = stmt.where(func.lower(ProcessedDocument.document_name).like(pattern))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        page_stmt = (
            stmt.order_by(
                ProcessedDocument.processed_at.desc(), ProcessedDocument.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )

        try:
            total = self._session.execute(count_stmt).scalar_one()
            rows = list(self._session.execute(page_stmt).scalars().all())
        except SQLAlchemyError as exc:
            logger.exception("failed to list processed documents")
            raise StorageError(log_detail=str(exc)) from exc
        return rows, total

    # -- internals ---------------------------------------------------------
    def _safe_scalar(self, stmt) -> ProcessedDocument | None:
        try:
            return self._session.execute(stmt).scalars().first()
        except SQLAlchemyError as exc:
            logger.exception("database read failed")
            raise StorageError(log_detail=str(exc)) from exc
