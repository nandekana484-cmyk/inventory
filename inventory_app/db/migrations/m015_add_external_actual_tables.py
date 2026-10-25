"""
015: 現場実績入力アプリの実績CSV（登録待ち）の表 external_actual_batches・external_actual_rows を足す。
表を足すだけで、既存の表とデータは変えない。新しく作る DB は db/schema.sql で同じ表を作る。
CREATE 文は一字一句同じにする（tests/test_external_actual_schema.py で確かめる）。
"""
VERSION = 15

# 取り込んだCSV1ファイル=1行。target_db_name は取り込んだ時点の月次DBのフォルダ名（登録の直前に今のDBと比べる）。
CREATE_BATCHES = """CREATE TABLE IF NOT EXISTS external_actual_batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    source_db_name TEXT NOT NULL,
    target_db_name TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    imported_by TEXT
)"""

# CSVの1行=1行。pending は登録待ち、registered は登録済み（消さずに残す）。exported_at は行ごとの書き出し日時（古い再送の判定用）。
CREATE_ROWS = """CREATE TABLE IF NOT EXISTS external_actual_rows (
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
)"""

# 同じ計画(キッティングNo.+ロットNo.)の登録待ちは1行だけ。再送は新しい行で置き換える。
CREATE_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_external_actual_rows_pending "
    "ON external_actual_rows(kitting_list_no, lot_no) WHERE status = 'pending'",
    "CREATE INDEX IF NOT EXISTS idx_external_actual_rows_batch ON external_actual_rows(batch_id)",
)


def upgrade(con):
    con.execute(CREATE_BATCHES)
    con.execute(CREATE_ROWS)
    for ddl in CREATE_INDEXES:
        con.execute(ddl)
