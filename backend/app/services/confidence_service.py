"""Explainable confidence scoring.

Confidence is optional in the specification, but if it is reported it has to
mean something. A number the model states about its own output is not evidence
of anything, so nothing here asks the model how sure it is. Instead the score
is computed after extraction from four things that can be checked
independently:

======================  ======  ==================================================
Signal                  Weight  Rationale
======================  ======  ==================================================
Source path             base    A native PDF text layer is exact; a rasterised
                                page was read visually and can be misread.
Evidence corroboration  +0.20   The extracted value is actually present in the
                                quoted source text - the strongest single signal
                                that it was transcribed rather than inferred.
Evidence completeness   +0.05   A page number and a quote were both returned.
Validation agreement    +0.10   The value participates in a reconciliation that
                        -0.30   passed; a failing reconciliation means at least
                                one of its operands is wrong.
======================  ======  ==================================================

A field the document does not report scores ``None`` rather than zero: there is
no value to be confident about, and zero would read as "we are sure it is
wrong".
"""

from __future__ import annotations

from app.schemas.document import ValidationCheck
from app.schemas.enums import CheckStatus
from app.utils.numbers import text_contains_number

BASE_NATIVE_TEXT = 0.75
BASE_VISION = 0.60

EVIDENCE_MATCH_BONUS = 0.20
EVIDENCE_PRESENT_BONUS = 0.05
VALIDATION_PASS_BONUS = 0.10
VALIDATION_FAIL_PENALTY = 0.30

MIN_CONFIDENCE = 0.05
MAX_CONFIDENCE = 0.99

#: Below this a value is highlighted in the dashboard as needing review.
LOW_CONFIDENCE_THRESHOLD = 0.70


def build_field_verdicts(checks: list[ValidationCheck]) -> dict[str, CheckStatus]:
    """Map each operand name to the worst outcome it took part in.

    FAIL dominates PASS: if a figure appears in one passing and one failing
    reconciliation, the failing one is the informative signal.
    """
    verdicts: dict[str, CheckStatus] = {}
    for check in checks:
        if check.status is CheckStatus.NOT_APPLICABLE:
            continue
        for operand, value in check.operands.items():
            if value is None:
                continue
            current = verdicts.get(operand)
            if current is CheckStatus.FAIL:
                continue
            verdicts[operand] = check.status
    return verdicts


def score_field(
    *,
    value: object,
    source_text: str | None,
    page_number: int | None,
    used_ocr: bool,
    verdict: CheckStatus | None = None,
) -> float | None:
    """Score one extracted value in the range 0.05 - 0.99, or ``None``."""
    if value is None:
        return None

    score = BASE_VISION if used_ocr else BASE_NATIVE_TEXT

    if source_text:
        score += EVIDENCE_PRESENT_BONUS
        if _corroborated(value, source_text):
            score += EVIDENCE_MATCH_BONUS
    if page_number is not None:
        score += EVIDENCE_PRESENT_BONUS

    if verdict is CheckStatus.PASS:
        score += VALIDATION_PASS_BONUS
    elif verdict is CheckStatus.FAIL:
        score -= VALIDATION_FAIL_PENALTY

    return round(min(MAX_CONFIDENCE, max(MIN_CONFIDENCE, score)), 3)


def overall_confidence(scores: list[float | None]) -> float | None:
    """Mean of the scored fields; ``None`` when nothing was extracted."""
    present = [s for s in scores if s is not None]
    if not present:
        return None
    return round(sum(present) / len(present), 3)


def _corroborated(value: object, source_text: str) -> bool:
    """Whether the value can be found in the text the model quoted."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return text_contains_number(source_text, float(value))
    if isinstance(value, str):
        needle = " ".join(value.split()).casefold()
        haystack = " ".join(source_text.split()).casefold()
        return bool(needle) and needle in haystack
    return False
