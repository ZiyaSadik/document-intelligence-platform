"""Application configuration.

Every value is read from the environment (optionally via a local ``.env``).
No secret ever has a hard-coded default: an unset ``ANTHROPIC_API_KEY`` stays
``None`` and is reported as a degraded-but-running service by ``/api/v1/health``
rather than silently falling back to something.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repository root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


class Settings(BaseSettings):
    """Typed view over the process environment."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -----------------------------------------------------
    app_name: str = "Document Intelligence Platform"
    app_version: str = "1.0.0"
    environment: str = "local"
    log_level: str = "INFO"
    log_json: bool = True

    # --- Anthropic -------------------------------------------------------
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_max_tokens: int = 32_000
    anthropic_timeout_seconds: float = 300.0
    anthropic_max_retries: int = 2

    # --- Persistence -----------------------------------------------------
    database_url: str = "sqlite:///./data/app.db"

    # --- Upload / document validation ------------------------------------
    max_upload_mb: int = 20
    max_page_count: int = 3

    # --- OCR / rasterisation ---------------------------------------------
    text_layer_min_chars: int = 200
    raster_dpi: int = 200
    max_image_edge_px: int = 2000
    jpeg_quality: int = 85

    # --- Financial validation --------------------------------------------
    validation_abs_tolerance: float = 0.1
    validation_rel_tolerance: float = 0.00001

    # --- Derived ----------------------------------------------------------
    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def extraction_enabled(self) -> bool:
        """False when no API key is configured; the API still serves reads."""
        return bool(self.anthropic_api_key)

    @property
    def templates_dir(self) -> Path:
        return FRONTEND_ROOT / "templates"

    @property
    def static_dir(self) -> Path:
        return FRONTEND_ROOT / "static"

    @field_validator("anthropic_api_key", "database_url", mode="before")
    @classmethod
    def _strip_surrounding_whitespace(cls, value: object) -> object:
        """Trim stray whitespace and quotes from credential-shaped values.

        This is not cosmetic. A value that reaches the process with a leading
        space -- ``ANTHROPIC_API_KEY= sk-ant-...`` in an env file that Docker
        reads verbatim, or a newline pasted into a hosting platform's
        environment UI -- produces an *illegal HTTP header*, which the SDK
        surfaces as a bare "Connection error." The service looks healthy, the
        key looks present, and every upload fails with MODEL_UNAVAILABLE.
        Costing an hour to that is easy; preventing it is one line.
        """
        if isinstance(value, str):
            return value.strip().strip('"').strip("'").strip()
        return value

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        """Render/Heroku hand out ``postgres://`` URLs that SQLAlchemy rejects."""
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor so the environment is parsed exactly once."""
    return Settings()


settings = get_settings()
