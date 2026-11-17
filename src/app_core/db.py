"""Property manager — database layer. Single persistent connection with WAL.

Safety notes:
- Single shared connection with RLock serialization; all writes should go through
  transaction()/execute helpers to preserve --workers 1 / single-process correctness.
- WAL + foreign_keys + busy_timeout are enforced on every (re)connect.
- Migrations are idempotent and guarded by PRAGMA user_version and provenance markers.
- Runtime data (DB/exports/invoices under paths.DATA_DIR) is never deleted by this module.
"""
import sqlite3
import threading
from contextlib import contextmanager

from . import paths

DATA_DIR = paths.DATA_DIR
BASE_DIR = paths.BASE_DIR
DB_PATH = paths.DB_PATH
SCHEMA_VERSION = 20

_conn = None
_lock = threading.RLock()


def _apply_pragmas(conn):
    # Safety/performance defaults for single-process app usage.
    for sql in (
        "PRAGMA foreign_keys = ON",
        "PRAGMA journal_mode = WAL",
        "PRAGMA synchronous = NORMAL",
        "PRAGMA busy_timeout = 30000",
    ):
        conn.execute(sql)
    try:
        conn.execute("PRAGMA trusted_schema = OFF")
    except Exception:
        # Older SQLite builds may not support trusted_schema; ignore safely.
        pass


def connect():
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        paths.ensure_data_dirs()
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
        _conn.row_factory = sqlite3.Row
        # Keep default autocommit mode for explicit transaction control.
        try:
            _conn.isolation_level = None
        except Exception:
            pass
        _apply_pragmas(_conn)
        return _conn


def close():
    global _conn
    with _lock:
        if _conn is not None:
            try:
                # Best-effort: do not mask app errors on shutdown, but avoid leaving txn open.
                if _conn.in_transaction:
                    _conn.execute("ROLLBACK")
            except Exception:
                pass
            try:
                _conn.close()
            finally:
                _conn = None


def is_initialized() -> bool:
    """Public readiness helper: whether a shared DB connection is currently open."""
    return _conn is not None


@contextmanager
def transaction():
    """Serialized write transaction (BEGIN IMMEDIATE) on the shared connection.

    Uses explicit BEGIN/COMMIT/ROLLBACK to avoid implicit-transaction edge cases.
    """
    conn = connect()
    with _lock:
        if getattr(conn, "in_transaction", False):
            raise RuntimeError("Nested/active transaction detected on shared DB connection")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise


def execute(sql, params=()):
    conn = connect()
    with _lock:
        return conn.execute(sql, params)


def executemany(sql, seq_of_params):
    conn = connect()
    with _lock:
        return conn.executemany(sql, seq_of_params)


def query(sql, params=()):
    """Read helper (returns cursor rows)."""
    conn = connect()
    with _lock:
        return conn.execute(sql, params)


def _fetch_cursor(cur, one: bool):
    try:
        return cur.fetchone() if one else cur.fetchall()
    finally:
        try:
            cur.close()
        except Exception:
            pass


def query_all(sql, params=()):
    return _fetch_cursor(query(sql, params), one=False)


def query_one(sql, params=()):
    return _fetch_cursor(query(sql, params), one=True)


def user_version():
    conn = connect()
    with _lock:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def is_fresh():
    return user_version() == 0


def integrity_check(max_errors=20):
    """Return (ok, details) from PRAGMA integrity_check/foreign_key_check."""
    try:
        max_errors = max(1, min(int(max_errors or 20), 200))
    except Exception:
        max_errors = 20
    conn = connect()
    with _lock:
        try:
            ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        except Exception as e:
            return False, [f"integrity_check error: {e}"]
        try:
            fk = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
        except Exception as e:
            return False, ic[:max_errors] + [f"foreign_key_check error: {e}"]
    ok = (len(ic) == 1 and str(ic[0]).lower() == "ok" and len(fk) == 0)
    details = list(ic[:max_errors])
    if fk:
        details.append(f"foreign_key_violations={len(fk)}")
        details.extend([str(x) for x in fk[:max_errors]])
    return ok, details


def _table_exists(conn, name: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _table_columns(conn, name: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()]


def _ensure_column(conn, table: str, col: str, decl: str, cols: list[str] | None = None):
    if cols is None:
        cols = _table_columns(conn, table)
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        cols.append(col)
    return cols


def _record_migration_meta(conn, status: str, from_version: int | None = None, to_version: int | None = None):
    """Best-effort provenance marker in app_metadata (idempotent/safe if table absent)."""
    try:
        if not _table_exists(conn, "app_metadata"):
            return
        now = conn.execute("SELECT datetime('now','localtime')").fetchone()[0]
        current_ver = conn.execute('PRAGMA user_version').fetchone()[0]
        pairs = [
            ("schema_version", str(to_version if to_version is not None else current_ver)),
            ("schema_migration_status", str(status)),
            ("schema_migration_at", str(now)),
        ]
        if from_version is not None:
            pairs.append(("schema_migration_from", str(from_version)))
        for k, v in pairs:
            conn.execute(
                "INSERT OR REPLACE INTO app_metadata(key,value,hash_signature) VALUES(?,?,COALESCE((SELECT hash_signature FROM app_metadata WHERE key=?), ''))",
                (k, v, k),
            )
    except Exception:
        # never block migrations for provenance bookkeeping
        pass


def ensure_runtime_indexes(conn=None):
    """Create/ensure safe performance indexes for batch/traceability/reporting queries (idempotent)."""
    own = conn is None
    if own:
        conn = connect()
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_batches_item_status ON inventory_batches(item_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_batches_item_expiry ON inventory_batches(item_id, expiry_date)",
        "CREATE INDEX IF NOT EXISTS idx_batches_barcode_item ON inventory_batches(barcode_value, item_id)",
        "CREATE INDEX IF NOT EXISTS idx_invlogs_item_at ON inventory_logs(item_id, at)",
        "CREATE INDEX IF NOT EXISTS idx_invlogs_batch_at ON inventory_logs(batch_id, at)",
        "CREATE INDEX IF NOT EXISTS idx_pos_item_created ON pos_tabs(inventory_item_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_pos_batch_created ON pos_tabs(batch_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_res_guest_status ON reservations(guest_id, status)",
    ]
    with _lock:
        for sql in stmts:
            try:
                conn.execute(sql)
            except Exception:
                # keep safe/idempotent: ignore index creation errors on partial/legacy states
                pass


_SCHEMA = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_number TEXT NOT NULL UNIQUE,
    room_type TEXT NOT NULL,
    base_price REAL NOT NULL,
    housekeeping_status TEXT NOT NULL DEFAULT 'Clean'
);

