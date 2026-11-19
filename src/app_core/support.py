"""Sanitized support/diagnostics reporting for in-app issue filing and CLI bundles.

Read-only, privacy-first: collects safe operational context, redacts secrets/PII-like
keys, formats a GitHub-friendly report, and builds prefilled issue URLs.
"""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db, paths

REPORT_VERSION = 1
DEFAULT_REPO = "Michaelhehelmy/spms"
DEFAULT_ISSUES_URL = f"https://github.com/{DEFAULT_REPO}/issues/new"

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|token|csrf|cookie|session|authorization|api[_-]?key|apikey|recovery|private[_-]?key|credential)"
)
# best-effort value redaction for obvious secrets/codes
_SENSITIVE_VALUE_PATTERNS = [
    re.compile(r"(?i)\b(rcv_[A-Za-z0-9_-]+)\b"),
    re.compile(r"(?i)\b(Bearer\s+[A-Za-z0-9._\-+/=]+)"),
]

_MAX_URL_BODY = 6000
_MAX_TEXT = 12000


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _redact_string(value: str) -> str:
    s = str(value)
    s = paths.redact_sensitive(s, max_len=_MAX_TEXT)
    for pat in _SENSITIVE_VALUE_PATTERNS:
        try:
            s = pat.sub("<redacted>", s)
        except Exception:
            pass
    return s


def _is_sensitive_key(key: str) -> bool:
    try:
        return bool(_SENSITIVE_KEY_RE.search(str(key or "")))
    except Exception:
        return True


def sanitize_for_report(obj: Any, key_hint: str = "") -> Any:
    """Recursively sanitize data for safe support reports (allow diagnostics, redact secrets)."""
    if _is_sensitive_key(key_hint):
        if obj is None:
            return None
        if isinstance(obj, bool):
            return obj
        # expose only safe boolean-ish presence for common secret flags
        if key_hint.lower().endswith(("_set", "_enabled", "_configured")):
            return bool(obj)
        return "<redacted>"
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _redact_string(obj)
    if isinstance(obj, Path):
        # avoid leaking full sensitive paths; keep basename/indicator style where possible
        try:
            p = Path(obj)
            return str(p.name or p)
        except Exception:
            return "<path>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if _is_sensitive_key(ks):
                # preserve only non-sensitive presence hints
                if ks.lower() in {"pm_web_secret_key_set", "secret_key_env_set", "configured"}:
                    out[ks] = bool(v)
                else:
                    out[ks] = "<redacted>"
            else:
                out[ks] = sanitize_for_report(v, ks)
        return out
    if isinstance(obj, (list, tuple, set)):
        seq = [sanitize_for_report(v, key_hint) for v in list(obj)[:50]]
        return seq
    # fallback: stringified and redacted
    return _redact_string(repr(obj))


def _safe_readiness_summary() -> dict[str, Any]:
    try:
        perm = paths.check_runtime_permissions() if hasattr(paths, "check_runtime_permissions") else {}
    except Exception:
        perm = {}
    def _flags(info):
        if not isinstance(info, dict):
            return {}
        return {
            "exists": bool(info.get("exists")),
            "writable": bool(info.get("writable")),
            "is_dir": bool(info.get("is_dir")),
            "is_file": bool(info.get("is_file")),
        }
    out = {
        "db_initialized": False,
        "paths": {
            "data_dir": _flags(perm.get("data_dir")),
            "db_path": _flags(perm.get("db_path")),
            "export_dir": _flags(perm.get("export_dir")),
            "invoice_dir": _flags(perm.get("invoice_dir")),
        },
        "pm_data_dir_set": bool(os.environ.get("PM_DATA_DIR")),
        "pm_web_secret_key_set": bool(os.environ.get("PM_WEB_SECRET_KEY")),
        "pm_web_cookie_secure": os.environ.get("PM_WEB_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on"),
    }
    try:
        out["db_initialized"] = bool(db.is_initialized()) if hasattr(db, "is_initialized") else False
    except Exception:
        out["db_initialized"] = False
    try:
        out["schema_version"] = getattr(db, "SCHEMA_VERSION", None)
    except Exception:
        pass
    return out


