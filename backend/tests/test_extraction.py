"""Concept matching, financial reconciliation, confidence and result shaping.

This is the arithmetic the specification is graded on, so the tests assert on
concrete numbers rather than "a check exists".
"""

from __future__ import annotations

import pytest

from app.schemas.enums import CheckStatus, DocumentType, ValidationStatus
from app.services.concept_matcher import (
    CASH_FLOW_CONCEPTS,
    PROFIT_AND_LOSS_CONCEPTS,
    candidate_rows,
    normalise_label,
    resolve_concepts,
)
from app.services.confidence_service import (
    build_field_verdicts,
    overall_confidence,
    score_field,
)
from app.services.financial_validation_service import (
    FinancialValidationService,
    Tolerance,
)
from app.services.result_builder import build_extracted_data, missing_field_names
from tests import factories
from tests.factories import CURRENT, PRIOR


@pytest.fixture
def service() -> FinancialValidationService:
    return FinancialValidationService(Tolerance(absolute=1.0, relative=0.005))


def by_name(checks, name, period=None):
    for check in checks:
        if check.name == name and (period is None or check.period == period):
            return check
    raise AssertionError(
        f"no check named {name!r} for period {period!r}; "
        f"got {[c.name for c in checks]}"
    )


# ---------------------------------------------------------------------------
# Label normalisation and concept matching
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Total Income", "total income"),
        ("II. Total Expenditure", "total expenditure"),
        ("(a) Reserves & surplus", "reserves and surplus"),
        ("Net cash flow (used in) / from operating activities",
         "net cash flow used in from operating activities"),
        ("  Cash   and cash  equivalents  ", "cash and cash equivalents"),
        (None, ""),
    ],
)
def test_normalise_label(raw, expected):
    assert normalise_label(raw) == expected


def test_total_income_does_not_match_other_income():
    data = factories.profit_and_loss()
    resolved = resolve_concepts(
        PROFIT_AND_LOSS_CONCEPTS, candidate_rows(data.line_items)
    )
    assert data.line_items[resolved["total_income"]].label == "Total Income"
    assert data.line_items[resolved["other_income"]].label == "Other income"


def test_section_heading_does_not_shadow_the_subtotal_row():
    """"Cash flows from operating activities:" reads like the concept but
    carries no figures, so the "Net cash flow..." row must win."""
    data = factories.cash_flow()
    resolved = resolve_concepts(CASH_FLOW_CONCEPTS, candidate_rows(data.line_items))
    label = data.line_items[resolved["net_cash_operating"]].label
    assert label == "Net cash flow (used in) / from operating activities"


def test_opening_and_closing_cash_are_distinguished():
    data = factories.cash_flow()
    resolved = resolve_concepts(CASH_FLOW_CONCEPTS, candidate_rows(data.line_items))
    assert "April 1" in data.line_items[resolved["opening_cash"]].label
    assert "March 31" in data.line_items[resolved["closing_cash"]].label


# ---------------------------------------------------------------------------
# Balance sheet
# ---------------------------------------------------------------------------
def test_balance_sheet_balances(service):
    result = service.validate(
        factories.balance_sheet(), document_type=DocumentType.BALANCE_SHEET
    )
    check = by_name(result.checks, "balance_sheet_equation_check", CURRENT)
    assert check.status is CheckStatus.PASS
    assert check.calculated_value == pytest.approx(2_466_081.47)
    assert check.variance == pytest.approx(0.0)
    assert result.overall_status is ValidationStatus.PASS


def test_balance_sheet_detects_an_imbalance(service):
    result = service.validate(
        factories.balance_sheet(balanced=False),
        document_type=DocumentType.BALANCE_SHEET,
    )
    check = by_name(result.checks, "balance_sheet_equation_check", CURRENT)
    assert check.status is CheckStatus.FAIL
    assert check.variance == pytest.approx(66_081.47)
    assert result.overall_status is ValidationStatus.FAIL
    assert result.issues


