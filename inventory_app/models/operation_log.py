# models/operation_log.py
"""
月次DB内の大まかな操作履歴（監査ログとまではいかない簡易な操作記録）のDBアクセス層。

「誰が（worker_name・pc_name）・いつ（timestamp）・何をしたか（operation_name・
detail）」を1行1操作の粒度で記録する。models.db_lock_service（同じくworker_name・
pc_nameで「誰が」を識別する既存の仕組み）と同じ考え方で、pc_nameは
socket.gethostname()により本モジュール内で自動取得する（呼び出し元に渡させない）。

timestamp列とcreated_at列の使い分け：timestampはlog_operation()が呼ばれた時点の
アプリケーション側の日時（"YYYY-MM-DD HH:MM:SS"文字列、業務上の記録時刻）、
created_atはDB挿入時にSQLite側が自動付与する日時（models.ng_exclusion_list等、
既存の多くのテーブルと同じ`datetime('now','localtime')`デフォルト）。通常は
ほぼ同時刻になるが、他テーブルとの設計の一貫性のため両方を持たせている。

月次DB（config.DB_PATH切り替えの対象）に同居するテーブルのため、DBを切り替える
たびに履歴も切り替わる（月をまたいだ通算履歴にはならない）。

本モジュールは記録・一覧取得のみを提供する。個々の操作箇所（NG登録・実績登録等）
への実際の呼び出し組み込みは次のステップで行う。
"""
import socket
from datetime import datetime

from models.db_common import get_connection


def init_operation_log_table():
    """operation_log テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS operation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                worker_name TEXT NOT NULL,
                pc_name TEXT NOT NULL,
                operation_name TEXT NOT NULL,
                detail TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.commit()


def log_operation(worker_name: str, operation_name: str, detail: str = None):
    """
    操作履歴を1件記録する。

    worker_name：操作した作業者名（呼び出し元がcurrent_worker等から渡す）。
    operation_name：操作内容を表す短い文字列（例："NG登録"「実績修正」等、
    呼び出し元が決める。本モジュール側では値の形式を限定しない）。
    detail：任意の補足文字列（例：対象のkitting_list_no等）。省略時はNULL。
    pc_name：呼び出し元には渡させず、models.db_lock_serviceと同じ
    socket.gethostname()で本関数が自動取得する。
    """
    init_operation_log_table()
    pc_name = socket.gethostname()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as con:
        con.execute("""
            INSERT INTO operation_log (timestamp, worker_name, pc_name, operation_name, detail)
            VALUES (?, ?, ?, ?, ?)
        """, (timestamp, worker_name, pc_name, operation_name, detail))
        con.commit()


def list_operation_log(limit: int = None) -> list:
    """
    操作履歴を新しい順（id降順。オートインクリメントのため、同一秒内に複数件
    記録された場合でもtimestamp文字列の同値比較に頼らず挿入順を保証できる）で
    返す。

    limit：省略時（None）は全件、指定時はその件数のみに絞り込む。
    """
    init_operation_log_table()
    with get_connection() as con:
        if limit is None:
            cur = con.execute("SELECT * FROM operation_log ORDER BY id DESC")
        else:
            cur = con.execute("SELECT * FROM operation_log ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cur.fetchall()]
