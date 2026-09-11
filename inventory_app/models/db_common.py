# models/db_common.py
"""
DBアクセス層共通のsqlite3接続ヘルパー。

各modelモジュールが個別に持っていたget_connection()（処理内容は全モジュールで
完全に同一だった）をここに集約した。

timeoutについて：sqlite3のデフォルトタイムアウトは5秒だが、共有フォルダ（SMB）
運用では他PC・他プロセスが同じDBファイルに対して短時間ロックを保持する場面が
起こり得るため、5秒では「database is locked」エラーになりやすい。30秒に延長し、
一定時間はリトライ（ポーリング）してからエラーにする猶予を持たせる。
"""
import sqlite3

import config

# 共有フォルダ上でのロック競合を考慮した接続タイムアウト（秒）。
# sqlite3のデフォルト5.0秒では、他PC・他プロセスの短時間ロックと重なった際に
# 即座に"database is locked"エラーになりやすいため延長した。
CONNECTION_TIMEOUT_SECONDS = 30.0


def get_connection():
    con = sqlite3.connect(config.DB_PATH, timeout=CONNECTION_TIMEOUT_SECONDS)
    con.row_factory = sqlite3.Row
    return con
