# AI Manager Assistant — Capabilities, Operations & Evolution

**Project:** `property-manager-tui` (shared `src/app_core/` backend with Textual TUI `ui/tui` and FastAPI/HTMX web `ui/web`)  
**Assistant focus:** grounded, **read-only** manager/business-partner helper over real backend data  
**Effort credited to:** Michael Helmy (`Michaelhehelmy`) — design, implementation, iteration, and integration across backend, TUI, web, NLU/learning, and deployment  
**Document date:** 2026-11-13  
**Resource constraint (planning):** < 400 MB total budget for assistant model/assets/runtime (on top of base app), favoring lightweight/local options

---

## 1) Current capabilities (accurate)

- **Grounded & read-only:** answers are derived from real tools/services/DB state (no invented records). The assistant does not perform unsafe/mutating actions outside explicit, guarded app flows.
- **Surfaces:** available in **Web** (`/assistant`, with feedback/correction UI) and **TUI** (Assistant tab with intent/confidence/tools/feedback as implemented).
- **NLU/routing:** exemplar memory (instant reuse) → optional ML (`ASSISTANT_NLU=auto|rules|sklearn|setfit`) → weighted rules/slots, confidence scoring, clarification/disambiguation, compound planning, and basic context carryover.
- **Response modes:** `core` (deterministic), `talking` (core + optional OpenAI-compatible rephrase, grounded), `full_llm` (tool-calling grounded to tools) where configured.
- **Learning loop:** 👍/👎 feedback and corrections stored (DB/JSONL as implemented) for instant exemplar reuse and future retraining/eval.
- **Representative read-only tool coverage:** business-partner briefing/decision support, stats/today ops, reports/comparisons, occupancy/housekeeping/pipeline/availability, AR/outstanding balances, inventory alerts, **expiring batches**, **inventory batch/lot lists**, **barcode/SKU/lot trace** (resolved item or lot with lot code, received/expiry, remaining, recent movements when available), expenses/sales/top sellers/menu/rate plans, ledger/data **integrity/health checks**, activities/stats/bookings, guest search/profile/**360° portfolio**/last visit/top items/spend, reservation summary/balance, recent activity, and **app version/update status** (local version/commit/branch and how to check/apply updates in-app).

### 1.1 Example queries

- **Batch/expiry/trace:** "what expires soon?", "expiring batches in 7 days", "list inventory batches", "trace barcode LOT-123" / "trace SKU ...".
- **Integrity/health:** "run a health check", "ledger integrity check", "any data integrity issues?" (read-only; CLI doctor also available).
- **App version/updates:** "what app version am I running?", "check for updates", "is there a changelog?" (grounded local status + guidance).
- **Ops/guests/finance:** "today's ops", "outstanding balances", "guest portfolio for ...", "top sellers this month", "activity bookings/stats".

## 2) Operational requirements & behavior (current)

### 2.1 Deployment / runtime (Web)
- Run FastAPI with **`--workers 1`** (SQLite/shared state). Do not increase workers.
- Bind `127.0.0.1` for local-only; `0.0.0.0` for LAN (with firewall control). For remote/shared access use **HTTPS** reverse proxy or private network (VPN/Tailscale/SSH tunnel); do not expose plain HTTP publicly.
- Set **`PM_WEB_SECRET_KEY`** to a strong random value for non-local/shared deployments.
- Health probes: `GET /health`, `GET /healthz`.

### 2.2 Security model (Web) relevant to assistant
- All state-changing requests are **CSRF**-protected (form tokens; middleware/handlers for JSON/header as applicable).
- Sensitive/destructive/admin mutations require **super-password unlock** (and CSRF). Some routine operational/front-desk flows remain usable with CSRF only per policy.
- Assistant ask/feedback follow configured unlock/allowlist policy (`assistant_require_unlock` behavior as implemented) and remain read-only. Session-history clear is non-destructive to business data (CSRF-protected).
- Route-group reference: `docs/web_authz_matrix.md`.

### 2.3 In-app updates (git-based installs, Web & TUI)
- **Settings → App updates:** **Check for updates**, view **changelog/commit details**, and **Apply/Update** via **git fast-forward**.
- **Preserved data:** runtime data is not modified by source updates (DB, exports, invoices, configured data dir such as `PM_DATA_DIR`/defaults).
- **Restart required:** restart the app/server after applying an update to load the new version.
- **Local changes / non-FF:** if the working tree has local source changes, commit/stash/discard before applying. Diverged/non-fast-forward histories require manual git resolution.
- **Manual CLI:** `git pull --ff-only` then re-run setup to refresh deps if needed; DB migrations run on startup (additive).