CREATE TABLE rate_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    price_modifier REAL NOT NULL DEFAULT 0
);

CREATE TABLE rate_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    rate_plan_id INTEGER,
    room_type TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    market_segment TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    min_nights INTEGER,
    max_nights INTEGER,
    adjustment_type TEXT NOT NULL DEFAULT 'amount',
    adjustment_value REAL NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (rate_plan_id) REFERENCES rate_plans(id)
);
CREATE INDEX idx_rate_rules_active ON rate_rules(active, priority);

CREATE TABLE guests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_guests_name ON guests(canonical_name);
CREATE INDEX idx_guests_phone ON guests(phone);

CREATE TABLE reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guest_id INTEGER,
    guest_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'Direct',
    source_detail TEXT NOT NULL DEFAULT '',
    market_segment TEXT NOT NULL DEFAULT '',
    room_id INTEGER NOT NULL,
    rate_plan_id INTEGER,
    agreed_nightly_rate REAL NOT NULL,
    tax_amount REAL NOT NULL DEFAULT 0,
    check_in_date TEXT NOT NULL,
    check_out_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Reserved',
    notes TEXT NOT NULL DEFAULT '',
    booking_notes TEXT NOT NULL DEFAULT '',
    pricing_snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (guest_id) REFERENCES guests(id),
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (rate_plan_id) REFERENCES rate_plans(id)
);
CREATE INDEX idx_res_guest ON reservations(guest_id);
CREATE INDEX idx_res_status ON reservations(status);
CREATE INDEX idx_res_dates ON reservations(check_in_date, check_out_date);
CREATE INDEX idx_res_room ON reservations(room_id);
CREATE INDEX idx_res_source_segment ON reservations(source, market_segment);

CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    stock_qty REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL,
    unit_cost REAL NOT NULL DEFAULT 0,
    selling_price REAL NOT NULL DEFAULT 0,
    low_stock_threshold REAL,
    overstock_threshold REAL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    sku TEXT,
    barcode TEXT,
    barcode_type TEXT NOT NULL DEFAULT 'code128',
    track_batches INTEGER NOT NULL DEFAULT 0,
    shelf_life_days INTEGER,
    default_supplier TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX idx_inventory_sku ON inventory(sku) WHERE sku IS NOT NULL AND sku <> '';
CREATE UNIQUE INDEX idx_inventory_barcode ON inventory(barcode) WHERE barcode IS NOT NULL AND barcode <> '';

CREATE TABLE inventory_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    lot_code TEXT NOT NULL UNIQUE,
    barcode_value TEXT,
    barcode_type TEXT NOT NULL DEFAULT 'code128',
    quantity_received REAL NOT NULL,
    quantity_remaining REAL NOT NULL,
    unit_cost REAL NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    expiry_date TEXT,
    supplier TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (item_id) REFERENCES inventory(id)
);
CREATE INDEX idx_batches_item ON inventory_batches(item_id);
CREATE INDEX idx_batches_expiry ON inventory_batches(expiry_date);
CREATE INDEX idx_batches_status ON inventory_batches(status);
CREATE INDEX idx_batches_barcode ON inventory_batches(barcode_value);

CREATE TABLE inventory_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    batch_id INTEGER,
    unit_cost REAL,
    balance_after REAL,
    FOREIGN KEY (item_id) REFERENCES inventory(id),
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(id)
);
CREATE INDEX idx_invlogs_item ON inventory_logs(item_id);
CREATE INDEX idx_invlogs_batch ON inventory_logs(batch_id);

CREATE TABLE recipe_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    menu_item_id INTEGER NOT NULL,
    ingredient_item_id INTEGER NOT NULL,
    qty_per_sale REAL NOT NULL,
    FOREIGN KEY (menu_item_id) REFERENCES inventory(id) ON DELETE CASCADE,
    FOREIGN KEY (ingredient_item_id) REFERENCES inventory(id),
    UNIQUE(menu_item_id, ingredient_item_id)
);
CREATE INDEX idx_recipe_menu ON recipe_components(menu_item_id);
CREATE INDEX idx_recipe_ing ON recipe_components(ingredient_item_id);

