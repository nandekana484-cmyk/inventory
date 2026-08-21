import sqlite3
from config import DB_PATH

def get_connection():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def add_part(part_id: str, code96: str, part_type: str = None, shelf_type: str = None, shape_category: str = None) -> bool:
    """部品・リール情報を追加"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO parts (part_id, code96, part_type, shelf_type, shape_category)
            VALUES (?, ?, ?, ?, ?)
            """,
            (part_id, code96, part_type, shelf_type, shape_category)
        )
        con.commit()
        return True

def get_part_by_id(part_id: str):
    """リールIDから部品情報を検索"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM parts WHERE part_id = ?", (part_id,))
        row = cur.fetchone()
        return dict(row) if row else None
