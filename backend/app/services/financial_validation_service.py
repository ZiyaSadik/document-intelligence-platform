"""Deterministic financial reconciliation.

No model is involved here, by design. The whole point of the validation section
is that a reviewer can check the arithmetic themselves, so every check reports
the formula it applied, the operands it used, what it computed, what the
document reported, and the difference.

Two rules govern the whole module:

* **A missing operand yields NOT_APPLICABLE, never a substituted zero.**
  Treating an absent figure as nothing would manufacture a reconciliation the
  source document does not support.
* **Every check runs once per comparative period.** Statements in this domain
  print two or more years side by side and each must reconcile on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.schemas.document import ValidationCheck, ValidationResult
from app.schemas.enums import CheckStatus, DocumentType, ValidationStatus
from app.schemas.extraction import (
    InvoiceExtraction,
    StatementExtraction,
    StatementLineItem,
)
from app.services.concept_matcher import (
    CONCEPTS_BY_TYPE,
    candidate_rows,
    normalise_label,
    resolve_concepts,
    value_for_period,
)
from app.utils.numbers import parse_money, safe_sum, variance, within_tolerance

logger = get_logger(__name__)

Operands = dict[str, float | None]


@dataclass(frozen=True)
class Tolerance:
    absolute: float
    relative: float

    @classmethod
    def from_settings(cls) -> "Tolerance":
        return cls(
            absolute=settings.validation_abs_tolerance,
            relative=settings.validation_rel_tolerance,
        )


class FinancialValidationService:
    """Runs the reconciliations required for each document type."""

    def __init__(self, tolerance: Tolerance | None = None) -> None:
        self._tolerance = tolerance or Tolerance.from_settings()

    # -- public API --------------------------------------------------------
    def validate(
        self,
        extraction: InvoiceExtraction | StatementExtraction,
        *,
        document_type: DocumentType,
    ) -> ValidationResult:
        if document_type is DocumentType.INVOICE:
            checks = self._validate_invoice(extraction)  # type: ignore[arg-type]
        else:
            checks = self._validate_statement(
                extraction,  # type: ignore[arg-type]
                document_type=document_type,
            )

        failures = [c for c in checks if c.status is CheckStatus.FAIL]
        issues = [
            f"{c.name}"
            + (f" [{c.period}]" if c.period else "")
            + f": {c.formula} = {c.calculated_value} but the document reports "
            f"{c.reported_value} (variance {c.variance})"
            for c in failures
        ]

        result = ValidationResult(
            checks=checks,
            overall_status=(
                ValidationStatus.FAIL if failures else ValidationStatus.PASS
            ),
            issues=issues,
            tolerance={
                "absolute": self._tolerance.absolute,
                "relative": self._tolerance.relative,
            },
        )
        logger.info(
            "financial validation complete",
            extra={
                "document_type": document_type.value,
                "checks": len(checks),
                "failed": len(failures),
                "not_applicable": sum(
                    1 for c in checks if c.status is CheckStatus.NOT_APPLICABLE
                ),
            },
        )
        return result

    # -- check construction ------------------------------------------------
    def _check(
        self,
        *,
        name: str,
        description: str,
        formula: str,
        operands: Operands,
        calculated: float | None,
        reported: float | None,
        period: str | None = None,
    ) -> ValidationCheck:
        """Assemble one check, deciding PASS / FAIL / NOT_APPLICABLE."""
        if calculated is None or reported is None:
            return ValidationCheck(
                name=name,
                description=description,
                formula=formula,
                period=period,
                operands=operands,
                calculated_value=calculated,
                reported_value=reported,
                variance=None,
                status=CheckStatus.NOT_APPLICABLE,
            )

        delta = variance(calculated, reported)
        passed = within_tolerance(
            calculated,
            reported,
            abs_tolerance=self._tolerance.absolute,
            rel_tolerance=self._tolerance.relative,
        )
        return ValidationCheck(
            name=name,
            description=description,
            formula=formula,
            period=period,
            operands=operands,
            calculated_value=round(calculated, 4),
            reported_value=round(reported, 4),
            variance=delta,
            status=CheckStatus.PASS if passed else CheckStatus.FAIL,
        )

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------
    def _validate_invoice(self, data: InvoiceExtraction) -> list[ValidationCheck]:
        def money(field_name: str) -> float | None:
            named = getattr(data, field_name, None)
            return parse_money(named.raw_value) if named else None

        subtotal = money("subtotal")
        tax = money("tax_amount")
        discount = money("discount")
        rounding = money("rounding_adjustment")
        total = money("total_amount")
        cash = money("cash_paid")
        change = money("change")

        checks: list[ValidationCheck] = []

        # 1. Each line: quantity x unit price = line total.
        for index, item in enumerate(data.line_items, start=1):
            quantity = parse_money(item.quantity)
            unit_price = parse_money(item.unit_price)
            amount = parse_money(item.amount)
            calculated = (
                quantity * unit_price
                if quantity is not None and unit_price is not None
                else None
            )
            checks.append(
                self._check(
                    name=f"line_item_{index}_amount_check",
                    description=(
                        "Quantity multiplied by unit price should equal the "
                        f"line total for '{(item.description or '').strip()[:60]}'."
                    ),
                    formula="quantity * unit_price",
                    operands={"quantity": quantity, "unit_price": unit_price},
                    calculated=calculated,
                    reported=amount,
                )
            )

        # 2. Line totals should reconcile to the reported subtotal (or, when no
        #    subtotal is printed, to the total - common on retail receipts).
        line_amounts = [parse_money(item.amount) for item in data.line_items]
        line_sum = safe_sum(line_amounts) if line_amounts else None
        reported_for_lines = subtotal if subtotal is not None else total
        checks.append(
            self._check(
                name="line_items_subtotal_check",
                description=(
                    "The sum of all line totals should reconcile to the "
                    "reported subtotal."
                ),
                formula="sum(line_item.amount)",
                operands={
                    f"line_{i}": value for i, value in enumerate(line_amounts, start=1)
                },
                calculated=line_sum,
                reported=reported_for_lines,
            )
        )

        # 3. Subtotal + tax - discount = total. When the document says tax is
        #    already inside the printed total, adding it again would be wrong,
        #    so the check becomes subtotal-only.
        tax_inclusive = data.tax_inclusive is True
        if tax_inclusive:
            operands: Operands = {"subtotal": subtotal, "discount": discount}
            calculated = (
                subtotal - (discount or 0.0) + (rounding or 0.0)
                if subtotal is not None
                else None
            )
            formula = "subtotal - discount (+ rounding); tax already included"
        else:
            operands = {"subtotal": subtotal, "tax_amount": tax, "discount": discount}
            calculated = (
                subtotal + (tax or 0.0) - (discount or 0.0) + (rounding or 0.0)
                if subtotal is not None
                else None
            )
            formula = "subtotal + tax_amount - discount (+ rounding)"
        if rounding is not None:
            operands["rounding_adjustment"] = rounding
        checks.append(
            self._check(
                name="invoice_total_check",
                description=(
                    "The taxable amount plus tax, less any discount, should "
                    "equal the reported total."
                ),
                formula=formula,
                operands=operands,
                calculated=calculated,
                reported=total,
            )
        )

        # 4. Cash tendered less the total equals the change given.
        checks.append(
            self._check(
                name="cash_change_check",
                description=(
                    "Cash paid less the total amount should equal the change given."
                ),
                formula="cash_paid - total_amount",
                operands={"cash_paid": cash, "total_amount": total},
                calculated=(
                    cash - total if cash is not None and total is not None else None
                ),
                reported=change,
            )
        )
        return checks

    # ------------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------------
    def _validate_statement(
        self, data: StatementExtraction, *, document_type: DocumentType
    ) -> list[ValidationCheck]:
        periods = [p.label for p in data.periods] or _implied_periods(data.line_items)
        if not periods:
            return []

        rules = CONCEPTS_BY_TYPE[document_type.value]
        resolved = resolve_concepts(rules, candidate_rows(data.line_items))

        logger.debug(
            "statement concepts resolved",
            extra={
                "document_type": document_type.value,
                "resolved": {k: data.line_items[v].label for k, v in resolved.items()},
            },
        )

        builder: Callable[..., list[ValidationCheck]] = {
            DocumentType.BALANCE_SHEET: self._balance_sheet_checks,
            DocumentType.PROFIT_AND_LOSS: self._profit_and_loss_checks,
            DocumentType.CASH_FLOW_STATEMENT: self._cash_flow_checks,
        }[document_type]

        checks: list[ValidationCheck] = []
        for period in periods:
            values = {
                concept: value_for_period(data.line_items[index], period)
                for concept, index in resolved.items()
            }
            checks.extend(builder(values, period=period, data=data))
        return checks

    def _balance_sheet_checks(
        self, values: dict[str, float | None], *, period: str, data: StatementExtraction
    ) -> list[ValidationCheck]:
        liabilities = values.get("total_capital_and_liabilities")
        assets = values.get("total_assets")

        checks = [
            self._check(
                name="balance_sheet_equation_check",
                description=(
                    "Total capital and liabilities should equal total assets."
                ),
                formula="total_capital_and_liabilities = total_assets",
                period=period,
                operands={
                    "total_capital_and_liabilities": liabilities,
                    "total_assets": assets,
                },
                calculated=liabilities,
                reported=assets,
            )
        ]
        checks.extend(self._section_sum_checks(data, period=period))
        return checks

    def _section_sum_checks(
        self, data: StatementExtraction, *, period: str
    ) -> list[ValidationCheck]:
        """Each section's component rows should add up to its own total row.

        Derived from the document's own structure - section headings and total
        flags - rather than from a fixed list of captions, so it works on a
        statement whose captions nobody anticipated.
        """
        by_section: dict[str, list[StatementLineItem]] = {}
        for item in data.line_items:
            key = (item.section or "").strip()
            if not key:
                continue
            by_section.setdefault(key, []).append(item)

        checks: list[ValidationCheck] = []
        for section, items in by_section.items():
            totals = [i for i in items if i.is_total]
            components = [i for i in items if not i.is_total]
            # One unambiguous total and enough components to be worth checking.
            if len(totals) != 1 or len(components) < 2:
                continue

            component_values = [value_for_period(i, period) for i in components]
            checks.append(
                self._check(
                    name=f"section_sum_check::{normalise_label(section)[:48]}",
                    description=(
                        f"The component rows of '{section}' should sum to its "
                        f"reported total ('{totals[0].label}')."
                    ),
                    formula=f"sum of {len(components)} component rows in '{section}'",
                    period=period,
                    operands={
                        (i.label or f"row_{n}")[:60]: v
                        for n, (i, v) in enumerate(
                            zip(components, component_values), start=1
                        )
                    },
                    calculated=safe_sum(component_values),
                    reported=value_for_period(totals[0], period),
                )
            )
        return checks

    def _profit_and_loss_checks(
        self, values: dict[str, float | None], *, period: str, data: StatementExtraction
    ) -> list[ValidationCheck]:
        interest_earned = values.get("interest_earned")
        other_income = values.get("other_income")
        total_income = values.get("total_income")

        interest_expended = values.get("interest_expended")
        operating_expenses = values.get("operating_expenses")
        provisions = values.get("provisions_and_contingencies")
        total_expenditure = values.get("total_expenditure")

        pbmi = values.get("profit_before_minority_interest")
        minority = values.get("minority_interest")
        attributable = values.get("net_profit_attributable_to_group")

        brought_forward = values.get("profit_brought_forward")
        appropriation = values.get("total_available_for_appropriation")

        return [
            self._check(
                name="total_income_check",
                description="Interest earned plus other income equals total income.",
                formula="interest_earned + other_income",
                period=period,
                operands={
                    "interest_earned": interest_earned,
                    "other_income": other_income,
                },
                calculated=safe_sum([interest_earned, other_income]),
                reported=total_income,
            ),
            self._check(
                name="total_expenditure_check",
                description=(
                    "Interest expended plus operating expenses plus provisions "
                    "and contingencies equals total expenditure."
                ),
                formula="interest_expended + operating_expenses + provisions_and_contingencies",
                period=period,
                operands={
                    "interest_expended": interest_expended,
                    "operating_expenses": operating_expenses,
                    "provisions_and_contingencies": provisions,
                },
                calculated=safe_sum([interest_expended, operating_expenses, provisions]),
                reported=total_expenditure,
            ),
            self._check(
                name="net_profit_before_minority_interest_check",
                description=(
                    "Total income less total expenditure equals consolidated "
                    "net profit before minority interest."
                ),
                formula="total_income - total_expenditure",
                period=period,
                operands={
                    "total_income": total_income,
                    "total_expenditure": total_expenditure,
                },
                calculated=(
                    total_income - total_expenditure
                    if total_income is not None and total_expenditure is not None
                    else None
                ),
                reported=pbmi,
            ),
            self._check(
                name="net_profit_attributable_to_group_check",
                description=(
                    "Profit before minority interest less minority interest "
                    "equals the profit attributable to the group."
                ),
                formula="profit_before_minority_interest - minority_interest",
                period=period,
                operands={
                    "profit_before_minority_interest": pbmi,
                    "minority_interest": minority,
                },
                calculated=(
                    pbmi - minority
                    if pbmi is not None and minority is not None
                    else None
                ),
                reported=attributable,
            ),
            self._check(
                name="appropriation_check",
                description=(
                    "Current-year profit plus profit brought forward equals the "
                    "total available for appropriation."
                ),
                formula="net_profit_attributable_to_group + profit_brought_forward",
                period=period,
                operands={
                    "net_profit_attributable_to_group": attributable,
                    "profit_brought_forward": brought_forward,
                },
                calculated=safe_sum([attributable, brought_forward]),
                reported=appropriation,
            ),
        ]

    def _cash_flow_checks(
        self, values: dict[str, float | None], *, period: str, data: StatementExtraction
    ) -> list[ValidationCheck]:
        operating = values.get("net_cash_operating")
        investing = values.get("net_cash_investing")
        financing = values.get("net_cash_financing")
        fx = values.get("fx_adjustment")
        net_increase = values.get("net_increase_in_cash")
        opening = values.get("opening_cash")
        closing = values.get("closing_cash")
        acquired = values.get("cash_acquired_on_amalgamation")

        # The FX and amalgamation lines are genuinely optional: most years do
        # not print them. Absent means zero contribution here, unlike the core
        # operands whose absence makes the check meaningless.
        core = [operating, investing, financing]
        net_calculated = (
            sum(v for v in core if v is not None) + (fx or 0.0)
            if all(v is not None for v in core)
            else None
        )
        closing_calculated = (
            opening + net_increase + (acquired or 0.0)
            if opening is not None and net_increase is not None
            else None
        )

        return [
            self._check(
                name="net_change_in_cash_check",
                description=(
                    "Operating, investing and financing cash flows plus any "
                    "translation adjustment equal the net increase in cash."
                ),
                formula=(
                    "net_cash_operating + net_cash_investing + net_cash_financing "
                    "+ fx_adjustment"
                ),
                period=period,
                operands={
                    "net_cash_operating": operating,
                    "net_cash_investing": investing,
                    "net_cash_financing": financing,
                    "fx_adjustment": fx,
                },
                calculated=net_calculated,
                reported=net_increase,
            ),
            self._check(
                name="closing_cash_check",
                description=(
                    "Opening cash plus the net increase, plus any cash acquired "
                    "on amalgamation, equals closing cash."
                ),
                formula=(
                    "opening_cash + net_increase_in_cash + cash_acquired_on_amalgamation"
                ),
                period=period,
                operands={
                    "opening_cash": opening,
                    "net_increase_in_cash": net_increase,
                    "cash_acquired_on_amalgamation": acquired,
                },
                calculated=closing_calculated,
                reported=closing,
            ),
        ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _implied_periods(items: list[StatementLineItem]) -> list[str]:
    """Recover period labels from the cells when the header list is missing."""
    seen: list[str] = []
    for item in items:
        for cell in item.values:
            if cell.period_label and cell.period_label not in seen:
                seen.append(cell.period_label)
    return seen
