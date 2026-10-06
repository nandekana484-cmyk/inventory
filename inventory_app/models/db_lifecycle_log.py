# models/db_lifecycle_log.py
"""
データベース（月次DBフォルダ）自体の作成・削除・バックアップ呼び出しの履歴を
記録するDBアクセス層（2026-10-06新設）。

models.operation_log（月次DB内の操作履歴、config.DB_PATH切り替えの対象）とは
意図的に別テーブル・別DBにしている。背景：新規作成・削除・バックアップの呼び出し
といった「DBそのものに対する操作」を月次DB側のoperation_logへ記録する既存の
やり方（ui/main_window.py・ui/db_delete_helper.pyの既存のlog_operation()呼び出し）
には、次の2つの問題があった：
  1. 削除したDB自体のoperation_logには記録できない（削除と同時に消えるため）。
  2. 新規作成・バックアップ呼び出しの記録は「その時点で新たに開いたDB」の
     operation_logに書かれるため、後で別のDBへ切り替えると見えなくなる
     （さらにそのDBを削除すると記録自体も失われる）。

この2点を解消するため、どの月次DBを開いているかに依存しない場所（マスタDB、
models.db_common.get_master_connection()）に専用テーブルを新設した。
マスタDBは月次DB切り替えの影響を受けない唯一の永続先であり、D-38・D-42で
確立された「共通マスタ（board_structure_master・parts_attributes・workers・
parts・final_products）は月次DBと無関係に永続化する」という既存方針と同じ
理由（月次DB切り替えに依存しないデータの置き場所）で一致するため、既存方針との
矛盾は無いと判断した。

既存のmodels.operation_log（月次DB側）への記録呼び出しは変更しない
（呼び出し元は、本モジュールへの記録を追加で行うのみ）。

本テーブルは、services.master_merge_service.py（「マスタデータを他PCから
取り込む」、不足分のみ取り込み）の対象5テーブルのハードコードされた一覧に
含めていない（そもそも対象外であり、追加の除外処理は不要）。
"""
from datetime import datetime

from models.db_common import get_master_connection

# operation_type の既知の値（呼び出し元が渡す文字列を本モジュール側では
# 限定しないが、一覧表示側（ui.operation_log_window）で表示用ラベルに
# 変換する際の対応表として、ここに定数としてまとめておく）。
OPERATION_TYPE_CREATE = "create"
OPERATION_TYPE_CREATE_WITH_CARRY_OVER = "create_with_carry_over"
OPERATION_TYPE_DELETE = "delete"
OPERATION_TYPE_RESTORE_FROM_BACKUP = "restore_from_backup"

OPERATION_TYPE_LABELS = {
    OPERATION_TYPE_CREATE: "新規作成",
    OPERATION_TYPE_CREATE_WITH_CARRY_OVER: "新規作成（前月から引き継ぎ）",
    OPERATION_TYPE_DELETE: "削除",
    OPERATION_TYPE_RESTORE_FROM_BACKUP: "バックアップの呼び出し",
}


def init_db_lifecycle_log_table():
    """db_lifecycle_log テーブルの初期化（既存があれば何もしない）。"""
    with get_master_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS db_lifecycle_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                target_db_folder TEXT NOT NULL,
                source_info TEXT,
                worker_name TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.commit()


def record_db_lifecycle_event(operation_type: str, target_db_folder: str,
                                worker_name: str, source_info: str = None):
    """
    DB作成・削除・バックアップ呼び出しの履歴を1件記録する。

    operation_type：OPERATION_TYPE_*のいずれか（本モジュール側では値の
    形式を限定しないが、一覧表示側のラベル変換はOPERATION_TYPE_LABELSに
    無い値をそのまま表示する）。
    target_db_folder：対象の月次DBフォルダ名（例："2026-08"）。削除時は
    削除したフォルダ名、作成時は新規作成したフォルダ名、バックアップ呼び出し
    時は取り込み先の新しいフォルダ名。
    source_info：引き継ぎ元の旧DBフォルダ名（新規作成・引き継ぎあり）、または
    呼び出し元のバックアップファイルパス（バックアップの呼び出し）。
    削除・引き継ぎ無しの新規作成ではNone。
    worker_name：操作した作業者名（呼び出し元がcurrent_worker等から渡す。
    models.operation_log.log_operation()と同じ考え方）。

    既定DB（未選択）に関する特別扱いは行わない：本関数が記録する操作
    （DB新規作成・削除・バックアップ呼び出し）は、いずれも既定DB自体への
    操作ではなく、既定DB以外の月次DBフォルダを対象とする操作のため、
    models.operation_log.log_operation()のis_default_db()チェックに相当する
    ガードは不要と判断した。
    """
    init_db_lifecycle_log_table()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_master_connection() as con:
        con.execute("""
            INSERT INTO db_lifecycle_log (timestamp, operation_type, target_db_folder, source_info, worker_name)
            VALUES (?, ?, ?, ?, ?)
        """, (timestamp, operation_type, target_db_folder, source_info, worker_name))
        con.commit()


def list_db_lifecycle_log(limit: int = None) -> list:
    """
    DB作成・削除・バックアップ呼び出しの履歴を新しい順（log_id降順）で返す。

    limit：省略時（None）は全件、指定時はその件数のみに絞り込む。
    """
    init_db_lifecycle_log_table()
    with get_master_connection() as con:
        if limit is None:
            cur = con.execute("SELECT * FROM db_lifecycle_log ORDER BY log_id DESC")
        else:
            cur = con.execute("SELECT * FROM db_lifecycle_log ORDER BY log_id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cur.fetchall()]
