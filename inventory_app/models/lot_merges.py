# models/lot_merges.py
"""
月次DB の lot_merges（ロット統合、D-145）。1行が「統合されるロット（member_lot、例 123456-）→ 統合先（target_lot、例 123456）」。
表は db/schema.sql（新しく作る DB）とマイグレーション 014（既存の DB）で作る。ここでは作らない。
「member_lot は target_lot の末尾に - を1つ付けたもの」は、表の CHECK に加えて、書き込む関数でも確かめる。
"""
from datetime import datetime

from models.db_common import get_connection, table_exists
from models.operation_log import init_operation_log_table, insert_operation_log

MERGE_SUFFIX = "-"


def is_valid_merge_pair(member_lot, target_lot) -> bool:
    """統合できる組の形か（D-136：末尾の「-」1つだけ。「-1」「K」「.1」は統合しない）。計画があるかは見ない。"""
    if not isinstance(member_lot, str) or not isinstance(target_lot, str):
        return False
    if not target_lot or target_lot != target_lot.strip() or target_lot.endswith(MERGE_SUFFIX):
        return False
    return member_lot == target_lot + MERGE_SUFFIX


def list_lot_merges() -> list:
    """統合の行をすべて返す（target_lot・member_lot の順）。表が無い DB では空のリストを返し、表を作らない。"""
    with get_connection() as con:
        if not table_exists(con, "lot_merges"):
            return []
        rows = con.execute(
            "SELECT member_lot, target_lot, merged_at, merged_by, note FROM lot_merges ORDER BY target_lot, member_lot"
        ).fetchall()
        return [dict(r) for r in rows]


def _check_pair(member_lot, target_lot):
    if not is_valid_merge_pair(member_lot, target_lot):
        raise ValueError(f"統合できる組ではありません（{member_lot} → {target_lot}）。統合されるロットは、統合先の末尾に「-」を1つ付けたものだけです。")


def insert_lot_merge(member_lot: str, target_lot: str, merged_by: str, note: str = None, operation: tuple = None) -> dict:
    """
    統合の行を1件書く。operation=(作業者名, 操作名, 詳細) を渡すと、操作履歴も同じトランザクションで書く（どちらかが失敗すれば両方とも書かない）。
    すでに統合済みの member_lot なら sqlite3.IntegrityError。戻り値は書いた行。
    """
    _check_pair(member_lot, target_lot)
    if operation is not None:
        init_operation_log_table()
    row = {
        "member_lot": member_lot, "target_lot": target_lot,
        "merged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "merged_by": merged_by, "note": note,
    }
    with get_connection() as con:
        con.execute(
            "INSERT INTO lot_merges (member_lot, target_lot, merged_at, merged_by, note) VALUES (?, ?, ?, ?, ?)",
            (row["member_lot"], row["target_lot"], row["merged_at"], row["merged_by"], row["note"]),
        )
        if operation is not None:
            insert_operation_log(con, *operation)
        con.commit()
    return row


def delete_lot_merge(member_lot: str, operation: tuple = None):
    """統合の行を1件消す（解除）。消した行を返す。無ければ何も書かずに None。operation は insert_lot_merge() と同じ。"""
    if operation is not None:
        init_operation_log_table()
    with get_connection() as con:
        found = con.execute(
            "SELECT member_lot, target_lot, merged_at, merged_by, note FROM lot_merges WHERE member_lot = ?", (member_lot,)
        ).fetchone()
        if found is None:
            return None
        con.execute("DELETE FROM lot_merges WHERE member_lot = ?", (member_lot,))
        if operation is not None:
            insert_operation_log(con, *operation)
        con.commit()
        return dict(found)


def copy_lot_merge_rows(rows: list) -> int:
    """
    別の DB から読んだ統合の行を、今の DB にそのまま（統合した日時・人も）写す（前月からの引き継ぎ用）。
    同じ member_lot の行がすでにあれば写さない（引き継ぎのやり直しで二重にしない）。戻り値は写した件数。
    """
    for r in rows:
        _check_pair(r["member_lot"], r["target_lot"])
    copied = 0
    with get_connection() as con:
        for r in rows:
            cur = con.execute(
                "INSERT OR IGNORE INTO lot_merges (member_lot, target_lot, merged_at, merged_by, note) VALUES (?, ?, ?, ?, ?)",
                (r["member_lot"], r["target_lot"], r["merged_at"], r.get("merged_by"), r.get("note")),
            )
            copied += cur.rowcount
        con.commit()
    return copied
