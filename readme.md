# `README.md` — Updated

```markdown
# Sukoon Camp — Property Manager

A terminal (TUI) property-management + POS + accounting system for a small
hospitality business. Single-manager, single-currency (EGP), accrual-based
accounting, with a first-run super password protecting sensitive actions.

Built with **Textual**, **textual-plotext**, and **SQLite**. No network calls,
no external services.

---

## 1. Quick start

```bash
pip install textual textual-plotext
python sukoon.py
```

**First launch sequence:**
1. Animated `sukoon` loading screen
2. DB created (schema version **3**) and seeded from `services._SEED`
3. Immutable gift attribution inserted (see §10.3)
4. Super password prompt (mandatory — cannot be changed later *from this prompt*; can be changed from Settings after)
5. App opens in English

**To re-seed from scratch**: delete `property_manager.db` and relaunch.

**Schema guard**: the app refuses to open any existing DB whose
`schema_version` ≠ the one in `SETTINGS_CATALOG` → `schema_version`. Since the
current version is **3**, any DB created before the split-into-4-files refactor
must be deleted.

---

## 2. Project structure

```
.
├── sukoon.py            # Entry point, LoadingScreen, PropertyManagerApp (shell)
├── services.py          # Business logic, DB, SETTINGS_CATALOG, i18n, _SEED
├── modals.py            # All modals (password base class, CRUD, charts, CRM)
├── tabs.py              # All 5 tabs (Front Desk, Reservations, POS, Accounting, Settings)
├── property_manager.db  # SQLite DB (created on first run — do not commit)
├── invoices/            # Auto-created — invoice & receipt text files
└── exports/             # Auto-created — settings JSON exports, printer test
```

**Dependency graph** (no cycles):

```
sukoon.py ──→ tabs.py, modals.py, services.py
tabs.py   ──→ modals.py, services.py
modals.py ──→ services.py
services.py → (stdlib + textual only)
```

**Guiding rules:**
- Only `services.py` touches SQLite.
- Only `services.py` computes tax.
- Only `services.py` writes ledger entries.
- Tabs are `VerticalScroll` subclasses. Modals are `ModalScreen` subclasses.

---

## 3. Architecture overview

```
┌────────────────────────────────────────────────────────────────┐
│  sukoon.py  (PropertyManagerApp)                                │
│  - CSS                                                          │
│  - LoadingScreen → animated intro                               │
│  - First-run → SetSuperPasswordScreen                           │
│  - Ctrl+L → LanguageScreen                                      │
│  - refresh_all_tables() → coordinates cross-tab reload          │
└──────┬──────────────────────────────────────────────────────────┘
       │ mounts 5 tabs
   ┌───┴────┬──────────────┬──────────────┬──────────────┬────────┐
   ▼        ▼              ▼              ▼              ▼        
┌──────┐┌──────────┐┌──────────────┐┌────────────┐┌──────────┐
│Front ││Reserva-  ││POS &         ││Accounting  ││Settings  │
│Desk  ││tions     ││Inventory     ││            ││🔒        │
└──┬───┘└────┬─────┘└──────┬───────┘└──────┬─────┘└────┬─────┘
   │         │             │               │           │
   └─────────┴─────────────┴───────────────┴───────────┘
                            │
                            ▼
              ┌──────────────────────────────┐
              │ services.py                  │
              │ - SETTINGS_CATALOG (22 keys) │
              │ - All SQL, all ledger writes │
              │ - i18n (`t()`), _SEED dict   │
              └──────────────┬───────────────┘
                             ▼
              ┌──────────────────────────────┐
              │ property_manager.db          │
              └──────────────────────────────┘
