# services/production_service.py
from datetime import datetime
from models.kitting_plan import find_plan_item_by_kitting_no, list_plan_items_by_lot
from models.production import (
    insert_daily_production,
    get_app_cumulative_qty,
    list_daily_production_by_kitting_no,
    update_daily_production,
    delete_daily_production,
    list_daily_production_today,
    list_daily_production_range,
)


def search_plan_by_kitting_no(kitting_list_no: str):
    """
    キッティングリストNo.から計画情報とアプリ内累計を取得する。
    UI表示用の辞書を返す。

    list_active_plan_items() のフィルタ（完了済み・1回目除外等）は適用しない。
    find_plan_item_by_kitting_no() は計画テーブルの該当行をそのまま返すため、
    完了済み・計画一覧には出ない計画も検索対象に含まれる。
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no)
    if not plan:
        return None

    app_cumulative = get_app_cumulative_qty(kitting_list_no)

    result = {
        "plan_item_id": plan["plan_item_id"],
        "kitting_list_no": plan["kitting_list_no"],
        "lot_no": plan["lot_no"],
        "setup_file_no": plan["setup_file_no"],
        "board_name": plan["board_name"],
        "production_side": plan["production_side"],
        "planned_qty": plan["planned_qty"],
        "order_qty": plan["order_qty"],
        "cumulative_qty_external": plan["cumulative_qty_external"],
        "app_cumulative_qty": app_cumulative,
    }

    lot_no = plan["lot_no"]
    lot_info = calculate_lot_completion(lot_no)
    result["lot_completed_quantity"] = lot_info["completed_quantity"]
    result["lot_remaining_quantity"] = lot_info["remaining_quantity"]
    result["lot_file_actuals"] = lot_info["file_actuals"]
    result["lot_surplus"] = lot_info["surplus"]

    return result


def register_daily_result(kitting_list_no: str, daily_qty: float, worker_id: str,
                            report_date: str = None):
    """
    当日実績を1件追加登録する。
    戻り値：更新後のアプリ内累計
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no)
    if not plan:
        raise ValueError(f"キッティングリストNo. {kitting_list_no} の計画が見つかりません。")

    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")

    insert_daily_production(
        plan_item_id=plan["plan_item_id"],
        kitting_list_no=kitting_list_no,
        lot_id=plan["lot_no"],
        group_id=plan["board_name"],
        report_date=report_date,
        daily_qty=daily_qty,
        worker_id=worker_id,
    )

    return get_app_cumulative_qty(kitting_list_no)


def get_daily_history(kitting_list_no: str):
    return list_daily_production_by_kitting_no(kitting_list_no)


def update_daily_result(prod_log_id: int, daily_qty: float):
    """
    実績1件（prod_log_id指定）のdaily_qtyを修正する。
    """
    update_daily_production(prod_log_id, daily_qty)


def delete_daily_result(prod_log_id: int):
    """
    実績1件（prod_log_id指定）を削除する。
    """
    delete_daily_production(prod_log_id)


def _build_report_rows(records):
    """
    production_daily のレコード群から、日報・月報共通の表示データを構築する。
    kitting_list_no をキーに計画情報（setup_file_no / board_name / lot_no / order_qty）と
    突き合わせ、通し番号・生産数・累計数を付与した辞書のリストを返す。

    さらに、同一 lot_no に属する実績（daily_qty）の最小値を「引落数量」とし、
    各行の「仕掛数量」（daily_qty - 引落数量）・「未完了数」（order_qty - 引落数量）を付与する。
    """
    enriched = []
    for rec in records:
        kitting_list_no = rec["kitting_list_no"]
        plan = find_plan_item_by_kitting_no(kitting_list_no)
        enriched.append({
            "kitting_list_no": kitting_list_no,
            "plan": plan,
            "daily_qty": rec["daily_qty"],
            "lot_no": plan["lot_no"] if plan else "",
        })

    lot_completed = {}
    for item in enriched:
        lot_no = item["lot_no"]
        daily_qty = item["daily_qty"]
        if lot_no not in lot_completed or daily_qty < lot_completed[lot_no]:
            lot_completed[lot_no] = daily_qty

    report_rows = []
    for i, item in enumerate(enriched, start=1):
        plan = item["plan"]
        kitting_list_no = item["kitting_list_no"]
        lot_no = item["lot_no"]
        daily_qty = item["daily_qty"]
        order_qty = plan["order_qty"] if plan else 0
        completed = lot_completed.get(lot_no, 0)

        report_rows.append({
            "seq": i,
            "file_no": plan["setup_file_no"] if plan else "",
            "board_name": plan["board_name"] if plan else "",
            "lot_no": lot_no,
            "daily_qty": daily_qty,
            "app_cumulative_qty": get_app_cumulative_qty(kitting_list_no),
            "order_qty": order_qty,
            "lot_completed": completed,
            "surplus_qty": daily_qty - completed,
            "lot_remaining": order_qty - completed,
        })

    return report_rows


def build_daily_report():
    """
    本日（report_date = 今日）入力された実績を元に、日報表示用のデータを構築する。
    """
    records = list_daily_production_today()
    return _build_report_rows(records)


def build_monthly_report(from_date: str, to_date: str):
    """
    指定期間（report_date が from_date～to_date、両端含む）の実績を元に、
    月報表示用のデータを構築する。列構成・集計ロジックは日報（build_daily_report）と共通。
    """
    records = list_daily_production_range(from_date, to_date)
    return _build_report_rows(records)


def calculate_lot_completion(lot_no: str):
    """
    lot_no 単位でロット完成数・未完成数・余剰基板を算出する。
    完成数は、同一 lot_no に属する各 setup_file_no（kitting_list_no）の
    実績累計（daily_qty の SUM）のうち最小値とする。
    """
    plan_items = list_plan_items_by_lot(lot_no)
    if not plan_items:
        raise ValueError(f"ロットNo. {lot_no} の計画が見つかりません。")

    order_qty = plan_items[0]["order_qty"]

    file_actuals = {}
    for item in plan_items:
        file_no = item["setup_file_no"]
        kitting_list_no = item["kitting_list_no"]
        file_actuals[file_no] = get_app_cumulative_qty(kitting_list_no)

    completed = min(file_actuals.values())
    remaining = order_qty - completed

    surplus = {
        file_no: actual_qty - completed
        for file_no, actual_qty in file_actuals.items()
    }

    return {
        "lot_no": lot_no,
        "order_quantity": order_qty,
        "completed_quantity": completed,
        "remaining_quantity": remaining,
        "file_actuals": file_actuals,
        "surplus": surplus,
    }