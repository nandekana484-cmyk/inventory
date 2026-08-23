import sqlite3
import config

def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# =====================================================
# 96部品ごとの在庫数量（inventory_stock）
# ※ 最小構成。後日「仕掛」「仕損」「理論在庫」等の列・テーブルを追加可能。
# =====================================================

def init_inventory_stock_table():
    """在庫数量テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS inventory_stock (
                part_no TEXT PRIMARY KEY,
                stock_qty INTEGER
            )
        """)
        con.commit()


def list_inventory():
    """在庫数量の一覧を part_no 順で取得する。"""
    init_inventory_stock_table()
    with get_connection() as con:
        cur = con.execute("SELECT part_no, stock_qty FROM inventory_stock ORDER BY part_no")
        return [dict(row) for row in cur.fetchall()]


def get_inventory(part_no: str):
    """指定 part_no の在庫数量を取得する。存在しなければ None を返す。"""
    init_inventory_stock_table()
    with get_connection() as con:
        cur = con.execute(
            "SELECT part_no, stock_qty FROM inventory_stock WHERE part_no = ?", (part_no,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_inventory(part_no: str, qty: int):
    """在庫数量を登録または更新する。"""
    init_inventory_stock_table()
    with get_connection() as con:
        con.execute("""
            INSERT INTO inventory_stock (part_no, stock_qty)
            VALUES (?, ?)
            ON CONFLICT(part_no) DO UPDATE SET stock_qty = excluded.stock_qty
        """, (part_no, qty))
        con.commit()


def delete_inventory(part_no: str):
    """在庫数量を削除する。"""
    init_inventory_stock_table()
    with get_connection() as con:
        con.execute("DELETE FROM inventory_stock WHERE part_no = ?", (part_no,))
        con.commit()

