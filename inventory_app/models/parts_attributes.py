# models/parts_attributes.py
"""
部品属性マスタ（丁取り数等）のDBアクセス層（フェーズ3・新BOM基盤統合）。

新BOM計算ロジック（services.bom_service.BOMService._calculate_bom）で、
BOM TSVの係数が0かつRフラグがある行の qty 計算に丁取り数（teitori）を使う
（qty = 部品員数 ÷ 丁取り数）。
"""
import sqlite3

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_parts_attributes_table():
    """parts_attributes テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS parts_attributes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_no TEXT NOT NULL,
                teitori INTEGER,
                part_type TEXT,
                supply_type TEXT,
                full_qty INTEGER,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(part_no)
            )
        """)
        con.commit()


def upsert_parts_attributes(part_no: str, teitori, part_type: str = None,
                             supply_type: str = None, full_qty=None):
    """
    96コード（part_no）をキーに部品属性（丁取り数等）を登録・更新する。
    既存なら上書き、なければ新規登録する（差分検知は行わず常に上書き）。
    """
    init_parts_attributes_table()
    with get_connection() as con:
        con.execute("""
            INSERT INTO parts_attributes (part_no, teitori, part_type, supply_type, full_qty)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(part_no) DO UPDATE SET
                teitori = excluded.teitori,
                part_type = excluded.part_type,
                supply_type = excluded.supply_type,
                full_qty = excluded.full_qty,
                imported_at = datetime('now', 'localtime')
        """, (part_no, teitori, part_type, supply_type, full_qty))
        con.commit()


def get_parts_attributes(part_no: str):
    """指定 part_no（96コード）の部品属性を取得する。存在しなければ None を返す。"""
    init_parts_attributes_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, teitori, part_type, supply_type, full_qty
            FROM parts_attributes WHERE part_no = ?
        """, (part_no,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_parts_attributes() -> list:
    """部品属性の一覧を part_no 順で取得する（インポート画面の一覧表示用）。"""
    init_parts_attributes_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, teitori, part_type, supply_type, full_qty
            FROM parts_attributes ORDER BY part_no
        """)
        return [dict(row) for row in cur.fetchall()]
