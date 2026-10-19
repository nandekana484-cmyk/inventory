import logging
from datetime import datetime
from models.kitting_plan import (
    find_plan_item_by_kitting_no,
    list_plan_items_by_lot,
    list_active_plan_items_by_kitting_no,
    find_opposite_side_plan,
    list_active_side2_plans_for_file,
    classify_side1_only_plan,
    SIDE1_ONLY_CLASS_WAITING_SIDE2,
)
from models.production import (
    insert_daily_production,
    replace_daily_result,
    get_app_cumulative_qty,
    list_daily_production_by_kitting_no,
    get_production_daily_by_id,
    update_daily_production,
    delete_daily_production,
    cancel_daily_production,
)
from models.ng_declarations import get_ng_declaration
from models.scrap_records import list_scrap_records_by_kitting_no
from models.wip_scrap_records import list_wip_scrap_records_by_kitting_no
from services.date_parsing import parse_plan_start_datetime
from services.lot_merge_service import load_lot_merge_map
# 段階5で services/lot_status_service.py へ移した。import している側を変えないよう、ここから re-export する。
from services.lot_status_service import (  # noqa: F401 （re-export）
    record_lot_status_snapshot,
    _compute_lot_completion,
    calculate_lot_completion,
    DRAWDOWN_ZERO_STATUSES,
    NEEDS_REVIEW_STATUSES,
    _build_lot_status_remarks,
    _lot_status_color_category,
    _evaluate_lot_status,
    evaluate_lot_status,
    list_incomplete_lots,
    check_lot_progress,
    evaluate_all_lot_status,
)
# 段階5で services/production_report_service.py へ移した。import している側を変えないよう、ここから re-export する。
from services.production_report_service import (  # noqa: F401 （re-export）
    _build_report_rows,
    build_daily_report,
    build_monthly_report,
)

logger = logging.getLogger(__name__)


def _record_lot_status_snapshot_safely(lot_no: str, trigger_source: str):
    """record_lot_status_snapshot() を呼び、失敗しても例外を外に出さない（履歴の記録の失敗で、実績の登録・修正を失敗させないため）。"""
    try:
        record_lot_status_snapshot(lot_no, trigger_source)
    except Exception:
        logger.exception(
            "lot_status_historyの記録に失敗しました（lot_no=%s, trigger_source=%s）。"
            "実績の登録・修正自体はそのまま続行します。",
            lot_no, trigger_source,
        )


class DailyResultAlreadyExists(Exception):
    """
    register_daily_result(check_duplicate=True) で、その計画に（日付を問わず）実績が既にあるときに送出する。
    呼び出し元は上書きを確認してから overwrite_daily_result() を呼ぶこと。
    """
    def __init__(self, existing_qty: float):
        self.existing_qty = existing_qty
        super().__init__(f"この計画の実績は既に登録されています（{existing_qty}）。")


# 「面2待ち」（生産面マスターでは面2があるのに、面2の計画がまだ無い）の面1への登録を断るときの案内（D-170、利用者の決定の文言）。
SIDE2_WAIT_MESSAGE = "このロットには面2の計画があるはずです。面2の計画を取り込んでから、面2に登録してください"


class Side1RegistrationRefused(ValueError):
    """面1の計画への登録を受け付けないとき（D-170）。メッセージはそのまま画面に出せる。"""


def side2_plans_hiding(plan) -> list:
    """
    plan が面1で、元のロット・ファイルNoに面2の有効な計画があれば、その面2の計画（生産予定日の順）。それ以外は空。
    空でなければ、plan は無いものとして扱う（一覧・候補・検索・履歴に出さない、登録させない、計算に使わない。D-167・D-170）。
    """
    if plan is None or str(plan.get("production_side") or "").strip() != "1":
        return []
    return list_active_side2_plans_for_file(plan.get("lot_no"), plan.get("setup_file_no"))


