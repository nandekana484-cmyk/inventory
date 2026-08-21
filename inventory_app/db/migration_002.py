# db/migration_002.py
import sqlite3

DB_PATH = r"C:\work\inventory\inventory_app\db\inventory.db"

def upgrade():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kitting_plan_batches (
        plan_batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_file TEXT NOT NULL,
        imported_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        imported_by TEXT,
        row_count INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS kitting_plan_items (
        plan_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_batch_id INTEGER NOT NULL,
        kitting_list_no TEXT NOT NULL UNIQUE,
        delete_flag INTEGER DEFAULT 0,
        setup_file_no TEXT,
        lot_no TEXT,
        mounting_line TEXT,
        board_name TEXT,
        planned_qty REAL DEFAULT 0,
        cumulative_qty_external REAL DEFAULT 0,
        order_qty REAL DEFAULT 0,
        production_side TEXT,
        status TEXT,
        plan_start_datetime TEXT,
        plan_end_datetime TEXT,
        deadline TEXT,
        actual_start_datetime TEXT,
        actual_end_datetime TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        updated_at TEXT DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (plan_batch_id) REFERENCES kitting_plan_batches(plan_batch_id)
    )
    """)

    # production_daily に plan_item_id / kitting_list_no を追加
    # （既に存在する場合はスキップするため try-except）
    for ddl in [
        "ALTER TABLE production_daily ADD COLUMN plan_item_id INTEGER",
        "ALTER TABLE production_daily ADD COLUMN kitting_list_no TEXT",
    ]:
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError as e:
            print(f"skip: {ddl} -> {e}")

    conn.commit()
    conn.close()
    print("migration_002 applied.")

if __name__ == "__main__":
    upgrade()