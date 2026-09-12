"""Server-rendered pages.

The frontend is deliberately thin: these two routes render templates, and the
browser then talks to the same REST API an evaluator would call with curl. That
keeps one source of truth for the response shape and means the dashboard cannot
drift from the documented contract.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import DocumentServiceDep
from app.core.config import settings
from app.schemas.enums import DocumentType
from app.utils.assets import asset_version

router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(directory=str(settings.templates_dir))


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "document_types": [(t.value, t.label) for t in DocumentType],
            "extraction_configured": settings.extraction_enabled,
            "max_upload_mb": settings.max_upload_mb,
            "max_page_count": settings.max_page_count,
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "asset_version": asset_version(),
        },
    )


@router.get("/documents/{document_name}", response_class=HTMLResponse)
def document_result(
    request: Request,
    service: DocumentServiceDep,
    document_name: Annotated[str, Path()],
) -> HTMLResponse:
    record = service.get_by_name(document_name)
    return templates.TemplateResponse(
        request=request,
        name="document_result.html",
        context={
            "document_name": document_name,
            "found": record is not None,
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "asset_version": asset_version(),
        },
        status_code=200 if record is not None else 404,
    )
