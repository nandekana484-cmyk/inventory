-- 注：kitting_plan_batches / kitting_plan_items（キッティング計画）は
-- ここには含まれない。models/kitting_plan.py の init_kitting_plan_tables() で
-- 別途作成される（idx_kitting_plan_items_lot_no インデックスも同所）。

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

-- 完成品マスタ
CREATE TABLE IF NOT EXISTS final_products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL
);

-- ロット・生産実績
CREATE TABLE IF NOT EXISTS lots (
    lot_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES final_products(product_id),
    planned_qty INTEGER NOT NULL,
    status TEXT DEFAULT 'active'
);

-- plan_item_id・kitting_list_no列、および idx_production_daily_kitting_list_no
-- インデックスは、この時点ではまだ存在しない列に対する追加のため schema.sql には
-- 書けない。db/init_db.py の init_database_at()（ALTER TABLE直後）で作成される。
-- 既存DBに対しては db/migration_002.py（列追加）・db/migration_007.py（インデックス）
-- を参照。
CREATE TABLE IF NOT EXISTS production_daily (
    prod_log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id TEXT REFERENCES lots(lot_id),
    group_id TEXT,
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

-- ロット統合（D-145。既存の DB にはマイグレーション 014 で足す。CREATE 文は db/migrations/m014_add_lot_merges.py と同じにする）
CREATE TABLE IF NOT EXISTS lot_merges (
    member_lot TEXT PRIMARY KEY,
    target_lot TEXT NOT NULL,
    merged_at TEXT NOT NULL,
    merged_by TEXT,
    note TEXT,
    CHECK (member_lot = target_lot || '-'),
    CHECK (target_lot <> '' AND substr(target_lot, -1) <> '-')
);
CREATE INDEX IF NOT EXISTS idx_lot_merges_target_lot ON lot_merges(target_lot);

-- 現場実績入力アプリの実績CSV（登録待ち）。既存の DB にはマイグレーション 015 で足す。CREATE 文は db/migrations/m015_add_external_actual_tables.py と同じにする
CREATE TABLE IF NOT EXISTS external_actual_batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    source_db_name TEXT NOT NULL,
    target_db_name TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    imported_by TEXT
);
CREATE TABLE IF NOT EXISTS external_actual_rows (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES external_actual_batches(batch_id),
    csv_row_no INTEGER NOT NULL,
    kitting_list_no TEXT NOT NULL,
    lot_no TEXT NOT NULL,
    file_no TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('1', '2')),
    report_date TEXT NOT NULL,
    daily_qty INTEGER NOT NULL CHECK (daily_qty >= 0),
    ng_side1 INTEGER NOT NULL DEFAULT 0 CHECK (ng_side1 >= 0),
    ng_side2 INTEGER NOT NULL DEFAULT 0 CHECK (ng_side2 >= 0),
    worker_id TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'registered')),
    registered_at TEXT,
    registered_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_external_actual_rows_pending ON external_actual_rows(kitting_list_no, lot_no) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_external_actual_rows_batch ON external_actual_rows(batch_id);
