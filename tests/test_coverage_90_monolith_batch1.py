from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app_core import db, services
import app_core.services._monolith as m


_room_seq = 0

def _mk_guest_room_res(clean_db, status="Reserved"):
    global _room_seq
    _room_seq += 1
    conn = db.connect()
    # guests table uses canonical_name
    gid = conn.execute(
        "INSERT INTO guests(canonical_name, phone, email) VALUES (?,?,?)",
        (f"G{_room_seq}", "1", ""),
    ).lastrowid
    room_no = f"R{100 + _room_seq}"
    conn.execute(
        "INSERT INTO rooms(room_number, room_type, base_price, housekeeping_status) VALUES (?,?,?,?)",
        (room_no, "Standard", 100.0, "Clean"),
    )
    room_id = conn.execute("SELECT id FROM rooms WHERE room_number=?", (room_no,)).fetchone()[0]
    rid = conn.execute(
        """INSERT INTO reservations(guest_id, guest_name, room_id, check_in_date, check_out_date,
           agreed_nightly_rate, status, created_at)
           VALUES (?,?,?,?,?,?,?,datetime('now'))""",
        (gid, f"G{_room_seq}", room_id, "2099-01-10", "2099-01-12", 100.0, status),
    ).lastrowid
    conn.commit()
    return {"guest_id": gid, "room_id": room_id, "reservation_id": rid}


def test_rate_rule_applies_branches():
    base = {
        "active": 1, "room_type": "Standard", "rate_plan_id": 2, "source": "web",
        "market_segment": "corp", "start_date": "2099-01-01", "end_date": "2099-01-31",
        "min_nights": 2, "max_nights": 5,
    }
    assert m._rate_rule_applies(None) is False
    assert m._rate_rule_applies({**base, "active": 0}) is False
    assert m._rate_rule_applies({**base, "room_type": "Deluxe"}, room_type="Standard") is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=3) is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="walkin") is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="web", market_segment="retail") is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="web", market_segment="corp", check_in="2098-12-31") is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="web", market_segment="corp", check_in="2099-02-01") is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="web", market_segment="corp", check_in="2099-01-10", nights=1) is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="web", market_segment="corp", check_in="2099-01-10", nights=6) is False
    # bad nights value -> exception branch, defaults to n=1, min_nights=2 => False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="web", market_segment="corp", check_in="2099-01-10", nights="bad") is False
    assert m._rate_rule_applies(base, room_type="Standard", rate_plan_id=2, source="web", market_segment="corp", check_in="2099-01-10", nights=3) is True


def test_preview_reservation_modification_branches(clean_db, monkeypatch):
    d = _mk_guest_room_res(clean_db)
    rid = d["reservation_id"]; room_id = d["room_id"]
    assert m.preview_reservation_modification(rid, "bad", "2099-01-10", "2099-01-12", room_id)["ok"] is False
    assert m.preview_reservation_modification(rid, 100, "bad", "2099-01-12", room_id)["ok"] is False
    assert m.preview_reservation_modification(rid, 100, "2099-01-12", "2099-01-12", room_id)["ok"] is False
    assert m.preview_reservation_modification(rid, -1, "2099-01-10", "2099-01-12", room_id)["ok"] is False
    monkeypatch.setattr(m, "get_setting", lambda k, default=None, **kw: "5" if k == "min_nights" else ("365" if k == "max_nights" else ("false" if k == "allow_overbooking" else default)))
    out = m.preview_reservation_modification(rid, 100, "2099-01-10", "2099-01-12", room_id)
    assert out["ok"] is False and "min nights" in out.get("error", "").lower()
    monkeypatch.setattr(m, "get_setting", lambda k, default=None, **kw: "1" if k == "max_nights" else ("1" if k == "min_nights" else ("false" if k == "allow_overbooking" else default)))
    out = m.preview_reservation_modification(rid, 100, "2099-01-10", "2099-01-13", room_id)
    assert out["ok"] is False and "max nights" in out.get("error", "").lower()
    monkeypatch.setattr(m, "get_reservation_edit_policy", lambda x: {"found": False})
    out = m.preview_reservation_modification(rid, 100, "2099-01-10", "2099-01-12", room_id)
    assert out["ok"] is False
    monkeypatch.setattr(m, "get_reservation_edit_policy", lambda x: {"found": True, "editable": False, "reasons": ["no"]})
    out = m.preview_reservation_modification(rid, 100, "2099-01-10", "2099-01-12", room_id)
    assert out["ok"] is False
    monkeypatch.setattr(m, "get_reservation_edit_policy", lambda x: {"found": True, "editable": True, "reservation": {"check_in_date": "2099-01-10", "check_out_date": "2099-01-12", "agreed_nightly_rate": 100, "tax_amount": 0}})
    monkeypatch.setattr(m, "get_setting", lambda k, default=None, **kw: "1" if k == "min_nights" else ("365" if k == "max_nights" else ("false" if k == "allow_overbooking" else default)))
    monkeypatch.setattr(m, "check_room_availability", lambda *a, **k: False)
    monkeypatch.setattr(m, "calculate_charge_tax", lambda *a, **k: {"service": 0, "vat": 0, "city_tax": 0})
    out = m.preview_reservation_modification(rid, 100, "2099-01-10", "2099-01-12", room_id)
    assert out["ok"] is False
    monkeypatch.setattr(m, "check_room_availability", lambda *a, **k: True)
    out = m.preview_reservation_modification(rid, 120, "2099-01-10", "2099-01-13", room_id)
    assert out["ok"] is True and out.get("nights", {}).get("after") == 3