```

### Why 4 files (not 1)

At ~5,000 lines, single-file editing on a phone terminal is error-prone.
The 4-file split keeps each file focused:

| File | Responsibility | Approx. lines |
|------|----------------|---------------|
| `services.py` | All business logic + persistence | ~1,400 |
| `modals.py` | All 30 modal screens | ~1,100 |
| `tabs.py` | All 5 tab layouts + handlers | ~1,300 |
| `sukoon.py` | App shell + CSS + loading | ~180 |

Cross-file communication is one-directional (`sukoon → tabs → modals → services`).
No circular imports. Any single feature can be debugged by opening **two** files.

### Cursor preservation

Every `refresh_data()` remembers `cursor_row` / `cursor_column` and restores
them via `move_cursor(row=…, animate=False)`.

---

## 4. Data model

### Schema version: **3**

Bumped from v2 when adding: `guests` table, `reservations.guest_id`,
`inventory.selling_price`, `inventory.low_stock_threshold`,
`inventory.overstock_threshold`, `inventory_logs.reference`.

### `settings` (key / value)

All 22 settings are declared in `services.SETTINGS_CATALOG`. Adding a new
setting = one dict entry. See §6.1 for the full reference.

### `rooms`

| Column | Purpose |
|--------|---------|
| `id` | PK |
| `room_number` | Display ("101") — unique |
| `room_type` | "Sea View Suite" |
| `base_price` | EGP — default nightly rate |
| `housekeeping_status` | `Clean` / `Dirty` / `Maintenance` |

### `rate_plans`

| Column | Purpose |
|--------|---------|
| `name` | "Bed & Breakfast" — unique, case-insensitive |
| `price_modifier` | EGP added to `rooms.base_price` when picked |

Rate plans are **edited from the Settings tab** (add / edit / delete). Cannot
delete a plan referenced by any reservation.

### `guests` — new in v3

| Column | Notes |
|--------|-------|
| `canonical_name` | The authoritative name — used for history and CRM |
| `phone`, `email` | Optional, normalized for matching |
| `notes` | Free text; merge operation appends `aka: Old Name` |
| `created_at`, `updated_at` | Audit columns |

**Purpose**: `reservations.guest_name` is the *as-written* name on that
booking (legal record). `reservations.guest_id` links to the canonical person.
Same guest under "Ahmed Ali" / "ahmed ali" / "A. Ali" resolves to one
`guests` row once matched.

### `reservations`

| Column | Notes |
|--------|-------|
| `guest_id` | FK to `guests` — NULL only for pre-v3 records (none, since fresh DB) |
| `guest_name` | As-written at booking |
| `source` | Free text ("Direct", "Booking.com", …) |
| `room_id`, `rate_plan_id` | FKs |
| `agreed_nightly_rate` | **Net** EGP rate (before tax) |
| `tax_amount` | **Snapshot** of tax at booking |
| `check_in_date`, `check_out_date` | `YYYY-MM-DD` |
| `status` | `Reserved` → `Checked In` → `Checked Out` (or `Cancelled`, `No-Show`) |

### `pos_tabs`

| Column | Notes |
|--------|-------|
| `reservation_id` | `NULL` = walk-in sale |
| `item_description`, `quantity`, `unit_price` | Price is **net** |
| `tax_amount` | Snapshot |

### `inventory` — extended in v3

| Column | Notes |
|--------|-------|
| `item_name`, `category`, `unit` | Free text |
| `stock_qty` | Real number (fractional kg allowed) |
| `unit_cost` | Weighted average — updated on restock |
| `selling_price` | Menu price for POS grid |
| `low_stock_threshold` | Per-item override (NULL = use global setting) |
| `overstock_threshold` | Per-item override (NULL = use global setting) |

### `inventory_logs`

| Column | Notes |
|--------|-------|
| `item_id`, `change_type`, `quantity` | `OUT` for POS sale, `SUPPLY` for restock |
| `reference` | Free text ("POS #42", "restock", …) |

**Now surfaced in UI** — the Inventory sub-tab shows the last 20 movements.

### `expense_categories` / `operational_expenses`
Standard category dictionary + individual expenses with `tax_amount` (input VAT).

### `payments`

| Column | Notes |
|--------|-------|
| `reservation_id` | `NULL` for walk-in payments |
| `amount` | **Signed** — positive = in, negative = refund |
| `payment_method` | `Cash` / `Credit Card` / `OTA Virtual Card` / etc. |
| `payment_type` | `Payment` / `Deposit` / `Refund` |

### `general_ledger` — the financial heart

Every financial event writes **exactly one row** here.

| Column | Notes |
|--------|-------|
| `entry_type` | `REVENUE` / `EXPENSE` / `PAYMENT` / `REFUND` |
| `category` | `Accommodation` / `F&B` / `COGS` / `Inventory Purchase` / `Operating` / `Other` / empty |
| `reference_id` | Reservation id, pos_tab id, payment id, expense id, or inventory id |
| `amount` | **Signed** — negative reverses positive |
| `tax_amount` | Signed tax component (snapshot) |
| `description` | Human-readable; shown in ledger view |

### `app_metadata`
Only stores super-password salt/hash and the immutable gift attribution.

---

## 5. Business logic

### 5.1 Tax formula — `services.calculate_tax(net, nights)`

```
service   = net × service_charge_rate
vat       = (net + service) × vat_rate
city      = nights × city_tax_per_night
tax_total = service + vat + city
gross     = net + tax_total
```

The **only** place tax is computed. Rates from `settings`.

**Changing a rate affects only future charges.** Existing reservations and POS
items keep their snapshotted `tax_amount`.

### 5.2 Accrual accounting

Revenue is written **when the charge is created**, not when paid.

| Action | Writes |
|--------|--------|
| Create reservation | `REVENUE` (Accommodation) for gross |
| Add POS item | `REVENUE` (F&B) for gross |
| Record payment | `PAYMENT` |
| Issue refund | `REFUND` |
| Cancel / No-show | Negative `REVENUE` reversal + auto `REFUND` for payments made |
| Void POS item | Negative `REVENUE` + optional auto `REFUND` if overpaid |
| Restock inventory (with cost) | `EXPENSE` (`Inventory Purchase`) |
| POS sale linked to inventory | `EXPENSE` (`COGS`) at `unit_cost × qty` |
| Record expense | `EXPENSE` (`Operating`) |

**Totals**:
- Billed revenue = `SUM(amount) WHERE entry_type='REVENUE'`
- Cash in = `SUM(amount) WHERE entry_type='PAYMENT'`
- Cash out = `-SUM(amount) WHERE entry_type='REFUND'`
- A/R = total charges − total payments over `Reserved` + `Checked In`

### 5.3 Walk-in POS

`add_pos_item(reservation_id=0, …)` triggers the atomic walk-in path:
`pos_tab (NULL res)` + `payment (NULL res)` + `REVENUE` + `PAYMENT`, plus
optional inventory deduction + `COGS`. Returns `{walk_in: True, gross, tax, …}`.

### 5.4 Guest CRM

**On reservation create** — `find_or_create_guest(name, phone, email)`:
1. Score all existing guests via `_name_score` (difflib on normalized,
   token-sorted names) + phone/email exact matches
2. If top score ≥ **0.95** → silently link
3. Else → insert new guest row

**In the Create form** — 300 ms debounce on `in_guest`:
- If matches found (score ≥ 0.82) → show suggestion list below the field
- Manager can ignore suggestions and just save (scores < 0.95 = new guest)

**Duplicate detection** — `get_duplicate_suggestions()` scans all guests
pairwise. Shows pairs with score ≥ 0.90 in the CRM modal.

**Merge** — `merge_guests(winner, loser, password)`:
- All reservations relink to winner
- Loser's name appended to winner's notes as `aka: Old Name`
- Loser row deleted
- **No financial data touched**

### 5.5 Inventory warnings

Computed on every Inventory sub-tab refresh:

| Flag | Condition | Color |
|------|-----------|-------|
| NEGATIVE | `stock_qty < 0` | 🔴 |
| LOW | `stock_qty < (per_item or global)` | 🔴 |
| OVER | `stock_qty > (per_item or global)` | 🟡 |
| OK | otherwise | (none) |

Panel is **always shown** — shows `✅ All Healthy` when clean.

### 5.6 Inventory & COGS

- **Restock** — weighted-average `unit_cost`, writes `EXPENSE` (`Inventory Purchase`)
  if a cost is provided, logs `SUPPLY` movement.
- **POS sale with `inventory_item_id`** — deducts stock, logs `OUT`. Refused
  if `allow_negative_stock=false` and `stock < qty`. Writes `EXPENSE` (`COGS`)
  when `unit_cost > 0`.

### 5.7 Checkout policy

Default `checkout_requires_payment = true`.

- Balance > `checkout_unpaid_threshold_egp` → **blocks checkout**
- Manager can **override** with password → logs a 0-amount `Operating` expense
  labeled `Checkout override Res#N — Balance X unpaid at checkout` for audit
