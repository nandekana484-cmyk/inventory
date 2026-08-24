# models/theoretical_inventory.py
import sqlite3
import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_theoretical_inventory_table():
    """理論在庫テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS theoretical_inventory (
                part_no TEXT PRIMARY KEY,
                qty INTEGER
            )
        """)
        con.commit()


def list_theoretical_inventory():
    """理論在庫の一覧を part_no 順で取得する。"""
    init_theoretical_inventory_table()
    with get_connection() as con:
        cur = con.execute("SELECT part_no, qty FROM theoretical_inventory ORDER BY part_no")
        return [dict(row) for row in cur.fetchall()]


def upsert_theoretical_inventory(part_no: str, qty: int):
    """理論在庫数量を登録または更新する。"""
    init_theoretical_inventory_table()
    with get_connection() as con:
        con.execute("""
            INSERT INTO theoretical_inventory (part_no, qty)
            VALUES (?, ?)
            ON CONFLICT(part_no) DO UPDATE SET qty = excluded.qty
        """, (part_no, qty))
        con.commit()


def delete_theoretical_inventory(part_no: str):
    """理論在庫データを削除する。"""
    init_theoretical_inventory_table()
    with get_connection() as con:
        con.execute("DELETE FROM theoretical_inventory WHERE part_no = ?", (part_no,))
        con.commit()
