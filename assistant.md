# AI Manager Assistant — Effort, Evolution & Enhancement Plan

**Project:** `property-manager-tui` (`app_core` backend shared by Textual TUI and FastAPI/HTMX web UI)  
**Assistant focus:** grounded, read-only (by default) manager/business-partner helper over real backend data  
**Effort credited to:** Michael Helmy (`Michaelhehelmy`) — design, implementation, iteration, and integration across backend, TUI, web, NLU/learning, and deployment  
**Document date:** 2026-11-11  
**Resource constraint (current planning):** **< 400 MB** total budget for assistant model/assets/runtime (on top of the base app), favoring lightweight/local options that run on constrained devices (incl. iSH/Termux-class environments where feasible)

---

## 1) Effort & evolution log (all work done to make this agent)

The table below summarizes the cumulative effort to build and mature the assistant and the platform capabilities it relies on (grounded in repository history and delivered milestones).

| Phase / Date | Effort (what was done) | Key components / areas | Outcome & impact |
|---|---|---|---|
| **Foundation & app structure** (2026-11-09) | Initial property-manager core/TUI, restructuring into `src/app_core` + `ui/tui` + `ui/web`, launcher (`TUI`/`Web`), FastAPI+HTMX web UI, cleanup/refactors, README/run-test docs | `app_core`, `ui/*`, `main.py`, `launcher.py`, repo structure | Shared backend with two frontends; stable base for grounded assistant tooling |
| **Quality, reliability & health** (2026-11-09) | Achieved **100% line coverage** for `--cov=src/app_core --cov=ui` (798 passed, 5425 stmts, 0 miss @ `86d12c7`), core/web/TUI improvements, fixes (incl. TUI loading crash, imports), added health probes `GET /health`, `/healthz` | `tests/*`, `ui/web/server.py`, TUI/web fixes | High-confidence, testable backend and observable web runtime for safe assistant grounding |
| **Domain depth (folio/POS/meals/recipes)** (2026-11-09–10) | Ledger integrity and real backend flows; POS/inventory/ingredients; **recipes/BOM** (ingredient auto-deduction, COGS) DB v5; **meal registration** (name/description/ingredients/preparation) with linked POS inventory + recipe sync, auto ingredient deduction DB v6; web/TUI wiring | `db.py` migrations, `services.py` (recipes/meals/POS/ledger), POS/inventory UIs | Rich, accurate operational data (orders, meals, stock, costs) for the assistant to query and explain |
| **Policy, safety & data lifecycle** (2026-11-10) | Reservation operation password-policy refinements (manager corrections vs accounting/policy-sensitive actions; password required to cancel/no-show paid/partially paid; checkout override/refunds/voids guarded), seed/master-data lifecycle | `services.py`, reservation routes/UI, tests | Correct, auditable business rules; assistant stays read-only and aligned with real constraints |
| **Seeding & setup/portability** (2026-11-10) | **Seed config** export/import (JSON master data: rooms, rate plans, inventory, expense categories, meals) with guarded apply (unlock + super password + confirm, no-op-data requirement) and recipe/inventory sync; room add/delete with guards; exposed in **Web & TUI**. Dynamic setup/deploy for **iSH/Alpine, Termux, Debian/Ubuntu, macOS** (`scripts/setup.sh`, `MODE/ACTION/SKIP_WEB`) and **Windows** (`scripts/setup_windows.ps1/.cmd`); README/usage | `services.py` (seed/rooms), Settings UIs, `scripts/*`, `README.md` | First-time seeding via “export→edit→upload”, manageable rooms/master data, reproducible install/run across target environments |
| **Assistant v1 — grounded core** (earlier iterations) | Grounded, read-only manager assistant over real `app_core.services`/DB (no mock data), web & TUI assistant surfaces, optional OpenAI-compatible **talking/full_llm** (tool-calling) modes, unlock-aware behavior | `app_core/assistant.py`, `ui/web/routes+templates/assistant`, `ui/tui` assistant tab | Trustworthy answers sourced from the real app state; foundation for NLU and partner insights |
| **Assistant v2 — tiered NLU & learning (SetFit-ready)** (2026-11-10, `c0a67f8`) | Rules-based intent/slot router with confidence, clarification, and compound handling; **feedback/corrections** (DB v7 `assistant_feedback` + JSONL), **instant exemplar-memory learning**, 👍/👎 + correction UI in Web/TUI; seeded complex intent dataset (20 situations × 10 prompts); `train`/`eval` scripts for **sklearn/SetFit** targeting `paraphrase-MiniLM-L3-v2` (<150 MB); intent/confidence/tools exposed for traceability | `assistant.py`, `db.py` v7, `ui/*/assistant`, `data/assistant_nlu/*`, `scripts/train|eval_assistant_intent.py` | Understands more phrasings, asks when unsure, and **learns from user corrections** with a lightweight, upgradable NLU path |
| **Assistant fixes & intent coverage** (2026-11-10, `f910c9a`, `440d3b2`) | Fixed low-confidence fallback/clarification behavior and string issue; added grounded **`property_stats`** intent/tool (guests/rooms/reservations, by status, in-house, arrivals/departures today); expanded seed intents (baseline eval ~76.5% on seed, key cases correct) | `assistant.py`, seeds/eval | Correct routing for counts/stats and ambiguous cases; clearer guidance instead of capability spam |
| **Assistant v3 — business partner + full in-app tools (no new deps)** (2026-11-10, `6646c25`) | **Many grounded read-only tools**: business-partner briefing/insights, today ops, property/housekeeping stats, outstanding balances (AR), inventory alerts, top sellers, expenses/sales breakdown, reservation pipeline, room availability, menu, recent activity, period comparison, guest last-visit spend. **Smarter no-dep NLU**: weighted intent scoring, better slots/context/carryover/fuzzy cues, compound planning, clarification. **Deterministic partner narrative**: snapshot / what’s going well / watch-outs / suggested actions from real data | `assistant.py` (tools, NLU, formatting, briefing) | Assistant behaves as a practical **business partner** (KPIs, risks, opportunities, actions) with broad, factual in-app visibility — zero added dependencies |
| **Traceability, integrity & updates in assistant/docs** (current) | Exposed and documented grounded read-only capabilities for **app version/update status** (local state, how to check/apply in-app with changelog; data preserved), **expiring batches**, **inventory batch/lot lists**, and **barcode/SKU/lot trace** (item/batch resolution with lot, received/expiry, remaining, movements), alongside integrity/health checks. Clarified in-app update/check/changelog/apply flows (Web & TUI), data-preservation behavior, traceability features, and `--workers 1` guidance in docs; appended intent seeds/examples. | `assistant.py`, `assistant.md`, `README.md`, `data/assistant_nlu/intents_seed.jsonl`, `scripts/*` (docs/help) | Assistant can clearly answer update/version, batch/expiry, and traceability questions from real data with aligned deployment/update documentation |
| **Guest 360° portfolio** (2026-11-11, `47abb21`) | `get_guest_portfolio`: profile, lifetime stats (stays/nights/charges/paid/outstanding/POS, first/last/favorite), all trips/stays with folio/balance, per-trip POS, payments, saved invoice files, aggregates/top items. Enriched guest index (outstanding). **Web** full 360° guest detail (trips/folio/POS/payments/invoices/aggregates) and **TUI** rich portfolio modal with drill-down; CRM/index enhancements | `services.py`, `ui/web/routes+templates/guests`, `ui/tui/modals.py` | Complete, real-data guest tracking (all trips, POS/activity, payments, invoices) exposed in UI and ready for assistant use |
| **Guest portfolio & bookable activities/trips in assistant** (2026-11-11, `0be07c0`) | **DB v8**: `activities` catalog + `pos_tabs.activity_id` (migration). Services: register/list/update/toggle activities, **`book_activity`** (creates POS charge linked to activity → posts to reservation **folio/balance/invoice** like POS), bookings listing & **per-activity stats**. **Web**: book activity on reservation, manage activities+stats in POS/Inventory. **Assistant**: `get_guest_portfolio`, `list_activities`, `get_activity_stats`, `list_activity_bookings` tools + intents/NLU/capabilities | `db.py` v8, `services.py`, `assistant.py`, POS/reservation web UIs | Registered excursions/trips/experiences are orderable, financially integrated, statistically tracked, and fully queryable by the assistant (incl. guest portfolios & bookings) |

