"""Health check.

Reports liveness plus the two things that actually stop this service working:
database reachability and whether extraction is configured. It always returns
200 when the process is up - a load balancer should not take the instance out
of rotation because an API key is missing - and puts the detail in the body.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import engine
from app.core.logging import get_logger
from app.schemas.document import HealthResponse

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health",
    description=(
        "Liveness probe. Always returns 200 while the process is running; "
        "check `database` and `extraction_configured` for degraded state."
    ),
)
def health() -> HealthResponse:
    database = "connected"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        database = "unavailable"
        logger.error("health check: database unreachable", extra={"error": str(exc)})

    return HealthResponse(
        status="ok" if database == "connected" else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database=database,
        extraction_configured=settings.extraction_enabled,
        model=settings.anthropic_model if settings.extraction_enabled else None,
        timestamp=datetime.now(timezone.utc),
    )
