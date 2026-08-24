# models/scrap_records.py
"""
NG（仕損）実績のDBアクセス層（フェーズ2・新BOM基盤接続）。

ui.ng_input_window で、新BOM基盤（services.bom_service.BOMService）による
BOM展開結果から操作者が選択した部品を、96コード単位の「消費数量」として
保存する。ng_qty 列は列名こそ「NG数量」だが、実際に保存する値は
BOM展開で計算済みの消費数量（qty_per_product × 入力されたNG枚数）である。
そのため services.inventory_diff_service 側では再計算せず、
そのまま part_no 単位に SUM するだけでよい。
"""
import sqlite3

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_scrap_records_table():
    """scrap_records テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS scrap_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                part_no TEXT NOT NULL,
                ng_qty REAL NOT NULL,
                report_date TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.commit()


def save_scrap_record(kitting_list_no: str, file_no: str, side: int, part_no: str,
                       ng_qty: float, report_date: str):
    """
    NG（仕損）実績を1部品・1レコードとして追加保存する（洗い替えではなく追記）。
    """
    init_scrap_records_table()
    with get_connection() as con:
        con.execute("""
            INSERT INTO scrap_records (
                kitting_list_no, file_no, production_side, part_no, ng_qty, report_date
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, (kitting_list_no, file_no, side, part_no, ng_qty, report_date))
        con.commit()


def list_scrap_records_by_kitting_no(kitting_list_no: str) -> list:
    """指定キッティングリストNo.のNG実績履歴を取得する。"""
    init_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT * FROM scrap_records
            WHERE kitting_list_no = ?
            ORDER BY report_date, id
        """, (kitting_list_no,))
        return [dict(row) for row in cur.fetchall()]


def query_scrap_totals() -> dict:
    """
    96コード（part_no）単位でNG（仕損）消費数量を集計する。

    戻り値：{part_no: 合計ng_qty, ...}
    """
    init_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, COALESCE(SUM(ng_qty), 0) AS total_qty
            FROM scrap_records
            GROUP BY part_no
        """)
        return {row["part_no"]: row["total_qty"] for row in cur.fetchall()}