CREATE TABLE meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    preparation TEXT NOT NULL DEFAULT '',
    selling_price REAL NOT NULL DEFAULT 0,
    category TEXT NOT NULL DEFAULT 'Meals',
    active INTEGER NOT NULL DEFAULT 1,
    linked_inventory_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (linked_inventory_id) REFERENCES inventory(id)
);
CREATE INDEX idx_meals_active ON meals(active);

CREATE TABLE meal_ingredients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meal_id INTEGER NOT NULL,
    inventory_item_id INTEGER NOT NULL,
    qty_per_sale REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (meal_id) REFERENCES meals(id) ON DELETE CASCADE,
    FOREIGN KEY (inventory_item_id) REFERENCES inventory(id),
    UNIQUE(meal_id, inventory_item_id)
);
CREATE INDEX idx_meal_ing_meal ON meal_ingredients(meal_id);

CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Trip',
    unit_price REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_activities_active ON activities(active);

CREATE TABLE pos_tabs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER,
    inventory_item_id INTEGER,
    item_description TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    unit_price REAL NOT NULL,
    tax_amount REAL NOT NULL DEFAULT 0,
    cost_at_sale REAL NOT NULL DEFAULT 0,
    voided_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    activity_id INTEGER,
    batch_id INTEGER,
    lot_code TEXT,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id),
    FOREIGN KEY (inventory_item_id) REFERENCES inventory(id),
    FOREIGN KEY (activity_id) REFERENCES activities(id),
    FOREIGN KEY (batch_id) REFERENCES inventory_batches(id)
);
CREATE INDEX idx_pos_res ON pos_tabs(reservation_id);
CREATE INDEX idx_pos_activity ON pos_tabs(activity_id);
CREATE INDEX idx_pos_batch ON pos_tabs(batch_id);


CREATE TABLE service_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Service',
    unit_price REAL NOT NULL DEFAULT 0,
    taxable INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_service_catalog_active ON service_catalog(active);

CREATE TABLE reservation_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL,
    service_id INTEGER,
    description TEXT NOT NULL,
    quantity REAL NOT NULL DEFAULT 1,
    unit_price REAL NOT NULL DEFAULT 0,
    tax_amount REAL NOT NULL DEFAULT 0,
    gross_amount REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'posted',
    voided_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    posted_at TEXT,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id),
    FOREIGN KEY (service_id) REFERENCES service_catalog(id)
);
CREATE INDEX idx_res_services_res ON reservation_services(reservation_id);
CREATE INDEX idx_res_services_status ON reservation_services(status);

CREATE TABLE expense_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name TEXT NOT NULL UNIQUE
);


CREATE TABLE discount_vouchers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    discount_type TEXT NOT NULL DEFAULT 'amount',
    value REAL NOT NULL DEFAULT 0,
    max_uses INTEGER,
    used_count INTEGER NOT NULL DEFAULT 0,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_discount_vouchers_active ON discount_vouchers(active);

CREATE TABLE reservation_discounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL,
    voucher_id INTEGER,
    description TEXT NOT NULL,
    discount_type TEXT NOT NULL DEFAULT 'amount',
    value REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'applied',
    voided_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    applied_at TEXT,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id),
    FOREIGN KEY (voucher_id) REFERENCES discount_vouchers(id)
);
CREATE INDEX idx_res_discounts_res ON reservation_discounts(reservation_id);


CREATE TABLE room_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    block_type TEXT NOT NULL DEFAULT 'OOO',
    reason TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    resolved_at TEXT,
    FOREIGN KEY (room_id) REFERENCES rooms(id)
);
CREATE INDEX idx_room_blocks_room_dates ON room_blocks(room_id, start_date, end_date, status);


CREATE TABLE housekeeping_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id INTEGER NOT NULL,
    reservation_id INTEGER,
    task_type TEXT NOT NULL DEFAULT 'cleaning',
    priority TEXT NOT NULL DEFAULT 'normal',
    status TEXT NOT NULL DEFAULT 'pending',
    assignee TEXT NOT NULL DEFAULT '',
    due_at TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    started_at TEXT,
    completed_at TEXT,
    inspected_at TEXT,
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);
CREATE INDEX idx_housekeeping_tasks_room_status ON housekeeping_tasks(room_id, status);
CREATE INDEX idx_housekeeping_tasks_due ON housekeeping_tasks(status, due_at);


CREATE TABLE cash_shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    opened_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    closed_at TEXT,
    opening_float REAL NOT NULL DEFAULT 0,
    expected_cash REAL NOT NULL DEFAULT 0,
    counted_cash REAL,
    variance REAL,
    notes TEXT NOT NULL DEFAULT '',
    opened_by TEXT NOT NULL DEFAULT '',
    closed_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_cash_shifts_status ON cash_shifts(status, opened_at);

CREATE TABLE cash_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shift_id INTEGER NOT NULL,
    movement_type TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'Cash',
    reference TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (shift_id) REFERENCES cash_shifts(id)
);
CREATE INDEX idx_cash_movements_shift ON cash_movements(shift_id, at);

CREATE TABLE night_audit_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    completed_at TEXT,
    summary_json TEXT NOT NULL DEFAULT '{}',
    exceptions_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    run_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_night_audit_date ON night_audit_runs(business_date, status);


