# models/kitting_plan_batches.py
"""
古い計画（D-192・D-193）の判定結果を、月次DB のビュー v_plan_stale（models/plan_stale_view.py）から読む。
判定の決まりはビューの1か所だけ。ここは読むだけで、計画の is_active・delete_flag は変えない。
ビューが無い DB（古い月の DB を、この版のアプリで一度も開いていないなど）は「古い計画なし」として扱う。
"""
import sqlite3

from models.db_common import get_connection

_MISSING_VIEW = "no such table: v_plan_stale"


def _read(sql: str):
    """ビューを読む。ビューが無い DB は None（ほかのエラーはそのまま送出する）。"""
    try:
        with get_connection() as con:
            return con.execute(sql).fetchall()
    except sqlite3.OperationalError as e:
        if _MISSING_VIEW in str(e):
            return None
        raise


def fetch_stale_plan_keys() -> set:
    """
    画面の「古い計画を隠す」で隠す計画の {(キッティングNo., ロットNo.（空は ''）)}。
    ビューの is_stale = 1 かつ has_legacy_actuals = 0（実績の入っている計画は隠さない）。一覧を作るときに1回だけ呼ぶこと。
    """
    rows = _read("SELECT kitting_list_no, lot_no FROM v_plan_stale WHERE is_stale = 1 AND has_legacy_actuals = 0")
    return {(row[0], row[1]) for row in rows or []}


def count_stale_plans() -> int:
    """古い計画と判定された計画の数（ビューの is_stale = 1。実績の有無は問わない）。計画CSVの取り込みの完了メッセージに出す。"""
    rows = _read("SELECT COUNT(*) FROM v_plan_stale WHERE is_stale = 1")
    return rows[0][0] if rows else 0
