from __future__ import annotations

import json
import io
from datetime import datetime, timedelta

from app_core import db
from tests.web.conftest import set_session


def _insert_inventory_and_batch():
    conn = db.connect()
    row = conn.execute("SELECT id FROM inventory ORDER BY id LIMIT 1").fetchone()
    if row:
        try:
            inv_id = row["id"]
        except Exception:
            inv_id = row[0]
    else:
        try:
            from app_core import services
            inv_id = services.add_inventory_item(
                "RItem", "General", "ea", 0, 1.0, 2.0, 0, 0, "testpass",
                sku="RSKU", barcode="RBAR", track_batches=1,
            )
        except Exception:
            try:
                cur = conn.execute(
                    "INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, low_stock_threshold, overstock_threshold, active, sku, barcode, barcode_type, track_batches, shelf_life_days, default_supplier) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("RItem", "General", 0, "ea", 1.0, 2.0, 0, 0, 1, "RSKU", "RBAR", "code128", 1, 30, ""),
                )
                conn.commit()
                inv_id = cur.lastrowid
            except Exception:
                inv_id = None
    if not inv_id:
        return None, None
    try:
        cur = conn.execute(
            "INSERT INTO inventory_batches (item_id, lot_code, received_at, expiry_date, quantity_received, quantity_remaining, supplier, unit_cost, reference, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(inv_id), "RLOT", "2099-01-01 00:00:00", "2099-12-31", 1, 1, "SupR", 1.0, "", ""),
        )
        conn.commit()
        return int(inv_id), int(cur.lastrowid)
    except Exception:
        return int(inv_id), None


def test_reservations_enrich_and_list_quote_branches(client, monkeypatch):
    import ui.web.routes.reservations as rr

    # _enrich_pos_rows batch enrichment path (34-38,43-49)
    _inv, bid = _insert_inventory_and_batch()
    if bid:
        out = rr._enrich_pos_rows([{"id": 1, "batch_id": bid}])
        assert out and out[0]["batch_lot_code"] == "RLOT"
        assert out[0]["batch_supplier"] == "SupR"

    # list page quote_json snapshot branch (84-85)
    snap = {"nights": 2, "total": 123.0}
    r = client.get("/reservations?tab=create&quote=1&quote_json=" + json.dumps(snap))
    assert r.status_code == 200