- If `checkout_requires_payment = false`, unpaid checkout proceeds with warning only

### 5.8 Protected actions (require super password)

| Action | Function |
|--------|----------|
| Edit room price / type | `update_room_price` / `update_room_type` |
| Modify reservation | `modify_reservation` |
| Cancel reservation | `cancel_reservation` |
| Delete reservation | `delete_reservation` |
| Void POS item | `void_pos_item` |
| Void expense | `void_expense` |
| Refund | `record_refund` |
| Edit / delete inventory item | `update_inventory_full` / `delete_inventory_item` |
| Add rate plan / update / delete | `add_rate_plan` / `update_rate_plan` / `delete_rate_plan` |
| Merge guests | `merge_guests` |
| Edit guest | `update_guest` |
| Save settings (all changes) | `save_settings` |
| Reset to defaults | `reset_to_defaults` |
| Change super password | `change_super_password` (needs current) |
| Unlock Settings tab | `verify_super_password` |
| Checkout override | `verify_super_password` inside `ReservationsTab._checkout` |

**Every protected function** calls `verify_super_password` **inside the service** —
not only in the modal. UI cannot bypass.

### 5.9 Invoice — EGP default, USD on-demand

`generate_invoice_text(reservation_id, currency='EGP')` renders text.

- Header uses `property_name`, `property_address`, `property_phone`
- Footer uses `invoice_footer_text`
- Attribution block (`Gifted with love from Michael Helmy to Sukoon Camp`)
  **always stays hardcoded** at the bottom — it is NOT a setting
- USD view divides every monetary value by `usd_to_egp` and appends a
  "for reference only" note
- **EGP file is authoritative** — printing from USD view saves a separate file
  with `_usd` suffix, never overwrites the EGP original

### 5.10 Printers

Three backends (auto-detected or manual):

