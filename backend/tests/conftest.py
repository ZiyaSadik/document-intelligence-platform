"""Shared test fixtures.

Two things every test here relies on:

* The database is a throwaway SQLite file per test, so tests never see each
  other's rows and none of them touch the developer's local database.
* The Anthropic client is always a stub. Nothing in this suite makes a network
  call, so it runs identically in CI and with no API key present.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Configure the environment before app.core.config is imported anywhere.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
os.environ.setdefault("LOG_JSON", "false")
os.environ.setdefault("LOG_LEVEL", "WARNING")


# ---------------------------------------------------------------------------
# Sample file builders
# ---------------------------------------------------------------------------
def make_pdf(pages: int = 1) -> bytes:
    """A structurally valid, blank PDF with the requested page count."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    for _ in range(pages):
        pdf.new_page(612, 792)
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


def make_image(fmt: str = "JPEG", size: tuple[int, int] = (600, 800)) -> bytes:
    from PIL import Image

    image = Image.new("RGB", size, (250, 250, 250))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def pdf_bytes() -> bytes:
    return make_pdf(1)


@pytest.fixture
def jpeg_bytes() -> bytes:
    return make_image("JPEG")


@pytest.fixture
def png_bytes() -> bytes:
    return make_image("PNG")


# ---------------------------------------------------------------------------
# Database / application
# ---------------------------------------------------------------------------
@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """A session bound to a fresh SQLite file for this test only."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import Base
    from app.models import document as _document  # noqa: F401

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class StubExtractionService:
    """Stands in for the model call.

    Returns whatever extraction object it was constructed with, and records the
    page content it was handed so tests can assert on what the pipeline read.
    """

    def __init__(self, payload: Any, *, model: str = "stub-model") -> None:
        self._payload = payload
        self._model = model
        self.calls: list[dict[str, Any]] = []
        self.is_configured = True

    def extract(self, content, *, document_type, filename):
        from app.services.extraction_service import ExtractionResult

        self.calls.append(
            {
                "filename": filename,
                "document_type": document_type,
                "pages": content.page_count,
                "ocr_used": content.ocr_used,
            }
        )
        if isinstance(self._payload, Exception):
            raise self._payload
        return ExtractionResult(data=self._payload, model=self._model)


@pytest.fixture
def client_factory(tmp_path, monkeypatch):
    """Build a TestClient whose extraction service is a stub.

    Usage::

        client = client_factory(some_invoice_extraction)
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.database import Base
    from app.models import document as _document  # noqa: F401

    engine = create_engine(
        f"sqlite:///{tmp_path / 'api.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    created: list[TestClient] = []

    def _build(extraction_payload: Any = None, *, stub: Any = None) -> TestClient:
        from app.api.deps import get_document_service
        from app.core.database import get_session
        from app.main import app
        from app.services.document_service import DocumentService

        extractor = stub or StubExtractionService(extraction_payload)

        def override_session():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        def override_service(session=None):
            return DocumentService(
                session=factory(), extraction_service=extractor  # type: ignore[arg-type]
            )

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_document_service] = override_service

        # init_db() in the lifespan would target the real engine, so the
        # context manager is skipped and the schema is created above instead.
        client = TestClient(app)
        client.extractor = extractor  # type: ignore[attr-defined]
        created.append(client)
        return client

    yield _build

    from app.main import app

    app.dependency_overrides.clear()
    for client in created:
        client.close()
    engine.dispose()