### 2.4 Batch/barcode/traceability & FEFO
- Inventory supports traceable barcodes/SKUs/lots with batch/lot tracking (received, expiry, remaining/received quantity, unit cost, status/expiry state, movements).
- Assistant and UIs can surface expiring/expired batches, list batches/lots, and trace barcode/SKU/lot codes to resolved item or lot (received/expiry/remaining/movements when available).
- Workflows support **FEFO (First Expired, First Out)** considerations.

### 2.5 Integrity, doctor, backups & restore
- **In-app:** read-only ledger/data integrity checks are available (and via assistant queries).
- **CLI doctor:** `PYTHONPATH=src python -m app_core.doctor` (or `scripts/backup_runtime.* --doctor`) supports `--json`, `--check-updates`, `--strict`, and safe backup/verify helpers (`--backup [--backup-to <path>]`, `--verify-backup <path>` / `--verify`).
- **Backups:** Settings → **Backup now** (subject to unlock/CSRF as applicable) or CLI/script helpers; backups stored under exports/data area as applicable. Take regular backups and before upgrades/migrations.
- **Data preserved across updates:** DB/exports/invoices/data dir (`PM_DATA_DIR`/defaults) preserved by source updates.
- **Restore (manual, offline only):** no destructive one-click in-app restore. Stop app/server, verify backup, back up current DB, replace active DB/files with verified backup, start app (migrations on startup), then verify with doctor/integrity checks.

## 3) Effort & evolution log (summary)

The table below summarizes cumulative effort to build and mature the assistant and the platform capabilities it relies on.

| Phase / Date | Effort (what was done) | Key components / areas | Outcome & impact |
|---|---|---|---|
| **Foundation & app structure** (2026-11-09) | Initial core/TUI, restructuring into `src/app_core` + `ui/tui` + `ui/web`, launcher, FastAPI+HTMX web UI | `app_core`, `ui/*`, launcher | Shared backend with two frontends; base for grounded assistant |
| **Quality, reliability & health** (2026-11-09) | High test coverage for core/UI, fixes, health probes `GET /health`, `/healthz` | `tests/*`, web/TUI fixes | Testable backend and observable web runtime |
| **Domain depth** (2026-11-09–10) | Ledger integrity, POS/inventory/ingredients, recipes/BOM, meals with auto-deduction and sync | `db.py`, `services.py`, UIs | Rich operational data for grounded queries |
| **Policy, safety & data lifecycle** (2026-11-10) | Reservation/payment/void/refund policy guards, seed/master-data lifecycle | services/routes/UI | Auditable rules; assistant remains read-only and aligned |
| **Seeding & setup/portability** (2026-11-10) | Seed export/import, room/rate-plan/master-data flows, dynamic setup scripts for Unix/Windows | services, Settings UIs, `scripts/*` | Reproducible install/run and manageable master data |
| **Assistant v1 — grounded core** | Grounded read-only assistant over real services/DB, web & TUI surfaces, optional talking/full_llm modes | `app_core/assistant.py`, assistant UIs | Trustworthy answers from real app state |
| **Assistant v2 — tiered NLU & learning** (2026-11-10) | Rules + confidence/clarification/compound handling, feedback/corrections, exemplar learning, seeded intents, train/eval scripts (sklearn/SetFit-ready) | assistant, DB feedback, seeds/scripts | Better understanding and instant learning from corrections |
| **Assistant fixes & intent coverage** (2026-11-10) | Fallback/clarification fixes, property stats and intent coverage improvements | assistant, seeds/eval | More reliable routing for stats/ambiguous cases |
| **Assistant v3 — business partner + tools** (2026-11-10) | Many grounded read-only tools (briefing/insights, ops, AR, inventory, reports, comparisons, etc.), smarter no-dep NLU, deterministic partner narratives | `assistant.py` | Practical business-partner insights with broad in-app visibility |
| **Traceability, integrity & updates** | Grounded capabilities/docs for version/update status, expiring batches, batch/lot lists, barcode/SKU/lot trace, integrity/health; update/check/changelog/apply and `--workers 1` guidance | assistant, docs, seeds, scripts | Clear answers and aligned deployment/update documentation |
| **Guest 360° & activities/trips** (2026-11-11) | Guest portfolio (profile/lifetime/trips/POS/payments/invoices/aggregates), activities catalog/booking/stats integrated to folio; assistant tools/intents for portfolios/activities/bookings | services, DB, assistant, UIs | Complete guest tracking and bookable activities queryable by assistant |

