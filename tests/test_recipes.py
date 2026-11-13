"""Recipe/BOM MVP: components CRUD + POS ingredient auto-deduction."""
import pytest
from app_core import db, services
from conftest import make_reservation


def _inv(name, stock=10.0, cost=5.0, price=20.0, unit="pcs", cat="F&B"):
    services.add_inventory_item(name, cat, unit, stock, cost, price, 0, 0, "testpass")
    row = db.connect().execute("SELECT id FROM inventory WHERE item_name=? ORDER BY id DESC LIMIT 1", (name,)).fetchone()
    return int(row["id"])


def _stock(inv_id):
    return float(db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (inv_id,)).fetchone()["stock_qty"])


def test_recipe_crud_and_has_recipe(unlocked):
    meal = _inv("Burger Meal", stock=0)
    bun = _inv("Bun", stock=50)
    patty = _inv("Patty", stock=30)
    assert services.get_recipe_components(meal) == []
    assert services.inventory_has_recipe(meal) is False
    comps = services.set_recipe_components(meal, [
        {"ingredient_item_id": bun, "qty_per_sale": 1},
        {"ingredient_item_id": patty, "qty_per_sale": 2},
    ], "testpass")
    assert len(comps) == 2
    assert services.inventory_has_recipe(meal) is True
    got = services.get_recipe_components(meal)
    assert {c["ingredient_item_id"] for c in got} == {bun, patty}
    # replace
    services.set_recipe_components(meal, [{"ingredient_item_id": bun, "qty_per_sale": 1}], "testpass")
    assert len(services.get_recipe_components(meal)) == 1
    services.clear_recipe_components(meal, "testpass")
    assert services.get_recipe_components(meal) == []
    assert services.inventory_has_recipe(meal) is False


