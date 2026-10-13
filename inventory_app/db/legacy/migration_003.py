# db/migration_003.py
import sqlite3
from config import DB_PATH

def upgrade():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # kitting_plan_batches に delete_flag 列を追加（無ければ）
    try:
        cur.execute("ALTER TABLE kitting_plan_batches ADD COLUMN delete_flag INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        # 既に列がある場合はスキップ
        pass

    conn.commit()
    conn.close()
    print("migration_003 applied.")

if __name__ == "__main__":
    upgrade()