"""FastAPI application entry point.

Wires the routers, mounts the frontend, and installs the exception handlers
that guarantee every failure leaves through the same door: a JSON body of the
shape ``{"error": {"code", "message", "request_id"}}``, with the internal
detail written to the log and never to the response.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import documents, health, ui
from app.core.config import settings
from app.core.database import init_db
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger, set_request_id

configure_logging()
logger = get_logger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "starting application",
        extra={
            "version": settings.app_version,
            "environment": settings.environment,
            "model": settings.anthropic_model,
            "extraction_configured": settings.extraction_enabled,
        },
    )
    init_db()
    if not settings.extraction_enabled:
        # Loud, but not fatal: reads and the dashboard still work, and the
        # health endpoint reports it. Only uploads will be refused.
        logger.warning(
            "ANTHROPIC_API_KEY is not configured; document processing will be "
            "rejected with MODEL_NOT_CONFIGURED"
        )
    yield
    logger.info("shutting down application")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Extracts, validates and stores structured data from invoices and "
        "financial statements.\n\n"
        "Upload a PDF / JPG / PNG of at most three pages to "
        "`POST /api/v1/documents/process` together with its document type. The "
        "service validates the file, reads it (native text layer where present, "
        "page rasterisation plus a vision model otherwise), extracts every "
        "visible field and table value with page-level evidence, runs the "
        "financial reconciliations for that document type, and stores the "
        "result for retrieval by name."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a correlation id and log the outcome of every request."""
    request_id = set_request_id(request.headers.get("x-request-id"))
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        # The exception handlers below cover routed errors; this catches
        # anything raised in middleware or during response streaming.
        logger.exception(
            "unhandled error",
            extra={"path": request.url.path, "method": request.method},
        )
        raise

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["x-request-id"] = request_id
    logger.info(
        "request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": elapsed_ms,
        },
    )
    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
def _envelope(code: str, message: str, status_code: int) -> JSONResponse:
    from app.core.logging import get_request_id

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message, "request_id": get_request_id()}
        },
    )


@app.exception_handler(AppError)
async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    log = logger.warning if exc.status_code < 500 else logger.error
    log(
        "request failed",
        extra={
            "code": exc.code,
            "status_code": exc.status_code,
            "detail": exc.log_detail,
            **exc.context,
        },
    )
    return _envelope(exc.code, exc.message, exc.status_code)


@app.exception_handler(RequestValidationError)
async def handle_request_validation(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    # Summarise which fields were wrong without echoing submitted values back.
    fields = ", ".join(
        ".".join(str(part) for part in error.get("loc", ())[1:]) or "body"
        for error in exc.errors()
    )
    logger.warning("request validation failed", extra={"fields": fields})
    return _envelope(
        "INVALID_REQUEST",
        f"The request is missing or has invalid fields: {fields}.",
        422,
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(
    _: Request, exc: StarletteHTTPException
) -> JSONResponse:
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "FILE_TOO_LARGE"}
    return _envelope(
        codes.get(exc.status_code, "HTTP_ERROR"),
        str(exc.detail),
        exc.status_code,
    )


@app.exception_handler(Exception)
async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
    # The traceback goes to the log; the client gets a generic message so no
    # internal path, query or secret can leak through an error body.
    logger.exception("unhandled exception", extra={"error_type": type(exc).__name__})
    return _envelope(
        "INTERNAL_ERROR",
        "An unexpected error occurred while processing the request.",
        500,
    )


# ---------------------------------------------------------------------------
# Routes and static assets
# ---------------------------------------------------------------------------
app.include_router(health.router, prefix=API_PREFIX)
app.include_router(documents.router, prefix=API_PREFIX)
app.include_router(ui.router)

if settings.static_dir.is_dir():
    app.mount(
        "/static", StaticFiles(directory=str(settings.static_dir)), name="static"
    )
else:  # pragma: no cover - only if the frontend directory is missing
    logger.warning(
        "static directory not found; the dashboard will render unstyled",
        extra={"path": str(settings.static_dir)},
    )
