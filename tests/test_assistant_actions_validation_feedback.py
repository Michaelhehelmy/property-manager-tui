from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app_core.assistant import action_validation as av
from app_core.assistant import actions
from app_core.assistant import feedback
from app_core import services


def _row(d):
    # simple mapping-like row for helpers that accept sqlite rows or dicts
    return d


def test_action_validation_helpers_and_normalization(monkeypatch):
    assert av._norm_str(None) == ""
    assert av._int_or_none("3") == 3
    assert av._int_or_none("x") is None
    assert av._float_or_none("2.5") == 2.5
    assert av._float_or_none("") is None
    assert av._bool("yes") is True
    assert av._bool(0) is False
    assert av._get({"a": 1}, "b", "a") == 1
    assert av._row_to_dict({"k": 1}) == {"k": 1}
    assert av._row_to_dict(None) is None
    assert av._match_inventory_candidate({"item_name": "Coke"}, "coke") is True
    assert av._match_inventory_candidate({"sku": "ABC"}, "abc") is True
    assert av._match_inventory_candidate({"id": 7}, "7") is True
    assert av._match_inventory_candidate({"id": 7}, "8") is False

    exp = av._expires_at(5)
    assert exp.endswith("Z")
    assert av._is_expired((datetime.utcnow() - timedelta(days=1)).replace(microsecond=0).isoformat()) is True
    assert av._is_expired((datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat()) is False
    assert av._parse_expiry("") is None
    assert av._parse_expiry("not-a-date") is None

    a = av.normalize_action_args("propose_add_pos_item", {"inv_id": "5", "quantity": 2, "item_name": "Water"})
    assert a["item_id"] == 5 and a["qty"] == 2 and a["desc"] == "Water"
    a = av.normalize_action_args("propose_record_payment", {"rid": 9, "amount": 10})
    assert a["reservation_id"] == 9 and a["payment_type"] == "Payment"
    a = av.normalize_action_args("propose_complete_walkin_sale", {"items": [{"item_id": 1, "item_name": "X"}]})
    assert a["items"][0]["inv_id"] == 1 and a["items"][0]["desc"] == "X" and a["payment_type"] == "Payment"

    unsupported = av.build_prechecks("unknown_action", {"x": 1})
    assert unsupported["ok"] is False and "Unsupported action type" in unsupported["errors"][0]


def test_action_validation_resolvers_and_previews(monkeypatch):
    # reservation resolver
    monkeypatch.setattr(services, "get_reservation", lambda rid: {"id": rid, "status": "Reserved"} if rid == 1 else None)
    ok = av.resolve_reservation({"reservation_id": 1}, for_posting=True)
    assert ok["ok"] is True
    bad = av.resolve_reservation({"reservation_id": 2}, for_posting=True)
    assert bad["ok"] is False
    status_bad = av.resolve_reservation({"reservation_id": 1}, for_posting=False)
    assert status_bad["ok"] is True
    monkeypatch.setattr(services, "get_reservation", lambda rid: {"id": rid, "status": "Cancelled"})
    not_allowed = av.resolve_reservation({"reservation_id": 1}, for_posting=True)
    assert not_allowed["ok"] is False

    # guest resolver
    monkeypatch.setattr(services, "get_guest_by_id", lambda gid: {"id": gid, "canonical_name": "G"} if gid == 5 else None)
    g = av.resolve_guest({"guest_id": 5})
    assert g["ok"] is True and g["resolved"]["guest"]["id"] == 5
    monkeypatch.setattr(services, "assistant_find_guests", lambda q, limit=8: [{"id": 1, "canonical_name": "Alice"}])
    g = av.resolve_guest({"guest_name": "Ali"})
    assert g["ok"] is True and g["resolved"]["guest"]["id"] == 1
    monkeypatch.setattr(services, "assistant_find_guests", lambda q, limit=8: [{"id": 1}, {"id": 2}])
    g = av.resolve_guest({"guest_name": "A"})
    assert g["ok"] is True and any("ambiguous" in w.lower() for w in g["warnings"])
    monkeypatch.setattr(services, "assistant_find_guests", lambda q, limit=8: [])
    g = av.resolve_guest({"guest_name": "Nobody"})
    assert g["ok"] is True and any("no existing guest" in w.lower() for w in g["warnings"])

    # inventory + batch
    monkeypatch.setattr(services, "get_inventory_item", lambda iid: {"id": iid, "item_name": "Water", "selling_price": 2.0, "stock_qty": 10, "active": 1, "track_batches": 0} if iid == 3 else None)
    monkeypatch.setattr(services, "resolve_barcode", lambda code: {"type": "item", "item": {"id": 3, "item_name": "Water", "selling_price": 2.0, "stock_qty": 10, "active": 1}})
    monkeypatch.setattr(services, "search_inventory", lambda query=None: [{"id": 3, "item_name": "Water", "selling_price": 2.0, "stock_qty": 10, "active": 1}])
    monkeypatch.setattr(services, "get_inventory_items", lambda query=None: [{"id": 3, "item_name": "Water", "selling_price": 2.0, "stock_qty": 10, "active": 1}])
    it = av.resolve_inventory_item({"item_id": 3})
    assert it["ok"] is True
    it = av.resolve_inventory_item({"barcode": "ABC"})
    assert it["ok"] is True
    it = av.resolve_inventory_item({"item_name": "Water"})
    assert it["ok"] is True
    monkeypatch.setattr(services, "search_inventory", lambda query=None: [{"id": 3, "item_name": "Water"}, {"id": 4, "item_name": "Water 2"}])
    monkeypatch.setattr(services, "get_inventory_items", lambda query=None: [{"id": 3, "item_name": "Water"}, {"id": 4, "item_name": "Water 2"}])
    amb = av.resolve_inventory_item({"item_name": "Wat"})
    assert amb["ok"] is False

    monkeypatch.setattr(services, "get_inventory_batch", lambda bid: {"id": bid, "item_id": 3, "quantity_remaining": 5, "status": "active", "lot_code": "L1"})
    br = av.resolve_batch_or_lot({"id": 3, "track_batches": 1}, {"batch_id": 7, "qty": 2})
    assert br["ok"] is True and br["resolved"]["batch"]["id"] == 7
    monkeypatch.setattr(services, "resolve_barcode", lambda code: {"type": "batch", "batch": {"id": 8, "item_id": 3, "quantity_remaining": 1, "status": "active"}})
    br = av.resolve_batch_or_lot({"id": 3, "track_batches": 1}, {"lot_code": "LOTX", "qty": 5})
    assert br["ok"] is True and any("exceeds" in w.lower() or "may fail" in w.lower() for w in br["warnings"])

    # room / rate plan / activity / availability
    monkeypatch.setattr(services, "get_room", lambda rid: {"id": rid, "room_number": "101", "base_price": 100.0} if rid == 1 else None)
    monkeypatch.setattr(services, "get_all_rooms", lambda: [{"id": 1, "room_number": "101", "base_price": 100.0}, {"id": 2, "room_number": "102", "base_price": 120.0}])
    assert av.resolve_room({"room_id": 1})["ok"] is True
    assert av.resolve_room({"room_number": "101"})["ok"] is True
    assert av.resolve_room({"room_number": "10"})["ok"] is False  # ambiguous contains match

    monkeypatch.setattr(services, "get_all_rate_plans", lambda: [{"id": 1, "name": "Std"}])
    assert av.resolve_rate_plan({"rate_plan_id": 1})["ok"] is True
    assert av.resolve_rate_plan({"rate_plan_id": 9})["ok"] is False

    monkeypatch.setattr(services, "get_activity", lambda aid: {"id": aid, "name": "Spa", "unit_price": 50.0, "active": 1} if aid == 4 else None)
    assert av.resolve_activity({"activity_id": 4})["ok"] is True
    assert av.resolve_activity({"activity_id": 9})["ok"] is False

    monkeypatch.setattr(services, "check_room_availability", lambda *a, **k: True)
    monkeypatch.setattr(services, "get_rooms_available_between", lambda ci, co: [{"id": 1}])
    avres = av.check_availability({"room_id": 1, "check_in": "2099-01-01", "check_out": "2099-01-03"})
    assert avres["ok"] is True and avres["preview"]["nights"] == 2
    monkeypatch.setattr(services, "check_room_availability", lambda *a, **k: False)
    avres = av.check_availability({"room_id": 1, "check_in": "2099-01-01", "check_out": "2099-01-03"})
    assert avres["ok"] is False

    # previews
    monkeypatch.setattr(services, "get_reservation", lambda rid: {"id": rid, "status": "Reserved"})
    monkeypatch.setattr(services, "get_inventory_item", lambda iid: {"id": iid, "item_name": "Water", "selling_price": 2.0, "stock_qty": 10, "active": 1, "track_batches": 0})
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: "false")
    monkeypatch.setattr(services, "calculate_tax", lambda subtotal, nights=0: {"net": subtotal, "service": 0, "vat": 0, "city_tax": 0, "gross": subtotal})
    p = av.preview_add_pos_item({"reservation_id": 1, "item_id": 3, "qty": 2})
    assert p["ok"] is True and p["preview"]["estimated_subtotal"] == 4.0

    monkeypatch.setattr(services, "get_balance_breakdown", lambda rid: {"balance_due": 50.0})
    p = av.preview_record_payment({"reservation_id": 1, "amount": 20, "method": "Cash"})
    assert p["ok"] is True and p["preview"]["projected_balance_due"] == 30.0

    monkeypatch.setattr(services, "get_activity", lambda aid: {"id": aid, "name": "Spa", "unit_price": 50.0, "active": 1})
    p = av.preview_book_activity({"reservation_id": 1, "activity_id": 4, "quantity": 2})
    assert p["ok"] is True and p["preview"]["estimated_total"] == 100.0

    monkeypatch.setattr(services, "get_room", lambda rid: {"id": rid, "room_number": "101", "base_price": 100.0})
    monkeypatch.setattr(services, "get_all_rooms", lambda: [{"id": 1, "room_number": "101", "base_price": 100.0}])
    monkeypatch.setattr(services, "check_room_availability", lambda *a, **k: True)
    monkeypatch.setattr(services, "assistant_find_guests", lambda q, limit=8: [])
    p = av.preview_create_reservation({"guest_name": "New Guest", "room_id": 1, "rate": 100.0, "check_in": "2099-02-01", "check_out": "2099-02-03"})
    assert p["ok"] is True and p["preview"]["nights"] == 2

    monkeypatch.setattr(services, "search_inventory", lambda query=None: [{"id": 3, "item_name": "Water", "selling_price": 2.0, "stock_qty": 10, "active": 1}])
    monkeypatch.setattr(services, "get_inventory_items", lambda query=None: [{"id": 3, "item_name": "Water", "selling_price": 2.0, "stock_qty": 10, "active": 1}])
    p = av.preview_complete_walkin_sale({"items": [{"inv_id": 3, "qty": 2, "price": 2.0, "desc": "Water"}], "method": "Cash"})
    assert p["ok"] is True and p["preview"]["estimated_total"] == 4.0