CREATE TABLE reservation_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    group_type TEXT NOT NULL DEFAULT 'group',
    contact_name TEXT NOT NULL DEFAULT '',
    contact_phone TEXT NOT NULL DEFAULT '',
    contact_email TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_reservation_groups_status ON reservation_groups(status);

CREATE TABLE reservation_group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    reservation_id INTEGER NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'member',
    notes TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (group_id) REFERENCES reservation_groups(id),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);
CREATE INDEX idx_res_group_members_group ON reservation_group_members(group_id);


CREATE TABLE packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_packages_active ON packages(active);

CREATE TABLE package_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id INTEGER NOT NULL,
    service_id INTEGER,
    description TEXT NOT NULL DEFAULT '',
    quantity REAL NOT NULL DEFAULT 1,
    unit_price REAL,
    taxable INTEGER,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (package_id) REFERENCES packages(id),
    FOREIGN KEY (service_id) REFERENCES service_catalog(id)
);
CREATE INDEX idx_package_items_pkg ON package_items(package_id);

CREATE TABLE reservation_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER NOT NULL,
    package_id INTEGER,
    package_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'applied',
    total_net REAL NOT NULL DEFAULT 0,
    total_tax REAL NOT NULL DEFAULT 0,
    total_gross REAL NOT NULL DEFAULT 0,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    voided_at TEXT,
    FOREIGN KEY (reservation_id) REFERENCES reservations(id),
    FOREIGN KEY (package_id) REFERENCES packages(id)
);
CREATE INDEX idx_res_packages_res ON reservation_packages(reservation_id);

CREATE TABLE reservation_package_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_package_id INTEGER NOT NULL,
    reservation_service_id INTEGER NOT NULL,
    item_snapshot_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (reservation_package_id) REFERENCES reservation_packages(id),
    FOREIGN KEY (reservation_service_id) REFERENCES reservation_services(id)
);
CREATE INDEX idx_res_pkg_lines_pkg ON reservation_package_lines(reservation_package_id);

CREATE TABLE operational_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    parameter_name TEXT NOT NULL,
    net_amount REAL NOT NULL,
    vat_amount REAL NOT NULL DEFAULT 0,
    method TEXT NOT NULL DEFAULT 'Cash',
    notes TEXT NOT NULL DEFAULT '',
    voided_at TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (category_id) REFERENCES expense_categories(id)
);

CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id INTEGER,
    amount REAL NOT NULL,
    method TEXT NOT NULL,
    payment_type TEXT NOT NULL DEFAULT 'Payment',
    notes TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    allocation_json TEXT NOT NULL DEFAULT '{}',
    reference TEXT NOT NULL DEFAULT '',
    shift_id INTEGER,
    voided_at TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id),
    FOREIGN KEY (shift_id) REFERENCES cash_shifts(id)
);
CREATE INDEX IF NOT EXISTS idx_payments_idempotency ON payments(idempotency_key);
CREATE INDEX idx_payments_res ON payments(reservation_id);
CREATE INDEX idx_payments_shift ON payments(shift_id);

CREATE TABLE general_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    ref_type TEXT NOT NULL,
    ref_id INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    net REAL NOT NULL DEFAULT 0,
    service REAL NOT NULL DEFAULT 0,
    vat REAL NOT NULL DEFAULT 0,
    city_tax REAL NOT NULL DEFAULT 0,
    gross REAL NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    reverses_id INTEGER,
    actor TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (reverses_id) REFERENCES general_ledger(id)
);
CREATE INDEX idx_ledger_at ON general_ledger(at);
CREATE INDEX idx_ledger_ref ON general_ledger(ref_type, ref_id);
CREATE INDEX idx_ledger_type ON general_ledger(entry_type);
CREATE INDEX idx_ledger_cat ON general_ledger(entry_type, category);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    actor TEXT NOT NULL DEFAULT 'manager',
    actor_user_id INTEGER,
    actor_role TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT ''
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'staff',
    active INTEGER NOT NULL DEFAULT 1,
    password_salt TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_users_active ON users(active, role);

CREATE TABLE roles (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    permissions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    hash_signature TEXT NOT NULL DEFAULT ''
);

CREATE TABLE assistant_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    message TEXT NOT NULL,
    predicted_intent TEXT NOT NULL DEFAULT '',
    corrected_intent TEXT NOT NULL DEFAULT '',
    confidence REAL,
    slots_json TEXT NOT NULL DEFAULT '{}',
    corrected_slots_json TEXT NOT NULL DEFAULT '{}',
    tools_used_json TEXT NOT NULL DEFAULT '[]',
    answer_preview TEXT NOT NULL DEFAULT '',
    rating INTEGER NOT NULL DEFAULT 0,
    user_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    source TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_assist_fb_created ON assistant_feedback(created_at);
CREATE INDEX idx_assist_fb_intent ON assistant_feedback(corrected_intent);
"""


def _migrate_to_5(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS recipe_components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        menu_item_id INTEGER NOT NULL,
        ingredient_item_id INTEGER NOT NULL,
        qty_per_sale REAL NOT NULL,
        FOREIGN KEY (menu_item_id) REFERENCES inventory(id) ON DELETE CASCADE,
        FOREIGN KEY (ingredient_item_id) REFERENCES inventory(id),
        UNIQUE(menu_item_id, ingredient_item_id)
    );
    CREATE INDEX IF NOT EXISTS idx_recipe_menu ON recipe_components(menu_item_id);
    CREATE INDEX IF NOT EXISTS idx_recipe_ing ON recipe_components(ingredient_item_id);
    """)


