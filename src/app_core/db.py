"""Property manager — database layer. Single persistent connection with WAL."""
import sqlite3
import threading
from contextlib import contextmanager

from . import paths

DATA_DIR = paths.DATA_DIR
BASE_DIR = paths.BASE_DIR
DB_PATH = paths.DB_PATH
SCHEMA_VERSION = 9

_conn = None
_lock = threading.RLock()


def connect():
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.execute("PRAGMA synchronous = NORMAL")
        _conn.execute("PRAGMA busy_timeout = 30000")
        return _conn


def close():
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


@contextmanager
def transaction():
    """Serialized write transaction (BEGIN IMMEDIATE) on the shared connection."""
    conn = connect()
    with _lock:
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
    return connect().execute(sql, params)


def user_version():
    return connect().execute("PRAGMA user_version").fetchone()[0]


def is_fresh():
    return user_version() == 0


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
    room_id INTEGER NOT NULL,
    rate_plan_id INTEGER,
    agreed_nightly_rate REAL NOT NULL,
    tax_amount REAL NOT NULL DEFAULT 0,
    check_in_date TEXT NOT NULL,
    check_out_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Reserved',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (guest_id) REFERENCES guests(id),
    FOREIGN KEY (room_id) REFERENCES rooms(id),
    FOREIGN KEY (rate_plan_id) REFERENCES rate_plans(id)
);
CREATE INDEX idx_res_guest ON reservations(guest_id);
CREATE INDEX idx_res_status ON reservations(status);
CREATE INDEX idx_res_dates ON reservations(check_in_date, check_out_date);
CREATE INDEX idx_res_room ON reservations(room_id);

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

CREATE TABLE expense_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_name TEXT NOT NULL UNIQUE
);

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
    voided_at TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    FOREIGN KEY (reservation_id) REFERENCES reservations(id)
);
CREATE INDEX idx_payments_res ON payments(reservation_id);

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
    action TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT ''
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
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pos_tabs)").fetchall()]
    if "activity_id" not in cols:
        conn.execute("ALTER TABLE pos_tabs ADD COLUMN activity_id INTEGER REFERENCES activities(id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_activity ON pos_tabs(activity_id)")


def _migrate_to_9(conn):
    # inventory identity/barcode/batch tracking columns
    inv_cols = [r[1] for r in conn.execute("PRAGMA table_info(inventory)").fetchall()]
    def _add_inv(col, decl):
        if col not in inv_cols:
            conn.execute(f"ALTER TABLE inventory ADD COLUMN {col} {decl}")
            inv_cols.append(col)
    _add_inv("sku", "TEXT")
    _add_inv("barcode", "TEXT")
    _add_inv("barcode_type", "TEXT NOT NULL DEFAULT 'code128'")
    _add_inv("track_batches", "INTEGER NOT NULL DEFAULT 0")
    _add_inv("shelf_life_days", "INTEGER")
    _add_inv("default_supplier", "TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_sku ON inventory(sku) WHERE sku IS NOT NULL AND sku <> ''")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_barcode ON inventory(barcode) WHERE barcode IS NOT NULL AND barcode <> ''")

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

    log_cols = [r[1] for r in conn.execute("PRAGMA table_info(inventory_logs)").fetchall()]
    if "batch_id" not in log_cols:
        conn.execute("ALTER TABLE inventory_logs ADD COLUMN batch_id INTEGER REFERENCES inventory_batches(id)")
    if "unit_cost" not in log_cols:
        conn.execute("ALTER TABLE inventory_logs ADD COLUMN unit_cost REAL")
    if "balance_after" not in log_cols:
        conn.execute("ALTER TABLE inventory_logs ADD COLUMN balance_after REAL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_invlogs_batch ON inventory_logs(batch_id)")

    pos_cols = [r[1] for r in conn.execute("PRAGMA table_info(pos_tabs)").fetchall()]
    if "batch_id" not in pos_cols:
        conn.execute("ALTER TABLE pos_tabs ADD COLUMN batch_id INTEGER REFERENCES inventory_batches(id)")
    if "lot_code" not in pos_cols:
        conn.execute("ALTER TABLE pos_tabs ADD COLUMN lot_code TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_batch ON pos_tabs(batch_id)")

    # Backfill legacy/opening batches for existing stock so traceability is consistent.
    items = conn.execute("SELECT id, stock_qty, unit_cost, created_at FROM inventory").fetchall()
    for it in items:
        has = conn.execute("SELECT 1 FROM inventory_batches WHERE item_id=? LIMIT 1", (it["id"],)).fetchone()
        if has:
            continue
        qty = float(it["stock_qty"] or 0)
        if abs(qty) < 1e-9:
            continue
        received_at = conn.execute(
            "SELECT MIN(at) AS at FROM inventory_logs WHERE item_id=?", (it["id"],)
        ).fetchone()["at"] or it["created_at"] or ""
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
             received_at or "1970-01-01 00:00:00", None, "", "legacy", "Opening/legacy stock backfill", "active", "{}")
        )
        bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO inventory_logs (item_id, change_type, quantity, reference, batch_id, unit_cost, balance_after)
               VALUES (?, 'OPENING', ?, ?, ?, ?, ?)""",
            (it["id"], qty, "legacy opening batch", bid, float(it["unit_cost"] or 0), qty)
        )


def migrate():
    conn = connect()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return "created"
    if version < SCHEMA_VERSION:
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
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return "upgraded"
    return "current"