| Backend | Format | Command |
|---------|--------|---------|
| CUPS | `cups:QueueName` | `lp -d QueueName <file>` |
| Windows | `win:PrinterName` | `os.startfile(file, "printto", PrinterName)` |
| Raw socket | `raw:host:port` | TCP socket, `\n` → `\r\n` |

If `default_printer` is empty → OS default behaviour.

**On Android/Termux**: CUPS is generally unavailable. The raw socket path is
the workaround. If no printer is configured when printing an invoice, the app
opens `AndroidPrintFallback` with options: **Send to IP:PORT**, **Copy Path**,
**Cancel**.

Detect via `list_printers()`:
- Windows → `Get-Printer` via PowerShell
- Linux / macOS → `lpstat -v` + `lpstat -d`

### 5.11 Charts

Rendered with **textual-plotext** (`PlotextPlot` widget):

| Chart | Endpoint |
|-------|----------|
| Revenue vs Expense (line) | `get_daily_revenue_expense_series` |
| Daily Profit (bar) | `get_daily_profit_series` |
| Payment Methods (bar) | `get_payment_method_dict` |
| Occupancy % (line) | `get_daily_occupancy_series` |
| Occupancy Heatmap (grid) | `get_occupancy_heatmap_data` |

If `textual-plotext` is not installed, the chart modal renders an install
hint instead of a broken plot.

---

## 6. Services reference (`services.py`)

### 6.1 Settings catalog — 22 keys

Every key is declared in `services.SETTINGS_CATALOG` as
`(label, type, default, section, help, [options])`.

| Key | Type | Default | Section |
|-----|------|---------|---------|
| `property_name` | str | "Sukoon Camp" | property |
| `property_address` | text | "" | property |
| `property_phone` | str | "" | property |
| `property_email` | str | "" | property |
| `invoice_footer_text` | text | "Thank you for staying with us!" | property |
| `vat_rate` | pct | 0.14 | financial |
| `service_charge_rate` | pct | 0.10 | financial |
| `city_tax_per_night` | float | 0.0 | financial |
| `usd_to_egp` | float | 50.0 | financial |
| `default_selling_markup_pct` | pct | 0.30 | financial |
| `week_start_day` | enum | monday (monday/sunday/saturday) | reservations |
| `min_nights` | int | 1 | reservations |
| `max_nights` | int | 365 | reservations |
| `checkout_requires_payment` | bool | true | reservations |
| `checkout_unpaid_threshold_egp` | float | 0.01 | reservations |
| `allow_overbooking` | bool | false | reservations |
| `low_stock_threshold` | float | 5 | inventory |
| `overstock_threshold` | float | 100 | inventory |
| `allow_negative_stock` | bool | true | inventory |
| `language` | enum | en (en/ar) | system |
| `session_timeout_minutes` | int | 20 | system |
| `default_printer` | str | "" | system |
| `schema_version` | internal | 3 | (hidden) |

**Adding a setting** = one dict entry. The Settings tab renders it automatically.

### 6.2 Full API

**Settings**
`get_setting`, `set_setting`, `get_all_settings`, `save_settings`, `reset_to_defaults`,
`get_tax_config`, `calculate_tax`, `export_settings_to_json`, `import_settings_from_json`.

**Super password**
`has_super_password`, `set_super_password`, `verify_super_password`, `change_super_password`.

**Rooms / Rate plans**
`get_all_rooms`, `get_room`, `update_housekeeping_status`, `update_room_price` 🔒,
`update_room_type` 🔒, `get_all_rate_plans`, `add_rate_plan` 🔒,
`update_rate_plan` 🔒, `delete_rate_plan` 🔒, `count_reservations_using_plan`.

**Guests (CRM)**
`find_guest_matches`, `find_or_create_guest`, `get_all_guests`, `get_guest_by_id`,
`get_guest_history_by_id`, `update_guest` 🔒, `get_duplicate_suggestions`,
`merge_guests` 🔒.

**Reservations**
`check_room_availability`, `create_reservation`, `update_reservation_status`,
`modify_reservation` 🔒, `cancel_reservation` 🔒, `mark_no_show`,
`delete_reservation` 🔒.

**POS**
`add_pos_item` (handles walk-in), `get_pos_items`, `void_pos_item` 🔒.

**Inventory**
`get_inventory_with_prices`, `search_inventory`, `get_inventory_categories`,
`get_inventory_warnings`, `get_recent_inventory_logs`, `add_inventory_item_full` 🔒,
`update_inventory_full` 🔒, `delete_inventory_item` 🔒, `restock_inventory`.

**Payments**
`record_payment`, `record_refund` 🔒, `calculate_guest_balance`, `get_balance_breakdown`.

**Expenses**
`get_expense_categories`, `record_operational_expense`, `void_expense` 🔒,
`get_recent_expenses`.

