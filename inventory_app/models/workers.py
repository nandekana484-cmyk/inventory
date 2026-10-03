from models.db_common import get_master_connection


def init_workers_table():
    """workers テーブルの初期化（既存があれば何もしない）。db/schema.sqlの定義と同一。"""
    with get_master_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS workers (
                worker_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT DEFAULT 'operator',
                is_active INTEGER DEFAULT 1
            )
        """)
        con.commit()


def create_worker(worker_id: str, name: str, role: str = 'operator') -> bool:
    """作業者を登録"""
    init_workers_table()
    with get_master_connection() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO workers (worker_id, name, role) VALUES (?, ?, ?)",
            (worker_id, name, role)
        )
        con.commit()
        return True

def get_active_workers():
    """有効な作業者一覧を取得"""
    init_workers_table()
    with get_master_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT worker_id, name, role FROM workers WHERE is_active = 1")
        return [dict(row) for row in cur.fetchall()]


def get_all_workers():
    """作業者管理画面用：無効化済みも含めた全作業者一覧を worker_id 順で取得"""
    init_workers_table()
    with get_master_connection() as con:
        cur = con.execute(
            "SELECT worker_id, name, role, is_active FROM workers ORDER BY worker_id"
        )
        return [dict(row) for row in cur.fetchall()]


def upsert_worker(worker_id: str, name: str, role: str = "operator", is_active: int = 1):
    """
    作業者を登録または更新する（既存なら上書き、なければ新規登録）。
    差分検知は行わず常に上書きする。
    """
    init_workers_table()
    with get_master_connection() as con:
        con.execute("""
            INSERT INTO workers (worker_id, name, role, is_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                is_active = excluded.is_active
        """, (worker_id, name, role, 1 if is_active else 0))
        con.commit()


def any_admin_exists() -> bool:
    """
    role='admin'の作業者がmaster.dbに1人でも存在するか返す（2026-10-03追加、
    ui.worker_registration_window.WorkerRegistrationWindowの役割選択肢の
    制御に使う）。

    is_active（有効/無効）は問わない：無効化されたadminが1人いるだけの状態で
    「admin不在」とみなし、新規登録画面で再び誰でもadminを選べるようにして
    しまうと、意図せず複数人がadmin権限を持つ抜け道になり得るため、
    有効/無効に関わらずrole='admin'の行が1件でも存在すれば「存在する」扱いとする。
    """
    init_workers_table()
    with get_master_connection() as con:
        cur = con.execute("SELECT 1 FROM workers WHERE role = 'admin' LIMIT 1")
        return cur.fetchone() is not None


def set_worker_active(worker_id: str, is_active: bool):
    """
    作業者の有効/無効を切り替える。

    production_daily.worker_id や audit_log.worker_id 等、過去実績から
    作業者IDが参照されているため、レコード自体は削除せず is_active フラグの
    切り替えのみで対応する（ログイン画面の一覧は is_active=1 のみ表示）。
    """
    init_workers_table()
    with get_master_connection() as con:
        con.execute(
            "UPDATE workers SET is_active = ? WHERE worker_id = ?",
            (1 if is_active else 0, worker_id),
        )
        con.commit()
