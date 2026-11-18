from __future__ import annotations

import io
import json
from pathlib import Path

from app_core import db, services, updater
from tests.web.conftest import set_session


def _csrf_post(client, url, data=None, *, unlocked=False, files=None, follow=False):
    csrf = set_session(client, unlocked=unlocked)
    payload = {"csrf": csrf}
    if data:
        payload.update(data)
    return client.post(url, data=payload, files=files, follow_redirects=follow)


def test_guest_detail_enriches_batches(client, monkeypatch):
    # Ensure a guest exists (seed data usually has guests)
    guest = db.connect().execute("SELECT id, canonical_name FROM guests ORDER BY id LIMIT 1").fetchone()
    if not guest:
        db.connect().execute("INSERT INTO guests (canonical_name, phone, email) VALUES (?,?,?)", ("Batch Guest", "", ""))
        db.connect().commit()
        guest = db.connect().execute("SELECT id, canonical_name FROM guests ORDER BY id DESC LIMIT 1").fetchone()
    gid = int(guest["id"])

    # Create an inventory batch row if possible (best-effort for enrichment path)
    c = db.connect()
    batch_id = None
    for stmt, params in [
        ("INSERT INTO inventory_batches (item_id, batch_code, lot_code, quantity_received, quantity_remaining, received_at, expiry_date, supplier, status) VALUES (?,?,?,?,?,?,?,?,?)",
         (1, "B1", "LOT1", 10, 5, "2026-01-01", "2099-01-01", "Supplier", "active")),
        ("INSERT INTO inventory_batches (item_id, lot_code, quantity_remaining, received_at, expiry_date, supplier) VALUES (?,?,?,?,?,?)",
         (1, "LOT1", 5, "2026-01-01", "2099-01-01", "Supplier")),
    ]:
        try:
            c.execute(stmt, params)
            c.commit()
            batch_id = int(c.execute("SELECT id FROM inventory_batches ORDER BY id DESC LIMIT 1").fetchone()["id"])
            break
        except Exception:
            pass
    if not batch_id:
        row = c.execute("SELECT id FROM inventory_batches ORDER BY id DESC LIMIT 1").fetchone()
        batch_id = int(row["id"]) if row else 1

    # Start from a real portfolio structure when available, then inject batch-linked items
    portfolio = services.get_guest_portfolio(gid) or {}
    portfolio = dict(portfolio)
    g = dict(portfolio.get("guest") or {"id": gid, "canonical_name": guest["canonical_name"] or "Guest"})
    portfolio["guest"] = g
    trips = list(portfolio.get("trips") or [])
    if not trips:
        trips = [{"pos_items": [], "pos_active": []}]
    trip0 = dict(trips[0])
    trip0["pos_items"] = list(trip0.get("pos_items") or []) + [{"batch_id": batch_id, "name": "Item"}]
    trip0["pos_active"] = list(trip0.get("pos_active") or []) + [{"batch_id": batch_id, "name": "Item"}]
    trips[0] = trip0
    portfolio["trips"] = trips
    portfolio["all_pos"] = list(portfolio.get("all_pos") or []) + [{"batch_id": batch_id, "name": "Item"}]
    portfolio.setdefault("all_payments", [])
    portfolio.setdefault("all_pos_active", [])

    monkeypatch.setattr(services, "get_guest_portfolio", lambda _gid: portfolio)
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: "7")

    set_session(client, unlocked=False)
    r = client.get(f"/guests/{gid}")
    assert r.status_code == 200