def test_balance_sheet_runs_every_period_independently(service):
    result = service.validate(
        factories.balance_sheet(balanced=False),
        document_type=DocumentType.BALANCE_SHEET,
    )
    # The prior year is untouched by the injected error and must still pass.
    assert by_name(result.checks, "balance_sheet_equation_check", PRIOR).status is (
        CheckStatus.PASS
    )


def test_section_components_sum_to_the_section_total(service):
    result = service.validate(
        factories.balance_sheet(), document_type=DocumentType.BALANCE_SHEET
    )
    section_checks = [c for c in result.checks if c.name.startswith("section_sum_check")]
    assert section_checks, "expected a component-sum check per statement section"
    assets = next(c for c in section_checks if "assets" in c.name)
    assert assets.status is CheckStatus.PASS
    # Indian digit grouping in "1,84,817.21" must have parsed correctly.
    liabilities = next(
        c for c in section_checks if "capital and liabilities" in c.name
    )
    assert liabilities.status is CheckStatus.PASS


# ---------------------------------------------------------------------------
# Profit and loss
# ---------------------------------------------------------------------------
def test_profit_and_loss_all_checks_pass(service):
    result = service.validate(
        factories.profit_and_loss(), document_type=DocumentType.PROFIT_AND_LOSS
    )
    assert result.overall_status is ValidationStatus.PASS

    income = by_name(result.checks, "total_income_check", CURRENT)
    assert income.calculated_value == pytest.approx(167_695.40)
    assert income.status is CheckStatus.PASS

    expenditure = by_name(result.checks, "total_expenditure_check", CURRENT)
    assert expenditure.calculated_value == pytest.approx(116_920.05)

    profit = by_name(result.checks, "net_profit_before_minority_interest_check", CURRENT)
    assert profit.calculated_value == pytest.approx(50_775.35)
    assert profit.status is CheckStatus.PASS

    group = by_name(result.checks, "net_profit_attributable_to_group_check", CURRENT)
    assert group.calculated_value == pytest.approx(49_651.24)

    appropriation = by_name(result.checks, "appropriation_check", CURRENT)
    assert appropriation.calculated_value == pytest.approx(119_651.24)


def test_profit_and_loss_detects_an_inconsistent_total(service):
    result = service.validate(
        factories.profit_and_loss(consistent=False),
        document_type=DocumentType.PROFIT_AND_LOSS,
    )
    assert by_name(result.checks, "total_income_check", CURRENT).status is CheckStatus.FAIL
    assert result.overall_status is ValidationStatus.FAIL


def test_missing_operand_yields_not_applicable(service):
    data = factories.profit_and_loss()
    # Drop the appropriations rows entirely.
    data.line_items = [
        item for item in data.line_items if item.section != "Appropriations"
    ]
    result = service.validate(data, document_type=DocumentType.PROFIT_AND_LOSS)
    check = by_name(result.checks, "appropriation_check", CURRENT)
    assert check.status is CheckStatus.NOT_APPLICABLE
    assert check.variance is None
    # A NOT_APPLICABLE check must never make the document fail.
    assert result.overall_status is ValidationStatus.PASS


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------
def test_cash_flow_reconciles(service):
    result = service.validate(
        factories.cash_flow(), document_type=DocumentType.CASH_FLOW_STATEMENT
    )
    net = by_name(result.checks, "net_change_in_cash_check", CURRENT)
    # (11,959.57) + (2,110.55) + 45,205.11 + 121.50
    assert net.calculated_value == pytest.approx(31_256.49)
    assert net.status is CheckStatus.PASS
    assert net.operands["net_cash_operating"] == pytest.approx(-11_959.57)

    closing = by_name(result.checks, "closing_cash_check", CURRENT)
    assert closing.calculated_value == pytest.approx(153_146.62)
    assert closing.status is CheckStatus.PASS
    assert result.overall_status is ValidationStatus.PASS


