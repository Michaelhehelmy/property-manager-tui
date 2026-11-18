from __future__ import annotations

import pytest

from app_core import services


def test_rate_rules_crud(clean_db):
    # seed a rate plan for FK/reference behavior (optional but useful)
    plans = services.get_all_rate_plans()
    plan_id = plans[0]["id"] if plans else None

    rid = services.add_rate_rule(
        name="Promo",
        adjustment_type="percent",
        adjustment_value=10,
        rate_plan_id=plan_id,
        room_type="",
        source="Direct",
        market_segment="",
        start_date="2099-01-01",
        end_date="2099-12-31",
        min_nights=1,
        max_nights=5,
        priority=50,
        active=True,
    )
    assert isinstance(rid, int)

    rules = services.list_rate_rules(active_only=False)
    assert any(r["id"] == rid and r["name"] == "Promo" and r["adjustment_type"] == "percent" for r in rules)
    active = services.list_rate_rules(active_only=True)
    assert any(r["id"] == rid for r in active)

    services.update_rate_rule(rid, name="Promo2", active=False, adjustment_value=15, priority=10)
    rules = services.list_rate_rules(active_only=False)
    row = next(r for r in rules if r["id"] == rid)
    assert row["name"] == "Promo2"
    assert int(row["active"]) == 0
    assert float(row["adjustment_value"]) == 15.0

    with pytest.raises(ValueError):
        services.update_rate_rule(999999, name="X")


def test_service_catalog_crud(clean_db):
    sid = services.create_service_catalog_item(
        name="Airport Pickup",
        category="Transport",
        unit_price=25.5,
        description="Pickup service",
        taxable=True,
        active=True,
    )
    assert isinstance(sid, int)

    item = services.get_service_catalog_item(sid)
    assert item and item["name"] == "Airport Pickup" and float(item["unit_price"]) == 25.5

    items = services.list_service_catalog(active_only=False)
    assert any(i["id"] == sid for i in items)

    services.update_service_catalog_item(sid, name="Airport Pickup VIP", unit_price=40, active=False, taxable=False)
    item = services.get_service_catalog_item(sid)
    assert item["name"] == "Airport Pickup VIP"
    assert float(item["unit_price"]) == 40.0
    assert int(item["active"]) == 0
    assert int(item["taxable"]) == 0

    with pytest.raises(ValueError):
        services.create_service_catalog_item("", unit_price=1)
    with pytest.raises(ValueError):
        services.create_service_catalog_item("Neg", unit_price=-1)
    with pytest.raises(ValueError):
        services.update_service_catalog_item(999999, name="X")
    with pytest.raises(ValueError):
        services.create_service_catalog_item("Airport Pickup VIP", unit_price=1)


def test_discount_vouchers_crud(clean_db):
    vid = services.create_discount_voucher(
        code="save10",
        discount_type="amount",
        value=10,
        name="Save 10",
        description="desc",
        max_uses=5,
        valid_from="2099-01-01",
        valid_to="2099-12-31",
        active=True,
    )
    assert isinstance(vid, int)

    vouchers = services.list_discount_vouchers(active_only=False)
    v = next(x for x in vouchers if x["id"] == vid)
    assert v["code"] == "SAVE10"
    assert float(v["value"]) == 10.0

    services.update_discount_voucher(vid, code="save15", value=15, discount_type="percent", max_uses=10, active=False)
    vouchers = services.list_discount_vouchers(active_only=False)
    v = next(x for x in vouchers if x["id"] == vid)
    assert v["code"] == "SAVE15"
    assert v["discount_type"] == "percent"
    assert float(v["value"]) == 15.0
    assert int(v["active"]) == 0

    # validation errors
    with pytest.raises(ValueError):
        services.create_discount_voucher("", discount_type="amount", value=1)
    with pytest.raises(ValueError):
        services.create_discount_voucher("BADPCT", discount_type="percent", value=150)
    with pytest.raises(ValueError):
        services.create_discount_voucher("ZERO", discount_type="amount", value=0)
    with pytest.raises(ValueError):
        services.update_discount_voucher(999999, code="X", value=1)
    with pytest.raises(ValueError):
        services.create_discount_voucher("SAVE15", discount_type="amount", value=5)
    with pytest.raises(ValueError):
        services.update_discount_voucher(vid, max_uses=-1)
