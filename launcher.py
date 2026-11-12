#!/usr/bin/env python3
"""Unified entry point for Property Manager: choose TUI or Web UI and switch between them."""
from __future__ import annotations

import argparse
import os
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
        parts.append(pp)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _clear():
    try:
        if os.name == "nt":
            os.system("cls")
        else:
            print("\033[2J\033[H", end="")
    except Exception:
        pass


def run_tui() -> int:
    print("\nStarting TUI... (exit the TUI to return to launcher)\n")
    try:
        return subprocess.call([sys.executable, str(ROOT / "main.py")], cwd=str(ROOT), env=_env())
    except KeyboardInterrupt:
        print("\nTUI interrupted.")
        return 130


def _web_deps_ok() -> tuple[bool, str]:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        import jinja2  # noqa: F401
        import multipart  # noqa: F401
        return True, ""
    except Exception as e:
        return False, str(e)


def _lan_ip_hint() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "<this-device-ip>"


def _validate_host(host: str) -> str:
    h = (host or "").strip()
    if not h or any(ch.isspace() for ch in h) or h.startswith("-"):
        raise ValueError(f"Invalid host: {host!r}")
    # allow common forms: IPv4, IPv6, localhost, hostnames
    if not all(c.isalnum() or c in ".:-_[]" for c in h):
        raise ValueError(f"Invalid host: {host!r}")
    return h


def _validate_port(port: int) -> int:
    try:
        p = int(port)
    except Exception:
        raise ValueError(f"Invalid port: {port!r}")
    if p < 1 or p > 65535:
        raise ValueError(f"Invalid port: {port!r}")
    return p


def run_web(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> int:
    try:
        host = _validate_host(host)
        port = _validate_port(port)
    except Exception as e:
        print(f"\nInvalid web settings: {e}")
        input("\nPress Enter to return to launcher...")
        return 2
    ok, err = _web_deps_ok()
    if not ok:
        print("\nWeb dependencies are missing or incomplete.")
        print(f"Details: {err}")
        print("\nInstall with:")
        print(f"  {sys.executable} -m pip install -r requirements-web.txt")
        print("\nTip: on iSH or constrained environments, use TUI mode (SKIP_WEB=1).")
        input("\nPress Enter to return to launcher...")
        return 1

    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{url_host}:{port}"
    print(f"\nStarting Web UI at http://{host}:{port} (workers=1)...")
    print(f"Local: {url}")
    if host in ("0.0.0.0", "::"):
        print(f"LAN:   http://{_lan_ip_hint()}:{port}  (other devices on same network)")
    print("Runtime data (DB/exports/invoices/data dir) is preserved across updates/restarts.")
    print("Press Ctrl+C to stop the web server and return to launcher.\n")

    env = _env()
    cmd = [sys.executable, "-m", "uvicorn", "ui.web.server:app", "--host", host, "--port", str(port), "--workers", "1"]
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    try:
        if open_browser:
            time.sleep(1.2)
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return proc.wait()
    except KeyboardInterrupt:
        print("\nStopping web server...")
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        print("Web UI stopped. Returning to launcher...")
        time.sleep(0.4)
        return 0


def _prompt(msg: str, default: str = "") -> str:
    try:
        v = input(msg)
    except EOFError:
        return default
    v = (v or "").strip()
    return v if v else default


def menu_loop(default_host: str = "127.0.0.1", default_port: int = 8000) -> int:
    host = os.environ.get("HOST", default_host)
    try:
        port = int(os.environ.get("PORT", str(default_port)))
    except Exception:
        port = default_port
    last = None
    while True:
        _clear()
        print("=" * 52)
        print("  Property Manager — Launcher")
        print("=" * 52)
        print("  [1] TUI  — Textual terminal app (local)")
        print("  [2] Web  — FastAPI + HTMX (browser/client-server)")
        print("  [s] Web settings (host/port)")
        print("  [q] Quit")
        if last is not None:
            print(f"\n  Last session exited with code: {last}")
        print(f"\n  Web target: http://{host}:{port}  {'(LAN)' if host in ('0.0.0.0','::') else '(local)'}")
        print("  Tip: exit TUI or stop Web (Ctrl+C) to return here and switch.")

        try:
            choice = input("\nChoose 1, 2, s, or q: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return 0

        if choice in {"1", "t", "tui"}:
            last = run_tui()
        elif choice in {"2", "w", "web"}:
            last = run_web(host=host, port=port, open_browser=True)
        elif choice in {"s", "settings", "set"}:
            print(f"\nCurrent web host: {host} (use 127.0.0.1 for local, 0.0.0.0 for LAN)")
            h = _prompt(f"Web host [{host}]: ", host)
            if h:
                host = h
            p = _prompt(f"Web port [{port}]: ", str(port))
            try:
                port = int(p)
            except Exception:
                print("Invalid port; keeping previous value.")
                time.sleep(0.8)
        elif choice in {"q", "quit", "exit"}:
            print("Bye.")
            return 0
        else:
            print("Please enter 1, 2, s, or q.")
            time.sleep(0.8)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="launcher.py", description="Property Manager launcher (TUI/Web)")
    parser.add_argument("mode", nargs="?", help="tui | web | menu (default: menu)")
    parser.add_argument("--tui", action="store_true", help="Launch TUI directly")
    parser.add_argument("--web", action="store_true", help="Launch Web UI directly")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Web host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="Web port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open browser for web mode")
    args = parser.parse_args(argv)

    if args.tui or (args.mode and args.mode.lower() in {"tui", "1", "t"}):
        return run_tui()
    if args.web or (args.mode and args.mode.lower() in {"web", "2", "w"}):
        return run_web(host=args.host, port=args.port, open_browser=not args.no_browser)
    # default: interactive menu loop (choose & switch)
    return menu_loop(default_host=args.host, default_port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
