# db/migration_009_add_ng_declarations.py
"""
migration_009:
NG（仕損）枚数の申告のみを記録する ng_declarations テーブルを新設する。

生産実績入力画面（ui/kitting_production_entry.py）のNG入力は、これまでBOM展開・
部品確認・scrap_recordsへの登録までその場で行っていたが、「枚数の申告のみ」を
行う画面（生産実績入力画面）と「BOM展開して部品登録する」画面（NG入力画面
ui/ng_input_window.py）に分離するにあたり、申告値の保存先として新設する。

kitting_list_no・production_side・report_dateの組み合わせにつき常に最大1件
（「同日は1件」ルール、models/ng_declarations.py::save_ng_declaration() が実装する）。
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
            CREATE TABLE IF NOT EXISTS ng_declarations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                ng_qty REAL NOT NULL,
                report_date TEXT NOT NULL,
                is_unplanned INTEGER NOT NULL DEFAULT 0,
                imported_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        conn.commit()
        print("migration_009 completed.")
    except Exception:
        conn.rollback()
        print("migration_009 failed. Transaction rolled back.", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
