"""Turns a raw model extraction into the published ``extracted_data`` payload.

Three things happen here:

1. **Interpretation.** Every figure the model transcribed as a string is turned
   into a number by :func:`~app.utils.numbers.parse_money`, so parentheses
   become negatives and digit grouping disappears - deterministically, in one
   place, rather than inside the model.
2. **Grounding.** Each value is emitted with its page number and the line of
   text it came from, so an evaluator can trace it back to the document.
3. **Key figures.** Statements additionally expose a per-period dictionary of
   the named concepts the specification asks for (``total_assets``,
   ``operating_cash_flow`` and so on) alongside the full line-item table, so
   those values are addressable without re-reading every row.
"""

from __future__ import annotations

from typing import Any

from app.schemas.document import Evidence, ExtractedField, ValidationCheck
from app.schemas.enums import CheckStatus, DocumentType
from app.schemas.extraction import (
    InvoiceExtraction,
    NamedValue,
    StatementExtraction,
    StatementLineItem,
)
from app.services.concept_matcher import (
    CONCEPTS_BY_TYPE,
    candidate_rows,
    resolve_concepts,
    value_for_period,
)
from app.services.confidence_service import (
    build_field_verdicts,
    overall_confidence,
    score_field,
)
from app.utils.numbers import parse_money

#: Invoice fields whose value is monetary and must be parsed to a number.
INVOICE_MONETARY_FIELDS = (
    "subtotal",
    "discount",
    "tax_amount",
    "rounding_adjustment",
    "total_amount",
    "cash_paid",
    "change",
)

#: Invoice fields carried through as text.
INVOICE_TEXT_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "purchase_order_number",
    "vendor_name",
    "vendor_address",
    "vendor_tax_id",
    "vendor_contact",
    "customer_name",
    "customer_address",
    "currency",
    "tax_rate",
    "payment_method",
)

#: The concepts each statement type must surface by name, per specification
#: section 2, mapped onto the internal concept keys.
KEY_FIGURE_ALIASES: dict[DocumentType, dict[str, str]] = {
    DocumentType.BALANCE_SHEET: {
        "total_assets": "total_assets",
        "total_liabilities": "total_capital_and_liabilities",
        "total_equity": "total_equity",
    },
    DocumentType.PROFIT_AND_LOSS: {
        "revenue": "interest_earned",
        "other_income": "other_income",
        "total_income": "total_income",
        "operating_expenses": "operating_expenses",
        "total_expenditure": "total_expenditure",
        "net_profit": "net_profit_attributable_to_group",
        "profit_before_minority_interest": "profit_before_minority_interest",
    },
    DocumentType.CASH_FLOW_STATEMENT: {
        "operating_cash_flow": "net_cash_operating",
        "investing_cash_flow": "net_cash_investing",
        "financing_cash_flow": "net_cash_financing",
        "net_change_in_cash": "net_increase_in_cash",
        "opening_cash": "opening_cash",
        "closing_cash": "closing_cash",
    },
}


