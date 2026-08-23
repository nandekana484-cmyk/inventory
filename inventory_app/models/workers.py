import sqlite3
import config

def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def create_worker(worker_id: str, name: str, role: str = 'operator') -> bool:
    """作業者を登録"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO workers (worker_id, name, role) VALUES (?, ?, ?)",
            (worker_id, name, role)
        )
        con.commit()
        return True

def get_active_workers():
    """有効な作業者一覧を取得"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT worker_id, name, role FROM workers WHERE is_active = 1")
        return [dict(row) for row in cur.fetchall()]
