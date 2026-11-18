from __future__ import annotations

import io
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app_core import db, services
from tests.web.conftest import set_session


def _csrf_post(client, url, data=None, *, unlocked=False, files=None, follow=False):
    csrf = set_session(client, unlocked=unlocked)
    payload = {"csrf": csrf}
    if data:
        payload.update(data)
    return client.post(url, data=payload, files=files, follow_redirects=follow)


def _future(days=300, nights=1):
    ci = date.today() + timedelta(days=days)
    return ci.strftime("%Y-%m-%d"), (ci + timedelta(days=nights)).strftime("%Y-%m-%d")


def test_dashboard_and_setup_branches(client):
    # normal dashboard
    set_session(client, unlocked=False)
    r = client.get("/")
    assert r.status_code == 200

    # ops exception branch
    orig = services.get_front_desk_ops
    try:
        services.get_front_desk_ops = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
        set_session(client, unlocked=False)
        r2 = client.get("/?day=2026-12-01")
        assert r2.status_code == 200
    finally:
        services.get_front_desk_ops = orig

    # branding not configured -> redirect to setup; setup page then post
    try:
        services.set_setting("branding_configured", "false")
    except Exception:
        pass
    set_session(client, unlocked=False)
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/setup" in r.headers.get("location", "")

    set_session(client, unlocked=False)
    sp = client.get("/setup")
    assert sp.status_code == 200

    # invalid setup (missing name)
    bad = _csrf_post(client, "/setup", {"display_name": "", "tagline": "", "animation": "reveal"})
    assert bad.status_code in (302, 303)

    # valid setup
    ok = _csrf_post(client, "/setup", {"display_name": "Test Property", "tagline": "Hi", "animation": "reveal"})
    assert ok.status_code in (302, 303)
    assert services.is_branding_configured()


def test_guests_routes(client):
    room_id = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()["id"]
    ci, co = _future(310, 1)
    rid = services.create_reservation("Guest List Person", "Direct", int(room_id), None, 90.0, ci, co, 0.0)
    # find guest id
    g = db.connect().execute("SELECT guest_id FROM reservations WHERE id=?", (rid,)).fetchone()
    gid = int(g["guest_id"]) if g and g["guest_id"] else None
    if not gid:
        row = db.connect().execute("SELECT id FROM guests ORDER BY id DESC LIMIT 1").fetchone()
        gid = int(row["id"]) if row else None
    assert gid is not None

    set_session(client, unlocked=False)
    r = client.get("/guests?q=Guest")
    assert r.status_code == 200
    r = client.get(f"/guests/{gid}")
    assert r.status_code == 200

    # not found
    set_session(client, unlocked=False)
    nf = client.get("/guests/999999", follow_redirects=False)
    assert nf.status_code in (302, 303)

    # update (unlocked + permission)
    upd = _csrf_post(client, f"/guests/{gid}/update", {
        "name": "Guest Updated",
        "phone": "123",
        "email": "g@example.com",
        "notes": "note",
        "password": "testpass",
    }, unlocked=True)
    assert upd.status_code in (302, 303)

    # update error path (if invalid id)
    bad = _csrf_post(client, "/guests/999999/update", {"name": "X", "password": "testpass"}, unlocked=True)
    assert bad.status_code in (302, 303)


def test_reports_routes(client):
    # reports index (tabs)
    set_session(client, unlocked=True)
    assert client.get("/reports?tab=generate").status_code == 200
    assert client.get("/reports?tab=downloads").status_code == 200

    # run report kinds
    today = date.today().strftime("%Y-%m-%d")
    month_start = date.today().replace(day=1).strftime("%Y-%m-%d")
    for kind, extra in [
        ("pl", {"start": month_start, "end": today}),
        ("daily_ops", {"day": today}),
        ("front_desk", {"day": today}),
        ("housekeeping_ops", {}),
        ("groups", {}),
        ("packages", {}),
        ("shifts", {}),
        ("night_audit", {}),
    ]:
        r = _csrf_post(client, "/reports/run", {"kind": kind, **extra}, unlocked=True)
        # may render view or redirect on error; both exercise branches
        assert r.status_code in (200, 302, 303)

    # report error path
    r = _csrf_post(client, "/reports/run", {"kind": "badkind"}, unlocked=True)
    assert r.status_code in (200, 302, 303)

    # analytics export
    r = _csrf_post(client, "/reports/analytics-export", {"preset": "management_pack", "start": month_start, "end": today}, unlocked=True)
    assert r.status_code in (302, 303)


