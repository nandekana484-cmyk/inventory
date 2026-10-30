# models/kitting_plan_batches.py
"""
計画CSVの「最新の取り込み」の中身を返す（生産実績入力の計画一覧の「古い計画を隠す」用、D-192）。
kitting_plan.py が大きいので、別のファイルにした。表示だけに使い、計画の is_active・delete_flag は変えない。
"""
from models.db_common import get_connection

CARRY_OVER_PREFIX = "carry_over:"


def fetch_latest_csv_batch_scope():
    """
    最新の計画CSVの取り込みの {"plan_batch_id", "keys", "datetimes"} を返す。無ければ None。
    最新の取り込み: バッチの delete_flag が 1 でなく、source_file が carry_over: で始まらない（前月引き継ぎは数えない）もののうち、
    imported_at が最新のもの。同じ時刻なら plan_batch_id が大きい方。
    keys: その取り込みの行の {(キッティングNo., ロットNo.（空は ''）)}。
    datetimes: その取り込みのすべての行の [(計画開始日, 計画終了日)]（生の値）。明細の delete_flag が 1 の行も含める。
    日時の解釈と期間の計算は services で行う（形式が混ざっていても最小・最大を取り違えないため）。
    表が無い DB（新しく作ったばかりなど）は None。
    """
    with get_connection() as con:
        batch_columns = {row[1] for row in con.execute("PRAGMA table_info(kitting_plan_batches)").fetchall()}
        item_columns = {row[1] for row in con.execute("PRAGMA table_info(kitting_plan_items)").fetchall()}
        if not batch_columns or not item_columns:
            return None

        conditions = ["substr(source_file, 1, ?) <> ?"]
        if "delete_flag" in batch_columns:
            conditions.append("COALESCE(delete_flag, 0) = 0")
        batch = con.execute(
            "SELECT plan_batch_id FROM kitting_plan_batches WHERE " + " AND ".join(conditions)
            + " ORDER BY imported_at DESC, plan_batch_id DESC LIMIT 1",
            (len(CARRY_OVER_PREFIX), CARRY_OVER_PREFIX),
        ).fetchone()
        if batch is None:
            return None

        keys = set()
        datetimes = []
        for row in con.execute(
            "SELECT kitting_list_no, COALESCE(lot_no, ''), plan_start_datetime, plan_end_datetime "
            "FROM kitting_plan_items WHERE plan_batch_id = ?",
            (batch[0],),
        ):
            keys.add((row[0], row[1]))
            datetimes.append((row[2], row[3]))
        return {"plan_batch_id": batch[0], "keys": keys, "datetimes": datetimes}
