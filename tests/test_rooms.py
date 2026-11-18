"""Rooms + rate plans — every function, every branch."""
import pytest
from app_core import services
from app_core.db import connect


# ═══════════════════════════════════════════════════════════════════════════
# ROOMS
# ═══════════════════════════════════════════════════════════════════════════

def test_get_all_rooms(clean_db):
    rooms = services.get_all_rooms()
    assert len(rooms) == 10
    assert rooms[0]["room_number"] == "101"


def test_get_room_exists(clean_db):
    room = services.get_room(1)
    assert room["room_number"] == "101"
    assert room["room_type"] == "Standard Room"


def test_get_room_not_found(clean_db):
    assert services.get_room(999) is None


def test_update_housekeeping_status_valid(clean_db):
    services.update_housekeeping_status(1, "Dirty")
    assert services.get_room(1)["housekeeping_status"] == "Dirty"


def test_update_housekeeping_status_maintenance(clean_db):
    services.update_housekeeping_status(1, "Maintenance")
    assert services.get_room(1)["housekeeping_status"] == "Maintenance"


def test_update_housekeeping_status_invalid(clean_db):
    with pytest.raises(ValueError):
        services.update_housekeeping_status(1, "Invalid")


def test_update_room_price_valid(clean_db):
    services.update_room_price(1, 75.0, "testpass")
    assert services.get_room(1)["base_price"] == 75.0


def test_update_room_price_negative(clean_db):
    with pytest.raises(ValueError):
        services.update_room_price(1, -10.0, "testpass")


def test_update_room_price_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.update_room_price(1, 75.0, "wrong")


def test_update_room_type_valid(clean_db):
    services.update_room_type(1, "Deluxe Suite", "testpass")
    assert services.get_room(1)["room_type"] == "Deluxe Suite"


def test_update_room_type_empty(clean_db):
    with pytest.raises(ValueError):
        services.update_room_type(1, "", "testpass")


def test_update_room_type_whitespace_only(clean_db):
    with pytest.raises(ValueError):
        services.update_room_type(1, "   ", "testpass")


def test_update_room_type_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.update_room_type(1, "Deluxe", "wrong")


# ═══════════════════════════════════════════════════════════════════════════
# RATE PLANS
# ═══════════════════════════════════════════════════════════════════════════

def test_get_all_rate_plans(clean_db):
    plans = services.get_all_rate_plans()
    assert len(plans) == 5
    assert plans[0]["name"] == "Room Only"


def test_add_rate_plan_valid(clean_db):
    services.add_rate_plan("Weekly Rate", -10.0, "testpass")
    plans = services.get_all_rate_plans()
    names = [p["name"] for p in plans]
    assert "Weekly Rate" in names


def test_add_rate_plan_duplicate(clean_db):
    with pytest.raises(ValueError, match="already exists"):
        services.add_rate_plan("Room Only", 0.0, "testpass")


def test_add_rate_plan_case_insensitive_duplicate(clean_db):
    with pytest.raises(ValueError, match="already exists"):
        services.add_rate_plan("room only", 0.0, "testpass")


def test_add_rate_plan_empty_name(clean_db):
    with pytest.raises(ValueError):
        services.add_rate_plan("", 0.0, "testpass")


def test_add_rate_plan_wrong_password(clean_db):
    with pytest.raises(PermissionError):
        services.add_rate_plan("New", 0.0, "wrong")


def test_update_rate_plan_valid(clean_db):
    plans = services.get_all_rate_plans()
    pid = plans[0]["id"]
    services.update_rate_plan(pid, "Room Only Deluxe", 5.0, "testpass")
    updated = [p for p in services.get_all_rate_plans() if p["id"] == pid][0]
    assert updated["name"] == "Room Only Deluxe"
    assert updated["price_modifier"] == 5.0


def test_update_rate_plan_duplicate_name(clean_db):
    plans = services.get_all_rate_plans()
    pid = plans[0]["id"]
    with pytest.raises(ValueError, match="already exists"):
        services.update_rate_plan(pid, "Bed & Breakfast", 0.0, "testpass")


def test_update_rate_plan_keeping_same_name(clean_db):
    """Updating a plan but keeping its own name should not raise."""
    plans = services.get_all_rate_plans()
    pid = plans[0]["id"]
    services.update_rate_plan(pid, plans[0]["name"], 10.0, "testpass")
    updated = [p for p in services.get_all_rate_plans() if p["id"] == pid][0]
    assert updated["price_modifier"] == 10.0


def test_update_rate_plan_empty_name(clean_db):
    pid = services.get_all_rate_plans()[0]["id"]
    with pytest.raises(ValueError):
        services.update_rate_plan(pid, "", 0.0, "testpass")


def test_update_rate_plan_wrong_password(clean_db):
    pid = services.get_all_rate_plans()[0]["id"]
    with pytest.raises(PermissionError):
        services.update_rate_plan(pid, "X", 0.0, "wrong")


def test_delete_rate_plan_unused(clean_db):
    services.add_rate_plan("Temp Plan", 5.0, "testpass")
    plans = services.get_all_rate_plans()
    target = [p for p in plans if p["name"] == "Temp Plan"][0]
    services.delete_rate_plan(target["id"], "testpass")
    assert target["id"] not in [p["id"] for p in services.get_all_rate_plans()]


def test_delete_rate_plan_in_use(clean_db):
    """Cannot delete a plan referenced by a reservation."""
    plans = services.get_all_rate_plans()
    pid = plans[0]["id"]
    services.create_reservation("Test", "Direct", 1, pid, 100,
                                "2026-12-01", "2026-12-03")
    with pytest.raises(ValueError, match="Cannot delete"):
        services.delete_rate_plan(pid, "testpass")


def test_delete_rate_plan_wrong_password(clean_db):
    services.add_rate_plan("Temp", 0.0, "testpass")
    plans = services.get_all_rate_plans()
    target = [p for p in plans if p["name"] == "Temp"][0]
    with pytest.raises(PermissionError):
        services.delete_rate_plan(target["id"], "wrong")


def test_count_reservations_using_plan_zero(clean_db):
    pid = services.get_all_rate_plans()[0]["id"]
    assert services.count_reservations_using_plan(pid) == 0


def test_count_reservations_using_plan_nonzero(clean_db):
    pid = services.get_all_rate_plans()[0]["id"]
    services.create_reservation("T", "Direct", 1, pid, 100, "2026-12-01", "2026-12-03")
    assert services.count_reservations_using_plan(pid) == 1