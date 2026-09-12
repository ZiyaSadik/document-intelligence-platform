"""Content-hashed cache-busting for the static assets.

Browsers cache ``/static/css/app.css`` heuristically when no explicit
``Cache-Control`` is sent, so after a redeploy a returning visitor keeps the
previous stylesheet and sees a stale - sometimes visibly broken - page. That is
not hypothetical: a CSS fix shipped correctly, the server served the new file,
and the browser still rendered the old one.

The fingerprint is a short hash of the concatenated asset contents, computed
once at startup and appended as ``?v=``. It changes only when an asset changes,
so caches stay warm across deploys that do not touch the frontend, and are
bypassed exactly when they must be.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Extensions worth fingerprinting - the ones the templates link to.
_TRACKED = (".css", ".js")


@lru_cache(maxsize=1)
def asset_version() -> str:
    """Short hash over every tracked static file, or a fallback."""
    static_dir: Path = settings.static_dir
    digest = hashlib.sha256()

    try:
        files = sorted(
            path
            for path in static_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in _TRACKED
        )
        for path in files:
            digest.update(path.relative_to(static_dir).as_posix().encode())
            digest.update(path.read_bytes())
    except OSError as exc:
        # Never let a fingerprinting problem stop the app from serving a page.
        logger.warning(
            "could not fingerprint static assets; falling back to app version",
            extra={"error": str(exc)},
        )
        return settings.app_version

    if not files:
        return settings.app_version

    version = digest.hexdigest()[:10]
    logger.info(
        "static assets fingerprinted",
        extra={"files": len(files), "asset_version": version},
    )
    return version
