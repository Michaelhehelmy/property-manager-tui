"""Authentication: password, lockout, session, recovery."""
import pytest
import services
from datetime import timedelta


def test_has_password_after_set(clean_db):
    assert services.has_super_password() is True


def test_password_too_short_raises(clean_db):
    services._session["unlocked"] = False
    # Already has password from fixture — but let's test fresh
    from db import transaction
    with transaction() as c:
        c.execute("DELETE FROM app_metadata WHERE key IN ('super_password_hash','super_password_salt')")
    with pytest.raises(ValueError):
        services.set_super_password("abc")


def test_set_password_twice_raises(clean_db):
    with pytest.raises(ValueError):
        services.set_super_password("another")


def test_verify_correct_password(clean_db):
    assert services.verify_password("testpass") is True


def test_verify_wrong_password(clean_db):
    assert services.verify_password("wrong") is False


def test_verify_empty_password(clean_db):
    assert services.verify_password("") is False


def test_verify_none_password(clean_db):
    assert services.verify_password(None) is False


def test_lockout_after_failures(clean_db):
    services.set_setting("lockout_attempts", "3")
    services.set_setting("lockout_minutes", "5")
    for _ in range(3):
        assert services.verify_password("wrong") is False
    # Now locked out — even correct should fail
    assert services.verify_password("testpass") is False


def test_change_password_success(clean_db):
    services.change_password("testpass", "newpass1", "newpass1")
    assert services.verify_password("newpass1") is True
    assert services.verify_password("testpass") is False


def test_change_password_wrong_current(clean_db):
    with pytest.raises(PermissionError):
        services.change_password("wrong", "newpass1", "newpass1")


def test_change_password_mismatch(clean_db):
    with pytest.raises(ValueError):
        services.change_password("testpass", "newpass1", "newpass2")


def test_change_password_same_as_current(clean_db):
    with pytest.raises(ValueError):
        services.change_password("testpass", "testpass", "testpass")


def test_session_unlock(clean_db):
    assert services.is_session_unlocked() is False
    assert services.unlock_session("testpass") is True
    assert services.is_session_unlocked() is True


def test_session_wrong_password(clean_db):
    assert services.unlock_session("wrong") is False
    assert services.is_session_unlocked() is False


def test_session_timeout(clean_db):
    services.set_setting("session_timeout_minutes", "1")
    services.unlock_session("testpass")
    assert services.is_session_unlocked() is True
    # Fake 2 minutes of inactivity
    services._session["last_activity"] = services._session["last_activity"] - timedelta(minutes=2)
    assert services.is_session_unlocked() is False


def test_recovery_codes_generated(clean_db):
    codes = services.generate_recovery_codes("testpass")
    assert len(codes) == 8
    for code in codes:
        assert services.verify_recovery_code(code) is True


def test_recovery_code_wrong(clean_db):
    services.generate_recovery_codes("testpass")
    assert services.verify_recovery_code("WRONGCODE") is False


def test_recovery_requires_password(clean_db):
    with pytest.raises(PermissionError):
        services.generate_recovery_codes("wrong")