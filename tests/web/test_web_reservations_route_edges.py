from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app_core import db, services
from tests.web.conftest import get_csrf, set_session


def _pick_room(index=0):
    rooms = services.get_all_rooms()
    r = rooms[index if index < len(rooms) else 0]
    rid = r["id"] if isinstance(r, dict) or hasattr(r, "__getitem__") else getattr(r, "id")
    return int(rid)


def _make_reservation(ci="2099-06-01", co="2099-06-03", guest="Res Edge Guest", room_index=0):
    return services.create_reservation(guest, "Direct", _pick_room(room_index), None, 100.0, ci, co, 0.0)


def test_enrich_pos_rows_and_list_variants(client, monkeypatch):
    import ui.web.routes.reservations as rr

    # _enrich_pos_rows: expiry_warn_days read failure and batch enrichment/no-batch paths
    monkeypatch.setattr(
        rr.services,
        "get_setting",
        lambda k, d=None: (_ for _ in ()).throw(RuntimeError("x")) if k == "expiry_warn_days" else d,
    )
    rows = [
        {"id": 1, "batch_id": None},
        {"id": 2, "batch_id": 999999},  # missing batch map
    ]
    out = rr._enrich_pos_rows(rows)
    assert isinstance(out, list) and len(out) == 2

    # list page with filters/search/tab normalization and quote param parsing errors
    r = client.get("/reservations?q=guest&source=Direct&segment=Corp&tab=create")
    assert r.status_code == 200
    r = client.get("/reservations?tab=unknown&quote_error=bad")
    assert r.status_code == 200 and "Reservations" in r.text
    r = client.get("/reservations?quote=1&quote_json={badjson}")
    assert r.status_code == 200


def test_reservation_detail_fallbacks_and_modify_quote_parse(client, monkeypatch):
    import ui.web.routes.reservations as rr

    rid = _make_reservation()
    # force detail loaders to raise to exercise exception fallback blocks
    monkeypatch.setattr(rr.services, "list_activities", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a")))
    monkeypatch.setattr(rr.services, "get_inventory_items", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("i")))
    monkeypatch.setattr(rr.services, "list_inventory_batches", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("b")))
    monkeypatch.setattr(
        rr.services, "list_reservation_services", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("s"))
    )
    monkeypatch.setattr(rr.services, "list_service_catalog", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("c")))
    monkeypatch.setattr(
        rr.services, "list_reservation_discounts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("d"))
    )
    monkeypatch.setattr(rr.services, "list_packages", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("p")))
    monkeypatch.setattr(
        rr.services, "list_reservation_packages", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rp"))
    )
    monkeypatch.setattr(
        rr.services, "get_reservation_group_membership", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("g"))
    )

    r = client.get(f"/reservations/{rid}?modify_quote=1&modify_quote_json={json.dumps({'ok': True})}&m_room_id=1")
    assert r.status_code == 200
    r = client.get(f"/reservations/{rid}?modify_quote=1&modify_quote_json=not-json&modify_quote_error=oops")
    assert r.status_code == 200


