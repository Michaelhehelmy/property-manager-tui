"""POS: add items, stock, COGS, void, walk-in."""
import pytest
from app_core import services
from app_core.db import connect
from conftest import make_reservation


def test_add_pos_item(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Beer", 2, 50, None)
    assert result["pos_id"] > 0
    assert result["gross"] == pytest.approx(2 * 50 * (1 + 0.10) * (1 + 0.14), abs=0.5)


def test_add_pos_negative_qty_raises(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", -1, 10, None)


def test_add_pos_zero_qty_raises(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 0, 10, None)


def test_add_pos_negative_price_raises(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 1, -10, None)


def test_add_pos_empty_desc_raises(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "", 1, 10, None)


def test_add_pos_to_cancelled_raises(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 1, 10, None)


def test_add_pos_invalid_reservation_raises(clean_db):
    with pytest.raises(ValueError):
        services.add_pos_item(999, "X", 1, 10, None)


def test_add_pos_deducts_stock(clean_db):
    rid = make_reservation()
    before = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    services.add_pos_item(rid, "Water", 5, 20, 1)
    after = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert after == before - 5


def test_add_pos_creates_cogs(clean_db):
    rid = make_reservation()
    services.add_pos_item(rid, "Water", 3, 20, 1)  # item 1 has unit_cost=3
    cogs = connect().execute(
        "SELECT net FROM general_ledger WHERE entry_type='COGS' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert cogs["net"] == pytest.approx(9.0)  # 3 * 3


def test_insufficient_stock_raises(clean_db):
    services.set_setting("allow_negative_stock", "false")
    rid = make_reservation()
    # Item 4 (Propane) has stock 3
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "Propane", 10, 250, 4)


def test_negative_stock_allowed_by_default(clean_db):
    rid = make_reservation()
    # Default allow_negative_stock=true
    services.add_pos_item(rid, "Propane", 10, 250, 4)
    # Stock should be -7
    after = connect().execute("SELECT stock_qty FROM inventory WHERE id=4").fetchone()["stock_qty"]
    assert after == -7


def test_walkin_creates_payment(clean_db):
    """Walk-in: add_pos_item creates the charge; record_payment creates the payment."""
    services.add_pos_item(None, "Water", 1, 20, None)
    charge = connect().execute(
        "SELECT gross FROM general_ledger WHERE ref_type='POS' AND entry_type='CHARGE'"
    ).fetchone()["gross"]
    assert charge > 0
    services.record_payment(None, charge, "Cash")
    pay = connect().execute("SELECT amount, method FROM payments WHERE reservation_id IS NULL").fetchone()
    assert pay is not None
    assert pay["method"] == "Cash"
    assert pay["amount"] == pytest.approx(charge)


def test_void_restores_stock(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Water", 3, 20, 1)
    before_void = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    services.void_pos_item(result["pos_id"], "testpass")
    after_void = connect().execute("SELECT stock_qty FROM inventory WHERE id=1").fetchone()["stock_qty"]
    assert after_void == before_void + 3


def test_void_reverses_cogs(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Water", 2, 20, 1)
    services.void_pos_item(result["pos_id"], "testpass")
    # COGS + its reversal
    rows = connect().execute(
        "SELECT entry_type, net FROM general_ledger WHERE entry_type IN ('COGS','VOID_COGS') ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["net"] == pytest.approx(6.0)
    assert rows[1]["net"] == pytest.approx(-6.0)


def test_void_twice_raises(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Water", 1, 20, 1)
    services.void_pos_item(result["pos_id"], "testpass")
    with pytest.raises(ValueError):
        services.void_pos_item(result["pos_id"], "testpass")


def test_void_requires_password(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Water", 1, 20, 1)
    with pytest.raises(PermissionError):
        services.void_pos_item(result["pos_id"], "wrong")


def test_pos_charge_ref_type(clean_db):
    rid = make_reservation()
    result = services.add_pos_item(rid, "Water", 1, 20, 1)
    charge = connect().execute(
        "SELECT ref_type, ref_id FROM general_ledger WHERE entry_type='CHARGE' AND ref_type='POS'"
    ).fetchone()
    assert charge["ref_type"] == "POS"
    assert charge["ref_id"] == result["pos_id"]