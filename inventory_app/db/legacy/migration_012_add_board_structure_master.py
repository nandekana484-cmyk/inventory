# db/migration_012_add_board_structure_master.py
"""
migration_012:
構成基板数マスタ（board_structure_master）テーブルを新設する。

kitting_plan_items.board_name（基板名）単位に「構成基板数」を保持する参照専用
マスタ。ui/board_structure_import_window.py のCSVインポートでのみ更新され、
生産実績入力画面（ui/kitting_production_entry.py）の計画情報欄で参照表示にのみ
使う。

models/board_structure_master.py::init_board_structure_master_table() が
CREATE TABLE IF NOT EXISTS で同じDDLを冪等に実行するため、本マイグレーションを
実行しなくても初回アクセス時に自動作成される。db/migration_009等の既存の
「新規テーブル追加」マイグレーションと同様、環境間でのDBスキーマ変更を明示的に
記録・共有する目的で作成する。
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
            CREATE TABLE IF NOT EXISTS board_structure_master (
                board_name TEXT PRIMARY KEY,
                board_count REAL,
                board_name_normalized TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_board_structure_master_normalized
            ON board_structure_master(board_name_normalized)
        """)

        conn.commit()
        print("migration_012 completed.")
    except Exception:
        conn.rollback()
        print("migration_012 failed. Transaction rolled back.", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
