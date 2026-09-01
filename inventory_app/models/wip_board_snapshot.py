# models/wip_board_snapshot.py
"""
仕掛（WIP）基板一覧のスナップショットのDBアクセス層。

月報（ui/monthly_report_window.py）の「仕掛数量抽出」ボタンで、その時点の
月報集計結果（self.report_rows）のうち仕掛数量（surplus_qty）が0より大きい行を
抽出し、後続の「仕掛展開」機能（未実装）の入力データとして保存する。

save_wip_snapshot() は「最後に抽出したデータのみを正とする」スナップショット
方式のため、呼び出しのたびにテーブル全体をクリアしてから渡された行を
全て入れ直す（行単位のキーによる差分更新ではなく、テーブル全体の置き換え）。
"""
import sqlite3

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_wip_board_snapshot_table():
    """wip_board_snapshot テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS wip_board_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT,
                file_no TEXT,
                board_name TEXT,
                production_side TEXT,
                mounting_line TEXT,
                lot_no TEXT,
                surplus_qty REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.commit()


def save_wip_snapshot(rows: list):
    """
    仕掛基板一覧のスナップショットを保存する（テーブル全体差し替え）。

    rows: [{"kitting_list_no", "file_no", "board_name", "production_side",
             "mounting_line", "lot_no", "surplus_qty"}, ...]

    テーブル全体をDELETEしてから rows を全てINSERTし直す（「最後に抽出した
    データを正とする」スナップショット方式のため、行単位のキーによる
    delete-then-insertではなく、テーブル全体の置き換えとした。月報の集計対象
    期間は実行のたびに変わり得るため、前回の抽出時にのみ存在した行を残さない
    ため）。1コネクション・1トランザクションで実行するため、途中で例外が
    発生した場合は with 文により自動的にロールバックされ、空になったまま
    残ることはない。

    戻り値：保存した件数。
    """
    init_wip_board_snapshot_table()
    with get_connection() as con:
        con.execute("DELETE FROM wip_board_snapshot")
        con.executemany("""
            INSERT INTO wip_board_snapshot (
                kitting_list_no, file_no, board_name, production_side,
                mounting_line, lot_no, surplus_qty
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                row.get("kitting_list_no"),
                row.get("file_no"),
                row.get("board_name"),
                row.get("production_side"),
                row.get("mounting_line"),
                row.get("lot_no"),
                row["surplus_qty"],
            )
            for row in rows
        ])
        con.commit()

    return len(rows)


def list_wip_snapshot() -> list:
    """仕掛基板スナップショットの全件を取得する（仕掛展開画面用）。"""
    init_wip_board_snapshot_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT kitting_list_no, file_no, board_name, production_side,
                   mounting_line, lot_no, surplus_qty, created_at
            FROM wip_board_snapshot
            ORDER BY id
        """)
        return [dict(row) for row in cur.fetchall()]
