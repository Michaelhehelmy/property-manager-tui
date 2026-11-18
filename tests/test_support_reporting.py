from __future__ import annotations

import json
from pathlib import Path

import pytest

from app_core import support


def test_support_sanitize_and_helpers(clean_db, monkeypatch):
    assert support._now_iso().endswith("+00:00") or "T" in support._now_iso()
    assert support._is_sensitive_key("password") is True
    assert support._is_sensitive_key("pm_web_secret_key_set") is True
    assert support._is_sensitive_key("page_title") is False

    # redact sensitive strings/patterns
    red = support._redact_string("Bearer abc.def token rcv_secret123")
    assert "<redacted>" in red
    assert "abc.def" not in red

    data = {
        "password": "secret",
        "pm_web_secret_key_set": True,
        "csrf_token": "abc",
        "page_title": "Hello /tmp/private",
        "nested": {"api_key": "x", "ok": 1, "path": Path("/tmp/some/file.txt")},
        "list": [{"secret": "y"}, ("tuple", "v"), {"safe": "Bearer tok"}],
    }
    out = support.sanitize_for_report(data)
    assert out["password"] == "<redacted>"
    assert out["pm_web_secret_key_set"] is True
    assert out["csrf_token"] == "<redacted>"
    assert out["nested"]["api_key"] == "<redacted>"
    assert out["nested"]["ok"] == 1
    assert "file.txt" in out["nested"]["path"]
    assert out["list"][0]["secret"] == "<redacted>"
    assert isinstance(out["list"][1], list)

    # sensitive key hint redacts values
    assert support.sanitize_for_report("raw", "password") == "<redacted>"


def test_support_readiness_doctor_and_collect(clean_db, monkeypatch):
    # avoid network/update side effects in diagnostics/doctor summaries
    monkeypatch.setenv("PM_WEB_SECRET_KEY", "test-secret-key-for-support")
    try:
        from app_core import updater
        monkeypatch.setattr(updater, "get_cached_update_notice", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))
    except Exception:
        pass

    readiness = support._safe_readiness_summary()
    assert "db_initialized" in readiness
    assert "paths" in readiness and "pm_web_secret_key_set" in readiness

    doctor = support._safe_doctor_summary(check_updates=False)
    assert isinstance(doctor, dict)
    assert "ok" in doctor or "error" in doctor

    diag = support.collect_support_diagnostics(
        context={"path": "/x", "page_title": "T", "password": "leak", "error_message": "boom token=abc"},
        include_update_check=False,
    )
    assert diag["report_version"] == support.REPORT_VERSION
    assert "app" in diag and "runtime" in diag and "environment" in diag and "doctor" in diag
    assert diag["client_context"].get("path") == "/x"
    assert "password" not in diag["client_context"]
    assert "redaction" in diag


def test_support_markdown_issue_bundle_and_cli(clean_db, tmp_path, monkeypatch):
    monkeypatch.setenv("PM_WEB_SECRET_KEY", "test-secret-key-for-support")
    try:
        from app_core import updater
        monkeypatch.setattr(updater, "get_cached_update_notice", lambda *a, **k: None)
    except Exception:
        pass

    diag = {"generated_at": "now", "report_version": 1, "ok": True}
    md = support.format_support_report_markdown(
        diag,
        user={
            "title": "Bug title",
            "category": "ui",
            "severity": "high",
            "steps": "1. do x",
            "expected": "a",
            "actual": "b",
            "notes": "n",
            "contact": "me@example.com",
        },
    )
    assert "## Issue report" in md
    assert "Bug title" in md
    assert "### Diagnostics (sanitized)" in md
    assert "```json" in md

    # truncation branches
    big_diag = {"blob": "x" * 13000}
    md_big = support.format_support_report_markdown(big_diag, user={"title": "t"})
    assert "truncated" in md_big

    issue = support.build_github_issue_url(title="T", body="short", labels=["bug", "ui"], repo="owner/repo")
    assert issue["repo"] == "owner/repo"
    assert "github.com/owner/repo/issues/new" in issue["url"]
    assert issue["truncated"] is False

    issue_trunc = support.build_github_issue_url(title="T", body=("x" * 7000), labels=["a"])
    assert issue_trunc["truncated"] is True
    assert "title=" in issue_trunc["url"]

    bundle = support.generate_support_bundle(context={"source": "test"}, user={"title": "Bundle"}, include_update_check=False)
    assert "diagnostics" in bundle and "markdown" in bundle and "issue" in bundle

    # CLI modes
    out_file = tmp_path / "report.md"
    rc = support._cli_main(["--out", str(out_file), "--title", "CLI", "--description", "desc"])
    assert rc == 0 and out_file.exists() and "CLI" in out_file.read_text(encoding="utf-8")

    rc_json = support._cli_main(["--json"])
    assert rc_json == 0

    rc_default = support._cli_main([])
    assert rc_default == 0
