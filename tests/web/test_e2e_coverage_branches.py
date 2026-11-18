"""Additional HTTP-level branch/edge coverage for trimmed-scope workflows."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app_core import db, services
from tests.web.conftest import set_session


def _room_ids(n=3):
    rows = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT ?", (n,)).fetchall()
    assert len(rows) >= n
    return [int(r["id"]) for r in rows]


def _future(days_from=200, nights=1):
    ci = date.today() + timedelta(days=days_from)
    return ci.strftime("%Y-%m-%d"), (ci + timedelta(days=nights)).strftime("%Y-%m-%d")


def _post(client, url, data, unlocked=False):
    csrf = set_session(client, unlocked=unlocked)
    return client.post(url, data={"csrf": csrf, **data}, follow_redirects=False)


def _redir(resp, contains):
    assert resp.status_code in (302, 303), (resp.status_code, getattr(resp, "text", "")[:200])
    loc = resp.headers.get("location", "")
    assert contains in loc, (contains, loc)
    return loc


def test_reservation_quote_modify_and_extras(client):
    room_id, room2 = _room_ids(2)[:2]
    ci, co = _future(200, 2)

    # pricing quote (success) and quote error path
    r = _post(client, "/reservations/quote", {
        "guest_name": "Q", "source": "Direct", "room_id": str(room_id),
        "rate_plan_id": "", "rate": "120", "check_in": ci, "check_out": co,
        "deposit": "0",
    })
    _redir(r, "tab=create")
    assert "quote=1" in r.headers.get("location", "") or "quote_error=" in r.headers.get("location", "")

    bad = _post(client, "/reservations/quote", {
        "guest_name": "Q", "source": "Direct", "room_id": str(room_id),
        "rate": "120", "check_in": "bad", "check_out": co,
    })
    _redir(bad, "tab=create")

    # create invalid reservation -> error redirect
    err = _post(client, "/reservations/create", {
        "guest_name": "", "source": "Direct", "room_id": str(room_id),
        "rate": "100", "check_in": ci, "check_out": co,
    })
    _redir(err, "tab=create")

    rid = services.create_reservation("Modify Guest", "Direct", room_id, None, 100.0, ci, co, 0.0)

    # quote modify success/error
    qm = _post(client, f"/reservations/{rid}/quote-modify", {
        "room_id": str(room2), "check_in": ci, "check_out": co, "rate": "110",
    })
    _redir(qm, f"/reservations/{rid}")

    qmb = _post(client, f"/reservations/{rid}/quote-modify", {
        "room_id": str(room2), "check_in": "bad", "check_out": co, "rate": "110",
    })
    _redir(qmb, f"/reservations/{rid}")

    # modify reservation (unlocked)
    mod = _post(client, f"/reservations/{rid}/modify", {
        "room_id": str(room2), "check_in": ci, "check_out": co, "rate": "130",
        "source": "OTA", "source_detail": "x", "market_segment": "leisure", "booking_notes": "m",
        "password": "testpass",
    }, unlocked=True)
    _redir(mod, f"/reservations/{rid}")

    # detail with modify quote params renders
    set_session(client, unlocked=False)
    page = client.get(f"/reservations/{rid}?modify_quote_error=oops")
    assert page.status_code == 200

    # checkin/checkout/cancel/noshow/delete style actions: checkout when unpaid may flash override path
    ci2, co2 = _future(210, 1)
    rid2 = services.create_reservation("Flow Guest", "Direct", room_id, None, 80.0, ci2, co2, 0.0)
    _redir(_post(client, f"/reservations/{rid2}/checkin", {}), f"/reservations/{rid2}")
    # try normal checkout with unpaid balance (may require override depending on settings)
    _post(client, f"/reservations/{rid2}/checkout", {})
    # checkout override with password (if still checked in) or error handled
    _post(client, f"/reservations/{rid2}/checkout-override", {"password": "testpass"}, unlocked=True)

    ci3, co3 = _future(220, 1)
    rid3 = services.create_reservation("Cancel Guest", "Direct", room_id, None, 80.0, ci3, co3, 0.0)
    _redir(_post(client, f"/reservations/{rid3}/cancel", {"password": "testpass"}, unlocked=True), f"/reservations/{rid3}")

    ci4, co4 = _future(230, 1)
    rid4 = services.create_reservation("NoShow Guest", "Direct", room_id, None, 80.0, ci4, co4, 0.0)
    _redir(_post(client, f"/reservations/{rid4}/noshow", {"password": "testpass"}, unlocked=True), f"/reservations/{rid4}")

    ci5, co5 = _future(240, 1)
    rid5 = services.create_reservation("Delete Guest", "Direct", room_id, None, 80.0, ci5, co5, 0.0)
    _redir(_post(client, f"/reservations/{rid5}/delete", {"password": "testpass"}, unlocked=True), "/reservations")


def test_reservation_services_discounts_packages_and_groups(client):
    room1, room2 = _room_ids(2)
    ci, co = _future(260, 1)
    rid = services.create_reservation("Extras Guest", "Direct", room1, None, 100.0, ci, co, 0.0)

    # service catalog item for extras
    try:
        svc_id = services.add_service_catalog_item("Laundry", category="service", unit_price=25.0, tax_rate=0.0, active=1)
    except Exception:
        svc_id = None

    # add ad-hoc service and void
    r = _post(client, f"/reservations/{rid}/services/add", {"description": "Adhoc", "quantity": "1", "unit_price": "10"})
    _redir(r, f"/reservations/{rid}")
    sl = db.connect().execute("SELECT id FROM reservation_services WHERE reservation_id=? AND COALESCE(voided_at,'')='' ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    if sl:
        _post(client, f"/reservations/{rid}/services/{int(sl['id'])}/void", {"password": "testpass"}, unlocked=True)

    if svc_id:
        _post(client, f"/reservations/{rid}/services/add", {"service_id": str(svc_id), "quantity": "2"})

    # discount apply and void
    _post(client, f"/reservations/{rid}/discounts/apply", {"discount_type": "amount", "value": "5", "description": "disc"})
    dl = db.connect().execute("SELECT id FROM reservation_discounts WHERE reservation_id=? AND COALESCE(voided_at,'')='' ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
    if dl:
        _post(client, f"/reservations/{rid}/discounts/{int(dl['id'])}/void", {"password": "testpass"}, unlocked=True)

    # package apply/void if supported
    pkg_id = None
    try:
        if hasattr(services, "create_package"):
            pkg_id = services.create_package("TestPkg", description="pkg", price=20.0, active=1)
    except Exception:
        pkg_id = None
    if pkg_id:
        _post(client, f"/reservations/{rid}/packages/apply", {"package_id": str(pkg_id), "notes": "n"})
        rp = db.connect().execute("SELECT id FROM reservation_packages WHERE reservation_id=? AND status='applied' ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        if rp:
            _post(client, f"/reservations/{rid}/packages/{int(rp['id'])}/void", {"password": "testpass"}, unlocked=True)

    # groups management branches
    _post(client, "/reservations/groups/create", {"name": "", "group_type": "group"}, unlocked=True)  # error
    _redir(_post(client, "/reservations/groups/create", {"name": "Cov Group", "group_type": "group"}, unlocked=True), "/reservations/groups")
    gid = int(db.connect().execute("SELECT id FROM reservation_groups WHERE name='Cov Group' ORDER BY id DESC LIMIT 1").fetchone()["id"])

    ci2, co2 = _future(262, 1)
    rid2 = services.create_reservation("Extras Guest 2", "Direct", room2, None, 90.0, ci2, co2, 0.0)
    _redir(_post(client, f"/reservations/groups/{gid}/add", {"reservation_id": str(rid), "role": "member"}, unlocked=True), "/reservations/groups")
    _redir(_post(client, f"/reservations/groups/{gid}/add", {"reservation_id": str(rid2), "role": "member"}, unlocked=True), "/reservations/groups")
    _post(client, f"/reservations/groups/{gid}/update", {"name": "Cov Group 2", "group_type": "group", "status": "active"}, unlocked=True)
    _post(client, f"/reservations/groups/{gid}/remove/{rid2}", {}, unlocked=True)

    # coordinated payment error: member not in group
    _post(client, f"/reservations/groups/{gid}/payment", {"reservation_id": str(rid2), "amount": "10", "method": "Cash"}, unlocked=True)
    # missing group folio
    set_session(client, unlocked=False)
    nf = client.get("/reservations/groups/999999/folio")
    assert nf.status_code in (302, 303)


def test_accounting_web_routes_and_errors(client):
    # expense categories seeded
    cat = db.connect().execute("SELECT id FROM expense_categories ORDER BY id LIMIT 1").fetchone()
    assert cat is not None
    cat_id = int(cat["id"])

    # expense add/void
    r = _post(client, "/accounting/expense", {"cat_id": str(cat_id), "param": "Supplies", "amount": "12.5", "vat": "0", "notes": "n", "method": "Cash"}, unlocked=True)
    _redir(r, "/accounting?tab=expenses")
    eid = db.connect().execute("SELECT id FROM operational_expenses ORDER BY id DESC LIMIT 1").fetchone()["id"]
    _post(client, f"/accounting/expense/{int(eid)}/void", {"password": "testpass"}, unlocked=True)

    # expense errors
    _post(client, "/accounting/expense", {"cat_id": str(cat_id), "param": "Bad", "amount": "-1"}, unlocked=True)

    # accounting payment/refund (non-reservation payment may be allowed as walk-in depending on implementation)
    room_id = _room_ids(1)[0]
    ci, co = _future(280, 1)
    rid = services.create_reservation("Acct Guest", "Direct", room_id, None, 100.0, ci, co, 0.0)
    _post(client, "/accounting/payment", {"reservation_id": str(rid), "amount": "10", "method": "Cash", "payment_type": "Payment"}, unlocked=True)
    _post(client, "/accounting/refund", {"reservation_id": str(rid), "amount": "5", "method": "Cash", "notes": "r", "password": "testpass"}, unlocked=True)
    # refund error
    _post(client, "/accounting/refund", {"reservation_id": str(rid), "amount": "9999", "method": "Cash", "notes": "bad", "password": "testpass"}, unlocked=True)

    # shifts: open, movement, close, error paths
    # close any existing open shift to allow opening a fresh one in this test context
    try:
        os_ = services.get_open_cash_shift()
        if os_:
            services.close_cash_shift(int(os_["id"]), counted_cash=0, notes="test reset")
    except Exception:
        pass
    _post(client, "/accounting/shifts/open", {"name": "CovShift", "opening_float": "50", "notes": ""}, unlocked=True)
    sh = services.get_open_cash_shift()
    assert sh is not None
    sid = int(sh["id"])
    _post(client, f"/accounting/shifts/{sid}/movement", {"movement_type": "cash_in", "amount": "5", "method": "Cash"}, unlocked=True)
    _post(client, f"/accounting/shifts/{sid}/movement", {"movement_type": "bad", "amount": "notnum"}, unlocked=True)  # error branch
    _post(client, f"/accounting/shifts/{sid}/close", {"counted_cash": "55", "notes": "done"}, unlocked=True)
    # close/movement on closed/invalid shift errors
    _post(client, f"/accounting/shifts/{sid}/close", {"counted_cash": "55"}, unlocked=True)
    _post(client, "/accounting/shifts/999999/movement", {"movement_type": "cash_in", "amount": "1"}, unlocked=True)

    # night audit run error branch
    _post(client, "/accounting/night-audit/run", {"business_date": "not-a-date", "notes": "x"}, unlocked=True)

    # accounting page with invalid/selected shift
    set_session(client, unlocked=False)
    assert client.get("/accounting?tab=shifts&shift_id=999999").status_code == 200
    assert client.get("/accounting?tab=overview").status_code == 200


def test_housekeeping_error_and_edge_branches(client):
    room_id = _room_ids(1)[0]

    # create task with bad room id -> error
    _post(client, "/housekeeping/tasks/create", {"room_id": "999999", "task_type": "cleaning"})

    # generate and edit/status error paths
    _post(client, "/housekeeping/tasks/999999/status", {"status": "done"})
    _post(client, "/housekeeping/tasks/999999/edit", {"task_type": "cleaning", "priority": "normal"})

    # create valid task then edit/status with notes append and filters
    _post(client, "/housekeeping/tasks/create", {"room_id": str(room_id), "task_type": "cleaning", "priority": "normal", "notes": "n"})
    t = db.connect().execute("SELECT id FROM housekeeping_tasks WHERE room_id=? ORDER BY id DESC LIMIT 1", (room_id,)).fetchone()
    if t:
        tid = int(t["id"])
        _post(client, f"/housekeeping/tasks/{tid}/status", {"status": "in_progress", "notes_append": "x"})
        _post(client, f"/housekeeping/tasks/{tid}/edit", {"task_type": "inspection", "priority": "high", "assignee": "a", "notes": "e", "status_filter": "open", "block_status_filter": "active"})

    # blocks error paths
    _post(client, "/housekeeping/blocks/create", {"room_id": "999999", "block_type": "OOO", "reason": "x", "start_date": "2026-12-01", "end_date": "2026-12-02"}, unlocked=True)
    _post(client, "/housekeeping/blocks/999999/resolve", {}, unlocked=True)

    # page renders with filters
    set_session(client, unlocked=False)
    assert client.get("/housekeeping?status=open&block_status=all").status_code == 200
    assert client.get("/housekeeping?block_status=bogus").status_code == 200