def _migrate_to_6(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS meals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        preparation TEXT NOT NULL DEFAULT '',
        selling_price REAL NOT NULL DEFAULT 0,
        category TEXT NOT NULL DEFAULT 'Meals',
        active INTEGER NOT NULL DEFAULT 1,
        linked_inventory_id INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (linked_inventory_id) REFERENCES inventory(id)
    );
    CREATE INDEX IF NOT EXISTS idx_meals_active ON meals(active);
    CREATE TABLE IF NOT EXISTS meal_ingredients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        meal_id INTEGER NOT NULL,
        inventory_item_id INTEGER NOT NULL,
        qty_per_sale REAL NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (meal_id) REFERENCES meals(id) ON DELETE CASCADE,
        FOREIGN KEY (inventory_item_id) REFERENCES inventory(id),
        UNIQUE(meal_id, inventory_item_id)
    );
    CREATE INDEX IF NOT EXISTS idx_meal_ing_meal ON meal_ingredients(meal_id);
    """)


def _migrate_to_7(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS assistant_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        message TEXT NOT NULL,
        predicted_intent TEXT NOT NULL DEFAULT '',
        corrected_intent TEXT NOT NULL DEFAULT '',
        confidence REAL,
        slots_json TEXT NOT NULL DEFAULT '{}',
        corrected_slots_json TEXT NOT NULL DEFAULT '{}',
        tools_used_json TEXT NOT NULL DEFAULT '[]',
        answer_preview TEXT NOT NULL DEFAULT '',
        rating INTEGER NOT NULL DEFAULT 0,
        user_note TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new',
        source TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_assist_fb_created ON assistant_feedback(created_at);
    CREATE INDEX IF NOT EXISTS idx_assist_fb_intent ON assistant_feedback(corrected_intent);
    """)