def test_cash_flow_detects_a_broken_closing_balance(service):
    result = service.validate(
        factories.cash_flow(consistent=False),
        document_type=DocumentType.CASH_FLOW_STATEMENT,
    )
    closing = by_name(result.checks, "closing_cash_check", CURRENT)
    assert closing.status is CheckStatus.FAIL
    assert closing.variance == pytest.approx(-6_853.38)


def test_cash_flow_without_an_fx_line_still_reconciles(service):
    """The translation adjustment is genuinely optional; most years omit it."""
    result = service.validate(
        factories.cash_flow(with_fx=False),
        document_type=DocumentType.CASH_FLOW_STATEMENT,
    )
    net = by_name(result.checks, "net_change_in_cash_check", CURRENT)
    assert net.status is CheckStatus.PASS
    assert net.operands["fx_adjustment"] is None


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------
def test_invoice_checks_pass(service):
    result = service.validate(factories.invoice(), document_type=DocumentType.INVOICE)
    total = by_name(result.checks, "invoice_total_check")
    assert total.calculated_value == pytest.approx(13_125.00)
    assert total.status is CheckStatus.PASS

    line = by_name(result.checks, "line_item_1_amount_check")
    assert line.calculated_value == pytest.approx(12_500.00)

    change = by_name(result.checks, "cash_change_check")
    assert change.calculated_value == pytest.approx(1_875.00)
    assert result.overall_status is ValidationStatus.PASS


def test_invoice_total_mismatch_fails(service):
    result = service.validate(
        factories.invoice(consistent=False), document_type=DocumentType.INVOICE
    )
    assert by_name(result.checks, "invoice_total_check").status is CheckStatus.FAIL
    assert result.overall_status is ValidationStatus.FAIL


def test_tax_inclusive_receipt_does_not_double_count_tax(service):
    """Adding the GST on top of a GST-inclusive total would fail spuriously."""
    result = service.validate(
        factories.receipt_tax_inclusive(), document_type=DocumentType.INVOICE
    )
    total = by_name(result.checks, "invoice_total_check")
    assert total.calculated_value == pytest.approx(18.00)
    assert total.status is CheckStatus.PASS
    assert "already included" in total.formula


def test_tolerance_is_configurable():
    strict = FinancialValidationService(Tolerance(absolute=0.0, relative=0.0))
    data = factories.balance_sheet()
    # Nudge the reported total by a rounding-sized amount.
    data.line_items[-1].values[0].raw_value = "2,466,081.90"
    result = strict.validate(data, document_type=DocumentType.BALANCE_SHEET)
    assert by_name(result.checks, "balance_sheet_equation_check", CURRENT).status is (
        CheckStatus.FAIL
    )

    lenient = FinancialValidationService(Tolerance(absolute=1.0, relative=0.005))
    assert by_name(
        lenient.validate(data, document_type=DocumentType.BALANCE_SHEET).checks,
        "balance_sheet_equation_check",
        CURRENT,
    ).status is CheckStatus.PASS


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------
def test_confidence_rewards_corroborated_evidence():
    corroborated = score_field(
        value=13125.0,
        source_text="Total Amount Due: USD 13,125.00",
        page_number=1,
        used_ocr=True,
    )
    uncorroborated = score_field(
        value=13125.0, source_text="Total Amount Due", page_number=1, used_ocr=True
    )
    assert corroborated > uncorroborated


def test_confidence_penalises_a_failing_check():
    passing = score_field(
        value=100.0, source_text="Total 100.00", page_number=1,
        used_ocr=True, verdict=CheckStatus.PASS,
    )
    failing = score_field(
        value=100.0, source_text="Total 100.00", page_number=1,
        used_ocr=True, verdict=CheckStatus.FAIL,
    )
    assert failing < passing


def test_native_text_scores_above_vision():
    assert score_field(value=1.0, source_text=None, page_number=None, used_ocr=False) > (
        score_field(value=1.0, source_text=None, page_number=None, used_ocr=True)
    )


