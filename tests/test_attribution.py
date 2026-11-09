"""Gift attribution: immutable, tamper-detected."""
import pytest
import services
from db import connect, transaction


def test_attribution_valid(clean_db):
    valid, msg = services.verify_app_attribution()
    assert valid is True
    assert msg == services.GIFT_ATTRIBUTION


def test_attribution_tampered_value(clean_db):
    with transaction() as c:
        c.execute("UPDATE app_metadata SET value='Fake' WHERE key='gift_attribution'")
    valid, msg = services.verify_app_attribution()
    assert valid is False
    assert "tampered" in msg.lower()


def test_attribution_tampered_hash(clean_db):
    with transaction() as c:
        c.execute("UPDATE app_metadata SET hash_signature='bogus' WHERE key='gift_attribution'")
    valid, msg = services.verify_app_attribution()
    assert valid is False


def test_attribution_missing(clean_db):
    with transaction() as c:
        c.execute("DELETE FROM app_metadata WHERE key='gift_attribution'")
    valid, msg = services.verify_app_attribution()
    assert valid is False