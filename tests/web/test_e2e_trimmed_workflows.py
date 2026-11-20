"""Local HTTP-level e2e workflow tests for trimmed property-manager scope.

Covers practical end-to-end flows against the real FastAPI app, routes, and
services with an isolated temp DB (tests.web.conftest). These are additive/local
integration tests (tests/ is gitignored in this repo).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app_core import db, services
from tests.web.conftest import get_csrf, set_session


# ── Helpers ──────────────────────────────────────────────────────────────────


def _room_id() -> int:
    row = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT 1").fetchone()
    assert row is not None, "expected seeded room"
    return int(row["id"])


def _room_ids(n: int = 2) -> list[int]:
    rows = db.connect().execute("SELECT id FROM rooms ORDER BY id LIMIT ?", (n,)).fetchall()
    assert len(rows) >= n, f"expected at least {n} seeded rooms"
    return [int(r["id"]) for r in rows]


def _future_range(days_from: int = 30, nights: int = 2) -> tuple[str, str]:
    ci = date.today() + timedelta(days=days_from)
    co = ci + timedelta(days=nights)
    return ci.strftime("%Y-%m-%d"), co.strftime("%Y-%m-%d")


def _post(client, url: str, data: dict, *, unlocked: bool = False, csrf: str | None = None, follow: bool = False):
    token = csrf or set_session(client, unlocked=unlocked)
    payload = {"csrf": token, **data}
    return client.post(url, data=payload, follow_redirects=follow)


def _get(client, url: str, *, unlocked: bool = False, csrf: str | None = None):
    set_session(client, unlocked=unlocked, csrf=csrf)
    return client.get(url)


def _reservation_detail(client, rid: int):
    # detail is accessible without unlock in this app; keep simple
    set_session(client, unlocked=False)
    return client.get(f"/reservations/{rid}")


def _assert_redirect(resp, contains: str):
    assert resp.status_code in (302, 303), (
        f"expected redirect, got {resp.status_code}: {getattr(resp, 'text', '')[:300]}"
    )
    loc = resp.headers.get("location", "")
    assert contains in loc, (contains, loc)
    return loc


def _active_payments_sum(rid: int) -> float:
    row = (
        db.connect()
        .execute(
            "SELECT COALESCE(SUM(amount),0) AS s FROM payments WHERE reservation_id=? AND voided_at IS NULL",
            (rid,),
        )
        .fetchone()
    )
    return round(float(row["s"] or 0), 2)


# ── 1) Owner unlock / CSRF / sensitive access controls ───────────────────────


def test_unlock_controls_and_csrf(client):
    # wrong password does not unlock
    csrf = set_session(client, unlocked=False)
    r = client.post(
        "/unlock", data={"csrf": csrf, "password": "wrongpass", "next": "/settings/health"}, follow_redirects=False
    )
    _assert_redirect(r, "/unlock")

    set_session(client, unlocked=False)
    blocked = client.get("/settings/health")
    assert blocked.status_code in (302, 303)
    assert "/unlock" in blocked.headers.get("location", "")

    # correct password unlocks and allows protected page
    csrf = set_session(client, unlocked=False)
    r = client.post(
        "/unlock", data={"csrf": csrf, "password": "testpass", "next": "/settings/health"}, follow_redirects=False
    )
    assert r.status_code in (302, 303)

    # session should now be unlocked
    set_session(client, unlocked=True)
    ok = client.get("/settings/health")
    assert ok.status_code == 200

    # CSRF mismatch is rejected for POST form
    set_session(client, unlocked=False, csrf="good-token")
    bad = client.post("/unlock", data={"csrf": "bad-token", "password": "testpass", "next": "/"})
    assert bad.status_code >= 400
    assert bad.status_code not in (200, 302, 303)

    # missing CSRF also rejected
    r = client.post("/unlock", data={"password": "testpass", "next": "/"})
    assert r.status_code >= 400
    assert r.status_code not in (200, 302, 303)


# ── 2) Reservation + deposit + folio/invoice/taxes ───────────────────────────


def test_reservation_deposit_folio_invoice_taxes_workflow(client, web_env):
    room_id = _room_id()
    ci, co = _future_range(40, nights=2)

    # create reservation with deposit (real service behavior: deposit posted consistently)
    csrf = set_session(client, unlocked=False)
    r = client.post(
        "/reservations/create",
        data={
            "csrf": csrf,
            "guest_name": "E2E Guest Deposit",
            "source": "Direct",
            "room_id": str(room_id),
            "rate_plan_id": "",
            "rate": "100",
            "check_in": ci,
            "check_out": co,
            "deposit": "50",
            "booking_notes": "e2e deposit",
        },
        follow_redirects=False,
    )
    loc = _assert_redirect(r, "/reservations?tab=list")

    # find created reservation
    row = (
        db.connect()
        .execute(
            "SELECT id FROM reservations WHERE guest_name=? ORDER BY id DESC LIMIT 1",
            ("E2E Guest Deposit",),
        )
        .fetchone()
    )
    assert row is not None
    rid = int(row["id"])

    bd = services.get_balance_breakdown(rid)
    assert bd is not None
    assert float(bd.get("total_paid") or 0) == pytest.approx(50.0, abs=0.01)
    assert float(bd.get("total_charges") or 0) > 0.0
    assert float(bd.get("accommodation_gross") or 0) >= float(bd.get("accommodation_net") or 0)
    assert (
        abs(
            float(bd.get("accommodation_gross") or 0)
            + float(bd.get("pos_gross") or 0)
            + float(bd.get("services_gross") or 0)
            + float(bd.get("discounts_gross") or 0)
            - float(bd.get("total_charges") or 0)
        )
        < 0.02
    )
    assert (
        abs(
            float(bd.get("balance_due") or 0) - (float(bd.get("total_charges") or 0) - float(bd.get("total_paid") or 0))
        )
        < 0.02
    )

    # folio/detail page renders
    detail = _reservation_detail(client, rid)
    assert detail.status_code == 200
    assert f"Reservation #{rid}" in detail.text or str(rid) in detail.text

    # invoice generation (requires unlocked)
    csrf = set_session(client, unlocked=True)
    inv = client.post(f"/reservations/{rid}/invoice", data={"csrf": csrf, "currency": "EGP"}, follow_redirects=False)
    inv_loc = _assert_redirect(inv, "/files/invoice/")
    # serve invoice file (unlocked)
    set_session(client, unlocked=True)
    file_resp = client.get(inv_loc)
    assert file_resp.status_code == 200
    assert "E2E Guest Deposit" in file_resp.text or f"{rid}" in file_resp.text

    # integrity helper for reservation
    chk = services.verify_reservation_financials(rid)
    assert chk.get("ok") is True, chk


# ── 3) Partial payments, refunds, void corrections, idempotency, allocations ─


def test_partial_payments_refunds_voids_idempotency_allocations(client):
    room_id = _room_id()
    ci, co = _future_range(60, nights=1)
    rid = services.create_reservation(
        "E2E Guest Payments",
        "Direct",
        room_id,
        None,
        120.0,
        ci,
        co,
        0.0,
    )

    total = float(services.get_balance_breakdown(rid).get("total_charges") or 0)
    assert total > 0

    first = round(total / 2, 2)
    second = round(total - first, 2)
    alloc1 = json.dumps({"accommodation": first})

    # partial payment with allocation + idempotency key
    csrf = set_session(client, unlocked=False)
    r = client.post(
        f"/reservations/{rid}/pay",
        data={
            "csrf": csrf,
            "amount": f"{first:.2f}",
            "method": "Cash",
            "notes": "part1",
            "payment_type": "Payment",
            "idempotency_key": "e2e-pay-1",
            "allocation_json": alloc1,
            "reference": "ref-1",
        },
        follow_redirects=False,
    )
    _assert_redirect(r, f"/reservations/{rid}")
    paid_after_first = float(services.get_balance_breakdown(rid).get("total_paid") or 0)
    assert paid_after_first == pytest.approx(first, abs=0.02)

    # duplicate submit with same idempotency key should not duplicate amount
    csrf = set_session(client, unlocked=False)
    r2 = client.post(
        f"/reservations/{rid}/pay",
        data={
            "csrf": csrf,
            "amount": f"{first:.2f}",
            "method": "Cash",
            "notes": "part1-dup",
            "payment_type": "Payment",
            "idempotency_key": "e2e-pay-1",
            "allocation_json": alloc1,
            "reference": "ref-1b",
        },
        follow_redirects=False,
    )
    _assert_redirect(r2, f"/reservations/{rid}")
    assert float(services.get_balance_breakdown(rid).get("total_paid") or 0) == pytest.approx(
        paid_after_first, abs=0.02
    )

    # second payment to fully settle
    csrf = set_session(client, unlocked=False)
    client.post(
        f"/reservations/{rid}/pay",
        data={
            "csrf": csrf,
            "amount": f"{second:.2f}",
            "method": "Card",
            "notes": "part2",
            "payment_type": "Payment",
            "idempotency_key": "e2e-pay-2",
            "allocation_json": json.dumps({"accommodation": second}),
        },
        follow_redirects=False,
    )
    bd_settled = services.get_balance_breakdown(rid)
    assert float(bd_settled.get("balance_due") or 0) == pytest.approx(0.0, abs=0.02)
    assert float(bd_settled.get("total_paid") or 0) == pytest.approx(total, abs=0.02)

    pay1 = (
        db.connect()
        .execute(
            "SELECT id, allocation_json, voided_at FROM payments WHERE reservation_id=? AND idempotency_key=?",
            (rid, "e2e-pay-1"),
        )
        .fetchone()
    )
    assert pay1 is not None
    pay1_id = int(pay1["id"])
    assert "accommodation" in (pay1["allocation_json"] or "")

    # refund (requires unlocked + super password)
    csrf = set_session(client, unlocked=True)
    ref = client.post(
        f"/reservations/{rid}/refund",
        data={
            "csrf": csrf,
            "password": "testpass",
            "amount": f"{first:.2f}",
            "method": "Cash",
            "notes": "partial refund",
            "idempotency_key": "e2e-ref-1",
            "allocation_json": json.dumps({"accommodation": first}),
        },
        follow_redirects=False,
    )
    _assert_redirect(ref, f"/reservations/{rid}")
    assert float(services.get_balance_breakdown(rid).get("balance_due") or 0) == pytest.approx(first, abs=0.02)

    refund_row = (
        db.connect()
        .execute(
            "SELECT id, payment_type, voided_at FROM payments WHERE reservation_id=? AND idempotency_key=?",
            (rid, "e2e-ref-1"),
        )
        .fetchone()
    )
    assert refund_row is not None
    refund_id = int(refund_row["id"])
    assert str(refund_row["payment_type"]).lower() == "refund"

    # void refund (correction) -> balance returns to settled
    csrf = set_session(client, unlocked=True)
    vref = client.post(
        f"/reservations/{rid}/payments/{refund_id}/void",
        data={"csrf": csrf, "password": "testpass"},
        follow_redirects=False,
    )
    _assert_redirect(vref, f"/reservations/{rid}")
    assert (
        db.connect().execute("SELECT voided_at FROM payments WHERE id=?", (refund_id,)).fetchone()["voided_at"]
        is not None
    )
    assert float(services.get_balance_breakdown(rid).get("balance_due") or 0) == pytest.approx(0.0, abs=0.02)

    # void an original payment -> creates open balance again
    csrf = set_session(client, unlocked=True)
    vpay = client.post(
        f"/reservations/{rid}/payments/{pay1_id}/void",
        data={"csrf": csrf, "password": "testpass"},
        follow_redirects=False,
    )
    _assert_redirect(vpay, f"/reservations/{rid}")
    assert (
        db.connect().execute("SELECT voided_at FROM payments WHERE id=?", (pay1_id,)).fetchone()["voided_at"]
        is not None
    )
    assert float(services.get_balance_breakdown(rid).get("balance_due") or 0) == pytest.approx(first, abs=0.02)

    # idempotent void feedback/behavior: voiding again is safe
    csrf = set_session(client, unlocked=True)
    vpay2 = client.post(
        f"/reservations/{rid}/payments/{pay1_id}/void",
        data={"csrf": csrf, "password": "testpass"},
        follow_redirects=False,
    )
    _assert_redirect(vpay2, f"/reservations/{rid}")

    chk = services.verify_reservation_financials(rid)
    assert chk.get("ok") is True, chk


# ── 4) Groups and coordinated billing ────────────────────────────────────────


def test_group_coordinated_billing_workflow(client):
    room1, room2 = _room_ids(2)
    ci1, co1 = _future_range(80, nights=1)
    ci2, co2 = _future_range(82, nights=1)

    rid1 = services.create_reservation("Group Member A", "Direct", room1, None, 100.0, ci1, co1, 0.0)
    rid2 = services.create_reservation("Group Member B", "Direct", room2, None, 150.0, ci2, co2, 0.0)

    # create group (requires unlocked + permission in route)
    csrf = set_session(client, unlocked=True)
    r = client.post(
        "/reservations/groups/create",
        data={
            "csrf": csrf,
            "name": "E2E Tour Group",
            "group_type": "group",
            "contact_name": "Coordinator",
            "notes": "e2e",
        },
        follow_redirects=False,
    )
    _assert_redirect(r, "/reservations/groups")
    g = (
        db.connect()
        .execute("SELECT id FROM reservation_groups WHERE name=? ORDER BY id DESC LIMIT 1", ("E2E Tour Group",))
        .fetchone()
    )
    assert g is not None
    gid = int(g["id"])

    # add members
    for rid, role in ((rid1, "leader"), (rid2, "member")):
        csrf = set_session(client, unlocked=True)
        r = client.post(
            f"/reservations/groups/{gid}/add",
            data={"csrf": csrf, "reservation_id": str(rid), "role": role, "notes": ""},
            follow_redirects=False,
        )
        _assert_redirect(r, "/reservations/groups")

    grp = services.get_reservation_group(gid)
    assert grp is not None
    member_ids = {int(m.get("reservation_id")) for m in (grp.get("members") or [])}
    assert member_ids == {rid1, rid2}
    totals = grp.get("totals") or {}
    assert float(totals.get("total_charges") or 0) > 0

    # coordinated payment for member 1
    due1 = float(services.get_balance_breakdown(rid1).get("balance_due") or 0)
    assert due1 > 0
    csrf = set_session(client, unlocked=True)
    pay = client.post(
        f"/reservations/groups/{gid}/payment",
        data={
            "csrf": csrf,
            "reservation_id": str(rid1),
            "amount": f"{due1:.2f}",
            "method": "Cash",
            "payment_type": "Payment",
            "notes": "coordinated",
            "idempotency_key": "e2e-grp-pay-1",
        },
        follow_redirects=False,
    )
    _assert_redirect(pay, "/reservations/groups")
    assert float(services.get_balance_breakdown(rid1).get("balance_due") or 0) == pytest.approx(0.0, abs=0.02)
    assert float(services.get_balance_breakdown(rid2).get("balance_due") or 0) > 0.0

    # group folio/report and page
    set_session(client, unlocked=False)
    page = client.get("/reservations/groups")
    assert page.status_code == 200

    set_session(client, unlocked=False)
    folio = client.get(f"/reservations/groups/{gid}/folio")
    assert folio.status_code == 200
    assert "GROUP FOLIO SUMMARY" in folio.text or "E2E Tour Group" in folio.text

    report = services.report_reservation_group_folio(gid)
    assert "E2E Tour Group" in report
    assert f"Res #{rid1}" in report and f"Res #{rid2}" in report


# ── 5) Housekeeping: blocks, tasks, statuses/ops ─────────────────────────────


def test_housekeeping_blocks_tasks_and_ops(client):
    room1, room2 = _room_ids(2)
    ci, co = _future_range(100, nights=1)

    # room block (requires unlocked) should exclude room from availability
    csrf = set_session(client, unlocked=True)
    blk = client.post(
        "/housekeeping/blocks/create",
        data={
            "csrf": csrf,
            "room_id": str(room1),
            "block_type": "OOO",
            "reason": "maintenance",
            "start_date": ci,
            "end_date": co,
            "notes": "e2e block",
        },
        follow_redirects=False,
    )
    _assert_redirect(blk, "/housekeeping")
    avail = services.get_rooms_available_between(ci, co)
    avail_ids = {int(r["id"]) for r in avail}
    assert room1 not in avail_ids

    block_row = (
        db.connect()
        .execute(
            "SELECT id, status FROM room_blocks WHERE room_id=? AND reason=? ORDER BY id DESC LIMIT 1",
            (room1, "maintenance"),
        )
        .fetchone()
    )
    assert block_row is not None and block_row["status"] == "active"
    block_id = int(block_row["id"])

    # create housekeeping task
    csrf = set_session(client, unlocked=False)
    task = client.post(
        "/housekeeping/tasks/create",
        data={
            "csrf": csrf,
            "room_id": str(room2),
            "task_type": "cleaning",
            "priority": "high",
            "assignee": "hk1",
            "due_at": (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
            "notes": "e2e task",
        },
        follow_redirects=False,
    )
    _assert_redirect(task, "/housekeeping")
    trow = (
        db.connect()
        .execute(
            "SELECT id, status, priority, assignee FROM housekeeping_tasks WHERE room_id=? ORDER BY id DESC LIMIT 1",
            (room2,),
        )
        .fetchone()
    )
    assert (
        trow is not None and trow["status"] in ("pending", "open", "in_progress", "new") or trow["status"] == "pending"
    )
    task_id = int(trow["id"])

    # update task status to in_progress then done (room becomes Clean)
    csrf = set_session(client, unlocked=False)
    client.post(
        f"/housekeeping/tasks/{task_id}/status",
        data={"csrf": csrf, "status": "in_progress", "notes_append": "started"},
        follow_redirects=False,
    )
    csrf = set_session(client, unlocked=False)
    client.post(
        f"/housekeeping/tasks/{task_id}/status",
        data={"csrf": csrf, "status": "done", "notes_append": "finished"},
        follow_redirects=False,
    )
    tdone = db.connect().execute("SELECT status FROM housekeeping_tasks WHERE id=?", (task_id,)).fetchone()
    assert tdone["status"] == "done"
    room2_status = (
        db.connect()
        .execute("SELECT housekeeping_status FROM rooms WHERE id=?", (room2,))
        .fetchone()["housekeeping_status"]
    )
    assert room2_status in ("Clean", "Inspected")

    # edit task details
    csrf = set_session(client, unlocked=False)
    edit = client.post(
        f"/housekeeping/tasks/{task_id}/edit",
        data={
            "csrf": csrf,
            "task_type": "inspection",
            "priority": "normal",
            "assignee": "hk2",
            "due_at": "",
            "notes": "edited",
            "reservation_id": "",
            "status_filter": "",
            "block_status_filter": "active",
        },
        follow_redirects=False,
    )
    _assert_redirect(edit, "/housekeeping")
    tedit = (
        db.connect()
        .execute("SELECT task_type, assignee, notes FROM housekeeping_tasks WHERE id=?", (task_id,))
        .fetchone()
    )
    assert tedit["task_type"] == "inspection"
    assert tedit["assignee"] == "hk2"

    # generate tasks for dirty rooms (service-backed route)
    db.connect().execute("UPDATE rooms SET housekeeping_status='Dirty' WHERE id=?", (room2,))
    db.connect().commit() if hasattr(db.connect(), "commit") else None
    # ensure no open task blocks generation for room2 context; easiest to call route/service in fresh state
    csrf = set_session(client, unlocked=False)
    gen = client.post("/housekeeping/tasks/generate", data={"csrf": csrf, "due_at": ""}, follow_redirects=False)
    _assert_redirect(gen, "/housekeeping")

    # resolve block -> room becomes available again
    csrf = set_session(client, unlocked=True)
    res = client.post(f"/housekeeping/blocks/{block_id}/resolve", data={"csrf": csrf}, follow_redirects=False)
    _assert_redirect(res, "/housekeeping")
    assert (
        db.connect().execute("SELECT status FROM room_blocks WHERE id=?", (block_id,)).fetchone()["status"]
        == "resolved"
    )
    avail2 = services.get_rooms_available_between(ci, co)
    assert room1 in {int(r["id"]) for r in avail2}

    # housekeeping page renders
    set_session(client, unlocked=False)
    page = client.get("/housekeeping")
    assert page.status_code == 200


# ── 6) Cash shifts, night audit, reconciliation ──────────────────────────────


def test_cash_shift_night_audit_and_reconciliation(client):
    # open cash shift
    csrf = set_session(client, unlocked=True)
    r = client.post(
        "/accounting/shifts/open",
        data={"csrf": csrf, "name": "E2E Shift", "opening_float": "100.00", "notes": "open"},
        follow_redirects=False,
    )
    _assert_redirect(r, "/accounting?tab=shifts")

    shift = services.get_open_cash_shift()
    assert shift is not None
    sid = int(shift["id"])

    # cash movement
    csrf = set_session(client, unlocked=True)
    mv = client.post(
        f"/accounting/shifts/{sid}/movement",
        data={
            "csrf": csrf,
            "movement_type": "cash_in",
            "amount": "20.00",
            "method": "Cash",
            "reference": "m1",
            "notes": "topup",
        },
        follow_redirects=False,
    )
    _assert_redirect(mv, f"/accounting?tab=shifts&shift_id={sid}")

    # create reservation and record cash payment (should auto-link to open shift)
    room_id = _room_id()
    ci, co = _future_range(120, nights=1)
    rid = services.create_reservation("E2E NightAudit Guest", "Direct", room_id, None, 80.0, ci, co, 0.0)
    bd = services.get_balance_breakdown(rid)
    amt = round(float(bd.get("total_charges") or 0), 2)
    csrf = set_session(client, unlocked=False)
    client.post(
        f"/reservations/{rid}/pay",
        data={"csrf": csrf, "amount": f"{amt:.2f}", "method": "Cash", "notes": "shift pay", "payment_type": "Payment"},
        follow_redirects=False,
    )

    recon = services.get_cash_shift_reconciliation(sid)
    assert recon is not None
    # linked cash should reflect payment (at least non-zero net) and reconciled expected present
    assert float(recon.get("movements_total") or 0) == pytest.approx(20.0, abs=0.02)
    assert float(recon.get("linked_net") or recon.get("linked_cash_in") or 0) >= 0.0
    assert "reconciled_expected_cash" in recon

    # close shift with counted cash
    expected = float(recon.get("reconciled_expected_cash") or 0)
    counted = round(expected - 1.0, 2)
    csrf = set_session(client, unlocked=True)
    close = client.post(
        f"/accounting/shifts/{sid}/close",
        data={"csrf": csrf, "counted_cash": f"{counted:.2f}", "notes": "close"},
        follow_redirects=False,
    )
    _assert_redirect(close, f"/accounting?tab=shifts&shift_id={sid}")
    closed = (
        db.connect().execute("SELECT status, counted_cash, variance FROM cash_shifts WHERE id=?", (sid,)).fetchone()
    )
    assert closed["status"] == "closed"
    assert float(closed["counted_cash"] or 0) == pytest.approx(counted, abs=0.02)

    # run night audit for a relevant business date (in-house on ci)
    csrf = set_session(client, unlocked=True)
    na = client.post(
        "/accounting/night-audit/run",
        data={"csrf": csrf, "business_date": ci, "notes": "e2e audit"},
        follow_redirects=False,
    )
    _assert_redirect(na, "/accounting?tab=night_audit")

    runs = services.list_night_audit_runs(10)
    assert runs, "expected at least one night audit run"
    latest = runs[0]
    assert latest.get("business_date") == ci
    # summary/exceptions stored as JSON text in DB row
    summary_raw = latest.get("summary_json") or "{}"
    exceptions_raw = latest.get("exceptions_json") or "[]"
    summary = json.loads(summary_raw) if isinstance(summary_raw, str) else (summary_raw or {})
    exceptions = json.loads(exceptions_raw) if isinstance(exceptions_raw, str) else (exceptions_raw or [])
    assert summary.get("business_date") == ci
    assert "exception_count" in summary
    assert isinstance(exceptions, list)

    # accounting pages render
    set_session(client, unlocked=False)
    assert client.get("/accounting?tab=shifts").status_code == 200
    assert client.get("/accounting?tab=night_audit").status_code == 200
    assert client.get(f"/accounting?tab=shifts&shift_id={sid}").status_code == 200


# ── 7) Integrity helpers smoke ───────────────────────────────────────────────


def test_financial_integrity_helpers_smoke(client):
    # basic smoke over fresh/active data state
    report = services.verify_ledger_integrity()
    assert isinstance(report, dict)
    assert "ok" in report and "issues" in report and "stats" in report

    # sample reservation integrity on a created record
    room_id = _room_id()
    ci, co = _future_range(140, nights=1)
    rid = services.create_reservation("E2E Integrity Guest", "Direct", room_id, None, 90.0, ci, co, 20.0)
    chk = services.verify_reservation_financials(rid)
    assert chk.get("ok") is True, chk
    bd = services.get_balance_breakdown(rid)
    assert abs(float(bd.get("balance_due") or 0) - float(services.calculate_guest_balance(rid) or 0)) < 0.02
