"""AI field and table extraction.

One model call per document. All pages (at most three) go into a single request
so the model can resolve values that span a page break - a cash-flow statement
whose closing-balance reconciliation sits on page 2 needs page 1 in view.

Two deliberate design choices:

* **Streaming.** A comparative statement with fifty-odd line items across two
  periods, each carrying evidence text, is a long JSON payload. Streaming with
  a large ``max_tokens`` avoids the request timeouts a blocking call would hit.
* **Values cross this boundary as strings.** The model transcribes what is
  printed; ``app.utils.numbers.parse_money`` decides what it means. Sign
  conventions and digit grouping stay deterministic and unit-testable instead
  of depending on the model's arithmetic.
"""

from __future__ import annotations

import time
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.exceptions import (
    ExtractionError,
    ModelNotConfiguredError,
    ModelUnavailableError,
)
from app.core.logging import get_logger
from app.schemas.enums import DocumentType
from app.schemas.extraction import InvoiceExtraction, StatementExtraction
from app.services.ocr_service import DocumentContent

logger = get_logger(__name__)


BASE_SYSTEM_PROMPT = """\
You are a meticulous document-extraction engine for a financial-services team.
You are given the pages of a single document, either as extracted text or as a \
page image. You return structured data describing what is printed on those pages.

Absolute rules:

1. TRANSCRIBE, DO NOT CALCULATE. Report every figure exactly as it is printed, \
including thousands separators, currency symbols, decimals and parentheses. \
Never compute a value that is not printed. Never re-derive a total. Never \
correct an apparent arithmetic error in the document - report what is there.
2. NEVER INVENT. If a value is absent, blank, illegible or cut off, return null \
for it. Do not infer it from a neighbouring period, a related line, or general \
knowledge. A null is a correct answer; a plausible guess is a wrong one.
3. PARENTHESES ARE PART OF THE VALUE. Write "(1,234.56)" exactly as printed - \
do not convert it to a negative number yourself.
4. BE EXHAUSTIVE. Extract every labelled value visible on the page, not just \
the well-known ones. Anything you leave out is treated as missing data.
5. GROUND EVERY VALUE. Give the page number it appears on, and quote the line \
of text it came from in source_text.
6. Use the exact wording printed in the document for labels. Do not translate, \
normalise, expand abbreviations or tidy up capitalisation.
"""

INVOICE_PROMPT = """\
This document is an INVOICE or a RETAIL RECEIPT.

Return one entry in `fields` for each recognised field the document actually reports, using the exact `name` from the allowed list. Omit a field entirely rather than including it with a null value.

Extract:
- Every header field: invoice/receipt number, dates, purchase-order number.
- Both parties: seller/vendor and customer, with addresses and any tax \
registration number (GST/VAT/TIN).
- The currency, exactly as printed (for example "RM", "$", "USD").
- Every monetary summary line: subtotal, discount, tax, rounding adjustment, \
total, cash tendered and change given.
- Every line item in the items table, with its description, quantity, unit \
price and line total. Include all of them, including ones with no price. \
Quantity is normally its own column: report it even when the description also \
mentions a pack size, and even when it is written as "2 PCS" or "1 x".
- Anything else that is labelled on the document goes into additional_fields.

Set tax_inclusive to true when the document states the total already includes \
tax (wording such as "GST inclusive", "inclusive of tax", or a tax summary that \
breaks the printed total down into a taxable amount plus tax). Set it to false \
when tax is clearly added on top of the subtotal. Leave it null if unclear.

Receipts are often faint, skewed or thermally faded. If a digit genuinely \
cannot be read, return null for that value rather than guessing at it.
"""

STATEMENT_PROMPT_TEMPLATE = """\
This document is a {label}.

First identify every comparative period column - these statements almost always \
report two or more periods side by side (for example "March 31, 2025" and \
"March 31, 2024"). List each one in `periods`, using the exact column heading.

Then extract EVERY row of the statement into `line_items`. For each row:
- `label` is the row caption exactly as printed.
- `section` is the heading the row sits under{section_hint}.
- `values` must contain one entry per period column, with `period_label` \
matching the corresponding entry in `periods` and `raw_value` exactly as \
printed in that column. If a cell is blank or has a dash, set raw_value to null.
- `is_total` is true for subtotal and total rows (captions beginning "Total", \
"Net", "Sub-total", or printed in bold above a rule).
- `schedule_reference` is the schedule or note number, if one is printed.

Getting the column alignment right is critical: a value placed under the wrong \
period is worse than a missing value. Work row by row and read across.

Also capture the entity name, the statement title, the reporting currency and \
the units note (for example "in crore", "in millions"). Put any other header \
text - registration numbers, dates, auditor references - into header_fields.

{extra}
"""

