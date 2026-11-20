# SPMS Android TWA (Trusted Web Activity) scaffold

Use a TWA to package the SPMS PWA as an Android app (recommended when serving SPMS over HTTPS with a valid domain).

## Prerequisites
- Node.js/npm
- Android SDK/build tools, JDK
- SPMS served over **HTTPS** with PWA assets (`/static/manifest.webmanifest`, icons, service worker)

## Quick start (Bubblewrap)
1. Install Bubblewrap CLI: `npm i -g @bubblewrap/cli`
2. Initialize from your deployed manifest URL:
   - `bubblewrap init --manifest=https://your-spms-host/static/manifest.webmanifest`
3. Build APK/AAB:
   - `bubblewrap build`
4. Configure Digital Asset Links on the server (`/.well-known/assetlinks.json`) to verify the app-domain relationship.

## Notes
- For local/LAN-only HTTP deployments, a WebView wrapper (`deploy/mobile/android-webview/`) is simpler than TWA.
- Keep `PM_WEB_SECRET_KEY`, HTTPS/TLS, and reverse proxy hardening for non-local access.
- SPMS must run with `--workers 1` on SQLite deployments.