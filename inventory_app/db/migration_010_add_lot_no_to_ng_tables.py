# db/migration_010_add_lot_no_to_ng_tables.py
"""
migration_010:
NG関連の2テーブル（scrap_records・ng_declarations）に lot_no TEXT 列（NULL許容）を
追加する。

背景：実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースが
478件確認されており、production_daily側は既にlot_no（lot_id列）で一意化する
修正を実施済み。scrap_records・ng_declarations も同様の巻き込みリスクがあるため
（models/scrap_records.py・models/ng_declarations.pyのコード側修正とセットで）
lot_no列を追加する。

既存データはlot_no=NULLのまま（過去の登録がどのlot_noに対するものだったかは
遡って判別できないため、無理に埋めない）。計画外（is_unplanned=1）の登録は
今後もlot_no=NULLのまま運用する。

テーブル自体が未作成の環境にも対応できるよう、先に有無を確認し、無ければ
lot_no列込みで新規作成、あれば列だけALTER TABLEで追加する
（db/migration_008・009と同じパターン）。
"""
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_PATH


def _ensure_lot_no_column(conn, table_name, create_sql):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    table_exists = cur.fetchone() is not None

    if not table_exists:
        cur.execute(create_sql)
        return

    cur.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in cur.fetchall()}
    if "lot_no" not in existing_columns:
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN lot_no TEXT")


def main():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")

        _ensure_lot_no_column(conn, "scrap_records", """
            CREATE TABLE scrap_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                part_no TEXT NOT NULL,
                ng_qty REAL NOT NULL,
                report_date TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                is_unplanned INTEGER NOT NULL DEFAULT 0,
                lot_no TEXT
            )
        """)

        _ensure_lot_no_column(conn, "ng_declarations", """
            CREATE TABLE ng_declarations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                ng_qty REAL NOT NULL,
                report_date TEXT NOT NULL,
                is_unplanned INTEGER NOT NULL DEFAULT 0,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                lot_no TEXT
            )
        """)

        conn.commit()
        print("migration_010 completed.")
    except Exception:
        conn.rollback()
        print("migration_010 failed. Transaction rolled back.", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
