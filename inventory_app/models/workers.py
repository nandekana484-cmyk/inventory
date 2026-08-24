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


def get_all_workers():
    """作業者管理画面用：無効化済みも含めた全作業者一覧を worker_id 順で取得"""
    with get_connection() as con:
        cur = con.execute(
            "SELECT worker_id, name, role, is_active FROM workers ORDER BY worker_id"
        )
        return [dict(row) for row in cur.fetchall()]


def upsert_worker(worker_id: str, name: str, role: str = "operator", is_active: int = 1):
    """
    作業者を登録または更新する（既存なら上書き、なければ新規登録）。
    差分検知は行わず常に上書きする。
    """
    with get_connection() as con:
        con.execute("""
            INSERT INTO workers (worker_id, name, role, is_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                is_active = excluded.is_active
        """, (worker_id, name, role, 1 if is_active else 0))
        con.commit()


def set_worker_active(worker_id: str, is_active: bool):
    """
    作業者の有効/無効を切り替える。

    production_daily.worker_id や audit_log.worker_id 等、過去実績から
    作業者IDが参照されているため、レコード自体は削除せず is_active フラグの
    切り替えのみで対応する（ログイン画面の一覧は is_active=1 のみ表示）。
    """
    with get_connection() as con:
        con.execute(
            "UPDATE workers SET is_active = ? WHERE worker_id = ?",
            (1 if is_active else 0, worker_id),
        )
        con.commit()
