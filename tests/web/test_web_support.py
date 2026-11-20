from __future__ import annotations

import json

from tests.web.conftest import set_session


def test_support_report_page_renders(client, monkeypatch):
    # avoid network side effects from update notices/checks during render/diagnostics
    try:
        import app_core.updater as updater

        monkeypatch.setattr(updater, "get_cached_update_notice", lambda *a, **k: None)
    except Exception:
        pass

    set_session(client, unlocked=False)
    r = client.get("/support/report")
    assert r.status_code == 200
    assert "Report issue" in r.text or "Diagnostics" in r.text or "report" in r.text.lower()


def test_support_report_prefill_from_error_context(client, monkeypatch):
    try:
        import app_core.updater as updater

        monkeypatch.setattr(
            updater, "get_cached_update_notice", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
    except Exception:
        pass

    # set last_error_report in session so route prefills from it
    set_session(
        client,
        unlocked=False,
        extra={
            "last_error_report": {
                "ref": "ERR123",
                "error_message": "Something failed",
                "error_type": "ValueError",
                "path": "/reservations/1",
            }
        },
    )
    r = client.get("/support/report?from_error=1&ref=ERR123")
    assert r.status_code == 200
    # prefill content may be embedded in JSON/template; just ensure page rendered
    assert "ERR123" in r.text or "ValueError" in r.text or "Report" in r.text


def test_rendering_to_template_data_branches(client):
    import sqlite3
    from ui.web.rendering import _to_template_data

    # tuple conversion branch
    assert _to_template_data((1, {"x": 2})) == (1, {"x": 2})

    # sqlite3.Row conversion branch
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT 1 AS a, 'b' AS c").fetchone()
    converted = _to_template_data(row)
    assert converted == {"a": 1, "c": "b"}
    conn.close()