_STATEMENT_HINTS: dict[DocumentType, dict[str, str]] = {
    DocumentType.BALANCE_SHEET: {
        "section_hint": (
            ', such as "Capital and Liabilities", "Assets", or '
            '"Contingent liabilities"'
        ),
        "extra": (
            "Include the capital, reserves, deposits, borrowings and other "
            "liability rows, every asset row, and the totals for each side of "
            "the statement. Do not omit off-balance-sheet or contingent items "
            "printed below the main table."
        ),
    },
    DocumentType.PROFIT_AND_LOSS: {
        "section_hint": (
            ', such as "Income", "Expenditure", "Profit", or "Appropriations"'
        ),
        "extra": (
            "Include every income and expenditure row, the profit subtotals, "
            "minority interest, share of profit of associates, earnings per "
            "share and any appropriations section printed beneath the main "
            "statement."
        ),
    },
    DocumentType.CASH_FLOW_STATEMENT: {
        "section_hint": (
            ', such as "Cash flows from operating activities", "...investing '
            'activities", "...financing activities", or "Cash and cash '
            'equivalents"'
        ),
        "extra": (
            "Include the adjustment rows within each activities section, the "
            "net cash flow subtotal for each of the three activities, any "
            "foreign-exchange or translation adjustment, the net increase or "
            "decrease in cash, and the opening and closing cash and cash "
            "equivalents balances. On a two-page statement the closing balances "
            "are usually on the second page - do not stop after page one."
        ),
    },
}


def build_prompt(document_type: DocumentType) -> str:
    if document_type is DocumentType.INVOICE:
        return INVOICE_PROMPT
    hints = _STATEMENT_HINTS[document_type]
    return STATEMENT_PROMPT_TEMPLATE.format(
        label=document_type.label.upper(),
        section_hint=hints["section_hint"],
        extra=hints["extra"],
    )


def model_for(document_type: DocumentType) -> type[BaseModel]:
    return (
        InvoiceExtraction
        if document_type is DocumentType.INVOICE
        else StatementExtraction
    )


class ExtractionResult(BaseModel):
    """What the service hands back to the orchestrator."""

    data: InvoiceExtraction | StatementExtraction
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    attempts: int = 1
    duration_ms: int = 0


