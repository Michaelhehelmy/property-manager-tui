#!/usr/bin/env python3
"""Unified entry point for SPMS Web UI.

Web-only launcher with safe defaults (SQLite requires --workers 1).
Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved across updates/restarts.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import sys
import time
import traceback
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def _env() -> dict[str, str]:
    env = os.environ.copy()
    pp = env.get("PYTHONPATH", "")
    parts = [str(SRC), str(ROOT)]
    if pp:
        for p in pp.split(os.pathsep):
            if p and p not in parts:
                parts.append(p)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _clear():
    try:
        if os.name == "nt":
            os.system("cls")
        else:
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
    except Exception:
        pass


def _web_deps_ok() -> tuple[bool, str]:
    missing = []
    for mod in ("fastapi", "uvicorn", "jinja2"):
        try:
            __import__(mod)
        except Exception:
            missing.append(mod)
    if missing:
        return False, "Missing web dependencies: " + ", ".join(missing)
    return True, "ok"


def _validate_host(host: str) -> str:
    h = (host or "127.0.0.1").strip()
    if not h:
        return "127.0.0.1"
    allowed_extra = set("._:-[]")
    if any(not (ch.isalnum() or ch in allowed_extra) for ch in h):
        raise ValueError("Invalid host")
    return h


def _validate_port(port: int) -> int:
    p = int(port)
    if p < 1 or p > 65535:
        raise ValueError("Port out of range")
    return p


def _port_available(host: str, port: int) -> bool:
    bind_host = host
    if host in {"0.0.0.0", "::", "[::]"}:
        bind_host = ""
    try:
        infos = socket.getaddrinfo(bind_host or None, port, type=socket.SOCK_STREAM)
    except Exception:
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("0.0.0.0", port))]
    for family, socktype, proto, _, sockaddr in infos:
        s = None
        try:
            s = socket.socket(family, socktype, proto)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(sockaddr)
            return True
        except OSError:
            continue
        except Exception:
            continue
        finally:
            try:
                if s is not None:
                    s.close()
            except Exception:
                pass
    return False


def _install_web_deps_hint() -> str:
    req = ROOT / "requirements.txt"
    if req.exists():
        return f"{sys.executable} -m pip install -r requirements.txt"
    return f"{sys.executable} -m pip install 'fastapi>=0.110' 'uvicorn[standard]>=0.27' jinja2 python-multipart itsdangerous"


def _detect_lan_ipv4s() -> list[str]:
    """Best-effort local IPv4 addresses for LAN access hints (no external network calls)."""
    ips: list[str] = []
    try:
        host_ips = socket.gethostbyname_ex(socket.gethostname())[2] or []
        for ip in host_ips:
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # UDP "connect" trick to find primary outbound interface IP (no packets sent)
    for target in ("8.8.8.8", "1.1.1.1", "192.168.1.1", "10.0.0.1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 80))
                ip = s.getsockname()[0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
        except Exception:
            continue
    return ips


def _launcher_config_path() -> Path | None:
    """Preferred persisted launcher settings path (best-effort)."""
    ac_paths, _ = _try_import_app_core()
    candidates = []
    try:
        if ac_paths and getattr(ac_paths, "DATA_DIR", None):
            candidates.append(Path(ac_paths.DATA_DIR) / "launcher_settings.json")
    except Exception:
        pass
    candidates.append(ROOT / "data" / "launcher_settings.json")
    candidates.append(ROOT / ".launcher_settings.json")
    for p in candidates:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            continue
    return None


def _load_launcher_settings() -> dict:
    p = _launcher_config_path()
    if not p or not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_launcher_settings(settings: dict) -> bool:
    p = _launcher_config_path()
    if not p:
        return False
    try:
        safe = {
            "web_host": str(settings.get("web_host") or "127.0.0.1"),
            "web_port": int(settings.get("web_port") or 8000),
            "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        p.write_text(json.dumps(safe, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _print_lan_access_hints(host: str, port: int) -> None:
    ips = _detect_lan_ipv4s()
    if host in {"0.0.0.0", "::", "[::]"}:
        print("\nLAN access URLs (open from other devices on the same network):")
        if ips:
            for ip in ips:
                print(f"  - http://{ip}:{port}")
            print("Tip: for a PWA installed on a phone/tablet to auto-connect reliably, use the same LAN URL/IP and keep the server host IP + port stable.")
            print("     (Set a static/DHCP-reserved IP for this machine on your router/OS so the address does not change.)")
        else:
            print(f"  - http://<this-machine-LAN-IP>:{port}")
            print("Could not auto-detect LAN IP; check your OS network settings (Wi-Fi/Ethernet IPv4 address).")
        print("Security: LAN/remote access should use a strong PM_WEB_SECRET_KEY; prefer HTTPS/reverse proxy or VPN outside trusted networks.")
    elif host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"\nNetwork access URL: http://{host}:{port}")
        print("Ensure this address is reachable on your network and that firewall rules allow the port.")



def _is_frozen_build() -> bool:
    try:
        return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals() or hasattr(sys, "_MEIPASS")
    except Exception:
        return False


def _serve_web_foreground(host: str = "127.0.0.1", port: int = 8000) -> int:
    """Run uvicorn in-process (used by packaged builds and internal server mode)."""
    try:
        host = _validate_host(host)
        port = _validate_port(port)
    except Exception as e:
        print(f"\nInvalid web target: {e}")
        if _DEBUG_ENABLED:
            traceback.print_exc()
        return 2
    try:
        import uvicorn
        from ui.web.server import app as web_app
    except Exception as e:
        print("\nWeb dependencies/server are not available.")
        print(f"Details: {e}")
        if not _is_frozen_build():
            print(f"Install: {_install_web_deps_hint()}")
        return 1
    try:
        uvicorn.run(web_app, host=host, port=port, workers=1)
        return 0
    except KeyboardInterrupt:
        print("\nWeb server stopped.")
        return 130
    except Exception as e:
        tb = traceback.format_exc()
        if _DEBUG_ENABLED:
            print("\n[debug] Web server traceback:")
            print(tb)
        print(f"\nFailed to start Web UI: {e}")
        # Best-effort crash report for packaged/server mode
        try:
            dummy = argparse.Namespace(host=host, port=port, mode=None, web=True, serve_web=True, no_browser=True, debug=_DEBUG_ENABLED, diagnostics=False, save_report="__DEFAULT__", open_issue=False)
            _handle_diagnostics_cli(dummy, error=e, tb_text=tb, context={"phase": "serve_web_foreground"}, exit_after=False)
        except Exception:
            pass
        return 1


def run_web(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> int:
    try:
        host = _validate_host(host)
        port = _validate_port(port)
    except Exception as e:
        print(f"\nInvalid web target: {e}")
        if _DEBUG_ENABLED:
            traceback.print_exc()
        return 2

    ok, msg = _web_deps_ok()
    if not ok:
        print("\nWeb dependencies are not installed.")
        print(msg)
        print("\nInstall web dependencies, then try again:")
        print(f"  {_install_web_deps_hint()}")
        print("\nOr on Unix setup scripts use:  SKIP_WEB=0 MODE=web ACTION=setup sh scripts/setup.sh")
        return 1

    if not _port_available(host, port):
        print(f"\nPort {port} on host {host} appears to be in use.")
        print("Choose another port with --port, or stop the existing server.")
        return 1

    url_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{url_host}:{port}"
    print(f"\nStarting Web UI at {url}")
    print("SQLite-safe mode: --workers 1 (do not increase).")
    print("Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved across updates/restarts.")
    if host in {"0.0.0.0", "::"}:
        print("NOTE: Binding to all interfaces; use HTTPS reverse proxy for shared/LAN/remote deployments.")
    _print_lan_access_hints(host, port)
    if host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("PM_WEB_SECRET_KEY"):
        print("WARNING: PM_WEB_SECRET_KEY is not set. Set a strong random secret for non-local/shared deployments.")
    print("Health: GET /health, /healthz, /readyz   |   Doctor: python3 -m app_core.doctor")
    print("Press Ctrl+C to stop and return to menu.\n")

    if open_browser:
        def _open():
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            import threading
            threading.Timer(1.0, _open).start()
        except Exception:
            pass

    frozen = _is_frozen_build()
    if frozen:
        # Re-exec the packaged binary in server mode so the launcher can keep menu/process semantics.
        cmd = [sys.executable, "--serve-web", "--host", host, "--port", str(port)]
    else:
        cmd = [
            sys.executable, "-m", "uvicorn", "ui.web.server:app",
            "--host", host,
            "--port", str(port),
            "--workers", "1",
        ]
    try:
        _debug(f"Starting web process: {' '.join(cmd[:3])} ... (cwd={ROOT.name})")
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=_env())
        rc = proc.wait()
        if rc not in (0, 130, None) and _DEBUG_ENABLED:
            print(f"[debug] Web process exited with code {rc}")
        return int(rc) if rc is not None else 1
    except KeyboardInterrupt:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        except Exception:
            pass
        print("\nWeb server stopped.")
        return 130
    except FileNotFoundError as e:
        tb = traceback.format_exc()
        if _DEBUG_ENABLED:
            print(tb)
        if frozen:
            print("\nFailed to start packaged web server process.")
        else:
            print("\nuvicorn is not available in this Python environment.")
            print(f"Install: {_install_web_deps_hint()}")
        try:
            dummy = argparse.Namespace(host=host, port=port, mode="web", web=True, serve_web=False, no_browser=not open_browser, debug=_DEBUG_ENABLED, diagnostics=False, save_report="__DEFAULT__", open_issue=False)
            _handle_diagnostics_cli(dummy, error=e, tb_text=tb, context={"phase": "run_web_spawn", "frozen": frozen}, exit_after=False)
        except Exception:
            pass
        return 1
    except Exception as e:
        tb = traceback.format_exc()
        if _DEBUG_ENABLED:
            print(tb)
        print(f"\nFailed to start Web UI: {e}")
        if not frozen:
            print(f"Try: {sys.executable} -m uvicorn ui.web.server:app --host {host} --port {port} --workers 1")
        try:
            dummy = argparse.Namespace(host=host, port=port, mode="web", web=True, serve_web=False, no_browser=not open_browser, debug=_DEBUG_ENABLED, diagnostics=False, save_report="__DEFAULT__", open_issue=False)
            _handle_diagnostics_cli(dummy, error=e, tb_text=tb, context={"phase": "run_web_spawn", "frozen": frozen}, exit_after=False)
        except Exception:
            pass
        return 1



# --- Debug / diagnostics / issue reporting (privacy-safe) ---------------------

_DEBUG_ENABLED = False


def _set_debug(enabled: bool) -> None:
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = bool(enabled)


def _debug(msg: str) -> None:
    if _DEBUG_ENABLED:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[debug {ts}] {msg}")
        except Exception:
            pass


def _safe_env_summary() -> dict:
    """Return presence/flags only for selected env vars (never secret values)."""
    def _present(name: str) -> bool:
        return bool((os.environ.get(name) or "").strip())

    host = (os.environ.get("HOST") or "").strip()
    port = (os.environ.get("PORT") or "").strip()
    return {
        "PM_DATA_DIR_set": _present("PM_DATA_DIR"),
        "PM_WEB_SECRET_KEY_set": _present("PM_WEB_SECRET_KEY"),
        "PM_WEB_COOKIE_SECURE": (os.environ.get("PM_WEB_COOKIE_SECURE") or "").strip().lower() in ("1", "true", "yes", "on"),
        "PM_WEB_ALLOW_INSECURE_DEV": (os.environ.get("PM_WEB_ALLOW_INSECURE_DEV") or "").strip() == "1",
        "HOST": host if host in {"127.0.0.1", "0.0.0.0", "localhost", "::1", "[::]", "::"} else ("<set>" if host else ""),
        "PORT": port if (port.isdigit() and len(port) <= 5) else ("<set>" if port else ""),
        "GITHUB_TOKEN_set": _present("GITHUB_TOKEN") or _present("GH_TOKEN"),
        "frozen": _is_frozen_build(),
    }


def _try_import_app_core():
    try:
        if str(SRC) not in sys.path:
            sys.path.insert(0, str(SRC))
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from app_core import paths as ac_paths  # type: ignore
        from app_core import support as ac_support  # type: ignore
        return ac_paths, ac_support
    except Exception as e:
        _debug(f"app_core import unavailable: {e}")
        return None, None


def _sanitize_text(value, max_len=4000):
    ac_paths, _ = _try_import_app_core()
    try:
        if ac_paths and hasattr(ac_paths, "redact_sensitive"):
            return ac_paths.redact_sensitive(value, max_len=max_len)
    except Exception:
        pass
    try:
        s = str(value if value is not None else "")
    except Exception:
        s = "<unprintable>"
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


def _default_report_dir() -> Path:
    ac_paths, _ = _try_import_app_core()
    try:
        if ac_paths and getattr(ac_paths, "DATA_DIR", None):
            d = Path(ac_paths.DATA_DIR) / "support_reports"
            d.mkdir(parents=True, exist_ok=True)
            return d
    except Exception:
        pass
    try:
        d = ROOT / "data" / "support_reports"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        pass
    try:
        d = Path.cwd() / "spms-support-reports"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return Path.cwd()


def _collect_launcher_debug_info(*, error: BaseException | None = None, tb_text: str | None = None, context: dict | None = None, args=None) -> dict:
    ctx = dict(context or {})
    info = {
        "launcher": {
            "entrypoint": "launcher.py",
            "frozen_build": _is_frozen_build(),
            "executable_name": Path(sys.executable).name if sys.executable else None,
            "argv": [str(a) for a in (sys.argv[1:] if sys.argv else [])][:20],
            "cwd": Path.cwd().name if Path.cwd() else None,
            "platform": platform.platform(),
            "python": sys.version.split()[0] if sys.version else None,
            "host_arg": getattr(args, "host", None) if args is not None else None,
            "port_arg": getattr(args, "port", None) if args is not None else None,
            "mode_arg": getattr(args, "mode", None) if args is not None else None,
            "web_flag": bool(getattr(args, "web", False)) if args is not None else None,
            "serve_web_flag": bool(getattr(args, "serve_web", False)) if args is not None else None,
            "no_browser": bool(getattr(args, "no_browser", False)) if args is not None else None,
            "debug": bool(_DEBUG_ENABLED),
        },
        "env": _safe_env_summary(),
        "context": ctx,
    }
    try:
        ok, msg = _web_deps_ok()
        info["launcher"]["web_deps_ok"] = bool(ok)
        info["launcher"]["web_deps_status"] = _sanitize_text(msg, 300)
    except Exception:
        pass
    if error is not None:
        info["error"] = {
            "type": type(error).__name__,
            "message": _sanitize_text(error, 1200),
        }
    if tb_text:
        info["traceback"] = _sanitize_text(tb_text, 12000)
    return info


def _build_launcher_report(*, error: BaseException | None = None, tb_text: str | None = None, context: dict | None = None, args=None, title_hint: str = "", notes_hint: str = "") -> tuple[dict, str, str | None]:
    """Return (report_json, markdown_text, issue_url_or_none). Never raises fatally."""
    ac_paths, ac_support = _try_import_app_core()
    debug_info = _collect_launcher_debug_info(error=error, tb_text=tb_text, context=context, args=args)
    try:
        if ac_support and hasattr(ac_support, "generate_support_bundle"):
            user = {
                "title": title_hint or ("SPMS launcher error (native app)" if error else "SPMS launcher diagnostics"),
                "category": "Bug" if error else "Support",
                "severity": "High" if error else "Medium",
                "actual": _sanitize_text(error, 1000) if error else notes_hint or "Launcher diagnostics report generated on user request.",
                "notes": notes_hint or "Auto-generated by launcher debug/report feature. Review before sharing.",
            }
            bundle = ac_support.generate_support_bundle(context={"source": "launcher", **(context or {})}, user=user, include_update_check=False)
            report = json.loads(json.dumps(bundle.get("diagnostics") or {}, default=str))
            markdown = str(bundle.get("markdown") or "")
            issue_url = None
            try:
                issue = bundle.get("issue") or {}
                issue_url = issue.get("url") if isinstance(issue, dict) else None
            except Exception:
                issue_url = None
        else:
            report = {
                "report_version": 1,
                "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "app": {"app_version": "unknown", "note": "app_core.support unavailable"},
                "runtime": {"platform": platform.platform(), "python": sys.version.split()[0], "executable": Path(sys.executable).name if sys.executable else None},
                "environment": {},
                "client_context": {"source": "launcher"},
                "redaction": {"notes": ["Sensitive values should be redacted/excluded. Review before sharing."]},
            }
            markdown = "# SPMS launcher report\n\n" + json.dumps(debug_info, indent=2, default=str)
            issue_url = "https://github.com/Michaelhehelmy/spms/issues/new"
    except Exception as e:
        _debug(f"support bundle generation failed: {e}")
        report = {
            "report_version": 1,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "app": {"error": _sanitize_text(e, 300)},
            "runtime": {"platform": platform.platform(), "python": sys.version.split()[0]},
            "client_context": {"source": "launcher"},
        }
        markdown = "# SPMS launcher report (fallback)\n\n" + json.dumps(debug_info, indent=2, default=str)
        issue_url = "https://github.com/Michaelhehelmy/spms/issues/new"

    # Ensure landing-page compatible top-level fields
    try:
        report = json.loads(json.dumps(report, default=str))
    except Exception:
        report = {"diagnostics": str(report)}
    report["report_kind"] = "launcher_debug_report"
    report["generated_at_client"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    report["launcher_debug"] = debug_info
    user_report = {
        "title": title_hint or ("SPMS launcher error (native app)" if error else "SPMS launcher diagnostics"),
        "category": "Bug" if error else "Support",
        "severity": "High" if error else "Medium",
        "steps_to_reproduce": "1. Launch native app / launcher\n2. Observe error\n3. Attach this report",
        "expected_behavior": "Launcher/Web UI should start normally.",
        "actual_behavior": _sanitize_text(error, 1500) if error else (notes_hint or "Diagnostics collected."),
        "notes": notes_hint or "Auto-generated by SPMS launcher. Upload this JSON on the landing page report form or attach to a GitHub issue. Review before sharing.",
    }
    report["user_report"] = user_report
    report["client_device"] = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0] if sys.version else None,
        "frozen_build": _is_frozen_build(),
    }
    report["attachments_guidance"] = [
        "Review this report for sensitive/business information before sharing.",
        "Upload this JSON file on the SPMS landing page: Report issue section (#report-upload).",
        "Or attach it to a GitHub issue: https://github.com/Michaelhehelmy/spms/issues/new",
    ]

    # Rebuild markdown to include launcher debug block when possible
    try:
        md_lines = [markdown.rstrip(), "", "### Launcher debug (sanitized)", "```json", json.dumps(debug_info, indent=2, default=str)[:12000], "```", ""]
        markdown = "\n".join(md_lines)
    except Exception:
        pass
    return report, markdown, issue_url


def _save_launcher_report(report: dict, markdown: str, out_path: str | None = None) -> dict:
    """Save report JSON and markdown; returns paths/status info."""
    result = {"ok": False, "json_path": None, "markdown_path": None, "error": None}
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        if out_path:
            p = Path(out_path).expanduser()
            if p.exists() and p.is_dir():
                base = p / f"spms-launcher-report-{ts}"
            elif str(p).lower().endswith(".json"):
                base = p.with_suffix("")
            else:
                base = p
            base.parent.mkdir(parents=True, exist_ok=True)
        else:
            d = _default_report_dir()
            base = d / f"spms-launcher-report-{ts}"
        json_path = base.with_suffix(".json")
        md_path = base.with_suffix(".md")
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        md_path.write_text(markdown, encoding="utf-8")
        result.update({"ok": True, "json_path": str(json_path), "markdown_path": str(md_path)})
    except Exception as e:
        result["error"] = _sanitize_text(e, 500)
    return result


def _print_report_guidance(saved: dict, issue_url: str | None = None) -> None:
    print("\n--- SPMS diagnostic / issue report ---")
    if saved.get("ok"):
        print(f"Saved JSON report: {saved.get('json_path')}")
        print(f"Saved Markdown report: {saved.get('markdown_path')}")
        print("Next steps:")
        print("  1. Review the report and remove any sensitive/business information.")
        print("  2. Upload the JSON report on the SPMS landing page: Report issue section (#report-upload).")
        if issue_url:
            print(f"  3. Or open a GitHub issue (prefilled URL): {issue_url}")
        else:
            print("  3. Or open a GitHub issue: https://github.com/Michaelhehelmy/spms/issues/new")
        print("  4. Attach the JSON/Markdown report and screenshots if helpful.")
    else:
        print(f"Could not save report automatically: {saved.get('error')}")
        if issue_url:
            print(f"Open a GitHub issue: {issue_url}")
        else:
            print("Open a GitHub issue: https://github.com/Michaelhehelmy/spms/issues/new")
        print("If possible, run: python3 -m app_core.support --out spms-support-report.md")


def _handle_diagnostics_cli(args, *, error: BaseException | None = None, tb_text: str | None = None, context: dict | None = None, exit_after: bool = True) -> int:
    title_hint = "SPMS native launcher error" if error else "SPMS launcher diagnostics"
    report, markdown, issue_url = _build_launcher_report(error=error, tb_text=tb_text, context=context, args=args, title_hint=title_hint)
    # default save location if requested or if crash
    out_path = getattr(args, "save_report", None)
    should_save = bool(getattr(args, "save_report", None) is not None or getattr(args, "diagnostics", False) or error is not None)
    saved = {"ok": False}
    if should_save:
        if out_path == "__DEFAULT__" or out_path is True:
            out_path = None
        saved = _save_launcher_report(report, markdown, out_path=out_path)
    if getattr(args, "diagnostics", False) or getattr(args, "debug", False) or error is not None:
        try:
            dbg = report.get("launcher_debug") or {}
            print("\n[SPMS diagnostics summary]")
            print(json.dumps({k: dbg.get(k) for k in ("launcher", "env", "error") if k in dbg}, indent=2, default=str))
        except Exception:
            pass
    _print_report_guidance(saved if should_save else {"ok": False, "error": "not saved (use --save-report)"}, issue_url)
    if getattr(args, "open_issue", False) and issue_url:
        try:
            webbrowser.open(issue_url)
            print("Opened GitHub issue page in browser (prefilled URL).")
        except Exception as e:
            print(f"Could not open browser automatically: {_sanitize_text(e, 200)}")
    return 1 if error else 0


def _prompt(msg: str, default: str = "") -> str:
    try:
        val = input(msg).strip()
        return val or default
    except Exception:
        return default


def menu_loop(default_host: str = "127.0.0.1", default_port: int = 8000) -> int:
    cfg = _load_launcher_settings()
    host = os.environ.get("HOST") or str(cfg.get("web_host") or default_host)
    try:
        port = int(os.environ.get("PORT") or cfg.get("web_port") or default_port)
    except Exception:
        port = default_port
    try:
        host = _validate_host(host)
        port = _validate_port(port)
    except Exception:
        host, port = "127.0.0.1", 8000
    last = None
    while True:
        _clear()
        is_non_local = host not in ("127.0.0.1", "localhost", "::1")
        secret_set = bool((os.environ.get("PM_WEB_SECRET_KEY") or "").strip())
        print("=" * 56)
        print("  SPMS — Web Launcher")
        print("=" * 56)
        print("  [1] Start Web UI (browser, --workers 1)")
        print("  [s] Web settings (host/port)")
        print("  [l] LAN mode (bind 0.0.0.0) / show network URLs")
        print("  [d] Diagnostics / create issue report (debug info)")
        print("  [q] Quit")
        if last is not None:
            print(f"\n  Last session exited with code: {last}")
        print(f"\n  Web target: http://{host}:{port}  {'(LAN/shared)' if is_non_local else '(local)'}")
        if is_non_local:
            ips = _detect_lan_ipv4s()
            if ips:
                print("  LAN URLs: " + ", ".join([f"http://{ip}:{port}" for ip in ips[:4]]))
            else:
                print(f"  LAN URL:  http://<this-machine-LAN-IP>:{port}")
        print("  SQLite: always --workers 1. Runtime data (DB/exports/invoices/PM_DATA_DIR) preserved.")
        print("  Health:  python3 -m app_core.doctor --json   |   /health, /healthz, /readyz")
        print("  Backup:  python3 scripts/backup_runtime.py --backup | --verify <backup.db>")
        print("  Support: python3 -m app_core.support  |  Web: /support/report")
        print("  Updates: Settings → App updates (git ff-only) or git pull --ff-only + rerun setup; restart after apply.")
        print("  Debug:   if native app/launcher crashes or fails to start, use [d] or --debug/--save-report.")
        print("  PWA/LAN: install/open PWA from the same LAN URL (same IP+port). Keep host IP stable (router DHCP reservation/static IP) for auto-reconnect.")
        if is_non_local and not secret_set:
            print("  WARNING: set PM_WEB_SECRET_KEY (strong random) for non-local/shared access.")
        if is_non_local:
            print("  Security: use HTTPS reverse proxy (Caddy/Nginx/Traefik). See deploy/ examples.")
        print("  Exit: press Ctrl+C while server is running to return here.")

        try:
            choice = input("\nChoose 1, s, l, d, or q: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        if choice in {"1", "w", "web", "start", ""}:
            last = run_web(host=host, port=port, open_browser=True)
        elif choice in {"s", "settings", "set"}:
            print(f"\nCurrent web host: {host} (use 127.0.0.1 for local, 0.0.0.0 for LAN)")
            h = _prompt(f"Web host [{host}]: ", host)
            try:
                host = _validate_host(h)
            except Exception:
                print("Invalid host; keeping previous value.")
                time.sleep(0.8)
                continue
            p = _prompt(f"Web port [{port}]: ", str(port))
            try:
                port = _validate_port(int(p))
            except Exception:
                print("Invalid port; keeping previous value.")
                time.sleep(0.8)
                continue
            saved = _save_launcher_settings({"web_host": host, "web_port": port})
            if saved:
                print("Saved launcher web settings.")
            else:
                print("Could not persist launcher settings (will still use them for this session).")
            if host in {"0.0.0.0", "::", "[::]"}:
                _print_lan_access_hints(host, port)
            time.sleep(0.8)
        elif choice in {"l", "lan", "network"}:
            host = "0.0.0.0"
            saved = _save_launcher_settings({"web_host": host, "web_port": port})
            print("\nLAN mode enabled: server will bind to 0.0.0.0 (all interfaces) on start.")
            _print_lan_access_hints(host, port)
            if not (os.environ.get("PM_WEB_SECRET_KEY") or "").strip():
                print("WARNING: PM_WEB_SECRET_KEY is not set. Set a strong random secret for LAN/shared access.")
            if saved:
                print("Saved launcher web settings.")
            else:
                print("Could not persist launcher settings (will still use them for this session).")
            try:
                input("\nPress Enter to return to menu...")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice in {"d", "diag", "diagnostics", "debug", "r", "report"}:
            print("\nGenerating sanitized diagnostics/report...")
            dummy = argparse.Namespace(
                host=host,
                port=port,
                mode="diagnostics",
                web=False,
                serve_web=False,
                no_browser=False,
                debug=_DEBUG_ENABLED,
                diagnostics=True,
                save_report="__DEFAULT__",
                open_issue=False,
            )
            try:
                _handle_diagnostics_cli(dummy, context={"phase": "menu_diagnostics"})
            except Exception as e:
                print(f"Diagnostics/report generation failed: {e}")
                if _DEBUG_ENABLED:
                    traceback.print_exc()
            try:
                open_now = input("Open prefilled GitHub issue page in browser now? [y/N]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                open_now = "n"
            if open_now in {"y", "yes"}:
                try:
                    _, _, issue_url = _build_launcher_report(
                        context={"phase": "menu_open_issue"},
                        args=dummy,
                        title_hint="SPMS launcher diagnostics",
                    )
                    webbrowser.open(issue_url or "https://github.com/Michaelhehelmy/spms/issues/new")
                    print("Opened GitHub issue page in browser (if supported).")
                except Exception as e:
                    print(f"Could not open browser: {e}")
            try:
                input("\nPress Enter to return to menu...")
            except (EOFError, KeyboardInterrupt):
                pass
        elif choice in {"q", "quit", "exit"}:
            print("Bye.")
            return 0
        else:
            print("Please enter 1, s, l, d, or q.")
            time.sleep(0.8)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="launcher.py", description="SPMS Web Launcher")
    parser.add_argument("mode", nargs="?", help="web | menu | diagnostics (default: menu)")
    parser.add_argument("--web", action="store_true", help="Launch Web UI directly")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Web host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="Web port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser for web mode")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug output and save diagnostic info on errors")
    parser.add_argument("--diagnostics", action="store_true", help="Print sanitized diagnostics and exit")
    parser.add_argument("--save-report", nargs="?", const="__DEFAULT__", help="Save a sanitized support report (JSON+Markdown) to PATH or default location")
    parser.add_argument("--open-issue", action="store_true", help="Open prefilled GitHub issue URL in browser when generating a report")
    parser.add_argument("--serve-web", action="store_true", help=argparse.SUPPRESS)
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        # argparse exits on --help / parse errors; preserve code
        raise
    except Exception as e:
        tb = traceback.format_exc()
        _set_debug(True)
        dummy = argparse.Namespace(host="127.0.0.1", port=8000, mode=None, web=False, serve_web=False, no_browser=True, debug=True, diagnostics=False, save_report="__DEFAULT__", open_issue=False)
        _handle_diagnostics_cli(dummy, error=e, tb_text=tb, context={"phase": "argparse"}, exit_after=False)
        return 2

    _set_debug(bool(getattr(args, "debug", False)) or bool(os.environ.get("SPMS_DEBUG")))

    # Internal mode for packaged/frozen builds (re-exec target).
    if args.serve_web:
        try:
            return _serve_web_foreground(args.host, args.port)
        except Exception as e:
            tb = traceback.format_exc()
            if _DEBUG_ENABLED:
                print(tb)
            _handle_diagnostics_cli(args, error=e, tb_text=tb, context={"phase": "serve_web_dispatch"}, exit_after=False)
            return 1

    # Diagnostics/report-only mode
    if args.diagnostics or args.save_report is not None or (args.mode and str(args.mode).lower() in {"diagnostics", "diag", "doctor"}):
        try:
            return _handle_diagnostics_cli(args, context={"phase": "diagnostics_cli"})
        except Exception as e:
            tb = traceback.format_exc()
            if _DEBUG_ENABLED:
                print(tb)
            print(f"Failed to generate diagnostics report: {e}")
            return 1

    try:
        # Web-only app: direct launch for web/legacy mode args (backward-compatible)
        if args.web or (args.mode and args.mode.lower() in {"web", "1", "w", "t", "2"}):
            return run_web(host=args.host, port=args.port, open_browser=not args.no_browser)
        return menu_loop(default_host=args.host, default_port=args.port)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as e:
        tb = traceback.format_exc()
        if _DEBUG_ENABLED:
            print("\n[debug] Unhandled launcher exception:")
            print(tb)
        print(f"\nLauncher error: {e}")
        try:
            _handle_diagnostics_cli(args, error=e, tb_text=tb, context={"phase": "main"}, exit_after=False)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