def test_absent_value_has_no_confidence():
    assert score_field(value=None, source_text="x", page_number=1, used_ocr=False) is None
    assert overall_confidence([None, None]) is None
    assert overall_confidence([0.8, 0.6]) == pytest.approx(0.7)


def test_fail_dominates_pass_in_field_verdicts(service):
    result = service.validate(
        factories.invoice(consistent=False), document_type=DocumentType.INVOICE
    )
    verdicts = build_field_verdicts(result.checks)
    assert verdicts["subtotal"] is CheckStatus.FAIL


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------
def test_invoice_result_parses_values_and_reports_missing_fields(service):
    data = factories.invoice()
    validation = service.validate(data, document_type=DocumentType.INVOICE)
    payload, confidence = build_extracted_data(
        data, document_type=DocumentType.INVOICE,
        checks=validation.checks, used_ocr=True,
    )

    assert payload["total_amount"]["value"] == pytest.approx(13_125.00)
    assert payload["total_amount"]["raw_value"] == "13,125.00"
    assert payload["invoice_number"]["value"] == "INV-23891"
    assert payload["line_items"][0]["unit_price"] == pytest.approx(12_500.00)
    assert payload["additional_fields"][0]["label"] == "GST Reg No"
    assert 0.0 < confidence <= 1.0

    # due_date was never present in the document.
    assert "due_date" in missing_field_names(payload)


def test_statement_result_exposes_key_figures_per_period(service):
    data = factories.cash_flow()
    validation = service.validate(
        data, document_type=DocumentType.CASH_FLOW_STATEMENT
    )
    payload, _ = build_extracted_data(
        data, document_type=DocumentType.CASH_FLOW_STATEMENT,
        checks=validation.checks, used_ocr=True,
    )

    figures = payload["key_figures"]
    assert set(figures) == {CURRENT, PRIOR}
    assert figures[CURRENT]["operating_cash_flow"]["value"] == pytest.approx(-11_959.57)
    assert figures[CURRENT]["closing_cash"]["value"] == pytest.approx(153_146.62)
    assert figures[PRIOR]["opening_cash"]["value"] == pytest.approx(70_482.36)

    # Line items keep one value per period, with the printed form retained.
    operating = next(
        i for i in payload["line_items"]
        if i["label"].startswith("Net cash flow (used in)")
    )
    assert operating["values"][CURRENT]["raw_value"] == "(11,959.57)"
    assert operating["values"][CURRENT]["value"] == pytest.approx(-11_959.57)
    assert operating["is_total"] is True


def test_balance_sheet_key_figures_cover_the_required_minimum(service):
    data = factories.balance_sheet()
    validation = service.validate(data, document_type=DocumentType.BALANCE_SHEET)
    payload, _ = build_extracted_data(
        data, document_type=DocumentType.BALANCE_SHEET,
        checks=validation.checks, used_ocr=True,
    )
    figures = payload["key_figures"][CURRENT]
    assert figures["total_assets"]["value"] == pytest.approx(2_466_081.47)
    assert figures["total_liabilities"]["value"] == pytest.approx(2_466_081.47)
    # This statement reports no separate "Total equity" row.
    assert figures["total_equity"]["value"] is None


# ---------------------------------------------------------------------------
# Regression: captions as real published statements actually print them
# ---------------------------------------------------------------------------
def test_bare_total_is_resolved_by_its_section(service):
    """Real statements print "Total" once per section, not "Total Assets".

    Without section-aware matching every check on these documents came back
    NOT_APPLICABLE - the extraction was correct and the matcher was not.
    """
    result = service.validate(
        factories.real_balance_sheet(), document_type=DocumentType.BALANCE_SHEET
    )
    check = by_name(result.checks, "balance_sheet_equation_check", CURRENT)
    assert check.status is CheckStatus.PASS
    assert check.calculated_value == pytest.approx(4_030_194.26)
    assert not [c for c in result.checks if c.status is CheckStatus.NOT_APPLICABLE]


