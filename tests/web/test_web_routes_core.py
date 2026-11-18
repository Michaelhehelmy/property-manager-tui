from __future__ import annotations

from pathlib import Path

from app_core import services
from app_core.db import connect
from tests.web.conftest import get_csrf, set_session


def test_dashboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Dashboard" in r.text


def test_files_download_requires_unlock(client, web_env):
    # create file
    f = web_env["export_dir"] / "r1.txt"
    f.write_text("ok", encoding="utf-8")
    r = client.get("/files/export/r1.txt")
    assert r.status_code == 303
    assert "/unlock?next=" in r.headers["location"]

    token = set_session(client, unlocked=True)
    r = client.get("/files/export/r1.txt")
    assert r.status_code == 200
    assert r.text == "ok"

    r = client.get("/files/invoice/nope.txt")
    assert r.status_code == 404


def test_guests_list_detail_update(client):
    gid, _, _ = services.find_or_create_guest("Alice", "123", "a@b.com")
    r = client.get("/guests")
    assert r.status_code == 200 and "Alice" in r.text
    r = client.get("/guests?q=Ali")
    assert "Alice" in r.text
    r = client.get(f"/guests/{gid}")
    assert r.status_code == 200 and "Alice" in r.text

    r = client.get("/guests/999999")
    assert r.status_code == 303
    assert r.headers["location"] == "/guests?tab=directory"

    # update requires unlock
    set_session(client, unlocked=False)
    page = client.get(f"/guests/{gid}")
    csrf = get_csrf(page.text) or "x"
    r = client.post(f"/guests/{gid}/update", data={"csrf": csrf, "name": "A2", "phone": "", "email": "", "notes": "", "password": "testpass"})
    assert r.status_code == 303  # require_unlock redirect

    token = set_session(client, unlocked=True)
    r = client.post(f"/guests/{gid}/update", data={"csrf": token, "name": "A2", "phone": "9", "email": "", "notes": "n", "password": "testpass"}, follow_redirects=True)
    assert r.status_code == 200
    assert ("updated" in r.text.lower() or "a2" in r.text.lower() or "A2" in r.text)

    # update error path
    token = set_session(client, unlocked=True)
    r = client.post(f"/guests/{gid}/update", data={"csrf": token, "name": "", "phone": "", "email": "", "notes": "", "password": "wrong"}, follow_redirects=True)
    assert r.status_code == 200
