from __future__ import annotations

from app_core import db, services
from tests.web.conftest import get_csrf, set_session


def _pick_room(index=0):
    rooms = services.get_all_rooms()
    r = rooms[index if index < len(rooms) else 0]
    rid = r["id"] if isinstance(r, dict) or hasattr(r, "__getitem__") else getattr(r, "id")
    return int(rid)


def test_pos_inventory_helpers_and_page_branches(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr

    # _warn_days fallback
    monkeypatch.setattr(pr.services, "get_setting", lambda k, d=None: (_ for _ in ()).throw(RuntimeError("x")))
    assert pr._warn_days() == 7
    monkeypatch.setattr(pr.services, "get_setting", lambda k, d=None: "10" if k == "expiry_warn_days" else d)

    # _batch_trace branches (not found / by id / by lot / exception)
    assert pr._batch_trace() == {}
    assert pr._batch_trace(lot_code="NOPE").get("lot_code") == "NOPE"
    # insert a batch row if possible for positive lookup; otherwise just exercise paths
    try:
        inv_id = db.connect().execute("SELECT id FROM inventory ORDER BY id LIMIT 1").fetchone()
        inv_id = inv_id["id"] if inv_id else None
    except Exception:
        inv_id = None
    if inv_id:
        try:
            services.receive_inventory_batch(int(inv_id), 1, 1.0, lot_code="LOT-TST", expiry_date="2099-12-31")
            row = db.connect().execute("SELECT id, lot_code FROM inventory_batches ORDER BY id DESC LIMIT 1").fetchone()
            if row:
                t1 = pr._batch_trace(batch_id=row["id"])
                t2 = pr._batch_trace(lot_code=row["lot_code"])
                assert t1.get("batch_id") == row["id"]
                assert t2.get("lot_code") == row["lot_code"]
        except Exception:
            pass

    # simulate DB error in _batch_trace
    real_execute = db.connect().execute

    def bad_execute(sql, *a, **k):
        if "FROM inventory_batches" in str(sql):
            raise RuntimeError("db boom")
        return real_execute(sql, *a, **k)

    # monkeypatching sqlite connection method globally is risky; instead patch connect to object with failing execute in context
    # easier: monkeypatch pr.connect
    class BadConn:
        def execute(self, sql, params=()):
            if "FROM inventory_batches" in str(sql):
                raise RuntimeError("db boom")
            return real_execute(sql, params)

    monkeypatch.setattr(pr, "connect", lambda: BadConn())
    assert pr._batch_trace(batch_id=123).get("lot_code", "") == ""
    monkeypatch.setattr(pr, "connect", db.connect)

    # _cart_view enrichment path
    cart = [{"desc": "Item", "qty": 2, "price": 10.0, "inv_id": None, "batch_id": None, "lot_code": "LOT-TST"}]
    lines, total = pr._cart_view(cart)
    assert lines and total >= 0

    # POS page branches: scan success/error, batch_item tab switch, invalid tab
    monkeypatch.setattr(
        pr.services,
        "resolve_barcode",
        lambda code: {
            "type": "item",
            "code": code,
            "item": {"id": 1, "item_name": "Test Item", "sku": "SKU1", "barcode": code, "selling_price": 5.0},
        },
    )
    r = client.get("/pos?scan=ABC123&tab=unknown")
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "resolve_barcode", lambda code: (_ for _ in ()).throw(ValueError("bad scan")))
    r = client.get("/pos?scan=BAD")
    assert r.status_code == 200
    r = client.get("/pos?batch_item=1&tab=sell")
    assert r.status_code == 200
    # force loader exceptions
    monkeypatch.setattr(pr.services, "list_activities", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(pr.services, "list_meals", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(pr.services, "get_expiring_batches", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(pr.services, "list_inventory_batches", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/pos?q=test&batch_item=bad")
    assert r.status_code == 200


def test_pos_inventory_recipe_meal_activity_batch_label_and_scan_routes(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr

    token = set_session(client, unlocked=True)

    # recipe set/clear
    monkeypatch.setattr(pr.services, "set_recipe_components", lambda *a, **k: None)
    r = client.post(
        "/recipes/set", data={"csrf": token, "menu_item_id": "1", "components": "2,1.5\n#c\n3:2"}, follow_redirects=True
    )
    assert r.status_code == 200
    monkeypatch.setattr(
        pr.services, "set_recipe_components", lambda *a, **k: (_ for _ in ()).throw(ValueError("set fail"))
    )
    r = client.post("/recipes/set", data={"csrf": token, "item_id": "1", "components": "x"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "clear_recipe_components", lambda *a, **k: None)
    r = client.post("/recipes/clear", data={"csrf": token, "menu_item_id": "1"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(
        pr.services, "clear_recipe_components", lambda *a, **k: (_ for _ in ()).throw(ValueError("clear fail"))
    )
    r = client.post("/recipes/clear", data={"csrf": token, "item_id": "1"}, follow_redirects=True)
    assert r.status_code == 200

    # meal save create/update and delete
    monkeypatch.setattr(pr.services, "create_meal", lambda *a, **k: {"linked_inventory_id": 123})
    r = client.post(
        "/meals/save",
        data={"csrf": token, "name": "Meal A", "selling_price": "50", "ingredients": "1,2,note\n2:1"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "update_meal", lambda *a, **k: None)
    r = client.post(
        "/meals/save",
        data={"csrf": token, "meal_id": "5", "name": "Meal A2", "selling_price": "55", "ingredients": ""},
        follow_redirects=True,
    )
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "create_meal", lambda *a, **k: (_ for _ in ()).throw(ValueError("meal fail")))
    r = client.post("/meals/save", data={"csrf": token, "name": "Bad", "selling_price": "1"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "delete_meal", lambda *a, **k: None)
    r = client.post("/meals/delete", data={"csrf": token, "meal_id": "5"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "delete_meal", lambda *a, **k: (_ for _ in ()).throw(ValueError("del fail")))
    r = client.post("/meals/delete", data={"csrf": token, "meal_id": "5"}, follow_redirects=True)
    assert r.status_code == 200

    # activities create/toggle
    monkeypatch.setattr(pr.services, "create_activity", lambda *a, **k: None)
    r = client.post(
        "/activities/create", data={"csrf": token, "name": "Trip", "unit_price": "10"}, follow_redirects=True
    )
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "create_activity", lambda *a, **k: (_ for _ in ()).throw(ValueError("act fail")))
    r = client.post(
        "/activities/create", data={"csrf": token, "name": "Trip", "unit_price": "10"}, follow_redirects=True
    )
    assert r.status_code == 200

    monkeypatch.setattr(pr.services, "get_activity", lambda aid: {"id": aid, "active": 1})
    toggled = []
    monkeypatch.setattr(pr.services, "set_activity_active", lambda aid, v: toggled.append((aid, v)))
    r = client.post("/activities/7/toggle", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200 and toggled == [(7, 0)]
    monkeypatch.setattr(pr.services, "get_activity", lambda aid: None)
    r = client.post("/activities/7/toggle", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    # batch receive
    monkeypatch.setattr(
        pr.services,
        "receive_inventory_batch",
        lambda *a, **k: {"lot_code": "L1", "received_at": "2099-01-01", "expiry_date": "2099-12-31"},
    )
    r = client.post(
        "/inventory/batch/receive",
        data={"csrf": token, "item_id": "1", "qty": "1", "unit_cost": "1"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    monkeypatch.setattr(
        pr.services, "receive_inventory_batch", lambda *a, **k: (_ for _ in ()).throw(ValueError("recv fail"))
    )
    r = client.post(
        "/inventory/batch/receive",
        data={"csrf": token, "item_id": "1", "qty": "1", "unit_cost": "1"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    # label printing item/batch success+error
    monkeypatch.setattr(pr.services, "print_item_label", lambda item_id: (True, "ok", "/tmp/item"))
    r = client.post("/inventory/label/item/1", data={"csrf": token, "next": "/pos?tab=batches"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(
        pr.services, "print_item_label", lambda item_id: (_ for _ in ()).throw(ValueError("label fail"))
    )
    r = client.post("/inventory/label/item/1", data={"csrf": token, "next": "/pos?tab=batches"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(pr.services, "print_batch_label", lambda bid: (False, "warn", "/tmp/batch"))
    r = client.post("/inventory/label/batch/2", data={"csrf": token, "next": "/pos?tab=batches"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "print_batch_label", lambda bid: (_ for _ in ()).throw(ValueError("blabel fail")))
    r = client.post("/inventory/label/batch/2", data={"csrf": token, "next": "/pos?tab=batches"}, follow_redirects=True)
    assert r.status_code == 200

    # scan redirect
    r = client.get("/inventory/scan?code=XYZ", follow_redirects=False)
    assert r.status_code in (302, 303) and "scan=XYZ" in r.headers.get("location", "")
