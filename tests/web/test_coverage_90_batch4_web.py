from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tests.web.conftest import get_csrf, set_session
from ui.web import deps


def test_server_runtime_readiness_and_middleware_edges(web_env, monkeypatch):
    import ui.web.server as srv
    import app_core.paths as paths
    import app_core.db as db

    # _runtime_readiness exception branches
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    out = srv._runtime_readiness(include_checks=True)
    assert out["ready"] is False
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: {"data_dir": {"writable": True, "exists": True}, "db_path": {"writable": True}, "export_dir": {"writable": True}, "invoice_dir": {"writable": True}})
    monkeypatch.setattr(db, "is_initialized", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    out = srv._runtime_readiness(include_checks=True)
    assert out["db_initialized"] is False and "checks" in out

    # create_app startup warning branches
    import app_core.services as services
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.delenv("PM_WEB_SECRET_KEY", raising=False)
    monkeypatch.setattr(paths, "check_runtime_permissions", lambda: {"data_dir": {"writable": False}})
    monkeypatch.setattr(services, "init_db", lambda: None)
    monkeypatch.setattr(services, "set_language", lambda x: None)
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: "en")
    monkeypatch.setattr(services, "get_app_display_name", lambda: "PM")
    app = srv.create_app(init_db=True, run_startup=True)
    assert app is not None

    # startup exception branch
    def boom_perm():
        raise RuntimeError("permboom")
    monkeypatch.setattr(paths, "check_runtime_permissions", boom_perm)
    app2 = srv.create_app(init_db=True, run_startup=True)
    assert app2 is not None

    # restore secret for session helpers
    monkeypatch.setenv("PM_WEB_SECRET_KEY", "test-secret")

    # middleware: actor set/reset exceptions, HTTPException logging, unhandled exception HTML/JSON, request log exception
    app = srv.create_app(init_db=False, run_startup=False)

    @app.post("/_boom_html")
    def boom_html():
        raise RuntimeError("boom-html")

    @app.post("/_boom_json")
    def boom_json():
        raise RuntimeError("boom-json")

    @app.get("/_http_exc")
    def http_exc():
        raise HTTPException(status_code=418, detail="teapot")

    monkeypatch.setattr(services, "set_current_actor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("actor")))
    monkeypatch.setattr(services, "reset_current_actor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reset")))
    with TestClient(app, raise_server_exceptions=False) as c:
        token = set_session(c, unlocked=True, csrf="tok")
        # json csrf path + unhandled exception json
        r = c.post("/_boom_json", headers={"x-csrf-token": "tok", "Accept": "application/json"})
        assert r.status_code == 500 and "error_ref" in r.json()
        # html error path
        token = set_session(c, unlocked=True, csrf="tok2")
        r = c.post("/_boom_html", data={"csrf": "tok2"}, headers={"Accept": "text/html"})
        assert r.status_code == 500
        # http exception path
        r = c.get("/_http_exc")
        assert r.status_code == 418

    # request logging exception branch (best-effort) - monkeypatch logger.info to raise
    app = srv.create_app(init_db=False, run_startup=False)
    @app.get("/_oklog")
    def oklog():
        return {"ok": True}
    orig_info = srv.logger.info
    def flaky_info(msg, *a, **k):
        if isinstance(msg, str) and msg.startswith("web_request "):
            raise RuntimeError("logfail")
        return orig_info(msg, *a, **k)
    monkeypatch.setattr(srv.logger, "info", flaky_info)
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/_oklog")
        assert r.status_code == 200
    monkeypatch.setattr(srv.logger, "info", orig_info)


