"""Fixtures: fresh DB + password per test."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import services
import db
from datetime import timedelta


@pytest.fixture
def clean_db(tmp_path, monkeypatch):
    """Fresh database per test — isolated, seeded, password=testpass."""
    try:
        db.close()
    except Exception:
        pass

    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_db)
    monkeypatch.setattr(db, "_conn", None)

    services._session["unlocked"] = False
    services._session["last_activity"] = None
    services.set_language("en")

    services.init_db()
    services.set_super_password("testpass")

    yield

    try:
        db.close()
    except Exception:
        pass


@pytest.fixture
def unlocked(clean_db):
    """Clean DB + Settings session unlocked."""
    services.unlock_session("testpass")
    yield
    services.lock_session()


# ─────────────────────────────────────────────────────────────────────────
# Helper constructors
# ─────────────────────────────────────────────────────────────────────────

def make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-03", room_id=1,
                     guest="Test Guest", deposit=0.0, source="Direct"):
    """Create a reservation with sensible defaults. Room 1 = seed room 101."""
    return services.create_reservation(guest, source, room_id, None, rate, ci, co, deposit)


def count_ledger(entry_type=None, ref_type=None):
    from db import connect
    q = "SELECT COUNT(*) FROM general_ledger WHERE 1=1"
    p = []
    if entry_type:
        q += " AND entry_type=?"
        p.append(entry_type)
    if ref_type:
        q += " AND ref_type=?"
        p.append(ref_type)
    return connect().execute(q, p).fetchone()[0]


def sum_ledger(column, entry_type=None):
    from db import connect
    q = f"SELECT COALESCE(SUM({column}), 0) FROM general_ledger"
    p = []
    if entry_type:
        q += " WHERE entry_type=?"
        p.append(entry_type)
    return connect().execute(q, p).fetchone()[0]


def get_guest_count():
    from db import connect
    return connect().execute("SELECT COUNT(*) FROM guests").fetchone()[0]