# services/production_service.py
from datetime import datetime
from models.kitting_plan import find_plan_item_by_kitting_no
from models.production import (
    insert_daily_production,
    get_app_cumulative_qty,
    list_daily_production_by_kitting_no,
)


def search_plan_by_kitting_no(kitting_list_no: str):
    """
    キッティングリストNo.から計画情報とアプリ内累計を取得する。
    UI表示用の辞書を返す。
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no)
    if not plan:
        return None

    app_cumulative = get_app_cumulative_qty(kitting_list_no)

    return {
        "plan_item_id": plan["plan_item_id"],
        "kitting_list_no": plan["kitting_list_no"],
        "lot_no": plan["lot_no"],
        "setup_file_no": plan["setup_file_no"],
        "board_name": plan["board_name"],
        "production_side": plan["production_side"],
        "planned_qty": plan["planned_qty"],
        "cumulative_qty_external": plan["cumulative_qty_external"],
        "app_cumulative_qty": app_cumulative,
    }


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