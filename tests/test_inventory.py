"""Inventory: CRUD, restock, warnings, soft delete."""
import pytest
from app_core import services
from app_core.db import connect


def test_add_item(clean_db):
    services.add_inventory_item("Chips", "F&B", "pcs", 50, 5.0, 15.0, 10, 100, "testpass")
    row = connect().execute("SELECT item_name, stock_qty FROM inventory WHERE item_name='Chips'").fetchone()
    assert row["stock_qty"] == 50


def test_add_requires_password(clean_db):
    with pytest.raises(PermissionError):
        services.add_inventory_item("Chips", "F&B", "pcs", 50, 5.0, 15.0, 10, 100, "wrong")


def test_update_item(clean_db):
    services.update_inventory_item(1, "Water Pro", "F&B", "pcs", 25.0, 5, 200, "testpass")
    row = connect().execute("SELECT item_name, selling_price FROM inventory WHERE id=1").fetchone()
    assert row["item_name"] == "Water Pro"
    assert row["selling_price"] == 25.0


def test_delete_zero_stock_item(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit) VALUES ('Empty', 'Test', 0, 'pcs')")
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    services.delete_inventory_item(iid, "testpass")
    row = connect().execute("SELECT active FROM inventory WHERE id=?", (iid,)).fetchone()
    assert row["active"] == 0


def test_delete_nonzero_raises(clean_db):
    with pytest.raises(ValueError):
        services.delete_inventory_item(1, "testpass")  # Water has 100 stock


def test_restock_increases_stock(clean_db):
    before = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    services.restock_inventory(1, 20, 3.5, "testpass")
    after = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert after == before + 20


def test_restock_creates_asset_in(clean_db):
    services.restock_inventory(1, 10, 3.0, "testpass")
    row = connect().execute(
        "SELECT net FROM general_ledger WHERE entry_type='ASSET_IN' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["net"] == pytest.approx(30.0)


def test_restock_creates_inventory_log(clean_db):
    services.restock_inventory(1, 10, 3.0, "testpass")
    log = connect().execute(
        "SELECT change_type FROM inventory_logs WHERE item_id=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert log["change_type"] == "IN_RECEIVE"


def test_restock_requires_password(clean_db):
    with pytest.raises(PermissionError):
        services.restock_inventory(1, 10, 3.0, "wrong")


def test_restock_negative_qty_raises(clean_db):
    with pytest.raises(ValueError):
        services.restock_inventory(1, -5, 3.0, "testpass")


def test_weighted_average_cost(clean_db):
    # Seed item 1: 200 units @ 3.0; restock adds 100 @ 5.0
    services.restock_inventory(1, 100, 5.0, "testpass")
    row = connect().execute("SELECT unit_cost FROM inventory WHERE id=1").fetchone()
    # (200*3 + 100*5) / 300 = 3.666...
    assert row["unit_cost"] == pytest.approx(3.6667, rel=1e-3)


def test_warnings_low_stock(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("UPDATE inventory SET stock_qty=2 WHERE id=1")  # Water below threshold
    warns = services.get_inventory_warnings()
    assert len(warns["low"]) >= 1


def test_warnings_negative_stock(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("UPDATE inventory SET stock_qty=-5 WHERE id=1")
    warns = services.get_inventory_warnings()
    assert len(warns["negative"]) >= 1


def test_warnings_overstock(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("UPDATE inventory SET stock_qty=500 WHERE id=1")
    warns = services.get_inventory_warnings()
    assert len(warns["over"]) >= 1


def test_search_inventory_by_name(clean_db):
    rows = services.search_inventory("Bottled Water")
    assert len(rows) >= 1
    assert any(r["item_name"] == "Bottled Water (0.5L)" for r in rows)


def test_search_inventory_filters_category(clean_db):
    rows = services.search_inventory("", "F&B")
    assert len(rows) >= 2


def test_search_inventory_filters_negative_stock(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("UPDATE inventory SET stock_qty=-5 WHERE id=1")
    rows = services.search_inventory("", "", "Negative")
    assert len(rows) == 1


def test_soft_deleted_hidden_from_search(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("INSERT INTO inventory (item_name, category, stock_qty, unit) VALUES ('Temp', 'Test', 0, 'pcs')")
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    services.delete_inventory_item(iid, "testpass")
    rows = services.search_inventory("Temp")
    assert len(rows) == 0