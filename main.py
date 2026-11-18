#!/usr/bin/env python3
"""Web UI entry point (compatibility launcher).

The application is web-only. This starts the FastAPI/HTMX server with safe
SQLite defaults (--workers 1). Runtime data (DB/exports/invoices/PM_DATA_DIR)
is preserved across updates/restarts.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="main.py", description="Property Manager Web UI entry point")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="Web host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")), help="Web port (default: 8000)")
    parser.add_argument("--no-browser", action="store_true", help="Compatibility flag (ignored; no browser auto-open)")
    args = parser.parse_args(argv)

    try:
        import uvicorn
        from ui.web.server import app as web_app
    except Exception:
        print("Web dependencies are not installed (uvicorn/server import failed).")
        print(f"Install: {sys.executable} -m pip install -r requirements-web.txt")
        return 1

    host = args.host or "127.0.0.1"
    port = int(args.port or 8000)
    print(f"Starting Property Manager Web UI at http://{host}:{port} (--workers 1)")
    print("Note: this project is now web-only. Use launcher.py or uvicorn directly for more options.")
    try:
        uvicorn.run(web_app, host=host, port=port, workers=1)
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
