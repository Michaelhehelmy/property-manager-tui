#!/usr/bin/env python3
"""TUI entry point (default). Rename-safe launcher; runs Textual app."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from ui.tui.app import main as tui_main
    tui_main()


if __name__ == "__main__":
    main()
