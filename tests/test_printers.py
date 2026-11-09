"""Printers — all OS branches with mocks."""
import pytest
from unittest.mock import MagicMock, patch
from app_core import services


# ── list_printers ────────────────────────────────────────────────────────

def test_list_printers_cups(clean_db):
    v_mock = MagicMock(returncode=0, stdout="device for Office: ipp://x\ndevice for Receipt: ipp://y")
    d_mock = MagicMock(returncode=0, stdout="system default destination: Office")
    with patch("services.subprocess.run", side_effect=[v_mock, d_mock]):
        with patch("services.platform.system", return_value="Linux"):
            r = services.list_printers()
    assert "Office" in r["queues"]
    assert "Receipt" in r["queues"]
    assert r["default"] == "Office"
    assert r["source"] == "cups"


def test_list_printers_cups_no_default(clean_db):
    v_mock = MagicMock(returncode=0, stdout="device for Office: ipp://x")
    d_mock = MagicMock(returncode=1, stdout="")
    with patch("services.subprocess.run", side_effect=[v_mock, d_mock]):
        with patch("services.platform.system", return_value="Linux"):
            r = services.list_printers()
    assert r["default"] == ""


def test_list_printers_linux_no_cups(clean_db):
    with patch("services.subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
        with patch("services.platform.system", return_value="Linux"):
            r = services.list_printers()
    assert r["queues"] == []
    assert r["source"] == "none"


def test_list_printers_windows(clean_db):
    mock = MagicMock(returncode=0, stdout="Printer1\nPrinter2\n")
    with patch("services.subprocess.run", return_value=mock):
        with patch("services.platform.system", return_value="Windows"):
            r = services.list_printers()
    assert r["queues"] == ["Printer1", "Printer2"]
    assert r["source"] == "winspool"


def test_list_printers_exception(clean_db):
    with patch("services.subprocess.run", side_effect=Exception("boom")):
        with patch("services.platform.system", return_value="Linux"):
            r = services.list_printers()
    assert r["source"] == "none"


# ── print_to_printer ─────────────────────────────────────────────────────

def test_print_file_not_found(clean_db):
    ok, msg = services.print_to_printer("/nonexistent/file.txt")
    assert ok is False
    assert "not found" in msg.lower()


def test_print_cups_success(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    with patch("services.subprocess.run", return_value=MagicMock(returncode=0)):
        ok, msg = services.print_to_printer(str(f), "cups:MyPrinter")
    assert ok is True
    assert "MyPrinter" in msg


def test_print_cups_failure(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    with patch("services.subprocess.run", return_value=MagicMock(returncode=1, stderr="error")):
        ok, msg = services.print_to_printer(str(f), "cups:MyPrinter")
    assert ok is False
    assert "CUPS" in msg


def test_print_win_success(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    with patch.object(services.os, "startfile", create=True) as mock:
        ok, msg = services.print_to_printer(str(f), "win:Printer")
    assert ok is True
    mock.assert_called_once_with(str(f), "printto", "Printer")


def test_print_raw_success(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    with patch("services.socket.socket") as mock_socket:
        ok, msg = services.print_to_printer(str(f), "raw:1.2.3.4:9100")
    assert ok is True
    assert "1.2.3.4" in msg


def test_print_default_linux_success(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    with patch("services.platform.system", return_value="Linux"):
        with patch("services.subprocess.run", return_value=MagicMock(returncode=0)):
            ok, msg = services.print_to_printer(str(f), "")
    assert ok is True


def test_print_default_linux_failure(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    with patch("services.platform.system", return_value="Linux"):
        with patch("services.subprocess.run", return_value=MagicMock(returncode=1, stderr="err")):
            ok, msg = services.print_to_printer(str(f), "")
    assert ok is False


def test_print_default_windows(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    with patch("services.platform.system", return_value="Windows"):
        with patch.object(services.os, "startfile", create=True) as mock:
            ok, msg = services.print_to_printer(str(f), "")
    assert ok is True
    mock.assert_called_once_with(str(f), "print")


def test_print_invalid_target(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    ok, msg = services.print_to_printer(str(f), "nonsense")
    assert ok is False
    assert "Unknown" in msg


def test_print_raw_exception(clean_db, tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("x")
    with patch("services._print_raw_socket", side_effect=Exception("net fail")):
        ok, msg = services.print_to_printer(str(f), "raw:1.2.3.4:9100")
    assert ok is False
    assert "net fail" in msg


def test_test_print(clean_db):
    with patch("services.print_to_printer", return_value=(True, "OK")):
        ok, msg = services.test_print()
    assert ok is True
    assert msg == "OK"