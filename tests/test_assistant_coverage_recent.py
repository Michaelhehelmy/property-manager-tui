from __future__ import annotations

import pytest

from app_core import assistant, services
from conftest import make_reservation


def test_recent_grounded_read_tools_direct(clean_db):
    rid = make_reservation(rate=120.0, ci="2026-12-10", co="2026-12-12", guest="Assistant Guest")

    # seed grounded entities
    rr_id = services.add_rate_rule(
        name="Weekend Adj",
        adjustment_type="amount",
        adjustment_value=10.0,
        active=True,
    )
    svc_id = services.create_service_catalog_item(
        name="Airport Pickup", category="Transport", unit_price=35.0, description="Pickup", taxable=True, active=True
    )
    v_id = services.create_discount_voucher(
        code="WELCOME10", discount_type="percent", value=10.0, name="Welcome", active=True
    )
    a_id = services.create_approval_request(
        action_type="discount_override",
        entity_type="reservation",
        entity_id=str(rid),
        payload={"amount": 5},
        reason="test",
        requested_by="manager",
    )

    rr = assistant.execute_tool("list_rate_rules", {"active_only": False})
    assert "error" not in rr
    assert rr.get("count", 0) >= 1
    assert any((r.get("id") == rr_id) or (r.get("name") == "Weekend Adj") for r in (rr.get("rate_rules") or []))

    sc = assistant.execute_tool("list_service_catalog", {"active_only": True})
    assert "error" not in sc
    assert sc.get("count", 0) >= 1
    assert any(
        (x.get("id") == svc_id) or (x.get("name") == "Airport Pickup") for x in (sc.get("service_catalog") or [])
    )

    dv = assistant.execute_tool("list_discount_vouchers", {"active_only": False})
    assert "error" not in dv
    assert dv.get("count", 0) >= 1
    assert any(
        (x.get("id") == v_id) or (str(x.get("code", "")).upper() == "WELCOME10")
        for x in (dv.get("discount_vouchers") or [])
    )

    ap = assistant.execute_tool("list_approvals", {"status": "", "limit": 50})
    assert "error" not in ap
    assert ap.get("count", 0) >= 1
    assert any(int(x.get("id") or 0) == int(a_id) for x in (ap.get("approvals") or []))

    one = assistant.execute_tool("get_approval", {"approval_id": a_id})
    assert "error" not in one
    assert int((one.get("approval") or {}).get("id") or 0) == int(a_id)

    miss_ap = assistant.execute_tool("get_approval", {})
    assert miss_ap.get("error")
    nf_ap = assistant.execute_tool("get_approval", {"approval_id": 999999})
    assert nf_ap.get("error")

    fin = assistant.execute_tool("verify_reservation_financials", {"reservation_id": rid})
    assert isinstance(fin, dict)
    assert fin.get("reservation_id") == rid or fin.get("ok") in (True, False)
    assert "error" not in fin or fin.get("reservation_id") == rid

    fin_miss = assistant.execute_tool("verify_reservation_financials", {})
    assert fin_miss.get("error")

    # reservation-scoped read tools already present but exercise common paths
    rs = assistant.execute_tool("search_reservations", {"query": "Assistant Guest", "limit": 10})
    assert "error" not in rs
    assert rs.get("count", 0) >= 1


def test_recent_grounded_read_intents_via_chat(clean_db):
    rid = make_reservation(rate=90.0, ci="2026-12-20", co="2026-12-21", guest="Intent Guest")
    services.add_rate_rule(name="Seasonal", adjustment_type="percent", adjustment_value=5.0, active=True)
    services.create_service_catalog_item(name="Spa", category="Wellness", unit_price=50.0, active=True)
    services.create_discount_voucher(code="SPA5", discount_type="amount", value=5.0, active=True)
    services.create_approval_request(
        action_type="void_payment", entity_type="reservation", entity_id=str(rid), reason="intent"
    )

    cases = [
        "show rate rules",
        "list service catalog",
        "show discount vouchers",
        "list approvals",
        f"check financial integrity for reservation {rid}",
        f"show reservation {rid} summary",
    ]
    for msg in cases:
        res = assistant.chat(msg)
        assert isinstance(res, dict)
        assert res.get("ok") is True or "answer" in res
        assert isinstance(res.get("answer", ""), str) and res.get("answer", "").strip() != ""
        # should not leak tracebacks
        assert "traceback" not in str(res).lower()