def _migrate_to_8(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'Trip',
        unit_price REAL NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_activities_active ON activities(active);
    """)
    if _table_exists(conn, "pos_tabs"):
        _ensure_column(conn, "pos_tabs", "activity_id", "INTEGER REFERENCES activities(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_activity ON pos_tabs(activity_id)")


def _migrate_to_9(conn):
    # inventory identity/barcode/batch tracking columns
    if _table_exists(conn, "inventory"):
        inv_cols = _table_columns(conn, "inventory")
        for col, decl in (
            ("sku", "TEXT"),
            ("barcode", "TEXT"),
            ("barcode_type", "TEXT NOT NULL DEFAULT 'code128'"),
            ("track_batches", "INTEGER NOT NULL DEFAULT 0"),
            ("shelf_life_days", "INTEGER"),
            ("default_supplier", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(conn, "inventory", col, decl, inv_cols)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_sku ON inventory(sku) WHERE sku IS NOT NULL AND sku <> ''")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_barcode ON inventory(barcode) WHERE barcode IS NOT NULL AND barcode <> ''")


def _migrate_to_10(conn):
    # Phase 1 additive fields for reservation source/segment/notes (front-desk & reporting)
    if _table_exists(conn, "reservations"):
        cols = _table_columns(conn, "reservations")
        for col, decl in (
            ("source_detail", "TEXT NOT NULL DEFAULT ''"),
            ("market_segment", "TEXT NOT NULL DEFAULT ''"),
            ("booking_notes", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(conn, "reservations", col, decl, cols)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_res_source_segment ON reservations(source, market_segment)")
        except Exception:
            pass
        # Backfill booking_notes from legacy notes where empty (idempotent, additive)
        try:
            conn.execute("UPDATE reservations SET booking_notes = COALESCE(notes,'') WHERE (booking_notes IS NULL OR booking_notes = '') AND COALESCE(notes,'') <> ''")
        except Exception:
            pass

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS inventory_batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        lot_code TEXT NOT NULL UNIQUE,
        barcode_value TEXT,
        barcode_type TEXT NOT NULL DEFAULT 'code128',
        quantity_received REAL NOT NULL,
        quantity_remaining REAL NOT NULL,
        unit_cost REAL NOT NULL DEFAULT 0,
        received_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        expiry_date TEXT,
        supplier TEXT NOT NULL DEFAULT '',
        reference TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (item_id) REFERENCES inventory(id)
    );
    CREATE INDEX IF NOT EXISTS idx_batches_item ON inventory_batches(item_id);
    CREATE INDEX IF NOT EXISTS idx_batches_expiry ON inventory_batches(expiry_date);
    CREATE INDEX IF NOT EXISTS idx_batches_status ON inventory_batches(status);
    CREATE INDEX IF NOT EXISTS idx_batches_barcode ON inventory_batches(barcode_value);
    """)

    if _table_exists(conn, "inventory_logs"):
        log_cols = _table_columns(conn, "inventory_logs")
        _ensure_column(conn, "inventory_logs", "batch_id", "INTEGER REFERENCES inventory_batches(id)", log_cols)
        _ensure_column(conn, "inventory_logs", "unit_cost", "REAL", log_cols)
        _ensure_column(conn, "inventory_logs", "balance_after", "REAL", log_cols)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invlogs_batch ON inventory_logs(batch_id)")

    if _table_exists(conn, "pos_tabs"):
        pos_cols = _table_columns(conn, "pos_tabs")
        _ensure_column(conn, "pos_tabs", "batch_id", "INTEGER REFERENCES inventory_batches(id)", pos_cols)
        _ensure_column(conn, "pos_tabs", "lot_code", "TEXT", pos_cols)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_batch ON pos_tabs(batch_id)")

    # Backfill legacy/opening batches for existing stock so traceability is consistent.
    # Provenance guard/marker: only create when no batch exists for the item AND no prior
    # v9 legacy backfill opening log exists (idempotent on re-run/partial states).
    if _table_exists(conn, "inventory"):
        items = conn.execute("SELECT id, stock_qty, unit_cost, created_at FROM inventory").fetchall()
        has_logs_table = _table_exists(conn, "inventory_logs")
        backfill_marker = "v9_legacy_backfill"
        legacy_ref = f"legacy opening batch [{backfill_marker}]"
        for it in items:
            has_batch = conn.execute("SELECT 1 FROM inventory_batches WHERE item_id=? LIMIT 1", (it["id"],)).fetchone()
            has_backfill_log = False
            if has_logs_table:
                has_backfill_log = conn.execute(
                    "SELECT 1 FROM inventory_logs WHERE item_id=? AND change_type='OPENING' AND reference=? LIMIT 1",
                    (it["id"], legacy_ref),
                ).fetchone()
            if has_batch or has_backfill_log:
                continue
            qty = float(it["stock_qty"] or 0)
            if abs(qty) < 1e-9:
                continue
            received_at = ""
            if has_logs_table:
                received_at = conn.execute(
                    "SELECT MIN(at) AS at FROM inventory_logs WHERE item_id=?", (it["id"],)
                ).fetchone()["at"] or ""
            received_at = received_at or it["created_at"] or ""
            lot_code = f"LEGACY-{it['id']}"
            # ensure uniqueness
            if conn.execute("SELECT 1 FROM inventory_batches WHERE lot_code=?", (lot_code,)).fetchone():
                lot_code = f"LEGACY-{it['id']}-{abs(hash((it['id'], received_at))) % 100000}"
            conn.execute(
                """INSERT INTO inventory_batches
                   (item_id, lot_code, barcode_value, barcode_type, quantity_received, quantity_remaining,
                    unit_cost, received_at, expiry_date, supplier, reference, notes, status, payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (it["id"], lot_code, lot_code, "code128", qty, qty, float(it["unit_cost"] or 0),
                 received_at or "1970-01-01 00:00:00", None, "", "legacy",
                 f"Opening/legacy stock backfill [{backfill_marker}]", "active", "{}")
            )
            bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            if has_logs_table:
                conn.execute(
                    """INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost, balance_after)
                       VALUES (?, 'OPENING', ?, ?, ?, ?, ?)""",
                    (it["id"], qty, legacy_ref, bid, float(it["unit_cost"] or 0), qty)
                )



def _migrate_to_11(conn):
    # P2: extra services/add-ons catalog and reservation service lines (additive)
    if not _table_exists(conn, "service_catalog"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS service_catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT 'Service',
            unit_price REAL NOT NULL DEFAULT 0,
            taxable INTEGER NOT NULL DEFAULT 1,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_service_catalog_active ON service_catalog(active);
        """)
    if not _table_exists(conn, "reservation_services"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservation_services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL,
            service_id INTEGER,
            description TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 1,
            unit_price REAL NOT NULL DEFAULT 0,
            tax_amount REAL NOT NULL DEFAULT 0,
            gross_amount REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'posted',
            voided_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            posted_at TEXT,
            FOREIGN KEY (reservation_id) REFERENCES reservations(id),
            FOREIGN KEY (service_id) REFERENCES service_catalog(id)
        );
        CREATE INDEX IF NOT EXISTS idx_res_services_res ON reservation_services(reservation_id);
        CREATE INDEX IF NOT EXISTS idx_res_services_status ON reservation_services(status);
        """)

def _migrate_to_12(conn):
    # P2: pricing snapshots and rate rules (additive)
    if _table_exists(conn, "reservations"):
        cols = _table_columns(conn, "reservations")
        _ensure_column(conn, "reservations", "pricing_snapshot_json", "TEXT NOT NULL DEFAULT '{}'", cols)
    if not _table_exists(conn, "rate_rules"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS rate_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rate_plan_id INTEGER,
            room_type TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            market_segment TEXT NOT NULL DEFAULT '',
            start_date TEXT NOT NULL DEFAULT '',
            end_date TEXT NOT NULL DEFAULT '',
            min_nights INTEGER,
            max_nights INTEGER,
            adjustment_type TEXT NOT NULL DEFAULT 'amount',
            adjustment_value REAL NOT NULL DEFAULT 0,
            priority INTEGER NOT NULL DEFAULT 100,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (rate_plan_id) REFERENCES rate_plans(id)
        );
        CREATE INDEX IF NOT EXISTS idx_rate_rules_active ON rate_rules(active, priority);
        """)



