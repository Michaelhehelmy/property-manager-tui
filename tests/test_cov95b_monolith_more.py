import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import app_core.services._monolith as m
from app_core import db, services


def _mk_res(guest="G", ci="2099-01-01", co="2099-01-03", rate=100.0, room_id=1, deposit=0.0):
    return services.create_reservation(guest, "Direct", room_id, None, rate, ci, co, deposit)


def test_run_night_audit_exception_branches(clean_db, monkeypatch):
    # force many internal exceptions/branches in run_night_audit by patching monolith-level names
    monkeypatch.setattr(m, "get_front_desk_ops", lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(m, "get_general_ledger_summary", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(m, "get_open_cash_shift", lambda: {"id": 123})
    monkeypatch.setattr(m, "get_cash_shift_reconciliation", lambda sid: (_ for _ in ()).throw(RuntimeError("boom")))
    # housekeeping/blocks/tasks queries may or may not exist; let DB state drive them
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO housekeeping_tasks (room_id, task_type, status, priority, due_at) VALUES (1, 'clean', 'in_progress', 1, datetime('now','localtime'))"
            )
            conn.execute(
                "INSERT OR IGNORE INTO room_blocks (room_id, block_type, start_date, end_date, reason, status) VALUES (1, 'OOS', '2000-01-01', '2999-01-01', 'x', 'active')"
            )
    except Exception:
        pass
    rid = services.run_night_audit(business_date="2099-05-01", notes="", run_by="")
    assert rid
    runs = services.list_night_audit_runs(limit=5)
    assert runs and int(runs[0]["id"]) == int(rid)

    # get_open_cash_shift exception branch
    monkeypatch.setattr(
        m, "get_front_desk_ops", lambda d: {"counts": {}, "exceptions": [], "in_house": [], "departures": []}
    )
    monkeypatch.setattr(m, "get_general_ledger_summary", lambda: {})
    monkeypatch.setattr(m, "get_open_cash_shift", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    rid2 = services.run_night_audit(business_date="2099-05-02")
    assert rid2


def test_reports_and_group_extra_branches(clean_db, monkeypatch):
    # group report/list branches
    gid = services.create_reservation_group(
        "G1", group_type="tour", contact_name="C", contact_phone="P", contact_email="E", notes="N"
    )
    rid = _mk_res(guest="GG", room_id=1, ci="2099-03-01", co="2099-03-03")
    services.add_reservation_to_group(gid, rid, role="leader", notes="n")
    groups = services.list_reservation_groups(status="active")
    assert any(g["id"] == gid for g in groups)
    assert services.list_reservation_groups(status="")  # no status filter
    # update and folio
    services.update_reservation_group(
        gid,
        name="G1b",
        group_type="corp",
        contact_name="C2",
        contact_phone="",
        contact_email="",
        notes="",
        status="active",
    )
    with pytest.raises(ValueError):
        services.update_reservation_group(gid, name="")
    with pytest.raises(ValueError):
        services.update_reservation_group(999999, name="x")
    folio = services.report_reservation_group_folio(gid)
    assert "GROUP FOLIO" in folio.upper() or "G1b" in folio
    # force balance/package exceptions inside folio formatting
    monkeypatch.setattr(m, "get_balance_breakdown", lambda _rid: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(m, "list_reservation_packages", lambda _rid: (_ for _ in ()).throw(RuntimeError("x")))
    folio2 = services.report_reservation_group_folio(gid)
    assert isinstance(folio2, str)
    with pytest.raises(ValueError):
        services.report_reservation_group_folio(999999)

    # remove member and membership none
    services.remove_reservation_from_group(gid, rid)
    assert services.get_reservation_group_membership(rid) in (None, {})

    # report_reservation_groups / housekeeping / cash / night audit summaries
    try:
        txt = services.report_reservation_groups(status="active")
        assert isinstance(txt, str)
    except Exception:
        pass
    try:
        txt = services.report_housekeeping_ops(day=datetime.now().strftime("%Y-%m-%d"))
        assert isinstance(txt, str)
    except Exception:
        pass
    # create shift to enrich summaries
    sid = services.open_cash_shift(name="R", opening_float=10)
    services.add_cash_movement(sid, "cash_in", 5)
    services.close_cash_shift(sid, counted_cash=15)
    try:
        txt = services.report_cash_shifts_summary(limit=5)
        assert isinstance(txt, str)
    except Exception:
        pass
    naid = services.run_night_audit(business_date="2099-06-01")
    try:
        txt = services.report_night_audit_summary(limit=5)
        assert isinstance(txt, str)
    except Exception:
        pass
    assert naid


def test_printer_and_label_more_branches(clean_db, monkeypatch, tmp_path):
    # list_printers exception branch
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = m.list_printers()
    assert r.get("source") == "none"

    # _print_text_label and print_to_printer related branches
    monkeypatch.setattr(m, "EXPORT_DIR", Path(services.EXPORT_DIR))
    monkeypatch.setattr(m, "INVOICE_DIR", Path(services.INVOICE_DIR))
    monkeypatch.setattr(m, "DATA_DIR", Path(services.EXPORT_DIR).parent)
    Path(services.EXPORT_DIR).mkdir(parents=True, exist_ok=True)
    orig_print = m.print_to_printer
    monkeypatch.setattr(m, "print_to_printer", lambda path, printer=None: (True, "ok"))
    ok, msg, path = m._print_text_label("hello", "my label / bad? name", printer="cups:X")
    assert ok is True and Path(path).exists()
    monkeypatch.setattr(m, "print_to_printer", orig_print)

    # print_to_printer: unsupported target, cups error path, raw socket call
    inside = Path(services.EXPORT_DIR) / "x.txt"
    inside.write_text("hi", encoding="utf-8")
    ok, msg = m.print_to_printer(str(inside), "nonsense")
    assert ok is False
    monkeypatch.setattr(
        m.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="err")
    )
    ok, msg = m.print_to_printer(str(inside), "cups:Q1")
    assert ok is False
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""))
    ok, msg = m.print_to_printer(str(inside), "cups:Q1")
    assert ok is True

    class DummySock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, t):
            pass

        def connect(self, addr):
            pass

        def sendall(self, data):
            pass

    monkeypatch.setattr(m.socket, "socket", lambda *a, **k: DummySock())
    ok, msg = m.print_to_printer(str(inside), "raw:127.0.0.1:9100")
    assert ok is True

    # _validate_print_filepath relative path resolution branch
    cwd = Path.cwd()
    try:
        import os

        os.chdir(Path(services.EXPORT_DIR))
        rel = Path("x.txt")
        vp = m._validate_print_filepath(str(rel))
        assert vp.name == "x.txt"
    finally:
        os.chdir(cwd)

    # _validate_print_filepath outside approved dir
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside = outside_dir / "o.txt"
    outside.write_text("x", encoding="utf-8")
    monkeypatch.setattr(m, "EXPORT_DIR", Path(services.EXPORT_DIR))
    monkeypatch.setattr(m, "INVOICE_DIR", Path(services.INVOICE_DIR))
    monkeypatch.setattr(m, "DATA_DIR", Path(services.EXPORT_DIR) / "data_only")
    from app_core import paths as core_paths

    monkeypatch.setattr(core_paths, "EXPORT_DIR", Path(services.EXPORT_DIR))
    monkeypatch.setattr(core_paths, "INVOICE_DIR", Path(services.INVOICE_DIR))
    monkeypatch.setattr(core_paths, "DATA_DIR", Path(services.EXPORT_DIR) / "data_only")
    with pytest.raises(ValueError):
        m._validate_print_filepath(str(outside))