**Cumulative result:** a grounded, traceable, increasingly smart business-partner assistant with instant feedback learning, broad read-only tool coverage (ops, finance, guests/portfolios, activities), deterministic insights, and multi-platform delivery (TUI/Web) — built iteratively by **Michael Helmy** with an emphasis on correctness, safety, portability, and lightweight/SetFit-ready NLU.

---

## 2) Current assistant capabilities & architecture (snapshot)

- **Grounded & safe:** facts from real tools/services/DB only (no invented records); read-only assistance, unlock/permission-aware flows; refuses unsupported/unsafe actions.
- **Interfaces:** Web assistant (`/assistant`, feedback/correction) and TUI Assistant tab (intent/confidence/tools/feedback), plus guest/ops UIs that supply the same real data.
- **NLU/routing:** exemplar memory (instant) → optional ML (`ASSISTANT_NLU=auto|rules|sklearn|setfit`) → weighted rules/slots, confidence, clarification/disambiguation, compound planning, basic context carryover.
- **Response modes:** `core` (deterministic), `talking` (core + optional OpenAI-compatible rephrase, grounded), `full_llm` (tool-calling, grounded to tools).
- **Learning loop:** 👍/👎 + corrections → `assistant_feedback` (DB) + JSONL → instant exemplar reuse and datasets for retraining/eval.
- **Tooling (representative, read-only):** briefing/decision support, stats/today ops, reports & comparisons, occupancy/housekeeping/pipeline/availability, AR/inventory alerts/**expiring batches**/**inventory batch/lot lists**/**barcode/SKU/lot trace** (resolved item/batch with lot, received/expiry, remaining, and recent movements), expenses/sales/top sellers/menu/rate plans, ledger/data **integrity/health checks**, activities, guest search/profile/**portfolio**/last visit/top items/spend/**activity bookings**, reservation summary/balance, recent activity, and **app version/update status** (local version/commit/branch and how to check/apply updates in-app).
- **In-app updates & integrity (app surfaces):** Web & TUI expose **Settings → App updates** to check for updates, view changelog/details, and apply a safe git fast-forward update for git-based installs; runtime data (DB, exports, invoices, configured data dir) is preserved and restart is required after apply. Integrity/health checks are available in-app; web also serves `GET /health` and `GET /healthz`. Deploy the web server with `--workers 1` (SQLite).

### 2.1 Examples (grounded, read-only)

- **Batch/expiry/trace:** “what expires soon?”, “expiring batches in 7 days”, “list inventory batches”, “trace barcode LOT-123” / “trace SKU ...” (shows resolved item or lot with received, expiry, remaining, and recent movements when available).
- **Integrity/health:** “run a health check”, “ledger integrity check”, “any data integrity issues?” (read-only ledger/data checks; CLI doctor also available: `PYTHONPATH=src python -m app_core.doctor`).
- **App version/updates:** “what app version am I running?”, “check for updates”, “is there a changelog?” (local grounded status plus guidance: Settings → App updates to check/apply with changelog; data preserved; restart after apply).

---

## 3) Deployment, updates, integrity & security (assistant-relevant, current implementation)

- **Deployment (Web):** run FastAPI with **`--workers 1`** (SQLite/shared state). Use `HOST=127.0.0.1` for local or `0.0.0.0` for LAN; prefer HTTPS (reverse proxy) or a private network (VPN/Tailscale) for remote access. Health probes: `GET /health`, `GET /healthz`.
- **Updates (Web & TUI, git-based installs):** **Settings → App updates** → **Check for updates** (shows changelog/commit details) → **Apply/Update** (fast-forward). Runtime data is preserved (DB, exports, invoices, configured data dir such as `PM_DATA_DIR`/`data/` are not modified by source updates). Restart after apply. If local source changes exist, commit/stash/discard before applying. Manual CLI: `git pull --ff-only` then re-run setup; DB migrations run on startup (additive).
- **Batch/barcode/traceability:** inventory supports traceable barcodes/SKUs/lots with batch/lot tracking (received, expiry, remaining quantity/cost, status/expiry state, movements). The assistant and UIs can show expiring/expired batches, list batches/lots, and trace a barcode/SKU/lot code to the resolved item or lot (including received/expiry/remaining and recent movements).
- **Integrity/doctor:** read-only ledger/data integrity checks are available in-app (and via the assistant as health/integrity queries). A CLI **doctor** provides operational health checks and safe backup/verify helpers (`PYTHONPATH=src python -m app_core.doctor`, see `--help` for `--json/--check-updates/--backup/--backup-to/--verify-backup`).
- **Backup/restore:** create DB backups from Settings (**Backup now**, subject to unlock/CSRF where applicable) stored in the exports/data area; keep regular backups and back up before upgrades/migrations. Runtime data (DB/exports/invoices/data dir) is preserved across updates. Restore is a manual offline process (stop app, replace active DB/files from backup, restart; migrations apply on startup) — verify with health/integrity checks afterward.
- **Security model (Web):** all state-changing requests are **CSRF**-protected (form tokens; middleware/handlers for JSON/header as applicable). Sensitive/destructive/admin mutations require **super-password unlock** (and CSRF), while some routine operational/front-desk flows remain usable with CSRF only (see `docs/web_authz_matrix.md` for the route-group matrix). Assistant asks/feedback follow configured unlock/allowlist policy and remain read-only. Use strong `PM_WEB_SECRET_KEY` for shared deployments.

---

## 4) Enhancement plan — friendlier replies & smarter assistant (< 400 MB)

**Goal:** make replies consistently **warm, clear, and conversational** while becoming **smarter** at understanding, reasoning over grounded data, and helping proactively — without sacrificing factual correctness.  
**Budget:** < 400 MB for assistant models/assets/runtime (choose options that fit together within this envelope).

### 3.1 Plan overview

| Track | Objective | What to do | Primary impact | Effort | Deps / footprint (fits < 400 MB) |
|---|---|---|---|---|---|
| **P1. Core reply UX (no LLM)** | Friendlier by default, even in `core` | Human answer templates per intent (replace JSON dumps) with answer-first summaries, bullets, key IDs/dates/amounts, scoped ranges; warm consistent “business partner” framing; better clarifications/disambiguation/empty states/errors; 1–2 **proactive, data-informed suggestions**; improved follow-up context (resolved entities/period/result memory) | Friendliness + clarity + usefulness | Medium | **None** (stdlib/existing). ~0 MB extra |
| **P2. Smarter understanding (lightweight)** | Higher intent/slot accuracy & robustness | Expand cues/synonyms/paraphrases & seeds (incl. portfolio/activities/invoices), better date/entity normalization, confidence calibration & compound handling; train/load **SetFit** (default `paraphrase-MiniLM-L3-v2` or similar small ST model), grow eval from feedback, periodic retrain from corrections/exemplars | Smarter routing/understanding | Low–Med | **Optional:** `setfit` + small sentence-transformer (**~80–120 MB** typical). Keep rules/exemplars as fallback |
| **P3. Deterministic partner reasoning** | Smarter insights without hallucination | Deeper grounded insights: deltas/trends vs prior period, anomalies (AR/occupancy/stock/housekeeping), per-intent takeaways and “why” from data; goal-driven multi-tool composition (synthesize, don’t dump); richer compare/ops/guest narratives | Smarter, actionable answers | Medium | **None**. ~0 MB extra |
| **P4. Grounded “talking” rephrase (small local LLM, opt-in)** | Natural, consistently friendly conversation | Use **core grounded result as source of truth** and a small local instruct model to **rephrase/summarize only** (no new facts): structured, warm, concise; configurable tone/verbosity; strict prompt: preserve all numbers/IDs/dates/names, state unknowns. Fallback to core on failure | Biggest friendliness/naturalness lift | Medium | **Optional:** quantized local model via `llama.cpp`/compatible runtime (e.g. ~0.5B-class GGUF Q4, ~**250–380 MB**) **or** external OpenAI-compatible endpoint. Fits within < 400 MB when chosen carefully |
| **P5. Full tool-calling & personalization (opt-in)** | Flexible multi-step help | Harden `full_llm` (tools-or-ask, no guessing, step limits, summarization, core fallback); optional preferences (tone/detail/default period) stored in settings | Smarter complex queries | Med | Uses P4/local model or external endpoint; prompt/policy work mostly **0 MB** |

### 3.2 Recommended < 400 MB configurations

| Config | Contents | Approx. footprint | Best for |
|---|---|---|---|
| **Lean (default-friendly core)** | P1 + P2 (SetFit MiniLM-L3-v2) + P3 | **~100–150 MB** | Constrained devices, offline/no-extra-runtime; reliable, grounded, much friendlier/smarter than today |
| **Lean + local rephrase** | P1–P3 + small quantized instruct model for `talking` rephrase (core stays factual) | **~300–400 MB** | Best balance under budget: natural friendly replies with grounded accuracy |
| **External LLM (zero local model weight)** | P1–P3 + `talking`/`full_llm` via configured OpenAI-compatible endpoint | **~0–150 MB** local (NLU only) | When a server/hosted model is available; maximum fluency without local model storage |

> Notes: prefer **rephrase-over-grounded-core** (P4) before unconstrained generation; keep **rules+exemplars** fallback; measure with eval + user feedback (👍/👎, corrections) and re-establish/track coverage on assistant changes.

### 3.3 Phased rollout

| Phase | Scope | Deliverables | Success signals |
|---|---|---|---|
| **Phase 1** | P1 | Templated natural answers for high-traffic intents (guest/portfolio/reservation/reports/ops/AR/inventory/activities), warm framing, proactive suggestions, improved clarification/context | Fewer raw-JSON replies, clearer answers, better follow-ups (qualitative + feedback) |
| **Phase 2** | P2 | Expanded seeds/eval, trained & loaded SetFit model, better calibration/compound/entity-date handling, retrain loop documented | Higher intent accuracy/eval scores, fewer misroutes/clarifications |
| **Phase 3** | P3 | Deterministic deltas/anomalies/takeaways and synthesized multi-tool responses (esp. briefing/compare/guest/ops) | More actionable, “partner-like” insights grounded in data |
| **Phase 4** | P4 (opt-in) | Grounded `talking` rephrase with small local model (or endpoint), tone controls, safe fallback; optional P5 hardening | Consistently friendly, natural tone with preserved factual accuracy |

---

## 5) Principles (guardrails)

- **Grounded truth:** never invent guests, amounts, dates, IDs, bookings, or actions; cite/derive from real tools/data or state uncertainty.
- **Read-only by default:** assist and explain; do not perform unsafe/mutating actions outside explicit, guarded app flows.
- **Lightweight & portable:** honor the < 400 MB budget; degrade gracefully to core/rules when models are unavailable.
- **Learn safely:** user corrections improve routing via exemplar/retrain loops with traceability (intent/confidence/tools/feedback).
- **Measurable:** track NLU eval and user feedback; keep tests/coverage healthy for core behavior.

---

*Maintained as part of the `property-manager-tui` repository (`assistant.md`). Credit: assistant evolution and integration effort led and delivered by **Michael Helmy**.*