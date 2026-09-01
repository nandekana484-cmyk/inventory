# db/migration_013_add_item_type_to_bom_master.py
"""
migration_013:
bom_masterテーブルに item_type TEXT NOT NULL DEFAULT 'part' 列を追加する。

背景：NG入力画面・仕掛展開画面のBOM展開結果に、通常部品に加えて基板自身
（K行の96コード）の消費枚数を含める機能を追加した（services.bom_service.
_calculate_bom()）。通常部品はitem_type="part"、基板自身の行はitem_type="board"
として区別する。bom_masterはBOM計算結果のキャッシュのため、item_type列が
無いとキャッシュヒット時（2回目以降のget_parts_for_file_no()呼び出し）に
この区別が失われてしまう。

db/migration_011と同様、SQLiteはNOT NULL DEFAULT付き列の追加程度なら
ALTER TABLEでも可能だが、bom_masterは共有フォルダのTSVから再計算可能な
キャッシュのみ（実データの源泉ではない）のため、既存データは保持せず
作り直す（migration_011と同じ方針）。
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

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bom_master'")
        existed = cur.fetchone() is not None

        existing_count = 0
        if existed:
            cur.execute("SELECT COUNT(*) FROM bom_master")
            existing_count = cur.fetchone()[0]

        cur.execute("DROP TABLE IF EXISTS bom_master")
        cur.execute("""
            CREATE TABLE bom_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                mounting_line TEXT NOT NULL DEFAULT '',
                part_no TEXT NOT NULL,
                qty_per_product REAL NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'part',
                data_ym TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                source_file_hash TEXT,
                UNIQUE(file_no, production_side, mounting_line, part_no, data_ym)
            )
        """)

        conn.commit()
        print(
            f"migration_013 completed. "
            f"既存の{existing_count}件のキャッシュ行を破棄し、item_type列込みで再作成しました。"
        )
    except Exception:
        conn.rollback()
        print("migration_013 failed. Transaction rolled back.", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
