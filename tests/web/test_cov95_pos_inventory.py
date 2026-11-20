from __future__ import annotations

from datetime import datetime, timedelta

from app_core import db, services
from tests.web.conftest import get_csrf, set_session


def _insert_inventory_item(name="Cov Item", sku="COV-SKU", barcode="COV-BAR"):
    # use real service signature when possible (requires super password "testpass" in web_env)
    try:
        return services.add_inventory_item(
            name, "General", "ea", 0, 1.0, 2.0, 0, 0, "testpass",
            sku=sku, barcode=barcode, barcode_type="code128", track_batches=1, shelf_life_days=30, default_supplier="",
        )
    except Exception:
        pass
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, low_stock_threshold, overstock_threshold, active, sku, barcode, barcode_type, track_batches, shelf_life_days, default_supplier) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, "General", 0, "ea", 1.0, 2.0, 0, 0, 1, sku, barcode, "code128", 1, 30, ""),
    )
    conn.commit()
    return int(cur.lastrowid)


def _insert_batch(item_id, lot="LOT-COV", received_at=None, expiry_date="2099-12-31", supplier="Sup"):
    if received_at is None:
        received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO inventory_batches (item_id, lot_code, received_at, expiry_date, quantity_received, quantity_remaining, supplier, unit_cost, reference, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (int(item_id), lot, received_at, expiry_date, 5, 5, supplier, 1.0, "ref", ""),
    )
    conn.commit()
    return cur.lastrowid


def test_pos_inventory_enrich_and_trace_missing_branches():
    import ui.web.routes.pos_inventory as pr
    import uuid

    # _enrich_pos_rows: batch_ids path + enrichment copy (29-51)
    uniq = uuid.uuid4().hex[:8]
    item_id = _insert_inventory_item(name=f"Cov Enrich Item {uniq}", sku=f"COV-ENR-SKU-{uniq}", barcode=f"COV-ENR-BAR-{uniq}")
    lot = f"LOT-ENR-{uniq}"
    bid = _insert_batch(item_id, lot=lot, expiry_date="2099-01-01")
    rows = [{"id": 1, "desc": "x", "batch_id": bid}]
    out = pr._enrich_pos_rows(rows)
    assert out and out[0]["batch_id"] == bid
    assert out[0]["batch_lot_code"] == lot
    assert out[0]["batch_expiry_date"] == "2099-01-01"
    assert out[0]["batch_expiry_state"] is not None

    # _batch_trace positive return path (82-84) by id and lot
    t_by_id = pr._batch_trace(batch_id=bid)
    assert t_by_id.get("batch_id") == bid and t_by_id.get("lot_code") == lot
    t_by_lot = pr._batch_trace(lot_code=lot)
    assert t_by_lot.get("lot_code") == lot

    # _cart_view copies pre-existing trace fields (118)
    cart = [{
        "desc": "Item", "qty": 1, "price": 1.0, "inv_id": item_id,
        "batch_id": bid, "lot_code": "LOT-ENR",
        "batch_received_at": "2099-01-01 10:00:00",
        "batch_expiry_date": "2099-01-01",
        "batch_expiry_state": "ok",
        "batch_days_to_expiry": 10,
        "batch_supplier": "Sup",
    }]
    lines, total = pr._cart_view(cart)
    assert lines[0]["batch_received_at"] == "2099-01-01 10:00:00"
    assert lines[0]["batch_supplier"] == "Sup"
    assert total >= 0


def test_pos_add_route_success_and_error(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr

    token = set_session(client, unlocked=True)
    calls = []

    def fake_add_pos_item(rid, desc, qty, price, inv_id, batch_id=None, lot_code=None):
        calls.append((rid, desc, qty, price, inv_id, batch_id, lot_code))

    monkeypatch.setattr(pr.services, "add_pos_item", fake_add_pos_item)
    r = client.post("/pos/add", data={
        "csrf": token,
        "reservation_id": "5",
        "inv_id": "7",
        "batch_id": "9",
        "lot_code": " L9 ",
        "desc": "Coffee",
        "qty": "2",
        "price": "3.5",
        "next": "/pos?tab=history",
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/pos?tab=history" in (r.headers.get("location") or "")
    assert calls and calls[-1] == (5, "Coffee", 2, 3.5, 7, 9, "L9")

    # invalid/empty optionals + error flash branch
    monkeypatch.setattr(pr.services, "add_pos_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    r = client.post("/pos/add", data={
        "csrf": token,
        "reservation_id": "",
        "inv_id": "",
        "batch_id": "",
        "lot_code": "",
        "desc": "Item",
        "qty": "1",
        "price": "1.0",
        "next": "http://evil.invalid/x",  # should be sanitized by safe_local_path
    }, follow_redirects=False)
    assert r.status_code in (302, 303)
    loc = r.headers.get("location") or ""
    assert "/pos?tab=sell" in loc or loc.startswith("/")

    # legacy-safe: ensure route requires unlock/csrf handled by dependency/middleware; unlocked session already set
    # success path with "None" style batch id parsing
    monkeypatch.setattr(pr.services, "add_pos_item", fake_add_pos_item)
    r = client.post("/pos/add", data={"csrf": token, "reservation_id": "1", "desc": "X", "qty": "1", "price": "0", "batch_id": "None"}, follow_redirects=True)
    assert r.status_code == 200
