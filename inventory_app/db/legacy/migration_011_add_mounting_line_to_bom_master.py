# db/migration_011_add_mounting_line_to_bom_master.py
"""
migration_011:
bom_masterテーブルに mounting_line TEXT NOT NULL DEFAULT '' 列を追加し、
UNIQUE制約を (file_no, production_side, mounting_line, part_no, data_ym) に
拡張する。

背景：BOM計算（services.bom_service._calculate_bom()）が「実装ライン」列を
区別せず、同一file_no・sideの全ライン分の行をそのまま合算していたため、
複数の実装ラインが同一BOMを重複記載しているファイル（実データで156件の
setup_file_no×production_side組み合わせ、is_active=1の約23%）では
部品数量が実装ライン数倍（2〜3倍）に過大計算されるバグがあった。
services.bom_service.get_parts_for_file_no()にmounting_line引数を追加して
修正したため、bom_masterのキャッシュキーにもmounting_lineを含めないと、
異なるラインの計算結果が同じキャッシュ行に混在してしまう。

SQLiteはUNIQUE制約の変更にALTER TABLEを使えないため、テーブルを
作り直す。既存のbom_masterは共有フォルダのTSVから再計算可能な
キャッシュのみ（実データの源泉ではない）のため、既存データは保持せず
作り直す（TRUNCATE相当）。
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
                data_ym TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                source_file_hash TEXT,
                UNIQUE(file_no, production_side, mounting_line, part_no, data_ym)
            )
        """)

        conn.commit()
        print(
            f"migration_011 completed. "
            f"既存の{existing_count}件のキャッシュ行を破棄し、mounting_line列込みで再作成しました。"
        )
    except Exception:
        conn.rollback()
        print("migration_011 failed. Transaction rolled back.", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
