"""Coverage tests for app_core.paths (module-level detection + ensure_data_dirs)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from app_core import paths


def _restore_paths_module_state(monkeypatch, tmp_path: Path) -> None:
    """Reload paths with normal env and point aliases to tmp_path-safe defaults for isolation."""
    monkeypatch.delenv("PM_DATA_DIR", raising=False)
    importlib.reload(paths)
    # keep sys.modules alias stable (conftest setdefault)
    sys.modules["paths"] = paths
    sys.modules["app_core.paths"] = paths


def test_detect_data_dir_uses_pm_data_dir_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "custom_data"
    monkeypatch.setenv("PM_DATA_DIR", str(data_dir))
    try:
        importlib.reload(paths)
        assert paths.DATA_DIR == data_dir.resolve()
        assert paths.DB_PATH == data_dir.resolve() / "property_manager.db"
        assert paths.EXPORT_DIR == data_dir.resolve() / "exports"
        assert paths.INVOICE_DIR == data_dir.resolve() / "invoices"
        assert data_dir.exists()  # mkdir side effect (lines 23-25)
    finally:
        _restore_paths_module_state(monkeypatch, tmp_path)


def test_detect_data_dir_legacy_project_root_markers(tmp_path, monkeypatch):
    monkeypatch.delenv("PM_DATA_DIR", raising=False)
    original_project_root = paths.PROJECT_ROOT
    marker_names = {"property_manager.db", "exports", "invoices"}
    orig_exists = Path.exists

    def fake_exists(self: Path) -> bool:  # type: ignore[override]
        if self.name in marker_names and self.parent == original_project_root:
            return True
        return orig_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    try:
        importlib.reload(paths)
        assert paths.DATA_DIR == original_project_root  # line 31
        assert paths.DB_PATH == original_project_root / "property_manager.db"
    finally:
        monkeypatch.setattr(Path, "exists", orig_exists)
        _restore_paths_module_state(monkeypatch, tmp_path)


def test_detect_data_dir_defaults_to_project_data(tmp_path, monkeypatch):
    monkeypatch.delenv("PM_DATA_DIR", raising=False)
    # No legacy markers simulated; ensure default branch creates data/ under project root
    try:
        importlib.reload(paths)
        assert paths.DATA_DIR == paths.PROJECT_ROOT / "data"
        assert paths.DATA_DIR.exists()
        assert paths.BASE_DIR == paths.DATA_DIR
    finally:
        _restore_paths_module_state(monkeypatch, tmp_path)


def test_ensure_data_dirs_creates_paths(tmp_path, monkeypatch):
    # Point globals to an isolated temp location and exercise mkdirs (lines 46-48)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "d")
    monkeypatch.setattr(paths, "EXPORT_DIR", tmp_path / "d" / "exports")
    monkeypatch.setattr(paths, "INVOICE_DIR", tmp_path / "d" / "invoices")
    # remove if any
    if (tmp_path / "d").exists():
        for p in sorted((tmp_path / "d").rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        (tmp_path / "d").rmdir()
    paths.ensure_data_dirs()
    assert paths.DATA_DIR.exists()
    assert paths.EXPORT_DIR.exists()
    assert paths.INVOICE_DIR.exists()