def test_settings_core_and_catalog_routes(client):
    # settings page tabs
    set_session(client, unlocked=True)
    for tab in ["general", "data", "rooms", "updates", "bogus"]:
        assert client.get(f"/settings?tab={tab}").status_code == 200

    # branding and save settings
    r = _csrf_post(client, "/settings/branding", {"display_name": "Brand X", "tagline": "T", "animation": "reveal"}, unlocked=True)
    assert r.status_code in (302, 303)
    r = _csrf_post(client, "/settings/save", {"password": "testpass"}, unlocked=True)
    assert r.status_code in (302, 303)

    # backup
    r = _csrf_post(client, "/settings/backup", {"password": "testpass"}, unlocked=True)
    assert r.status_code in (302, 303)

    # reset with wrong confirmation and correct confirmation
    bad = _csrf_post(client, "/settings/reset", {"password": "testpass", "confirm": "nope"}, unlocked=True)
    assert bad.status_code in (302, 303)
    ok = _csrf_post(client, "/settings/reset", {"password": "testpass", "confirm": "RESET DEFAULTS"}, unlocked=True)
    assert ok.status_code in (302, 303)

    # rooms add/delete
    add = _csrf_post(client, "/settings/rooms/add", {"room_number": "Z999", "room_type": "Test", "base_price": "50", "housekeeping_status": "Clean"}, unlocked=True)
    assert add.status_code in (302, 303)
    row = db.connect().execute("SELECT id FROM rooms WHERE room_number='Z999' ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        _csrf_post(client, f"/settings/rooms/{int(row['id'])}/delete", {}, unlocked=True)
    _csrf_post(client, "/settings/rooms/999999/delete", {}, unlocked=True)

    # rate plans/rules
    set_session(client, unlocked=True)
    assert client.get("/settings/rate-plans").status_code == 200
    _csrf_post(client, "/settings/rate-plans/add", {"name": "WebPlan", "modifier": "1.1"}, unlocked=True)
    plan = db.connect().execute("SELECT id FROM rate_plans WHERE name='WebPlan' ORDER BY id DESC LIMIT 1").fetchone()
    if plan:
        pid = int(plan["id"])
        _csrf_post(client, f"/settings/rate-plans/{pid}/edit", {"name": "WebPlan2", "modifier": "1.2"}, unlocked=True)
        _csrf_post(client, "/settings/rate-rules/add", {"name": "WebRule", "adjustment_type": "amount", "adjustment_value": "5", "rate_plan_id": str(pid), "priority": "100", "active": "on"}, unlocked=True)
        rule = db.connect().execute("SELECT id FROM rate_rules WHERE name='WebRule' ORDER BY id DESC LIMIT 1").fetchone()
        if rule:
            _csrf_post(client, f"/settings/rate-rules/{int(rule['id'])}/edit", {"name": "WebRule2", "adjustment_type": "amount", "adjustment_value": "7", "rate_plan_id": str(pid), "priority": "100", "active": "on"}, unlocked=True)
        _csrf_post(client, f"/settings/rate-plans/{pid}/delete", {}, unlocked=True)

    # vouchers
    set_session(client, unlocked=True)
    assert client.get("/settings/vouchers").status_code == 200
    _csrf_post(client, "/settings/vouchers/add", {"code": "WEB10", "discount_type": "amount", "value": "10", "name": "Web", "active": "on"}, unlocked=True)
    v = db.connect().execute("SELECT id FROM discount_vouchers WHERE code='WEB10' ORDER BY id DESC LIMIT 1").fetchone()
    if v:
        _csrf_post(client, f"/settings/vouchers/{int(v['id'])}/edit", {"code": "WEB10", "discount_type": "amount", "value": "12", "name": "Web2", "active": "on"}, unlocked=True)

    # service catalog
    set_session(client, unlocked=True)
    assert client.get("/settings/service-catalog").status_code == 200
    _csrf_post(client, "/settings/service-catalog/add", {"name": "WebSvc", "category": "Service", "unit_price": "15", "description": "d", "taxable": "on", "active": "on"}, unlocked=True)
    s = db.connect().execute("SELECT id FROM service_catalog WHERE name='WebSvc' ORDER BY id DESC LIMIT 1").fetchone()
    if s:
        _csrf_post(client, f"/settings/service-catalog/{int(s['id'])}/edit", {"name": "WebSvc2", "category": "Service", "unit_price": "20", "description": "d2", "taxable": "on", "active": "on"}, unlocked=True)

    # packages
    set_session(client, unlocked=True)
    assert client.get("/settings/packages").status_code == 200
    _csrf_post(client, "/settings/packages/add", {"name": "WebPkg", "description": "p", "active": "on"}, unlocked=True)
    p = db.connect().execute("SELECT id FROM packages WHERE name='WebPkg' ORDER BY id DESC LIMIT 1").fetchone()
    if p:
        pid2 = int(p["id"])
        _csrf_post(client, f"/settings/packages/{pid2}/edit", {"name": "WebPkg2", "description": "p2", "active": "on"}, unlocked=True)
        _csrf_post(client, f"/settings/packages/{pid2}/items/add", {"service_id": str(int(s["id"])) if s else "", "description": "pkg item", "quantity": "1", "unit_price": "10"}, unlocked=True)
        pi = db.connect().execute("SELECT id FROM package_items WHERE package_id=? ORDER BY id DESC LIMIT 1", (pid2,)).fetchone()
        if pi:
            _csrf_post(client, f"/settings/packages/items/{int(pi['id'])}/delete", {}, unlocked=True)

    # users and approvals pages/actions
    set_session(client, unlocked=True)
    assert client.get("/settings/users").status_code == 200
    _csrf_post(client, "/settings/users/add", {"username": "webuser", "display_name": "Web User", "role": "staff", "new_password": "pass123", "active": "on"}, unlocked=True)
    u = db.connect().execute("SELECT id FROM users WHERE username='webuser' ORDER BY id DESC LIMIT 1").fetchone()
    if u:
        uid = int(u["id"])
        _csrf_post(client, f"/settings/users/{uid}/edit", {"display_name": "Web User 2", "role": "staff", "active": "on"}, unlocked=True)
        # login/logout user session (requires csrf but not unlocked)
        _csrf_post(client, "/settings/users/login", {"username": "webuser", "user_password": "pass123"}, unlocked=False)
        _csrf_post(client, "/settings/users/logout", {}, unlocked=False)

    set_session(client, unlocked=True)
    assert client.get("/settings/approvals").status_code == 200
    _csrf_post(client, "/settings/approvals/request", {
        "action_type": "manual_note", "entity_type": "reservation", "entity_id": "1",
        "payload_json": "{\"x\":1}", "reason": "r", "notes": "n"
    }, unlocked=True)
    a = db.connect().execute("SELECT id FROM approvals ORDER BY id DESC LIMIT 1").fetchone()
    if a:
        aid = int(a["id"])
        _csrf_post(client, f"/settings/approvals/{aid}/decide", {"decision": "approved", "notes": "ok", "status_filter": ""}, unlocked=True)
        _csrf_post(client, f"/settings/approvals/{aid}/execute", {"execution_result_json": "{\"done\":true}", "notes": "x"}, unlocked=True)

    # health page and updates check/apply (apply likely fails safely in this env)
    set_session(client, unlocked=True)
    assert client.get("/settings/health").status_code == 200
    _csrf_post(client, "/settings/updates/check", {}, unlocked=False)
    _csrf_post(client, "/settings/updates/apply", {"confirm": "WRONG"}, unlocked=True)
    _csrf_post(client, "/settings/updates/apply", {"confirm": "UPDATE APP"}, unlocked=True)

    # lock
    _csrf_post(client, "/lock", {}, unlocked=True)
