# models/bom_master.py
"""
BOM基盤用のDBアクセス層。

共有フォルダのTSVから計算したBOM（file_no・面・96コード単位の構成数）を
月（data_ym）単位でキャッシュ保存し、以降は再計算せずDBから取得できるようにする。
"""
import sqlite3
from datetime import datetime

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_bom_master_table():
    """bom_master テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bom_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                part_no TEXT NOT NULL,
                qty_per_product REAL NOT NULL,
                data_ym TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                source_file_hash TEXT,
                UNIQUE(file_no, production_side, part_no, data_ym)
            )
        """)
        con.commit()


def get_current_ym() -> str:
    """当月を 'YYYYMM' 形式で返す。"""
    return datetime.now().strftime("%Y%m")


def query_bom_master(file_no: str, side: int, data_ym: str = None) -> list:
    """
    file_no・side（・data_ym）に対応する bom_master の行を取得する。
    data_ym を省略した場合は当月（get_current_ym()）分を対象とする。

    戻り値：[{"part_no": ..., "qty_per_product": ...}, ...]（該当なしは空リスト）
    """
    init_bom_master_table()
    if data_ym is None:
        data_ym = get_current_ym()

    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, qty_per_product
            FROM bom_master
            WHERE file_no = ? AND production_side = ? AND data_ym = ?
            ORDER BY part_no
        """, (file_no, side, data_ym))
        return [dict(row) for row in cur.fetchall()]


def save_bom_master(file_no: str, side: int, parts: list, data_ym: str = None,
                     source_file_hash: str = None):
    """
    BOM計算結果（parts）を bom_master へ保存する。
    data_ym を省略した場合は当月（get_current_ym()）を使う。

    parts：[{"part_no": ..., "qty_per_product": ...}, ...]

    同一 (file_no, production_side, part_no, data_ym) は UNIQUE制約により
    上書き更新する（常に上書き。差分検知は行わない）。
    """
    init_bom_master_table()
    if data_ym is None:
        data_ym = get_current_ym()

    with get_connection() as con:
        for part in parts:
            con.execute("""
                INSERT INTO bom_master (
                    file_no, production_side, part_no, qty_per_product,
                    data_ym, source_file_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_no, production_side, part_no, data_ym) DO UPDATE SET
                    qty_per_product = excluded.qty_per_product,
                    imported_at = datetime('now', 'localtime'),
                    source_file_hash = excluded.source_file_hash
            """, (file_no, side, part["part_no"], part["qty_per_product"],
                  data_ym, source_file_hash))
        con.commit()
