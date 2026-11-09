#!/usr/bin/env python3
"""Prompt for TUI or Web UI and launch it."""
from __future__ import annotations

import os
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


def run_tui() -> int:
    print("Starting TUI...")
    return subprocess.call([sys.executable, str(ROOT / "main.py")], cwd=str(ROOT), env=_env())


def _web_deps_ok() -> tuple[bool, str]:
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        import jinja2  # noqa: F401
        import multipart  # noqa: F401
        return True, ""
    except Exception as e:
        return False, str(e)


def run_web(host: str = "127.0.0.1", port: int = 8000) -> int:
    ok, err = _web_deps_ok()
    if not ok:
        print("Web dependencies are missing or incomplete.")
        print(f"Details: {err}")
        print("\nInstall with:")
        print(f"  {sys.executable} -m pip install -r requirements-web.txt")
        return 1

    url = f"http://{host}:{port}"
    print(f"Starting Web UI at {url} (workers=1, localhost MVP)...")
    print("Press Ctrl+C to stop.")

    # Start server, then open browser shortly after.
    env = _env()
    cmd = [sys.executable, "-m", "uvicorn", "ui.web.server:app", "--host", host, "--port", str(port), "--workers", "1"]
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
    try:
        time.sleep(1.2)
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        print("\nWeb UI stopped.")
        return 0


def prompt_choice() -> str:
    print(f"\n{('=' * 48)}")
    print("  Property Manager Launcher")
    print(f"{('=' * 48)}")
    print("  [1] TUI  (Textual terminal app)")
    print("  [2] Web  (FastAPI + HTMX, opens browser)")
    print("  [q] Quit")
    while True:
        choice = input("\nChoose 1, 2, or q: ").strip().lower()
        if choice in {"1", "t", "tui"}:
            return "tui"
        if choice in {"2", "w", "web"}:
            return "web"
        if choice in {"q", "quit", "exit"}:
            return "quit"
        print("Please enter 1, 2, or q.")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        arg = argv[0].lower()
        if arg in {"tui", "--tui", "1"}:
            return run_tui()
        if arg in {"web", "--web", "2"}:
            return run_web()
        if arg in {"-h", "--help"}:
            print("Usage: python launcher.py [tui|web]")
            return 0

    choice = prompt_choice()
    if choice == "tui":
        return run_tui()
    if choice == "web":
        return run_web()
    print("Bye.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
