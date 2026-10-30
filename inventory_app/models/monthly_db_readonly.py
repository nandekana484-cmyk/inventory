# models/monthly_db_readonly.py
"""
月次DB を読み取り専用で開く（基板丁数の対応表の取得用、D-191）。

config.DB_PATH の切り替え・形の確認・マイグレーション・ロックファイルの操作は、ここではしない。
開いている間も、その DB を使っている画面（書き込み側）を止めない（ジャーナルモードは delete のままで、読み取りは共有ロックだけ）。
"""
import os
import sqlite3
from pathlib import Path

# 読み取り側が待つ秒数。書き込み中の DB でも長く待たない（取得は次の機会にやり直せる）。
READONLY_TIMEOUT_SECONDS = 5.0


def open_monthly_db_readonly(db_path: str) -> sqlite3.Connection:
    """
    db_path を file:...?mode=ro の URI で開き、PRAGMA query_only = ON にして返す。
    ファイルが無ければ、作らずに sqlite3.OperationalError を送出する（mode=ro は新規作成しない）。
    """
    uri = Path(os.path.abspath(db_path)).as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=READONLY_TIMEOUT_SECONDS)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con
