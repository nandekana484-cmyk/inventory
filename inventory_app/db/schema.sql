-- 作業者・ログイン
CREATE TABLE IF NOT EXISTS workers (
    worker_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT DEFAULT 'operator',
    is_active INTEGER DEFAULT 1
);

-- 部品マスタ
CREATE TABLE IF NOT EXISTS parts (
    part_id TEXT PRIMARY KEY,
    code96 TEXT NOT NULL,
    part_type TEXT,
    shelf_type TEXT,
    shape_category TEXT,
    is_high_value INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1
);

-- 完成品・基板・面・BOM定義
CREATE TABLE IF NOT EXISTS final_products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS board_definitions (
    board_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES final_products(product_id),
    setup_file_no TEXT,
    required_sides INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS component_groups (
    group_id TEXT PRIMARY KEY,
    board_id TEXT REFERENCES board_definitions(board_id),
    side_number INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS component_bom (
    bom_id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT REFERENCES component_groups(group_id),
    code96 TEXT NOT NULL,
    usage_qty REAL NOT NULL
);

-- ロット・生産実績
CREATE TABLE IF NOT EXISTS lots (
    lot_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES final_products(product_id),
    planned_qty INTEGER NOT NULL,
    status TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS production_daily (
    prod_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id TEXT REFERENCES lots(lot_id),
    group_id TEXT REFERENCES component_groups(group_id),
    report_date TEXT NOT NULL,
    daily_qty INTEGER NOT NULL,
    worker_id TEXT REFERENCES workers(worker_id)
);

CREATE TABLE IF NOT EXISTS usage_daily (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prod_log_id INTEGER REFERENCES production_daily(prod_log_id),
    code96 TEXT NOT NULL,
    used_qty REAL NOT NULL
);

-- 入荷・調整
CREATE TABLE IF NOT EXISTS incoming_goods_log (
    incoming_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code96 TEXT NOT NULL,
    qty REAL NOT NULL,
    received_date TEXT NOT NULL,
    status TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS stock_manual_adjustment (
    adj_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code96 TEXT NOT NULL,
    qty REAL NOT NULL,
    reason TEXT,
    adjusted_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending'
);

-- 締め処理・仕掛調整結果
CREATE TABLE IF NOT EXISTS closing_runs (
    closing_id INTEGER PRIMARY KEY AUTOINCREMENT,
    closing_date TEXT NOT NULL,
    executed_by TEXT REFERENCES workers(worker_id)
);

CREATE TABLE IF NOT EXISTS closing_wip_adjustment (
    wip_adj_id INTEGER PRIMARY KEY AUTOINCREMENT,
    closing_id INTEGER REFERENCES closing_runs(closing_id),
    lot_id TEXT NOT NULL,
    code96 TEXT NOT NULL,
    returned_qty REAL NOT NULL
);

-- 監査ログ
CREATE TABLE IF NOT EXISTS audit_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    detail TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    worker_id TEXT
);
