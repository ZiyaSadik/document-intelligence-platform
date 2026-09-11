"""Public API contract.

These models define exactly what the REST endpoints emit; they are what shows
up in the generated OpenAPI document, so the examples matter as much as the
field names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import (
    CheckStatus,
    DocumentType,
    ProcessingStatus,
    ValidationStatus,
)


class FileValidation(BaseModel):
    """Result of the input-control layer, per specification section 4.1."""

    file_type: str = Field(examples=["application/pdf"])
    is_supported: bool
    is_readable: bool
    page_count: int | None = Field(default=None, examples=[1])
    status: ProcessingStatus
    reason: str | None = Field(
        default=None, description="Populated only when status is FAILED."
    )


class Evidence(BaseModel):
    """Where a value came from, so an evaluator can trace it back."""

    source_text: str | None = Field(
        default=None, examples=["Total Amount Due: USD 13,125.00"]
    )
    page_number: int | None = Field(default=None, examples=[1])


class ExtractedField(BaseModel):
    """A single extracted key-value pair with grounding."""

    value: Any = Field(
        default=None,
        description="Parsed value: float for monetary fields, string otherwise, "
        "null when the document does not report it.",
        examples=[13125.0],
    )
    raw_value: str | None = Field(
        default=None,
        description="The value exactly as printed in the document.",
        examples=["13,125.00"],
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, examples=[0.97])
    page_number: int | None = Field(default=None, examples=[1])
    evidence: Evidence | None = None


class ValidationCheck(BaseModel):
    """One financial reconciliation, per specification section 4.4."""

    name: str = Field(examples=["invoice_total_check"])
    description: str | None = None
    formula: str = Field(examples=["subtotal + tax_amount - discount"])
    period: str | None = Field(
        default=None,
        description="Which comparative period this check was run for; null for invoices.",
        examples=["March 31, 2022"],
    )
    operands: dict[str, float | None] = Field(
        default_factory=dict,
        examples=[{"subtotal": 12500.0, "tax_amount": 625.0, "discount": 0.0}],
    )
    calculated_value: float | None = Field(default=None, examples=[13125.0])
    reported_value: float | None = Field(default=None, examples=[13125.0])
    variance: float | None = Field(default=None, examples=[0.0])
    status: CheckStatus


class ValidationResult(BaseModel):
    checks: list[ValidationCheck] = Field(default_factory=list)
    overall_status: ValidationStatus
    issues: list[str] = Field(default_factory=list)
    tolerance: dict[str, float] = Field(
        default_factory=dict,
        description="The absolute and relative tolerances applied.",
        examples=[{"absolute": 1.0, "relative": 0.005}],
    )


class ProcessingMetadata(BaseModel):
    ocr_used: bool = Field(
        description="True when at least one page was rasterised and read by the "
        "vision model rather than parsed from a native text layer."
    )
    extraction_method: str = Field(examples=["vision_llm"])
    model: str | None = Field(default=None, examples=["claude-opus-5"])
    pages_processed: int = Field(examples=[1])
    processed_at: datetime
    processing_time_ms: int = Field(examples=[2840])


class DocumentResult(BaseModel):
    """The mandatory structured response of specification section 5.2."""

    model_config = ConfigDict(from_attributes=True)

    document_name: str = Field(examples=["sample_invoice.pdf"])
    document_type: DocumentType
    processing_status: ProcessingStatus
    overall_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    file_validation: FileValidation
    extracted_data: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "All extracted content. Scalar fields map to objects with `value`, "
            "`raw_value`, `confidence`, `page_number` and `evidence`. Invoices "
            "additionally carry `line_items`; statements carry `periods`, "
            "`line_items` (each with a value per period) and `key_figures`."
        ),
    )
    validation: ValidationResult
    processing_metadata: ProcessingMetadata


class DocumentSummary(BaseModel):
    """Row shape backing the dashboard list."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_name: str
    document_type: DocumentType
    processing_status: ProcessingStatus
    validation_status: ValidationStatus | None = None
    overall_confidence: float | None = None
    page_count: int | None = None
    ocr_used: bool
    processing_time_ms: int | None = None
    processed_at: datetime


class DocumentListResponse(BaseModel):
    total: int
    count: int
    limit: int
    offset: int
    items: list[DocumentSummary]


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str
    environment: str
    database: str = Field(
        description="'connected' or 'unavailable'.", examples=["connected"]
    )
    extraction_configured: bool = Field(
        description="False when ANTHROPIC_API_KEY is not set; uploads will be rejected."
    )
    model: str | None = None
    timestamp: datetime


class ErrorBody(BaseModel):
    code: str = Field(examples=["UNSUPPORTED_FILE_TYPE"])
    message: str = Field(examples=["Only PDF / JPG / PNG documents are supported."])
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """The error envelope of specification section 5.3."""

    error: ErrorBody
