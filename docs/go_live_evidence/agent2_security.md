# Agent 2 Evidence: Security Hardening & Threat Validation

## Status
- Phase: Not started (awaiting G1 policy draft and target deployment profile)

## Prereqs to collect
- Approved deployment profile (OS/host, LAN/VPN/internet exposure, reverse proxy/TLS, users/roles)
- RISK-1 policy decision (Agent 1)
- Staging host/network details

## Planned tasks
- [ ] Set/verify production config: strong persistent `PM_WEB_SECRET_KEY`, `PM_DATA_DIR`, secure cookies (`PM_WEB_COOKIE_SECURE`/HTTPS), no insecure dev overrides, `--workers 1`
- [ ] TLS/reverse proxy or VPN-only access; firewall and least-privilege service user/permissions
- [ ] CSRF coverage audit for all mutating routes; negative tests (missing/invalid token)
- [ ] Auth/unlock/rate-limit/recovery/proof validation per policy
- [ ] Path/input confinement review (downloads, seed import/export, labels/exports/invoices, printer/subprocess)
- [ ] Data exposure review (health/errors/logs/assistant)
- [ ] Dependency/runtime risk inventory and pinning recommendations
- [ ] Residual risk register and security sign-off

## Evidence/log
- (to be filled)
