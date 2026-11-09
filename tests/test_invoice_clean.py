"""Verify attribution is gone from invoice but preserved in metadata."""
import pytest
from app_core import services
from conftest import make_reservation


def test_invoice_no_attribution(clean_db):
    """Invoice must NOT show the gift attribution."""
    rid = make_reservation()
    text = services.generate_invoice_text(rid)
    assert "Gifted with love" not in text
    assert "Michael Helmy" not in text
    assert "Sukoon Camp" in text  # Property name still shows


def test_invoice_usd_view_no_attribution(clean_db):
    rid = make_reservation()
    text = services.generate_invoice_text(rid, "USD")
    assert "Gifted with love" not in text
    assert "Michael Helmy" not in text


def test_invoice_has_footer_text(clean_db):
    """The configurable footer still appears."""
    rid = make_reservation()
    text = services.generate_invoice_text(rid)
    assert "Thank you for staying with us!" in text


def test_invoice_custom_footer(clean_db):
    """Manager can set their own footer."""
    services.set_setting("invoice_footer_text", "Visit us again!")
    rid = make_reservation()
    text = services.generate_invoice_text(rid)
    assert "Visit us again!" in text
    assert "Thank you for staying with us!" not in text


def test_attribution_still_verified(clean_db):
    """Attribution in DB is untouched — only the invoice text changed."""
    ok, msg = services.verify_app_attribution()
    assert ok is True
    assert "Michael Helmy" in msg


def test_attribution_constant_unchanged():
    """The GIFT_ATTRIBUTION constant is preserved."""
    assert services.GIFT_ATTRIBUTION == "Gifted with love from Michael Helmy to Sukoon Camp"
    assert services.GIFT_HASH  # hash still computed