def test_group_routes_success_and_errors(client, monkeypatch):
    import ui.web.routes.reservations as rr

    token = set_session(client, unlocked=True)

    # list groups exception fallback
    monkeypatch.setattr(
        rr.services, "list_reservation_groups", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    r = client.get("/reservations/groups")
    assert r.status_code == 200 and "Group" in r.text
    monkeypatch.setattr(rr.services, "list_reservation_groups", services.list_reservation_groups)

    # create/update/add/remove/payment/folio flows and errors
    gid = services.create_reservation_group("Edge Group", group_type="group")
    rid = _make_reservation(ci="2099-07-01", co="2099-07-03", guest="G1", room_index=0)
    rid2 = (
        _make_reservation(ci="2099-07-01", co="2099-07-03", guest="G2", room_index=1)
        if len(services.get_all_rooms()) > 1
        else rid
    )

    r = client.post("/reservations/groups/create", data={"csrf": token, "name": "New G"}, follow_redirects=True)
    assert r.status_code == 200

    r = client.post(
        f"/reservations/groups/{gid}/add",
        data={"csrf": token, "reservation_id": str(rid), "role": "member"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    r = client.post(
        f"/reservations/groups/{gid}/update",
        data={"csrf": token, "name": "Edge Group 2", "status": "active"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    # group payment validation branches
    r = client.post(
        f"/reservations/groups/{gid}/payment",
        data={"csrf": token, "reservation_id": "", "amount": "10"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "get_reservation_group", lambda gid_: None)
    r = client.post(
        f"/reservations/groups/{gid}/payment",
        data={"csrf": token, "reservation_id": str(rid), "amount": "10"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    # member-not-in-group
    monkeypatch.setattr(
        rr.services, "get_reservation_group", lambda gid_: {"id": gid, "members": [{"reservation_id": 999999}]}
    )
    r = client.post(
        f"/reservations/groups/{gid}/payment",
        data={"csrf": token, "reservation_id": str(rid), "amount": "10"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "get_reservation_group", services.get_reservation_group)

    # folio not found / error
    monkeypatch.setattr(rr.services, "get_reservation_group", lambda gid_: None)
    r = client.get(f"/reservations/groups/{gid}/folio", follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "get_reservation_group", lambda gid_: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.get(f"/reservations/groups/{gid}/folio", follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(rr.services, "get_reservation_group", services.get_reservation_group)

    # remove and error branches for add/remove/update/create
    r = client.post(f"/reservations/groups/{gid}/remove/{rid}", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(
        rr.services, "add_reservation_to_group", lambda *a, **k: (_ for _ in ()).throw(ValueError("add fail"))
    )
    r = client.post(
        f"/reservations/groups/{gid}/add", data={"csrf": token, "reservation_id": str(rid2)}, follow_redirects=True
    )
    assert r.status_code == 200
    monkeypatch.setattr(
        rr.services, "remove_reservation_from_group", lambda *a, **k: (_ for _ in ()).throw(ValueError("rem fail"))
    )
    r = client.post(f"/reservations/groups/{gid}/remove/{rid2}", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(
        rr.services, "update_reservation_group", lambda *a, **k: (_ for _ in ()).throw(ValueError("upd fail"))
    )
    r = client.post(f"/reservations/groups/{gid}/update", data={"csrf": token, "name": "x"}, follow_redirects=True)
    assert r.status_code == 200
    monkeypatch.setattr(
        rr.services, "create_reservation_group", lambda *a, **k: (_ for _ in ()).throw(ValueError("create fail"))
    )
    r = client.post("/reservations/groups/create", data={"csrf": token, "name": "Bad"}, follow_redirects=True)
    assert r.status_code == 200


def test_reservation_action_error_and_void_payment_branches(client, monkeypatch, tmp_path):
    import ui.web.routes.reservations as rr

    token = set_session(client, unlocked=True)
    rid = _make_reservation(ci="2099-08-01", co="2099-08-03", guest="Act Guest")

    # void payment branches: not found, wrong reservation, already voided
    r = client.post(f"/reservations/{rid}/payments/999999/void", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    # create a payment for another reservation then try to void via wrong rid
    rid2 = _make_reservation(ci="2099-08-05", co="2099-08-06", guest="Other")
    pid = None
    try:
        pid = services.record_payment(rid2, 10.0, "Cash", notes="x", payment_type="Payment")
    except Exception:
        pass
    if pid:
        r = client.post(f"/reservations/{rid}/payments/{pid}/void", data={"csrf": token}, follow_redirects=True)
        assert r.status_code == 200

    # already voided (or just successful void path)
    try:
        pid2 = services.record_payment(rid, 15.0, "Cash", notes="y", payment_type="Payment")
        db.connect().execute("UPDATE payments SET voided_at=datetime('now','localtime') WHERE id=?", (int(pid2),))
        db.connect().commit()
        r = client.post(f"/reservations/{rid}/payments/{pid2}/void", data={"csrf": token}, follow_redirects=True)
        assert r.status_code == 200
    except Exception:
        pass

    # refund/invoice/book-activity/service/discount/package error branches
    monkeypatch.setattr(rr.services, "record_refund", lambda *a, **k: (_ for _ in ()).throw(ValueError("refund fail")))
    r = client.post(
        f"/reservations/{rid}/refund", data={"csrf": token, "amount": "5", "method": "Cash"}, follow_redirects=True
    )
    assert r.status_code == 200

    monkeypatch.setattr(
        rr.services, "generate_invoice_text", lambda *a, **k: (_ for _ in ()).throw(ValueError("inv fail"))
    )
    r = client.post(f"/reservations/{rid}/invoice", data={"csrf": token, "currency": "EGP"}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(rr.services, "book_activity", lambda *a, **k: (_ for _ in ()).throw(ValueError("act fail")))
    r = client.post(
        f"/reservations/{rid}/book-activity",
        data={"csrf": token, "activity_id": "1", "qty": "1"},
        follow_redirects=True,
    )
    assert r.status_code == 200

    monkeypatch.setattr(
        rr.services, "add_reservation_service", lambda *a, **k: (_ for _ in ()).throw(ValueError("svc fail"))
    )
    r = client.post(
        f"/reservations/{rid}/services/add",
        data={"csrf": token, "description": "x", "quantity": "1"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    monkeypatch.setattr(
        rr.services, "void_reservation_service", lambda *a, **k: (_ for _ in ()).throw(ValueError("vsvc fail"))
    )
    r = client.post(f"/reservations/{rid}/services/1/void", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(
        rr.services, "apply_reservation_discount", lambda *a, **k: (_ for _ in ()).throw(ValueError("disc fail"))
    )
    r = client.post(
        f"/reservations/{rid}/discounts/apply",
        data={"csrf": token, "discount_type": "amount", "value": "5"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    monkeypatch.setattr(
        rr.services, "void_reservation_discount", lambda *a, **k: (_ for _ in ()).throw(ValueError("vdisc fail"))
    )
    r = client.post(f"/reservations/{rid}/discounts/1/void", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200

    monkeypatch.setattr(
        rr.services, "apply_package_to_reservation", lambda *a, **k: (_ for _ in ()).throw(ValueError("pkg fail"))
    )
    r = client.post(
        f"/reservations/{rid}/packages/apply", data={"csrf": token, "package_id": "1"}, follow_redirects=True
    )
    assert r.status_code == 200
    monkeypatch.setattr(
        rr.services, "void_reservation_package", lambda *a, **k: (_ for _ in ()).throw(ValueError("vpkg fail"))
    )
    r = client.post(f"/reservations/{rid}/packages/1/void", data={"csrf": token}, follow_redirects=True)
    assert r.status_code == 200