def side2_plans_hiding_plan_no(kitting_list_no: str, lot_no: str) -> list:
    """計画No・ロットNoで引いた計画の side2_plans_hiding()（実績修正の画面で、面2がある面1の実績を読み取り専用にするため）。"""
    return side2_plans_hiding(find_plan_item_by_kitting_no(kitting_list_no, lot_no))


def side1_registration_block(plan):
    """
    plan への実績の登録を受け付けない理由（受け付けるなら None）。どの登録経路でも、この関数で判定する（D-170）。
    - 面2の有効な計画がある面1: 面2に登録してもらう
    - 「面2待ち」の面1（classify_side1_only_plan() の b）: SIDE2_WAIT_MESSAGE
    片面の製品（生産面マスターで面2なし）と、生産面マスター未登録（c、確認のうえで登録できる。D-94）は受け付ける。
    """
    hiding = side2_plans_hiding(plan)
    if hiding:
        side2_nos = "、".join(p["kitting_list_no"] for p in hiding)
        return (f"この計画（{plan['kitting_list_no']}）は面1です。同じロット・ファイルNoに面2の計画（{side2_nos}）があるため、"
                "面1には登録できません。面2の計画に登録してください")
    if plan is not None and classify_side1_only_plan(plan) == SIDE1_ONLY_CLASS_WAITING_SIDE2:
        return SIDE2_WAIT_MESSAGE
    return None


def _refuse_side1_registration(plan):
    """登録の入口での二重の安全策（画面でも先に断っている）。"""
    reason = side1_registration_block(plan)
    if reason is not None:
        raise Side1RegistrationRefused(reason + "。")


def _resolve_plan_item(kitting_list_no: str, lot_no: str = None):
    """
    kitting_list_no（と lot_no）から計画を1件に確定する。戻り値 (plan, candidates) は、どちらか一方だけが None でない。
    lot_no ありなら一意に特定する。lot_no なしなら候補を集め、0件→(None, None)、1件→(その1件, None)、
    2件以上→(None, candidates)（kitting_list_no は lot_no をまたいで重複するため。呼び出し元で選ばせ、lot_no 付きで呼び直すこと）。
    """
    if lot_no is not None:
        return find_plan_item_by_kitting_no(kitting_list_no, lot_no), None

    candidates = list_active_plan_items_by_kitting_no(kitting_list_no)
    if len(candidates) <= 1:
        return (candidates[0] if candidates else None), None
    return None, candidates


def search_plan_by_kitting_no(kitting_list_no: str, lot_no: str = None):
    """
    計画情報とアプリ内累計を、UI 表示用の辞書で返す。list_active_plan_items() の絞り込み（完了済み・面1の除外）は適用しない。
    戻り値は (辞書, None)。lot_no なしで候補が複数なら (None, candidates)、該当なしなら (None, None)。
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
                            report_date: str = None, check_duplicate: bool = False,
                            record_history: bool = True):
    """
    実績を1件追加登録し、更新後のアプリ内累計を返す。計画は必ず lot_no も条件にして特定する（kitting_list_no は重複するため）。
    check_duplicate=True: 日付を問わず、その計画に実績があれば追記せず DailyResultAlreadyExists を送出する（1計画＝1レコード）。
    check_duplicate=False（既定）: 無条件に追記する（CSV 自動取込はこちら）。
    record_history=False: 履歴（lot_status_history）を記録しない。一括登録で行ごとに記録すると遅いので、呼び出し元が最後にまとめて記録する。
    面2がある面1・「面2待ち」の面1の計画には登録せず、Side1RegistrationRefused を送出する（D-170）。
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
    if not plan:
        raise ValueError(f"キッティングリストNo. {kitting_list_no}（ロットNo. {lot_no}）の計画が見つかりません。")
    _refuse_side1_registration(plan)

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

    # 履歴の記録に失敗しても、登録は失敗させない（_record_lot_status_snapshot_safely()）。
    if record_history:
        _record_lot_status_snapshot_safely(lot_no, "register")

    return get_app_cumulative_qty(kitting_list_no, lot_no)


