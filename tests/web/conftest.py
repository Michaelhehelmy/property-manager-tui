"""Web UI test fixtures (isolated DB + TestClient helpers)."""
from __future__ import annotations

import os
import re
import secrets
import sys
from pathlib import Path

# Must be set before importing ui.web.deps so the serializer secret is deterministic.
os.environ.setdefault("PM_WEB_SECRET_KEY", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeSerializer

from app_core import db, paths, services
from ui.web import deps


def _serializer():
    return URLSafeSerializer(os.environ["PM_WEB_SECRET_KEY"], salt="pm-session")


@pytest.fixture
def web_env(tmp_path, monkeypatch):
    """Isolated DB/paths for web tests (no module-level app DB state)."""
    try:
        db.close()
    except Exception:
        pass

    test_db = tmp_path / "test.db"
    export_dir = tmp_path / "exports"
    invoice_dir = tmp_path / "invoices"
    data_dir = tmp_path

    for mod in (db, paths):
        monkeypatch.setattr(mod, "DB_PATH", test_db, raising=False)
        monkeypatch.setattr(mod, "DATA_DIR", data_dir, raising=False)
        monkeypatch.setattr(mod, "EXPORT_DIR", export_dir, raising=False)
        monkeypatch.setattr(mod, "INVOICE_DIR", invoice_dir, raising=False)
        monkeypatch.setattr(mod, "BASE_DIR", data_dir, raising=False)
    monkeypatch.setattr(db, "_conn", None, raising=False)
    monkeypatch.setattr(services, "EXPORT_DIR", export_dir, raising=False)
    monkeypatch.setattr(services, "INVOICE_DIR", invoice_dir, raising=False)
    monkeypatch.setattr(services, "ANALYTICS_EXPORT_DIR", export_dir / "analytics", raising=False)
    if hasattr(services, "DATA_DIR"):
        monkeypatch.setattr(services, "DATA_DIR", data_dir, raising=False)

    export_dir.mkdir(parents=True, exist_ok=True)
    invoice_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "analytics").mkdir(parents=True, exist_ok=True)

    services._session["unlocked"] = False
    services._session["last_activity"] = None
    try:
        services.set_language("en")
    except Exception:
        pass

    services.init_db()
    services.set_super_password("testpass")

    yield {
        "tmp_path": tmp_path,
        "db_path": test_db,
        "export_dir": export_dir,
        "invoice_dir": invoice_dir,
    }

    try:
        db.close()
    except Exception:
        pass


@pytest.fixture
def web_app(web_env):
    from ui.web.server import create_app

    return create_app(init_db=False, run_startup=False)


@pytest.fixture
def client(web_app):
    with TestClient(web_app, raise_server_exceptions=False, follow_redirects=False) as c:
        yield c


def get_csrf(html: str) -> str | None:
    m = re.search(r'name=["\']csrf["\']\s+value=["\']([^"\']+)["\']', html)
    if m:
        return m.group(1)
    m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']csrf["\']', html)
    return m.group(1) if m else None


def make_session_cookie(unlocked: bool = False, csrf: str | None = None, extra: dict | None = None) -> str:
    data = {"csrf": csrf or secrets.token_urlsafe(24), "unlocked": unlocked, "flashes": []}
    if extra:
        data.update(extra)
    return _serializer().dumps(data)


def set_session(client: TestClient, unlocked: bool = False, csrf: str | None = None, extra: dict | None = None) -> str:
    token = csrf or secrets.token_urlsafe(24)
    # Avoid duplicate pm_session cookies (path/domain variants) causing httpx CookieConflict.
    try:
        client.cookies.clear()
    except Exception:
        pass
    client.cookies.set(deps.SESSION_COOKIE, make_session_cookie(unlocked=unlocked, csrf=token, extra=extra))
    return token