**Ledger**
`get_recent_ledger`, `get_general_ledger_summary`, `verify_app_attribution`.

**Invoices**
`generate_invoice_text`, `save_invoice_file`.

**Printers**
`list_printers`, `print_to_printer`, `test_print`.

**Front Desk**
`get_front_desk_summary`, `get_rooms_available_between`,
`get_upcoming_reservations_for_room`, `get_occupancy_for_window`.

**Chart data**
`get_daily_revenue_expense_series`, `get_daily_profit_series`,
`get_payment_method_dict`, `get_daily_occupancy_series`, `get_occupancy_heatmap_data`.

**Reports** (return strings ready for `RichLog`)
`report_pl_statement`, `report_cash_flow`, `report_payment_methods`,
`report_vat`, `report_occupancy`, `report_daily_ops`.

---

## 7. Reports reference

All reports are in `services.py` (there is no separate `reports.py`).
All monetary values in **EGP**.

| Function | Output |
|----------|--------|
| `report_pl_statement(start, end)` | P&L — revenue by category, COGS, gross, expenses, net |
| `report_cash_flow(start, end)` | Cash in / out / net |
| `report_payment_methods(start, end)` | Per-method totals |
| `report_vat(start, end)` | Output VAT − input VAT = payable |
| `report_occupancy(start, end)` | Room-nights, %, ADR, RevPAR |
| `report_daily_ops(date)` | Arrivals / departures / in-house |

`start`, `end`, `date` are `YYYY-MM-DD` strings.

Reports are opened via **`ReportScreen`** modal from the Accounting tab.
**ChartScreen** modal renders visualizations (uses the chart-data services above).

---

## 8. UI layer

### 8.1 `sukoon.py`

```python
class LoadingScreen(ModalScreen):
    # Animated "sukoon" reveal + blink + tagline → dismisses after ~1.5 s

class PropertyManagerApp(App):
    CSS = "..."
    BINDINGS = [("ctrl+l", "language", "Language")]
    compose()  -> Header, TabbedContent(5 tabs), Footer
    on_mount() -> init_db, verify attribution, push LoadingScreen
    _after_loading() -> if no super password, push SetSuperPasswordScreen
    refresh_all_tables() -> calls .refresh_data() on all 5 tabs
```

`refresh_all_tables()` is the **only** cross-tab coordination point.

### 8.2 The 5 tabs

| Tab | Class | Parent | Content |
|-----|-------|--------|---------|
| Front Desk | `FrontDeskTab` | `VerticalScroll` | General info, 3 filter modes, occupancy window, room table, housekeeping, context panel |
| Reservations | `ReservationsTab` | `VerticalScroll` | Reservations table, action buttons, collapsible Create form, collapsible Folio |
| POS & Inventory | `PosInventoryTab` | `Vertical` | Nested `TabbedContent` → `NewOrderTab` + `InventorySubTab` |
| Accounting | `AccountingTab` | `VerticalScroll` | Expense form, ledger overview, reports row, expenses & ledger tables |
| ⚙ Settings | `SettingsTab` | `VerticalScroll` | Unlock gate → catalog-driven UI + rate plans + printers + security + maintenance |

Every tab has `compose()`, `on_mount()` (calls `refresh_data()`), `refresh_data()`, and `on_button_pressed()`.

### 8.3 Front Desk — 3 modes

| Mode | Filter | Columns shown |
|------|--------|---------------|
| **All Rooms** | none | ID, Room, Type, Base Price, Status, Next Guest, Next In |
| **Available in Period** | From / To required | ID, Room, Type, Base Price, Status |
| **Room View** | Room ID or row click | same as All + upcoming reservations panel below |

**Occupancy window** (independent RadioSet): Day / Week / Month / Custom.
Week respects `week_start_day` setting.

**General Info** panel always shows:
- Occupancy % (today) + clean/dirty/maint counts
- Arrivals / departures / in-house today
- Window occupancy + ADR + RevPAR

### 8.4 POS & Inventory — two sub-tabs

**New Order** (`NewOrderTab`):
- Target: Existing Reservation / Walk-in radio + Load by Res ID
- Guest info strip shows guest + room + current balance
- Item grid (search + category filter + `[+]` per row) → `QtyInputScreen`
- Custom item form (Description / Qty / Price)
- Cart DataTable showing current folio (or walk-in list)
- Void item from cart → `ProtectedModal` subclass
- **Walk-in** → `Pay Now & Print Receipt` → generates receipt, opens `WalkInReceiptScreen`

**Inventory** (`InventorySubTab`):
- Warning panel (NEGATIVE / LOW / OVER) always visible
- Filters: search, category, stock status (All / Low / Over / Negative)
- Inventory table with Warn column + emoji flags
- Actions: New Item / 🔒 Edit / 🔒 Delete / Restock Selected
- Restock form (qty + unit cost)
- Recent Movements table (uses `inventory_logs` — was write-only before)

