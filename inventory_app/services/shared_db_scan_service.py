# services/shared_db_scan_service.py
"""
共有フォルダ上の月別DB一覧のスキャン処理。

ui.main_window.MainWindow._load_db_folders()は、ローカルのconfig.APP_DATA_DIR
配下のdb/フォルダに限定して「直下のサブフォルダのうち、inventory.dbが存在する
もの」を列挙している。本モジュールは同じ考え方を、ユーザーが指定した任意の
親ディレクトリ（共有フォルダのUNCパス等）に対しても適用できるようにしたもの
（ui.shared_db_list_window.SharedDbListWindowから使う）。
"""
import os
from datetime import datetime


def scan_shared_db_folders(root_dir: str) -> list:
    """
    root_dir直下の各サブフォルダをスキャンし、"inventory.db"が存在するものだけを
    対象に一覧を返す（_load_db_folders()と同じ「直下1階層のみ、フォルダ名で
    判定」という設計方針。深い階層までの再帰探索は行わない）。

    root_dirが存在しない・ディレクトリでない、またはリスト取得自体に失敗した
    場合（共有フォルダが一時的に利用不可等）は、例外を送出せず空リストを返す。

    戻り値：[{"folder_name", "path"（inventory.db自体のフルパス）,
              "modified_at"（"YYYY-MM-DD HH:MM:SS"文字列、取得失敗時はNone）,
              "size_bytes"（取得失敗時はNone）}, ...]。folder_name昇順。
    """
    if not os.path.isdir(root_dir):
        return []

    try:
        entries = sorted(os.listdir(root_dir))
    except OSError:
        return []

    results = []
    for name in entries:
        db_path = os.path.join(root_dir, name, "inventory.db")
        if not os.path.isfile(db_path):
            continue

        modified_at = None
        size_bytes = None
        try:
            stat_result = os.stat(db_path)
            modified_at = datetime.fromtimestamp(stat_result.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_bytes = stat_result.st_size
        except OSError:
            # 一覧に含めること自体は諦めない（列挙後にファイルが移動・
            # ロックされた等でstat()だけ失敗するケースを想定）。
            pass

        results.append({
            "folder_name": name,
            "path": db_path,
            "modified_at": modified_at,
            "size_bytes": size_bytes,
        })

    return results
