"""End-to-end API behaviour.

The extraction service is stubbed, so these exercise the real routing, upload
handling, validation, persistence and error envelope without a network call.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import ExtractionError
from tests import factories
from tests.conftest import StubExtractionService, make_image, make_pdf


def upload(client, payload: bytes, *, name: str, doc_type: str, mime="application/pdf"):
    return client.post(
        "/api/v1/documents/process",
        files={"file": (name, payload, mime)},
        data={"document_type": doc_type},
    )


# ---------------------------------------------------------------------------
# Health and docs
# ---------------------------------------------------------------------------
def test_health(client_factory):
    response = client_factory().get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert "version" in body and "extraction_configured" in body


def test_health_stays_responsive_during_processing(client_factory):
    """Regression: a long extraction must not block the event loop.

    Render probes /api/v1/health with a 5s timeout. If process() ran inline in
    the async handler, the probe would hang and the instance would get a 502.
    """

    class SlowExtractionService(StubExtractionService):
        def extract(self, content, *, document_type, filename):
            time.sleep(1.5)
            return super().extract(
                content, document_type=document_type, filename=filename
            )

    client = client_factory(stub=SlowExtractionService(factories.invoice()))
    transport = ASGITransport(app=client.app)

    async def _exercise() -> None:
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            process_task = asyncio.create_task(
                http.post(
                    "/api/v1/documents/process",
                    files={
                        "file": ("receipt.jpg", make_image("JPEG"), "image/jpeg")
                    },
                    data={"document_type": "invoice"},
                )
            )
            await asyncio.sleep(0.1)
            started = time.perf_counter()
            health = await http.get("/api/v1/health")
            waited = time.perf_counter() - started
            process = await process_task

        assert health.status_code == 200
        assert waited < 0.75, (
            f"health took {waited:.2f}s; the event loop was blocked by processing"
        )
        assert process.status_code == 200

    asyncio.run(_exercise())


def test_openapi_documents_every_required_endpoint(client_factory):
    paths = client_factory().get("/openapi.json").json()["paths"]
    assert "/api/v1/health" in paths
    assert "/api/v1/documents/process" in paths
    assert "/api/v1/documents" in paths
    assert "/api/v1/documents/{document_name}" in paths


def test_swagger_ui_is_served(client_factory):
    assert client_factory().get("/docs").status_code == 200


# ---------------------------------------------------------------------------
# Successful processing
# ---------------------------------------------------------------------------
def test_process_invoice_returns_the_specified_shape(client_factory):
    client = client_factory(factories.invoice())
    response = upload(
        client, make_image("JPEG"), name="receipt.jpg",
        doc_type="invoice", mime="image/jpeg",
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["document_name"] == "receipt.jpg"
    assert body["document_type"] == "invoice"
    assert body["processing_status"] == "PASS"
    assert body["file_validation"] == {
        "file_type": "image/jpeg",
        "is_supported": True,
        "is_readable": True,
        "page_count": 1,
        "status": "PASS",
        "reason": None,
    }
    assert body["extracted_data"]["total_amount"]["value"] == 13125.0
    assert body["validation"]["overall_status"] == "PASS"
    assert body["validation"]["checks"]
    assert body["processing_metadata"]["ocr_used"] is True
    assert body["processing_metadata"]["pages_processed"] == 1
    assert body["processing_metadata"]["processing_time_ms"] >= 0
    assert 0 < body["overall_confidence"] <= 1


def test_process_statement_records_a_failed_validation(client_factory):
    client = client_factory(factories.balance_sheet(balanced=False))
    response = upload(
        client, make_pdf(1), name="bs2024.pdf", doc_type="balance_sheet"
    )
    assert response.status_code == 200
    body = response.json()
    # Extraction succeeded, so processing passed; the reconciliation did not.
    assert body["processing_status"] == "PASS"
    assert body["validation"]["overall_status"] == "FAIL"
    assert body["validation"]["issues"]


@pytest.mark.parametrize(
    "doc_type,payload",
    [
        ("balance_sheet", factories.balance_sheet()),
        ("profit_and_loss", factories.profit_and_loss()),
        ("cash_flow_statement", factories.cash_flow()),
    ],
)
def test_all_statement_types_process(client_factory, doc_type, payload):
    client = client_factory(payload)
    response = upload(client, make_pdf(1), name=f"{doc_type}.pdf", doc_type=doc_type)
    assert response.status_code == 200
    assert response.json()["document_type"] == doc_type


def test_multi_page_pdf_is_accepted(client_factory):
    client = client_factory(factories.cash_flow())
    response = upload(
        client, make_pdf(2), name="cf.pdf", doc_type="cash_flow_statement"
    )
    assert response.status_code == 200
    assert response.json()["processing_metadata"]["pages_processed"] == 2


# ---------------------------------------------------------------------------
# Rejected uploads
# ---------------------------------------------------------------------------
def test_unsupported_file_type_is_rejected(client_factory):
    client = client_factory(factories.invoice())
    response = upload(
        client, b"just some text", name="notes.txt",
        doc_type="invoice", mime="text/plain",
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED_FILE_TYPE"
    assert error["message"] == "Only PDF / JPG / PNG documents are supported."
    assert error["request_id"]


def test_page_limit_is_enforced(client_factory):
    client = client_factory(factories.balance_sheet())
    response = upload(client, make_pdf(4), name="long.pdf", doc_type="balance_sheet")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PAGE_LIMIT_EXCEEDED"


def test_empty_file_is_rejected(client_factory):
    client = client_factory(factories.invoice())
    response = upload(client, b"", name="empty.pdf", doc_type="invoice")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EMPTY_FILE"


def test_corrupted_pdf_is_rejected(client_factory):
    client = client_factory(factories.invoice())
    response = upload(
        client, make_pdf(1)[:120], name="broken.pdf", doc_type="invoice"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CORRUPTED_FILE"


def test_unknown_document_type_is_rejected(client_factory):
    client = client_factory(factories.invoice())
    response = upload(client, make_pdf(1), name="x.pdf", doc_type="tax_return")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DOCUMENT_TYPE"


def test_missing_document_type_is_rejected(client_factory):
    client = client_factory(factories.invoice())
    response = client.post(
        "/api/v1/documents/process",
        files={"file": ("x.pdf", make_pdf(1), "application/pdf")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_rejected_upload_is_recorded_on_the_dashboard(client_factory):
    """A failed attempt must still be visible, not silently dropped."""
    client = client_factory(factories.invoice())
    upload(client, b"nope", name="bad.txt", doc_type="invoice", mime="text/plain")

    items = client.get("/api/v1/documents").json()["items"]
    failed = [i for i in items if i["document_name"] == "bad.txt"]
    assert len(failed) == 1
    assert failed[0]["processing_status"] == "FAILED"


def test_extraction_failure_returns_a_controlled_error(client_factory):
    stub = StubExtractionService(
        ExtractionError(log_detail="upstream exploded, do not leak this")
    )
    client = client_factory(stub=stub)
    response = upload(client, make_pdf(1), name="x.pdf", doc_type="invoice")
    assert response.status_code == 502
    body = response.text
    assert response.json()["error"]["code"] == "EXTRACTION_FAILED"
    assert "upstream exploded" not in body, "internal detail must not be exposed"
    assert "Traceback" not in body


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def test_get_by_name_returns_the_latest_result(client_factory):
    client = client_factory(factories.invoice())
    upload(client, make_pdf(1), name="invoice.pdf", doc_type="invoice")

    # Re-process the same name with a different payload.
    client.extractor._payload = factories.invoice(consistent=False)
    upload(client, make_pdf(1), name="invoice.pdf", doc_type="invoice")

    body = client.get("/api/v1/documents/invoice.pdf").json()
    assert body["extracted_data"]["total_amount"]["value"] == 14000.0
    assert body["validation"]["overall_status"] == "FAIL"


def test_get_by_name_is_case_insensitive(client_factory):
    client = client_factory(factories.invoice())
    upload(client, make_pdf(1), name="Invoice.PDF", doc_type="invoice")
    assert client.get("/api/v1/documents/invoice.pdf").status_code == 200


def test_get_by_unknown_name_is_404(client_factory):
    response = client_factory().get("/api/v1/documents/nothing-here.pdf")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


def test_list_shows_one_row_per_name_and_supports_filters(client_factory):
    client = client_factory(factories.invoice())
    upload(client, make_pdf(1), name="a.pdf", doc_type="invoice")
    upload(client, make_pdf(1), name="a.pdf", doc_type="invoice")  # reprocessed
    client.extractor._payload = factories.balance_sheet()
    upload(client, make_pdf(1), name="b.pdf", doc_type="balance_sheet")

    listing = client.get("/api/v1/documents").json()
    assert listing["total"] == 2, "the list must collapse re-runs to the latest"
    assert {i["document_name"] for i in listing["items"]} == {"a.pdf", "b.pdf"}

    filtered = client.get("/api/v1/documents?document_type=invoice").json()
    assert [i["document_name"] for i in filtered["items"]] == ["a.pdf"]

    searched = client.get("/api/v1/documents?search=B").json()
    assert [i["document_name"] for i in searched["items"]] == ["b.pdf"]


def test_list_pagination(client_factory):
    client = client_factory(factories.invoice())
    for index in range(5):
        upload(client, make_pdf(1), name=f"doc{index}.pdf", doc_type="invoice")

    page = client.get("/api/v1/documents?limit=2&offset=0").json()
    assert page["count"] == 2 and page["total"] == 5
    assert client.get("/api/v1/documents?limit=2&offset=4").json()["count"] == 1
    assert client.get("/api/v1/documents?limit=0").status_code == 422


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
def test_dashboard_renders(client_factory):
    response = client_factory().get("/")
    assert response.status_code == 200
    assert "Process a document" in response.text
    assert "Processed documents" in response.text


def test_result_page_renders_for_a_stored_document(client_factory):
    client = client_factory(factories.invoice())
    upload(client, make_pdf(1), name="shown.pdf", doc_type="invoice")
    response = client.get("/documents/shown.pdf")
    assert response.status_code == 200
    assert "Financial validation" in response.text
    assert "Raw JSON response" in response.text


def test_result_page_404s_for_an_unknown_document(client_factory):
    assert client_factory().get("/documents/missing.pdf").status_code == 404
