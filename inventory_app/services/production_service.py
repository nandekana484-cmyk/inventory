# services/production_service.py
from datetime import datetime
from models.kitting_plan import (
    find_plan_item_by_kitting_no,
    list_plan_items_by_lot,
    list_plan_items_for_all_lots,
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
        "mounting_line": plan["mounting_line"],
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

    面1省略（重要）：同一(lot_no, setup_file_no)に現在アクティブな面2計画が
    存在する場合、面1の行は一覧から除外する（models.kitting_plan.
    list_active_plan_items()の「1回目除外」・ui.kitting_production_entry.py::
    search_plan()の基板別実績表示と同じ考え方。面連動登録により通常は面1・面2の
    実績数量は常に一致するはずだが、面1のみを個別に表示し続ける意味が無いため）。

    ただし、面1の実績数量が面2（find_opposite_side_plan()で特定した、現在の
    アプリ内累計＝get_app_cumulative_qty()）を上回っている場合は「不整合」として
    扱い、除外はするが黙って消さず、戻り値のinconsistency_warningsに記録する
    （ui.kitting_production_entry.py::ActualCorrectionWindowが面連動を行わず
    片面のみを修正・削除できるため、面1・面2の実績が食い違う状態を作れる。
    調査により確認済み）。面2計画が存在しない（片面のみの計画）場合は、
    比較対象が無いため除外・警告いずれも行わない。

    戻り値：(report_rows, inconsistency_warnings) のタプル。
      report_rows：[{"seq", "kitting_list_no", ...}, ...]（面1省略後、seqは
                    表示される行のみで1から振り直す）
      inconsistency_warnings：[{"lot_no", "setup_file_no", "side1_kitting_list_no",
                                 "side1_qty", "side2_kitting_list_no", "side2_qty"}, ...]
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

    excluded_indices = set()
    inconsistency_warnings = []
    for idx, item in enumerate(enriched):
        plan = item["plan"]
        if plan is None:
            continue
        side = str(plan.get("production_side") or "").strip()
        if side != "1":
            continue

        setup_file_no = plan.get("setup_file_no")
        lot_no = item["lot_no"]
        opposite = find_opposite_side_plan(lot_no, setup_file_no, "1")
        if opposite is None:
            continue  # 面2計画が無い（片面のみの計画）→ 除外しない

        opposite_kitting_list_no = opposite["kitting_list_no"]
        side1_qty = item["daily_qty"]
        side2_qty = get_app_cumulative_qty(opposite_kitting_list_no, lot_no)

        if side1_qty > side2_qty:
            inconsistency_warnings.append({
                "lot_no": lot_no,
                "setup_file_no": setup_file_no,
                "side1_kitting_list_no": item["kitting_list_no"],
                "side1_qty": side1_qty,
                "side2_kitting_list_no": opposite_kitting_list_no,
                "side2_qty": side2_qty,
            })

        excluded_indices.add(idx)

    report_rows = []
    seq = 1
    for idx, item in enumerate(enriched):
        if idx in excluded_indices:
            continue

        plan = item["plan"]
        kitting_list_no = item["kitting_list_no"]
        lot_no = item["lot_no"]
        daily_qty = item["daily_qty"]
        order_qty = plan["order_qty"] if plan else 0
        completed = lot_completed.get(lot_no, 0)

        report_rows.append({
            "seq": seq,
            "kitting_list_no": kitting_list_no,
            "file_no": plan["setup_file_no"] if plan else "",
            "board_name": plan["board_name"] if plan else "",
            "production_side": plan["production_side"] if plan else None,
            "mounting_line": plan["mounting_line"] if plan else None,
            "lot_no": lot_no,
            "daily_qty": daily_qty,
            "app_cumulative_qty": get_app_cumulative_qty(kitting_list_no, lot_no),
            "order_qty": order_qty,
            "lot_completed": completed,
            "surplus_qty": daily_qty - completed,
            "lot_remaining": order_qty - completed,
        })
        seq += 1

    return report_rows, inconsistency_warnings


def build_daily_report():
    """
    本日（report_date = 今日）入力された実績を元に、日報表示用のデータを構築する。
    戻り値は_build_report_rows()と同じ (report_rows, inconsistency_warnings) タプル。
    """
    records = list_daily_production_today()
    return _build_report_rows(records)


def build_monthly_report(from_date: str, to_date: str):
    """
    指定期間（report_date が from_date～to_date、両端含む）の実績を元に、
    月報表示用のデータを構築する。列構成・集計ロジックは日報（build_daily_report）と共通。
    戻り値は_build_report_rows()と同じ (report_rows, inconsistency_warnings) タプル。
    """
    records = list_daily_production_range(from_date, to_date)
    return _build_report_rows(records)


def _compute_lot_completion(lot_no: str, plan_items: list, cumulative_by_pair: dict) -> dict:
    """
    calculate_lot_completion()・list_incomplete_lots()の共通ロジック。

    plan_itemsは同一lot_noに属する計画行のリスト、cumulative_by_pairは
    (kitting_list_no, lot_no) -> アプリ内累計 の辞書（呼び出し元が
    get_app_cumulative_qty_bulk()で事前に一括取得したものをそのまま渡す。
    calculate_lot_completion()は対象lot_no1件分のみ、list_incomplete_lots()は
    全lot_no分をまとめて1回のバルク取得で済ませており、取得方法自体は
    呼び出し元ごとに異なるためこの関数の責務には含めない）。

    完成数は、同一lot_noに属する各setup_file_no × production_side（面）
    単位で実績累計（daily_qtyのSUM）を合算した値のうち、最小値とする。
    キーを(setup_file_no, production_side)の2要素にし、代入ではなく必ず
    加算とする理由：同一file_no・同一面に対してkitting_list_noが異なる
    複数のバッチ（例：実装予定日違いの別ロット）が同時にアクティブ
    （is_active=1）な場合が実データで222件確認されているが、これを
    「代入」で処理すると片方のバッチの実績がもう片方で上書きされて
    しまう（データ消失）。file_no×面単位の合計として正しく取り込むには
    必ず加算する必要がある。

    order_qtyは、以前はplan_items[0]（順序不定のSELECT結果の先頭行）の値を
    無条件に採用していたが、実DBで複数file_noを持つlot_no 301件中3件
    （166248・516526・516626）でfile_no間のorder_qtyが不一致であることが
    調査で判明した。distinctな値が1つならその値をそのまま採用し、複数ある
    場合は従来通りplan_items[0]相当の値を代表として採用しつつ、
    戻り値のorder_qty_inconsistent/order_qty_valuesで不一致を検知したことを
    呼び出し元に伝える（月報側での警告表示に使う想定。日報側は今回スコープ外）。
    """
    order_qty_values = sorted({item["order_qty"] for item in plan_items})
    order_qty_inconsistent = len(order_qty_values) > 1
    order_qty = plan_items[0]["order_qty"] if order_qty_inconsistent else order_qty_values[0]

    file_actuals = {}
    for item in plan_items:
        kitting_list_no = item["kitting_list_no"]
        key = (item["setup_file_no"], item["production_side"])
        file_actuals[key] = file_actuals.get(key, 0) + cumulative_by_pair[(kitting_list_no, lot_no)]

    completed = min(file_actuals.values())
    remaining = order_qty - completed

    return {
        "lot_no": lot_no,
        "order_quantity": order_qty,
        "order_qty_inconsistent": order_qty_inconsistent,
        "order_qty_values": order_qty_values,
        "completed_quantity": completed,
        "remaining_quantity": remaining,
        "file_actuals": file_actuals,
    }


def calculate_lot_completion(lot_no: str):
    """
    lot_no 単位でロット完成数・未完成数を算出する。計算の詳細は
    _compute_lot_completion()のdocstring参照。
    """
    plan_items = list_plan_items_by_lot(lot_no)
    if not plan_items:
        raise ValueError(f"ロットNo. {lot_no} の計画が見つかりません。")

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

    return _compute_lot_completion(lot_no, plan_items, cumulative_by_pair)


def list_incomplete_lots():
    """
    distinctなlot_no全件について、ロット未完成数（lot_remaining_quantity、
    calculate_lot_completion()と同じ計算式、内部的にも_compute_lot_completion()を
    共有している）を算出し、0より大きい（＝未完了）ロットのみを一覧で返す
    （DB間コピー機能の「未完了lot_noの抽出」用）。

    calculate_lot_completion()をlot_no件数分ループ呼び出しするとN+1
    （list_plan_items_by_lot()のSELECTがlot_no件数分発生）になるため、
    models.production.get_app_cumulative_qty_bulk()による一括取得
    （list_active_plan_items()で採用済みの高速化パターン）と同じ考え方で、
    全lot_no分の計画行をmodels.kitting_plan.list_plan_items_for_all_lots()で
    1回のSELECTにまとめて取得し、アプリ内累計もget_app_cumulative_qty_bulk()で
    1回（〜数回）のバルククエリにまとめて取得した上で、Python側でlot_noごとに
    グルーピングして_compute_lot_completion()に渡す。

    list_active_plan_items()は使わない：「1回目除外」ロジック（同一setup_file_no
    でproduction_side=2が存在する場合、対応するside=1を除外する）や完了済み
    除外ロジック（デフォルトinclude_completed=False）が適用されており、
    lot単位の完成数計算に必要な全ての(setup_file_no, production_side,
    kitting_list_no)組を欠落なく集める、という本関数の目的には合わないため
    （calculate_lot_completion()自身もlist_plan_items_by_lot()を使っており、
    list_active_plan_items()は使っていない）。

    戻り値：[{"lot_no", "kitting_list_nos"（そのlot_noに属するdistinctな
              kitting_list_noのソート済みリスト。DB間コピー時にどのkitting_list_no
              を対象にすればよいか把握するための情報）, "order_quantity",
              "completed_quantity", "remaining_quantity"}, ...]
             lot_no昇順。remaining_quantity > 0 の行のみを含む。
    """
    plan_items = list_plan_items_for_all_lots()

    items_by_lot = {}
    for item in plan_items:
        items_by_lot.setdefault(item["lot_no"], []).append(item)

    kitting_list_no_lot_pairs = [
        (item["kitting_list_no"], item["lot_no"]) for item in plan_items
    ]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs)

    results = []
    for lot_no, items in items_by_lot.items():
        info = _compute_lot_completion(lot_no, items, cumulative_by_pair)

        if info["remaining_quantity"] <= 0:
            continue

        results.append({
            "lot_no": lot_no,
            "kitting_list_nos": sorted({item["kitting_list_no"] for item in items}),
            "order_quantity": info["order_quantity"],
            "completed_quantity": info["completed_quantity"],
            "remaining_quantity": info["remaining_quantity"],
        })

    results.sort(key=lambda r: r["lot_no"])
    return results


def _pick_representative_plan_item(items: list):
    """
    同一(setup_file_no, production_side)に属する複数バッチ（items）から、
    仕掛スナップショットの代表として1件を選ぶ。plan_start_datetime
    （"YYYY/MM/DD HH:MM:SS"形式）が最も新しいものを採用する（直近の
    バッチほど、まだ手元に残っている仕掛の実体に近いと判断）。
    全件パース不能・欠落の場合は、フォールバックとしてitemsの最後
    （list_plan_items_by_lot()の取得順そのまま）を採用する。
    """
    def parse_dt(value):
        try:
            return datetime.strptime(str(value).strip(), "%Y/%m/%d %H:%M:%S")
        except (TypeError, ValueError):
            return None

    parseable = [(parse_dt(item.get("plan_start_datetime")), item) for item in items]
    parseable = [(dt, item) for dt, item in parseable if dt is not None]
    if parseable:
        return max(parseable, key=lambda pair: pair[0])[1]
    return items[-1]


def build_wip_extraction_rows(lot_nos: list) -> list:
    """
    指定されたlot_no一覧について、setup_file_no × production_side（面）単位の
    仕掛数量（= calculate_lot_completion()のfile_actuals[key] - completed_quantity）
    を算出し、models.wip_board_snapshot.save_wip_snapshot()にそのまま渡せる
    行のリストを返す（ui.monthly_report_window.MonthlyReportWindow.on_extract_wip()
    から呼ばれる）。

    以前はself.report_rows（_build_report_rows()、kitting_list_no＝バッチ単位）の
    surplus_qty（そのバッチのdaily_qty − ロット全体の最小値）をそのまま抽出して
    いたが、この方式では複数バッチを持つfile_noの仕掛数量が正しく合算されない
    問題があった（BOM_MIGRATION_NOTES.md/調査参照）。calculate_lot_completion()が
    file_no×面単位で実績を合算する方式に変更されたことに伴い、仕掛数量の算出も
    同じfile_actualsを土台にする。

    面1省略（重要）：同一setup_file_noに面2の実績も存在する場合、面1は
    ui.kitting_production_entry.py::search_plan()の「基板別実績」表示や
    _build_report_rows()の面1省略ロジックと同じ考え方で除外する。面連動登録に
    より面1・面2の実績は常に一致するはずのため、除外せずに両方を仕掛として
    抽出すると、物理的には1枚の基板の仕掛が面1・面2それぞれの行として二重に
    計上されてしまう。

    正の仕掛数量を持つ(setup_file_no, production_side)の組ごとに、該当バッチの
    うちplan_start_datetimeが最も新しいもの（_pick_representative_plan_item()）を
    代表として選び、そのkitting_list_no・board_name・mounting_lineを使う
    （wip_board_snapshotのスキーマは1行1kitting_list_noのまま変更しないため、
    複数バッチを1行にまとめる以上、代表値を選ぶ必要がある）。

    lot_noの計画が見つからない（既に削除・無効化された等）場合は、
    calculate_lot_completion()がValueErrorを送出するため、その lot_no は
    スキップする。
    """
    rows = []
    for lot_no in lot_nos:
        try:
            info = calculate_lot_completion(lot_no)
        except ValueError:
            continue

        completed = info["completed_quantity"]

        items_by_key = {}
        for item in list_plan_items_by_lot(lot_no):
            key = (item["setup_file_no"], item["production_side"])
            items_by_key.setdefault(key, []).append(item)

        second_side_files = {
            file_no for (file_no, side) in info["file_actuals"]
            if str(side).strip() == "2"
        }

        for key, file_actual in info["file_actuals"].items():
            file_no, side = key
            if str(side).strip() == "1" and file_no in second_side_files:
                continue

            wip_qty = file_actual - completed
            if wip_qty <= 0:
                continue

            candidates = items_by_key.get(key)
            if not candidates:
                continue
            representative = _pick_representative_plan_item(candidates)

            rows.append({
                "kitting_list_no": representative["kitting_list_no"],
                "file_no": representative["setup_file_no"],
                "board_name": representative["board_name"],
                "production_side": representative["production_side"],
                "mounting_line": representative["mounting_line"],
                "lot_no": lot_no,
                "surplus_qty": wip_qty,
            })

    return rows