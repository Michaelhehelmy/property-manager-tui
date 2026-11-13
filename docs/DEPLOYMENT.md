# Deployment Guide (Local, LAN, Remote)

This guide covers practical deployment for the **TUI** and **Web UI** (FastAPI/HTMX) with the shared `app_core` backend and SQLite datastore.

## 1) Deployment model and constraints

- **Shared backend:** `src/app_core/` is the source of business logic/data access for both UIs.
- **SQLite:** local-first, single-file database in the configured data directory.
- **Web server constraint:** always run with **`--workers 1`**. Do **not** increase workers (SQLite/shared state).
- **Runtime data:** DB, exports, invoices, and configured data dir (`PM_DATA_DIR` or defaults/legacy locations) are preserved across source/app updates. Migrations run on startup (additive where applicable).
- **Entry points:** `python launcher.py [--tui|--web]`, `python main.py` (TUI), or `python -m uvicorn ui.web.server:app --host <HOST> --port <PORT> --workers 1`.

## 2) Choose a topology

| Topology | Use when | Bind/transport | Notes |
|---|---|---|---|
| **Local workstation/server (terminal)** | Single operator on same machine | TUI, or Web on `127.0.0.1` | Lowest exposure; easiest ops |
| **LAN (trusted network)** | Phones/tablets/desktops on same network | Web on `0.0.0.0:<PORT>` | Set `PM_WEB_SECRET_KEY`; restrict firewall to trusted subnets; prefer HTTPS reverse proxy if possible |
| **Remote (internet)** | Off-site access | Web on `127.0.0.1` behind **HTTPS** reverse proxy / VPN / Tailscale | Never expose plain HTTP publicly; enforce strong secrets and access controls |
| **Constrained devices (e.g. iSH)** | Limited toolchain/resources | TUI preferred (`SKIP_WEB=1`) | Web deps/builds may be impractical |

## 3) Prerequisites

- Python **3.10+** (3.11+ recommended), `pip`, `venv`
- Git (for clone/update workflows)
- SQLite bundled with Python
- Build tools may be needed if wheels unavailable (platform/arch dependent)
- Web deps: `requirements-web.txt` (FastAPI, Uvicorn, Jinja2, python-multipart, itsdangerous)

## 4) Install / bootstrap (automated) and update (automated/manual)

### 4.1 One-line bootstrap (recommended)
Use the OS-appropriate one-liner from `README.md` (Unix `scripts/install.sh`, Windows `scripts/install_windows.ps1`/`.cmd`). Intended for first install and re-runs:
- **Automated:** prerequisite bootstrap where supported (package manager/winget as available), clone or fast-forward update, venv + dependency refresh, runtime-data preservation, and launch/run per mode.
- **Pinned/reproducible installs:** pass `GIT_REF=<tag|commit|branch>` (or `TAG`/`COMMIT`; Windows: `-GitRef` / `$env:GIT_REF`) and, for reproducible installer behavior, fetch the bootstrap script from the same raw ref (e.g. `.../raw/<tag-or-commit>/scripts/install.sh` or `install_windows.ps1`).
- **Dependency/install robustness:** if web deps fail to build/install on constrained environments (e.g. `pydantic-core`/Rust wheel issues on iSH/Termux/limited toolchains), fallback to TUI-only (`SKIP_WEB=1 MODE=tui` or `-SkipWeb 1 -Mode tui`) or run web on a fuller host.
- **Operational:** provide correct env vars (`PM_WEB_SECRET_KEY`, `PM_DATA_DIR`, `HOST`, `PORT`, network/firewall/TLS decisions). Keep web on `--workers 1` and use HTTPS reverse proxy/VPN for shared/non-local access. Runtime data (DB/exports/invoices/`PM_DATA_DIR`) is preserved.

### 4.2 Setup scripts (from checkout)
- **Unix-like:** `sh scripts/setup.sh` (alias `scripts/setup_ish_alpine.sh`)
  - `MODE=auto|tui|web|both`, `ACTION=auto|setup|run|deploy`, `SKIP_WEB=auto|0|1`
  - Examples:
    - `MODE=tui ACTION=deploy SKIP_WEB=1 sh scripts/setup.sh`
    - `MODE=web HOST=127.0.0.1 PORT=8000 ACTION=deploy sh scripts/setup.sh`
    - `MODE=web HOST=0.0.0.0 PORT=8000 ACTION=deploy sh scripts/setup.sh` (LAN; set secret)
- **Windows:** `scripts/install_windows.ps1` or `scripts/setup_windows.ps1` / `.cmd`
  - `-Action setup|run|deploy`, `-Mode auto|tui|web|both`, `-BindHost`, `-Port`, `-SkipWeb`, `-InstallDev`, `-RunTests`
  - Example: `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -Action deploy -Mode web -BindHost 127.0.0.1 -Port 8000`

### 4.3 Manual run
```bash
# TUI
PYTHONPATH=src python3 main.py
# or
python3 launcher.py --tui

# Web (ALWAYS --workers 1)
export PM_WEB_SECRET_KEY='replace-with-strong-random-secret'
PYTHONPATH=src python3 -m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1
```

Helper: `scripts/update_and_run_web.sh` (git fast-forward if repo, activates venv, sets `PYTHONPATH`, warns/defaults secret for local dev, runs uvicorn `--workers 1`).

## 5) Web deployment details

