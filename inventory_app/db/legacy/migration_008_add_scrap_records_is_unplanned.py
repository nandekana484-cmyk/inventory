# db/migration_008_add_scrap_records_is_unplanned.py
"""
migration_008:
scrap_records（NG／仕損実績）に、計画外（kitting_plan_itemsに存在しないfile_no・
生産面へのNG登録。ui.ng_input_window のファイルNo.検索経由）かどうかを示す
is_unplanned フラグ列（INTEGER, NOT NULL, DEFAULT 0）を追加する。

query_scrap_totals()（在庫差異レポートで使われている唯一の集計関数）は
part_no単位のSUMのみでis_unplannedを区別しないため、この列の追加によって
既存の集計結果には影響しない。

scrap_records テーブル自体が未作成（NG入力機能が一度も使われていない環境）の
場合にも対応できるよう、テーブルの有無を先に確認する。テーブルが存在する場合は
既存データを保持したまま ALTER TABLE で列を追加する（既に列がある場合は何もしない）。
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

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scrap_records'")
        table_exists = cur.fetchone() is not None

        if not table_exists:
            cur.execute("""
                CREATE TABLE scrap_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kitting_list_no TEXT NOT NULL,
                    file_no TEXT NOT NULL,
                    production_side INTEGER NOT NULL,
                    part_no TEXT NOT NULL,
                    ng_qty REAL NOT NULL,
                    report_date TEXT NOT NULL,
                    imported_at TEXT DEFAULT (datetime('now','localtime')),
                    is_unplanned INTEGER NOT NULL DEFAULT 0
                )
            """)
        else:
            cur.execute("PRAGMA table_info(scrap_records)")
            existing_columns = {row[1] for row in cur.fetchall()}
            if "is_unplanned" not in existing_columns:
                cur.execute("""
                    ALTER TABLE scrap_records
                    ADD COLUMN is_unplanned INTEGER NOT NULL DEFAULT 0
                """)

        conn.commit()
        print("migration_008 completed.")
    except Exception:
        conn.rollback()
        print("migration_008 failed. Transaction rolled back.", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