def _migrate_to_13(conn):
    # P2: discounts/vouchers and payment hardening fields (additive)
    if not _table_exists(conn, "discount_vouchers"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS discount_vouchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            discount_type TEXT NOT NULL DEFAULT 'amount',
            value REAL NOT NULL DEFAULT 0,
            max_uses INTEGER,
            used_count INTEGER NOT NULL DEFAULT 0,
            valid_from TEXT NOT NULL DEFAULT '',
            valid_to TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_discount_vouchers_active ON discount_vouchers(active);
        """)
    if not _table_exists(conn, "reservation_discounts"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservation_discounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL,
            voucher_id INTEGER,
            description TEXT NOT NULL,
            discount_type TEXT NOT NULL DEFAULT 'amount',
            value REAL NOT NULL DEFAULT 0,
            amount REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'applied',
            voided_at TEXT,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            applied_at TEXT,
            FOREIGN KEY (reservation_id) REFERENCES reservations(id),
            FOREIGN KEY (voucher_id) REFERENCES discount_vouchers(id)
        );
        CREATE INDEX IF NOT EXISTS idx_res_discounts_res ON reservation_discounts(reservation_id);
        """)
    if _table_exists(conn, "payments"):
        cols = _table_columns(conn, "payments")
        for col, decl in (
            ("idempotency_key", "TEXT NOT NULL DEFAULT ''"),
            ("allocation_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("reference", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(conn, "payments", col, decl, cols)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_idempotency ON payments(idempotency_key)")
        except Exception:
            pass



def _migrate_to_14(conn):
    # P3: room blocks / out-of-order (additive)
    if not _table_exists(conn, "room_blocks"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS room_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            block_type TEXT NOT NULL DEFAULT 'OOO',
            reason TEXT NOT NULL DEFAULT '',
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            resolved_at TEXT,
            FOREIGN KEY (room_id) REFERENCES rooms(id)
        );
        CREATE INDEX IF NOT EXISTS idx_room_blocks_room_dates ON room_blocks(room_id, start_date, end_date, status);
        """)



def _migrate_to_15(conn):
    # P3: housekeeping tasks (additive)
    if not _table_exists(conn, "housekeeping_tasks"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS housekeeping_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            reservation_id INTEGER,
            task_type TEXT NOT NULL DEFAULT 'cleaning',
            priority TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'pending',
            assignee TEXT NOT NULL DEFAULT '',
            due_at TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            started_at TEXT,
            completed_at TEXT,
            inspected_at TEXT,
            FOREIGN KEY (room_id) REFERENCES rooms(id),
            FOREIGN KEY (reservation_id) REFERENCES reservations(id)
        );
        CREATE INDEX IF NOT EXISTS idx_housekeeping_tasks_room_status ON housekeeping_tasks(room_id, status);
        CREATE INDEX IF NOT EXISTS idx_housekeeping_tasks_due ON housekeeping_tasks(status, due_at);
        """)



