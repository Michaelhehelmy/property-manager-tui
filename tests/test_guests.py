"""Guest CRM: matching, Arabic normalization, merge."""
import pytest
import services
from conftest import get_guest_count


def test_create_new_guest(clean_db):
    gid, name, created = services.find_or_create_guest("Alice Smith")
    assert created is True
    assert name == "Alice Smith"
    assert get_guest_count() == 1


def test_exact_name_matches(clean_db):
    services.find_or_create_guest("Alice Smith")
    gid, name, created = services.find_or_create_guest("Alice Smith")
    assert created is False
    assert get_guest_count() == 1


def test_fuzzy_match_high_confidence(clean_db):
    services.find_or_create_guest("Ahmed Hassan")
    gid, name, created = services.find_or_create_guest("Ahmed Hasan")
    # 0.95 threshold — close enough to auto-link
    assert created is False
    assert get_guest_count() == 1


def test_fuzzy_match_below_threshold(clean_db):
    services.find_or_create_guest("Ahmed Hassan")
    gid, name, created = services.find_or_create_guest("Bob Wilson")
    assert created is True
    assert get_guest_count() == 2


def test_arabic_matching_alef_variants(clean_db):
    services.find_or_create_guest("أحمد علي")
    matches = services.find_guest_matches("احمد علي")
    assert len(matches) > 0
    assert matches[0]["score"] > 0.9


def test_arabic_matching_yaa_alef_maqsura(clean_db):
    services.find_or_create_guest("محمد مصطفى")
    matches = services.find_guest_matches("محمد مصطفي")
    assert len(matches) > 0
    assert matches[0]["score"] > 0.9


def test_arabic_matching_taa_marbuta(clean_db):
    services.find_or_create_guest("فاطمة")
    matches = services.find_guest_matches("فاطمه")
    assert len(matches) > 0
    assert matches[0]["score"] > 0.9


def test_arabic_diacritics_stripped(clean_db):
    services.find_or_create_guest("مُحَمَّد")
    matches = services.find_guest_matches("محمد")
    assert len(matches) > 0
    assert matches[0]["score"] > 0.9


def test_phone_normalization(clean_db):
    services.find_or_create_guest("Test User", phone="+20 100 123 4567")
    matches = services.find_guest_matches("Test User", phone="01001234567")
    assert len(matches) > 0
    assert matches[0]["score"] == 1.0


def test_empty_name_raises(clean_db):
    with pytest.raises(ValueError):
        services.find_or_create_guest("")


def test_get_all_guests(clean_db):
    services.find_or_create_guest("Alice")
    services.find_or_create_guest("Bob")
    guests = services.get_all_guests()
    assert len(guests) == 2


def test_get_all_guests_search(clean_db):
    services.find_or_create_guest("Alice Smith")
    services.find_or_create_guest("Bob Jones")
    guests = services.get_all_guests("Alice")
    assert len(guests) == 1


def test_duplicate_suggestions_bucketing(clean_db):
    services.find_or_create_guest("Ahmed Hassan")
    # Force second one with a slightly different spelling
    from db import transaction
    with transaction() as c:
        c.execute("INSERT INTO guests (canonical_name, phone) VALUES (?, ?)",
                  ("Ahmed Hasan", ""))
    dupes = services.get_duplicate_suggestions()
    assert len(dupes) >= 1


def test_merge_guests(clean_db):
    gid1, _, _ = services.find_or_create_guest("Ahmed Hassan")
    from db import transaction, connect
    with transaction() as c:
        c.execute("INSERT INTO guests (canonical_name, phone) VALUES (?, ?)",
                  ("Ahmed Hasan", ""))
        gid2 = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    # Create a reservation for gid2
    rid = services.create_reservation("Ahmed Hasan", "Direct", 1, None, 100,
                                       "2026-12-01", "2026-12-03", guest_id=gid2)
    moved = services.merge_guests(gid1, gid2, "testpass")
    assert moved == 1
    row = connect().execute("SELECT guest_id FROM reservations WHERE id=?", (rid,)).fetchone()
    assert row["guest_id"] == gid1
    # Loser deleted
    assert connect().execute("SELECT 1 FROM guests WHERE id=?", (gid2,)).fetchone() is None


def test_merge_same_id_raises(clean_db):
    gid, _, _ = services.find_or_create_guest("Alice")
    with pytest.raises(ValueError):
        services.merge_guests(gid, gid, "testpass")


def test_merge_requires_password(clean_db):
    g1, _, _ = services.find_or_create_guest("Alice")
    g2, _, _ = services.find_or_create_guest("Bob")
    with pytest.raises(PermissionError):
        services.merge_guests(g1, g2, "wrong")


def test_update_guest(clean_db):
    gid, _, _ = services.find_or_create_guest("Alice")
    services.update_guest(gid, "Alice Smith", "+201234", "alice@x.com", "VIP", "testpass")
    g = services.get_guest_by_id(gid)
    assert g["name"] == "Alice Smith"
    assert g["phone"] == "+201234"
    assert g["notes"] == "VIP"


def test_update_guest_requires_password(clean_db):
    gid, _, _ = services.find_or_create_guest("Alice")
    with pytest.raises(PermissionError):
        services.update_guest(gid, "Bob", "", "", "", "wrong")