def _safe_doctor_summary(check_updates: bool = False) -> dict[str, Any]:
    """Best-effort compact doctor summary (sanitized, no raw sensitive details)."""
    try:
        from .doctor import collect_health
        h = collect_health(check_updates=bool(check_updates))
    except Exception as e:
        return {"ok": False, "error": paths.redact_sensitive(e, 300)}

    def _summarize_integrity(integ: Any) -> Any:
        if not isinstance(integ, dict):
            return integ
        out = {}
        for k, v in integ.items():
            if isinstance(v, dict):
                # keep booleans/counts/status-like fields, drop large/raw structures
                sub = {}
                for sk, sv in v.items():
                    if sk in {"ok", "checked", "mismatch_count", "ok_integrity", "ok_foreign_keys", "integrity_check"}:
                        if sk == "integrity_check" and isinstance(sv, list):
                            sub[sk] = sv[:3]
                        elif isinstance(sv, (bool, int, float, str)) or sv is None:
                            sub[sk] = sv
                    elif sk in {"error", "integrity_error", "foreign_key_error"} and sv:
                        sub[sk] = paths.redact_sensitive(sv, 200)
                out[k] = sub
            elif isinstance(v, (bool, int, float, str)) or v is None:
                out[k] = v
        return out

    schema_info = h.get("schema") or {}
    tables_info = schema_info.get("tables") or {}
    missing_tables = tables_info.get("missing_tables") or []
    summary = {
        "ok": bool(h.get("ok")),
        "checked_at": h.get("checked_at"),
        "schema": {
            "user_version": schema_info.get("user_version"),
            "expected_version": schema_info.get("expected_version"),
            "matches_expected": schema_info.get("matches_expected"),
            "tables_ok": tables_info.get("ok"),
            "missing_tables_count": len(missing_tables),
        },
        "integrity": _summarize_integrity(h.get("integrity") or {}),
        "config_issues": [],
        "update": {},
        "notes": [paths.redact_sensitive(n, 240) for n in (h.get("notes") or [])[:8]],
    }
    for issue in (h.get("config_issues") or [])[:15]:
        if not isinstance(issue, dict):
            continue
        summary["config_issues"].append({
            "severity": issue.get("severity"),
            "code": issue.get("code"),
            "message": paths.redact_sensitive(issue.get("message"), 240),
        })
    upd = h.get("update") or {}
    local = upd.get("local") or {}
    remote = upd.get("remote") or {}
    summary["update"] = {
        "local": {
            "app_version": local.get("app_version"),
            "branch": local.get("branch"),
            "commit_short": local.get("commit_short"),
            "dirty": local.get("dirty"),
            "is_git_repo": local.get("is_git_repo"),
        },
        "remote": {
            "ok": remote.get("ok"),
            "update_available": remote.get("update_available"),
            "commits_behind": remote.get("commits_behind"),
            "latest": (remote.get("latest") or {}).get("commit_short") if isinstance(remote.get("latest"), dict) else None,
            "error": paths.redact_sensitive(remote.get("error"), 200) if remote.get("error") else None,
        } if remote else None,
        "remote_error": paths.redact_sensitive(upd.get("remote_error"), 200) if upd.get("remote_error") else None,
    }
    return summary


def collect_support_diagnostics(context: dict | None = None, include_update_check: bool = False) -> dict[str, Any]:
    """Collect a sanitized, read-only diagnostics snapshot for issue reporting."""
    ctx = context or {}
    # never trust/echo raw sensitive context values; only allow safe keys
    safe_ctx = {}
    for k in ("path", "page_title", "viewport", "user_agent", "language", "source", "error_code", "error_message"):
        if k in ctx and ctx.get(k) is not None:
            safe_ctx[k] = sanitize_for_report(ctx.get(k), k)

    app_state = {}
    try:
        from .updater import APP_VERSION, get_local_state, get_cached_update_notice
        local = get_local_state(redact_remote=True)
        app_state = {
            "app_version": APP_VERSION,
            "local": {
                "is_git_repo": local.get("is_git_repo"),
                "branch": local.get("branch"),
                "commit_short": local.get("commit_short"),
                "commit_date": local.get("commit_date"),
                "dirty": local.get("dirty"),
            },
            "update_notice": None,
        }
        try:
            notice = get_cached_update_notice(force=False, timeout=3)
            if isinstance(notice, dict):
                app_state["update_notice"] = {
                    "update_available": bool(notice.get("update_available")),
                    "kind": notice.get("kind"),
                    "message": paths.redact_sensitive(notice.get("message"), 200),
                    "latest_release_tag": ((notice.get("latest_release") or {}).get("tag_name") if isinstance(notice.get("latest_release"), dict) else None),
                }
        except Exception:
            app_state["update_notice"] = None
    except Exception as e:
        app_state = {"error": paths.redact_sensitive(e, 200)}

    report = {
        "report_version": REPORT_VERSION,
        "generated_at": _now_iso(),
        "app": sanitize_for_report(app_state),
        "runtime": sanitize_for_report({
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "executable": Path(sys.executable).name if sys.executable else None,
        }),
        "environment": sanitize_for_report(_safe_readiness_summary()),
        "doctor": sanitize_for_report(_safe_doctor_summary(check_updates=include_update_check)),
        "client_context": safe_ctx,
        "redaction": {
            "notes": [
                "Sensitive values (passwords, secrets, tokens, cookies, CSRF/session/recovery codes, API keys) are redacted or excluded.",
                "Database contents, guest/payment/invoice records, and raw request/form bodies are not included.",
                "Review this report before sharing, especially if the issue tracker is public.",
            ]
        },
    }
    return report


