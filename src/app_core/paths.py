"""Stable filesystem paths for DB and generated artifacts.

Anchors data to project/data locations so moving Python modules does not
relocate the live SQLite DB, exports, or invoices.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _detect_project_root() -> Path:
    # .../repo/src/app_core/paths.py -> repo
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _detect_project_root()


def _detect_data_dir() -> Path:
    env = os.environ.get("PM_DATA_DIR")
    if env:
        raw = str(env).strip()
        if not raw:
            raise ValueError("PM_DATA_DIR is set but empty")
        p = Path(raw).expanduser()
        try:
            p = p.resolve()
        except Exception:
            p = p.absolute()
        # Never point runtime data at filesystem root by accident.
        if p == p.parent:
            raise ValueError(f"PM_DATA_DIR resolves to an unsafe path: {p}")
        p.mkdir(parents=True, exist_ok=True)
        return p
    # Legacy compatibility: if DB/dirs exist at project root, keep using root.
    # This preserves existing runtime data locations across upgrades.
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
    """Ensure runtime data directories exist. Never deletes DB/exports/invoices."""
    global DATA_DIR, DB_PATH, EXPORT_DIR, INVOICE_DIR, BASE_DIR
    for p in (DATA_DIR, EXPORT_DIR, INVOICE_DIR):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"Failed to create runtime data directory '{p}': {e}") from e
    # Keep derived paths consistent if caller refreshed globals externally.
    DB_PATH = DATA_DIR / "property_manager.db"
    EXPORT_DIR = DATA_DIR / "exports"
    INVOICE_DIR = DATA_DIR / "invoices"
    BASE_DIR = DATA_DIR


def is_path_within(path: str | Path, root: str | Path) -> bool:
    """Return True if resolved path is inside resolved root (path confinement helper)."""
    try:
        p = Path(path).expanduser().resolve()
        r = Path(root).expanduser().resolve()
        return p == r or r in p.parents
    except Exception:
        return False


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename_component(value: object, default: str = "file", max_len: int = 120) -> str:
    """Sanitize a value for safe use as a single filename component (no path separators/traversal)."""
    s = str(value or "").strip().replace("\\", "/")
    s = s.rsplit("/", 1)[-1]
    s = s.strip().strip(".")
    s = _SAFE_NAME_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or s in {".", ".."}:
        s = default
    return s[: max(1, int(max_len or 120))]


def safe_child_path(root: str | Path, filename: str, *, must_exist: bool = False) -> Path:
    """Build a confined child path under root from an untrusted filename component."""
    base = Path(root).expanduser().resolve()
    name = sanitize_filename_component(filename, default="file")
    if name in {"", ".", ".."}:
        raise ValueError("Unsafe filename")
    candidate = (base / name).resolve()
    if not is_path_within(candidate, base):
        raise ValueError(f"Path escapes allowed root: {base}")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate
