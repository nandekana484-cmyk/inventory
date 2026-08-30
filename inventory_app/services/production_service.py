# services/production_service.py
from datetime import datetime
from models.kitting_plan import (
    find_plan_item_by_kitting_no,
    list_plan_items_by_lot,
    list_active_plan_items_by_kitting_no,
    find_opposite_side_plan,
)
from models.production import (
    insert_daily_production,
    replace_daily_result,
    get_app_cumulative_qty,
    get_app_cumulative_qty_bulk,
    list_daily_production_by_kitting_no,
    update_daily_production,
    delete_daily_production,
    list_daily_production_today,
    list_daily_production_range,
)


class DailyResultAlreadyExists(Exception):
    """
    register_daily_result(check_duplicate=True) で、その計画（kitting_list_no・
    lot_no）の実績が（日付を問わず）既に登録されている場合に送出される。
    呼び出し元（UI）はこれを捕捉して上書き確認を行った上で
    overwrite_daily_result() を呼ぶこと。
    """
    def __init__(self, existing_qty: float):
        self.existing_qty = existing_qty
        super().__init__(f"この計画の実績は既に登録されています（{existing_qty}）。")


def _resolve_plan_item(kitting_list_no: str, lot_no: str = None):
    """
    kitting_list_no（および optional lot_no）から計画を1件に確定する。
    search_plan_by_kitting_no() の「候補を集める部分」と「1件に確定する部分」を
    どちらも担う。

    戻り値：(plan, candidates) のタプル。常にどちらか一方だけが非Noneになる。
    - lot_no指定時：find_plan_item_by_kitting_no(kitting_list_no, lot_no)で一意に
      特定する。呼び出し元（計画一覧の行選択・日次実績履歴のダブルクリック等）が
      既にlot_noを把握している場合に使う経路。→ (plan_or_None, None)
    - lot_no省略時（kitting_list_no欄への直接入力による検索が該当）：
      models.kitting_plan.list_active_plan_items_by_kitting_no() で候補を集める。
      実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースが
      478件確認されているため、
        - 候補0件 → (None, None)（該当なしとして扱う）
        - 候補1件 → (その1件, None)（重複が無い通常のケース、従来通りそのまま
          1件に確定。ダイアログは経由しない）
        - 候補2件以上 → (None, candidates)（呼び出し元でユーザーに選択させ、
          選ばれたlot_noで改めてlot_no指定の経路を呼び直すこと）
    """
    if lot_no is not None:
        return find_plan_item_by_kitting_no(kitting_list_no, lot_no), None

    candidates = list_active_plan_items_by_kitting_no(kitting_list_no)
    if len(candidates) <= 1:
        return (candidates[0] if candidates else None), None
    return None, candidates


def search_plan_by_kitting_no(kitting_list_no: str, lot_no: str = None):
    """
    キッティングリストNo.（および optional lot_no）から計画情報とアプリ内累計を
    取得する。UI表示用の辞書を返す。

    list_active_plan_items() のフィルタ（完了済み・1回目除外等）は適用しない。
    _resolve_plan_item() は計画テーブルの該当行をそのまま返すため、
    完了済み・計画一覧には出ない計画も検索対象に含まれる。

    lot_no：呼び出し元が既にlot_noを把握している場合（計画一覧の行選択・日次実績
    履歴やNG一覧・製品NGレポートのダブルクリック等）に渡すと、_resolve_plan_item()
    がfind_plan_item_by_kitting_no(kitting_list_no, lot_no)で一意に計画を特定する。

    戻り値：(result_dict_or_None, candidates_or_None) のタプル。
    - 一意に確定した場合：(UI表示用辞書, None)
    - lot_no省略時に候補が複数ある場合：(None, candidates)。呼び出し元は
      選択ダイアログ等でユーザーにlot_noを選ばせ、search_plan_by_kitting_no(
      kitting_list_no, 選ばれたlot_no) を改めて呼ぶこと。
    - 該当なしの場合：(None, None)
    """
    plan, candidates = _resolve_plan_item(kitting_list_no, lot_no)
    if candidates is not None:
        return None, candidates
    if not plan:
        return None, None

    app_cumulative = get_app_cumulative_qty(kitting_list_no, plan["lot_no"])

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
        "plan_start_datetime": plan["plan_start_datetime"],
    }

    lot_no = plan["lot_no"]
    lot_info = calculate_lot_completion(lot_no)
    result["lot_completed_quantity"] = lot_info["completed_quantity"]
    result["lot_remaining_quantity"] = lot_info["remaining_quantity"]
    result["lot_file_actuals"] = lot_info["file_actuals"]
    result["lot_surplus"] = lot_info["surplus"]

    return result, None


def register_daily_result(kitting_list_no: str, lot_no: str, daily_qty: float, worker_id: str,
                            report_date: str = None, check_duplicate: bool = False):
    """
    当日実績を1件追加登録する。
    戻り値：更新後のアプリ内累計

    lot_no：呼び出し元（ui.kitting_production_entry.py、既に検索・選択済みの
    current_plan["lot_no"]）から明示的に受け取る。実DBで同一kitting_list_noが
    複数の異なるlot_noにまたがって存在するケースが478件確認されているため、
    find_plan_item_by_kitting_no(kitting_list_no, lot_no)のように必ずlot_noも
    条件に含めて計画を特定する（kitting_list_noだけの検索は、どちらの計画が
    返るか不定になる）。

    check_duplicate=True の場合、登録前に同一kitting_list_no・lot_noの既存レコードの
    有無を、report_dateを問わず確認する（「1計画（kitting_list_no・lot_no）=
    1レコード、常に上書き」ルール。以前は当日分のみを確認する仕様だったが、
    過去日付分も含めてその計画に既存レコードが1件でもあれば重複とみなすよう
    変更した）。既に存在する場合は新規追記せずDailyResultAlreadyExists（既存数量を
    保持）を送出する。呼び出し元（services.production_import_service.
    import_production_csv()）は、これを捕捉して overwrite_daily_result() を呼ぶこと。
    ui.kitting_production_entry.KittingProductionEntryWindow（手動入力）は、
    登録確認ダイアログの時点で既存レコードの有無を先に確認・ユーザーに提示済み
    のため、この例外ハンドリングは経由せず、直接 register_daily_result()／
    overwrite_daily_result() を呼び分ける（_perform_registration()参照）。
    既存レコードが複数件ある場合（本仕様変更前の過去データ等）は、
    最も新しいreport_dateのレコードの数量を表示する
    （list_daily_production_by_kitting_no()はreport_date昇順で返すため末尾）。

    check_duplicate=False（デフォルト）の場合は従来通り無条件に追記する。
    services.production_import_service.import_production_csv()（CSV自動取込）は
    このデフォルト動作のまま呼び出しており、重複防止ロジックの対象外
    （挙動は変更していない）。
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
    if not plan:
        raise ValueError(f"キッティングリストNo. {kitting_list_no}（ロットNo. {lot_no}）の計画が見つかりません。")

    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")

    if check_duplicate:
        existing = list_daily_production_by_kitting_no(kitting_list_no, lot_no)
        if existing:
            raise DailyResultAlreadyExists(existing[-1]["daily_qty"])

    insert_daily_production(
        plan_item_id=plan["plan_item_id"],
        kitting_list_no=kitting_list_no,
        lot_id=plan["lot_no"],
        group_id=plan["board_name"],
        report_date=report_date,
        daily_qty=daily_qty,
        worker_id=worker_id,
    )

    return get_app_cumulative_qty(kitting_list_no, lot_no)


def overwrite_daily_result(kitting_list_no: str, lot_no: str, daily_qty: float, worker_id: str,
                             report_date: str = None):
    """
    同一kitting_list_no・lot_noの既存レコードを、report_dateを問わず全て削除した
    上で、新しい実績を登録し直す（delete-then-insert、「1計画=1レコード、常に
    上書き」ルール。以前は当日分のみを削除する仕様だったが、過去日付分の
    レコードも含めて全て削除するよう変更した）。
    register_daily_result(check_duplicate=True) が DailyResultAlreadyExists を
    送出した後、ユーザーが上書きを承認した場合に呼ぶ。

    lot_no：register_daily_result()と同様、呼び出し元から明示的に受け取り、
    kitting_list_noだけでなくlot_noも条件に含めて計画を特定・削除範囲を絞り込む
    （理由はregister_daily_result()のdocstring参照）。
    戻り値：更新後のアプリ内累計
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
    if not plan:
        raise ValueError(f"キッティングリストNo. {kitting_list_no}（ロットNo. {lot_no}）の計画が見つかりません。")

    if report_date is None:
        report_date = datetime.now().strftime("%Y-%m-%d")

    replace_daily_result(
        plan_item_id=plan["plan_item_id"],
        kitting_list_no=kitting_list_no,
        lot_id=plan["lot_no"],
        group_id=plan["board_name"],
        report_date=report_date,
        daily_qty=daily_qty,
        worker_id=worker_id,
    )

    return get_app_cumulative_qty(kitting_list_no, lot_no)