def overwrite_daily_result(kitting_list_no: str, lot_no: str, daily_qty: float, worker_id: str,
                             report_date: str = None, record_history: bool = True):
    """
    その計画の既存の実績を日付を問わず全部削除してから登録し直す（1計画＝1レコード、常に上書き）。更新後のアプリ内累計を返す。
    report_date が None なら、そのまま replace_daily_result() に渡して既存行の日付を引き継がせる
    （ここで今日にすると、手で数量を直すたびに CSV 由来の正しい日付が今日に書き換わってしまう）。
    登録と同じく、面2がある面1・「面2待ち」の面1には書かない（D-170）。
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
    if not plan:
        raise ValueError(f"キッティングリストNo. {kitting_list_no}（ロットNo. {lot_no}）の計画が見つかりません。")
    _refuse_side1_registration(plan)

    replace_daily_result(
        plan_item_id=plan["plan_item_id"],
        kitting_list_no=kitting_list_no,
        lot_id=plan["lot_no"],
        group_id=plan["board_name"],
        report_date=report_date,
        daily_qty=daily_qty,
        worker_id=worker_id,
    )

    # register_daily_result() と同じく履歴を記録する。
    if record_history:
        _record_lot_status_snapshot_safely(lot_no, "overwrite")

    return get_app_cumulative_qty(kitting_list_no, lot_no)


def get_daily_history(kitting_list_no: str, lot_no: str, report_date: str = None):
    """kitting_list_no・lot_no の実績履歴を返す。report_date を指定するとその日だけ。lot_no が必須なのは kitting_list_no が重複するため。"""
    return list_daily_production_by_kitting_no(kitting_list_no, lot_no, report_date)


def update_daily_result(prod_log_id: int, daily_qty: float):
    """
    実績1件（prod_log_id）の数量を修正する。履歴を記録するため、UPDATE の前に対象行を読んで lot_no を特定する
    （行が既に無ければ、更新はそのまま実行し、履歴の記録だけを省く）。
    """
    row = get_production_daily_by_id(prod_log_id)
    update_daily_production(prod_log_id, daily_qty)
    if row is not None:
        _record_lot_status_snapshot_safely(row["lot_id"], "update")


def delete_daily_result(prod_log_id: int):
    """
    実績1件（prod_log_id）を削除する。削除後は行を読めないので、lot_no を特定するため必ず DELETE の前に対象行を読むこと。
    行が無ければ何もしない。
    """
    row = get_production_daily_by_id(prod_log_id)
    delete_daily_production(prod_log_id)
    if row is not None:
        _record_lot_status_snapshot_safely(row["lot_id"], "delete")


def build_correction_preview(prod_log_id: int, action: str, new_daily_qty: float = None):
    """
    実績1件の修正・削除の内容を組み立てる（DB には書き込まない）。反対側の面へは連動しない（面1に実績を書かないため。D-169）。
    戻り値: {"primary": {"prod_log_id", "kitting_list_no", "lot_no", "old_daily_qty", "new_daily_qty"（削除は None）,
                         "report_date", "plan"（計画 or None）}}
    """
    if action not in ("update", "delete"):
        raise ValueError(f"未対応のaction: {action}")

    row = get_production_daily_by_id(prod_log_id)
    if row is None:
        raise ValueError(f"実績（prod_log_id={prod_log_id}）が見つかりません。")

    return {"primary": {
        "prod_log_id": prod_log_id, "kitting_list_no": row["kitting_list_no"], "lot_no": row["lot_id"],
        "old_daily_qty": row["daily_qty"], "new_daily_qty": new_daily_qty if action == "update" else None,
        "report_date": row["report_date"], "plan": find_plan_item_by_kitting_no(row["kitting_list_no"], row["lot_id"]),
    }}


def apply_correction(preview: dict, action: str, record_history: bool = True):
    """build_correction_preview() の内容（利用者が承認したもの）を確定する。修正・削除するのはその実績1件だけ。"""
    primary = preview["primary"]
    if action == "update":
        update_daily_production(primary["prod_log_id"], primary["new_daily_qty"])
    else:
        delete_daily_production(primary["prod_log_id"])
    if record_history:
        _record_lot_status_snapshot_safely(primary["lot_no"], f"correction_{action}")


# 実績の取り消しで作り直した登録待ちの行の、取込のバッチの source_file（どこから来た行か見分けるため。D-129）。
CANCELLED_REGISTRATION_SOURCE = "（実績の取り消しで登録待ちに戻した行）"


def to_csv_date_format(report_date):
    """"2026-07-14" → "2026/7/14"（実績CSVの書式。同じCSVの再取込で、登録待ちの行が2つに増えないようにするため）。"""
    try:
        year, month, day = str(report_date).split("-")
        return f"{int(year)}/{int(month)}/{int(day)}"
    except (TypeError, ValueError):
        return report_date


def build_cancellation_preview(prod_log_id: int) -> dict:
    """
    実績1件の取り消し（D-129）の内容を組み立てる。DB には書き込まない。
    - 取り消すのはその実績1件だけ（反対側の面の実績へは連動しない。D-169）
    - NG申告は、取り消す計画の分と、面2なら同じロット・ファイルNoの面1の計画の分を取り消す（面2の NG は面1にも加算して
      保存されているため。面1の計画は NG の欄と同じ find_opposite_side_plan() で探す）
    - そのNG申告の計画・面に NG展開（scrap_records）が、または取り消す実績の計画・面に仕掛部品展開（wip_scrap_records）が
      確定済みなら、取り消せない（blockers に理由を入れる）
    - 登録待ちに戻す行は1行（取り消す実績の分だけ）。日付は実績CSVの書式にする
    戻り値: {"primary", "ng_declarations", "blockers", "restored_row"}
    """
    primary = build_correction_preview(prod_log_id, "delete")["primary"]
    plan = primary["plan"]
    lot_no = primary["lot_no"]
    side = str(plan.get("production_side") or "").strip() if plan else ""
    row = get_production_daily_by_id(prod_log_id)

    primary_info = {
        "prod_log_id": prod_log_id, "kitting_list_no": primary["kitting_list_no"], "lot_no": lot_no,
        "production_side": side, "daily_qty": primary["old_daily_qty"], "report_date": primary["report_date"],
    }
    ng_targets = [(primary["kitting_list_no"], side)] if side in ("1", "2") else []
    if side == "2":
        side1_plan = find_opposite_side_plan(
            lot_no, plan.get("setup_file_no"), "2", current_plan_start_datetime=plan.get("plan_start_datetime"),
        )
        if side1_plan is not None:
            ng_targets.append((side1_plan["kitting_list_no"], "1"))
    ng_declarations = []
    blockers = []
    for kitting_list_no, ng_side in ng_targets:
        declaration = get_ng_declaration(kitting_list_no, int(ng_side), lot_no)
        if declaration is None:
            continue
        ng_declarations.append({
            "kitting_list_no": kitting_list_no, "production_side": ng_side, "lot_no": lot_no,
            "ng_qty": declaration["ng_qty"], "report_date": declaration["report_date"],
        })
        scrap = list_scrap_records_by_kitting_no(kitting_list_no, lot_no, int(ng_side))
        if scrap:
            blockers.append(f"計画 {kitting_list_no}（面{ng_side}）の NG が、NG展開（仕損展開）で確定済みです（{len(scrap)}件）。")
    for target in [primary_info]:
        if target["production_side"] not in ("1", "2"):
            continue
        wip = list_wip_scrap_records_by_kitting_no(target["kitting_list_no"], lot_no, int(target["production_side"]))
        if wip:
            blockers.append(
                f"計画 {target['kitting_list_no']}（面{target['production_side']}）の仕掛が、仕掛部品展開で確定済みです（{len(wip)}件）。"
            )

    restored_row = {
        "lot_no": lot_no,
        "product_name": (plan or {}).get("board_name") or (row or {}).get("group_id"),
        "daily_qty": primary["old_daily_qty"],
        "report_date": to_csv_date_format(primary["report_date"]),
        "worker_id": (row or {}).get("worker_id"),
    }
    return {
        "primary": primary_info, "ng_declarations": ng_declarations,
        "blockers": blockers, "restored_row": restored_row,
    }


def apply_cancellation(preview: dict, return_to_staging: bool, imported_by: str = None):
    """
    build_cancellation_preview() の内容を1つのトランザクションで確定する（D-129）。blockers があれば ValueError。
    return_to_staging=True なら登録待ちの行を作り直し、その pending_row_id を返す（False なら None）。
    """
    if preview["blockers"]:
        raise ValueError("NG展開・仕掛部品展開で確定済みのため、取り消せません。")
    prod_log_ids = [preview["primary"]["prod_log_id"]]
    ng_keys = [(d["kitting_list_no"], int(d["production_side"]), d["lot_no"]) for d in preview["ng_declarations"]]
    pending_row_id = cancel_daily_production(
        prod_log_ids, ng_keys,
        restored_row=preview["restored_row"] if return_to_staging else None,
        restored_source=CANCELLED_REGISTRATION_SOURCE, imported_by=imported_by,
    )
    # 記録に失敗しても取り消しは失敗させない。
    _record_lot_status_snapshot_safely(preview["primary"]["lot_no"], "cancel_registration")
    return pending_row_id


def _pick_representative_plan_item(items: list):
    """
    同じ (setup_file_no, production_side) の複数バッチから、仕掛スナップショットの代表を1件選ぶ。
    生産予定日が最も新しいもの（手元に残る仕掛の実体に近い）。解釈できなければ最後の1件。
    """
    parseable = [(parse_plan_start_datetime(item.get("plan_start_datetime")), item) for item in items]
    parseable = [(dt, item) for dt, item in parseable if dt is not None]
    if parseable:
        return max(parseable, key=lambda pair: pair[0])[1]
    return items[-1]


def build_wip_extraction_rows(lot_nos: list) -> list:
    """
    指定ロットの file_no・面ごとの仕掛数量（file_actuals − 引落）を、save_wip_snapshot() に渡せる行のリストで返す。
    - 引落は evaluate_lot_status() の値を使う（shortfall のロットも漏れずに抽出される）
    - 面2の計画がある file_no の面1は、完成数の計算（file_actuals）に入らないので出てこない（D-167）
    - 複数バッチは、生産予定日が最も新しいものを代表にする（スナップショットは1行1 kitting_list_no のため）
    - 計画が見つからないロットは飛ばす。shortfall の「未確定」は対象外（実在の kitting_list_no が無いため）
    - 統合したロット（D-150）は統合後のロットで1回だけ数え、スナップショットの lot_no には代表の計画の元のロットNoを書く
      （仕掛一覧は (計画No, ロットNo, 面) で突き合わせるため）
    """
    merge_map = load_lot_merge_map()
    rows = []
    evaluated = set()
    for lot_no in lot_nos:
        if merge_map.target_of(lot_no) in evaluated:
            continue
        try:
            lot_eval = evaluate_lot_status(lot_no, merge_map=merge_map)
        except ValueError:
            continue
        if "merged_lot_nos" in lot_eval:
            evaluated.add(lot_eval["lot_no"])   # 統合先と統合されるロットの両方が渡されても、1回だけ数える

        lot_completed = lot_eval["lot_completed"]

        group_lots = [lot_eval["lot_no"]] + lot_eval.get("merged_lot_nos", []) if "merged_lot_nos" in lot_eval else [lot_no]
        items_by_key = {}
        for group_lot in group_lots:
            for item in list_plan_items_by_lot(group_lot):
                key = (item["setup_file_no"], item["production_side"])
                items_by_key.setdefault(key, []).append(item)

        # file_actuals には、面2がある (元のロット, ファイルNo) の面1のキーが入らない（D-167）ので、面1・面2の二重計上は起きない。
        for key, file_actual in lot_eval["file_actuals"].items():
            wip_qty = file_actual - lot_completed
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
                "lot_no": representative["lot_no"] if "merged_lot_nos" in lot_eval else lot_no,
                "surplus_qty": wip_qty,
            })

    return rows