"""
生産実績入力画面（ui/kitting_production_entry.py）の、画面に依存しない計算・判定。GUI に依存しない純粋関数。

リファクタリング段階1で移した。旧位置には re-export か、ここを呼ぶだけの薄いメソッドを残しているので、
既存の呼び出し側はそのまま動く。業務ルールは docs/domain/production_entry.md。
"""
from services.date_parsing import _parse_flexible_date


def _resolve_csv_report_date(raw_value):
    """
    CSV の払い出し日を "YYYY-MM-DD" に正規化する。解釈できなければ None（登録側で実行日を使う）。
    表記ゆれを残すと report_date の文字列範囲検索が壊れるため、必ず正規化する（docs/domain/production_entry.md）。
    """
    parsed = _parse_flexible_date(raw_value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d")


def compute_ng_save_qty(own_qty_by_side, ng_side_plans):
    """
    NG の保存値を計算する（面2の NG は面1にも連動させる）。
      - 面1の保存値 ＝ 面1欄の入力値 ＋ 面2欄の入力値
      - 面2の保存値 ＝ 面2欄の入力値のみ
    合計が0の面は保存しない（既存の NG 申告に触れない）。docs/domain/production_entry.md 参照。
    ng_side_plans は面（"1"/"2"）-> その面の計画（無ければ None）。旧 KittingProductionEntryWindow._compute_ng_save_qty() の本体。
    """
    own_2 = own_qty_by_side.get("2", 0.0)
    save_qty_by_side = {}
    if ng_side_plans.get("1") is not None:
        total_1 = own_qty_by_side.get("1", 0.0) + own_2
        if total_1 > 0:
            save_qty_by_side["1"] = total_1
    if ng_side_plans.get("2") is not None and own_2 > 0:
        save_qty_by_side["2"] = own_2
    return save_qty_by_side


def is_plan_row_completed(row, lot_info, file_no_index, order_qty_index):
    """
    計画一覧の行が「入力済み」か。行の (file_no, 面) の実績合計が、その行の発注数以上なら True。
    lot_info は calculate_lot_completion() の戻り値。面は行の末尾の隠し要素（production_side）。
    旧 apply_plan_filters() 内の is_row_completed() から、キャッシュと DB 呼び出しを除いた判定部分。
    """
    # 末尾の隠し要素（production_side、_fetch_plan_list_rows()参照）。
    file_no = row[file_no_index]
    production_side = row[-1]
    file_actual = lot_info["file_actuals"].get((file_no, production_side), 0)
    order_qty = float(row[order_qty_index])
    return file_actual >= order_qty


def plan_row_tag(classification):
    """
    計画一覧の行に付ける背景色のタグ。面1だけの計画の分類が b（面2待ち）・c（生産面マスター未登録）のときだけ付ける（D-9x）。
    旧 _populate_plan_list_tree()・_refresh_plan_list_for_lot() にあった同じ式。
    """
    tag = (
        "needs_side2_wait" if classification == "b"
        else "side_master_unregistered" if classification == "c"
        else ""
    )
    return tag