def register_opposite_side_daily_result(plan: dict, daily_qty: float, worker_id: str,
                                          report_date: str = None):
    """
    plan（"kitting_list_no"・"lot_no"・"setup_file_no"・"production_side"・
    "plan_start_datetime" を含む計画dict。ui.kitting_production_entry の
    self.current_plan、または models.kitting_plan.find_plan_item_by_kitting_no()の
    戻り値をそのまま渡せる）の反対側の面（find_opposite_side_plan()）が存在する
    場合、同じ数量（daily_qty）をその面にも登録する（面2への登録時に面1へ連動、
    など）。反対側が存在しない（片面のみの計画）場合は何もしない。

    ui.kitting_production_entry.KittingProductionEntryWindow（手動入力）・
    services.production_import_service.import_production_csv()（CSV自動取込）の
    両方から共通で使う（重複実装を避けるため、UI層に置いていたロジックをここへ
    切り出した）。

    反対側でも重複防止（check_duplicate=True、日付を問わずその計画に既存レコードが
    あれば重複とみなす）は通すが、選択中／取込元の面では既に登録が確定している
    ため、反対側でDailyResultAlreadyExistsが出ても確認は求めず、そのまま
    overwrite_daily_result()で自動上書きする。

    戻り値：反対側への登録を実際に行った場合True、反対側が存在しない場合False。
    反対側の登録自体が失敗した場合（計画不整合等）は例外がそのまま呼び出し元へ
    伝播する。呼び出し元（元の面の登録は既に成功している）は、この例外によって
    元の面の登録結果を取り消す必要はない（呼び出し元の責任で、必要に応じて
    try/exceptで囲むこと）。
    """
    side = str(plan.get("production_side") or "").strip()
    if side not in ("1", "2"):
        return False

    opposite_plan = find_opposite_side_plan(
        plan.get("lot_no"), plan.get("setup_file_no"), side,
        current_plan_start_datetime=plan.get("plan_start_datetime"),
    )
    if opposite_plan is None:
        return False

    opposite_kitting_no = opposite_plan["kitting_list_no"]
    opposite_lot_no = opposite_plan["lot_no"]
    try:
        register_daily_result(
            opposite_kitting_no, opposite_lot_no, daily_qty, worker_id,
            report_date=report_date, check_duplicate=True,
        )
    except DailyResultAlreadyExists:
        overwrite_daily_result(opposite_kitting_no, opposite_lot_no, daily_qty, worker_id, report_date=report_date)
    return True


def get_daily_history(kitting_list_no: str, lot_no: str, report_date: str = None):
    """
    指定kitting_list_no・lot_noの履歴を取得する。report_date（"YYYY-MM-DD"）を
    指定するとその日付のみ、省略時は全期間の履歴を返す。

    lot_noを必須にしている理由はmodels.production.list_daily_production_by_kitting_no()
    と同様（同一kitting_list_noが複数の異なるlot_noにまたがって存在する実データが
    478件確認されているため）。
    """
    return list_daily_production_by_kitting_no(kitting_list_no, lot_no, report_date)


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

    lot_noは、計画を都度検索し直す（plan["lot_no"]）のではなく、
    production_daily自身が持つrec["lot_id"]（登録時のlot_noがそのまま記録されている
    列）をそのまま使う。実DBで同一kitting_list_noが複数の異なるlot_noにまたがって
    存在するケースが478件確認されており、kitting_list_noだけの検索
    （find_plan_item_by_kitting_no(kitting_list_no)）ではどちらの計画が返るか
    不定になるため、その実績が実際にどのlot_noに対して登録されたかを
    確実に示すrec["lot_id"]を優先する。
    """
    enriched = []
    for rec in records:
        kitting_list_no = rec["kitting_list_no"]
        lot_no = rec["lot_id"] or ""
        plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no) if lot_no \
            else find_plan_item_by_kitting_no(kitting_list_no)
        enriched.append({
            "kitting_list_no": kitting_list_no,
            "plan": plan,
            "daily_qty": rec["daily_qty"],
            "lot_no": lot_no or (plan["lot_no"] if plan else ""),
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
            "kitting_list_no": kitting_list_no,
            "file_no": plan["setup_file_no"] if plan else "",
            "board_name": plan["board_name"] if plan else "",
            "production_side": plan["production_side"] if plan else None,
            "lot_no": lot_no,
            "daily_qty": daily_qty,
            "app_cumulative_qty": get_app_cumulative_qty(kitting_list_no, lot_no),
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

    # get_app_cumulative_qty()を件数分ループ呼び出しする代わりに、対象の
    # (kitting_list_no, lot_no)の組を先に集めて1回（〜数回）のクエリでまとめて
    # 取得する。list_plan_items_by_lot(lot_no)で取得したplan_itemsは全て同一
    # lot_noに属するため、各kitting_list_noにこのlot_noをそのまま組み合わせれば
    # よい。lot_noを組に含める理由：実DBで同一kitting_list_noが複数の異なる
    # lot_noにまたがって存在するケースが478件確認されており、kitting_list_noだけで
    # 集計すると別ロットの実績まで巻き込んで合算してしまう
    # （実データで完成数の取り違えを確認済み。このバグの直接の修正対象）。
    kitting_list_no_lot_pairs = [(item["kitting_list_no"], lot_no) for item in plan_items]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs)

    file_actuals = {}
    for item in plan_items:
        file_no = item["setup_file_no"]
        kitting_list_no = item["kitting_list_no"]
        file_actuals[file_no] = cumulative_by_pair[(kitting_list_no, lot_no)]

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