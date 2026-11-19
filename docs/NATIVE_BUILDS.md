# Native / Standalone Builds (Nuitka)

This document describes how to produce **native/standalone SPMS executables** with **Nuitka** and publish them as **GitHub Release assets** (auto-detected by the landing page).

## Target outputs (current build scripts/workflow)

- **Windows:** single-file executable **`.exe`** (Nuitka `--onefile`)
  - Example: `spms-windows-x64.exe`
- **Linux:** single-file **binary/executable** (Nuitka `--onefile`) and best-effort **`.AppImage`** in CI/community script when `appimagetool` is available
  - Examples: `spms-linux-x64`, `spms-linux-x64.AppImage`
- **macOS:** **`.dmg`** containing a macOS **`.app`** bundle (Nuitka `--standalone --macos-create-app-bundle`, packaged with `hdiutil`)
  - Examples: `spms-macos-x64.dmg`, `spms-macos-arm64.dmg`

> Single-file builds (`--onefile`) may have slower cold-start times because they extract/cache runtime components. Test on clean target machines.

## Goals and constraints

- **Bundle:** Python runtime + app code (`src/app_core`, `ui`, `launcher.py`) and web assets (`ui/web/templates`, `ui/web/static`, `pyproject.toml`).
- **Runtime data preserved:** DB, exports, invoices, and `PM_DATA_DIR` must remain external/user-writable (not inside read-only bundle paths). The app supports `PM_DATA_DIR` and frozen-runtime data handling—verify on packaged builds.
- **Web UI:** web dependencies (FastAPI/Uvicorn/Jinja2/etc.) must be included/bundled.
- **SQLite single-writer:** web must run with `--workers 1`.
- **Cross-platform:** build **on each target OS/arch** (no reliable cross-compiling). Use CI matrix runners or native machines.

## Prerequisites (build machine)

- Python 3.11+ recommended, `pip`, `venv`
- C/C++ toolchain: MSVC Build Tools on Windows, `build-essential`/clang on Linux, Xcode Command Line Tools on macOS
- App dependencies: `pip install -r requirements.txt`
- Nuitka deps: `pip install nuitka ordered-set zstandard imageio`
- **Linux AppImage (optional):** `appimagetool` (CI attempts to download it if missing)
- **macOS DMG:** `hdiutil` (standard on macOS)

## Build (local)

### Windows (PowerShell) — single `.exe`
```powershell
py -3 -m venv .venv-build
.\.venv-build\Scripts\Activate.ps1
pip install -U pip
pip install -r requirements.txt
pip install nuitka ordered-set zstandard imageio

python -m nuitka `
  --onefile `
  --windows-console-mode=force `
  --assume-yes-for-downloads `
  --enable-plugin=anti-bloat `
  --include-package=app_core `
  --include-package=ui `
  --include-package=uvicorn `
  --include-package=fastapi `
  --include-package=starlette `
  --include-package=anyio `
  --include-package=jinja2 `
  --include-package=itsdangerous `
  --include-package=python_multipart `
  --include-package=multipart `
  --include-package=pydantic `
  --include-package=pydantic_core `
  --include-data-dir=ui/web/templates=ui/web/templates `
  --include-data-dir=ui/web/static=ui/web/static `
  --include-data-files=pyproject.toml=pyproject.toml `
  --output-dir=dist\nuitka `
  --output-filename=spms `
  launcher.py
# Output: dist\nuitka\spms.exe (name may vary by Nuitka version)
```

### Linux — single binary (+ optional AppImage)
```bash
python3 -m venv .venv-build
. .venv-build/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install nuitka ordered-set zstandard imageio

python -m nuitka \
  --onefile \
  --assume-yes-for-downloads \
  --enable-plugin=anti-bloat \
  --include-package=app_core \
  --include-package=ui \
  --include-package=uvicorn \
  --include-package=fastapi \
  --include-package=starlette \
  --include-package=anyio \
  --include-package=jinja2 \
  --include-package=itsdangerous \
  --include-package=python_multipart \
  --include-package=multipart \
  --include-package=pydantic \
  --include-package=pydantic_core \
  --include-data-dir=ui/web/templates=ui/web/templates \
  --include-data-dir=ui/web/static=ui/web/static \
  --include-data-files=pyproject.toml=pyproject.toml \
  --output-dir=dist/nuitka \
  --output-filename=spms \
  launcher.py
chmod +x dist/nuitka/spms* || true
# Output: dist/nuitka/spms or dist/nuitka/spms.bin (single-file binary)
# Optional: wrap in an AppImage using appimagetool (see scripts/community_native_build.sh / CI workflow)
```

