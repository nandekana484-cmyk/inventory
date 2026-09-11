# models/wip_exclusion_list.py
"""
仕掛一覧（ui/wip_expansion_window.py）の行を「対象外」としてマークするための
DBアクセス層。models/ng_exclusion_list.pyと同じパターンで実装している。

識別キーは (kitting_list_no, lot_no, file_no, production_side) の組み合わせ。
models.wip_board_snapshot（月報の「仕掛数量抽出」で都度全件差し替えされる
スナップショット）には、行を一意に識別できる安定したキー（主キーやID）が
存在しないため、以前の調査で確認済みの通り、この4項目の組み合わせを
識別キーとして採用する。
"""
from models.db_common import get_connection


def init_wip_exclusion_list_table():
    """wip_exclusion_list テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS wip_exclusion_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                lot_no TEXT,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                reason TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.commit()


def mark_wip_excluded(kitting_list_no: str, lot_no: str, file_no: str, side: int,
                        reason: str = None, created_by: str = None):
    """
    指定の行を「対象外」として登録する。既存の登録（同一kitting_list_no・
    lot_no・file_no・production_side）があれば、delete-then-insertで上書きする
    （models.ng_exclusion_list.mark_ng_excluded()と同じ設計）。
    """
    init_wip_exclusion_list_table()
    with get_connection() as con:
        con.execute(
            "DELETE FROM wip_exclusion_list WHERE kitting_list_no = ? AND file_no = ? "
            "AND production_side = ? AND COALESCE(lot_no, '') = COALESCE(?, '')",
            (kitting_list_no, file_no, side, lot_no),
        )
        con.execute("""
            INSERT INTO wip_exclusion_list (kitting_list_no, lot_no, file_no, production_side, reason, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (kitting_list_no, lot_no, file_no, side, reason, created_by))
        con.commit()


def unmark_wip_excluded(kitting_list_no: str, lot_no: str, file_no: str, side: int):
    """指定の行の「対象外」指定を解除する（登録が無ければ何もしない）。"""
    init_wip_exclusion_list_table()
    with get_connection() as con:
        con.execute(
            "DELETE FROM wip_exclusion_list WHERE kitting_list_no = ? AND file_no = ? "
            "AND production_side = ? AND COALESCE(lot_no, '') = COALESCE(?, '')",
            (kitting_list_no, file_no, side, lot_no),
        )
        con.commit()


def is_wip_excluded(kitting_list_no: str, lot_no: str, file_no: str, side: int) -> bool:
    """指定の行が現在「対象外」として登録されているか判定する。"""
    init_wip_exclusion_list_table()
    with get_connection() as con:
        cur = con.execute(
            "SELECT 1 FROM wip_exclusion_list WHERE kitting_list_no = ? AND file_no = ? "
            "AND production_side = ? AND COALESCE(lot_no, '') = COALESCE(?, '') LIMIT 1",
            (kitting_list_no, file_no, side, lot_no),
        )
        return cur.fetchone() is not None


def list_wip_exclusions() -> list:
    """
    「対象外」として登録されている全行を取得する（仕掛一覧での突き合わせ用）。

    戻り値：[{"kitting_list_no", "lot_no", "file_no", "production_side", "reason",
              "created_by", "created_at"}, ...]
    """
    init_wip_exclusion_list_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT kitting_list_no, lot_no, file_no, production_side, reason, created_by, created_at
            FROM wip_exclusion_list
        """)
        return [dict(row) for row in cur.fetchall()]
