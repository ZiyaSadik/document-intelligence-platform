"""Document processing and retrieval endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Path, Query, UploadFile, status

from app.api.deps import DocumentServiceDep, LimitDep, OffsetDep, parse_document_type
from app.core.config import settings
from app.core.exceptions import DocumentNotFoundError, EmptyFileError
from app.core.logging import get_logger
from app.schemas.document import (
    DocumentListResponse,
    DocumentResult,
    DocumentSummary,
    ErrorResponse,
)
from app.schemas.enums import DocumentType

logger = get_logger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "The upload failed validation."},
    404: {"model": ErrorResponse, "description": "No such processed document."},
    413: {"model": ErrorResponse, "description": "The file exceeds the size limit."},
    422: {"model": ErrorResponse, "description": "The request was malformed."},
    502: {"model": ErrorResponse, "description": "Extraction failed."},
    503: {"model": ErrorResponse, "description": "A dependency is unavailable."},
}


@router.post(
    "/process",
    response_model=DocumentResult,
    status_code=status.HTTP_200_OK,
    responses=_ERROR_RESPONSES,
    summary="Upload and process a document",
    description=(
        "Accepts a PDF, JPG or PNG of at most three pages together with the "
        "document type as request metadata. Validates the file, reads it via "
        "its native text layer or by rasterising and reading the pages, "
        "extracts all visible fields and tables, runs the financial "
        "reconciliations for that document type, stores the result and returns "
        "the structured response.\n\n"
        "Processing is synchronous. Re-processing the same file name stores a "
        "new result; the retrieval endpoint returns the most recent one."
    ),
)
async def process_document(
    service: DocumentServiceDep,
    file: Annotated[UploadFile, File(description="The PDF / JPG / PNG to process.")],
    document_type: Annotated[
        str,
        Form(
            description=(
                "One of: invoice, balance_sheet, profit_and_loss, "
                "cash_flow_statement."
            ),
            examples=["invoice"],
        ),
    ],
) -> DocumentResult:
    parsed_type = parse_document_type(document_type)

    # Read with a ceiling so an oversized upload cannot exhaust memory before
    # the size check runs. One extra byte is enough to detect the overflow.
    payload = await file.read(settings.max_upload_bytes + 1)
    await file.close()

    if not payload:
        raise EmptyFileError(context={"file_name": file.filename})

    return service.process(
        payload, filename=file.filename, document_type=parsed_type
    )


@router.get(
    "",
    response_model=DocumentListResponse,
    responses=_ERROR_RESPONSES,
    summary="List processed documents",
    description=(
        "Returns the most recent processing result for each distinct document "
        "name, newest first. Backs the dashboard."
    ),
)
def list_documents(
    service: DocumentServiceDep,
    limit: LimitDep = 50,
    offset: OffsetDep = 0,
    document_type: Annotated[
        DocumentType | None, Query(description="Filter by document type.")
    ] = None,
    search: Annotated[
        str | None, Query(max_length=200, description="Case-insensitive name filter.")
    ] = None,
) -> DocumentListResponse:
    records, total = service.list_documents(
        limit=limit, offset=offset, document_type=document_type, search=search
    )
    items = [
        DocumentSummary(
            id=record.id,
            document_name=record.document_name,
            document_type=record.document_type,
            processing_status=record.processing_status,
            validation_status=record.validation_status,
            overall_confidence=record.overall_confidence,
            page_count=record.page_count,
            ocr_used=record.ocr_used,
            processing_time_ms=record.processing_time_ms,
            processed_at=record.processed_at,
        )
        for record in records
    ]
    return DocumentListResponse(
        total=total, count=len(items), limit=limit, offset=offset, items=items
    )


@router.get(
    "/{document_name}",
    response_model=DocumentResult,
    responses=_ERROR_RESPONSES,
    summary="Retrieve the latest result by document name",
    description=(
        "Returns the most recent stored processing result for the given file "
        "name. Matching is case-insensitive."
    ),
)
def get_document(
    service: DocumentServiceDep,
    document_name: Annotated[
        str,
        Path(
            description="The uploaded file name, e.g. `sample_invoice.pdf`.",
            examples=["sample_invoice.pdf"],
        ),
    ],
) -> DocumentResult:
    record = service.get_by_name(document_name)
    if record is None:
        raise DocumentNotFoundError(
            f"No processed result was found for '{document_name}'.",
            context={"document_name": document_name},
        )
    return DocumentResult.model_validate(record.result_json)
