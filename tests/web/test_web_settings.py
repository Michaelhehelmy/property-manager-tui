from __future__ import annotations

from urllib.parse import quote

from app_core import services
from tests.web.conftest import get_csrf, set_session
from ui.web import deps


def test_unlock_lock_recovery(client, monkeypatch):
    r = client.get("/unlock?next=/settings")
    assert r.status_code == 200
    csrf = get_csrf(r.text)

    # fail
    r = client.post("/unlock", data={"csrf": csrf, "password": "wrong", "next": "/settings"}, follow_redirects=True)
    assert r.status_code == 200
    # success
    page = client.get("/unlock"); csrf = get_csrf(page.text)
    r = client.post("/unlock", data={"csrf": csrf, "password": "testpass", "next": "/settings"})
    assert r.status_code == 303 and r.headers["location"] == "/settings"

    # lock
    token = set_session(client, unlocked=True)
    r = client.post("/lock", data={"csrf": token})
    assert r.status_code == 303

    # recovery page
    r = client.get("/unlock/recovery?next=/x")
    assert r.status_code == 200

    import ui.web.routes.settings_branding as sbr
    # recovery success
    monkeypatch.setattr(sbr.services, "use_recovery_code", lambda c,n,cf: None)
    monkeypatch.setattr(deps, "unlock", lambda request, password: True)
    monkeypatch.setattr(sbr.services, "unlock_session", lambda p: True)
    monkeypatch.setattr(sbr.services, "touch_session", lambda: None)
    page = client.get("/unlock/recovery"); csrf = get_csrf(page.text)
    r = client.post("/unlock/recovery", data={"csrf": csrf, "code": "c", "new_password": "np", "confirm_password": "np", "next": "/settings"})
    assert r.status_code == 303 and r.headers["location"] == "/settings"

    # next sanitization
    monkeypatch.setattr(deps, "unlock", lambda request, password: True)
    page = client.get("/unlock/recovery"); csrf = get_csrf(page.text)
    r = client.post("/unlock/recovery", data={"csrf": csrf, "code": "c", "new_password": "np", "confirm_password": "np", "next": "//evil"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    page = client.get("/unlock/recovery"); csrf = get_csrf(page.text)
    r = client.post("/unlock/recovery", data={"csrf": csrf, "code": "c", "new_password": "np", "confirm_password": "np", "next": "http://x"})
    assert r.status_code == 303 and r.headers["location"] == "/"

    # recovery exception
    monkeypatch.setattr(sbr.services, "use_recovery_code", lambda c,n,cf: (_ for _ in ()).throw(ValueError("bad code")))
    page = client.get("/unlock/recovery"); csrf = get_csrf(page.text)
    r = client.post("/unlock/recovery", data={"csrf": csrf, "code": "c", "new_password": "np", "confirm_password": "np", "next": "/ok"})
    assert r.status_code == 303 and "/unlock/recovery?next=" in r.headers["location"]


def test_settings_pages_and_actions(client, monkeypatch):
    r = client.get("/settings")
    assert r.status_code == 200

    page = client.get("/settings"); csrf = get_csrf(page.text)
    import ui.web.routes.settings_branding as sbr

    # branding success/error
    monkeypatch.setattr(sbr.services, "apply_branding_settings", lambda *a, **k: None)
    r = client.post("/settings/branding", data={"csrf": csrf, "display_name": "X", "tagline": "T", "animation": "reveal"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "apply_branding_settings", lambda *a, **k: (_ for _ in ()).throw(ValueError("b")))
    r = client.post("/settings/branding", data={"csrf": csrf, "display_name": "X"}, follow_redirects=True)
    assert r.status_code == 200

    # save settings requires unlock
    token_locked = set_session(client, unlocked=False)
    r = client.post("/settings/save", data={"csrf": token_locked, "password": "testpass"})
    assert r.status_code == 303

    token = set_session(client, unlocked=True)
    # build some keys
    monkeypatch.setattr(sbr.services, "SETTINGS_CATALOG", {"language": {}, "schema_version": {}, "currency": {}})
    monkeypatch.setattr(sbr.services, "save_settings", lambda changes, pw: (len(changes), []))
    r = client.post("/settings/save", data={"csrf": token, "language": "en", "schema_version": "9", "currency": "USD", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "save_settings", lambda changes, pw: (0, ["err1"]))
    r = client.post("/settings/save", data={"csrf": token, "language": "en", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "save_settings", lambda changes, pw: (_ for _ in ()).throw(ValueError("boom")))
    r = client.post("/settings/save", data={"csrf": token, "language": "en", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200

    # backup
    token = set_session(client, unlocked=True)
    monkeypatch.setattr(sbr.services, "backup_now", lambda: "/tmp/bak.db")
    r = client.post("/settings/backup", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "backup_now", lambda: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/settings/backup", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    # demo preset requires unlock
    token_locked = set_session(client, unlocked=False)
    r = client.post("/settings/demo-preset", data={"csrf": token_locked, "preset": "analysis_small", "password": "testpass", "confirm": "x"})
    assert r.status_code == 303
    token = set_session(client, unlocked=True)
    monkeypatch.setattr(sbr.services, "apply_demo_data_preset", lambda *a, **k: {"created": 5})
    r = client.post("/settings/demo-preset", data={"csrf": token, "preset": "analysis_small", "password": "testpass", "confirm": "x"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "apply_demo_data_preset", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/settings/demo-preset", data={"csrf": token, "preset": "analysis_small", "password": "testpass", "confirm": "x"}, follow_redirects=True)
    assert r.status_code == 200

    # reset requires unlock and confirm
    token_locked = set_session(client, unlocked=False)
    r = client.post("/settings/reset", data={"csrf": token_locked, "password": "testpass", "confirm": "RESET DEFAULTS"})
    assert r.status_code == 303
    token = set_session(client, unlocked=True)
    r = client.post("/settings/reset", data={"csrf": token, "password": "testpass", "confirm": "nope"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "reset_to_defaults", lambda p: None)
    r = client.post("/settings/reset", data={"csrf": token, "password": "testpass", "confirm": "RESET DEFAULTS"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "reset_to_defaults", lambda p: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/settings/reset", data={"csrf": token, "password": "testpass", "confirm": "RESET DEFAULTS"}, follow_redirects=True)
    assert r.status_code == 200


def test_rate_plans_and_health(client, monkeypatch):
    import ui.web.routes.settings_branding as sbr
    # list with usage count exception branch
    monkeypatch.setattr(sbr.services, "get_all_rate_plans", lambda: [{"id": 1, "name": "Std", "price_modifier": 0}])
    monkeypatch.setattr(sbr.services, "count_reservations_using_plan", lambda pid: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/settings/rate-plans")
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "count_reservations_using_plan", lambda pid: 3)
    r = client.get("/settings/rate-plans")
    assert r.status_code == 200

    # mutations require unlock
    token = set_session(client, unlocked=False)
    r = client.post("/settings/rate-plans/add", data={"csrf": token, "name": "A", "modifier": "1", "password": "testpass"})
    assert r.status_code == 303
    r = client.post("/settings/rate-plans/1/edit", data={"csrf": token, "name": "A", "modifier": "1", "password": "testpass"})
    assert r.status_code == 303
    r = client.post("/settings/rate-plans/1/delete", data={"csrf": token, "password": "testpass"})
    assert r.status_code == 303

    token = set_session(client, unlocked=True)
    monkeypatch.setattr(sbr.services, "add_rate_plan", lambda *a, **k: 1)
    r = client.post("/settings/rate-plans/add", data={"csrf": token, "name": "A", "modifier": "1", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "add_rate_plan", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/settings/rate-plans/add", data={"csrf": token, "name": "A", "modifier": "1", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(sbr.services, "update_rate_plan", lambda *a, **k: None)
    r = client.post("/settings/rate-plans/1/edit", data={"csrf": token, "name": "A", "modifier": "1", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "update_rate_plan", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/settings/rate-plans/1/edit", data={"csrf": token, "name": "A", "modifier": "1", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(sbr.services, "delete_rate_plan", lambda *a, **k: None)
    r = client.post("/settings/rate-plans/1/delete", data={"csrf": token, "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sbr.services, "delete_rate_plan", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/settings/rate-plans/1/delete", data={"csrf": token, "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200

    # health requires unlock
    set_session(client, unlocked=False)
    r = client.get("/settings/health")
    assert r.status_code == 303
    token = set_session(client, unlocked=True)
    monkeypatch.setattr(sbr.services, "verify_ledger_integrity", lambda: {"ok": True, "checked_at": "now", "issues": [], "warnings": [], "stats": {}, "note": "ok"})
    r = client.get("/settings/health")
    assert r.status_code == 200