def test_printed_dash_is_nil_so_section_sums_still_run(service):
    """A "-" cell is reported as zero, not absent.

    Two rows in this statement carry a dash. Treating those as missing made
    the component-sum check NOT_APPLICABLE for a period that reconciles fine.
    """
    result = service.validate(
        factories.real_balance_sheet(), document_type=DocumentType.BALANCE_SHEET
    )
    for period in (CURRENT, PRIOR):
        for section in ("capital and liabilities", "assets"):
            check = next(
                c for c in result.checks
                if c.name.startswith("section_sum_check") and section in c.name
                and c.period == period
            )
            assert check.status is CheckStatus.PASS, (section, period)


def test_real_profit_and_loss_captions_all_resolve(service):
    result = service.validate(
        factories.real_profit_and_loss(),
        document_type=DocumentType.PROFIT_AND_LOSS,
    )
    assert result.overall_status is ValidationStatus.PASS
    assert not [c for c in result.checks if c.status is CheckStatus.NOT_APPLICABLE]

    # "minorities' interest" must match the same concept as "minority interest".
    profit = by_name(result.checks, "net_profit_before_minority_interest_check", CURRENT)
    assert profit.calculated_value == pytest.approx(38_150.90)

    # The deduction is "Less : Minorities' Interest" (98.15), not the
    # "Transfer to / (from) Minority Interest" appropriation line.
    group = by_name(result.checks, "net_profit_attributable_to_group_check", CURRENT)
    assert group.operands["minority_interest"] == pytest.approx(98.15)
    assert group.calculated_value == pytest.approx(38_052.75)

    # "Add: Brought forward ... attributable to the group" must not be mistaken
    # for the attributable-profit row itself.
    appropriation = by_name(result.checks, "appropriation_check", CURRENT)
    assert appropriation.operands["profit_brought_forward"] == pytest.approx(78_594.20)
    assert appropriation.calculated_value == pytest.approx(116_646.95)


def test_taxable_base_falls_back_to_the_line_items(service):
    """Till receipts often print no subtotal; the line sum is the only base."""
    result = service.validate(
        factories.receipt_without_subtotal(), document_type=DocumentType.INVOICE
    )
    check = by_name(result.checks, "invoice_total_check")
    assert check.status is CheckStatus.PASS
    assert check.calculated_value == pytest.approx(9.00)
    assert "sum(line_item.amount)" in check.formula


def test_tax_treatment_is_derived_from_the_figures_not_the_flag(service):
    """The model called this invoice tax-inclusive; the arithmetic disagrees.

    Trusting the flag produced a spurious FAIL with a variance of exactly the
    tax amount, which is the signature of double-counting it.
    """
    result = service.validate(
        factories.gst_invoice_mislabelled_inclusive(),
        document_type=DocumentType.INVOICE,
    )
    check = by_name(result.checks, "invoice_total_check")
    assert check.status is CheckStatus.PASS
    assert check.calculated_value == pytest.approx(6_862.00)
    assert check.operands["tax_amount"] == pytest.approx(1_046.72)
    assert "document states: inclusive" in check.description


def test_genuinely_inclusive_total_is_not_double_counted(service):
    """The opposite case must still work: tax inside the printed total."""
    result = service.validate(
        factories.receipt_tax_inclusive(), document_type=DocumentType.INVOICE
    )
    check = by_name(result.checks, "invoice_total_check")
    assert check.status is CheckStatus.PASS
    assert check.calculated_value == pytest.approx(18.00)
    assert "already included" in check.formula


def test_a_real_total_mismatch_still_fails(service):
    """Deriving the treatment must not turn every invoice into a pass."""
    result = service.validate(
        factories.invoice(consistent=False), document_type=DocumentType.INVOICE
    )
    assert by_name(result.checks, "invoice_total_check").status is CheckStatus.FAIL
