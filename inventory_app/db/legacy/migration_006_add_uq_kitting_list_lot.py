# (コピーして db/migration_006_add_uq_kitting_list_lot.py として保存)
import sqlite3, sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from config import DB_PATH

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 1) 重複チェック（is_active = 1 で同一 kitting_list_no, lot_no が複数ないか）
        dup = cur.execute("""
            SELECT kitting_list_no, COALESCE(lot_no,'') as lot_no, COUNT(*) as c
            FROM kitting_plan_items
            WHERE COALESCE(is_active,1) = 1
            GROUP BY kitting_list_no, COALESCE(lot_no,'')
            HAVING c > 1
        """).fetchall()
        if dup:
            print("ERROR: 以下の (kitting_list_no, lot_no) に is_active=1 の重複があります。事前に解消してください。")
            for row in dup[:50]:
                print(row)
            raise SystemExit(1)

        # 2) 古いインデックスを削除（存在すれば）
        cur.execute("DROP INDEX IF EXISTS uq_kitting_plan_items_active_kitting_no")
        # 3) 新しい部分ユニークインデックスを作成
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_kitting_plan_items_active_kitting_lot
            ON kitting_plan_items(kitting_list_no, COALESCE(lot_no, ''))
            WHERE COALESCE(is_active, 1) = 1
        """)
        conn.commit()
        print("migration_006 completed.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    main()