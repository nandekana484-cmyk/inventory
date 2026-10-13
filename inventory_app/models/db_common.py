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
import os
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


def get_master_connection():
    """
    マスタDB（config.MASTER_DB_PATH。board_structure_master・parts_attributes・
    workers・parts・final_products）専用の接続。

    月次DB用のget_connection()とは意図的に別関数にしている（呼び出し側から見て
    どちらのDBに繋ぐかが関数名だけで明確になるようにするため）。

    月次DB（DB_PATH）はinit_database_at()等のDB新規作成経路で事前にフォルダが
    作成される前提だが、マスタDBにはそのような専用の新規作成経路が無く、各
    modelモジュールのinit_xxx_table()からの遅延初期化のみで作成されるため、
    フォルダが無ければここで作成する。
    """
    os.makedirs(os.path.dirname(config.MASTER_DB_PATH), exist_ok=True)
    con = sqlite3.connect(config.MASTER_DB_PATH, timeout=CONNECTION_TIMEOUT_SECONDS)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------- DB ファイル全体の検査・汎用の読み取り
# 段階4で services（backup_service・master_merge_service）から SQL を移した。特定のテーブルに属さないのでここに置く。
# どれも呼び出し元が開いた接続 con を受け取る（バックアップファイルなど、config.DB_PATH 以外の DB にも使うため）。

def run_integrity_check(con):
    """PRAGMA integrity_check の最初の行を返す（正常なら ("ok",)）。DB でなければ sqlite3.DatabaseError を送出する。"""
    cur = con.execute("PRAGMA integrity_check")
    return cur.fetchone()


def list_table_names(con) -> set:
    """con の DB にあるテーブル名の集合。"""
    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def table_exists(con, table_name: str) -> bool:
    cur = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table_name,),
    )
    return cur.fetchone() is not None


def select_key_column(con, table: str, key_column: str) -> list:
    """table の key_column 列を全行読む。table・key_column は呼び出し元の定義済みの名前だけを渡すこと（SQL に埋め込むため）。"""
    return con.execute(f"SELECT {key_column} FROM {table}").fetchall()


def select_all_rows(con, table: str) -> list:
    """table の全行・全列を読む。table は呼び出し元の定義済みの名前だけを渡すこと（SQL に埋め込むため）。"""
    return con.execute(f"SELECT * FROM {table}").fetchall()
