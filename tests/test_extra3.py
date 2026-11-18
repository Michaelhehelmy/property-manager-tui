"""Closes remaining reachable branches."""
import pytest
from unittest.mock import patch, MagicMock
from app_core import services
from app_core.db import connect
from conftest import make_reservation


# ═══════════════════════════════════════════════════════════════════════════
# list_printers — every OS branch with mocks
# ═══════════════════════════════════════════════════════════════════════════

def test_list_printers_cups_full(clean_db):
    v_mock = MagicMock(returncode=0, stdout="device for Office: ipp://x\ndevice for R2: ipp://y")
    d_mock = MagicMock(returncode=0, stdout="system default destination: Office")
    with patch("services.subprocess.run", side_effect=[v_mock, d_mock]):
        with patch("services.platform.system", return_value="Linux"):
            r = services.list_printers()
    assert "Office" in r["queues"]
    assert r["default"] == "Office"
    assert r["source"] == "cups"


def test_list_printers_linux_no_cups(clean_db):
    with patch("services.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
        with patch("services.platform.system", return_value="Linux"):
            r = services.list_printers()
    assert r["queues"] == []
    assert r["source"] == "none"


def test_list_printers_windows(clean_db):
    with patch("services.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="P1\nP2\n")):
        with patch("services.platform.system", return_value="Windows"):
            r = services.list_printers()
    assert r["queues"] == ["P1", "P2"]
    assert r["source"] == "winspool"


def test_list_printers_exception(clean_db):
    with patch("services.subprocess.run", side_effect=Exception("boom")):
        with patch("services.platform.system", return_value="Linux"):
            r = services.list_printers()
    assert r["source"] == "none"


# ═══════════════════════════════════════════════════════════════════════════
# print_to_printer — every branch
# ═══════════════════════════════════════════════════════════════════════════

def test_print_file_missing(clean_db):
    ok, msg = services.print_to_printer("/nope.txt")
    assert ok is False


def test_print_cups_success(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch("services.subprocess.run", return_value=MagicMock(returncode=0)):
        ok, msg = services.print_to_printer(str(f), "cups:MyPrinter")
    assert ok is True


def test_print_cups_fail(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch("services.subprocess.run", return_value=MagicMock(returncode=1, stderr="err")):
        ok, msg = services.print_to_printer(str(f), "cups:X")
    assert ok is False


def test_print_win_success(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch.object(services.os, "startfile", create=True):
        ok, msg = services.print_to_printer(str(f), "win:Printer")
    assert ok is True


def test_print_raw_success(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch("services.socket.socket"):
        ok, msg = services.print_to_printer(str(f), "raw:1.2.3.4:9100")
    assert ok is True


def test_print_default_linux(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch("services.platform.system", return_value="Linux"):
        with patch("services.subprocess.run", return_value=MagicMock(returncode=0)):
            ok, _ = services.print_to_printer(str(f), "")
    assert ok is True


def test_print_default_linux_fail(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch("services.platform.system", return_value="Linux"):
        with patch("services.subprocess.run", return_value=MagicMock(returncode=1, stderr="e")):
            ok, _ = services.print_to_printer(str(f), "")
    assert ok is False


def test_print_default_windows(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch("services.platform.system", return_value="Windows"):
        with patch.object(services.os, "startfile", create=True):
            ok, _ = services.print_to_printer(str(f), "")
    assert ok is True


def test_print_unknown_target(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    ok, msg = services.print_to_printer(str(f), "weird:format")
    assert ok is False


def test_print_raw_exception(clean_db, tmp_path):
    f = tmp_path / "x.txt"; f.write_text("x")
    with patch("services._print_raw_socket", side_effect=Exception("fail")):
        ok, msg = services.print_to_printer(str(f), "raw:1.2.3.4:9100")
    assert ok is False


def test_test_print(clean_db):
    with patch("services.print_to_printer", return_value=(True, "ok")):
        ok, _ = services.test_print()
    assert ok is True


# ═══════════════════════════════════════════════════════════════════════════
# backup_now
# ═══════════════════════════════════════════════════════════════════════════

def test_backup_sqlite_valid(clean_db):
    path = services.backup_now()
    import sqlite3
    conn = sqlite3.connect(path)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    conn.close()
    assert "reservations" in tables
    assert "general_ledger" in tables


# ═══════════════════════════════════════════════════════════════════════════
# verify_password — lockout expiry branch
# ═══════════════════════════════════════════════════════════════════════════

def test_verify_lockout_expires(clean_db):
    """Lockout timestamp in the past = not locked."""
    from app_core.db import transaction
    from datetime import datetime, timedelta
    with transaction() as c:
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', ?)", (past,))
    assert services.verify_password("testpass") is True


def test_verify_lockout_bad_format(clean_db):
    """Corrupt lockout_until is ignored."""
    from app_core.db import transaction
    with transaction() as c:
        c.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('lockout_until', 'garbage')")
    assert services.verify_password("testpass") is True


# ═══════════════════════════════════════════════════════════════════════════
# Reservation — remaining branches
# ═══════════════════════════════════════════════════════════════════════════

def test_create_reservation_none_plan(clean_db):
    rid = services.create_reservation("X", "Direct", 1, None, 100, "2026-12-01", "2026-12-03")
    assert rid > 0


def test_create_reservation_non_int_plan(clean_db):
    """rate_plan_id that's not int is coerced to None."""
    rid = services.create_reservation("X", "Direct", 1, "not-int", 100, "2026-12-01", "2026-12-03")
    assert rid > 0


def test_update_status_checkedout_from_reserved_fails(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.update_reservation_status(rid, "Checked Out")


# ═══════════════════════════════════════════════════════════════════════════
# POS — remaining branches
# ═══════════════════════════════════════════════════════════════════════════

def test_add_pos_zero_qty(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 0, 10, None)


def test_add_pos_negative_price(clean_db):
    rid = make_reservation()
    with pytest.raises(ValueError):
        services.add_pos_item(rid, "X", 1, -10, None)


def test_void_nonexistent_pos(clean_db):
    with pytest.raises(ValueError):
        services.void_pos_item(9999, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# Expenses
# ═══════════════════════════════════════════════════════════════════════════

def test_expense_negative_vat(clean_db):
    with pytest.raises(ValueError):
        services.record_operational_expense(1, "X", 100.0, -5.0)


def test_void_expense_nonexistent(clean_db):
    with pytest.raises(ValueError):
        services.void_expense(9999, "testpass")


# ═══════════════════════════════════════════════════════════════════════════
# Settings — export/import edge
# ═══════════════════════════════════════════════════════════════════════════

def test_import_settings_invalid_json(clean_db, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json")
    with pytest.raises(Exception):
        services.import_settings_from_json(str(p), "testpass")


def test_import_settings_missing_file(clean_db):
    with pytest.raises(ValueError):
        services.import_settings_from_json("/nope.json", "testpass")


def test_import_settings_skips_unknown_keys(clean_db, tmp_path):
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"unknown_key": "x", "vat_rate": "0.15"}))
    result = services.import_settings_from_json(str(p), "testpass")
    assert result["ok"] is True
    assert "unknown_key" in result["skipped"]
    assert services.get_setting("vat_rate") == "0.15"


# ═══════════════════════════════════════════════════════════════════════════
# Auth — recovery codes
# ═══════════════════════════════════════════════════════════════════════════

def test_generate_recovery_codes_requires_password(clean_db):
    with pytest.raises(PermissionError):
        services.generate_recovery_codes("wrong")


def test_recovery_code_wrong(clean_db):
    services.generate_recovery_codes("testpass")
    assert services.verify_recovery_code("WRONG") is False


# ═══════════════════════════════════════════════════════════════════════════
# Attribution — tamper detection
# ═══════════════════════════════════════════════════════════════════════════

def test_attribution_tampered(clean_db):
    from app_core.db import transaction
    with transaction() as c:
        c.execute("UPDATE app_metadata SET value='Hacked' WHERE key='gift_attribution'")
    ok, msg = services.verify_app_attribution()
    assert ok is False


# ═══════════════════════════════════════════════════════════════════════════
# Audit log
# ═══════════════════════════════════════════════════════════════════════════

def test_get_audit_log_returns_entries(clean_db):
    make_reservation()
    log = services.get_audit_log(10)
    assert len(log) > 0


# ═══════════════════════════════════════════════════════════════════════════
# Invariant: never assume — cover empty states
# ═══════════════════════════════════════════════════════════════════════════

def test_balance_breakdown_cancelled(clean_db):
    rid = make_reservation()
    services.cancel_reservation(rid, "testpass")
    bd = services.get_balance_breakdown(rid)
    assert bd["balance_due"] == 0


def test_balance_nonexistent(clean_db):
    assert services.get_balance_breakdown(9999) is None


def test_pos_items_empty(clean_db):
    rid = make_reservation()
    assert services.get_pos_items(rid) == []


def test_guest_history_empty(clean_db):
    gid, _, _ = services.find_or_create_guest("X")
    assert services.get_guest_history_by_id(gid) == []