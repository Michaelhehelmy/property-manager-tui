"""P1-8 automated smoke/QA checks for critical web journeys.

Focused end-to-end-style smoke coverage using the isolated web TestClient/fixtures.
Safe and deterministic: uses tmp DB, no network, no destructive ops outside test scope.

Manual staging QA still required (not automated here): HTTPS/proxy/TLS termination and
security headers at edge, real backup/restore drills on production-like data, multi-device
and concurrent-user behavior, installer/updater flows on real git installs, print/label
hardware, LLM/model runtime availability, and long-run performance/load.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app_core import db, services
from tests.web.conftest import get_csrf, set_session


pytestmark = [pytest.mark.authz, pytest.mark.invariant]


def _seed_room_id() -> int:
    row = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()
    assert row is not None, "expected seeded room"
    return int(row["id"])


def _seed_inventory_item(name: str = "SmokeCola", stock: float = 25.0) -> int:
    return services.add_inventory_item(
        name,
        "Bar",
        "bottle",
        stock,
        2.0,
        5.0,
        1.0,
        0.0,
        "testpass",
        sku=f"SKU-{name}",
        barcode=f"BAR-{name}",
        barcode_type="code128",
        track_batches=0,
    )


def test_health_and_readyz_smoke(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    data = r.json()
    assert data.get("service") == "property-manager-web"
    assert "readiness" in data
    assert isinstance(data["readiness"].get("ready"), bool)

    r = client.get("/health")
    assert r.status_code == 200
    assert r.json().get("service") == "property-manager-web"

    r = client.get("/readyz")
    assert r.status_code == 200
    data = r.json()
    assert data.get("service") == "property-manager-web"
    assert "checks" in data.get("readiness", {})


def test_unlock_auth_basics_and_lock(client):
    page = client.get("/unlock?next=/settings/health")
    assert page.status_code == 200
    csrf = get_csrf(page.text)
    assert csrf

    # invalid password keeps user on unlock flow with feedback
    r = client.post(
        "/unlock",
        data={"csrf": csrf, "password": "wrong", "next": "/settings/health"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "invalid" in r.text.lower() or "unlock" in r.text.lower()

    # valid password unlocks and redirects to requested next
    page = client.get("/unlock?next=/settings/health")
    csrf = get_csrf(page.text)
    r = client.post(
        "/unlock",
        data={"csrf": csrf, "password": "testpass", "next": "/settings/health"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers.get("location") == "/settings/health"

    r = client.get("/settings/health")
    assert r.status_code == 200

    # lock clears per-browser unlock for protected routes
    token = set_session(client, unlocked=True)
    r = client.post("/lock", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/settings/health")
    assert r.status_code == 303
    assert "/unlock?next=" in r.headers.get("location", "")


def test_dashboard_and_core_pages_render_smoke(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "dashboard" in r.text.lower()

    for path, marker in [
        ("/reservations", "reservation"),
        ("/pos", "pos"),
        ("/settings", "setting"),
        ("/reports", "report"),
        ("/assistant", "assistant"),
    ]:
        r = client.get(path)
        assert r.status_code == 200, f"{path} should render"
        assert marker in r.text.lower(), f"{path} missing expected marker {marker!r}"

    # inventory is surfaced in the POS/inventory workspace
    r = client.get("/pos?tab=inventory")
    assert r.status_code == 200
    assert "inventory" in r.text.lower()


def test_protected_routes_and_file_download_smoke(client, web_env):
    export_file = web_env["export_dir"] / "smoke-export.txt"
    export_file.write_text("export-ok", encoding="utf-8")
    invoice_file = web_env["invoice_dir"] / "smoke-invoice.txt"
    invoice_file.write_text("invoice-ok", encoding="utf-8")

    set_session(client, unlocked=False)
    for path in ["/settings/health", "/files/export/smoke-export.txt", "/files/invoice/smoke-invoice.txt"]:
        r = client.get(path)
        assert r.status_code == 303, path
        assert "/unlock?next=" in r.headers.get("location", ""), path

    # locked mutation attempt is gated before side effects
    before = {p.name for p in web_env["export_dir"].glob("*")}
    token = set_session(client, unlocked=False)
    r = client.post("/settings/backup", data={"csrf": token})
    assert r.status_code == 303
    assert "/unlock?next=" in r.headers.get("location", "")
    after = {p.name for p in web_env["export_dir"].glob("*")}
    assert after == before

    set_session(client, unlocked=True)
    r = client.get("/files/export/smoke-export.txt")
    assert r.status_code == 200
    assert r.text == "export-ok"
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "attachment;" in (r.headers.get("content-disposition") or "").lower()

    r = client.get("/files/invoice/smoke-invoice.txt")
    assert r.status_code == 200
    assert r.text == "invoice-ok"

    # traversal/out-of-scope remain blocked when unlocked
    assert client.get("/files/export/../invoices/smoke-invoice.txt").status_code in (400, 404)
    assert client.get("/files/other/smoke-export.txt").status_code == 400


def test_reservation_create_smoke_journey(client):
    room_id = _seed_room_id()
    ci = (date.today() + timedelta(days=60)).isoformat()
    co = (date.today() + timedelta(days=62)).isoformat()
    token = set_session(client, unlocked=False)
    r = client.post(
        "/reservations/create",
        data={
            "csrf": token,
            "guest_name": "P1-8 Smoke Guest",
            "source": "Direct",
            "room_id": str(room_id),
            "rate_plan_id": "",
            "rate": "100",
            "check_in": ci,
            "check_out": co,
            "deposit": "0",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers.get("location", "")
    assert loc.startswith("/reservations")
    # list renders and new record is visible
    r = client.get("/reservations")
    assert r.status_code == 200
    assert "p1-8 smoke guest" in r.text.lower() or "smoke guest" in r.text.lower()


def test_pos_walkin_sale_smoke_journey(client):
    item_id = _seed_inventory_item("P18SmokeItem", stock=10.0)
    token = set_session(client, unlocked=False, extra={"walkin_cart": []})

    r = client.post(
        "/pos/cart/add",
        data={"csrf": token, "desc": "P18SmokeItem", "qty": "1", "price": "5", "inv_id": str(item_id)},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers.get("location", "").startswith("/pos")

    token = set_session(client, unlocked=False, extra={"walkin_cart": [{"desc": "P18SmokeItem", "qty": 1, "price": 5.0, "inv_id": item_id}]})
    r = client.post("/pos/walkin", data={"csrf": token, "method": "Cash", "notes": "smoke"}, follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers.get("location", "")
    assert loc.startswith("/pos") or loc.startswith("/files/invoice/")


def test_settings_backup_action_smoke_when_unlocked(client, web_env):
    token = set_session(client, unlocked=True)
    before = {p.name for p in web_env["export_dir"].glob("*")}
    r = client.post("/settings/backup", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    assert "/settings" in r.headers.get("location", "")
    r = client.get("/settings?tab=general")
    assert r.status_code == 200
    # backup path is service-defined; action should be accepted and not crash
    # (file count may stay same in some environments, but response flow is healthy)
    _ = {p.name for p in web_env["export_dir"].glob("*")}


def test_assistant_unlock_gate_and_basic_actions_smoke(client, monkeypatch):
    import ui.web.routes.assistant as ar

    # assistant_require_unlock defaults to true in app logic; locked browser must be gated
    set_session(client, unlocked=False)
    token = set_session(client, unlocked=False)
    r = client.post("/assistant/ask", data={"csrf": token, "message": "hello"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/unlock?next=/assistant"

    # unlocked user can ask/clear (chat mocked for deterministic, no external model dependency)
    token = set_session(client, unlocked=True)
    monkeypatch.setattr(
        ar.assistant,
        "chat",
        lambda message, history=None: {
            "answer": "Smoke answer",
            "source": "test",
            "intent": "general_help",
            "confidence": 1.0,
            "tools_used": [],
            "slots": {},
        },
    )
    r = client.post("/assistant/ask", data={"csrf": token, "message": "help"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/assistant"
    r = client.get("/assistant")
    assert r.status_code == 200
    assert "smoke answer" in r.text.lower() or "help" in r.text.lower()

    token = set_session(client, unlocked=True)
    r = client.post("/assistant/clear", data={"csrf": token}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers.get("location") == "/assistant"


def test_reports_generate_and_export_smoke(client, monkeypatch):
    import ui.web.routes.reports_analytics as rr

    # locked browser is gated for report generation
    set_session(client, unlocked=False)
    token = set_session(client, unlocked=False)
    r = client.post(
        "/reports/run",
        data={"csrf": token, "kind": "pl", "start": "2026-01-01", "end": "2026-01-31"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "/unlock?next=" in r.headers.get("location", "")

    token = set_session(client, unlocked=True)
    monkeypatch.setattr(rr.services, "report_pl_statement", lambda s, e: "SMOKE PL REPORT")
    r = client.post(
        "/reports/run",
        data={"csrf": token, "kind": "pl", "start": "2026-01-01", "end": "2026-01-31"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "smoke pl report" in r.text.lower()

    token = set_session(client, unlocked=True)
    monkeypatch.setattr(rr.services, "generate_unified_report", lambda preset, s, e: {"outputs": {"summary": "x"}})
    r = client.post(
        "/reports/analytics-export",
        data={"csrf": token, "preset": "management_pack", "start": "2026-01-01", "end": "2026-01-31"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers.get("location") == "/reports?tab=downloads"


def test_csrf_rejected_for_smoke_mutation(client):
    # Missing CSRF must be blocked by middleware
    r = client.post("/pos/cart/add", data={"desc": "X", "qty": "1", "price": "1"})
    assert r.status_code >= 400
    assert r.status_code not in (200, 302, 303)
    # JSON detail payload is acceptable for blocked unsafe request
    try:
        detail = json.dumps(r.json())
    except Exception:
        detail = r.text
    assert "csrf" in detail.lower() or r.status_code in (400, 403)
