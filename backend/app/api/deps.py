"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_session
from app.core.exceptions import InvalidDocumentTypeError
from app.schemas.enums import DocumentType
from app.services.document_service import DocumentService

SessionDep = Annotated[Session, Depends(get_session)]


def get_document_service(session: SessionDep) -> DocumentService:
    return DocumentService(session=session)


DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]


def parse_document_type(value: str) -> DocumentType:
    """Coerce the multipart ``document_type`` field into the enum.

    FastAPI would reject an unknown value with its own 422 body; routing it
    through the application error type keeps every failure in the same
    ``{"error": {...}}`` envelope.
    """
    try:
        return DocumentType(value.strip().lower())
    except ValueError as exc:
        raise InvalidDocumentTypeError(
            f"'{value}' is not a supported document_type. Use one of: "
            + ", ".join(t.value for t in DocumentType)
        ) from exc


LimitDep = Annotated[
    int, Query(ge=1, le=200, description="Maximum number of records to return.")
]
OffsetDep = Annotated[int, Query(ge=0, description="Records to skip.")]