### 8.5 Settings tab

**Locked by default.** Click Unlock → `UnlockSettingsModal` → password.
Once unlocked, six collapsible sections render:

1. **Property Information** — name, address, phone, email, invoice footer
2. **Financial & Tax** — VAT, service, city tax, USD rate, default markup
3. **Rate Plans** — dedicated mini-CRUD table with Add / Edit / Delete
4. **Reservations / Inventory / System** — grouped settings
5. **Printers** — detect + test + manual entry + queue list
6. **Security** — Change Password
7. **Maintenance** — DB path + size, Export / Import / Reset to Defaults

**Save flow**: collect changes → `ReviewChangesModal` (shows diffs) → password
→ `save_settings()` → notification → all tabs refresh.

**Lock Now** button → immediate re-lock.

### 8.6 Modals (all in `modals.py`)

**Base classes:**
- `ProtectedModal` — title + body + Super Password + Confirm/Cancel
- `ModalScreen` subclass for everything else

**Password-protected subclasses** (extend `ProtectedModal`):
`DeleteReservationScreen`, `ModifyReservationScreen`, `VoidItemScreen`,
`CancelConfirmScreen`, `RefundScreen`, `VoidExpenseScreen`,
`InventoryDeleteScreen`, `EditRoomFieldScreen`, `RatePlanEditScreen`,
`RatePlanDeleteScreen`, `GuestEditScreen`.

**Standalone modals:**
`SetSuperPasswordScreen`, `LanguageScreen`, `InvoiceScreen`,
`AndroidPrintFallback`, `RawPrintTargetScreen`, `ViewFolioScreen`,
`WalkInReceiptScreen`, `ReportScreen`, `InventoryEditScreen`,
`QtyInputScreen`, `ChartScreen`, `GuestCRMModal`, `GuestHistoryModal`,
`GuestMergeModal`, `UnlockSettingsModal`, `ChangePasswordModal`,
`ReviewChangesModal`, `ImportSettingsModal`, `ImportConfirmModal`.

All pushed via:

```python
self.app.push_screen(MyModal(...), callback=lambda ok: self.app.refresh_all_tables() if ok else None)
```

---

## 9. i18n

### 9.1 How it works

`services.py` defines `_AR: dict[str, str]` mapping **English key → Arabic value**.
English is the key. Missing Arabic entries fall back to the English string.
Current language stored in `settings.language` and loaded at app startup.

```python
from services import t
button = Button(t("Check-In Guest"), id="btn_checkin")
```

### 9.2 Adding a translation

Add `"English String": "الترجمة"` to `_AR` in `services.py`. Next launch
picks it up. No other change needed.

### 9.3 Adding a new translatable string

Wrap the string in `t()`, then add its Arabic entry. **Never** write a plain
English string on a widget.

### 9.4 Changing the language at runtime

`LanguageScreen` (Ctrl+L) writes `settings.language` then calls
`self.app.refresh(recompose=True)`. Whole UI re-renders.

### 9.5 RTL caveat

Arabic text displays correctly. Layout direction stays LTR — Textual does not
support RTL layout.

---

## 10. Seed data — `_SEED` in `services.py`

### 10.1 When is it read?

Only when `property_manager.db` does **not** exist.

### 10.2 To change seeded data

1. Edit the `_SEED` dict at the top of `services.py`
2. Delete `property_manager.db`
3. Launch `python sukoon.py`

### 10.3 `_SEED` structure

```python
_SEED = {
    "rooms":            [{room_number, room_type, base_price, housekeeping_status}, ...],
    "rate_plans":       [{name, price_modifier}, ...],
    "inventory":        [{item_name, category, stock_qty, unit, unit_cost, selling_price}, ...],
    "expense_categories": [category_name, ...],
}
```

Settings are seeded separately from `SETTINGS_CATALOG` (defaults come from the
catalog, not from `_SEED`).

> **The gift attribution is NOT in `_SEED`.** It is a hardcoded immutable
> constant (`GIFT_ATTRIBUTION` / `GIFT_SALT` / `GIFT_HASH`) in `services.py`.
> `verify_app_attribution()` compares **both** the stored value **and** the
> stored hash against those constants. Tamper-evident against DB edits;
> unreplaceable via re-seeding.

### 10.4 Bumping the schema version

1. Change `"schema_version"` default in `SETTINGS_CATALOG` (currently `"3"`)
2. Update the `CREATE TABLE IF NOT EXISTS` statements in `init_db()`
3. Announce that users must delete their existing DB

`init_db()` uses `INSERT OR IGNORE` for settings — adding a new key to the
catalog **automatically appears** in existing DBs with its default. This is
the "dynamic & scalable" mechanic.

---

## 11. Cookbook

### 11.1 Add a new setting

One dict entry in `SETTINGS_CATALOG`:

