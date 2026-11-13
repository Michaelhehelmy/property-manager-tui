from __future__ import annotations

from pathlib import Path

from tests.web.conftest import get_csrf, set_session


def test_reservations_list_create_detail(client, monkeypatch):
    import ui.web.routes.reservations as rr
    r = client.get("/reservations")
    assert r.status_code == 200
    r = client.get("/reservations?q=guest")
    assert r.status_code == 200

    page = client.get("/reservations"); csrf = get_csrf(page.text)
    monkeypatch.setattr(rr.services, "create_reservation", lambda *a, **k: 123)
    r = client.post("/reservations/create", data={"csrf": csrf, "guest_name": "G", "source": "Direct", "room_id": "1", "rate_plan_id": "2", "rate": "100", "check_in": "2026-12-01", "check_out": "2026-12-02", "deposit": "0"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "create_reservation", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    r = client.post("/reservations/create", data={"csrf": csrf, "guest_name": "G", "room_id": "1", "rate": "100", "check_in": "x", "check_out": "y"}, follow_redirects=True)
    assert r.status_code == 200
    # rate_plan_id empty branch
    monkeypatch.setattr(rr.services, "create_reservation", lambda *a, **k: 124)
    r = client.post("/reservations/create", data={"csrf": csrf, "guest_name": "G", "room_id": "1", "rate_plan_id": "", "rate": "100", "check_in": "2026-12-01", "check_out": "2026-12-02"}, follow_redirects=True)
    assert r.status_code == 200

    # detail not found
    monkeypatch.setattr(rr.services, "get_reservation", lambda rid: None)
    r = client.get("/reservations/999")
    assert r.status_code == 303
    # detail found
    monkeypatch.setattr(rr.services, "get_reservation", lambda rid: {"id": rid, "guest_name": "G", "status": "Reserved"})
    monkeypatch.setattr(rr.services, "get_balance_breakdown", lambda rid: {"balance": 0})
    r = client.get("/reservations/1")
    assert r.status_code == 200


def test_reservation_actions(client, monkeypatch):
    import ui.web.routes.reservations as rr
    token = set_session(client, unlocked=True)
    # checkin via _act
    monkeypatch.setattr(rr.services, "update_reservation_status", lambda *a, **k: None)
    r = client.post("/reservations/1/checkin", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "update_reservation_status", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/reservations/1/checkin", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    # checkout: needs override
    monkeypatch.setattr(rr.services, "checkout_requires_override", lambda rid: (True, 50.0, None))
    monkeypatch.setattr(rr.services, "fmt", lambda v: str(v))
    r = client.post("/reservations/1/checkout", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200
    # checkout success
    monkeypatch.setattr(rr.services, "checkout_requires_override", lambda rid: (False, 0.0, None))
    monkeypatch.setattr(rr.services, "checkout_reservation", lambda *a, **k: None)
    r = client.post("/reservations/1/checkout", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200
    # checkout error
    monkeypatch.setattr(rr.services, "checkout_requires_override", lambda rid: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/reservations/1/checkout", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    # checkout override
    monkeypatch.setattr(rr.services, "checkout_reservation", lambda *a, **k: None)
    r = client.post("/reservations/1/checkout-override", data={"csrf": token, "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "checkout_reservation", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/reservations/1/checkout-override", data={"csrf": token, "password": "x"}, follow_redirects=True)
    assert r.status_code == 200

    # cancel, noshow
    for ep, svc in [("cancel", "cancel_reservation"), ("noshow", "mark_no_show")]:
        monkeypatch.setattr(rr.services, svc, lambda *a, **k: None)
        r = client.post(f"/reservations/1/{ep}", data={"csrf": token, "password": "testpass"}, follow_redirects=True)
        assert r.status_code == 200
        monkeypatch.setattr(rr.services, svc, lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        r = client.post(f"/reservations/1/{ep}", data={"csrf": token, "password": "x"}, follow_redirects=True)
        assert r.status_code == 200

    # delete success -> /reservations, error -> /reservations/{rid}
    monkeypatch.setattr(rr.services, "delete_reservation", lambda *a, **k: None)
    r = client.post("/reservations/1/delete", data={"csrf": token, "password": "testpass"})
    assert r.status_code == 303 and r.headers["location"] == "/reservations?tab=list"
    monkeypatch.setattr(rr.services, "delete_reservation", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/reservations/1/delete", data={"csrf": token, "password": "x"})
    assert r.status_code == 303 and r.headers["location"] == "/reservations/1"

    # modify, pay, refund
    for ep, svc, data in [
        ("modify", "modify_reservation", {"rate":"1","check_in":"a","check_out":"b","room_id":"1","password":"p"}),
        ("pay", "record_payment", {"amount":"1","method":"Cash","notes":"","payment_type":"Payment"}),
        ("refund", "record_refund", {"amount":"1","method":"Cash","notes":"","password":"p"}),
    ]:
        monkeypatch.setattr(rr.services, svc, lambda *a, **k: None)
        r = client.post(f"/reservations/1/{ep}", data={"csrf": token, **data}, follow_redirects=True)
        assert r.status_code == 200
        monkeypatch.setattr(rr.services, svc, lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        r = client.post(f"/reservations/1/{ep}", data={"csrf": token, **data}, follow_redirects=True)
        assert r.status_code == 200


def test_reservation_invoice(client, monkeypatch, web_env):
    import ui.web.routes.reservations as rr
    token = set_session(client, unlocked=True)
    inv_path = web_env["invoice_dir"] / "inv1.txt"
    inv_path.write_text("invoice", encoding="utf-8")
    monkeypatch.setattr(rr.services, "generate_invoice_text", lambda *a, **k: "text")
    monkeypatch.setattr(rr.services, "save_invoice_file", lambda text, rid, cur: str(inv_path))
    r = client.post("/reservations/1/invoice", data={"csrf": token, "currency": "EGP"})
    assert r.status_code == 303 and r.headers["location"] == "/files/invoice/inv1.txt"

    monkeypatch.setattr(rr.services, "generate_invoice_text", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = client.post("/reservations/1/invoice", data={"csrf": token, "currency": "EGP"})
    assert r.status_code == 303 and r.headers["location"] == "/reservations/1"
