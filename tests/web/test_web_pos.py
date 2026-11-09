from __future__ import annotations

from app_core import services
from app_core.db import connect
from tests.web.conftest import get_csrf, set_session


def test_pos_get_and_add(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr
    monkeypatch.setattr(pr.services, "search_inventory", lambda q: [{"id": 1, "item_name": "Cola", "selling_price": 5, "stock_qty": 10}])
    monkeypatch.setattr(pr.services, "list_walkin_pos", lambda limit=30: [])
    monkeypatch.setattr(pr.services, "list_walkin_payments", lambda limit=20: [])
    r = client.get("/pos?q=co")
    assert r.status_code == 200 and "POS" in r.text

    # search_inventory exception -> fallback DB query
    monkeypatch.setattr(pr.services, "search_inventory", lambda q: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/pos")
    assert r.status_code == 200

    page = client.get("/pos"); csrf = get_csrf(page.text)
    monkeypatch.setattr(pr.services, "add_pos_item", lambda *a, **k: 1)
    r = client.post("/pos/add", data={"csrf": csrf, "reservation_id": "1", "desc": "X", "qty": "1", "price": "2", "inv_id": "3"}, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/pos/add", data={"csrf": csrf, "reservation_id": "", "desc": "X", "qty": "1", "price": "2", "inv_id": ""}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "add_pos_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    r = client.post("/pos/add", data={"csrf": csrf, "desc": "X"}, follow_redirects=True)
    assert r.status_code == 200


def test_pos_cart_and_walkin(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr
    token = set_session(client, unlocked=False)

    # cart add success
    r = client.post("/pos/cart/add", data={"csrf": token, "desc": "Cola", "qty": "2", "price": "5", "inv_id": "1"}, follow_redirects=True)
    assert r.status_code == 200
    # cart add validation errors
    token = set_session(client, unlocked=False, extra={"walkin_cart": []})
    r = client.post("/pos/cart/add", data={"csrf": token, "desc": "X", "qty": "0", "price": "1"}, follow_redirects=True)
    assert r.status_code == 200
    token = set_session(client, unlocked=False, extra={"walkin_cart": []})
    r = client.post("/pos/cart/add", data={"csrf": token, "desc": "X", "qty": "1", "price": "-1"}, follow_redirects=True)
    assert r.status_code == 200
    orig_get_cart = pr._get_cart
    monkeypatch.setattr(pr, "_get_cart", lambda req: (_ for _ in ()).throw(RuntimeError("boom")))
    token = set_session(client, unlocked=False)
    r = client.post("/pos/cart/add", data={"csrf": token, "desc": "X", "qty": "1", "price": "1"})
    assert r.status_code == 303
    monkeypatch.setattr(pr, "_get_cart", orig_get_cart)

    # cart remove found, not found, error
    token = set_session(client, unlocked=False, extra={"walkin_cart": [{"desc": "A", "qty": 1, "price": 1, "inv_id": None}]})
    r = client.post("/pos/cart/remove", data={"csrf": token, "idx": "0"}, follow_redirects=True)
    assert r.status_code == 200
    token = set_session(client, unlocked=False, extra={"walkin_cart": []})
    r = client.post("/pos/cart/remove", data={"csrf": token, "idx": "5"}, follow_redirects=True)
    assert r.status_code == 200
    token = set_session(client, unlocked=False)
    r = client.post("/pos/cart/remove", data={"csrf": token, "idx": "bad"}, follow_redirects=True)
    assert r.status_code == 200

    # clear
    token = set_session(client, unlocked=False, extra={"walkin_cart": [{"desc": "A", "qty": 1, "price": 1}]})
    r = client.post("/pos/cart/clear", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    # walkin empty cart
    token = set_session(client, unlocked=False, extra={"walkin_cart": []})
    r = client.post("/pos/walkin", data={"csrf": token, "method": "Cash"}, follow_redirects=True)
    assert r.status_code == 200

    # legacy single fallback
    monkeypatch.setattr(pr.services, "complete_walkin_sale", lambda cart, method, notes, ptype: {"pos_ids": [1], "total_gross": 10.0, "receipt_name": ""})
    token = set_session(client, unlocked=False, extra={"walkin_cart": []})
    r = client.post("/pos/walkin", data={"csrf": token, "legacy_single": "1", "desc": "W", "qty": "1", "price": "10", "inv_id": "", "method": "Cash", "notes": ""}, follow_redirects=True)
    assert r.status_code == 200

    # success with receipt
    monkeypatch.setattr(pr.services, "complete_walkin_sale", lambda *a, **k: {"pos_ids": [1,2], "total_gross": 20.0, "receipt_name": "walkin.txt"})
    token = set_session(client, unlocked=False, extra={"walkin_cart": [{"desc": "A", "qty": 1, "price": 20, "inv_id": None}]})
    r = client.post("/pos/walkin", data={"csrf": token, "method": "Cash"})
    assert r.status_code == 303 and r.headers["location"] == "/files/invoice/walkin.txt"

    # error
    monkeypatch.setattr(pr.services, "complete_walkin_sale", lambda *a, **k: (_ for _ in ()).throw(ValueError("fail")))
    token = set_session(client, unlocked=False, extra={"walkin_cart": [{"desc": "A", "qty": 1, "price": 20, "inv_id": None}]})
    r = client.post("/pos/walkin", data={"csrf": token, "method": "Cash"}, follow_redirects=True)
    assert r.status_code == 200


def test_pos_void_restock_inventory(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr
    token = set_session(client, unlocked=True)
    monkeypatch.setattr(pr.services, "void_pos_item", lambda *a, **k: None)
    r = client.post("/pos/void/5", data={"csrf": token, "password": "testpass", "next": "/reservations/1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/reservations/1"
    monkeypatch.setattr(pr.services, "void_pos_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/pos/void/5", data={"csrf": token, "password": "x"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(pr.services, "restock_inventory", lambda *a, **k: None)
    r = client.post("/inventory/restock", data={"csrf": token, "item_id": "1", "qty": "2", "unit_cost": "3", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "restock_inventory", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/inventory/restock", data={"csrf": token, "item_id": "1", "qty": "2"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(pr.services, "add_inventory_item", lambda *a, **k: 1)
    r = client.post("/inventory/add", data={"csrf": token, "name": "N", "category": "C", "unit": "u", "stock": "1", "unit_cost": "1", "selling_price": "2", "low": "0", "over": "0", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "add_inventory_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/inventory/add", data={"csrf": token, "name": "N"}, follow_redirects=True)
    assert r.status_code == 200