**Cumulative result:** a grounded, traceable, increasingly smart business-partner assistant with instant feedback learning, broad read-only tool coverage, and multi-platform delivery (TUI/Web), built iteratively by **Michael Helmy** with emphasis on correctness, safety, portability, and lightweight NLU.

## 4) Enhancement plan — friendlier replies & smarter assistant (< 400 MB)

**Goal:** warmer, clearer, conversational replies while becoming smarter at understanding, reasoning over grounded data, and helping proactively — without sacrificing factual correctness.  
**Budget:** < 400 MB for assistant models/assets/runtime.

### 4.1 Tracks

| Track | Objective | What to do | Impact | Effort | Footprint |
|---|---|---|---|---|---|
| **P1. Core reply UX (no LLM)** | Friendlier by default in `core` | Human answer templates per intent, warm framing, better clarifications/empty states/errors, proactive data-informed suggestions, improved follow-up context | Friendliness + clarity | Medium | ~0 MB |
| **P2. Smarter understanding** | Higher intent/slot accuracy | Expand cues/synonyms/seeds, better normalization/calibration/compound handling; optional SetFit/small ST model; retrain/eval from feedback | Smarter routing | Low–Med | Optional ~80–120 MB |
| **P3. Deterministic partner reasoning** | Smarter insights without hallucination | Deltas/trends, anomalies, per-intent takeaways, multi-tool synthesis (not dumps) | Actionable answers | Medium | ~0 MB |
| **P4. Grounded "talking" rephrase (opt-in)** | Natural friendly conversation | Rephrase/summarize grounded core result only (no new facts), preserve numbers/IDs/dates/names, fallback to core | Biggest tone lift | Medium | Optional small quantized local model (~250–380 MB) or external endpoint |
| **P5. Full tool-calling & personalization (opt-in)** | Flexible multi-step help | Harden `full_llm` (tools-or-ask, no guessing, limits/fallback), optional preferences | Complex queries | Med | Uses P4/external; mostly policy/prompt work |

### 4.2 Recommended configurations

| Config | Contents | Approx. footprint | Best for |
|---|---|---|---|
| **Lean (default-friendly core)** | P1 + P2 (SetFit/small model) + P3 | ~100–150 MB | Constrained/offline; reliable grounded UX |
| **Lean + local rephrase** | P1–P3 + small quantized instruct model for `talking` | ~300–400 MB | Best balance under budget |
| **External LLM** | P1–P3 + talking/full_llm via OpenAI-compatible endpoint | ~0–150 MB local | Max fluency without local model storage |

### 4.3 Phased rollout

| Phase | Scope | Deliverables | Success signals |
|---|---|---|---|
| **Phase 1** | P1 | Templated natural answers for high-traffic intents, warm framing, proactive suggestions, improved clarification/context | Fewer raw dumps, clearer follow-ups |
| **Phase 2** | P2 | Expanded seeds/eval, trained & loaded lightweight model, calibration/compound/entity-date improvements, retrain loop | Higher intent accuracy, fewer misroutes |
| **Phase 3** | P3 | Deterministic deltas/anomalies/takeaways and synthesized multi-tool responses | More actionable partner-like insights |
| **Phase 4** | P4 (opt-in) | Grounded talking rephrase with safe fallback; optional P5 hardening | Natural tone with preserved factual accuracy |

## 5) Principles (guardrails)

- **Grounded truth:** never invent guests, amounts, dates, IDs, bookings, or actions; derive from real tools/data or state uncertainty.
- **Read-only by default:** assist and explain; do not perform unsafe/mutating actions outside explicit, guarded app flows.
- **Lightweight & portable:** honor < 400 MB budget; degrade gracefully to core/rules when models unavailable.
- **Learn safely:** corrections improve routing via exemplar/retrain loops with traceability.
- **Measurable:** track NLU eval and user feedback; keep tests/coverage healthy for core behavior.

---

*Maintained as part of `property-manager-tui` (`assistant.md`). Credit: assistant evolution and integration effort led and delivered by **Michael Helmy**.*