def test_settings_routes_coverage_edges(client, monkeypatch):
    import ui.web.routes.settings_branding as sbr
    import app_core.services as services

    # recovery unlock failure branch
    monkeypatch.setattr(sbr.services, "use_recovery_code", lambda c,n,cf: None)
    monkeypatch.setattr(deps, "unlock", lambda request, password: False)
    page = client.get("/unlock/recovery"); csrf = get_csrf(page.text)
    r = client.post("/unlock/recovery", data={"csrf": csrf, "code": "c", "new_password": "np", "confirm_password": "np", "next": "/settings"})
    assert r.status_code == 303 and "/unlock/recovery" in r.headers["location"]

    # settings page exception branches for demo_presets and update_local
    monkeypatch.setattr(sbr.services, "list_demo_data_presets", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(sbr.updater, "get_local_state", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings?tab=invalid")
    assert r.status_code == 200

    # setup redirect when configured and post exception
    monkeypatch.setattr(sbr.services, "is_branding_configured", lambda: True)
    r = client.get("/setup")
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "is_branding_configured", lambda: False)
    page = client.get("/setup"); csrf = get_csrf(page.text)
    monkeypatch.setattr(sbr.services, "apply_branding_settings", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    r = client.post("/setup", data={"csrf": csrf, "display_name": "X"})
    assert r.status_code == 303 and r.headers["location"] == "/setup"
    monkeypatch.setattr(sbr.services, "apply_branding_settings", lambda *a, **k: None)
    r = client.post("/setup", data={"csrf": csrf, "display_name": "X"})
    assert r.status_code == 303 and r.headers["location"] == "/"

    # rate plans page exception branches
    token = set_session(client, unlocked=True)
    monkeypatch.setattr(sbr.services, "get_all_rate_plans", lambda: [{"id": 1, "name": "A", "price_modifier": 1}])
    monkeypatch.setattr(sbr.services, "count_reservations_using_plan", lambda pid: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(sbr.services, "list_rate_rules", lambda active_only=False: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(sbr.services, "get_all_rooms", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings/rate-plans")
    assert r.status_code == 200

    # rate plan/rule add/edit/delete exception branches
    token = set_session(client, unlocked=True, csrf="t1")
    monkeypatch.setattr(sbr.services, "add_rate_plan", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/rate-plans/add", data={"csrf": "t1", "name": "A", "modifier": "1", "password": "testpass"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "update_rate_plan", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/rate-plans/1/edit", data={"csrf": "t1", "name": "B", "password": "testpass"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "delete_rate_plan", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/rate-plans/1/delete", data={"csrf": "t1", "password": "testpass"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "add_rate_rule", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/rate-rules/add", data={"csrf": "t1", "name": "R"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "update_rate_rule", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/rate-rules/1/edit", data={"csrf": "t1", "name": "R"})
    assert r.status_code == 303

    # users login/logout branches
    page = client.get("/settings/users"); csrf = get_csrf(page.text)
    monkeypatch.setattr(sbr.services, "verify_user_password", lambda u,p: None)
    r = client.post("/settings/users/login", data={"csrf": csrf, "username": "u", "user_password": "p"})
    assert r.status_code == 303
    user = {"id": 1, "username": "u", "display_name": "U", "role": "staff"}
    monkeypatch.setattr(sbr.services, "verify_user_password", lambda u,p: user)
    monkeypatch.setattr(sbr.services, "_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.post("/settings/users/login", data={"csrf": csrf, "username": "u", "user_password": "p"})
    assert r.status_code == 303
    page = client.get("/settings/users"); csrf = get_csrf(page.text)
    monkeypatch.setattr(sbr.services, "_audit", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.post("/settings/users/logout", data={"csrf": csrf})
    assert r.status_code == 303
    # logout with no current user
    token = set_session(client, unlocked=True, csrf="t2")
    r = client.post("/settings/users/logout", data={"csrf": "t2"})
    assert r.status_code == 303

    # settings users/health page exception branches
    set_session(client, unlocked=True)
    monkeypatch.setattr(sbr.services, "list_users", lambda active_only=False: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(sbr.services, "list_roles", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings/users")
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "verify_ledger_integrity", lambda: {"ok": True, "stats": {}, "issues": [], "warnings": [], "checked_at": "now", "note": ""})
    r = client.get("/settings/health")
    assert r.status_code == 200

    # user add/edit exceptions
    token = set_session(client, unlocked=True, csrf="t3")
    monkeypatch.setattr(sbr.services, "create_user", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/users/add", data={"csrf": "t3", "username": "u", "new_password": "p"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "update_user", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/users/1/edit", data={"csrf": "t3", "display_name": "d"})
    assert r.status_code == 303

    # seed export/import/export-inventory-csv branches
    token = set_session(client, unlocked=True, csrf="t4")
    monkeypatch.setattr(sbr.services, "export_seed_config", lambda: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/seed/export", data={"csrf": "t4"})
    assert r.status_code == 303
    # import validation branches
    r = client.post("/settings/seed/import", data={"csrf": "t4"})
    assert r.status_code == 303
    r = client.post("/settings/seed/import", data={"csrf": "t4"}, files={"seed_file": ("x.txt", b"{}", "text/plain")})
    assert r.status_code == 303
    r = client.post("/settings/seed/import", data={"csrf": "t4"}, files={"seed_file": ("x.json", b"{}", "application/pdf")})
    assert r.status_code == 303
    r = client.post("/settings/seed/import", data={"csrf": "t4"}, files={"seed_file": ("x.json", b"x" * (5*1024*1024 + 1), "application/json")})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "import_seed_config", lambda *a, **k: {"summary": {"ok": 1}})
    r = client.post("/settings/seed/import", data={"csrf": "t4", "confirm": "x"}, files={"seed_file": ("x.json", b"{}", "application/json")})
    assert r.status_code == 303
    r = client.post("/settings/seed/import", data={"csrf": "t4", "confirm": "x"}, files={"seed_file": ("x.csv", b"a,b\n1,2", "text/csv")})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "export_inventory_seed_csv", lambda template=False: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/seed/export-inventory-csv", data={"csrf": "t4", "template": "1"})
    assert r.status_code == 303

    # rooms add exception
    monkeypatch.setattr(sbr.services, "add_room", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/rooms/add", data={"csrf": "t4", "room_number": "1", "room_type": "T", "base_price": "10"})
    assert r.status_code == 303

    # vouchers pages and exception branches
    monkeypatch.setattr(sbr.services, "list_discount_vouchers", lambda active_only=False: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings/vouchers")
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "create_discount_voucher", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/vouchers/add", data={"csrf": "t4", "code": "C", "value": "1"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "update_discount_voucher", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/vouchers/1/edit", data={"csrf": "t4", "code": "C", "value": "1"})
    assert r.status_code == 303

    # service catalog and packages exception branches
    monkeypatch.setattr(sbr.services, "list_service_catalog", lambda active_only=False: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings/service-catalog")
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "create_service_catalog_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/service-catalog/add", data={"csrf": "t4", "name": "S", "unit_price": "1"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "update_service_catalog_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/service-catalog/1/edit", data={"csrf": "t4", "name": "S", "unit_price": "1"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "list_packages", lambda active_only=False: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings/packages")
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "list_service_catalog", lambda active_only=True: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(sbr.services, "list_packages", lambda active_only=False: [])
    r = client.get("/settings/packages")
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "create_package", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/packages/add", data={"csrf": "t4", "name": "P"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "update_package", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/packages/1/edit", data={"csrf": "t4", "name": "P"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "add_package_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/packages/1/items/add", data={"csrf": "t4", "description": "d"})
    assert r.status_code == 303
    monkeypatch.setattr(sbr.services, "delete_package_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("e")))
    r = client.post("/settings/packages/items/1/delete", data={"csrf": "t4"})
    assert r.status_code == 303

    # approvals exceptions
    monkeypatch.setattr(sbr.services, "list_approvals", lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings/approvals")
    assert r.status_code == 200
    r = client.post("/settings/approvals/request", data={"csrf": "t4", "action_type": "x", "payload_json": "{bad"})
    assert r.status_code == 303
