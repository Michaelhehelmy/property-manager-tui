"""Stable filesystem paths for DB and generated artifacts.

Anchors data to project/data locations so moving Python modules does not
relocate the live SQLite DB, exports, or invoices.
"""
from __future__ import annotations

import os
from pathlib import Path


def _detect_project_root() -> Path:
    # .../repo/src/app_core/paths.py -> repo
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _detect_project_root()


def _detect_data_dir() -> Path:
    env = os.environ.get("PM_DATA_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    # Legacy compatibility: if DB/dirs exist at project root, keep using root.
    root_db = PROJECT_ROOT / "property_manager.db"
    root_exports = PROJECT_ROOT / "exports"
    root_invoices = PROJECT_ROOT / "invoices"
    if root_db.exists() or root_exports.exists() or root_invoices.exists():
        return PROJECT_ROOT
    data = PROJECT_ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    return data


DATA_DIR = _detect_data_dir()
DB_PATH = DATA_DIR / "property_manager.db"
EXPORT_DIR = DATA_DIR / "exports"
INVOICE_DIR = DATA_DIR / "invoices"
BASE_DIR = DATA_DIR  # backward-compatible alias for code expecting BASE_DIR


def ensure_data_dirs() -> None:
    global DATA_DIR, DB_PATH, EXPORT_DIR, INVOICE_DIR, BASE_DIR
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
