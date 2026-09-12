"""Maps printed statement captions onto the concepts the validation rules need.

The specification's reconciliations are written in terms of ideas ("Total
Income", "Net Cash Flow from Operating Activities"), but documents print
captions like ``"Net cash flow (used in) / from operating activities"`` or
``"II. Total Expenditure"``. Something has to bridge the two, and it must do so
without hardcoding this particular corpus - the rules should still fire on a
statement nobody has seen.

The bridge here is a small declarative registry: each concept lists caption
tokens that must be present, alternatives, and tokens that disqualify a match.
Matching runs on a normalised caption (case-folded, punctuation stripped,
leading enumerators like ``"I."`` or ``"(a)"`` removed), and an exact caption
match always beats a token match so that ``"Total Income"`` never loses to
``"Total Income from operations"``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.utils.numbers import parse_money

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for typing only
    from app.schemas.extraction import StatementLineItem

# Leading enumerators: "I.", "II)", "A.", "1.", "(a)", "(iv)"
_ENUMERATOR = re.compile(
    r"^\s*\(?(?:[ivxlcdm]{1,7}|[a-z]|\d{1,2})\)?[.)]\s+", re.IGNORECASE
)
_NON_WORD = re.compile(r"[^a-z0-9 ]+")
_SPACES = re.compile(r"\s+")


def normalise_label(label: str | None) -> str:
    """Reduce a printed caption to a comparable token string."""
    if not label:
        return ""
    text = label.strip().lower()
    text = text.replace("&", " and ")
    # Strip an enumerator, possibly more than one ("I. (a) Assets").
    for _ in range(2):
        stripped = _ENUMERATOR.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = _NON_WORD.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


@dataclass(frozen=True)
class ConceptRule:
    """How to recognise one concept among a statement's captions."""

    concept: str
    #: Captions that match one of these exactly win outright.
    exact: tuple[str, ...] = ()
    #: Every token here must appear in the normalised caption.
    must_contain: tuple[str, ...] = ()
    #: Each inner tuple is an OR-group; at least one token from each must appear.
    any_of: tuple[tuple[str, ...], ...] = ()
    #: A caption containing any of these is rejected outright.
    must_not_contain: tuple[str, ...] = ()
    #: When set, the row's *section heading* must contain one of these. This is
    #: what makes a bare "Total" resolvable: real statements print the caption
    #: once per section and rely on the heading to say which total it is.
    section_any: tuple[str, ...] = ()
    #: Rows flagged as totals are preferred when several captions match.
    prefer_total: bool = True

    def matches(self, normalised: str, section: str = "") -> int:
        """Score a caption: 2 = exact, 1 = token match, 0 = no match."""
        if not normalised:
            return 0
        if normalised in self.exact:
            return 2
        for token in self.must_not_contain:
            if token in normalised:
                return 0
        for token in self.must_contain:
            if token not in normalised:
                return 0
        for group in self.any_of:
            if not any(token in normalised for token in group):
                return 0
        if self.section_any and not any(t in section for t in self.section_any):
            return 0
        if not self.must_contain and not self.any_of:
            return 0
        return 1


# ---------------------------------------------------------------------------
# Balance sheet
# ---------------------------------------------------------------------------
BALANCE_SHEET_CONCEPTS: tuple[ConceptRule, ...] = (
    ConceptRule(
        concept="total_capital_and_liabilities",
        exact=(
            "total capital and liabilities",
            "total equity and liabilities",
            "total liabilities and equity",
            "total liabilities and shareholders funds",
        ),
        must_contain=("total",),
        any_of=(("liabilities",),),
        must_not_contain=("asset", "current liabilities", "contingent"),
    ),
    # Many statements print the caption as a bare "Total" and leave the
    # section heading to say which total it is.
    ConceptRule(
        concept="total_capital_and_liabilities",
        must_contain=("total",),
        section_any=("liabilities", "equity"),
        must_not_contain=("asset", "contingent"),
    ),
    ConceptRule(
        concept="total_assets",
        exact=("total assets",),
        must_contain=("total", "assets"),
        must_not_contain=("current assets", "fixed assets", "other assets"),
    ),
    ConceptRule(
        concept="total_assets",
        must_contain=("total",),
        section_any=("assets",),
        must_not_contain=("liabilities", "contingent"),
    ),
    ConceptRule(
        concept="total_equity",
        exact=("total equity", "total shareholders funds", "shareholders funds"),
        must_contain=("equity",),
        any_of=(("total", "shareholders"),),
        must_not_contain=("liabilities", "share capital"),
    ),
    ConceptRule(
        concept="capital",
        exact=("capital", "share capital", "equity share capital"),
    ),
    ConceptRule(
        concept="reserves_and_surplus",
        exact=("reserves and surplus", "reserves", "other equity"),
    ),
)

