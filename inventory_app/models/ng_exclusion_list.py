# models/ng_exclusion_list.py
"""
NG一覧（ui/ng_input_window.py）の行を「対象外」としてマークするためのDBアクセス層。

業務上NGとして扱う必要がない（例：申告ミス、対応不要と判断された等）行を、
実績（scrap_records）やNG申告（ng_declarations）のデータ自体は変更せずに
「対象外」として記録し、NG一覧上で区別できるようにする。

識別キーは (kitting_list_no, production_side, lot_no) の組み合わせ。
実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースが
478件確認されているため、models/ng_declarations.py・models/scrap_records.py と
同様にlot_noも識別キーに含める（計画外＝lot_no=NoneはNULLのまま扱う）。
"""
from models.db_common import get_connection


def init_ng_exclusion_list_table():
    """ng_exclusion_list テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS ng_exclusion_list (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                lot_no TEXT,
                reason TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.commit()


def mark_ng_excluded(kitting_list_no: str, side: int, lot_no: str = None,
                       reason: str = None, created_by: str = None):
    """
    指定の行を「対象外」として登録する。既存の登録（同一kitting_list_no・
    production_side・lot_no）があれば、delete-then-insertで上書きする
    （models.ng_declarations.save_ng_declaration()と同じ設計。理由・登録者を
    更新したい場合に再度呼べば良いだけにするため、個別のUPDATE文は用意しない）。
    """
    init_ng_exclusion_list_table()
    with get_connection() as con:
        con.execute(
            "DELETE FROM ng_exclusion_list WHERE kitting_list_no = ? AND production_side = ? "
            "AND COALESCE(lot_no, '') = COALESCE(?, '')",
            (kitting_list_no, side, lot_no),
        )
        con.execute("""
            INSERT INTO ng_exclusion_list (kitting_list_no, production_side, lot_no, reason, created_by)
            VALUES (?, ?, ?, ?, ?)
        """, (kitting_list_no, side, lot_no, reason, created_by))
        con.commit()


def unmark_ng_excluded(kitting_list_no: str, side: int, lot_no: str = None):
    """指定の行の「対象外」指定を解除する（登録が無ければ何もしない）。"""
    init_ng_exclusion_list_table()
    with get_connection() as con:
        con.execute(
            "DELETE FROM ng_exclusion_list WHERE kitting_list_no = ? AND production_side = ? "
            "AND COALESCE(lot_no, '') = COALESCE(?, '')",
            (kitting_list_no, side, lot_no),
        )
        con.commit()


def is_ng_excluded(kitting_list_no: str, side: int, lot_no: str = None) -> bool:
    """指定の行が現在「対象外」として登録されているか判定する。"""
    init_ng_exclusion_list_table()
    with get_connection() as con:
        cur = con.execute(
            "SELECT 1 FROM ng_exclusion_list WHERE kitting_list_no = ? AND production_side = ? "
            "AND COALESCE(lot_no, '') = COALESCE(?, '') LIMIT 1",
            (kitting_list_no, side, lot_no),
        )
        return cur.fetchone() is not None


def list_ng_exclusions() -> list:
    """
    「対象外」として登録されている全行を取得する（NG一覧での突き合わせ用）。

    戻り値：[{"kitting_list_no", "production_side", "lot_no", "reason",
              "created_by", "created_at"}, ...]
    """
    init_ng_exclusion_list_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT kitting_list_no, production_side, lot_no, reason, created_by, created_at
            FROM ng_exclusion_list
        """)
        return [dict(row) for row in cur.fetchall()]