### 5.1 Required/strongly recommended settings
- **`--workers 1`** (mandatory for SQLite/shared state; never increase workers).
- **`PM_WEB_SECRET_KEY`**: strong random secret for any non-local/shared use (sessions/signing; required for non-loopback deployment unless an explicit local/dev insecure override is set). Never reuse weak defaults.
- **`PM_DATA_DIR`** (optional): explicit data directory for DB/exports/invoices isolation and backup targeting.
- **`HOST`/`PORT`**: choose bind interface and port deliberately.
- **Data directory & permissions:** ensure the service user can read/write the configured/default data directory and runtime locations (single writer). Readiness checks include data-dir writability.

### 5.2 Health checks / probes
- `GET /health` / `GET /healthz`: liveness/basic health endpoints returning health/readiness summary (status may be degraded if readiness checks fail).
- `GET /readyz`: readiness endpoint with runtime checks (including data-dir writability and related checks) for service/proxy/monitor readiness gating.
Use for liveness/readiness in process managers, reverse proxies, or external monitors. Keep web on `--workers 1`.

### 5.3 Reverse proxy + HTTPS (recommended for LAN/remote)

Run the app bound to localhost and terminate TLS at a reverse proxy.

**Caddy (example, automatic TLS for a real domain):**
```caddyfile
pm.example.com {
    encode gzip zstd
    reverse_proxy 127.0.0.1:8000
}
```

**Nginx (example):**
```nginx
server {
    listen 443 ssl http2;
    server_name pm.example.com;

    ssl_certificate     /etc/letsencrypt/live/pm.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pm.example.com/privkey.pem;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_redirect off;
    }

    location = /healthz { proxy_pass http://127.0.0.1:8000/healthz; }
    location = /readyz { proxy_pass http://127.0.0.1:8000/readyz; }
}
```

**Traefik:** route HTTPS entrypoint/router to service `http://127.0.0.1:8000` with TLS cert resolver; keep app private on localhost.

**Operational notes:** restrict proxy/admin ports, keep app on loopback when proxied, enable HSTS/modern TLS at proxy, and monitor cert renewal.

## 6) Process supervision / autostart

### 6.1 systemd (Linux, example)

`/etc/systemd/system/property-manager-web.service`:
```ini
[Unit]
Description=Property Manager Web UI
After=network.target

[Service]
Type=simple
User=pmapp
Group=pmapp
WorkingDirectory=/opt/property-manager-tui
Environment=PYTHONPATH=/opt/property-manager-tui/src:/opt/property-manager-tui
Environment=PM_WEB_SECRET_KEY=replace-with-strong-random-secret
Environment=PM_DATA_DIR=/var/lib/property-manager
ExecStart=/opt/property-manager-tui/.venv/bin/python -m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
# Optional hardening examples (validate paths/perms first):
# NoNewPrivileges=true
# PrivateTmp=true

[Install]
WantedBy=multi-user.target
```
Commands: `systemctl daemon-reload && systemctl enable --now property-manager-web && systemctl status property-manager-web`.

### 6.2 Windows (NSSM / Task Scheduler)
- Run venv Python with args: `-m uvicorn ui.web.server:app --host 127.0.0.1 --port 8000 --workers 1`
- Set working directory to install root and env vars (`PYTHONPATH`, `PM_WEB_SECRET_KEY`, `PM_DATA_DIR`) in the service/task configuration.
- Configure restart-on-failure and log capture per tool.

### 6.3 macOS (launchd) / generic supervisor
- Use `launchd`, `supervisord`, or equivalent to run the same uvicorn command with `--workers 1`, correct working dir, env vars, and restart policy.

## 7) Containers / Docker (guidance)

No official image is bundled. Minimal example:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt
ENV PYTHONPATH=/app/src:/app \
    PM_WEB_SECRET_KEY=change-me \
    PM_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "ui.web.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```
Run: `docker run -d --restart unless-stopped -p 127.0.0.1:8000:8000 -v pmdata:/data -e PM_WEB_SECRET_KEY=... property-manager`
- Keep **`--workers 1`**.
- Mount persistent volume for `PM_DATA_DIR` (DB/exports/invoices) and back it up.
- Prefer publishing on localhost and fronting with TLS reverse proxy; if exposed directly, ensure network controls and HTTPS termination strategy are appropriate.

## 8) LAN / client access notes

- `127.0.0.1`: local machine only.
- `0.0.0.0`: all interfaces; open firewall deliberately for trusted clients only.
- Mobile browsers can "Add to Home Screen" against the web URL (use HTTPS/VPN for non-local networks where possible).
- TUI remote access alternative: SSH into host and run TUI.

## 9) Post-deploy verification (automated checks available)

- Web responds: `curl -fsS http://127.0.0.1:8000/health`, `/healthz`, and `/readyz`
- Doctor/integrity: `PYTHONPATH=src python -m app_core.doctor --json`
- Backup sanity: `python3 scripts/backup_runtime.py --backup` (safe, non-destructive) and verify result
- Confirm `PM_WEB_SECRET_KEY` set, `--workers 1`, correct `PM_DATA_DIR`, and TLS/proxy behavior if applicable

## 10) What is automated vs operational/manual

- **Automated/tooling:** bootstrap/setup scripts, dependency install, git fast-forward updates (when clean), in-app update check/apply (ff-only), DB migrations on startup, health probes, doctor checks, backup/verify helpers.
- **Operational/manual:** choosing topology and TLS/proxy/VPN, DNS/domain/cert lifecycle (except ACME automation at proxy if configured), firewall/network segmentation, secret generation/rotation/storage, service account/permissions hardening, monitoring/alerting/SIEM, backup scheduling/offsite retention, restore execution and validation, capacity/DR planning, go-live/pilot sign-off.
