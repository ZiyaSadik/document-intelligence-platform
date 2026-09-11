"""Enumerations shared by the API surface and the services."""

from __future__ import annotations

from enum import StrEnum


class DocumentType(StrEnum):
    INVOICE = "invoice"
    BALANCE_SHEET = "balance_sheet"
    PROFIT_AND_LOSS = "profit_and_loss"
    CASH_FLOW_STATEMENT = "cash_flow_statement"

    @property
    def is_statement(self) -> bool:
        return self is not DocumentType.INVOICE

    @property
    def label(self) -> str:
        return {
            DocumentType.INVOICE: "Invoice",
            DocumentType.BALANCE_SHEET: "Balance Sheet",
            DocumentType.PROFIT_AND_LOSS: "Profit & Loss",
            DocumentType.CASH_FLOW_STATEMENT: "Cash Flow Statement",
        }[self]


class ProcessingStatus(StrEnum):
    PASS = "PASS"
    FAILED = "FAILED"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
