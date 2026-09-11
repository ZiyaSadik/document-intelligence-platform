"""Builders for realistic extraction payloads used across the tests.

The figures mirror the shape of a consolidated Indian-bank statement (two
comparative years, values in crore, negatives in parentheses) because that is
what the reconciliation rules in the specification are written against.
"""

from __future__ import annotations

from app.schemas.extraction import (
    InvoiceExtraction,
    InvoiceLineItem,
    NamedValue,
    PeriodValue,
    StatementExtraction,
    StatementLineItem,
    StatementPeriod,
)

CURRENT = "March 31, 2022"
PRIOR = "March 31, 2021"


def named(value: str | None, *, source: str | None = None, page: int = 1):
    if value is None and source is None:
        return None
    return NamedValue(
        label="", raw_value=value, source_text=source, page_number=page
    )


def row(
    label: str,
    current: str | None,
    prior: str | None = None,
    *,
    section: str | None = None,
    is_total: bool = False,
    page: int = 1,
) -> StatementLineItem:
    return StatementLineItem(
        label=label,
        section=section,
        is_total=is_total,
        values=[
            PeriodValue(period_label=CURRENT, raw_value=current),
            PeriodValue(period_label=PRIOR, raw_value=prior),
        ],
        source_text=f"{label} {current or '-'} {prior or '-'}",
        page_number=page,
    )


def statement(rows: list[StatementLineItem], **kwargs) -> StatementExtraction:
    return StatementExtraction(
        entity_name=named("Sample Bank Limited"),
        statement_title=named(kwargs.get("title", "Consolidated Statement")),
        currency=named("INR"),
        units=named("in crore"),
        periods=[
            StatementPeriod(label=CURRENT, period_end_date="2022-03-31"),
            StatementPeriod(label=PRIOR, period_end_date="2021-03-31"),
        ],
        line_items=rows,
    )


# ---------------------------------------------------------------------------
# Balance sheet
# ---------------------------------------------------------------------------
def balance_sheet(*, balanced: bool = True) -> StatementExtraction:
    assets_total = "2,466,081.47" if balanced else "2,400,000.00"
    return statement(
        [
            row("Capital and Liabilities", None, None),  # heading: no figures
            row("Capital", "554.55", "551.28", section="Capital and Liabilities"),
            row("Reserves and surplus", "246,771.62", "209,258.90",
                section="Capital and Liabilities"),
            row("Deposits", "1,559,217.44", "1,335,060.22",
                section="Capital and Liabilities"),
            row("Borrowings", "1,84,817.21", "1,35,487.32",
                section="Capital and Liabilities"),
            row("Other liabilities and provisions", "474,720.65", "395,000.00",
                section="Capital and Liabilities"),
            row("Total Capital and Liabilities", "2,466,081.47", "2,075,357.72",
                section="Capital and Liabilities", is_total=True),
            row("Assets", None, None),
            row("Cash and balances with Reserve Bank of India", "129,995.64",
                "97,340.74", section="Assets"),
            row("Investments", "455,535.69", "443,728.29", section="Assets"),
            row("Advances", "1,420,942.28", "1,185,283.52", section="Assets"),
            row("Fixed assets", "6,083.67", "4,909.32", section="Assets"),
            row("Other assets", "453,524.19", "344,095.85", section="Assets"),
            row("Total Assets", assets_total, "2,075,357.72",
                section="Assets", is_total=True),
        ],
        title="Consolidated Balance Sheet",
    )


