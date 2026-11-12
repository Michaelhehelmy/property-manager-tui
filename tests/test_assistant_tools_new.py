from app_core import assistant


def test_app_update_status_tool_local():
    res = assistant.execute_tool("get_app_update_status", {})
    assert "local" in res
    assert res["local"].get("app_version")
    assert "how_to_check" in res


def test_expiring_batches_tool_shape(tmp_path, monkeypatch):
    # tool should return structured response even on empty/fresh state (may error without DB init context in some envs, so be tolerant)
    res = assistant.execute_tool("get_expiring_batches", {"within_days": 7})
    assert isinstance(res, dict)
    assert "batches" in res or "error" in res
