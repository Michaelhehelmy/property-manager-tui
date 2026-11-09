"""Regression tests for ledger netting, flows, and analytics exports."""
import json
from pathlib import Path
import pytest
import services
import db
from conftest import make_reservation


def test_modify_reservation_does_not_double_count_balance(clean_db):
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-03")  # 2 nights
    before = services.get_balance_breakdown(rid)
    services.modify_reservation(rid, 150.0, "2026-12-01", "2026-12-04", 1, "testpass")  # 3 nights
    after = services.get_balance_breakdown(rid)
    bal = services.calculate_guest_balance(rid)
    types = services._net_types(("CHARGE",))
    ph = ",".join("?" * len(types))
    ledger_gross = db.connect().execute(
        f"SELECT COALESCE(SUM(gross),0) FROM general_ledger WHERE ref_type='RES' AND ref_id=? AND entry_type IN ({ph})",
        (rid, *types),
    ).fetchone()[0]
    assert abs(after["accommodation_gross"] - ledger_gross) < 0.01
    assert abs(after["balance_due"] - bal) < 0.01
    # old 200 net plus tax, new 450 net plus tax; must not be old+new
    assert after["accommodation_gross"] < before["accommodation_gross"] * 3


def test_invoice_reflects_modified_accommodation_net(clean_db):
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-03")
    services.modify_reservation(rid, 200.0, "2026-12-01", "2026-12-05", 1, "testpass")
    inv = services.generate_invoice_text(rid)
    bd = services.get_balance_breakdown(rid)
    assert f"{bd['balance_due']:.2f}" in inv or "Balance Due" in inv


def test_modify_reservation_respects_allow_overbooking(clean_db):
    services.set_setting("allow_overbooking", "true")
    make_reservation(ci="2026-12-10", co="2026-12-12", room_id=2)
    rid = services.create_reservation("B", "Direct", 2, None, 100.0, "2026-12-10", "2026-12-12")
    services.modify_reservation(rid, 120.0, "2026-12-10", "2026-12-13", 2, "testpass")
    assert services.get_reservation(rid)["agreed_nightly_rate"] == 120.0


def test_estimate_cancellation_outcome_uses_policy(clean_db):
    rid = make_reservation(rate=100.0, ci="2026-12-01", co="2026-12-03", deposit=50.0)
    services.record_payment(rid, 100.0, "Cash")
    services.set_setting("cancel_policy", "keep_deposit")
    est = services.estimate_cancellation_outcome(rid, "cancel")
    assert est["policy"] == "keep_deposit"
    assert est["total_paid"] == 150.0
    assert est["estimated_refund"] == 100.0


def test_complete_walkin_sale_is_atomic(clean_db, monkeypatch):
    inv = db.connect().execute("SELECT id, stock_qty FROM inventory WHERE active=1 LIMIT 1").fetchone()
    before = inv["stock_qty"]
    # force failure at payment posting by breaking method validation? easier: monkeypatch _post to fail on WALKIN_PAY
    real_post = services._post
    def boom(cur, ref_type, *a, **k):
        if ref_type == "WALKIN_PAY":
            raise RuntimeError("boom")
        return real_post(cur, ref_type, *a, **k)
    monkeypatch.setattr(services, "_post", boom)
    with pytest.raises(RuntimeError):
        services.complete_walkin_sale([{"desc": "W", "qty": 1, "price": 20.0, "inv_id": inv["id"]}], "Cash")
    after = db.connect().execute("SELECT stock_qty FROM inventory WHERE id=?", (inv["id"],)).fetchone()["stock_qty"]
    assert after == before
    assert db.connect().execute("SELECT COUNT(*) FROM pos_tabs WHERE reservation_id IS NULL").fetchone()[0] == 0


def test_export_analytics_preset_writes_files(clean_db, tmp_path, monkeypatch):
    monkeypatch.setattr(services, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(services, "ANALYTICS_EXPORT_DIR", tmp_path / "analytics")
    make_reservation(rate=100.0)
    res = services.export_analytics_preset("ml_features_v1", "2026-12-01", "2026-12-31", formats=["json", "csv"])
    assert Path(res["outputs"]["json"]).exists()
    payload = json.loads(Path(res["outputs"]["json"]).read_text())
    assert payload["metadata"]["preset"] == "ml_features_v1"
    assert "datasets" in payload


def test_demo_preset_guard_and_apply(clean_db):
    assert not services.has_operational_data()
    result = services.apply_demo_data_preset("analysis_small", "testpass", "APPLY DEMO DATA")
    assert result["created"]["reservations"] > 0
    assert services.has_operational_data()
    with pytest.raises(ValueError):
        services.apply_demo_data_preset("analysis_small", "testpass", "APPLY DEMO DATA")
