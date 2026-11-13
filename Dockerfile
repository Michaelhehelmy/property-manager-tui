# Minimal container for Property Manager Web UI (FastAPI/HTMX).
# - SQLite-safe: ALWAYS run uvicorn with --workers 1
# - Runtime data is persisted via PM_DATA_DIR (/data) volume mount
# - Put a reverse proxy (Caddy/Nginx/Traefik) in front for HTTPS/TLS in production
# - Runs as non-root user (uid/gid 10001) by default; ensure /data volume permissions allow writes

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
# and run as non-root where feasible. If mounting an existing volume, chown/adjust host perms as needed.
RUN groupadd --gid 10001 pmapp \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/pmapp --shell /usr/sbin/nologin pmapp \
    && mkdir -p /data/exports /data/invoices \
    && chown -R 10001:10001 /app /data

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"]

# IMPORTANT:
# - Set PM_WEB_SECRET_KEY to a long random value for any non-local/shared deployment.
# - Set PM_WEB_COOKIE_SECURE=1 when behind HTTPS reverse proxy.
# - Do not increase workers with SQLite.
CMD ["python", "-m", "uvicorn", "ui.web.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]