def test_reservation_quote_modify_and_success_flash_routes(client, monkeypatch):
    import ui.web.routes.reservations as rr

    token = set_session(client, unlocked=True)

    # quote-modify exception branches for get_reservation and rate_plan access (420-421,426-427)
    monkeypatch.setattr(rr.services, "quote_reservation_pricing", lambda *a, **k: {"pricing": {"quoted_nightly_rate": 100}})
    monkeypatch.setattr(rr.services, "get_reservation", lambda rid: (_ for _ in ()).throw(RuntimeError("nope")))
    r = client.post("/reservations/1/quote-modify", data={
        "csrf": token, "room_id": "1", "check_in": "2099-01-01", "check_out": "2099-01-02", "rate": "100",
        "source": "Direct", "source_detail": "", "market_segment": "", "booking_notes": ""
    }, follow_redirects=False)
    assert r.status_code in (302, 303)

    class BadRes:
        def __getitem__(self, k):
            raise RuntimeError("bad-key")
    monkeypatch.setattr(rr.services, "get_reservation", lambda rid: BadRes())
    r = client.post("/reservations/1/quote-modify", data={
        "csrf": token, "room_id": "1", "check_in": "2099-01-01", "check_out": "2099-01-02",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)

    # success flash branches (541,599,612,623) - assert handler success (redirect) without rendering detail page
    monkeypatch.setattr(rr.services, "book_activity", lambda *a, **k: {"pos_id": 77})
    r = client.post("/reservations/1/book-activity", data={"csrf": token, "activity_id": "2", "qty": "1"}, follow_redirects=False)
    assert r.status_code in (302, 303)

    monkeypatch.setattr(rr.services, "void_reservation_discount", lambda *a, **k: None)
    r = client.post("/reservations/1/discounts/3/void", data={"csrf": token}, follow_redirects=False)
    assert r.status_code in (302, 303)

    monkeypatch.setattr(rr.services, "apply_package_to_reservation", lambda *a, **k: None)
    r = client.post("/reservations/1/packages/apply", data={"csrf": token, "package_id": "4", "notes": ""}, follow_redirects=False)
    assert r.status_code in (302, 303)

    monkeypatch.setattr(rr.services, "void_reservation_package", lambda *a, **k: None)
    r = client.post("/reservations/1/packages/5/void", data={"csrf": token}, follow_redirects=False)
    assert r.status_code in (302, 303)


def test_support_report_prefill_exception_branch(client, monkeypatch):
    import ui.web.routes.support as sr
    try:
        import app_core.updater as updater
        monkeypatch.setattr(updater, "get_cached_update_notice", lambda *a, **k: None)
    except Exception:
        pass

    # force exception in prefill try block (43-44) without breaking render/session middleware
    class BadSess(dict):
        def get(self, key, default=None):
            if key == "last_error_report":
                raise RuntimeError("sess-boom")
            return super().get(key, default)

    monkeypatch.setattr(sr.deps, "get_session", lambda request: BadSess())
    monkeypatch.setattr(sr, "render", lambda request, name, ctx=None, status=200: __import__("fastapi.responses", fromlist=["HTMLResponse"]).HTMLResponse("ok", status_code=status))
    r = client.get("/support/report?from_error=1&ref=X")
    assert r.status_code == 200


def test_settings_branding_targeted_branches(client, monkeypatch):
    import ui.web.routes.settings_branding as sb

    # setup wizard GET when not configured (covers 132)
    monkeypatch.setattr(sb.services, "is_branding_configured", lambda: False)
    monkeypatch.setattr(sb.services, "get_app_display_name", lambda: "Name")
    monkeypatch.setattr(sb.services, "get_app_tagline", lambda: "Tag")
    monkeypatch.setattr(sb.services, "get_loading_animation", lambda: "reveal")
    monkeypatch.setattr(sb.services, "get_gift_attribution", lambda: "")
    r = client.get("/setup")
    assert r.status_code == 200

    token = set_session(client, unlocked=True)

    # rate rule add/edit include min_nights and field lines (282,303,304)
    calls = []
    monkeypatch.setattr(sb.services, "add_rate_rule", lambda **k: calls.append(("add", k)))
    monkeypatch.setattr(sb.services, "update_rate_rule", lambda *a, **k: calls.append(("edit", a, k)))
    r = client.post("/settings/rate-rules/add", data={
        "csrf": token, "name": "Rule", "adjustment_type": "amount", "adjustment_value": "5",
        "rate_plan_id": "", "room_type": "Std", "source": "Direct", "market_segment": "Retail",
        "start_date": "", "end_date": "", "min_nights": "2", "max_nights": "", "priority": "100", "active": "1",
    }, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/settings/rate-rules/9/edit", data={
        "csrf": token, "name": "Rule2", "adjustment_type": "percent", "adjustment_value": "10",
        "rate_plan_id": "", "room_type": "Deluxe", "source": "OTA", "market_segment": "Corp",
        "start_date": "", "end_date": "", "min_nights": "1", "max_nights": "3", "priority": "50", "active": "1",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert calls

    # settings users login/logout routes (320-321 etc.) basic flows
    monkeypatch.setattr(sb.services, "verify_user_password", lambda u, p: {"id": 7, "username": u, "display_name": "U", "role": "staff"})
    if hasattr(sb.services, "_audit"):
        monkeypatch.setattr(sb.services, "_audit", lambda *a, **k: None)
    r = client.post("/settings/users/login", data={"csrf": token, "username": "u1", "user_password": "pw"}, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/settings/users/login", data={"csrf": token, "username": "u1", "user_password": "bad"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(sb.services, "verify_user_password", lambda u, p: None)
    r = client.post("/settings/users/login", data={"csrf": token, "username": "u1", "user_password": "bad"}, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/settings/users/logout", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    # seed export success redirect (420)
    monkeypatch.setattr(sb.services, "export_seed_config", lambda: "/tmp/seed-config.json")
    r = client.post("/settings/seed/export", data={"csrf": token}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/files/export/seed-config.json" in (r.headers.get("location") or "")

    # seed import validation/error paths (432,433 and beyond)
    r = client.post("/settings/seed/import", data={"csrf": token}, follow_redirects=True)  # missing upload
    assert r.status_code == 200

    # approvals request/decide branches (759-760,777,781-782)
    monkeypatch.setattr(sb.services, "create_approval_request", lambda *a, **k: 123)
    monkeypatch.setattr(sb.services, "decide_approval", lambda *a, **k: None)
    r = client.post("/settings/approvals/request", data={
        "csrf": token, "action_type": "x", "entity_type": "y", "entity_id": "1",
        "payload_json": '{"a":1}', "reason": "", "notes": ""
    }, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/settings/approvals/request", data={
        "csrf": token, "action_type": "x", "payload_json": "{bad", "entity_type": "", "entity_id": ""
    }, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/settings/approvals/123/decide", data={
        "csrf": token, "decision": "approved", "notes": "", "status_filter": "pending"
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "status=pending" in (r.headers.get("location") or "")
