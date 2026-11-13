# Minimal container for Property Manager Web UI (FastAPI/HTMX).
# - SQLite-safe: ALWAYS run uvicorn with --workers 1
# - Runtime data is persisted via PM_DATA_DIR (/data) volume mount
# - Put a reverse proxy (Caddy/Nginx/Traefik) in front for HTTPS/TLS in production

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PM_DATA_DIR=/data

WORKDIR /app

# Install runtime deps first for better layer caching
COPY requirements.txt requirements-web.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt -r requirements-web.txt

# Copy application source
COPY pyproject.toml main.py launcher.py ./
COPY src ./src
COPY ui ./ui
COPY scripts ./scripts

# Ensure persistent runtime directories exist (DB/exports/invoices live under PM_DATA_DIR)
RUN mkdir -p /data/exports /data/invoices

EXPOSE 8000

# IMPORTANT:
# - Set PM_WEB_SECRET_KEY to a long random value for any non-local/shared deployment.
# - Do not increase workers with SQLite.
CMD ["python", "-m", "uvicorn", "ui.web.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]