### macOS — `.app` bundle in `.dmg`
```bash
python3 -m venv .venv-build
. .venv-build/bin/activate
pip install -U pip
pip install -r requirements.txt
pip install nuitka ordered-set zstandard imageio

python -m nuitka \
  --standalone \
  --macos-create-app-bundle \
  --macos-app-name=SPMS \
  --assume-yes-for-downloads \
  --enable-plugin=anti-bloat \
  --include-package=app_core \
  --include-package=ui \
  --include-package=uvicorn \
  --include-package=fastapi \
  --include-package=starlette \
  --include-package=anyio \
  --include-package=jinja2 \
  --include-package=itsdangerous \
  --include-package=python_multipart \
  --include-package=multipart \
  --include-package=pydantic \
  --include-package=pydantic_core \
  --include-data-dir=ui/web/templates=ui/web/templates \
  --include-data-dir=ui/web/static=ui/web/static \
  --include-data-files=pyproject.toml=pyproject.toml \
  --output-dir=dist/nuitka \
  --output-filename=spms \
  launcher.py

# Package as .dmg (single distributable file)
APP_BUNDLE="$(find dist/nuitka -maxdepth 2 -type d -name '*.app' | head -n1)"
hdiutil create -volname "SPMS" -srcfolder "$APP_BUNDLE" -ov -format UDZO dist/spms-macos.dmg
```

## Community/local build scripts

- **Linux/macOS (and Unix shells):** `scripts/community_native_build.sh`
  - Builds platform-appropriate output (`.exe` in Windows shells where applicable, Linux single binary + best-effort AppImage, macOS `.dmg`), writes logs/report to `dist/community-native/`, and can generate/open a sanitized GitHub issue URL.
- **Windows (PowerShell):** `scripts/community_native_build.ps1`
  - Builds a single-file `spms-windows-<arch>.exe` and writes logs/report to `dist/community-native/`.

## GitHub Actions (automated builds on tag/release)

See `.github/workflows/release-native.yml`:
- Triggers on tag push `v*` (and manual dispatch)
- Builds matrix: Windows x64 (`.exe`), Linux x64 (binary + best-effort `.AppImage`), macOS x64/arm64 (`.dmg`)
- Runs packaged-binary smoke tests (`--web` + `/healthz`)
- Uploads artifacts and publishes them to the GitHub Release on tags

You may need to refine flags, add signing/notarization (macOS/Windows), and adjust AppImage/tooling for additional Linux targets.

## Suggested release asset naming (landing page auto-detect)

Publish assets with clear OS/arch tokens (and platform-native extensions):

- `spms-windows-x64.exe`
- `spms-linux-x64` (single binary) and/or `spms-linux-x64.AppImage`
- `spms-macos-x64.dmg`
- `spms-macos-arm64.dmg`
- Include checksums (e.g. `SHA256SUMS.txt`)

Keep `index.html` asset-matching patterns aligned with produced filenames/extensions.

## Testing checklist for packaged builds

- [ ] Launch on a clean target machine with no preinstalled Python (true standalone)
- [ ] Test startup/help/diagnostics: `./spms --help`, `./spms --diagnostics --save-report ./diag` (Windows: `spms.exe ...`)
- [ ] Start web UI and verify: `./spms --web --host 127.0.0.1 --port 8000 --no-browser`, then check `/healthz` and `/readyz`
- [ ] Verify templates/static load correctly in web mode
- [ ] Confirm `PM_DATA_DIR`/default data dir is writable and external to bundle; runtime data (DB/exports/invoices) persists across restarts/updates
- [ ] Run with `--workers 1` only; test unlock/CSRF/core flows and LAN access (`--host 0.0.0.0`) if needed
- [ ] On Linux, validate binary and `.AppImage` separately (if both produced) and check `chmod +x`/glibc compatibility
- [ ] On macOS, validate `.dmg` mount/install/run and Gatekeeper/signing/notarization behavior as applicable

## Alternatives

- **PyInstaller**: similar bundling, different hooks/behavior (`--onefile` or folder/app bundle modes).
- **Briefcase/BeeWare**: platform-native packaging approach.
- **OS installers/packages:** `.msi`/installer `.exe` (Windows), signed/notarized `.pkg` (macOS), `.deb`/`.rpm` (Linux) can wrap the produced binaries/bundles for managed installs.
- **Script installer (current):** `scripts/install.sh` / `scripts/install_windows.ps1` remain reliable for source-based installs/updates.
