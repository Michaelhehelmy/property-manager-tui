# Property Manager (TUI + Web UI)

Local-first property management system: reservations, front desk, POS/inventory, accounting ledger, invoices, reports/analytics. **Business logic lives in `src/app_core/`** and is shared by both UIs.

## Structure

```text
launcher.py             # prompts and launches TUI or Web UI
main.py                 # TUI entry point (default)
requirements-web.txt    # Web UI dependencies (FastAPI/HTMX stack)
src/app_core/           # backend: db.py, services.py, paths.py
ui/tui/                 # Textual TUI (app, tabs, modals)
ui/web/                 # FastAPI + HTMX web UI
tests/                  # pytest tests (use app_core imports; conftest sets paths)
data/ or project root   # runtime DB/exports/invoices (auto-detected; PM_DATA_DIR overrides)
```

## Quick start (launcher)

```bash
python launcher.py          # prompts: [1] TUI, [2] Web
python launcher.py tui      # run TUI directly
python launcher.py web      # start Web UI and auto-open browser
```

The launcher sets `PYTHONPATH` automatically. For the Web UI, install dependencies first if needed:
```bash
pip install -r requirements-web.txt
```

## Run (TUI)

```bash
python main.py
# or
python launcher.py tui
```
`main.py` is the canonical TUI entry point. The TUI includes branding/animation setup (wizard), full reservation/POS/accounting flows, and analytics exports.

## Run (Web UI, localhost MVP)

```bash
pip install -r requirements-web.txt
python launcher.py web
# or manually:
PYTHONPATH=src uvicorn ui.web.server:app --reload --workers 1
```
Open http://127.0.0.1:8000

Notes: use `--workers 1` (SQLite/global connection safety). Sensitive web actions use CSRF + super-password verification/unlock flow. The web UI is a localhost MVP second interface over the same `app_core` services/DB.

## Tests / checks

```bash
python -m py_compile src/app_core/*.py ui/tui/*.py ui/web/*.py main.py launcher.py
PYTHONPATH=src pytest -q
```

## Data paths
`app_core.paths` anchors runtime data. By default it uses legacy root `property_manager.db`/`exports/`/`invoices/` if present; otherwise uses `data/`. Override with:
```bash
export PM_DATA_DIR=/path/to/data
```
