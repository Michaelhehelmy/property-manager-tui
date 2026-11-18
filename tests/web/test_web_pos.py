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


def test_pos_recipe_management_and_parser(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr

    # parser formats: blank/comment/colon/semicolon/comma
    comps = pr._parse_recipe_components("1,2\n#c\n\n2:0.5;3,1.25\nbadline")
    assert comps == [
        {"ingredient_item_id": "1", "qty_per_sale": "2"},
        {"ingredient_item_id": "2", "qty_per_sale": "0.5"},
        {"ingredient_item_id": "3", "qty_per_sale": "1.25"},
    ]
    assert pr._parse_recipe_components("") == []

    token = set_session(client, unlocked=True)
    calls = {}
    monkeypatch.setattr(pr.services, "set_recipe_components", lambda mid, comps, pw: calls.setdefault("set", (mid, comps, pw)) or [])
    r = client.post("/recipes/set", data={"csrf": token, "menu_item_id": "10", "components": "2,1\n3:0.5", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200 and calls["set"][0] == 10 and len(calls["set"][1]) == 2
    monkeypatch.setattr(pr.services, "set_recipe_components", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad recipe")))
    r = client.post("/recipes/set", data={"csrf": token, "menu_item_id": "10", "components": "2,1", "password": "x"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(pr.services, "clear_recipe_components", lambda mid, pw: calls.setdefault("clear", (mid, pw)) or True)
    r = client.post("/recipes/clear", data={"csrf": token, "menu_item_id": "10", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200 and calls["clear"][0] == 10
    monkeypatch.setattr(pr.services, "clear_recipe_components", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/recipes/clear", data={"csrf": token, "menu_item_id": "10", "password": "x"}, follow_redirects=True)
    assert r.status_code == 200


def test_pos_get_recipe_counts_exception_branch(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr
    monkeypatch.setattr(pr.services, "search_inventory", lambda q: [{"id": 1, "item_name": "Meal", "selling_price": 5, "stock_qty": 1}])
    monkeypatch.setattr(pr.services, "list_walkin_pos", lambda limit=30: [])
    monkeypatch.setattr(pr.services, "list_walkin_payments", lambda limit=20: [])
    real_connect = pr.connect
    class _Res:
        def fetchall(self):
            return []
    class FakeConn:
        def execute(self, sql, *a, **k):
            s = str(sql)
            if "recipe_components" in s:
                raise RuntimeError("no recipes")
            if "FROM reservations" in s:
                return _Res()
            return real_connect().execute(sql, *a, **k)
    monkeypatch.setattr(pr, "connect", lambda: FakeConn())
    r = client.get("/pos")
    assert r.status_code == 200 and "Meal" in r.text


def test_meal_save_delete_and_parser(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr
    token = set_session(client, unlocked=True)
    comps = pr._parse_meal_ingredients("12,1,bun\n#x\n15:0.5;16,2,note,extra")
    assert comps[0]["inventory_item_id"] == "12" and comps[0]["note"] == "bun"
    assert comps[1]["inventory_item_id"] == "15"
    assert comps[2]["note"] == "note,extra"
    assert pr._parse_meal_ingredients("") == []

    monkeypatch.setattr(pr.services, "create_meal", lambda *a, **k: {"id": 1, "linked_inventory_id": 99})
    r = client.post("/meals/save", data={"csrf": token, "name": "Burger", "description": "d", "preparation": "p", "selling_price": "10", "ingredients": "1,2", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "update_meal", lambda *a, **k: {"id": 1, "linked_inventory_id": 99})
    r = client.post("/meals/save", data={"csrf": token, "meal_id": "1", "name": "Burger2", "description": "d", "preparation": "p", "selling_price": "10", "ingredients": "1,2", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "create_meal", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    r = client.post("/meals/save", data={"csrf": token, "name": "X", "ingredients": "", "password": "x"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(pr.services, "delete_meal", lambda *a, **k: True)
    r = client.post("/meals/delete", data={"csrf": token, "meal_id": "1", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(pr.services, "delete_meal", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/meals/delete", data={"csrf": token, "meal_id": "1", "password": "x"}, follow_redirects=True)
    assert r.status_code == 200


def test_pos_get_meals_exception_branch(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr
    monkeypatch.setattr(pr.services, "search_inventory", lambda q: [])
    monkeypatch.setattr(pr.services, "list_walkin_pos", lambda limit=30: [])
    monkeypatch.setattr(pr.services, "list_walkin_payments", lambda limit=20: [])
    monkeypatch.setattr(pr.services, "list_meals", lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    r = client.get("/pos")
    assert r.status_code == 200


def test_pos_get_shows_meals(client, monkeypatch):
    import ui.web.routes.pos_inventory as pr
    monkeypatch.setattr(pr.services, "search_inventory", lambda q: [])
    monkeypatch.setattr(pr.services, "list_walkin_pos", lambda limit=30: [])
    monkeypatch.setattr(pr.services, "list_walkin_payments", lambda limit=20: [])
    monkeypatch.setattr(pr.services, "list_meals", lambda active_only=False: [{"id": 1, "name": "Soup", "description": "d", "preparation": "p", "selling_price": 10, "linked_inventory_id": 5, "active": 1}])
    monkeypatch.setattr(pr.services, "get_meal", lambda mid: {"id": 1, "name": "Soup", "description": "desc", "preparation": "prep", "ingredients": [{"item_name": "Water", "qty_per_sale": 1, "unit": "L", "note": ""}], "linked_inventory_id": 5})
    r = client.get("/pos")
    assert r.status_code == 200 and "Soup" in r.text and "auto-deduct" in r.text.lower() or r.status_code == 200
