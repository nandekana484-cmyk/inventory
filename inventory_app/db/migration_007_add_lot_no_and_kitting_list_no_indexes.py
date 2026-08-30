# db/migration_007_add_lot_no_and_kitting_list_no_indexes.py
"""
migration_007:
計画一覧表示のN+1クエリ調査（BOM_MIGRATION_NOTES.md参照）で判明した、
インデックス不足によるフルスキャンを解消するためのインデックスを追加する。

- kitting_plan_items.lot_no：list_plan_items_by_lot() の WHERE lot_no = ? 用。
- production_daily.kitting_list_no：get_app_cumulative_qty() の
  WHERE kitting_list_no = ? 用（productions_daily には元々インデックスが1つも無かった）。

いずれも非ユニークインデックスのため、migration_006のような事前重複チェックは不要。
"""
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_PATH


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_kitting_plan_items_lot_no
            ON kitting_plan_items(lot_no)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_production_daily_kitting_list_no
            ON production_daily(kitting_list_no)
        """)

        conn.commit()
        print("migration_007 completed.")
    except Exception:
        conn.rollback()
        print("migration_007 failed. Transaction rolled back.", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