def _migrate_to_16(conn):
    # P4: cash shifts/register and night audit advisory records (additive)
    if not _table_exists(conn, "cash_shifts"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cash_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            opened_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            closed_at TEXT,
            opening_float REAL NOT NULL DEFAULT 0,
            expected_cash REAL NOT NULL DEFAULT 0,
            counted_cash REAL,
            variance REAL,
            notes TEXT NOT NULL DEFAULT '',
            opened_by TEXT NOT NULL DEFAULT '',
            closed_by TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_cash_shifts_status ON cash_shifts(status, opened_at);
        """)
    if not _table_exists(conn, "cash_movements"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cash_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shift_id INTEGER NOT NULL,
            movement_type TEXT NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            method TEXT NOT NULL DEFAULT 'Cash',
            reference TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (shift_id) REFERENCES cash_shifts(id)
        );
        CREATE INDEX IF NOT EXISTS idx_cash_movements_shift ON cash_movements(shift_id, at);
        """)
    if not _table_exists(conn, "night_audit_runs"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS night_audit_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            completed_at TEXT,
            summary_json TEXT NOT NULL DEFAULT '{}',
            exceptions_json TEXT NOT NULL DEFAULT '[]',
            notes TEXT NOT NULL DEFAULT '',
            run_by TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_night_audit_date ON night_audit_runs(business_date, status);
        """)



def _migrate_to_17(conn):
    # P4: RBAC/user groundwork and actor attribution (additive, non-breaking)
    if _table_exists(conn, "audit_log"):
        cols = _table_columns(conn, "audit_log")
        _ensure_column(conn, "audit_log", "actor_user_id", "INTEGER", cols)
        _ensure_column(conn, "audit_log", "actor_role", "TEXT NOT NULL DEFAULT ''", cols)
    if not _table_exists(conn, "users"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'staff',
            active INTEGER NOT NULL DEFAULT 1,
            password_salt TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_users_active ON users(active, role);
        """)
    if not _table_exists(conn, "roles"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS roles (
            name TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT '',
            permissions_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        """)
        # seed default roles (idempotent)
        try:
            defaults = [
                ("admin", "Full access (maps to existing unlock/super-password authority)", '["*"]'),
                ("manager", "Operational management", '["reservations.*","pos.*","inventory.*","housekeeping.*","reports.read","accounting.*"]'),
                ("front_desk", "Front desk and reservations operations", '["reservations.*","guests.*","housekeeping.read","reports.read"]'),
                ("housekeeping", "Housekeeping and room readiness", '["housekeeping.*","reservations.read"]'),
                ("accounting", "Accounting, payments, and reports", '["accounting.*","reports.read","reservations.read"]'),
            ]
            for name, desc, perms in defaults:
                conn.execute("INSERT OR IGNORE INTO roles (name, description, permissions_json) VALUES (?, ?, ?)", (name, desc, perms))
        except Exception:
            pass



def _migrate_to_18(conn):
    # P5: reservation groups (additive)
    if not _table_exists(conn, "reservation_groups"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservation_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            group_type TEXT NOT NULL DEFAULT 'group',
            contact_name TEXT NOT NULL DEFAULT '',
            contact_phone TEXT NOT NULL DEFAULT '',
            contact_email TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_reservation_groups_status ON reservation_groups(status);
        """)
    if not _table_exists(conn, "reservation_group_members"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservation_group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            reservation_id INTEGER NOT NULL UNIQUE,
            role TEXT NOT NULL DEFAULT 'member',
            notes TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (group_id) REFERENCES reservation_groups(id),
            FOREIGN KEY (reservation_id) REFERENCES reservations(id)
        );
        CREATE INDEX IF NOT EXISTS idx_res_group_members_group ON reservation_group_members(group_id);
        """)



def _migrate_to_19(conn):
    # P5: packages/bundles foundation (additive)
    if not _table_exists(conn, "packages"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_packages_active ON packages(active);
        """)
    if not _table_exists(conn, "package_items"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS package_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            package_id INTEGER NOT NULL,
            service_id INTEGER,
            description TEXT NOT NULL DEFAULT '',
            quantity REAL NOT NULL DEFAULT 1,
            unit_price REAL,
            taxable INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (package_id) REFERENCES packages(id),
            FOREIGN KEY (service_id) REFERENCES service_catalog(id)
        );
        CREATE INDEX IF NOT EXISTS idx_package_items_pkg ON package_items(package_id);
        """)
    if not _table_exists(conn, "reservation_packages"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservation_packages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id INTEGER NOT NULL,
            package_id INTEGER,
            package_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'applied',
            total_net REAL NOT NULL DEFAULT 0,
            total_tax REAL NOT NULL DEFAULT 0,
            total_gross REAL NOT NULL DEFAULT 0,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            notes TEXT NOT NULL DEFAULT '',
            applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            voided_at TEXT,
            FOREIGN KEY (reservation_id) REFERENCES reservations(id),
            FOREIGN KEY (package_id) REFERENCES packages(id)
        );
        CREATE INDEX IF NOT EXISTS idx_res_packages_res ON reservation_packages(reservation_id);
        """)
    if not _table_exists(conn, "reservation_package_lines"):
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS reservation_package_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_package_id INTEGER NOT NULL,
            reservation_service_id INTEGER NOT NULL,
            item_snapshot_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (reservation_package_id) REFERENCES reservation_packages(id),
            FOREIGN KEY (reservation_service_id) REFERENCES reservation_services(id)
        );
        CREATE INDEX IF NOT EXISTS idx_res_pkg_lines_pkg ON reservation_package_lines(reservation_package_id);
        """)



def _migrate_to_20(conn):
    # P4: link payments/refunds to cash shifts (additive)
    if _table_exists(conn, "payments"):
        cols = _table_columns(conn, "payments")
        _ensure_column(conn, "payments", "shift_id", "INTEGER", cols)
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_shift ON payments(shift_id)")
        except Exception:
            pass


def migrate():
    """Create or upgrade schema to SCHEMA_VERSION.

    Idempotent and serialized. Fresh DB is created inside an explicit transaction.
    Step migrations run in order and user_version is advanced only after success.
    Individual migration steps contain IF NOT EXISTS / column-existence / provenance guards.
    """
    conn = connect()
    with _lock:
        # Defensive: clear any stray transaction state before migration control.
        try:
            if getattr(conn, "in_transaction", False):
                conn.execute("ROLLBACK")
        except Exception:
            pass
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than supported {SCHEMA_VERSION}. "
                "Refusing to run to avoid data loss."
            )
        if version == 0:
            try:
                conn.executescript(_SCHEMA)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                ensure_runtime_indexes(conn)
                _record_migration_meta(conn, status="created", from_version=0, to_version=SCHEMA_VERSION)
            except Exception:
                raise
            return "created"
        if version < SCHEMA_VERSION:
            from_version = version
            try:
                conn.execute("BEGIN IMMEDIATE")
            except Exception:
                pass
            try:
                if version < 5:
                    _migrate_to_5(conn)
                if version < 6:
                    _migrate_to_6(conn)
                if version < 7:
                    _migrate_to_7(conn)
                if version < 8:
                    _migrate_to_8(conn)
                if version < 9:
                    _migrate_to_9(conn)
                if version < 10:
                    _migrate_to_10(conn)
                if version < 11:
                    _migrate_to_11(conn)
                if version < 12:
                    _migrate_to_12(conn)
                if version < 13:
                    _migrate_to_13(conn)
                if version < 14:
                    _migrate_to_14(conn)
                if version < 15:
                    _migrate_to_15(conn)
                if version < 16:
                    _migrate_to_16(conn)
                if version < 17:
                    _migrate_to_17(conn)
                if version < 18:
                    _migrate_to_18(conn)
                if version < 19:
                    _migrate_to_19(conn)
                if version < 20:
                    _migrate_to_20(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                ensure_runtime_indexes(conn)
                _record_migration_meta(conn, status="upgraded", from_version=from_version, to_version=SCHEMA_VERSION)
                try:
                    conn.execute("COMMIT")
                except Exception:
                    pass
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            return "upgraded"
        # Even on current schema, ensure helper indexes/provenance markers are present (idempotent).
        try:
            ensure_runtime_indexes(conn)
            _record_migration_meta(conn, status="current", from_version=version, to_version=version)
        except Exception:
            pass
        return "current"