# ---------------------------------------------------------------------------
# Profit and loss
# ---------------------------------------------------------------------------
def profit_and_loss(*, consistent: bool = True) -> StatementExtraction:
    total_income = "167,695.40" if consistent else "170,000.00"
    return statement(
        [
            row("I. Income", None, None),
            row("Interest earned", "135,936.41", "128,552.39", section="Income"),
            row("Other income", "31,758.99", "27,332.61", section="Income"),
            row("Total Income", total_income, "155,885.00",
                section="Income", is_total=True),
            row("II. Expenditure", None, None),
            row("Interest expended", "58,584.33", "58,626.14", section="Expenditure"),
            row("Operating expenses", "37,442.19", "33,036.06",
                section="Expenditure"),
            row("Provisions and contingencies", "20,893.53", "21,450.24",
                section="Expenditure"),
            row("Total Expenditure", "116,920.05", "113,112.44",
                section="Expenditure", is_total=True),
            row("Consolidated net profit for the year before minority interest",
                "50,775.35", "42,772.56", section="Profit", is_total=True),
            row("Minority interest", "1,124.11", "980.42", section="Profit"),
            row("Consolidated net profit for the year attributable to the group",
                "49,651.24", "41,792.14", section="Profit", is_total=True),
            row("Profit brought forward", "70,000.00", "55,000.00",
                section="Appropriations"),
            row("Total available for appropriation", "119,651.24", "96,792.14",
                section="Appropriations", is_total=True),
        ],
        title="Consolidated Profit and Loss Account",
    )


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------
def cash_flow(*, consistent: bool = True, with_fx: bool = True) -> StatementExtraction:
    closing = "153,146.62" if consistent else "160,000.00"
    rows = [
        row("Cash flows from operating activities:", None, None),
        row("Consolidated profit before income tax", "50,775.24", "42,772.58",
            section="Cash flows from operating activities"),
        row("Direct taxes paid (net of refunds)", "(14,838.16)", "(13,021.45)",
            section="Cash flows from operating activities"),
        row("Net cash flow (used in) / from operating activities",
            "(11,959.57)", "42,476.45",
            section="Cash flows from operating activities", is_total=True),
        row("Cash flows from investing activities:", None, None),
        row("Purchase of fixed assets", "(2,236.24)", "(1,696.15)",
            section="Cash flows from investing activities"),
        row("Net cash flow used in investing activities", "(2,110.55)", "(1,510.98)",
            section="Cash flows from investing activities", is_total=True),
        row("Cash flows from financing activities:", None, None),
        row("Net cash flow from financing activities", "45,205.11", "10,442.30",
            section="Cash flows from financing activities", is_total=True),
    ]
    if with_fx:
        rows.append(
            row("Effect of exchange rate changes on cash and cash equivalents",
                "121.50", "88.30", section="Cash and cash equivalents")
        )
    net_increase = "31,256.49" if with_fx else "31,134.99"
    rows += [
        row("Net increase / (decrease) in cash and cash equivalents",
            net_increase, "51,407.77", section="Cash and cash equivalents",
            is_total=True, page=2),
        row("Cash and cash equivalents at April 1, 2021", "121,890.13",
            "70,482.36", section="Cash and cash equivalents", page=2),
        row("Cash and cash equivalents at March 31, 2022", closing, "121,890.13",
            section="Cash and cash equivalents", is_total=True, page=2),
    ]
    return statement(rows, title="Consolidated Cash Flow Statement")


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
def invoice(*, consistent: bool = True, tax_inclusive: bool = False) -> InvoiceExtraction:
    total = "13,125.00" if consistent else "14,000.00"
    return InvoiceExtraction(
        invoice_number=named("INV-23891", source="Invoice No: INV-23891"),
        invoice_date=named("2026-08-15", source="Date: 15/08/2026"),
        vendor_name=named("ABC Technologies Sdn Bhd", source="ABC Technologies Sdn Bhd"),
        customer_name=named("Northwind Retail"),
        currency=named("USD", source="Amounts in USD"),
        subtotal=named("12,500.00", source="Subtotal 12,500.00"),
        tax_amount=named("625.00", source="Tax (5%) 625.00"),
        discount=named("0.00", source="Discount 0.00"),
        total_amount=named(total, source=f"Total Amount Due: USD {total}"),
        cash_paid=named("15,000.00", source="Cash 15,000.00"),
        change=named("1,875.00", source="Change 1,875.00"),
        tax_inclusive=tax_inclusive,
        line_items=[
            InvoiceLineItem(
                description="Service A", quantity="1", unit_price="12,500.00",
                amount="12,500.00", page_number=1,
            )
        ],
        additional_fields=[
            NamedValue(label="GST Reg No", raw_value="000123456789",
                       source_text="GST Reg No: 000123456789", page_number=1)
        ],
    )


def receipt_tax_inclusive() -> InvoiceExtraction:
    """A retail receipt where the printed total already contains GST."""
    return InvoiceExtraction(
        vendor_name=named("SYARIKAT PERNIAGAAN GIN KEE"),
        currency=named("RM"),
        subtotal=named("18.00", source="Total Sales (Inclusive of GST) 18.00"),
        tax_amount=named("1.02", source="GST @6% 1.02"),
        total_amount=named("18.00", source="Total 18.00"),
        cash_paid=named("20.00", source="Cash 20.00"),
        change=named("2.00", source="Change 2.00"),
        tax_inclusive=True,
        line_items=[
            InvoiceLineItem(description="KF MODELLING CLAY", quantity="2",
                            unit_price="9.00", amount="18.00", page_number=1)
        ],
    )
