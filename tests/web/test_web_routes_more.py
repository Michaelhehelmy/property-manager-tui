from __future__ import annotations

from pathlib import Path

from app_core import services
from app_core.db import connect
from tests.web.conftest import get_csrf, set_session


def test_assistant_page_and_flows(client, monkeypatch):
    import ui.web.routes.assistant as ar
    monkeypatch.setattr(ar.assistant, "_llm_config", lambda: {"configured": False})
    monkeypatch.setattr(ar.services, "get_setting", lambda k, d=None: "true")
    monkeypatch.setattr(ar.assistant, "chat", lambda msg, history=None: {"answer": f"echo:{msg}", "source": "mock", "tools_used": ["t1"]})

    # locked because unlock required and not unlocked
    r = client.get("/assistant")
    assert r.status_code == 200 and "Assistant" in r.text

    # ask while locked -> redirect to unlock (assistant page hides forms when locked; seed CSRF)
    csrf = set_session(client, unlocked=False)
    r = client.post("/assistant/ask", data={"csrf": csrf, "message": "hi"})
    assert r.status_code == 303 and "/unlock?next=/assistant" in r.headers["location"]

    # unlocked ask normal redirect
    token = set_session(client, unlocked=True)
    r = client.post("/assistant/ask", data={"csrf": token, "message": "hello"})
    assert r.status_code == 303 and r.headers["location"] == "/assistant"
    r = client.get("/assistant")
    assert "echo:hello" in r.text or "hello" in r.text

    # empty message does not append
    token = set_session(client, unlocked=True, extra={"assistant_history": []})
    r = client.post("/assistant/ask", data={"csrf": token, "message": "   "})
    assert r.status_code == 303

    # HX ask
    token = set_session(client, unlocked=True, extra={"assistant_history": [{"role":"user","content":"x"}]})
    r = client.post("/assistant/ask", data={"csrf": token, "message": "hx"}, headers={"HX-Request": "true"})
    assert r.status_code == 200

    # clear normal and HX
    token = set_session(client, unlocked=True, extra={"assistant_history": [{"role":"user","content":"x"}]})
    r = client.post("/assistant/clear", data={"csrf": token})
    assert r.status_code == 303
    token = set_session(client, unlocked=True, extra={"assistant_history": [{"role":"user","content":"x"}]})
    r = client.post("/assistant/clear", data={"csrf": token}, headers={"HX-Request": "true"})
    assert r.status_code == 200

    # get_setting exception path in _assistant_allowed (selective to avoid breaking rendering)
    monkeypatch.setattr(ar.services, "get_setting", lambda k, d=None: (_ for _ in ()).throw(RuntimeError("x")) if k == "assistant_require_unlock" else d)
    token = set_session(client, unlocked=False)
    r = client.get("/assistant")
    assert r.status_code == 200


def test_accounting_routes(client, monkeypatch):
    # seed a reservation for payment/refund
    rid = services.create_reservation("G", "Direct", 1, None, 100.0, "2026-12-01", "2026-12-03", 0.0)
    r = client.get("/accounting")
    assert r.status_code == 200

    page = client.get("/accounting"); csrf = get_csrf(page.text)
    r = client.post("/accounting/expense", data={"csrf": csrf, "cat_id": "1", "param": "Soap", "amount": "10", "vat": "0", "notes": "", "method": "Cash"}, follow_redirects=True)
    assert r.status_code == 200

    # expense error
    r = client.post("/accounting/expense", data={"csrf": csrf, "cat_id": "999999", "param": "X", "amount": "bad", "vat": "0"}, follow_redirects=True)
    assert r.status_code == 200

    # record payment with and without reservation
    r = client.post("/accounting/payment", data={"csrf": csrf, "reservation_id": str(rid), "amount": "50", "method": "Cash", "notes": "", "payment_type": "Payment"}, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/accounting/payment", data={"csrf": csrf, "reservation_id": "", "amount": "20", "method": "Cash"}, follow_redirects=True)
    assert r.status_code == 200
    r = client.post("/accounting/payment", data={"csrf": csrf, "reservation_id": str(rid), "amount": "bad"}, follow_redirects=True)
    assert r.status_code == 200

    # refund success/error (needs valid password/unlock context depending on service; monkeypatch if needed)
    token = set_session(client, unlocked=True)
    # find a payment? easier to monkeypatch record_refund
    import ui.web.routes.accounting as accr
    monkeypatch.setattr(accr.services, "record_refund", lambda *a, **k: 1)
    r = client.post("/accounting/refund", data={"csrf": token, "reservation_id": str(rid), "amount": "5", "method": "Cash", "notes": "", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(accr.services, "record_refund", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope")))
    r = client.post("/accounting/refund", data={"csrf": token, "reservation_id": str(rid), "amount": "5", "password": "x"}, follow_redirects=True)
    assert r.status_code == 200

    # void expense (service return value not used; route calls service with path eid)
    monkeypatch.setattr(accr.services, "void_expense", lambda *a, **k: None)
    r = client.post("/accounting/expense/1/void", data={"csrf": token, "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(accr.services, "void_expense", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    r = client.post("/accounting/expense/1/void", data={"csrf": token, "password": "x"}, follow_redirects=True)
    assert r.status_code == 200


def test_reports_routes(client, web_env, monkeypatch):
    # create files to list
    (web_env["export_dir"] / "sub").mkdir(parents=True, exist_ok=True)
    (web_env["export_dir"] / "sub" / "e1.csv").write_text("1", encoding="utf-8")
    (web_env["invoice_dir"] / "i1.txt").write_text("inv", encoding="utf-8")
    r = client.get("/reports")
    assert r.status_code == 200

    page = client.get("/reports"); csrf = get_csrf(page.text)
    token = set_session(client, unlocked=True, csrf=csrf)
    import ui.web.routes.reports_analytics as rr
    monkeypatch.setattr(rr.services, "report_pl_statement", lambda s,e: "PL")
    monkeypatch.setattr(rr.services, "report_cash_flow", lambda s,e: "CF")
    monkeypatch.setattr(rr.services, "report_vat", lambda s,e: "VAT")
    monkeypatch.setattr(rr.services, "report_payment_methods", lambda s,e: "PM")
    monkeypatch.setattr(rr.services, "report_occupancy", lambda s,e: "OCC")
    for kind in ["pl","cash","vat","payments","occupancy","unknown"]:
        r = client.post("/reports/run", data={"csrf": token, "kind": kind, "start": "2026-01-01", "end": "2026-01-31"})
        assert r.status_code == 200 and ("Report" in r.text or "PL" in r.text or "CF" in r.text or "VAT" in r.text or "PM" in r.text or "OCC" in r.text)

    monkeypatch.setattr(rr.services, "report_pl_statement", lambda s,e: (_ for _ in ()).throw(ValueError("boom")))
    r = client.post("/reports/run", data={"csrf": token, "kind": "pl", "start": "x", "end": "y"})
    assert r.status_code == 303

    monkeypatch.setattr(rr.services, "generate_unified_report", lambda p,s,e: {"outputs": {"a": "b"}})
    r = client.post("/reports/analytics-export", data={"csrf": token, "preset": "management_pack", "start": "2026-01-01", "end": "2026-01-31"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "generate_unified_report", lambda p,s,e: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/reports/analytics-export", data={"csrf": token, "preset": "x"}, follow_redirects=True)
    assert r.status_code == 200
