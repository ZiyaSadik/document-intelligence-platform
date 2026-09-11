# Single-stage image: every Python dependency here ships as a binary wheel
# (pypdfium2, Pillow, psycopg-binary), so no apt-get layer and no compiler are
# needed. The image stays small and the build is reproducible.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so the layer caches across source changes.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Fallback location for the local SQLite database. On a platform with an
# ephemeral filesystem, set DATABASE_URL to a managed PostgreSQL instance
# instead - see README, "Persistence".
RUN mkdir -p /app/data

# Run unprivileged.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

WORKDIR /app/backend
EXPOSE 8000

# $PORT is injected by most hosting platforms; 8000 is the local default.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