class ExtractionService:
    """Wraps the Anthropic client with prompts, schemas and error mapping."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._model = settings.anthropic_model

    # -- client ------------------------------------------------------------
    @property
    def client(self) -> Any:
        """Lazily construct the SDK client.

        Deferring construction means the app still boots (and serves reads and
        the dashboard) when no key is configured; only the process endpoint
        reports the misconfiguration.
        """
        if self._client is None:
            if not settings.anthropic_api_key:
                raise ModelNotConfiguredError()
            self._client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key,
                timeout=settings.anthropic_timeout_seconds,
                max_retries=settings.anthropic_max_retries,
            )
        return self._client

    @property
    def is_configured(self) -> bool:
        return self._client is not None or bool(settings.anthropic_api_key)

    # -- public API --------------------------------------------------------
    def extract(
        self,
        content: DocumentContent,
        *,
        document_type: DocumentType,
        filename: str,
    ) -> ExtractionResult:
        schema = model_for(document_type)
        blocks = self._content_blocks(content, document_type=document_type)
        started = time.perf_counter()

        # One retry, and it is not a blind repeat: the validator's complaint is
        # fed back so the second attempt is actually informed.
        last_error: ValidationError | None = None
        for attempt in (1, 2):
            messages: list[dict[str, Any]] = [{"role": "user", "content": blocks}]
            if last_error is not None:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response did not match the required "
                            f"schema:\n{last_error}\n\nReturn the same extraction "
                            "again, corrected to match the schema exactly. Do not "
                            "change any transcribed value."
                        ),
                    }
                )

            message = self._call_model(
                messages=messages,
                schema=schema,
                document_type=document_type,
                filename=filename,
                attempt=attempt,
            )

            parsed = getattr(message, "parsed_output", None)
            if parsed is not None:
                duration_ms = int((time.perf_counter() - started) * 1000)
                usage = getattr(message, "usage", None)
                logger.info(
                    "extraction succeeded",
                    extra={
                        "file_name": filename,
                        "document_type": document_type.value,
                        "attempts": attempt,
                        "duration_ms": duration_ms,
                        "input_tokens": getattr(usage, "input_tokens", None),
                        "output_tokens": getattr(usage, "output_tokens", None),
                    },
                )
                return ExtractionResult(
                    data=parsed,
                    model=self._model,
                    input_tokens=getattr(usage, "input_tokens", None),
                    output_tokens=getattr(usage, "output_tokens", None),
                    attempts=attempt,
                    duration_ms=duration_ms,
                )

            # No parsed output: reconstruct the validation error for the retry.
            raw_text = self._first_text(message)
            try:
                schema.model_validate_json(raw_text or "")
            except ValidationError as exc:
                last_error = exc
            logger.warning(
                "extraction response failed schema validation",
                extra={
                    "file_name": filename,
                    "attempt": attempt,
                    "stop_reason": getattr(message, "stop_reason", None),
                },
            )

        raise ExtractionError(
            "The document was read but the extracted data did not match the "
            "expected structure.",
            context={"file_name": filename},
            log_detail=str(last_error) if last_error else "no parsed output",
        )

    # -- internals ---------------------------------------------------------
    def _call_model(
        self,
        *,
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        document_type: DocumentType,
        filename: str,
        attempt: int,
    ) -> Any:
        system = f"{BASE_SYSTEM_PROMPT}\n\n{build_prompt(document_type)}"
        logger.info(
            "calling extraction model",
            extra={
                "file_name": filename,
                "model": self._model,
                "document_type": document_type.value,
                "attempt": attempt,
            },
        )
        try:
            with self.client.messages.stream(
                model=self._model,
                max_tokens=settings.anthropic_max_tokens,
                system=system,
                messages=messages,
                output_format=schema,
                thinking={"type": "adaptive"},
                output_config={"effort": settings.anthropic_effort},
            ) as stream:
                message = stream.get_final_message()
        except anthropic.NotFoundError as exc:
            raise ExtractionError(
                "The configured extraction model is not available to this API key.",
                context={"file_name": filename},
                log_detail=f"model={self._model}: {exc}",
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise ModelNotConfiguredError(
                "The configured Anthropic API key was rejected.",
                log_detail=str(exc),
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ModelUnavailableError(
                "The extraction service is rate limited. Please retry shortly.",
                context={"file_name": filename},
                log_detail=str(exc),
            ) from exc
        except anthropic.APIStatusError as exc:
            status = getattr(exc, "status_code", None)
            if status is not None and 500 <= status < 600:
                raise ModelUnavailableError(
                    context={"file_name": filename},
                    log_detail=f"status={status}: {exc}",
                ) from exc
            raise ExtractionError(
                context={"file_name": filename},
                log_detail=f"status={status}: {exc}",
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ModelUnavailableError(
                "Could not reach the extraction service.",
                context={"file_name": filename},
                log_detail=str(exc),
            ) from exc

        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason == "refusal":
            raise ExtractionError(
                "The extraction model declined to process this document.",
                context={"file_name": filename},
                log_detail=f"stop_details={getattr(message, 'stop_details', None)}",
            )
        if stop_reason == "max_tokens":
            # The JSON is truncated, so parsing will fail; say why explicitly
            # rather than letting it surface as an opaque schema error.
            raise ExtractionError(
                "The document contains more data than the configured output "
                "limit allows. Increase ANTHROPIC_MAX_TOKENS and retry.",
                context={"file_name": filename},
                log_detail=f"max_tokens={settings.anthropic_max_tokens} exhausted",
            )
        return message

    def _content_blocks(
        self, content: DocumentContent, *, document_type: DocumentType
    ) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    f"Document type: {document_type.label}. "
                    f"The document has {content.page_count} page(s)."
                ),
            }
        ]
        for page in content.pages:
            blocks.append(
                {"type": "text", "text": f"--- Page {page.page_number} ---"}
            )
            if page.text:
                blocks.append(
                    {
                        "type": "text",
                        "text": (
                            "Text extracted from this page's native text layer:\n\n"
                            f"{page.text}"
                        ),
                    }
                )
            elif page.image_b64:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": page.image_media_type,
                            "data": page.image_b64,
                        },
                    }
                )
        blocks.append(
            {
                "type": "text",
                "text": (
                    "Extract the document now. Return null for anything you "
                    "cannot read with certainty."
                ),
            }
        )
        return blocks

    @staticmethod
    def _first_text(message: Any) -> str | None:
        for block in getattr(message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                return getattr(block, "text", None)
        return None
