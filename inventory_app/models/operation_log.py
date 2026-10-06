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

import config
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

    既定DB（未選択、config.is_default_db()）の間は、一切書き込まずそのまま
    戻る（例外にはしない）。「既定DBには一切のファイル読み書きを行わない」
    という方針（D-56・D-57）が、本関数経由の操作履歴記録には適用されて
    いなかった見落とし（D-71）への対応。ログイン画面・作業者登録（新規）
    画面等、DB選択前に到達できる経路を含め、呼び出し元ごとに
    is_default_db()の確認を書かせるのではなく本関数に集約することで、
    新しい呼び出し元が増えるたびに同じ対応を繰り返す必要をなくしている。
    そのため、未選択の間に行った操作（作業者登録等）は操作履歴に残らない。
    """
    if config.is_default_db():
        return
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


# 在庫値出力済みDBの警告（CANONICAL_DESIGN_DECISIONS.md D-5x参照）で使う
# operation_name。ui.inventory_diff_window.InventoryDiffWindowの
# on_export_pdf()/on_export_csv()が成功時にこの名前でlog_operation()を呼ぶ。
OPERATION_NAME_INVENTORY_DIFF_EXPORT = "在庫値出力"


def get_latest_operation_timestamp(operation_name: str):
    """
    指定したoperation_nameの最新の記録時刻（timestamp列、"YYYY-MM-DD HH:MM:SS"
    文字列）を返す。一度も記録が無い場合はNoneを返す。

    init_operation_log_table()（CREATE TABLE IF NOT EXISTS）を呼んでから
    SELECTするため、operation_logテーブル自体が存在しない古いDB（例：
    マスタDB分離・操作履歴機能の導入より前に作成された月次DBを「バックアップの
    呼び出し」で取り込んだ場合）を開いても例外にならない。
    """
    init_operation_log_table()
    with get_connection() as con:
        cur = con.execute(
            "SELECT timestamp FROM operation_log WHERE operation_name = ? ORDER BY id DESC LIMIT 1",
            (operation_name,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def get_inventory_diff_export_status() -> dict:
    """
    現在のDB（config.DB_PATH）が在庫値出力済みかどうかと、最終出力日時を返す。

    戻り値：{"exported": bool, "last_exported_at": str または None}
    """
    last_exported_at = get_latest_operation_timestamp(OPERATION_NAME_INVENTORY_DIFF_EXPORT)
    return {"exported": last_exported_at is not None, "last_exported_at": last_exported_at}
