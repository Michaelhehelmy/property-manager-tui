# Packaging, Distribution, and Modularization Plan

This document defines the practical packaging and modularization strategy for **SPMS** (`spms`), and the implementation status.

## Goals

- Provide multiple **delivery formats** for different environments (server, Raspberry Pi, desktop, mobile browser/PWA, container).
- Keep the **core app modular**: shared backend (`src/app_core/`) reusable by the web UI and optional components (assistant, integrations).
- Keep base installs **lightweight** (important for Raspberry Pi/Termux/constrained devices) via optional extras.
- Preserve operational constraints: SQLite **single-writer** web runtime (`--workers 1`), external/persistent `PM_DATA_DIR`, backups/restore safety.

## Current state (implemented in repo)

| Package / format | Status | Notes |
|---|---|---|
| **Source install scripts** | ✅ Existing | `scripts/install.sh`, `scripts/setup*.sh`, Windows scripts; `launcher.py`/`main.py` entry points. |
| **Python project metadata & extras** | ✅ Existing | `pyproject.toml` (`spms`), console scripts `property-manager`/`pm`, extras `assistant-ml`, `assistant-llm`, `dev`. |
| **Python sdist/wheel build config** | ✅ Implemented | Setuptools config includes `ui` package data (templates/static/manifest/icons/SW) for distributable wheels/sdists. |
| **Python package CI build** | ✅ Implemented | `.github/workflows/release-python-package.yml` builds sdist/wheel, runs `twine check`, uploads artifacts on tags/dispatch; optional PyPI publish when `PYPI_API_TOKEN` secret is configured. |
| **Container image (GHCR)** | ✅ Existing | `.github/workflows/release-container.yml` publishes multi-arch `ghcr.io/michaelhehelmy/spms` on tags/dispatch (`--workers 1`, persistent `/data`). |
| **Native binaries (Nuitka)** | ✅ Existing | `.github/workflows/release-native.yml` and `docs/NATIVE_BUILDS.md` (Windows `.exe`, Linux binary/AppImage path, macOS `.dmg`). |
| **PWA installability** | ✅ Implemented/Existing | `ui/web/static/manifest.webmanifest`, icons, base template meta/links, and minimal service worker (`ui/web/static/sw.js`) registered for static app-shell caching (network-first for navigations; no caching of transactional POST/HTML data). |
| **Android app package** | Planned (recommended: wrapper) | Best fit: thin WebView/TWA wrapper APK/AAB pointing to local/LAN SPMS URL (reuses web UI). Full Kivy rewrite is a separate product effort. |
| **Linux OS packages (`.deb`/`.rpm`)** | Planned | Recommended for Raspberry Pi/server appliances with systemd unit and `PM_DATA_DIR` conventions. |
| **Windows/macOS installers** | Planned | MSI/Inno Setup for Windows, signed/notarized `.pkg`/improved `.dmg` for macOS. |
| **Helm chart (Kubernetes)** | ✅ Implemented (skeleton) | `deploy/helm/spms` chart for single-replica SQLite-safe deployment (Recreate strategy, `--workers 1`, PVC, health/readiness probes, secret/config). |

## Logical implementation order

1. **Stabilize canonical build/package metadata (done):** ensure `pyproject.toml` produces complete wheels/sdists including web assets and entry points.
2. **Automate distributable artifacts (done/ongoing):** CI builds for Python package, container, and native binaries on tagged releases.
3. **Improve client installability (done):** PWA manifest/icons/service worker for “install to home screen” on mobile/desktop/LAN screens.
4. **Harden deployment packages (next):** `.deb`/systemd package for Raspberry Pi/Ubuntu, improved Docker Compose examples, health/readiness docs alignment.
5. **Platform-specific installers (next):** Windows installer, macOS signing/notarization, optional Android WebView/TWA wrapper.
6. **Deeper modular boundaries (incremental):** keep `app_core` UI-agnostic, keep assistant/integrations optional via extras and lazy loading, avoid premature repo splits.

## Modularity boundaries (target architecture)

- **Core (`app_core`)**: domain logic, DB/migrations, config/paths, services, integrity/backup primitives, assistant core interfaces/tools.
- **Web UI (`ui/web`)**: FastAPI routes, templates/static/PWA assets, web auth/session/CSRF integration; depends on core.
- **Optional assistant capabilities**: base assistant remains lightweight; ML/LLM runtime/models shipped as opt-in extras (`assistant-ml`, `assistant-llm`) and/or external model/data packages.
- **Optional hardware/integration adapters**: printing (file/CUPS/raw TCP), barcode/label formats, future device bridges behind small interfaces.
- **Delivery bundles** (container/native/OS packages/PWA/Android wrapper): package selected components per target; do not duplicate business logic.

> PWA is a **client install mode** of the web app (served by SPMS), not a Python dependency package.

## Publishing & credentials (manual/external)

- **PyPI publish:** configure repository secret `PYPI_API_TOKEN` (or switch to trusted publishing) to enable the publish job in `release-python-package.yml`.
- **GHCR/container:** uses `GITHUB_TOKEN` in workflow (already configured pattern); package visibility/permissions managed in GitHub.
- **Signing/notarization:** Windows code signing, macOS notarization, Android signing keys require external certificates/keystores.
- **GitHub Packages cleanup:** deleting old packages (e.g. `property-manager-tui`) requires token scopes such as `read:packages` and `delete:packages` (or owner action in GitHub UI). See README/operations notes.

## Validation checklist for package changes

- `python -m build` produces wheel+sdist containing `ui/web/templates` and `ui/web/static` assets.
- Web UI serves `/static/manifest.webmanifest`, icons, and `/static/sw.js`; install prompt/Add-to-home-screen works over supported contexts (HTTPS recommended for non-localhost).
- `PYTHONPATH=src pytest -q` remains green (and coverage targets maintained as applicable).
- Container/native builds continue to run with `--workers 1` and persistent `PM_DATA_DIR`.