```python
"my_new_key": ("My New Setting", "int", "10", "reservations", "help text"),
```

Done. Appears in Settings tab automatically. `get_setting("my_new_key")`
works everywhere. No migration needed (uses `INSERT OR IGNORE`).

### 11.2 Add a new button to a tab

1. In the tab's `compose()`: `yield Button(t("My Button"), id="btn_my_action")`
2. Add `"My Button": "ترجمة"` to `_AR` in `services.py`
3. Handle in `on_button_pressed` → call service → `self.app.refresh_all_tables()`

### 11.3 Add a new service function

```python
def my_service(arg1, arg2):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO ... VALUES (?, ?)", (arg1, arg2))
        # if financial → also write a general_ledger entry here
        conn.commit()
    finally:
        conn.close()
```

**Every financial mutation writes exactly one ledger entry.**

### 11.4 Add a new ledger category

Pass a new string to the `category` field. Reports group by whatever string.
Add to `_AR` for translation.

### 11.5 Add a new modal

```python
class MyModal(ProtectedModal):     # or ModalScreen
    CONFIRM_LABEL = "Save"
    def body(self):
        yield Input(placeholder=t("Something"), id="in_x")
    def perform(self, password):
        my_service(self.query_one("#in_x", Input).value, password)
```

Push with:

```python
self.app.push_screen(MyModal(),
                     callback=lambda ok: self.app.refresh_all_tables() if ok else None)
```

Add the modal class name to the `CSS` modal-screens rule in `sukoon.py`.

### 11.6 Add a new tab

1. New class in `tabs.py` extending `VerticalScroll`
2. In `sukoon.py` → `compose()` → new `TabPane`, `yield MyTab()`
3. In `refresh_all_tables()` → `self.query_one(MyTab).refresh_data()`

### 11.7 Add a new column to a table

Schema-versioned change:

1. `CREATE TABLE IF NOT EXISTS` in `init_db()`
2. Bump `schema_version` default in `SETTINGS_CATALOG`
3. Users delete DB

### 11.8 Change the tax formula

Only edit `services.calculate_tax`. Never duplicate elsewhere.

### 11.9 Change the ledger schema

Multi-currency or double-entry requires **new schema version + new tables**.
Do not retrofit the current flat ledger.

---

## 12. Known gaps / extension points

| Area | Missing | Where to start |
|------|---------|----------------|
| Session auto-lock timer | `session_timeout_minutes` exists but no background timer | Add `set_interval(30, check)` in `SettingsTab.on_mount` |
| CSV export buttons | `services.export_*_csv` funcs removed during refactor — reports only render onscreen | Re-add to `services.py` + buttons in Accounting |
| `import_settings` full flow | `ImportSettingsModal` exists but `ImportConfirmModal` not wired end-to-end | Push `ImportConfirmModal` after successful parse |
| Language-aware tabs | Tab titles on `TabbedContent` don't refresh mid-run when language changes | `recompose=True` on language switch may not refresh `TabPane` titles — verify |
| Audit log | Not tracked — changes are reconstructable from `general_ledger` only | Add `audit_log` table if compliance needed |
| Group bookings | Deliberately omitted — manager overrides per guest | Add `group_id TEXT` to `reservations` |
| FX gains/losses | Deliberately omitted — EGP is authoritative | Not recommended for this scale |
| Rate plan unique case-insens. | Enforced in service but not via DB constraint | Add `COLLATE NOCASE` to `name UNIQUE` |
| Multi-property | Single-property only | Big refactor |

---

## 13. Key reference formulas

| Concept | Formula |
|---------|---------|
| Nights | `max(1, (check_out − check_in).days)` |
| Accommodation net | `rate × nights` |
| POS net | `Σ(quantity × unit_price)` |
| Service charge | `net × service_charge_rate` |
| VAT | `(net + service) × vat_rate` |
| City tax | `nights × city_tax_per_night` |
| Gross | `net + service + VAT + city` |
| Balance due | `(accommodation_gross + pos_gross) − total_paid` |
| AR outstanding | `Σ balance_due` over `Reserved` + `Checked In` |
| Occupancy % | `occupied_room_nights / total_room_nights × 100` |
| ADR | `accommodation_revenue / occupied_room_nights` |
| RevPAR | `accommodation_revenue / total_room_nights` |
| Guest match score | `max(difflib_ratio(direct), difflib_ratio(token_sorted))` |

---

## 14. Rules to keep the app healthy

1. **All SQL lives in `services.py`.**
2. **All financial mutations write exactly one `general_ledger` row.**
3. **All tax comes from `calculate_tax`.** Never inline `0.14` in new code.
4. **All protected actions call `verify_super_password` inside the service**,
   not only in the modal.
5. **All UI strings go through `t()`.**
6. **Every mutation that touches more than one tab calls
   `self.app.refresh_all_tables()`** after success.