def test_recipe_crud_validation(unlocked):
    meal = _inv("Meal A", stock=0)
    ing = _inv("Ing A", stock=5)
    with pytest.raises(PermissionError):
        services.set_recipe_components(meal, [{"ingredient_item_id": ing, "qty_per_sale": 1}], "wrong")
    with pytest.raises(ValueError, match="Menu item not found"):
        services.set_recipe_components(999999, [{"ingredient_item_id": ing, "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="numeric"):
        services.set_recipe_components(meal, [{"ingredient_item_id": "x", "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="itself"):
        services.set_recipe_components(meal, [{"ingredient_item_id": meal, "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="Duplicate"):
        services.set_recipe_components(meal, [
            {"ingredient_item_id": ing, "qty_per_sale": 1},
            {"inv_id": ing, "qty": 2},
        ], "testpass")
    with pytest.raises(ValueError, match="greater than 0"):
        services.set_recipe_components(meal, [{"ingredient_item_id": ing, "qty_per_sale": 0}], "testpass")
    with pytest.raises(ValueError, match="Ingredient .* not found"):
        services.set_recipe_components(meal, [{"ingredient_item_id": 999999, "qty_per_sale": 1}], "testpass")
    with pytest.raises(PermissionError):
        services.clear_recipe_components(meal, "bad")
    # delete cleans recipe refs (soft delete requires zero stock)
    services.set_recipe_components(meal, [{"ingredient_item_id": ing, "qty_per_sale": 1}], "testpass")
    services.delete_inventory_item(meal, "testpass")
    assert db.connect().execute("SELECT COUNT(*) FROM recipe_components WHERE menu_item_id=?", (meal,)).fetchone()[0] == 0


def test_pos_recipe_deducts_ingredients_not_meal_stock(unlocked):
    meal = _inv("Set Meal", stock=5, cost=0, price=50)
    bun = _inv("RBun", stock=10, cost=1)
    patty = _inv("RPatty", stock=10, cost=2)
    services.set_recipe_components(meal, [
        {"ingredient_item_id": bun, "qty_per_sale": 1},
        {"ingredient_item_id": patty, "qty_per_sale": 0.5},
    ], "testpass")
    rid = make_reservation()
    res = services.add_pos_item(rid, "Set Meal", 2, 50.0, meal)
    assert res["recipe_deducted"] is True
    assert _stock(meal) == 5.0  # meal stock untouched
    assert _stock(bun) == 8.0   # 1 * 2
    assert _stock(patty) == 9.0 # 0.5 * 2
    logs = db.connect().execute(
        "SELECT item_id, quantity, reference FROM inventory_logs WHERE reference LIKE ? ORDER BY item_id",
        (f"POS #{res['pos_id']} recipe:%",),
    ).fetchall()
    assert len(logs) == 2
    refs = " ".join(r["reference"] for r in logs)
    assert "Set Meal" in refs
    # COGS posted from ingredients: (1*1 + 2*0.5) * 2 = 4.0
    cogs = db.connect().execute(
        "SELECT COALESCE(SUM(net),0) FROM general_ledger WHERE ref_type='POS' AND ref_id=? AND entry_type='COGS'",
        (res["pos_id"],),
    ).fetchone()[0]
    assert abs(float(cogs) - 4.0) < 0.001


def test_walkin_sale_deducts_recipe_ingredients(unlocked):
    meal = _inv("Walk Meal", stock=0, cost=0, price=30)
    ing = _inv("WIng", stock=5, cost=3)
    services.set_recipe_components(meal, [{"ingredient_item_id": ing, "qty_per_sale": 2}], "testpass")
    out = services.complete_walkin_sale(
        [{"desc": "Walk Meal", "qty": 2, "price": 30.0, "inv_id": meal}],
        method="Cash",
    )
    assert out["payment_id"]
    assert _stock(ing) == 1.0  # 2 * 2


def test_recipe_insufficient_ingredient_stock(unlocked):
    services.set_setting("allow_negative_stock", "false")
    meal = _inv("Meal S", stock=0, cost=0, price=10)
    ing = _inv("ShortIng", stock=1, cost=1)
    services.set_recipe_components(meal, [{"ingredient_item_id": ing, "qty_per_sale": 2}], "testpass")
    rid = make_reservation()
    with pytest.raises(ValueError, match=r"Insufficient (ingredient|batch).*stock"):
        services.add_pos_item(rid, "Meal S", 1, 10.0, meal)
    assert _stock(ing) == 1.0


def test_recipe_allows_negative_stock_when_setting_enabled(unlocked):
    services.set_setting("allow_negative_stock", "true")
    meal = _inv("Meal Neg", stock=0, cost=0, price=10)
    ing = _inv("NegIng", stock=0, cost=1)
    services.set_recipe_components(meal, [{"ingredient_item_id": ing, "qty_per_sale": 1}], "testpass")
    rid = make_reservation()
    services.add_pos_item(rid, "Meal Neg", 2, 10.0, meal)
    assert _stock(ing) == -2.0


def test_recipe_inactive_ingredient_and_error_paths(unlocked, monkeypatch):
    meal = _inv("Meal E", stock=0, cost=0, price=10)
    ing = _inv("InactIng", stock=10, cost=1)
    services.set_recipe_components(meal, [{"ingredient_item_id": ing, "qty_per_sale": 1}], "testpass")
    db.connect().execute("UPDATE inventory SET active=0 WHERE id=?", (ing,))
    db.connect().commit()
    rid = make_reservation()
    with pytest.raises(ValueError, match="inactive"):
        services.add_pos_item(rid, "Meal E", 1, 10.0, meal)

    # inventory_has_recipe exception path
    import app_core.services as svc
    monkeypatch.setattr(db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("db")))
    assert svc.inventory_has_recipe(meal) is False

    # _recipe_components exception path returns []
    def boom(*a, **k):
        raise RuntimeError("x")
    class BadConn:
        def execute(self, *a, **k): raise RuntimeError("x")
    assert services._recipe_components(BadConn(), 1) == []


def test_non_recipe_item_behavior_unchanged(unlocked):
    inv = _inv("Plain Item", stock=5, cost=2, price=10)
    rid = make_reservation()
    res = services.add_pos_item(rid, "Plain Item", 1, 10.0, inv)
    assert res.get("recipe_deducted") is False
    assert _stock(inv) == 4.0

def test_recipe_component_aliases_and_bad_db_qty(unlocked):
    meal = _inv("Alias Meal", stock=0, cost=0, price=10)
    ing = _inv("AliasIng", stock=10, cost=1)
    services.set_recipe_components(meal, [{"inv_id": ing, "qty": 1.5}], "testpass")
    got = services.get_recipe_components(meal)
    assert abs(float(got[0]["qty_per_sale"]) - 1.5) < 0.001
    # invalid qty in DB triggers sale guard
    db.connect().execute("UPDATE recipe_components SET qty_per_sale=0 WHERE menu_item_id=?", (meal,))
    db.connect().commit()
    rid = make_reservation()
    with pytest.raises(ValueError, match="Invalid recipe quantity"):
        services.add_pos_item(rid, "Alias Meal", 1, 10.0, meal)


def test_meal_registration_auto_deducts_ingredients(unlocked):
    bun = _inv("MealBun", stock=20, cost=1)
    patty = _inv("MealPatty", stock=10, cost=3)
    res = services.create_meal(
        name="Cheeseburger", description="Beef burger", preparation="Grill patty, assemble bun.",
        selling_price=75.0,
        ingredients=[{"inventory_item_id": bun, "qty_per_sale": 1}, {"inventory_item_id": patty, "qty_per_sale": 0.5, "note": "kg"}],
        password="testpass",
    )
    assert res["id"] and res["linked_inventory_id"]
    meal = services.get_meal(res["id"])
    assert meal["name"] == "Cheeseburger"
    assert meal["preparation"].startswith("Grill")
    assert len(meal["ingredients"]) == 2
    # ordering meal via linked inventory auto-deducts registered ingredients
    rid = make_reservation()
    out = services.add_pos_item(rid, "Cheeseburger", 2, 75.0, meal["linked_inventory_id"])
    assert out["recipe_deducted"] is True
    assert _stock(bun) == 18.0
    assert _stock(patty) == 9.0


def test_meal_crud(unlocked):
    ing = _inv("MIng", stock=5, cost=1)
    r = services.create_meal("Soup", "Hot soup", "Boil", 30, [{"inventory_item_id": ing, "qty_per_sale": 1}], "testpass")
    meals = services.list_meals()
    assert any(m["name"] == "Soup" for m in meals)
    services.update_meal(r["id"], "Soup v2", "Hotter", "Boil more", 35, [{"inventory_item_id": ing, "qty_per_sale": 2}], "testpass", active=1)
    m = services.get_meal(r["id"])
    assert m["name"] == "Soup v2" and abs(m["ingredients"][0]["qty_per_sale"] - 2) < 0.001
    services.delete_meal(r["id"], "testpass")
    assert services.get_meal(r["id"]) is None


def test_meal_service_validation(unlocked):
    ing = _inv("VIng", stock=5, cost=1)
    with pytest.raises(PermissionError):
        services.create_meal("X", "", "", 1, [{"inventory_item_id": ing, "qty_per_sale": 1}], "bad")
    with pytest.raises(ValueError, match="name is required"):
        services.create_meal("", "", "", 1, [{"inventory_item_id": ing, "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="number"):
        services.create_meal("X", "", "", "no", [{"inventory_item_id": ing, "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="cannot be negative"):
        services.create_meal("X", "", "", -1, [{"inventory_item_id": ing, "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="Ingredient inventory item"):
        services.create_meal("X", "", "", 1, [{"inventory_item_id": 999999, "qty_per_sale": 1}], "testpass")
    r = services.create_meal("DupMeal", "", "", 1, [{"inventory_item_id": ing, "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="already exists"):
        services.create_meal("DupMeal", "", "", 1, [{"inventory_item_id": ing, "qty_per_sale": 1}], "testpass")
    with pytest.raises(ValueError, match="Meal not found"):
        services.update_meal(999999, "N", "", "", 1, [], "testpass")
    with pytest.raises(PermissionError):
        services.delete_meal(r["id"], "bad")
    assert services.get_meal(999999) is None
    # update to inactive clears linked recipe
    services.update_meal(r["id"], "DupMeal", "d", "p", 2, [{"inventory_item_id": ing, "qty_per_sale": 1}], "testpass", active=0)
    m = services.get_meal(r["id"])
    assert m["active"] == 0
