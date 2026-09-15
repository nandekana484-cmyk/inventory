# services/db_delete_service.py
"""
月別DB削除処理。ui/main_window.py（ローカルDB一覧）・ui/shared_db_list_window.py
（共有フォルダDB一覧）の両方から、ui/db_delete_helper.py経由で共通利用する。

ダイアログ表示・最終的な削除可否のユーザー確認自体はUI層
（ui/db_delete_helper.py）の責務とし、本モジュールは「削除してよいかの
機械的な判定材料」の提供と「実際のファイル削除」のみを担う。
"""
import os

import config
from services.db_lock_service import get_active_lock_info

# DBファイルと同じフォルダにある、DB本体に付随する関連ファイル。削除時に
# DB本体と合わせて削除する（残すと次回acquire_lock()が古いロックファイルを
# 踏むことになる、またはos.replace()前の一時ファイルがゴミとして残り続ける
# ため。services/db_lock_service.pyの_lock_path()・_write_lock_atomic()参照）。
_RELATED_SUFFIXES = (".lock", ".lock.tmp")


def is_current_database(db_path: str) -> bool:
    """削除対象が、現在このプロセスが接続中のDB（config.DB_PATH）と同一かどうか。"""
    return os.path.abspath(db_path) == os.path.abspath(config.DB_PATH)


def check_delete_safety(db_path: str) -> dict:
    """
    削除前の安全確認結果をまとめて返す。呼び出し元（UI層）はこの結果に応じて
    表示するダイアログの強さを切り替える（ui/db_delete_helper.py参照）。

    戻り値：{
        "is_current": bool,        # 現在接続中のDBと同一か（Trueなら削除を拒否すべき）
        "active_lock": dict|None,  # 有効なロック情報（get_active_lock_info()）
    }
    """
    return {
        "is_current": is_current_database(db_path),
        "active_lock": get_active_lock_info(db_path),
    }


def delete_database_files(db_path: str) -> list:
    """
    DBファイル本体、および同じフォルダにある関連ファイル（.lock・.lock.tmp）を
    削除する。DBファイルが格納されているフォルダ自体は削除しない（無関係な
    ファイルまで誤って巻き込むリスクを避けるため、DB関連ファイルのみを対象に
    する安全側の設計）。

    各ファイルの削除は個別にtry/exceptで保護し、存在しない・削除に失敗した
    ファイルがあっても他のファイルの削除は継続する（他プロセスが既に一部だけ
    削除していた等のケースを想定）。

    戻り値：実際に削除できたファイルパスのリスト。
    """
    deleted = []
    candidates = [db_path] + [db_path + suffix for suffix in _RELATED_SUFFIXES]
    for path in candidates:
        try:
            os.remove(path)
            deleted.append(path)
        except OSError:
            pass
    return deleted