7. **Reads use `services.fetch_all` / `fetch_one`** — never open a connection
   in a tab.
8. **Modals dismiss with `True` (success) or `False` (cancel)** — never `None`.
9. **Never store secrets or identity markers in `_SEED` or `SETTINGS_CATALOG`.**
   Super password lives in `app_metadata` (salted hash). Gift attribution is
   hardcoded constants in `services.py`.
10. **Never delete financial rows silently.** Always write reversal entries —
    except `delete_reservation`, which is explicitly a full wipe behind the
    password.
11. **A guest is never deleted individually — only merged.** Prevents orphaned
    reservations.
12. **`guests.canonical_name` is the truth for history aggregation.**
    `reservations.guest_name` is the historical as-written record.

---

## 15. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| "Schema mismatch" on launch | DB was created by a different schema version | Delete `property_manager.db` (schema is now **3**) |
| "Old database detected" on launch | Same | Same |
| App exits right after launch | Clicked **Quit** on super-password modal | Relaunch and set a password |
| "No such table" on first tab load | `init_db()` not called before `run()` | Check `if __name__ == "__main__": services.init_db(); PropertyManagerApp().run()` |
| Charts show "Install textual-plotext" | Package missing | `pip install textual-plotext` |
| Printer detect returns empty on Android | CUPS unavailable on Termux | Use `raw:host:port` manual entry |
| Buttons show English in Arabic mode | String wasn't wrapped in `t()` | Wrap it |
| Checkout blocked on unpaid guest | `checkout_requires_payment = true` (default) | Record payment, or use **Override & Checkout** with password |
| Invoice shows wrong USD conversion | `usd_to_egp` changed after issuing | USD is display-only; EGP file is authoritative |
| Ledger totals seem off | A mutation wrote two `REVENUE` entries for one charge | Verify §5.2 |
| Balance is negative after POS sale | Guest overpaid | `REFUND` entry may be auto-created on void |
| Guest suggestion not appearing | Name typed < 3 chars, or debounce not elapsed | Wait 300 ms; type ≥ 3 chars |
| Duplicate suggestions cluttered | Many similar guests | Merge them; the list caps at 20 pairs |
| `invoice_Res*.txt` files piling up | Expected — no rotation | Delete old files from `./invoices/` |
| Attribution warning appears | `app_metadata.gift_attribution` was edited | Out of scope — constants in `services.py` no longer match DB |
| `import_settings` fails silently | Parse error in JSON | Check error notification; all-or-nothing apply |

---

## 16. Versioning

`SETTINGS_CATALOG["schema_version"][2]` (currently `"3"`) is the single source
of truth. `init_db()` refuses to touch any DB whose stored `schema_version`
differs.

**Breaking change workflow:**

1. Update `CREATE TABLE IF NOT EXISTS` statements in `init_db()`
2. Change the `schema_version` default in `SETTINGS_CATALOG`
3. Commit
4. Users delete `property_manager.db` and relaunch

**Non-breaking (settings only):** add a new key to `SETTINGS_CATALOG` —
existing DBs pick up its default automatically via `INSERT OR IGNORE`. No
schema bump, no user action needed.

No in-place migration path for structural changes — single-manager app,
fresh-start cost is lower than migration-code cost.
```

---

## Summary of updates

| Section | Change |
|---------|--------|
| §1 Quick start | `pip install textual textual-plotext`; entry file `sukoon.py`; loading screen; schema 3 |
| §2 Structure | 4 files (`sukoon.py`, `services.py`, `modals.py`, `tabs.py`); dependency graph |
| §3 Architecture | New diagram with 5 tabs; file responsibility table |
| §4 Data model | Added `guests` table; extended `inventory`; `reservations.guest_id`; `inventory_logs.reference` |
| §5 Business logic | Added §5.4 Guest CRM, §5.5 Inventory warnings, §5.7 Checkout policy, §5.10 Printers, §5.11 Charts; updated protected actions |
| §6 Services reference | Added §6.1 Settings catalog (22 keys); complete API listing |
| §7 Reports | All in `services.py` with `report_` prefix; added chart-data endpoints |
| §8 UI layer | 5 tabs documented including Settings tab and 2 POS sub-tabs; modal classes listed by base/standalone |
| §9 i18n | Unchanged |
| §10 Seed data | Now inline `_SEED` in `services.py`, not JSON file |
| §11 Cookbook | Updated for new architecture (add setting = 1 dict entry; protected-modal pattern) |
| §12 Known gaps | Reflects what's done vs pending (auto-lock timer, CSV buttons) |
| §13 Formulas | Added guest match score |
| §14 Rules | Added rule 11 (guests merge only) and 12 (canonical_name truth) |
| §15 Troubleshooting | Added printer, checkout, guest, chart, schema-v3 entries |
| §16 Versioning | Clarified non-breaking settings additions vs breaking schema changes |