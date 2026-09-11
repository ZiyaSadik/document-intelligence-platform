"""Numeric parsing and comparison helpers for financial documents.

Financial statements encode negatives as parentheses, use locale-specific digit
grouping (including the Indian ``1,23,456.78`` convention), prefix currency
symbols and represent "no value" as a dash. Getting these wrong silently
inverts signs and breaks every downstream reconciliation, so the parsing lives
in one tested place.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# Characters that appear around a number but carry no numeric meaning.
_CURRENCY_AND_NOISE = re.compile(
    r"[\s\u00a0"          # whitespace incl. non-breaking space
    r"\u20b9$\u20ac\u00a3\u00a5\u20a6\u20aa"  # currency symbols
    r"`'\"]"              # stray quotes; '`' is how some PDFs render the rupee
    r"|(?i:rs\.?|inr|usd|eur|gbp|rm|myr|sgd|aed)"
)

# A printed dash is not the same as a blank cell, and the difference matters.
# In a financial statement "-" is the standard notation for nil - the document
# *does* report the value, as zero - whereas an absent value means it does not
# say. Collapsing the two would either invent a zero or discard a real one, and
# it decides whether a reconciliation runs or returns NOT_APPLICABLE.
_NIL_TOKENS = frozenset(
    {"-", "--", "---", "\u2013", "\u2014", "\u2212", "\u2013\u2013", "nil", "nill"}
)

# Strings that mean "the document does not report this".
_ABSENT_TOKENS = frozenset(
    {"", "n/a", "na", "n.a.", "none", "null", "not applicable", "not reported"}
)

_NUMERIC = re.compile(r"^[+-]?\d+(\.\d+)?$")

# Any number-looking token inside a longer sentence, used to verify that an
# extracted value is actually present in its cited evidence text.
NUMBER_IN_TEXT = re.compile(r"[-+(]?\s*\d[\d,\u00a0 ]*(?:\.\d+)?\s*\)?")


def parse_money(raw: Any) -> float | None:
    """Parse a monetary/quantity token into a float.

    Returns ``None`` when the document does not report a value -- never a
    substituted zero, because a missing operand must propagate as
    ``NOT_APPLICABLE`` rather than quietly reconciling to nothing. A *printed*
    dash is a different thing: it is the accounting notation for nil, so it
    parses as ``0.0``.

    >>> parse_money("(1,234.56)")
    -1234.56
    >>> parse_money("\u20b9 1,23,456.78")
    123456.78
    >>> parse_money("-")            # printed dash: reported as nil
    0.0
    >>> parse_money("N/A") is None  # not reported at all
    True
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is an int subclass; never a money value
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, Decimal):
        return float(raw)
    if not isinstance(raw, str):
        return None

    text = raw.strip()
    lowered = text.lower()
    if lowered in _NIL_TOKENS:
        return 0.0
    if lowered in _ABSENT_TOKENS:
        return None

    negative = False

    # Parentheses denote a negative value in every statement convention.
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = _CURRENCY_AND_NOISE.sub("", text)

    # Some exports place the minus sign after the digits.
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    if text.startswith("\u2212"):  # Unicode minus
        text = "-" + text[1:]

    # A second parenthesis check: the currency symbol may have sat outside it.
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]

    text = text.replace(",", "")

    if not text:
        return None
    if text.lower() in _NIL_TOKENS:
        return 0.0
    if text.lower() in _ABSENT_TOKENS:
        return None

    if not _NUMERIC.match(text):
        return None

    try:
        value = float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None

    if negative:
        value = -abs(value)
    return value


def variance(calculated: float, reported: float) -> float:
    """Signed difference between what we computed and what the document says."""
    return round(calculated - reported, 6)


def within_tolerance(
    calculated: float,
    reported: float,
    *,
    abs_tolerance: float,
    rel_tolerance: float,
) -> bool:
    """True when two figures agree within the combined allowance.

    The allowance is ``abs_tolerance + rel_tolerance * scale``, not one *or*
    the other. An OR rule is too permissive at both ends: a 0.5% relative limit
    lets a 20,000-crore discrepancy through on a large balance sheet, while an
    absolute limit generous enough for a statement is 11% of a 9.00 till
    receipt - loose enough to make two contradictory readings both "pass".

    The absolute term absorbs rounding at the printed precision (a sum of
    twenty figures each rounded to 2dp can drift ~0.1); the relative term is a
    small safety valve so that drift can grow with magnitude.
    """
    delta = abs(calculated - reported)
    scale = max(abs(calculated), abs(reported))
    return delta <= abs_tolerance + rel_tolerance * scale


def safe_sum(values: list[float | None]) -> float | None:
    """Sum only if every operand is present; otherwise ``None``.

    Treating a missing operand as zero would fabricate a reconciliation that
    the source document does not support.
    """
    if not values or any(v is None for v in values):
        return None
    return round(sum(v for v in values if v is not None), 6)


def text_contains_number(text: str | None, value: float) -> bool:
    """Whether ``value`` appears among the numbers written in ``text``.

    Used by the confidence model to check that an extracted figure is really
    present in the evidence snippet the model cited, rather than inferred.
    """
    if not text:
        return False
    for match in NUMBER_IN_TEXT.finditer(text):
        parsed = parse_money(match.group().strip())
        if parsed is None:
            continue
        if abs(abs(parsed) - abs(value)) < 0.005:
            return True
    return False
