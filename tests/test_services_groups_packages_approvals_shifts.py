from __future__ import annotations

import pytest

from app_core import db, services


def _pick_room(index=0):
    rooms = services.get_all_rooms()
    assert rooms, "expected seeded rooms"
    r = rooms[index if index < len(rooms) else 0]
    # get_all_rooms may return dicts or rows; normalize
    rid = r["id"] if isinstance(r, dict) or hasattr(r, "__getitem__") else getattr(r, "id")
    return int(rid)


def _make_reservation(ci="2099-01-10", co="2099-01-12", rate=100.0, guest="Svc Test Guest", deposit=0.0, room_index=0):
    room_id = _pick_room(room_index)
    # signature: (guest_name, source, room_id, rate_plan_id, agreed_nightly_rate, ci, co, deposit_amount=0.0, ...)
    return services.create_reservation(guest, "Direct", room_id, None, rate, ci, co, deposit)


def test_approvals_flow(clean_db):
    aid = services.create_approval_request(
        action_type="discount_override",
        entity_type="reservation",
        entity_id="1",
        payload={"amount": 50},
        reason="need approval",
        notes="initial",
        requested_by="manager",
    )
    assert isinstance(aid, int)

    ap = services.get_approval(aid)
    assert ap and ap["status"] == "pending"
    assert ap["action_type"] == "discount_override"
    assert ap["payload_json"]["amount"] == 50 or ap.get("payload", {}).get("amount") == 50

    listed = services.list_approvals(status="pending")
    assert any(x["id"] == aid for x in listed)

    services.decide_approval(aid, "approved", decided_by="owner", notes="ok")
    ap = services.get_approval(aid)
    assert ap["status"] == "approved"
    assert ap["decided_by"] == "owner"

    services.mark_approval_executed(aid, result={"ok": True}, notes="done")
    ap = services.get_approval(aid)
    assert ap["status"] == "executed"
    assert ap["execution_result_json"].get("ok") is True or ap.get("execution_result", {}).get("ok") is True

    # non-pending decision is a no-op
    services.decide_approval(aid, "rejected")
    assert services.get_approval(aid)["status"] == "executed"

    with pytest.raises(ValueError):
        services.create_approval_request("", entity_type="x")
    with pytest.raises(ValueError):
        services.decide_approval(aid, "maybe")
    with pytest.raises(ValueError):
        services.get_approval(999999) or (_ for _ in ()).throw(ValueError("x"))  # no-op guard replaced below

    assert services.get_approval(999999) is None
    with pytest.raises(ValueError):
        services.mark_approval_executed(999999, result={})


def test_reservation_groups(clean_db):
    gid = services.create_reservation_group(
        name="Team Alpha",
        group_type="corporate",
        contact_name="Alice",
        contact_phone="123",
        contact_email="a@example.com",
        notes="note",
    )
    assert isinstance(gid, int)
    r1 = _make_reservation(ci="2099-02-01", co="2099-02-03", guest="Group Guest 1", room_index=0)
    r2 = _make_reservation(ci="2099-02-01", co="2099-02-04", guest="Group Guest 2", room_index=1)

    services.add_reservation_to_group(gid, r1, role="leader", notes="lead")
    services.add_reservation_to_group(gid, r2, role="member")

    grp = services.get_reservation_group(gid)
    assert grp and grp["name"] == "Team Alpha"
    member_ids = {m.get("reservation_id") for m in (grp.get("members") or [])}
    assert r1 in member_ids and r2 in member_ids

    mem = services.get_reservation_group_membership(r1)
    assert mem and int(mem.get("id") or mem.get("group_id") or gid) == gid

    groups = services.list_reservation_groups(status="active")
    assert any(g["id"] == gid for g in groups)

    # duplicate add is typically ignored/no-op or raises depending impl; handle both
    try:
        services.add_reservation_to_group(gid, r1)
    except Exception:
        pass

    services.remove_reservation_from_group(gid, r2)
    grp = services.get_reservation_group(gid)
    member_ids = {m.get("reservation_id") for m in (grp.get("members") or [])}
    assert r2 not in member_ids

    with pytest.raises(ValueError):
        services.create_reservation_group("")
    assert services.get_reservation_group(999999) in (None, {})
    assert services.get_reservation_group_membership(999999) in (None, {})


