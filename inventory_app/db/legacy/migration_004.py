# db/migration_004.py
import sqlite3
from config import DB_PATH

def upgrade():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    ddls = [
        "ALTER TABLE kitting_plan_items ADD COLUMN version INTEGER DEFAULT 1",
        "ALTER TABLE kitting_plan_items ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE kitting_plan_items ADD COLUMN previous_plan_item_id INTEGER",
        "ALTER TABLE kitting_plan_items ADD COLUMN created_by TEXT",
        "ALTER TABLE kitting_plan_items ADD COLUMN created_at TEXT DEFAULT (datetime('now','localtime'))"
    ]

    for ddl in ddls:
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError:
            # 既に列がある場合はスキップ
            pass

    conn.commit()
    conn.close()
    print("migration_004 applied.")

if __name__ == "__main__":
    upgrade()
