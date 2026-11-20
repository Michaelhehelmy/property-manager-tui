from __future__ import annotations

from datetime import datetime, timedelta

from app_core import db, services
from tests.web.conftest import get_csrf, set_session


def _pick_room():
    rooms = services.get_all_rooms()
    r = rooms[0]
    rid = r["id"] if isinstance(r, dict) or hasattr(r, "__getitem__") else getattr(r, "id")
    return int(rid)


def test_accounting_page_exception_fallbacks_and_shift_selection(client, monkeypatch):
    import ui.web.routes.accounting as ar

    # force exception paths in accounting() data gathering
    monkeypatch.setattr(ar.services, "list_cash_shifts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ar.services, "get_open_cash_shift", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(
        ar.services, "get_cash_shift_reconciliation", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(ar.services, "list_cash_movements", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(
        ar.services, "list_night_audit_runs", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    r = client.get("/accounting?tab=unknown&shift_id=123")
    assert r.status_code == 200
    assert "Accounting" in r.text


def test_accounting_post_error_paths(client, monkeypatch):
    import ui.web.routes.accounting as ar

    token = set_session(client, unlocked=True)

    # payment error branch
    monkeypatch.setattr(ar.services, "record_payment", lambda *a, **k: (_ for _ in ()).throw(ValueError("pay fail")))
    r = client.post(
        "/accounting/payment",
        data={"csrf": token, "reservation_id": "", "amount": "10", "method": "Cash"},
        follow_redirects=True,
    )
    assert r.status_code == 200 and "pay fail" in r.text

    # shift open/movement/close/night-audit error branches
    monkeypatch.setattr(ar.services, "open_cash_shift", lambda *a, **k: (_ for _ in ()).throw(ValueError("open fail")))
    r = client.post(
        "/accounting/shifts/open", data={"csrf": token, "name": "x", "opening_float": "0"}, follow_redirects=True
    )
    assert r.status_code == 200 and "open fail" in r.text

    monkeypatch.setattr(
        ar.services, "add_cash_movement", lambda *a, **k: (_ for _ in ()).throw(ValueError("move fail"))
    )
    r = client.post(
        "/accounting/shifts/1/movement",
        data={"csrf": token, "movement_type": "drop", "amount": "1"},
        follow_redirects=True,
    )
    assert r.status_code == 200 and "move fail" in r.text

    monkeypatch.setattr(
        ar.services, "close_cash_shift", lambda *a, **k: (_ for _ in ()).throw(ValueError("close fail"))
    )
    r = client.post("/accounting/shifts/1/close", data={"csrf": token, "counted_cash": ""}, follow_redirects=True)
    assert r.status_code == 200 and "close fail" in r.text

    monkeypatch.setattr(ar.services, "run_night_audit", lambda *a, **k: (_ for _ in ()).throw(ValueError("audit fail")))
    r = client.post(
        "/accounting/night-audit/run", data={"csrf": token, "business_date": "", "notes": ""}, follow_redirects=True
    )
    assert r.status_code == 200 and "audit fail" in r.text


def test_housekeeping_edge_branches(client, monkeypatch):
    import ui.web.routes.housekeeping as hr

    # exception fallbacks for task/block listing
    monkeypatch.setattr(
        hr.services, "list_housekeeping_tasks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tasks boom"))
    )
    monkeypatch.setattr(
        hr.services, "list_room_blocks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocks boom"))
    )
    r = client.get("/housekeeping?status=pending&block_status=weird")
    assert r.status_code == 200 and "Housekeeping" in r.text

    # overdue parsing branches (past due, future, unparsable, date-only) + create/update/edit/generate/block flows
    monkeypatch.setattr(hr.services, "list_housekeeping_tasks", services.list_housekeeping_tasks)
    monkeypatch.setattr(hr.services, "list_room_blocks", services.list_room_blocks)

    room_id = _pick_room()
    past = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    t1 = services.create_housekeeping_task(
        room_id, task_type="cleaning", priority="high", assignee="a", due_at=past, notes="past"
    )
    t2 = services.create_housekeeping_task(
        room_id, task_type="inspection", priority="normal", assignee="b", due_at=future, notes="future"
    )
    t3 = services.create_housekeeping_task(
        room_id, task_type="turn", priority="low", assignee="c", due_at=today, notes="dateonly"
    )
    t4 = services.create_housekeeping_task(
        room_id, task_type="other", priority="low", assignee="d", due_at="not-a-date", notes="bad"
    )
    # mark one completed so overdue flag branch for non-open statuses is exercised
    try:
        services.update_housekeeping_task_status(t2, "completed")
    except Exception:
        pass

    r = client.get("/housekeeping?status=&block_status=active")
    assert r.status_code == 200

    token = set_session(client, unlocked=True)

    # edit task (reservation_id empty string branch) and keep filters in redirect
    r = client.post(
        f"/housekeeping/tasks/{t1}/edit",
        data={
            "csrf": token,
            "task_type": "deep_clean",
            "priority": "normal",
            "assignee": "z",
            "due_at": future,
            "notes": "edited",
            "reservation_id": "",
            "status_filter": "pending",
            "block_status_filter": "active",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303 and "/housekeeping?status=pending&block_status=active" in r.headers.get("location", "")

    # generate tasks error branch
    monkeypatch.setattr(
        hr.services,
        "generate_housekeeping_tasks_for_dirty_rooms",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("gen fail")),
    )
    r = client.post("/housekeeping/tasks/generate", data={"csrf": token, "due_at": ""}, follow_redirects=True)
    assert r.status_code == 200 and "gen fail" in r.text

    # create/update task error branches (best-effort)
    monkeypatch.setattr(
        hr.services, "create_housekeeping_task", lambda *a, **k: (_ for _ in ()).throw(ValueError("create fail"))
    )
    r = client.post("/housekeeping/tasks/create", data={"csrf": token, "room_id": str(room_id)}, follow_redirects=True)
    assert r.status_code == 200 and "create fail" in r.text
    monkeypatch.setattr(
        hr.services, "update_housekeeping_task_status", lambda *a, **k: (_ for _ in ()).throw(ValueError("status fail"))
    )
    r = client.post(
        f"/housekeeping/tasks/{t3}/status", data={"csrf": token, "status": "in_progress"}, follow_redirects=True
    )
    assert r.status_code == 200 and "status fail" in r.text

    # block create/resolve error branches
    monkeypatch.setattr(
        hr.services, "create_room_block", lambda *a, **k: (_ for _ in ()).throw(ValueError("block fail"))
    )
    r = client.post(
        "/housekeeping/blocks/create",
        data={
            "csrf": token,
            "room_id": str(room_id),
            "block_type": "OOO",
            "reason": "r",
            "start_date": today,
            "end_date": today,
        },
        follow_redirects=True,
    )
    assert r.status_code == 200 and "block fail" in r.text
    monkeypatch.setattr(
        hr.services, "resolve_room_block", lambda *a, **k: (_ for _ in ()).throw(ValueError("resolve fail"))
    )
    r = client.post("/housekeeping/blocks/1/resolve", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200 and "resolve fail" in r.text