def test_unlock_lock_recovery_and_settings_branches(client, monkeypatch):
    # unlock page and invalid/success posts
    set_session(client, unlocked=False)
    assert client.get("/unlock?next=/settings").status_code == 200
    bad = _csrf_post(client, "/unlock", {"password": "wrong", "next": "/settings"})
    assert bad.status_code in (302, 303)
    ok = _csrf_post(client, "/unlock", {"password": "testpass", "next": "/settings"})
    assert ok.status_code in (302, 303)

    # lock
    r = _csrf_post(client, "/lock", {}, unlocked=True)
    assert r.status_code in (302, 303)

    # recovery pages
    set_session(client, unlocked=False)
    assert client.get("/unlock/recovery?next=/").status_code == 200
    monkeypatch.setattr(services, "use_recovery_code", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad code")))
    badrec = _csrf_post(client, "/unlock/recovery", {"code": "x", "new_password": "newpass123", "confirm_password": "newpass123", "next": "/"})
    assert badrec.status_code in (302, 303)

    def ok_use(code, new_password, confirm_password):
        services.set_super_password(new_password)
    monkeypatch.setattr(services, "use_recovery_code", ok_use)
    goodrec = _csrf_post(client, "/unlock/recovery", {"code": "ok", "new_password": "newpass123", "confirm_password": "newpass123", "next": "/"})
    assert goodrec.status_code in (302, 303)
    # restore test password for subsequent operations
    try:
        services.set_super_password("testpass")
    except Exception:
        pass

    # settings page with exception branches in demo_presets/update_local
    monkeypatch.setattr(services, "list_demo_data_presets", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(updater, "get_local_state", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    set_session(client, unlocked=True)
    assert client.get("/settings?tab=general").status_code == 200


def test_seed_export_import_demo_label_health_updates(client, monkeypatch, tmp_path):
    # seed export
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps({"rooms": []}), encoding="utf-8")
    monkeypatch.setattr(services, "export_seed_config", lambda: str(seed_file))
    r = _csrf_post(client, "/settings/seed/export", {}, unlocked=True)
    assert r.status_code in (302, 303)

    # seed export inventory csv (template flag)
    csv_file = tmp_path / "inv.csv"
    csv_file.write_text("item\n", encoding="utf-8")
    monkeypatch.setattr(services, "export_inventory_seed_csv", lambda template=False: str(csv_file))
    r = _csrf_post(client, "/settings/seed/export-inventory-csv", {"template": "1"}, unlocked=True)
    assert r.status_code in (302, 303)

    # seed import invalid/missing/valid-ish payloads (service may reject, but route branches execute)
    csrf = set_session(client, unlocked=True)
    r = client.post("/settings/seed/import", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code in (302, 303)
    r = client.post("/settings/seed/import", data={"csrf": csrf}, files={"seed_file": ("seed.txt", b"{}", "text/plain")}, follow_redirects=False)
    assert r.status_code in (302, 303)
    monkeypatch.setattr(services, "import_seed_config", lambda *a, **k: {"summary": {"ok": True}})
    r = client.post("/settings/seed/import", data={"csrf": csrf, "password": "testpass", "confirm": "IMPORT"}, files={"seed_file": ("seed.json", b"{}", "application/json")}, follow_redirects=False)
    assert r.status_code in (302, 303)
    monkeypatch.setattr(services, "import_seed_config", lambda *a, **k: {"summary": {"ok": True}})
    r = client.post("/settings/seed/import", data={"csrf": csrf, "password": "testpass", "confirm": "IMPORT"}, files={"seed_file": ("seed.csv", b"a,b\n1,2", "text/csv")}, follow_redirects=False)
    assert r.status_code in (302, 303)

    # demo preset and reset wrong confirm already covered elsewhere; exercise demo route + error
    monkeypatch.setattr(services, "apply_demo_data_preset", lambda *a, **k: {"created": {"x": 1}})
    r = _csrf_post(client, "/settings/demo-preset", {"preset": "analysis_small", "confirm": "DEMO"}, unlocked=True)
    assert r.status_code in (302, 303)
    monkeypatch.setattr(services, "apply_demo_data_preset", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    r = _csrf_post(client, "/settings/demo-preset", {"preset": "analysis_small", "confirm": "DEMO"}, unlocked=True)
    assert r.status_code in (302, 303)

    # test label
    monkeypatch.setattr(services, "test_label_print", lambda: (True, "ok", "p"))
    r = _csrf_post(client, "/settings/test-label", {}, unlocked=True)
    assert r.status_code in (302, 303)
    monkeypatch.setattr(services, "test_label_print", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
    r = _csrf_post(client, "/settings/test-label", {}, unlocked=True)
    assert r.status_code in (302, 303)

    # health
    monkeypatch.setattr(services, "verify_ledger_integrity", lambda: {"ok": True, "stats": {}, "issues": [], "checks": {}})
    set_session(client, unlocked=True)
    assert client.get("/settings/health").status_code == 200

    # updates check branches
    monkeypatch.setattr(updater, "check_for_updates", lambda: {"ok": True, "update_available": True, "commits_behind": 2})
    assert _csrf_post(client, "/settings/updates/check", {}, unlocked=False).status_code in (302, 303)
    monkeypatch.setattr(updater, "check_for_updates", lambda: {"ok": True, "update_available": False})
    assert _csrf_post(client, "/settings/updates/check", {}, unlocked=False).status_code in (302, 303)
    monkeypatch.setattr(updater, "check_for_updates", lambda: {"ok": False, "error": "offline"})
    assert _csrf_post(client, "/settings/updates/check", {}, unlocked=False).status_code in (302, 303)
    monkeypatch.setattr(updater, "check_for_updates", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _csrf_post(client, "/settings/updates/check", {}, unlocked=False).status_code in (302, 303)

    # updates apply branches
    monkeypatch.setattr(updater, "apply_update", lambda: {"ok": True})
    assert _csrf_post(client, "/settings/updates/apply", {"confirm": "UPDATE APP"}, unlocked=True).status_code in (302, 303)
    monkeypatch.setattr(updater, "apply_update", lambda: {"ok": False, "error": "nope"})
    assert _csrf_post(client, "/settings/updates/apply", {"confirm": "UPDATE APP"}, unlocked=True).status_code in (302, 303)


def test_reports_and_support_small_branches(client, monkeypatch, tmp_path):
    # reports with actual export/invoice files to exercise listing loops
    # exports/invoices dirs exist in web_env; create files
    # (client fixture provides env; locate dirs via services paths if set)
    from app_core import paths
    try:
        (paths.EXPORT_DIR / "sample.txt").write_text("x", encoding="utf-8")
        (paths.INVOICE_DIR / "inv1.txt").write_text("x", encoding="utf-8")
    except Exception:
        pass
    set_session(client, unlocked=True)
    r = client.get("/reports?tab=downloads")
    assert r.status_code == 200

    # reports run unknown kind renders generic path? unknown kind falls back to pl(start,end) via .get(..., services.report_pl_statement)
    # and error branch already covered; cover analytics export error already possible
    # invalid preset error branch
    monkeypatch.setattr(services, "generate_unified_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad preset")))
    r = _csrf_post(client, "/reports/analytics-export", {"preset": "nope", "start": "2026-01-01", "end": "2026-01-31"}, unlocked=True)
    assert r.status_code in (302, 303)

    # support error-page prefill branch
    set_session(client, unlocked=False, extra={"last_error_report": {"ref": "abc", "error_type": "Boom", "error_message": "msg", "path": "/x"}})
    r = client.get("/support/report?from_error=1&ref=abc&path=/x")
    assert r.status_code == 200
