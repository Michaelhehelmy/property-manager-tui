#!/usr/bin/env python3
"""Unified entry point for Property Manager Web UI.

Web-only launcher with safe defaults (SQLite requires --workers 1).
Runtime data (DB/exports/invoices/PM_DATA_DIR) is preserved across updates/restarts.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
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
    req = ROOT / "requirements-web.txt"
    if req.exists():
        return f"{sys.executable} -m pip install -r requirements-web.txt"
    return f"{sys.executable} -m pip install 'fastapi>=0.110' 'uvicorn[standard]>=0.27' jinja2 python-multipart itsdangerous"


def run_web(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> int:
    try:
        host = _validate_host(host)
        port = _validate_port(port)
    except Exception as e:
        print(f"\nInvalid web target: {e}")
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

    cmd = [
        sys.executable, "-m", "uvicorn", "ui.web.server:app",
        "--host", host,
        "--port", str(port),
        "--workers", "1",
    ]
    try:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=_env())
        return proc.wait()
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
    except FileNotFoundError:
        print("\nuvicorn is not available in this Python environment.")
        print(f"Install: {_install_web_deps_hint()}")
        return 1
    except Exception as e:
        print(f"\nFailed to start Web UI: {e}")
        print(f"Try: {sys.executable} -m uvicorn ui.web.server:app --host {host} --port {port} --workers 1")
        return 1


def _prompt(msg: str, default: str = "") -> str:
    try:
        val = input(msg).strip()
        return val or default
    except Exception:
        return default


def menu_loop(default_host: str = "127.0.0.1", default_port: int = 8000) -> int:
    host = os.environ.get("HOST", default_host)
    try:
        port = int(os.environ.get("PORT", str(default_port)))
    except Exception:
        port = default_port
    last = None
    while True:
        _clear()
        is_non_local = host not in ("127.0.0.1", "localhost", "::1")
        secret_set = bool((os.environ.get("PM_WEB_SECRET_KEY") or "").strip())
        print("=" * 56)
        print("  Property Manager — Web Launcher")
        print("=" * 56)
        print("  [1] Start Web UI (browser, --workers 1)")
        print("  [s] Web settings (host/port)")
        print("  [q] Quit")
        if last is not None:
            print(f"\n  Last session exited with code: {last}")
        print(f"\n  Web target: http://{host}:{port}  {'(LAN/shared)' if is_non_local else '(local)'}")
        print("  SQLite: always --workers 1. Runtime data (DB/exports/invoices/PM_DATA_DIR) preserved.")
        print("  Health:  python3 -m app_core.doctor --json   |   /health, /healthz, /readyz")
        print("  Backup:  python3 scripts/backup_runtime.py --backup | --verify <backup.db>")
        print("  Support: python3 -m app_core.support  |  Web: /support/report")
        print("  Updates: Settings → App updates (git ff-only) or git pull --ff-only + rerun setup; restart after apply.")
        if is_non_local and not secret_set:
            print("  WARNING: set PM_WEB_SECRET_KEY (strong random) for non-local/shared access.")
        if is_non_local:
            print("  Security: use HTTPS reverse proxy (Caddy/Nginx/Traefik). See deploy/ examples.")
        print("  Exit: press Ctrl+C while server is running to return here.")

        try:
            choice = input("\nChoose 1, s, or q: ").strip().lower()
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
        elif choice in {"q", "quit", "exit"}:
            print("Bye.")
            return 0
        else:
            print("Please enter 1, s, or q.")
            time.sleep(0.8)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="launcher.py", description="Property Manager Web launcher")
    parser.add_argument("mode", nargs="?", help="web | menu (default: menu)")
    parser.add_argument("--web", action="store_true", help="Launch Web UI directly")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Web host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="Web port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser for web mode")
    args = parser.parse_args(argv)

    # Web-only app: direct launch for web/legacy mode args (backward-compatible)
    if args.web or (args.mode and args.mode.lower() in {"web", "1", "w", "tui", "t", "2"}):
        return run_web(host=args.host, port=args.port, open_browser=not args.no_browser)
    return menu_loop(default_host=args.host, default_port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
