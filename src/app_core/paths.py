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


def _resolve_existingish(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except Exception:
        try:
            return path.expanduser().absolute()
        except Exception:
            return path


def _detect_data_dir() -> Path:
    env = os.environ.get("PM_DATA_DIR")
    if env:
        raw = str(env).strip()
        if not raw:
            raise ValueError("PM_DATA_DIR is set but empty")
        if "\x00" in raw:
            raise ValueError("PM_DATA_DIR contains NUL byte")
        p = _resolve_existingish(Path(raw))
        # Never point runtime data at filesystem root or unresolved/unsafe targets.
        if p == p.parent:
            raise ValueError(f"PM_DATA_DIR resolves to an unsafe path: {p}")
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise ValueError(f"PM_DATA_DIR is not usable: {p} ({e})") from e
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
    DATA_DIR = _resolve_existingish(Path(DATA_DIR))
    for p in (DATA_DIR, DATA_DIR / "exports", DATA_DIR / "invoices"):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise RuntimeError(f"Failed to create runtime data directory '{p}': {e}") from e
    DB_PATH = DATA_DIR / "property_manager.db"
    EXPORT_DIR = DATA_DIR / "exports"
    INVOICE_DIR = DATA_DIR / "invoices"
    BASE_DIR = DATA_DIR


def check_runtime_permissions() -> dict:
    """Read-only runtime path/permission checks (no mutation, no sensitive data)."""
    try:
        ensure_data_dirs()
    except Exception:
        pass
    checks = {}
    for key, p in [
        ("data_dir", DATA_DIR),
        ("export_dir", EXPORT_DIR),
        ("invoice_dir", INVOICE_DIR),
        ("db_path", DB_PATH),
    ]:
        try:
            pp = Path(p)
            exists = pp.exists()
            is_dir = pp.is_dir() if exists else False
            is_file = pp.is_file() if exists else False
            # For missing DB, writability depends on parent data dir.
            writable_target = pp if exists else pp.parent
            checks[key] = {
                "path": str(pp),
                "exists": exists,
                "is_dir": is_dir,
                "is_file": is_file,
                "readable": os.access(str(pp), os.R_OK) if exists else os.access(str(writable_target), os.R_OK),
                "writable": os.access(str(pp), os.W_OK) if exists else os.access(str(writable_target), os.W_OK),
            }
        except Exception as e:
            checks[key] = {"path": str(p), "exists": False, "error": redact_sensitive(e, 300)}
    # Detect world-writable data dir as a warning signal on POSIX-like systems.
    try:
        mode = os.stat(str(DATA_DIR)).st_mode
        checks["data_dir_mode"] = oct(mode & 0o777)
        checks["data_dir_world_writable"] = bool(mode & 0o002)
    except Exception:
        pass
    return checks


def is_path_within(path: str | Path, root: str | Path) -> bool:
    """Return True if resolved path is inside resolved root (path confinement helper)."""
    try:
        p = _resolve_existingish(Path(path))
        r = _resolve_existingish(Path(root))
        return p == r or r in p.parents
    except Exception:
        return False


# Sensitive-data / log-hygiene helpers (do not log passwords, recovery codes, secrets)
_REDACT_PATTERNS = [
    (re.compile(r'(?i)\b(password|passwd|super[_-]?password|secret|api[_-]?key|token|recovery[_-]?code)\b\s*[:=]\s*([^\s,;]+)'), r'\1=<redacted>'),
    (re.compile(r'(?i)"(password|passwd|super_password|secret|api_key|token|recovery_code)"\s*:\s*"[^"]*"'), r'"\1":"<redacted>"'),
    (re.compile(r'(?i)\b(rcv_[A-Za-z0-9_-]+)\b'), '<redacted-recovery-code>'),
]


def redact_sensitive(text: object, max_len: int = 4000) -> str:
    """Best-effort redaction of secrets/passwords/recovery codes for diagnostics/log text."""
    try:
        s = str(text if text is not None else "")
    except Exception:
        s = "<unprintable>"
    s = s.replace("\x00", "")
    for pat, repl in _REDACT_PATTERNS:
        try:
            s = pat.sub(repl, s)
        except Exception:
            pass
    try:
        ml = max(1, int(max_len or 4000))
    except Exception:
        ml = 4000
    if len(s) > ml:
        s = s[:ml] + "…"
    return s


_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_filename_component(value: object, default: str = "file", max_len: int = 120) -> str:
    """Sanitize a value for safe use as a single filename component (no path separators/traversal)."""
    s = str(value or "").replace("\x00", "").strip().replace("\\", "/")
    s = s.rsplit("/", 1)[-1]
    s = s.strip().strip(".")
    s = _SAFE_NAME_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or s in {".", ".."}:
        s = default
    return s[: max(1, int(max_len or 120))]


def safe_child_path(root: str | Path, filename: str, *, must_exist: bool = False) -> Path:
    """Build a confined child path under root from an untrusted filename component."""
    raw = str(filename or "")
    if "\x00" in raw:
        raise ValueError("Unsafe filename")
    base = _resolve_existingish(Path(root))
    name = sanitize_filename_component(raw, default="file")
    if name in {"", ".", ".."}:
        raise ValueError("Unsafe filename")
    candidate = _resolve_existingish(base / name)
    if not is_path_within(candidate, base):
        raise ValueError(f"Path escapes allowed root: {base}")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate


def runtime_data_summary() -> dict:
    """Non-destructive summary of runtime data locations (for doctor/diagnostics)."""
    try:
        ensure_data_dirs()
    except Exception:
        pass
    return {
        "project_root": str(PROJECT_ROOT),
        "data_dir": str(DATA_DIR),
        "db_path": str(DB_PATH),
        "export_dir": str(EXPORT_DIR),
        "invoice_dir": str(INVOICE_DIR),
        "preserved_on_update_backup_restore": True,
    }
