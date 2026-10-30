# models/kitting_plan_setup_pairs.py
"""
kitting_plan_items から、(ファイルNo., 実装ライン) の組を集める（基板丁数の対応表の取得用、D-191）。
kitting_plan.py が大きいので、別のファイルにした。呼び出し元が開いた接続を受け取る（読み取り専用の接続でよい）。
"""
from models.production_side_master import normalize_mounting_line, normalize_setup_file_no


def _columns(con, table: str) -> set:
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def has_plan_items_table(con) -> bool:
    """kitting_plan_items がある DB か（古い月次DB には無いことがある）。"""
    return bool(_columns(con, "kitting_plan_items"))


def count_active_plan_rows_by_pair(con) -> dict:
    """
    対象の計画の {(正規化したファイルNo., 正規化した実装ライン): 計画の行数}。
    対象の計画は COALESCE(is_active, 1) = 1 AND COALESCE(delete_flag, 0) = 0（完了済みのロットも含む）。
    delete_flag の列が無い古い版の DB では is_active だけで、is_active の列も無ければ全行を数える。
    kitting_plan_items が無い DB は空の辞書。どちらかが空になる組（ファイルNo.・実装ラインが空欄の計画）は含めない。
    """
    columns = _columns(con, "kitting_plan_items")
    if not columns:
        return {}

    conditions = []
    if "is_active" in columns:
        conditions.append("COALESCE(is_active, 1) = 1")
    if "delete_flag" in columns:
        conditions.append("COALESCE(delete_flag, 0) = 0")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    counts = {}
    for row in con.execute(
        f"SELECT setup_file_no, mounting_line, COUNT(*) FROM kitting_plan_items{where} GROUP BY setup_file_no, mounting_line"
    ):
        pair = (normalize_setup_file_no(row[0]), normalize_mounting_line(row[1]))
        if not pair[0] or not pair[1]:
            continue
        counts[pair] = counts.get(pair, 0) + row[2]
    return counts


def list_active_setup_pairs(con) -> list:
    """対象の計画の (正規化したファイルNo., 正規化した実装ライン) を、重複なく並べて返す（count_active_plan_rows_by_pair() と同じ条件）。"""
    return sorted(count_active_plan_rows_by_pair(con))
