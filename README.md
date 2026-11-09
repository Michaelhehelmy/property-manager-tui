# Property Manager (TUI + Web UI)

Local-first property management system: reservations, front desk, POS/inventory, accounting ledger, invoices, reports/analytics. **Business logic lives in `src/app_core/`** and is shared by both UIs.

## Structure

```text
main.py                 # TUI entry point (default)
src/app_core/           # backend: db.py, services.py, paths.py
ui/tui/                 # Textual TUI (app, tabs, modals)
ui/web/                 # FastAPI + HTMX web UI
tests/                  # pytest tests (use app_core imports; conftest sets paths)
data/ or project root   # runtime DB/exports/invoices (auto-detected; PM_DATA_DIR overrides)
```

## Run (TUI)

```bash
python main.py
```
`main.py` is rename-safe as a launcher pattern; the TUI includes branding/animation setup, full reservation/POS/accounting flows, and analytics exports.

## Run (Web UI, localhost MVP)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-web.txt
PYTHONPATH=src uvicorn ui.web.server:app --reload --workers 1
```
Open http://127.0.0.1:8000

Notes: use `--workers 1` (SQLite/global connection safety). Sensitive web actions use CSRF + super-password verification/unlock flow.

## Tests / checks

```bash
python -m py_compile src/app_core/*.py ui/tui/*.py ui/web/*.py main.py
PYTHONPATH=src pytest -q
./run_coverage.sh   # if pytest-cov installed
```

## Data paths
`app_core.paths` anchors runtime data. By default it uses legacy root `property_manager.db`/`exports/`/`invoices/` if present; otherwise uses `data/`. Override with:
```bash
export PM_DATA_DIR=/path/to/data
```
