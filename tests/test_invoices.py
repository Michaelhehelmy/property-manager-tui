"""Invoice generation — all scenarios, EGP and USD."""
import os
import pytest
from app_core import services
from app_core.db import connect
from conftest import make_reservation


def test_generate_invoice_basic(clean_db):
    rid = make_reservation(rate=100.0, guest="Alice Smith", ci="2026-12-01", co="2026-12-03")
    text = services.generate_invoice_text(rid)
    assert "INVOICE" in text
    assert "Alice Smith" in text
    assert "INV-" in text
    assert "Balance Due" in text
    assert "Gifted with love" not in text  # attribution removed from invoice


def test_generate_invoice_not_found(clean_db):
    with pytest.raises(ValueError, match="not found"):
        services.generate_invoice_text(999)


def test_generate_invoice_contains_tax_breakdown(clean_db):
    rid = make_reservation(rate=100.0)
    text = services.generate_invoice_text(rid)
    assert "Service:" in text
    assert "VAT:" in text
    assert "City tax:" in text
    assert "Accommodation total" in text


def test_generate_invoice_with_pos_items(clean_db):
    rid = make_reservation(rate=100.0)
    services.add_pos_item(rid, "Beer", 2, 50, None)
    text = services.generate_invoice_text(rid)
    assert "Beer" in text
    assert "POS" in text


def test_generate_invoice_with_payments(clean_db):
    rid = make_reservation(rate=100.0, deposit=100.0)
    text = services.generate_invoice_text(rid)
    assert "PAYMENTS" in text
    assert "Deposit" in text
    assert "Total Paid" in text


def test_generate_invoice_usd_view(clean_db):
    rid = make_reservation(rate=100.0)
    usd_text = services.generate_invoice_text(rid, "USD")
    egp_text = services.generate_invoice_text(rid, "EGP")
    assert "USD" in usd_text
    assert "USD view" in usd_text
    assert "USD" not in egp_text


def test_generate_invoice_uses_property_settings(clean_db):
    services.set_setting("property_name", "Test Hotel")
    services.set_setting("property_phone", "+20 100")
    services.set_setting("invoice_footer_text", "Come again!")
    rid = make_reservation()
    text = services.generate_invoice_text(rid)
    assert "Test Hotel" in text
    assert "+20 100" in text
    assert "Come again!" in text


def test_save_invoice_file_creates_file(clean_db):
    rid = make_reservation()
    text = services.generate_invoice_text(rid)
    path = services.save_invoice_file(text, rid, "EGP")
    assert os.path.exists(path)
    assert path.endswith(".txt")
    assert "Res" in path
    with open(path, encoding="utf-8") as f:
        assert "INVOICE" in f.read()


def test_save_invoice_file_usd_suffix(clean_db):
    rid = make_reservation()
    text = services.generate_invoice_text(rid, "USD")
    path = services.save_invoice_file(text, rid, "USD")
    assert "_usd" in path


def test_generate_invoice_reflects_balance(clean_db):
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-03")
    services.record_payment(rid, 100.0, "Cash")
    text = services.generate_invoice_text(rid)
    bal_line = [l for l in text.split("\n") if "Balance Due" in l][0]
    amount = float(bal_line.split(":")[1].strip().split()[0].replace(",", ""))
    assert amount > 0  # 220 - 100 = 120