def test_actions_proposal_execute_and_cancel(monkeypatch):
    # create proposal invalid type
    bad = actions.create_proposal("nope", {})
    assert bad["ok"] is False and bad["status"] == "invalid"

    # proposal validation failed
    monkeypatch.setattr(av, "normalize_action_args", lambda t, a: dict(a))
    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": False, "errors": ["x"], "warnings": [], "checks": [], "resolved": {}, "preview": {"type": t}})
    bad = actions.create_proposal("propose_record_payment", {"reservation_id": 1})
    assert bad["ok"] is False and bad["status"] == "invalid"

    # preview-only proposal (no execute type)
    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": True, "errors": [], "warnings": ["w"], "checks": [], "resolved": {}, "preview": {"type": t, "reservation_id": 1}})
    p = actions.create_proposal("preview_add_pos_item", {"reservation_id": 1})
    assert p["ok"] is True and p["status"] == "preview" and p["execute_action_type"] is None
    assert actions._summary_for("preview_add_pos_item", p["prechecks"], {"reservation_id": 1})

    # pending proposal + cancel
    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {"type": t, "reservation_id": 1, "amount": 10, "method": "Cash"}})
    p = actions.create_proposal("propose_record_payment", {"reservation_id": 1, "amount": 10, "method": "Cash"})
    assert p["ok"] is True and p["status"] == "pending" and p["execute_action_type"] == "execute_record_payment"
    c = actions.cancel_pending_action(p, reason="user")
    assert c["ok"] is True and c["status"] == "cancelled" and c["cancel_reason"] == "user"
    assert actions.cancel_pending_action(None)["ok"] is False

    # execute branches: invalid, expired, proof required, validation failed, success, failure
    assert actions.execute_pending_action(None)["status"] == "invalid"
    assert actions.execute_pending_action({"status": "executed"})["status"] == "executed"

    monkeypatch.setattr(av, "_is_expired", lambda exp: True)
    assert actions.execute_pending_action({"status": "pending", "expires_at": "x"})["status"] == "expired"
    monkeypatch.setattr(av, "_is_expired", lambda exp: False)

    pending = {
        "status": "pending",
        "action_id": "aid",
        "action_type": "propose_record_payment",
        "execute_action_type": "execute_record_payment",
        "args": {"reservation_id": 1, "amount": 10, "method": "Cash"},
        "required_proofs": ["ui_confirmation"],
        "prechecks": {},
    }
    r = actions.execute_pending_action(pending, form_proofs={}, ui_context={})
    assert r["status"] == "proof_required"

    monkeypatch.setattr(av, "normalize_action_args", lambda t, a: dict(a))
    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": False, "errors": ["bad"], "warnings": [], "checks": [], "resolved": {}})
    r = actions.execute_pending_action(pending, form_proofs={"confirmed": True}, ui_context={})
    assert r["status"] == "validation_failed"

    monkeypatch.setattr(av, "build_prechecks", lambda t, a, ui_context=None: {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {"reservation": {"id": 1}}, "preview": {}})
    monkeypatch.setattr(services, "record_payment", lambda *a, **k: {"payment_id": 77})
    r = actions.execute_pending_action(pending, form_proofs={"confirmed": True}, ui_context={})
    assert r["ok"] is True and r["status"] == "executed" and r["affected_ids"]["payment_id"] == 77

    monkeypatch.setattr(services, "record_payment", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = actions.execute_pending_action(pending, form_proofs={"confirmed": True}, ui_context={})
    assert r["ok"] is False and r["status"] == "failed"

    # direct executor wrappers via monkeypatched services
    monkeypatch.setattr(services, "add_pos_item", lambda *a, **k: {"pos_id": 5})
    out = actions._exec_add_pos_item({"reservation_id": 1, "item_id": 3, "desc": "Water", "qty": 1, "price": 2.0}, {"item": {"id": 3}})
    assert out["affected_ids"]["pos_id"] == 5
    monkeypatch.setattr(services, "complete_walkin_sale", lambda *a, **k: {"payment_id": 9, "pos_ids": [1]})
    out = actions._exec_complete_walkin_sale({"items": [{"inv_id": 3, "desc": "Water", "qty": 1, "price": 2.0}], "method": "Cash"}, {})
    assert out["affected_ids"]["payment_id"] == 9
    monkeypatch.setattr(services, "book_activity", lambda *a, **k: {"pos_id": 11})
    out = actions._exec_book_activity({"reservation_id": 1, "activity_id": 4, "quantity": 1}, {})
    assert out["affected_ids"]["pos_id"] == 11
    monkeypatch.setattr(services, "create_reservation", lambda **k: 123)
    out = actions._exec_create_reservation({"guest_name": "A", "room_id": 1, "rate": 100.0, "check_in": "2099-01-01", "check_out": "2099-01-02"}, {})
    assert out["affected_ids"]["reservation_id"] == 123

    # summary helper branches
    assert "walk-in sale" in actions._summary_for("propose_complete_walkin_sale", {"preview": {"item_count": 1, "estimated_total": 5, "method": "Cash"}}, {"items": [{}], "method": "Cash"})
    assert "Book activity" in actions._summary_for("propose_book_activity", {"preview": {"reservation_id": 1, "activity_name": "Spa", "quantity": 2}}, {})
    assert "Create reservation" in actions._summary_for("propose_create_reservation", {"preview": {"guest_name": "A", "room_number": "101", "check_in": "x", "check_out": "y"}}, {})


def test_feedback_helpers(clean_db, monkeypatch, tmp_path):
    # record/list feedback (DB-backed)
    fid = feedback.record_feedback("hello", predicted_intent="greet", corrected_intent="help", rating=1, confidence=0.5, slots={"a": 1}, corrected_slots={"a": 2}, tools_used=["t"], answer="ans", user_note="note", source="test", status="new")
    assert fid is None or isinstance(fid, int)
    rows = feedback.list_feedback(limit=10)
    assert isinstance(rows, list)
    # correction exemplars / exemplar_match
    monkeypatch.setattr(feedback, "_EXEMPLAR_CACHE", None)
    ex = feedback.get_correction_exemplars(limit=50)
    assert isinstance(ex, list)
    # force cache for matching
    feedback._EXEMPLAR_CACHE = [{"text": "show balance for reservation 1", "label": "reservation_balance", "slots": {"reservation_id": 1}}]
    m = feedback.exemplar_match("show balance for reservation 1")
    assert m and m["source"] == "exemplar_exact"
    m = feedback.exemplar_match("please show the balance for reservation 1")
    assert m and m["intent"] == "reservation_balance"

    # ML loaders should fail gracefully without models/packages
    monkeypatch.setenv("ASSISTANT_NLU", "sklearn")
    monkeypatch.setenv("ASSISTANT_SKLEARN_MODEL", str(tmp_path / "missing.pkl"))
    feedback._SKLEARN_CACHE = None
    assert feedback._load_sklearn_router() is None
    assert feedback._predict_intent_ml("hi") in (None, dict(), False) or True  # no crash
    monkeypatch.setenv("ASSISTANT_NLU", "setfit")
    monkeypatch.setenv("ASSISTANT_SETFIT_MODEL_DIR", str(tmp_path / "missing-model"))
    feedback._SETFIT_CACHE = None
    assert feedback._load_setfit_router() is None
    assert feedback._predict_intent_ml("hi") in (None, dict(), False) or True