def build_extracted_data(
    extraction: InvoiceExtraction | StatementExtraction,
    *,
    document_type: DocumentType,
    checks: list[ValidationCheck],
    used_ocr: bool,
) -> tuple[dict[str, Any], float | None]:
    """Return the published ``extracted_data`` block and overall confidence."""
    verdicts = build_field_verdicts(checks)
    if document_type is DocumentType.INVOICE:
        return _build_invoice(
            extraction,  # type: ignore[arg-type]
            verdicts=verdicts,
            used_ocr=used_ocr,
        )
    return _build_statement(
        extraction,  # type: ignore[arg-type]
        document_type=document_type,
        verdicts=verdicts,
        used_ocr=used_ocr,
    )


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
def _build_invoice(
    data: InvoiceExtraction,
    *,
    verdicts: dict[str, CheckStatus],
    used_ocr: bool,
) -> tuple[dict[str, Any], float | None]:
    payload: dict[str, Any] = {}
    scores: list[float | None] = []

    for name in INVOICE_TEXT_FIELDS:
        field = _field_from_named(
            getattr(data, name, None),
            numeric=False,
            used_ocr=used_ocr,
            verdict=verdicts.get(name),
        )
        payload[name] = field.model_dump()
        scores.append(field.confidence)

    for name in INVOICE_MONETARY_FIELDS:
        field = _field_from_named(
            getattr(data, name, None),
            numeric=True,
            used_ocr=used_ocr,
            verdict=verdicts.get(name),
        )
        payload[name] = field.model_dump()
        scores.append(field.confidence)

    payload["tax_inclusive"] = data.tax_inclusive

    payload["line_items"] = [
        {
            "description": item.description,
            "quantity": parse_money(item.quantity),
            "unit_price": parse_money(item.unit_price),
            "amount": parse_money(item.amount),
            "raw": {
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "amount": item.amount,
            },
            "page_number": item.page_number,
        }
        for item in data.line_items
    ]

    payload["additional_fields"] = [
        _named_to_dict(item, used_ocr=used_ocr) for item in data.additional_fields
    ]
    scores.extend(
        entry["confidence"] for entry in payload["additional_fields"]
    )

    return payload, overall_confidence(scores)


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------
def _build_statement(
    data: StatementExtraction,
    *,
    document_type: DocumentType,
    verdicts: dict[str, CheckStatus],
    used_ocr: bool,
) -> tuple[dict[str, Any], float | None]:
    payload: dict[str, Any] = {}
    scores: list[float | None] = []

    for name in ("entity_name", "statement_title", "currency", "units"):
        field = _field_from_named(
            getattr(data, name, None), numeric=False, used_ocr=used_ocr
        )
        payload[name] = field.model_dump()
        scores.append(field.confidence)

    periods = [p.label for p in data.periods]
    payload["periods"] = [
        {"label": p.label, "period_end_date": p.period_end_date} for p in data.periods
    ]

    line_items: list[dict[str, Any]] = []
    for item in data.line_items:
        values: dict[str, Any] = {}
        for cell in item.values:
            parsed = parse_money(cell.raw_value)
            confidence = score_field(
                value=parsed,
                source_text=item.source_text,
                page_number=item.page_number,
                used_ocr=used_ocr,
                verdict=verdicts.get(item.label),
            )
            scores.append(confidence)
            values[cell.period_label] = {
                "value": parsed,
                "raw_value": cell.raw_value,
                "confidence": confidence,
            }
        line_items.append(
            {
                "label": item.label,
                "section": item.section,
                "is_total": item.is_total,
                "schedule_reference": item.schedule_reference,
                "page_number": item.page_number,
                "evidence": {
                    "source_text": item.source_text,
                    "page_number": item.page_number,
                },
                "values": values,
            }
        )
    payload["line_items"] = line_items

    payload["key_figures"] = _key_figures(
        data, document_type=document_type, periods=periods
    )
    payload["header_fields"] = [
        _named_to_dict(item, used_ocr=used_ocr) for item in data.header_fields
    ]
    scores.extend(entry["confidence"] for entry in payload["header_fields"])

    return payload, overall_confidence(scores)


def _key_figures(
    data: StatementExtraction,
    *,
    document_type: DocumentType,
    periods: list[str],
) -> dict[str, dict[str, Any]]:
    """Per-period dictionary of the specification's named minimum fields."""
    rules = CONCEPTS_BY_TYPE[document_type.value]
    resolved = resolve_concepts(rules, candidate_rows(data.line_items))
    aliases = KEY_FIGURE_ALIASES[document_type]

    figures: dict[str, dict[str, Any]] = {}
    for period in periods:
        entry: dict[str, Any] = {}
        for public_name, concept in aliases.items():
            index = resolved.get(concept)
            if index is None:
                entry[public_name] = {"value": None, "source_label": None}
                continue
            item = data.line_items[index]
            entry[public_name] = {
                "value": value_for_period(item, period),
                "source_label": item.label,
                "page_number": item.page_number,
            }
        figures[period] = entry
    return figures


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------
def _field_from_named(
    named: NamedValue | None,
    *,
    numeric: bool,
    used_ocr: bool,
    verdict: CheckStatus | None = None,
) -> ExtractedField:
    """Convert a model-returned field into the published shape."""
    if named is None:
        return ExtractedField(value=None, raw_value=None, confidence=None)

    value: Any = parse_money(named.raw_value) if numeric else named.raw_value
    return ExtractedField(
        value=value,
        raw_value=named.raw_value,
        confidence=score_field(
            value=value,
            source_text=named.source_text,
            page_number=named.page_number,
            used_ocr=used_ocr,
            verdict=verdict,
        ),
        page_number=named.page_number,
        evidence=Evidence(
            source_text=named.source_text, page_number=named.page_number
        ),
    )


def _named_to_dict(named: NamedValue, *, used_ocr: bool) -> dict[str, Any]:
    """An unanticipated labelled value; numeric if it parses, text otherwise."""
    numeric = parse_money(named.raw_value)
    value: Any = numeric if numeric is not None else named.raw_value
    return {
        "label": named.label,
        "value": value,
        "raw_value": named.raw_value,
        "confidence": score_field(
            value=value,
            source_text=named.source_text,
            page_number=named.page_number,
            used_ocr=used_ocr,
        ),
        "page_number": named.page_number,
        "evidence": {
            "source_text": named.source_text,
            "page_number": named.page_number,
        },
    }


def missing_field_names(extracted_data: dict[str, Any]) -> list[str]:
    """Names of scalar fields the document did not report.

    Surfaced in the response and highlighted in the dashboard so an evaluator
    can see at a glance what could not be read.
    """
    missing: list[str] = []
    for name, entry in extracted_data.items():
        if isinstance(entry, dict) and "value" in entry and entry.get("value") is None:
            missing.append(name)
    return sorted(missing)


def statement_row_count(extracted_data: dict[str, Any]) -> int:
    rows = extracted_data.get("line_items")
    return len(rows) if isinstance(rows, list) else 0
