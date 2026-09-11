"""Schemas describing what the vision model returns.

Design note -- every monetary figure crosses this boundary as a **string,
verbatim as printed** (``"(1,234.56)"``, ``"1,23,456.78"``, ``"-"``). The model
is asked to read, not to interpret: sign conventions, digit grouping and
"no value" tokens are resolved afterwards by :func:`app.utils.numbers.parse_money`.

That split is deliberate. It keeps numeric interpretation deterministic and
unit-testable, it preserves the parenthesis-means-negative convention the
specification calls out, and it lets the confidence model check that a figure
really appears in the evidence text the model cited.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    """Base config: reject unknown keys so schema drift surfaces loudly."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class NamedValue(_Strict):
    """A document field that has no dedicated slot in the schema."""

    label: str = Field(description="The field label exactly as printed.")
    raw_value: str | None = Field(
        default=None, description="The value exactly as printed, or null if unreadable."
    )
    source_text: str | None = Field(
        default=None, description="The verbatim line of text this came from."
    )
    page_number: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# Invoices / receipts
# ---------------------------------------------------------------------------
class InvoiceLineItem(_Strict):
    description: str | None = None
    quantity: str | None = Field(default=None, description="As printed, e.g. '2'.")
    unit_price: str | None = Field(default=None, description="As printed.")
    amount: str | None = Field(default=None, description="Line total, as printed.")
    page_number: int | None = Field(default=None, ge=1)


class InvoiceExtraction(_Strict):
    """Every field commonly present on an invoice or retail receipt."""

    invoice_number: NamedValue | None = None
    invoice_date: NamedValue | None = None
    due_date: NamedValue | None = None
    purchase_order_number: NamedValue | None = None

    vendor_name: NamedValue | None = None
    vendor_address: NamedValue | None = None
    vendor_tax_id: NamedValue | None = Field(
        default=None, description="GST / VAT / TIN registration number of the seller."
    )
    vendor_contact: NamedValue | None = None

    customer_name: NamedValue | None = None
    customer_address: NamedValue | None = None

    currency: NamedValue | None = Field(
        default=None, description="ISO code or symbol as printed, e.g. 'RM', 'USD'."
    )
    subtotal: NamedValue | None = None
    discount: NamedValue | None = None
    tax_amount: NamedValue | None = None
    tax_rate: NamedValue | None = None
    rounding_adjustment: NamedValue | None = None
    total_amount: NamedValue | None = None
    cash_paid: NamedValue | None = None
    change: NamedValue | None = None
    payment_method: NamedValue | None = None

    tax_inclusive: bool | None = Field(
        default=None,
        description=(
            "True when the printed total already includes tax (e.g. the receipt "
            "says 'GST inclusive'), false when tax is added on top, null if unclear."
        ),
    )

    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    additional_fields: list[NamedValue] = Field(
        default_factory=list,
        description="Every other visible labelled value not covered above.",
    )


# ---------------------------------------------------------------------------
# Financial statements (balance sheet / P&L / cash flow)
# ---------------------------------------------------------------------------
class StatementPeriod(_Strict):
    label: str = Field(description="Column heading as printed, e.g. 'March 31, 2022'.")
    period_end_date: str | None = Field(
        default=None, description="ISO date (YYYY-MM-DD) if determinable, else null."
    )


class PeriodValue(_Strict):
    """One cell: the value of a line item under one period column."""

    period_label: str = Field(description="Must match a label in `periods`.")
    raw_value: str | None = Field(
        default=None,
        description="Exactly as printed, keeping parentheses. Null if blank/unreadable.",
    )


class StatementLineItem(_Strict):
    label: str = Field(description="Row label exactly as printed.")
    section: str | None = Field(
        default=None,
        description=(
            "The section heading this row sits under, e.g. 'Assets', "
            "'Cash flows from operating activities'."
        ),
    )
    is_total: bool = Field(
        default=False, description="True for subtotal/total rows."
    )
    schedule_reference: str | None = Field(
        default=None, description="Schedule or note number if printed."
    )
    values: list[PeriodValue] = Field(default_factory=list)
    source_text: str | None = None
    page_number: int | None = Field(default=None, ge=1)


class StatementExtraction(_Strict):
    """Shared shape for all three statement types."""

    entity_name: NamedValue | None = None
    statement_title: NamedValue | None = None
    currency: NamedValue | None = Field(
        default=None, description="e.g. 'INR', 'USD'. Read it from the document."
    )
    units: NamedValue | None = Field(
        default=None,
        description="Scale note as printed, e.g. 'in crore', 'in thousands'.",
    )
    periods: list[StatementPeriod] = Field(default_factory=list)
    line_items: list[StatementLineItem] = Field(default_factory=list)
    header_fields: list[NamedValue] = Field(
        default_factory=list,
        description="Other header information: registration numbers, dates, notes.",
    )


EXTRACTION_MODEL_BY_TYPE: dict[str, type[_Strict]] = {
    "invoice": InvoiceExtraction,
    "balance_sheet": StatementExtraction,
    "profit_and_loss": StatementExtraction,
    "cash_flow_statement": StatementExtraction,
}
