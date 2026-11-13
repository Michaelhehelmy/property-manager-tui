"""In-app update checks and safe self-update (git-based installs).

Preserves runtime/user data: DB, exports, invoices, and configured data dirs live
under paths.DATA_DIR / PM_DATA_DIR and are ignored or untracked by git in normal
installs. Updates only fast-forward tracked source files.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

APP_VERSION = "0.1.0-dev"
REPO_SLUG = "Michaelhehelmy/property-manager-tui"
REPO_URL = f"https://github.com/{REPO_SLUG}"
API_COMMITS_URL = f"https://api.github.com/repos/{REPO_SLUG}/commits"
DEFAULT_BRANCH = "main"
USER_AGENT = "property-manager-tui-updater"
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")


def _validate_ref(name: str, value: str) -> str:
    v = str(value or "").strip()
    if not v or not _SAFE_REF_RE.match(v) or v.startswith("-") or ".." in v or v.endswith(".lock"):
        raise ValueError(f"Invalid {name}: {value!r}")
    return v


def _project_root() -> Path:
    try:
        return Path(paths.PROJECT_ROOT)
    except Exception:
        return Path(__file__).resolve().parents[2]


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = 20) -> tuple[int, str, str]:
    cwd = cwd or _project_root()
    try:
        t = max(1, min(int(timeout or 20), 300))
    except Exception:
        t = 20
    env = os.environ.copy()
    # non-interactive, no prompts/pagers; avoids hangs in TUI/web contexts
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_PAGER", "cat")
    try:
        p = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
            env=env,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"git command timed out after {t}s"
    except Exception as e:
        return 1, "", str(e)


def _redact_remote_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        u = str(url)
        # redact credentials in URLs like https://user:token@host/repo.git
        u = re.sub(r"(https?://)([^/@\s]+)@", r"\1<redacted>@", u)
        u = re.sub(r"(ssh://)([^/@\s]+)@", r"\1<redacted>@", u)
        return u
    except Exception:
        return "<redacted>"


def get_local_state(redact_remote: bool = True) -> dict[str, Any]:
    root = _project_root()
    state: dict[str, Any] = {
        "app_version": APP_VERSION,
        "project_root": str(root),
        "is_git_repo": (root / ".git").exists(),
        "branch": None,
        "commit": None,
        "commit_short": None,
        "commit_date": None,
        "commit_subject": None,
        "dirty": None,
        "remote": None,
        "data_dir": str(getattr(paths, "DATA_DIR", "")),
        "db_path": str(getattr(paths, "DB_PATH", "")),
    }
    if not state["is_git_repo"]:
        return state
    rc, out, _ = _run_git(["rev-parse", "HEAD"])
    if rc == 0 and out:
        state["commit"] = out
        state["commit_short"] = out[:7]
    rc, out, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        state["branch"] = out or None
    rc, out, _ = _run_git(["log", "-1", "--format=%ci|%s"])
    if rc == 0 and out:
        parts = out.split("|", 1)
        state["commit_date"] = parts[0].strip() if parts else None
        state["commit_subject"] = parts[1].strip() if len(parts) > 1 else None
    rc, out, _ = _run_git(["status", "--porcelain"])
    if rc == 0:
        state["dirty"] = bool(out.strip())
    rc, out, _ = _run_git(["remote", "get-url", "origin"])
    if rc == 0:
        state["remote"] = _redact_remote_url(out) if redact_remote else (out or None)
    return state


def _github_get(url: str, timeout: int = 12) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
        },
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    # Hard timeout to avoid hanging on network issues
    t = max(1, min(int(timeout or 12), 30))
    with urllib.request.urlopen(req, timeout=t) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    return json.loads(data)


def check_for_updates(branch: str = DEFAULT_BRANCH, limit: int = 20, timeout: int = 12) -> dict[str, Any]:
    """Check GitHub for newer commits and return changelog/details. Read-only."""
    local = get_local_state()
    result: dict[str, Any] = {
        "ok": True,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "local": local,
        "remote_branch": branch,
        "update_available": False,
        "latest": None,
        "commits_behind": None,
        "changelog": [],
        "compare_url": None,
        "error": None,
        "notes": [],
    }
    result["notes"].append("Runtime data (DB, exports, invoices, PM_DATA_DIR/DATA_DIR) is preserved during updates/backups/restores.")
    try:
        branch = _validate_ref("branch", branch)
        result["remote_branch"] = branch
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        return result
    try:
        timeout = max(2, min(int(timeout or 12), 30))
    except Exception:
        timeout = 12
    local_commit = local.get("commit")
    try:
        safe_limit = max(1, min(int(limit or 20), 100))
        q = urllib.parse.urlencode({"sha": branch, "per_page": str(safe_limit)})
        commits = _github_get(f"{API_COMMITS_URL}?{q}", timeout=timeout)
    except Exception as e:
        result["ok"] = False
        result["error"] = f"Could not check for updates: {e}"
        result["notes"].append("Update check needs network access to GitHub (or configure GITHUB_TOKEN/GH_TOKEN if rate-limited).")
        if not local.get("is_git_repo"):
            result["notes"].append("Not a git-based install: auto-update/apply is unavailable; reinstall from the latest release/installer to update source while preserving runtime data.")
        return result

    if not isinstance(commits, list) or not commits:
        result["ok"] = False
        result["error"] = "No commits returned from GitHub."
        return result

    latest = commits[0] if isinstance(commits[0], dict) else {}
    latest_sha = latest.get("sha")
    latest_info = {
        "commit": latest_sha,
        "commit_short": (latest_sha or "")[:7] if latest_sha else None,
        "date": (((latest.get("commit") or {}).get("author") or {}).get("date")),
        "subject": (((latest.get("commit") or {}).get("message") or "").splitlines() or [""])[0],
        "url": latest.get("html_url"),
    }
    result["latest"] = latest_info
    if local_commit and latest_sha:
        result["compare_url"] = f"{REPO_URL}/compare/{local_commit[:7]}...{latest_sha[:7]}"

    # Build changelog: commits from newest down to (but not including) local commit, if found.
    changelog = []
    found_local = False
    for c in commits:
        if not isinstance(c, dict):
            continue
        sha = c.get("sha") or ""
        if local_commit and sha == local_commit:
            found_local = True
            break
        msg = (c.get("commit") or {}).get("message") or ""
        subject = (msg.splitlines() or [""])[0].strip()
        body_lines = [ln.rstrip() for ln in (msg.splitlines()[1:] if len(msg.splitlines()) > 1 else []) if ln.strip()]
        changelog.append({
            "commit": sha,
            "commit_short": sha[:7] if sha else "",
            "date": (((c.get("commit") or {}).get("author") or {}).get("date")),
            "author": (((c.get("commit") or {}).get("author") or {}).get("name")),
            "subject": subject,
            "details": body_lines[:6],
            "url": c.get("html_url"),
        })

    if local_commit and latest_sha and local_commit == latest_sha:
        result["update_available"] = False
        result["commits_behind"] = 0
        result["changelog"] = []
        result["notes"].append("You are up to date.")
    elif local_commit and found_local:
        result["update_available"] = len(changelog) > 0
        result["commits_behind"] = len(changelog)
        result["changelog"] = changelog
    elif local_commit and not found_local:
        # Local commit not in recent history: could be ahead, diverged, or far behind.
        result["update_available"] = True if latest_sha != local_commit else False
        result["commits_behind"] = None
        result["changelog"] = changelog  # recent commits as context
        result["notes"].append(
            "Local version not found in recent remote history (ahead, diverged, or many commits behind). Review changes before updating."
        )
    else:
        result["update_available"] = bool(latest_sha)
        result["commits_behind"] = None
        result["changelog"] = changelog
        result["notes"].append("Local git commit unknown; showing recent remote changes.")

    return result


def apply_update(branch: str = DEFAULT_BRANCH) -> dict[str, Any]:
    """Safely update a git-based install (fast-forward only). Preserves data dirs/DB."""
    local = get_local_state()
    root = _project_root()
    res: dict[str, Any] = {
        "ok": False,
        "steps": [],
        "local_before": local,
        "local_after": None,
        "error": None,
        "restart_required": False,
        "preserved": [
            str(getattr(paths, "DATA_DIR", "")),
            "property_manager.db / exports / invoices (ignored runtime data)",
        ],
        "notes": [
            "Update uses 'git pull --ff-only' and does not modify ignored runtime data (DB, exports, invoices).",
            "Restart the app after a successful update.",
            "Run backup/doctor checks before and after updates in production-like environments.",
        ],
    }
    try:
        branch = _validate_ref("branch", branch)
    except Exception as e:
        res["error"] = str(e)
        return res
    if not local.get("is_git_repo"):
        res["error"] = "Not a git installation; cannot auto-update. Reinstall using the latest installer/scripts."
        return res
    if local.get("dirty"):
        res["error"] = "Local source has uncommitted changes. Commit/stash/discard them before updating to avoid conflicts."
        return res
    if local.get("branch") and local.get("branch") not in ("HEAD", branch):
        res["notes"].append(f"Current branch is '{local.get('branch')}'; update will fetch/pull origin/{branch} via fast-forward only.")

    for name, args in [
        ("fetch", ["fetch", "--prune", "origin", branch, "--tags"]),
        ("pull", ["pull", "--ff-only", "origin", branch]),
    ]:
        rc, out, err = _run_git(args, cwd=root, timeout=180)
        res["steps"].append({"step": name, "rc": rc, "stdout": out, "stderr": err})
        if rc != 0:
            msg = err or out or f"{name} failed"
            res["error"] = f"Update failed during {name}: {msg}"
            res["local_after"] = get_local_state()
            return res

    res["ok"] = True
    res["restart_required"] = True
    res["local_after"] = get_local_state()
    return res