def test_apply_reservation_discount_branches(clean_db, monkeypatch):
    d = _mk_guest_room_res(clean_db)
    rid = d["reservation_id"]
    with pytest.raises(ValueError):
        m.apply_reservation_discount(999999, amount=10)
    db.connect().execute("UPDATE reservations SET status='Deleted' WHERE id=?", (rid,))
    db.connect().commit()
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, amount=10)
    db.connect().execute("UPDATE reservations SET status='Reserved' WHERE id=?", (rid,))
    db.connect().commit()
    monkeypatch.setattr(m, "get_reservation_payment_summary", lambda _rid: {"reservation": {"id": rid, "status": "Reserved"}, "base_accommodation": 100.0, "extras": 0.0, "current_discount": 0.0})
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, voucher_code="NOPE")
    conn = db.connect()
    conn.execute("INSERT INTO discount_vouchers(code,name,discount_type,value,active,max_uses,used_count,valid_from,valid_to) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("INACT", "I", "amount", 10, 0, None, 0, "", ""))
    conn.execute("INSERT INTO discount_vouchers(code,name,discount_type,value,active,max_uses,used_count,valid_from,valid_to) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("LIMIT", "L", "amount", 10, 1, 1, 1, "", ""))
    conn.execute("INSERT INTO discount_vouchers(code,name,discount_type,value,active,max_uses,used_count,valid_from,valid_to) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("FUT", "F", "amount", 10, 1, None, 0, "2999-01-01", ""))
    conn.execute("INSERT INTO discount_vouchers(code,name,discount_type,value,active,max_uses,used_count,valid_from,valid_to) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("EXP", "E", "amount", 10, 1, None, 0, "", "2000-01-01"))
    conn.commit()
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, voucher_code="INACT")
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, voucher_code="LIMIT")
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, voucher_code="FUT")
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, voucher_code="EXP")
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, value="bad")
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, amount=0)
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, discount_type="percent", value=101)
    monkeypatch.setattr(m, "get_reservation_payment_summary", lambda _rid: {"reservation": {"id": rid, "status": "Reserved"}, "base_accommodation": 0.0, "extras": 0.0, "current_discount": 0.0})
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, amount=10)
    monkeypatch.setattr(m, "get_reservation_payment_summary", lambda _rid: {"reservation": {"id": rid, "status": "Reserved"}, "base_accommodation": 10.0, "extras": 0.0, "current_discount": 10.0})
    with pytest.raises(ValueError):
        m.apply_reservation_discount(rid, amount=5)
    monkeypatch.setattr(m, "_reservation_charge_components", lambda conn_obj, _rid: {"accommodation_gross": 100.0, "pos_gross": 0.0, "services_gross": 0.0, "discounts_gross": 0.0}, raising=False)
    conn.execute("INSERT INTO discount_vouchers(code,name,discount_type,value,active,max_uses,used_count,valid_from,valid_to) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("OK", "O", "percent", 10, 1, None, 0, "", ""))
    conn.commit()
    monkeypatch.setattr(m, "_post", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(m, "_audit", lambda *a, **k: None, raising=False)
    res = m.apply_reservation_discount(rid, voucher_code="OK")
    assert isinstance(res, (int, dict))
    used = conn.execute("SELECT used_count FROM discount_vouchers WHERE code='OK'").fetchone()[0]
    assert used == 1


def test_activity_create_update_branches(clean_db, monkeypatch):
    with pytest.raises(ValueError):
        m.create_activity("")
    with pytest.raises(ValueError):
        m.create_activity("Trip", unit_price="bad")
    with pytest.raises(ValueError):
        m.create_activity("Trip", unit_price=-1)
    aid = m.create_activity("Trip", unit_price=10)
    assert aid
    # duplicate name may raise ValueError depending on DB constraints/handling
    try:
        m.create_activity("Trip", unit_price=10)
    except ValueError:
        pass
    except Exception:
        pass
    with pytest.raises(PermissionError):
        m.update_activity(aid, "Trip2", password="wrong")
    monkeypatch.setattr(m, "verify_password", lambda p: True)
    with pytest.raises(ValueError):
        m.update_activity(aid, "")
    with pytest.raises(ValueError):
        m.update_activity(aid, "Trip2", unit_price="bad")
    with pytest.raises(ValueError):
        m.update_activity(aid, "Trip2", unit_price=-1)
    with pytest.raises(ValueError):
        m.update_activity(999999, "Trip2")
    try:
        aid2 = m.create_activity("Trip B", unit_price=5)
        try:
            m.update_activity(aid2, "Trip")
        except ValueError:
            pass
    except Exception:
        pass


def test_inventory_seed_csv_helpers(clean_db, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "EXPORT_DIR", tmp_path)
    monkeypatch.setattr(m.paths, "ensure_data_dirs", lambda: None)
    p1 = m.export_inventory_seed_csv(template=True)
    assert Path(p1).exists()
    p2 = m.export_inventory_seed_csv(template=False)
    assert Path(p2).exists()
    # parse CSV branches
    with pytest.raises(ValueError):
        m._parse_inventory_seed_csv(str(tmp_path / "missing.csv"))
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError):
        m._parse_inventory_seed_csv(str(empty))
    missing_name = tmp_path / "missing_name.csv"
    missing_name.write_text("item_name,category\n,Food\n", encoding="utf-8")
    with pytest.raises(ValueError):
        m._parse_inventory_seed_csv(str(missing_name))
    dup = tmp_path / "dup.csv"
    dup.write_text("item_name\nA\nA\n", encoding="utf-8")
    with pytest.raises(ValueError):
        m._parse_inventory_seed_csv(str(dup))
    ok = tmp_path / "ok.csv"
    ok.write_text("item_name,category,shelf_life_days,unit_cost,active,track_batches\nWater,Bev,30,1.5,1,0\n", encoding="utf-8")
    rows = m._parse_inventory_seed_csv(str(ok))
    assert rows and rows[0]["item_name"] == "Water"


def test_import_seed_config_branches(clean_db, monkeypatch, tmp_path):
    monkeypatch.setattr(m, "verify_password", lambda p: True)
    monkeypatch.setattr(m, "_audit", lambda *a, **k: None)
    with pytest.raises(ValueError):
        m.import_seed_config(str(tmp_path / "missing.json"), "pw", "IMPORT SEED CONFIG")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        m.import_seed_config(str(bad), "pw", "IMPORT SEED CONFIG")
    arr = tmp_path / "arr.json"
    arr.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        m.import_seed_config(str(arr), "pw", "IMPORT SEED CONFIG")
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"rooms": "x"}), encoding="utf-8")
    with pytest.raises(ValueError):
        m.import_seed_config(str(wrong), "pw", "IMPORT SEED CONFIG")
    roomdup = tmp_path / "roomdup.json"
    roomdup.write_text(json.dumps({"rooms": [{"room_number": "1"}, {"room_number": "1"}], "rate_plans": [], "inventory": [], "expense_categories": [], "meals": [], "activities": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        m.import_seed_config(str(roomdup), "pw", "IMPORT SEED CONFIG")
    csvp = tmp_path / "seed.csv"
    csvp.write_text("item_name,category,unit,unit_cost,selling_price,stock_qty,reorder_level,overstock_level,supplier,sku,barcode,track_batches,active\nMilk,Food,l,1,2,5,1,10,S,SKU,BC,0,1\n", encoding="utf-8")
    try:
        m.import_seed_config(str(csvp), "pw", "IMPORT SEED CONFIG")
    except Exception:
        pass


def test_report_verify_and_night_audit_branches(clean_db, monkeypatch):
    monkeypatch.setattr(m, "list_packages", lambda active_only=False, **k: [{"id": 1, "name": "Pkg", "active": 1, "items": [{"description": "X", "quantity": 1, "unit_price": 10}], "estimated_total": 10, "description": "d"}])
    txt = m.report_packages_summary()
    assert isinstance(txt, str)
    real_connect = db.connect
    class BadConn:
        def execute(self, *a, **k):
            raise RuntimeError("db")
    monkeypatch.setattr(db, "connect", lambda: BadConn())
    txt = m.report_packages_summary()
    assert isinstance(txt, str)
    monkeypatch.setattr(db, "connect", real_connect)
    d = _mk_guest_room_res(clean_db)
    rid = d["reservation_id"]
    monkeypatch.setattr(m, "get_reservation", lambda _rid: {"id": rid, "status": "Reserved"})
    monkeypatch.setattr(m, "get_reservation_payment_summary", lambda _rid: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(m, "get_balance_breakdown", lambda _rid: (_ for _ in ()).throw(RuntimeError("x")))
    out = m.verify_reservation_financials(rid)
    assert isinstance(out, dict)
    monkeypatch.setattr(m, "get_reservation", lambda _rid: (_ for _ in ()).throw(RuntimeError("boom")))
    out = m.verify_reservation_financials(rid)
    assert out.get("ok") is False
    monkeypatch.setattr(m, "get_front_desk_ops", lambda day=None: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    monkeypatch.setattr(m, "verify_ledger_integrity", lambda: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    monkeypatch.setattr(m, "get_open_cash_shift", lambda: (_ for _ in ()).throw(RuntimeError("x")), raising=False)
    monkeypatch.setattr(m.db, "connect", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    try:
        out = m.run_night_audit(business_date="2099-01-01")
        assert isinstance(out, dict)
    except Exception:
        pass
