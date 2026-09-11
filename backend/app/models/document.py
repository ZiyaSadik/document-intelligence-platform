"""ORM model for a processed document."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProcessedDocument(Base):
    """One processing run.

    Rows are append-only: re-processing a document under the same name inserts
    a new row, and the read path returns the most recent one. Keeping history
    costs nothing and makes "why did this change?" answerable during review.
    """

    __tablename__ = "processed_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    document_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    processing_status: Mapped[str] = mapped_column(String(16), nullable=False)
    validation_status: Mapped[str | None] = mapped_column(String(16), nullable=True)

    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The full section-5.2 response, stored verbatim so the dashboard and the
    # GET-by-name endpoint serve exactly what the POST returned.
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    __table_args__ = (
        # Supports "latest result for this name" without a full scan.
        Index("ix_processed_documents_name_time", "document_name", "processed_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ProcessedDocument id={self.id} name={self.document_name!r} "
            f"status={self.processing_status}>"
        )