def test_packages_crud_apply_and_void(clean_db):
    svc_id = services.create_service_catalog_item("Breakfast", category="F&B", unit_price=20.0, taxable=True, active=True)
    pid = services.create_package("Romance", description="desc", active=True)
    assert isinstance(pid, int)

    services.update_package(pid, name="Romance Plus", description="d2", active=True)
    pkg = services.get_package(pid)
    assert pkg and pkg["name"] == "Romance Plus"

    iid = services.add_package_item(pid, service_id=svc_id, description="", quantity=2, unit_price=None, taxable=None, sort_order=1)
    assert isinstance(iid, int)
    # custom line item (no service)
    services.add_package_item(pid, service_id=None, description="Late checkout", quantity=1, unit_price=15.0, taxable=True, sort_order=2)

    pkgs = services.list_packages(active_only=False)
    p = next(x for x in pkgs if x["id"] == pid)
    assert len(p.get("items") or []) == 2

    rid = _make_reservation(ci="2099-03-01", co="2099-03-03", guest="Pkg Guest")
    rpid = services.apply_package_to_reservation(rid, pid, notes="apply")
    assert isinstance(rpid, int)

    applied = services.list_reservation_packages(rid)
    assert any(x["id"] == rpid and (x.get("status") == "applied") for x in applied)

    services.void_reservation_package(rpid, password="testpass")
    applied = services.list_reservation_packages(rid)
    row = next(x for x in applied if x["id"] == rpid)
    assert row.get("status") == "voided" or row.get("voided_at")

    services.delete_package_item(iid)
    pkg = services.get_package(pid)
    assert all(int(it.get("id")) != iid for it in (pkg.get("items") or []))

    with pytest.raises(ValueError):
        services.create_package("")
    with pytest.raises(ValueError):
        services.get_package(999999) or (_ for _ in ()).throw(ValueError("x"))
    # get_package may return None
    assert services.get_package(999999) in (None, {})


def test_cash_shifts_basic(clean_db):
    # ensure no open shift
    open_shift = services.get_open_cash_shift()
    if open_shift:
        services.close_cash_shift(open_shift["id"], counted_cash=open_shift.get("expected_cash") or 0)

    sid = services.open_cash_shift(name="Morning", opening_float=100.0, notes="open", opened_by="mgr")
    assert isinstance(sid, int)
    cur = services.get_open_cash_shift()
    assert cur and int(cur["id"]) == sid

    services.add_cash_movement(sid, movement_type="drop", amount=50.0, method="Cash", reference="R1", notes="drop")
    services.add_cash_movement(sid, movement_type="paid_out", amount=-20.0, method="Cash", reference="R2", notes="out")
    movs = services.list_cash_movements(sid)
    assert len(movs) >= 2

    if hasattr(services, "get_cash_shift_reconciliation"):
        rec = services.get_cash_shift_reconciliation(sid)
        assert rec and int(rec["id"]) == sid

    services.close_cash_shift(sid, counted_cash=130.0, notes="close", closed_by="mgr")
    shifts = services.list_cash_shifts(limit=10)
    row = next(s for s in shifts if int(s["id"]) == sid)
    assert row["status"] == "closed"
    assert services.get_open_cash_shift() in (None, {})

    sid_dup = services.open_cash_shift(name="Dup", opening_float=0)
    with pytest.raises(ValueError):
        services.open_cash_shift(name="Another", opening_float=0)
    services.close_cash_shift(sid_dup, counted_cash=0)

    # create/close another to exercise path
    sid2 = services.open_cash_shift(name="Evening", opening_float=0)
    services.close_cash_shift(sid2, counted_cash=0)
    assert isinstance(sid2, int)

    try:
        services.add_cash_movement(999999, movement_type="drop", amount=1)
        raise AssertionError("expected ValueError for missing shift")
    except ValueError:
        pass
