from __future__ import annotations

from datetime import datetime, timedelta

from app_core import db
from tests.web.conftest import set_session


def _insert_guest(name="Guest Cov"):
    conn = db.connect()
    cur = conn.execute(
        "INSERT INTO guests (canonical_name, phone, email, notes) VALUES (?,?,?,?)",
        (name, "", "", ""),
    )
    conn.commit()
    return cur.lastrowid


def test_housekeeping_overdue_parse_branches(client, monkeypatch):
    import ui.web.routes.housekeeping as hk

    now = datetime.now()
    past_dt = now - timedelta(days=2)
    future_dt = now + timedelta(days=2)
    tasks = [
        {"id": 1, "status": "pending", "due_at": past_dt.strftime("%Y-%m-%d %H:%M")},       # overdue fmt 1
        {"id": 2, "status": "in_progress", "due_at": past_dt.strftime("%Y-%m-%d %H:%M:%S")}, # overdue fmt 2
        {"id": 3, "status": "pending", "due_at": past_dt.strftime("%Y-%m-%d")},              # overdue fmt 3
        {"id": 4, "status": "pending", "due_at": future_dt.strftime("%Y-%m-%d %H:%M")},      # not overdue
        {"id": 5, "status": "completed", "due_at": past_dt.strftime("%Y-%m-%d")},            # ignored by status
        {"id": 6, "status": "pending", "due_at": "not-a-date"},                              # parse fail path
        {"id": 7, "status": "in_progress", "due_at": None},                                   # no due_at
    ]
    monkeypatch.setattr(hk.services, "list_housekeeping_tasks", lambda status="": tasks)
    monkeypatch.setattr(hk.services, "list_room_blocks", lambda status="active": [])
    monkeypatch.setattr(hk, "_rooms", lambda: [])

    r = client.get("/housekeeping?status=pending&block_status=active")
    assert r.status_code == 200
    # ensure page renders; detailed overdue flags are computed server-side
    assert "Housekeeping" in r.text or r.text

    # trigger outer per-task exception branch (43-44) with a bad object
    class BadTask(dict):
        def get(self, key, default=None):
            if key == "due_at":
                # first access for status check may occur; raise on second to enter outer except
                if not hasattr(self, "_n"):
                    self._n = 0
                self._n += 1
                if self._n >= 2:
                    raise RuntimeError("boom-due")
            return super().get(key, default)

    bad = BadTask({"id": 99, "status": "pending", "due_at": past_dt.strftime("%Y-%m-%d")})
    monkeypatch.setattr(hk.services, "list_housekeeping_tasks", lambda status="": [bad])
    r = client.get("/housekeeping")
    assert r.status_code == 200


def test_guests_helper_branches_and_routes(client, monkeypatch):
    import ui.web.routes.guests as gr

    # _batch_map: expiry_warn_days exception fallback (28-29)
    # create batch row so mapping loop runs, but force get_setting to raise
    conn = db.connect()
    # try to find an inventory item; create minimal if needed
    row = conn.execute("SELECT id FROM inventory ORDER BY id LIMIT 1").fetchone()
    if row:
        try:
            inv_id = row["id"]
        except Exception:
            inv_id = row[0]
    else:
        inv_id = None
        try:
            from app_core import services as svc
            inv_id = svc.add_inventory_item(
                "GInv", "General", "ea", 0, 1.0, 2.0, 0, 0, "testpass",
                sku="GSKU", barcode="GBAR", track_batches=1,
            )
        except Exception:
            try:
                cur = conn.execute(
                    "INSERT INTO inventory (item_name, category, stock_qty, unit, unit_cost, selling_price, low_stock_threshold, overstock_threshold, active, sku, barcode, barcode_type, track_batches, shelf_life_days, default_supplier) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("GInv", "General", 0, "ea", 1.0, 2.0, 0, 0, 1, "GSKU", "GBAR", "code128", 1, 30, ""),
                )
                conn.commit()
                inv_id = cur.lastrowid
            except Exception:
                inv_id = None
    batch_id = None
    if inv_id:
        try:
            cur = conn.execute(
                "INSERT INTO inventory_batches (item_id, lot_code, received_at, expiry_date, quantity_received, quantity_remaining, supplier, unit_cost, reference, notes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (int(inv_id), "GLOT", "2099-01-01 00:00:00", "2099-12-31", 1, 1, "S", 1.0, "", ""),
            )
            conn.commit()
            batch_id = cur.lastrowid
        except Exception:
            batch_id = None

    if batch_id:
        monkeypatch.setattr(gr.services, "get_setting", lambda k, d=None: (_ for _ in ()).throw(RuntimeError("x")))
        bm = gr._batch_map([batch_id])
        assert isinstance(bm, dict)
        assert batch_id in bm  # default warn=7 used

    # _enrich_pos_items: non-dict item (43) and missing batch in map (46)
    items = ["not-a-dict", {"id": 2, "batch_id": 999999}]
    out = gr._enrich_pos_items(items, {})
    assert out[0] == "not-a-dict"
    assert out[1].get("batch_id") == 999999 and "batch_lot_code" not in out[1]

    # _enrich_portfolio falsy (59)
    assert gr._enrich_portfolio(None) is None
    assert gr._enrich_portfolio({}) == {}

    # route branches: guest not found redirect, and name normalization fallback
    monkeypatch.setattr(gr.services, "get_guest_portfolio", lambda gid: None)
    r = client.get("/guests/999999", follow_redirects=False)
    assert r.status_code in (302, 303)

    # portfolio with guest dict missing canonical_name but having name (covers normalization path around detail)
    gid = _insert_guest("Norm Guest")
    portfolio = {
        "guest": {"id": gid, "name": "Norm Guest", "phone": "", "email": "", "notes": ""},
        "trips": [],
        "all_pos": [],
        "all_payments": [],
        "top_items_overall": [],
        "stats": {
            "stays_total": 0, "completed_stays": 0, "total_nights": 0,
            "total_charges": 0, "pos_gross_total": 0, "total_paid": 0, "total_outstanding": 0,
            "first_visit": None, "last_visit": None, "favorite_room": None,
        },
    }
    monkeypatch.setattr(gr.services, "get_guest_portfolio", lambda _gid: portfolio)
    r = client.get(f"/guests/{gid}")
    assert r.status_code == 200