# ---------------------------------------------------------------------------
# Profit and loss
# ---------------------------------------------------------------------------
PROFIT_AND_LOSS_CONCEPTS: tuple[ConceptRule, ...] = (
    ConceptRule(
        concept="interest_earned",
        exact=("interest earned", "interest income", "revenue from operations"),
        must_contain=("interest", "earned"),
    ),
    ConceptRule(
        concept="other_income",
        exact=("other income",),
        must_contain=("other", "income"),
        must_not_contain=("total", "comprehensive", "expenditure"),
    ),
    ConceptRule(
        concept="total_income",
        exact=("total income", "total revenue"),
        must_contain=("total",),
        any_of=(("income", "revenue"),),
        must_not_contain=("expenditure", "expense", "comprehensive", "other income"),
    ),
    ConceptRule(
        concept="total_income",
        must_contain=("total",),
        section_any=("income", "revenue"),
        must_not_contain=("expenditure", "expense"),
    ),
    ConceptRule(
        concept="interest_expended",
        exact=("interest expended", "interest expense", "finance costs"),
        must_contain=("interest",),
        any_of=(("expended", "expense"),),
        must_not_contain=("total",),
    ),
    ConceptRule(
        concept="operating_expenses",
        exact=("operating expenses", "operating expense"),
        must_contain=("operating", "expens"),
        must_not_contain=("total", "provisions"),
    ),
    ConceptRule(
        concept="provisions_and_contingencies",
        exact=("provisions and contingencies", "provisions and contingencies net"),
        must_contain=("provisions",),
        any_of=(("contingencies", "contingency"),),
        must_not_contain=("total",),
    ),
    ConceptRule(
        concept="total_expenditure",
        exact=("total expenditure", "total expenses"),
        must_contain=("total",),
        any_of=(("expenditure", "expenses"),),
        must_not_contain=("income", "operating expenses"),
    ),
    ConceptRule(
        concept="total_expenditure",
        must_contain=("total",),
        section_any=("expenditure", "expenses"),
        must_not_contain=("income",),
    ),
    ConceptRule(
        concept="profit_before_minority_interest",
        exact=(
            "consolidated net profit for the year before minority interest",
            "net profit before minority interest",
            "profit before minority interest",
            "consolidated net profit before minority interest",
        ),
        must_contain=("profit", "before", "minorit"),
    ),
    ConceptRule(
        concept="minority_interest",
        exact=("minority interest", "less minority interest", "non controlling interest"),
        must_contain=("minorit",),
        must_not_contain=(
            # "Transfer to / (from) Minority Interest" is an appropriation
            # line, not the deduction the reconciliation needs.
            "before", "after", "attributable", "transfer", "increase", "opening",
        ),
    ),
    ConceptRule(
        concept="net_profit_attributable_to_group",
        exact=(
            "consolidated net profit for the year attributable to the group",
            "net profit attributable to the group",
            "profit attributable to the group",
            "profit attributable to owners of the parent",
        ),
        must_contain=("profit", "attributable"),
        any_of=(("group", "owners", "parent"),),
        must_not_contain=("brought forward", "carried"),
    ),
    ConceptRule(
        concept="profit_brought_forward",
        exact=(
            "profit brought forward",
            "balance brought forward",
            "brought forward from previous year",
            "surplus brought forward",
        ),
        must_contain=("brought forward",),
    ),
    ConceptRule(
        concept="total_available_for_appropriation",
        exact=(
            "total available for appropriation",
            "amount available for appropriation",
            "profit available for appropriation",
        ),
        must_contain=("appropriation",),
        any_of=(("total", "available"),),
    ),
    ConceptRule(
        concept="total_available_for_appropriation",
        must_contain=("total",),
        section_any=("profit", "appropriation"),
        must_not_contain=("income", "expenditure"),
    ),
    # A restructuring can add a third component to the appropriation build-up,
    # printed between the year's profit and the brought-forward balance -
    # "Impact on amalgamation", say. Most years omit it, so it is an optional
    # operand rather than a required one, exactly like the cash-flow
    # translation adjustment.
    ConceptRule(
        concept="appropriation_adjustment",
        must_contain=("amalgamation",),
        must_not_contain=("cash", "activities"),
    ),
)