def format_support_report_markdown(diag: dict[str, Any], user: dict[str, Any] | None = None) -> str:
    """Format diagnostics + user description as GitHub-friendly Markdown."""
    user = user or {}
    safe_user = {}
    for k in ("title", "category", "severity", "steps", "expected", "actual", "notes", "contact"):
        if user.get(k):
            safe_user[k] = _redact_string(str(user.get(k)))

    lines = []
    lines.append("## Issue report")
    if safe_user.get("title"):
        lines.append(f"**Title:** {safe_user['title']}")
    meta = [f"generated_at: {diag.get('generated_at')}", f"report_version: {diag.get('report_version')}"]
    if safe_user.get("category"):
        meta.append(f"category: {safe_user['category']}")
    if safe_user.get("severity"):
        meta.append(f"severity: {safe_user['severity']}")
    lines.append("- " + "\n- ".join(meta))
    lines.append("")

    for label, key in (("Steps to reproduce", "steps"), ("Expected behavior", "expected"), ("Actual behavior", "actual"), ("Additional notes", "notes")):
        if safe_user.get(key):
            lines.append(f"### {label}")
            lines.append(safe_user[key].rstrip())
            lines.append("")

    lines.append("### Diagnostics (sanitized)")
    lines.append("```json")
    try:
        payload = json.dumps(diag, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        payload = "{}"
    if len(payload) > _MAX_TEXT:
        payload = payload[:_MAX_TEXT] + "\n... (truncated)"
    lines.append(payload)
    lines.append("```")
    lines.append("")
    lines.append("> Review before posting. Remove any remaining sensitive/business data. Attach screenshot manually if needed.")
    text = "\n".join(lines).rstrip() + "\n"
    if len(text) > _MAX_TEXT * 2:
        text = text[: _MAX_TEXT * 2] + "\n... (truncated)\n"
    return text


def build_github_issue_url(title: str = "", body: str = "", labels: list[str] | None = None, repo: str | None = None) -> dict[str, Any]:
    """Build a prefilled GitHub new-issue URL with safe truncation."""
    repo = (repo or DEFAULT_REPO).strip().strip("/")
    base = f"https://github.com/{repo}/issues/new"
    title = _redact_string(title or "").strip()[:120]
    body = body or ""
    truncated = False
    if len(body) > _MAX_URL_BODY:
        body = body[:_MAX_URL_BODY].rstrip() + "\n\n...(report truncated for URL length; paste/download full report and attach screenshot manually)"
        truncated = True
    params = {}
    if title:
        params["title"] = title
    if body:
        params["body"] = body
    if labels:
        clean = [str(l).strip() for l in labels if str(l).strip()]
        if clean:
            params["labels"] = ",".join(clean[:10])
    url = base + ("?" + urllib.parse.urlencode(params) if params else "")
    # Some browsers/servers have URL limits; signal if very large
    if len(url) > 8200:
        # drop body, keep title
        params.pop("body", None)
        url = base + ("?" + urllib.parse.urlencode(params) if params else "")
        truncated = True
    return {"url": url, "truncated": truncated, "repo": repo}


def generate_support_bundle(context: dict | None = None, user: dict[str, Any] | None = None, include_update_check: bool = False) -> dict[str, Any]:
    diag = collect_support_diagnostics(context=context or {}, include_update_check=include_update_check)
    md = format_support_report_markdown(diag, user=user or {})
    issue = build_github_issue_url(
        title=((user or {}).get("title") or ""),
        body=md,
        labels=None,
        repo=DEFAULT_REPO,
    )
    return {"diagnostics": diag, "markdown": md, "issue": issue}


def _cli_main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m app_core.support", description="Generate sanitized support/diagnostics report")
    ap.add_argument("--out", help="Write Markdown report to file path")
    ap.add_argument("--json", dest="as_json", action="store_true", help="Print diagnostics JSON to stdout")
    ap.add_argument("--check-updates", action="store_true", help="Include remote update check (network)")
    ap.add_argument("--title", default="", help="Issue title hint")
    ap.add_argument("--description", default="", help="Issue description/actual behavior")
    args = ap.parse_args(argv)
    bundle = generate_support_bundle(
        context={"source": "cli"},
        user={"title": args.title, "actual": args.description},
        include_update_check=bool(args.check_updates),
    )
    if args.out:
        p = Path(args.out).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(bundle["markdown"], encoding="utf-8")
        print(str(p))
    elif args.as_json:
        print(json.dumps(bundle["diagnostics"], ensure_ascii=False, indent=2))
    else:
        print(bundle["markdown"])
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
