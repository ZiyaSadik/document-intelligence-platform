"""Anthropic SDK error mapping in ExtractionService.

No network calls: the client is a fake that raises constructed SDK errors.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from app.core.exceptions import (
    ExtractionError,
    ModelQuotaExceededError,
    ModelUnavailableError,
)
from app.schemas.enums import DocumentType
from app.services.extraction_service import ExtractionService, _is_quota_exhausted


def _status_error(
    cls: type[anthropic.APIStatusError],
    *,
    status_code: int,
    message: str,
    error_type: str = "invalid_request_error",
) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {
        "type": "error",
        "error": {"type": error_type, "message": message},
    }
    response = httpx2.Response(status_code, request=request, json=body)
    return cls(message, response=response, body=body)


class _RaisingStream(AbstractContextManager):
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __enter__(self):
        raise self._exc

    def __exit__(self, *args: object) -> bool:
        return False


class _FakeClient:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.messages = self

    def stream(self, **_kwargs: Any) -> _RaisingStream:
        return _RaisingStream(self._exc)


class _TinySchema(BaseModel):
    value: str = "x"


def _call(service: ExtractionService) -> Any:
    return service._call_model(
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        schema=_TinySchema,
        document_type=DocumentType.INVOICE,
        filename="receipt.jpg",
        attempt=1,
    )


@pytest.mark.parametrize(
    "message",
    [
        "Your credit balance is too low to access the Anthropic API. "
        "Please go to Plans & Billing to upgrade or purchase credits.",
        "Exceeded your current quota, please check your plan.",
        "Organization spending limit reached.",
    ],
)
def test_credit_and_quota_errors_become_model_quota_exceeded(message):
    exc = _status_error(anthropic.BadRequestError, status_code=400, message=message)
    service = ExtractionService(client=_FakeClient(exc))
    with pytest.raises(ModelQuotaExceededError) as raised:
        _call(service)
    assert raised.value.code == "MODEL_QUOTA_EXCEEDED"
    assert raised.value.status_code == 503
    assert "credit" in raised.value.message.lower() or "quota" in raised.value.message.lower()
    # Provider wording stays out of the public message.
    assert "Plans & Billing" not in raised.value.message
    assert message not in raised.value.message


def test_generic_bad_request_stays_extraction_failed():
    exc = _status_error(
        anthropic.BadRequestError,
        status_code=400,
        message="messages.0.content: unexpected field 'foo'",
    )
    service = ExtractionService(client=_FakeClient(exc))
    with pytest.raises(ExtractionError) as raised:
        _call(service)
    assert type(raised.value) is ExtractionError
    assert raised.value.code == "EXTRACTION_FAILED"
    assert raised.value.status_code == 502


def test_rate_limit_without_quota_stays_model_unavailable():
    exc = _status_error(
        anthropic.RateLimitError,
        status_code=429,
        message="Rate limited; retry later.",
        error_type="rate_limit_error",
    )
    service = ExtractionService(client=_FakeClient(exc))
    with pytest.raises(ModelUnavailableError) as raised:
        _call(service)
    assert raised.value.code == "MODEL_UNAVAILABLE"
    assert raised.value.status_code == 503


def test_rate_limit_that_is_quota_exhaustion_becomes_quota_error():
    exc = _status_error(
        anthropic.RateLimitError,
        status_code=429,
        message="You have exceeded your current quota.",
        error_type="rate_limit_error",
    )
    service = ExtractionService(client=_FakeClient(exc))
    with pytest.raises(ModelQuotaExceededError) as raised:
        _call(service)
    assert raised.value.code == "MODEL_QUOTA_EXCEEDED"


def test_upstream_5xx_stays_model_unavailable():
    exc = _status_error(
        anthropic.InternalServerError,
        status_code=500,
        message="Internal server error",
        error_type="api_error",
    )
    service = ExtractionService(client=_FakeClient(exc))
    with pytest.raises(ModelUnavailableError) as raised:
        _call(service)
    assert raised.value.code == "MODEL_UNAVAILABLE"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Your credit balance is too low", True),
        ("messages: invalid schema", False),
        ("usage limit reached for this key", True),
    ],
)
def test_quota_marker_helper(text, expected):
    class _Exc(Exception):
        body = {"error": {"type": "invalid_request_error", "message": text}}

    assert _is_quota_exhausted(_Exc(text)) is expected