# ---------------------------------------------------------------------------
# Cash flow statement
# ---------------------------------------------------------------------------
CASH_FLOW_CONCEPTS: tuple[ConceptRule, ...] = (
    ConceptRule(
        concept="net_cash_operating",
        must_contain=("operating activities",),
        any_of=(("net cash", "cash generated", "cash flow"),),
        must_not_contain=("investing", "financing", "before"),
    ),
    ConceptRule(
        concept="net_cash_investing",
        must_contain=("investing activities",),
        any_of=(("net cash", "cash used", "cash flow"),),
        must_not_contain=("operating", "financing"),
    ),
    ConceptRule(
        concept="net_cash_financing",
        must_contain=("financing activities",),
        any_of=(("net cash", "cash used", "cash flow"),),
        must_not_contain=("operating", "investing"),
    ),
    ConceptRule(
        concept="fx_adjustment",
        must_contain=(),
        any_of=(
            ("exchange", "translation", "foreign currency"),
            ("effect", "adjustment", "difference", "fluctuation", "gain", "loss"),
        ),
        must_not_contain=("net cash", "activities"),
    ),
    ConceptRule(
        concept="net_increase_in_cash",
        must_contain=("cash",),
        any_of=(("net increase", "net decrease", "net change", "increase decrease"),),
        must_not_contain=("activities",),
    ),
    ConceptRule(
        concept="cash_acquired_on_amalgamation",
        must_contain=("cash",),
        any_of=(("amalgamation", "acquired on", "acquisition", "merger", "scheme"),),
        must_not_contain=("activities", "net increase"),
    ),
    ConceptRule(
        concept="opening_cash",
        must_contain=("cash",),
        any_of=(
            (
                "beginning",
                "opening",
                "at april 1",
                "as at april 1",
                "at the start",
                "at january 1",
            ),
        ),
        must_not_contain=("activities", "net increase", "end", "closing"),
    ),
    ConceptRule(
        concept="closing_cash",
        must_contain=("cash",),
        any_of=(
            (
                "end of",
                "closing",
                "at march 31",
                "as at march 31",
                "at december 31",
                "at the end",
            ),
        ),
        must_not_contain=("activities", "net increase", "beginning", "opening"),
    ),
)

CONCEPTS_BY_TYPE: dict[str, tuple[ConceptRule, ...]] = {
    "balance_sheet": BALANCE_SHEET_CONCEPTS,
    "profit_and_loss": PROFIT_AND_LOSS_CONCEPTS,
    "cash_flow_statement": CASH_FLOW_CONCEPTS,
}


@dataclass
class ConceptMatch:
    concept: str
    label: str
    score: int
    is_total: bool
    index: int
    source_text: str | None = None
    page_number: int | None = None
    values: dict[str, float | None] = field(default_factory=dict)


def resolve_concepts(
    rules: tuple[ConceptRule, ...],
    rows: list[tuple[str, bool, str]],
) -> dict[str, int]:
    """Pick the best row index for each concept.

    ``rows`` is ``(caption, is_total, section)`` in document order. Ranking is
    exact match, then total rows, then document order.

    A concept may have more than one rule - typically a label-only rule for
    documents that spell the caption out ("Total Income") and a section-gated
    rule for those that do not ("Total", under a heading of "I INCOME"). The
    best match across every rule for that concept wins.
    """
    captions = [normalise_label(caption) for caption, _, _ in rows]
    sections = [normalise_label(section) for _, _, section in rows]

    best_by_concept: dict[str, tuple[int, int, int]] = {}
    resolved: dict[str, int] = {}

    for rule in rules:
        for index, caption in enumerate(captions):
            score = rule.matches(caption, sections[index])
            if score == 0:
                continue
            is_total = 1 if (rule.prefer_total and rows[index][1]) else 0
            candidate = (score, is_total, -index)
            if candidate > best_by_concept.get(rule.concept, (0, 0, -10**9)):
                best_by_concept[rule.concept] = candidate
                resolved[rule.concept] = index

    return resolved


def value_for_period(item: "StatementLineItem", period: str) -> float | None:
    """The numeric value of a statement row under one period column.

    Period labels are compared after normalisation because the column heading
    in ``periods`` and the one repeated on each cell can differ in punctuation
    ("March 31, 2025" vs "March 31 2025").
    """
    target = normalise_label(period)
    for cell in item.values:
        if normalise_label(cell.period_label) == target:
            return parse_money(cell.raw_value)
    return None


def has_any_value(item: "StatementLineItem") -> bool:
    """Whether a row reports a figure in at least one period.

    Section headings ("Cash flows from operating activities:") read like the
    concept they introduce and would otherwise out-rank the actual subtotal
    row. A caption with no figure under any column cannot be the source of a
    value, so it is excluded from matching.
    """
    return any(parse_money(cell.raw_value) is not None for cell in item.values)


def candidate_rows(
    items: list["StatementLineItem"],
) -> list[tuple[str, bool, str]]:
    """``(caption, is_total, section)`` triples for concept matching.

    Rows carrying no figure in any period are blanked out. Positions are
    preserved so a resolved index still addresses ``items``.
    """
    return [
        (
            item.label if has_any_value(item) else "",
            item.is_total,
            item.section or "",
        )
        for item in items
    ]
