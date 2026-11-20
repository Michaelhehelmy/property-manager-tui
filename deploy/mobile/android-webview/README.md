# SPMS Android WebView Wrapper (scaffold)

Minimal Android WebView wrapper to load the SPMS web UI (local Termux server or LAN/remote URL). This reuses the existing SPMS web app/PWA instead of rewriting the UI.

## Configure
- Edit `app/src/main/res/values/strings.xml` and set `spms_url` to your SPMS server URL, e.g.:
  - Local on-device server (Termux): `http://127.0.0.1:8000`
  - LAN server: `http://<lan-ip>:8000`
  - Remote (recommended HTTPS): `https://spms.example.com`

## Build
- Open this folder in Android Studio, or build with Gradle + Android SDK installed.
- Minimum SDK/API and dependencies are defined in Gradle files; adjust as needed.
- For production, enable HTTPS, configure network security as appropriate, and consider adding app icons/splash/assetlinks for TWA if desired.

## Notes
- SPMS server must be running and reachable from the device (`--workers 1` on SQLite deployments).
- For non-local/shared access, use HTTPS/reverse proxy and set `PM_WEB_SECRET_KEY` on the server.
- This scaffold is a starting point; harden permissions, navigation, file downloads/uploads, and TLS settings for your environment.