from pathlib import Path
from app_core import updater, paths


def test_get_local_state_basic(tmp_path, monkeypatch):
    # point project root to a non-git dir
    monkeypatch.setattr(updater, "_project_root", lambda: tmp_path)
    state = updater.get_local_state()
    assert state["is_git_repo"] is False
    assert state["app_version"]
    assert "project_root" in state


def test_apply_update_refuses_non_git(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "_project_root", lambda: tmp_path)
    res = updater.apply_update()
    assert res["ok"] is False
    assert "git" in (res.get("error") or "").lower()


def test_check_for_updates_handles_network_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(updater, "_github_get", boom)
    res = updater.check_for_updates()
    assert res["ok"] is False
    assert res["update_available"] is False
    assert res.get("error")
