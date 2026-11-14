# Native / Standalone Builds (Nuitka)

This document describes how to produce **native/standalone executables** (e.g. via **Nuitka**) and publish them as **GitHub Release assets** so the landing page can auto-detect and offer downloads.

> Status: packaging scaffolding provided. Native binaries are produced when you run the build scripts/workflow for a tagged release on the target OS/architecture. Test thoroughly on clean machines.

## Goals and constraints

- **Bundle:** Python runtime + app code (`src/app_core`, `ui`, `launcher.py`, templates/static) into a distributable executable/folder.
- **Runtime data preserved:** DB, exports, invoices, and `PM_DATA_DIR` must remain external/user-writable (not inside read-only bundle paths). The app already supports `PM_DATA_DIR` and default data locations—verify behavior in packaged builds.
- **Web UI:** if web mode is included, bundle `ui/web/templates` and `ui/web/static` and ensure FastAPI/Jinja2/uvicorn dependencies are included.
- **SQLite single-writer:** web must run with `--workers 1`.
- **Cross-platform:** build **on each target OS** (Windows build for Windows, Linux for Linux, macOS for macOS). Cross-compiling is limited; use CI matrix runners.

## Prerequisites (build machine)

- Python 3.11+ recommended, pip, virtualenv
- C compiler toolchain (MSVC Build Tools on Windows, gcc/clang/build-essential on Linux, Xcode CLT on macOS)
- App dependencies installed (`requirements.txt`, `requirements-web.txt` if bundling web)
- Nuitka: `pip install nuitka ordered-set zstandard` (and `imageio` if needed by plugins)

## Build (local)

### Linux / macOS
```bash
python3 -m venv .venv-build
. .venv-build/bin/activate
pip install -U pip
pip install -r requirements.txt -r requirements-web.txt
pip install nuitka ordered-set zstandard

# Standalone folder build (recommended for easier debugging of bundled data)
python -m nuitka \
  --standalone \
  --enable-plugin=anti-bloat \
  --include-package=app_core \
  --include-package=ui \
  --include-data-dir=ui/web/templates=ui/web/templates \
  --include-data-dir=ui/web/static=ui/web/static \
  --include-data-files=pyproject.toml=pyproject.toml \
  --output-dir=dist/nuitka \
  launcher.py

# Output: dist/nuitka/launcher.dist/ (run launcher binary inside)
```

### Windows (PowerShell)
```powershell
py -3 -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt -r requirements-web.txt
pip install nuitka ordered-set zstandard

python -m nuitka ^
  --standalone ^
  --enable-plugin=anti-bloat ^
  --include-package=app_core ^
  --include-package=ui ^
  --include-data-dir=ui/web/templates=ui/web/templates ^
  --include-data-dir=ui/web/static=ui/web/static ^
  --include-data-files=pyproject.toml=pyproject.toml ^
  --output-dir=dist\nuitka ^
  launcher.py
```

> Adjust flags as needed (icons, onefile, console/windowed, additional hidden imports/data). For `--onefile`, startup/extraction behavior and data-path handling need extra validation.

## Suggested release asset naming (for landing page auto-detect)

Publish assets with clear OS/arch tokens so `index.html` can match them:

- `property-manager-windows-x64.zip` (or `.exe` installer)
- `property-manager-linux-x64.tar.gz` / `.AppImage`
- `property-manager-macos-x64.tar.gz`
- `property-manager-macos-arm64.tar.gz`
- Include checksums (e.g. `SHA256SUMS.txt`)

## GitHub Actions (automated builds on tag/release)

See `.github/workflows/release-native.yml` for a matrix workflow scaffold that builds on Windows/Linux/macOS runners and uploads artifacts to a GitHub Release (on tag push `v*` or manual dispatch). You may need to:

- grant workflow `contents: write` permission,
- refine Nuitka flags/includes per platform,
- add signing/notarization (macOS/Windows) for trusted distribution,
- add smoke tests for produced binaries.

## Landing page integration

`index.html` fetches the latest GitHub Release via the public API and tries to match assets for the detected OS/arch. If a matching asset is found it shows a **Download** button; otherwise it falls back to script installers. Keep asset names consistent with the patterns above.

## Testing checklist for packaged builds

- [ ] Launch TUI and Web modes on a clean target machine
- [ ] Verify templates/static load correctly in web mode
- [ ] Confirm `PM_DATA_DIR`/default data dir is writable and external to bundle
- [ ] Run with `--workers 1` for web; test unlock/CSRF/core flows
- [ ] Verify backup/restore, exports/invoices, and update behavior expectations
- [ ] Validate startup with no preinstalled Python on target (true standalone)

## Alternatives

- **PyInstaller**: similar standalone bundling, different hooks/behavior.
- **Briefcase/BeeWare**: app-packaging approach (platform-native bundles/installers).
- **Script installer (current)**: `scripts/install.sh` / `scripts/install_windows.ps1` remain the most reliable cross-environment method today, with in-app/script updates and `REPAIR=1` recovery.
