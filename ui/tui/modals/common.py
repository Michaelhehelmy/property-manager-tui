"""All modals. UI-only — all logic in services.py."""
import os
from datetime import datetime
from textual.widgets import Static, Button, Input, RichLog, DataTable, Select, Checkbox, Collapsible
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from rich.text import Text
from app_core import services
from app_core.services import t, fmt, selected_id, SETTINGS_CATALOG

try:
    import plotext as _plt
    PLOTEXT_OK = True
except ImportError:  # pragma: no cover - plotext is installed in this environment
    _plt = None  # pragma: no cover
    PLOTEXT_OK = False  # pragma: no cover


# ═══════════════════════════════════════════════════════════════════════════
# BASE
# ═══════════════════════════════════════════════════════════════════════════