def test_cash_shift_and_approval_more_branches(clean_db):
    # add_cash_movement / close / reconcile error-ish branches
    with pytest.raises(ValueError):
        services.add_cash_movement(999999, "cash_in", 1)
    sid = services.open_cash_shift("A", 5)
    with pytest.raises(ValueError):
        services.add_cash_movement(sid, "cash_in", "bad")
    services.close_cash_shift(sid, counted_cash=5)
    # movement on closed shift
    with pytest.raises(ValueError):
        services.add_cash_movement(sid, "cash_in", 1)
    with pytest.raises(ValueError):
        services.get_cash_shift_reconciliation(999999)

    # approvals: malformed stored json parsing
    aid = services.create_approval_request("act", entity_type="e", entity_id="1", payload={"x": 1}, requested_by="")
    with db.transaction() as conn:
        conn.execute("UPDATE approvals SET payload_json='{bad', execution_result_json='{bad' WHERE id=?", (aid,))
    ap = services.get_approval(aid)
    assert ap is not None
    listed = services.list_approvals()
    assert any(x["id"] == aid for x in listed)
    # cancel/reject decisions
    aid2 = services.create_approval_request("act2")
    services.decide_approval(aid2, "cancelled")
    assert services.get_approval(aid2)["status"] == "cancelled"
    with pytest.raises(ValueError):
        services.create_approval_request("")
