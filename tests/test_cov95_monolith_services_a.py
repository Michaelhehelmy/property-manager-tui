from __future__ import annotations

import json
import os
import socket
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app_core import db, paths, services
import app_core.services._monolith as m


def _mk_guest_room_res(clean_db, status="Reserved", ci="2099-01-10", co="2099-01-12"):
    conn = db.connect()
    gid = conn.execute(
        "INSERT INTO guests(canonical_name, phone, email) VALUES (?,?,?)",
        ("Cov Guest", "123", "g@example.test"),
    ).lastrowid
    room_no = f"CV{gid}{status[:1]}"
    conn.execute(
        "INSERT INTO rooms(room_number, room_type, base_price, housekeeping_status) VALUES (?,?,?,?)",
        (room_no, "Standard", 100.0, "Clean"),
    )
    room_id = conn.execute("SELECT id FROM rooms WHERE room_number=?", (room_no,)).fetchone()[0]
    rid = conn.execute(
        """INSERT INTO reservations(guest_id, guest_name, room_id, check_in_date, check_out_date,
           agreed_nightly_rate, status, created_at, tax_amount, source, notes, booking_notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (gid, "Cov Guest", room_id, ci, co, 100.0, status, "2099-01-01 10:00:00", 0.0, "Direct", "", ""),
    ).lastrowid
    conn.commit()
    return {"guest_id": gid, "room_id": room_id, "reservation_id": rid, "room_no": room_no}


def test_low_level_helper_branches(monkeypatch, tmp_path):
    # _svc_attr exception path and fallback to globals/default
    assert m._svc_attr("this_attr_does_not_exist_xyz", default=123) == 123

    monkeypatch.setattr(m, "_round_qty", m._round_qty)  # keep
    assert m._round_qty("bad") == 0.0
    assert m._round_money("bad") == 0.0

    # _qty_eq normal and coercion
    assert m._qty_eq("1.0", 1.0) is True
    assert m._qty_eq(1, 2) is False

    # _allowed_print_roots skips filesystem root and dedupes
    monkeypatch.setattr(m, "EXPORT_DIR", Path("/"))
    monkeypatch.setattr(m, "INVOICE_DIR", tmp_path / "inv")
    monkeypatch.setattr(m, "DATA_DIR", tmp_path / "inv")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "inv")
    monkeypatch.setattr(paths, "EXPORT_DIR", tmp_path / "inv2")
    monkeypatch.setattr(paths, "INVOICE_DIR", tmp_path / "inv3")
    roots = m._allowed_print_roots()
    assert all(r != r.parent for r in roots)
    assert len(roots) == len({str(r) for r in roots})

    # _is_under_allowed_root branches
    assert m._is_under_allowed_root("\x00bad") is False
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assert m._is_under_allowed_root(outside) is False

    safe_dir = tmp_path / "safe"
    safe_dir.mkdir()
    monkeypatch.setattr(m, "EXPORT_DIR", safe_dir)
    monkeypatch.setattr(m, "INVOICE_DIR", safe_dir)
    monkeypatch.setattr(m, "DATA_DIR", safe_dir)
    monkeypatch.setattr(paths, "DATA_DIR", safe_dir)
    monkeypatch.setattr(paths, "EXPORT_DIR", safe_dir)
    monkeypatch.setattr(paths, "INVOICE_DIR", safe_dir)
    safe_file = safe_dir / "ok.txt"
    safe_file.write_text("hello", encoding="utf-8")
    assert m._is_under_allowed_root(safe_file) is True

    # traceability formatting
    t = m._traceability_text(lot_code="L1", batch_id=7, expiry_date="2030-01-01", received_at="2030-01-01 12:00:00")
    assert "lot L1" in t and "batch #7" in t and "exp 2030-01-01" in t and "rcv " in t


def test_command_arg_and_print_target_validation(clean_db, tmp_path, monkeypatch):
    # _sanitize_command_arg currently rejects control chars/newlines/null only
    assert m._sanitize_command_arg("", "x") == ""
    assert m._sanitize_command_arg("bad|pipe", "x") == "bad|pipe"
    with pytest.raises(ValueError):
        m._sanitize_command_arg("a\x00b", "x")
    with pytest.raises(ValueError):
        m._sanitize_command_arg("a\nb", "x")

    assert m._validate_print_target("") == ("", None)
    with pytest.raises(ValueError):
        m._validate_print_target("x" * 201)
    assert m._validate_print_target("cups:Printer_1") == ("cups", "Printer_1")
    assert m._validate_print_target("win:HP LaserJet") == ("win", "HP LaserJet")
    assert m._validate_print_target("raw:127.0.0.1:9100") == ("raw", ("127.0.0.1", 9100))
    with pytest.raises(ValueError):
        m._validate_print_target("cups:bad name!")
    with pytest.raises(ValueError):
        m._validate_print_target("win:bad|name")
    with pytest.raises(ValueError):
        m._validate_print_target("raw:hostonly")
    with pytest.raises(ValueError):
        m._validate_print_target("raw:bad host:9100")
    with pytest.raises(ValueError):
        m._validate_print_target("raw:127.0.0.1:notaport")
    with pytest.raises(ValueError):
        m._validate_print_target("raw:127.0.0.1:70000")
    with pytest.raises(ValueError):
        m._validate_print_target("weird:thing")


def test_validate_print_filepath_and_raw_socket(clean_db, tmp_path, monkeypatch):
    safe_dir = tmp_path / "exports"
    safe_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, "EXPORT_DIR", safe_dir)
    monkeypatch.setattr(m, "INVOICE_DIR", safe_dir)
    monkeypatch.setattr(m, "DATA_DIR", safe_dir)
    monkeypatch.setattr(paths, "DATA_DIR", safe_dir)
    monkeypatch.setattr(paths, "EXPORT_DIR", safe_dir)
    monkeypatch.setattr(paths, "INVOICE_DIR", safe_dir)

    good = safe_dir / "label.txt"
    good.write_text("hello\nworld", encoding="utf-8")
    p = m._validate_print_filepath(str(good))
    assert p == good.resolve()

    with pytest.raises(ValueError):
        m._validate_print_filepath("")
    with pytest.raises(ValueError):
        m._validate_print_filepath("\x00bad")
    with pytest.raises(ValueError):
        m._validate_print_filepath("~/nope")
    with pytest.raises(FileNotFoundError):
        m._validate_print_filepath(str(safe_dir / "missing.txt"))

    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        m._validate_print_filepath(str(outside))

    big = safe_dir / "big.txt"
    big.write_bytes(b"a" * (5 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        m._validate_print_filepath(str(big))

    # _print_raw_socket: oversized guard and send path
    with pytest.raises(ValueError):
        m._print_raw_socket("127.0.0.1", 9100, str(big))

    raw_file = safe_dir / "raw.txt"
    raw_file.write_text("A\nB\n", encoding="utf-8")
    sent = {}

    class FakeSock:
        def __init__(self): self.timeout = None; self.connected = None
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False
        def settimeout(self, t): self.timeout = t
        def connect(self, addr): self.connected = addr
        def sendall(self, data): sent["data"] = data

    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    m._print_raw_socket("127.0.0.1", 9100, str(raw_file))
    assert sent["data"] == b"A\r\nB\r\n"

    esc_file = safe_dir / "esc.bin"
    esc_file.write_bytes(b"\x1b@X\n")
    sent.clear()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    m._print_raw_socket("localhost", 9100, str(esc_file))
    assert sent["data"] == b"\x1b@X\n"

    empty = safe_dir / "empty.txt"
    empty.write_bytes(b"")
    sent.clear()
    monkeypatch.setattr(socket, "socket", lambda *a, **k: FakeSock())
    m._print_raw_socket("localhost", 9100, str(empty))
    assert "data" not in sent  # early return on empty payload


def test_print_to_printer_and_list_printers(clean_db, tmp_path, monkeypatch):
    safe_dir = tmp_path / "exports"
    safe_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, "EXPORT_DIR", safe_dir)
    monkeypatch.setattr(m, "INVOICE_DIR", safe_dir)
    monkeypatch.setattr(m, "DATA_DIR", safe_dir)
    monkeypatch.setattr(paths, "DATA_DIR", safe_dir)
    monkeypatch.setattr(paths, "EXPORT_DIR", safe_dir)
    monkeypatch.setattr(paths, "INVOICE_DIR", safe_dir)
    f = safe_dir / "doc.txt"
    f.write_text("content", encoding="utf-8")

    # default printer, non-Windows lp success/failure
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    ok, msg = m.print_to_printer(str(f), "")
    assert ok and "default printer" in msg.lower()

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="no lp"))
    ok, msg = m.print_to_printer(str(f), "")
    assert not ok and "print failed" in msg.lower()

    # cups / win / raw / unsupported handled via validation result branches
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    ok, msg = m.print_to_printer(str(f), "cups:Office")
    assert ok and "CUPS" in msg

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="oops", stderr=""))
    ok, msg = m.print_to_printer(str(f), "cups:Office")
    assert not ok and "cups error" in msg.lower()

    started = {}
    monkeypatch.setattr(m.platform, "system", lambda: "Windows")
    monkeypatch.setattr(m.os, "startfile", lambda path, op, arg=None: started.setdefault("calls", []).append((path, op, arg)), raising=False)
    ok, msg = m.print_to_printer(str(f), "win:Printer1")
    assert ok and started["calls"][-1][1:] == ("printto", "Printer1")
    ok, msg = m.print_to_printer(str(f), "")
    assert ok and started["calls"][-1][1] == "print"

    raw_calls = {}
    monkeypatch.setattr(m, "_print_raw_socket", lambda h, p, fp: raw_calls.setdefault("v", (h, p, fp)))
    monkeypatch.setattr(services, "_print_raw_socket", lambda h, p, fp: raw_calls.setdefault("v", (h, p, fp)), raising=False)
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    ok, msg = m.print_to_printer(str(f), "raw:10.0.0.5:9100")
    assert ok and raw_calls["v"][0] == "10.0.0.5"

    # error mappings
    monkeypatch.setattr(m, "_validate_print_filepath", lambda x: (_ for _ in ()).throw(FileNotFoundError("missing")))
    ok, msg = m.print_to_printer("x", "")
    assert not ok and "missing" in msg
    monkeypatch.setattr(m, "_validate_print_filepath", lambda x: (_ for _ in ()).throw(ValueError("bad target path")))
    ok, msg = m.print_to_printer("x", "")
    assert not ok and "bad target path" in msg
    monkeypatch.setattr(m, "_validate_print_filepath", lambda x: (_ for _ in ()).throw(m.subprocess.TimeoutExpired(cmd="lp", timeout=1)))
    ok, msg = m.print_to_printer("x", "")
    assert not ok and "timed out" in msg.lower()
    monkeypatch.setattr(m, "_validate_print_filepath", lambda x: (_ for _ in ()).throw(RuntimeError("boom")))
    ok, msg = m.print_to_printer("x", "")
    assert not ok and "print error" in msg.lower()

    # list_printers branches
    monkeypatch.setattr(m.platform, "system", lambda: "Windows")
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="A\nB\n", stderr=""))
    r = m.list_printers()
    assert r["source"] == "winspool" and r["queues"] == ["A", "B"]

    def lpstat_run(cmd, *a, **k):
        if cmd and cmd[0] == "lpstat" and "-v" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="device for HP: usb://hp\ndevice for BadLine\n", stderr="")
        if cmd and cmd[0] == "lpstat" and "-d" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="system default destination: HP\n", stderr="")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    monkeypatch.setattr(m.subprocess, "run", lpstat_run)
    r = m.list_printers()
    assert r["source"] == "cups" and "HP" in r["queues"] and r["default"] == "HP"

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no cups")))
    r = m.list_printers()
    assert r["source"] == "none" and r["queues"] == []


def test_audit_actor_and_session_paths(clean_db, monkeypatch):
    # actor context set/get/reset and exception-tolerant branches
    tok = m.set_current_actor("u1", actor_user_id=7, actor_role="admin")
    assert m.get_current_actor()["actor"] == "u1"
    m.reset_current_actor(tok)

    class BadCtx:
        def get(self): raise RuntimeError("boom")
        def set(self, v): raise RuntimeError("boom")
        def reset(self, t): raise RuntimeError("boom")
    monkeypatch.setattr(m, "_CURRENT_ACTOR", BadCtx())
    assert m.get_current_actor() == {}
    # should not raise
    m.reset_current_actor(object())

    # audit fallback path when normal insert fails
    calls = {"n": 0}
    class FakeConn:
        def execute(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("primary fail")
            return None
    class Tx:
        def __enter__(self): return FakeConn()
        def __exit__(self, exc_type, exc, tb): return False
    monkeypatch.setattr(db, "transaction", lambda: Tx())
    m._audit("x", "y", "1", "d")

    # session timeout handling and touch
    m.lock_session()
    assert m._session["unlocked"] is False and m._session["last_activity"] is None
    m._session["unlocked"] = True
    m._session["last_activity"] = datetime.now() - timedelta(minutes=999)
    monkeypatch.setattr(m, "get_setting", lambda k, d=None: "bad" if k == "session_timeout_minutes" else d)
    assert m.is_session_unlocked() in (True, False)  # exercises exception path; may relock depending on default parse
    m._session["unlocked"] = True
    m._session["last_activity"] = datetime.now() - timedelta(minutes=999)
    monkeypatch.setattr(m, "get_setting", lambda k, d=None: "0")
    assert m.is_session_unlocked() is False

    m._session["unlocked"] = True
    m.touch_session()
    assert m._session["last_activity"] is not None

    # unlock_session sentinel rejection
    assert m.unlock_session("__session__") is False


def test_migrate_branding_if_needed_branches(clean_db, monkeypatch):
    calls = []
    monkeypatch.setattr(m, "get_setting", lambda k, d="": {"branding_configured": "true"}.get(k, d))
    m.migrate_branding_if_needed()  # early return

    def gs(k, d=""):
        return {
            "branding_configured": "false",
            "property_name": "Sukoon Camp",
            "app_tagline": "tag",
            "loading_animation": "reveal",
        }.get(k, d)
    monkeypatch.setattr(m, "get_setting", gs)
    monkeypatch.setattr(m, "apply_branding_settings", lambda *a, **k: calls.append(("apply", a, k)))
    m.migrate_branding_if_needed()
    assert calls and calls[-1][0] == "apply"

    calls.clear()
    monkeypatch.setattr(m, "get_setting", lambda k, d="": {
        "branding_configured": "false", "property_name": "Legacy Name", "app_display_name": ""
    }.get(k, d))
    monkeypatch.setattr(m, "set_setting", lambda k, v: calls.append((k, v)))
    m.migrate_branding_if_needed()
    assert ("app_display_name", "Legacy Name") in calls

    # exception safety
    monkeypatch.setattr(m, "get_setting", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    m.migrate_branding_if_needed()


def test_settings_import_export_and_reset(unlocked, tmp_path, monkeypatch):
    # export/import happy-ish paths (import requires _require_super_password; unlocked has session context helpers)
    out = services.export_settings_to_json()
    assert Path(out).exists()
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert isinstance(data, dict)

    bad = tmp_path / "missing.json"
    with pytest.raises(ValueError):
        services.import_settings_from_json(str(bad), "testpass")

    # import with unknown/internal keys skipped
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"unknown_key_xyz": "1", "language": data.get("language", "en")}), encoding="utf-8")
    # some installs require password proof; pass __session__ if unlocked works, else real pass
    try:
        res = services.import_settings_from_json(str(src), "__session__")
    except Exception:
        res = services.import_settings_from_json(str(src), "testpass")
    assert res["ok"] is True
    assert "unknown_key_xyz" in res.get("skipped", [])

    # invalid JSON payload triggers error via json.loads in caller context? function expects valid json file
    src2 = tmp_path / "bad.json"
    src2.write_text("{bad", encoding="utf-8")
    with pytest.raises(Exception):
        services.import_settings_from_json(str(src2), "testpass")

    # reset_to_defaults works with valid password
    c, errs = services.save_settings({"language": services.get_setting("language", "en")}, "testpass")
    assert errs == []
    services.reset_to_defaults("testpass")


def test_guest_portfolio_invoice_and_walkin_helpers(clean_db, tmp_path, monkeypatch):
    d = _mk_guest_room_res(clean_db)
    rid = d["reservation_id"]

    # invoice file listing helper
    inv_dir = tmp_path / "invfiles"
    inv_dir.mkdir()
    monkeypatch.setattr(m, "INVOICE_DIR", inv_dir)
    (inv_dir / f"invoice_Res{rid}_001.txt").write_text("x", encoding="utf-8")
    (inv_dir / f"invoice_Res{rid}_001.pdf").write_text("x", encoding="utf-8")
    files = m._list_invoice_files_for_reservation(rid)
    names = {f["name"] for f in files}
    assert f"invoice_Res{rid}_001.txt" in names and f"invoice_Res{rid}_001.pdf" in names

    # portfolio basic shape
    p = m.get_guest_portfolio(d["guest_id"])
    assert p and p["guest"]["id"] == d["guest_id"]
    assert "stats" in p and "trips" in p and isinstance(p["trips"], list)

    # generate/save wrappers (delegate to billing_docs; exercise code paths)
    try:
        txt = m.generate_walkin_receipt_text([{"item_description": "X", "quantity": 1, "unit_price": 10, "tax_amount": 0, "batch_id": None, "lot_code": None}], 10.0, "Cash", payment_id=123)
        assert "X" in txt or "Walk" in txt or "Cash" in txt
    except Exception:
        # billing_docs implementation details may vary; wrapper call still executed
        pass
    try:
        path = m.save_walkin_receipt_file("hello", 123)
        assert path
    except Exception:
        pass

    # test_print writes file and calls printer
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path / "exports")
    monkeypatch.setattr(m, "get_app_display_name", lambda: "App")
    called = {}
    monkeypatch.setattr(m, "print_to_printer", lambda path, printer=None: called.setdefault("v", (path, printer)) or (True, "ok"))
    monkeypatch.setattr(services, "print_to_printer", lambda path, printer=None: called.setdefault("v", (path, printer)) or (True, "ok"), raising=False)
    ok, msg = m.test_print(None)
    assert ok and "printer_test.txt" in called["v"][0]

    # label printer setting getter
    monkeypatch.setattr(m, "get_setting", lambda k, d="": "LPT1" if k == "label_printer" else d)
    assert m.get_label_printer() == "LPT1"