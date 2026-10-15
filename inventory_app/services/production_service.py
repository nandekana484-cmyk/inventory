import logging
from datetime import datetime
from models.kitting_plan import (
    find_plan_item_by_kitting_no,
    list_plan_items_by_lot,
    list_active_plan_items_by_kitting_no,
    find_opposite_side_plan,
    list_active_side_plans_for_file,
)
from models.production import (
    insert_daily_production,
    replace_daily_result,
    get_app_cumulative_qty,
    list_daily_production_by_kitting_no,
    get_production_daily_by_id,
    update_daily_production,
    delete_daily_production,
    apply_daily_production_changes,
    cancel_daily_production,
)
from models.ng_declarations import get_ng_declaration
from models.scrap_records import list_scrap_records_by_kitting_no
from models.wip_scrap_records import list_wip_scrap_records_by_kitting_no
from services.date_parsing import parse_plan_start_datetime
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
    """
    plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
    if not plan:
        raise ValueError(f"キッティングリストNo. {kitting_list_no}（ロットNo. {lot_no}）の計画が見つかりません。")

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


def register_opposite_side_daily_result(plan: dict, daily_qty: float, worker_id: str,
                                          report_date: str = None, record_history: bool = True):
    """
    plan の反対側の面に計画があれば、同じ数量をその面にも登録する（手入力と CSV 自動取込の共通処理）。反対側が無ければ何もしない。
    反対側に既存の実績があっても、確認せずに上書きする（選択中・取込元の面で確定済みのため）。
    反対側へ登録したら True、無ければ False。失敗したら例外を出す（元の面の登録は取り消さない。呼び出し元で try/except すること）。
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
            report_date=report_date, check_duplicate=True, record_history=record_history,
        )
    except DailyResultAlreadyExists:
        overwrite_daily_result(
            opposite_kitting_no, opposite_lot_no, daily_qty, worker_id, report_date=report_date,
            record_history=record_history,
        )
    return True


def _resulting_report_date(existing_records: list, report_date: str = None) -> str:
    """登録・上書きの後の日付。指定が無ければ、上書きは既存行の日付を引き継ぎ（replace_daily_result()）、新規は今日。"""
    if report_date:
        return report_date
    if existing_records and existing_records[0]["report_date"]:
        return existing_records[0]["report_date"]
    return datetime.now().strftime("%Y-%m-%d")


def preview_opposite_side_overwrites(entries: list) -> dict:
    """
    entries（[(key, plan, daily_qty, report_date), ...]、登録する順）を register_actual_and_ng() で順に登録したとき、
    register_opposite_side_daily_result() が反対側の面の既存の実績の数量か日付を変える行を返す（D-126。DB には書き込まない）。
    確認の対象は面2の登録で面1が変わる場合だけ（利用者の決定の範囲）。面1の登録で面2が変わる逆向きは対象にしない。
    同じ一括の中で前の行が書く実績も、後の行の「既存の実績」として数える（面2の計画が2件・面1の計画が1件など）。
    戻り値は {key: {"kitting_list_no", "lot_no", "production_side", "old": [(日付, 数量), ...], "new_date", "new_qty"}}。
    反対側に計画・実績が無い行と、変わらない（同じ数量・日付の1件だけ）行は含めない。
    """
    written = {}  # (kitting_list_no, lot_no) -> この一括の中で書かれる予定の [(日付, 数量)]
    changes = {}

    def records_of(kitting_list_no, lot_no):
        if (kitting_list_no, lot_no) in written:
            return written[(kitting_list_no, lot_no)]
        return [(r["report_date"], r["daily_qty"]) for r in list_daily_production_by_kitting_no(kitting_list_no, lot_no)]

    for key, plan, daily_qty, report_date in entries:
        primary_key = (plan["kitting_list_no"], plan["lot_no"])
        primary_records = [{"report_date": d, "daily_qty": q} for d, q in records_of(*primary_key)]

        side = str(plan.get("production_side") or "").strip()
        opposite = None
        if side == "2":
            opposite = find_opposite_side_plan(
                plan.get("lot_no"), plan.get("setup_file_no"), side,
                current_plan_start_datetime=plan.get("plan_start_datetime"),
            )
        if opposite is not None:
            opposite_key = (opposite["kitting_list_no"], opposite["lot_no"])
            old = records_of(*opposite_key)
            new_date = _resulting_report_date([{"report_date": d} for d, _q in old], report_date)
            if old and old != [(new_date, daily_qty)]:
                changes[key] = {
                    "kitting_list_no": opposite["kitting_list_no"], "lot_no": opposite["lot_no"],
                    "production_side": str(opposite.get("production_side") or "").strip(),
                    "old": old, "new_date": new_date, "new_qty": daily_qty,
                }
            written[opposite_key] = [(new_date, daily_qty)]
        written[primary_key] = [(_resulting_report_date(primary_records, report_date), daily_qty)]
    return changes


def preview_opposite_side_overwrite(plan: dict, daily_qty: float, report_date: str = None):
    """preview_opposite_side_overwrites() の1件版。変わるならその内容、変わらなければ None。"""
    return preview_opposite_side_overwrites([(None, plan, daily_qty, report_date)]).get(None)


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


def build_linked_correction_preview(prod_log_id: int, action: str, new_daily_qty: float = None):
    """
    実績の修正・削除で、両面の計画があるロットの反対側の面の実績を連動させる内容を組み立てる（D-112。DB には書き込まない）。
    相手の実績の特定:
      a. 反対側の面の実績のうち、修正・削除前の数量・日付が完全一致するものが1件だけなら、それを相手にする
      b. a で決まらなければ、登録時と同じ find_opposite_side_plan() で計画を1件に絞る。実績が2件以上なら連動しない（ambiguous）
      c. 相手の計画に実績が無ければ、修正なら新規登録、削除なら何もしない
    戻り値の形と各キーの意味は docs/domain/lot_completion.md。
    """
    if action not in ("update", "delete"):
        raise ValueError(f"未対応のaction: {action}")

    row = get_production_daily_by_id(prod_log_id)
    if row is None:
        raise ValueError(f"実績（prod_log_id={prod_log_id}）が見つかりません。")

    kitting_list_no = row["kitting_list_no"]
    lot_no = row["lot_id"]
    old_daily_qty = row["daily_qty"]
    report_date = row["report_date"]

    plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
    primary = {
        "prod_log_id": prod_log_id, "kitting_list_no": kitting_list_no, "lot_no": lot_no,
        "old_daily_qty": old_daily_qty, "new_daily_qty": new_daily_qty if action == "update" else None,
        "report_date": report_date, "plan": plan,
    }
    result = {"primary": primary, "has_opposite_plan": False, "secondary": None}

    if plan is None:
        return result
    side = str(plan.get("production_side") or "").strip()
    if side not in ("1", "2"):
        return result
    setup_file_no = plan.get("setup_file_no")

    opposite_side_plans = [
        p for p in list_active_side_plans_for_file(lot_no, setup_file_no)
        if str(p.get("production_side") or "").strip() != side
    ]
    if not opposite_side_plans:
        return result  # 片面だけの計画。連動対象なし（従来どおり）

    result["has_opposite_plan"] = True

    # a. 修正・削除前の数量・日付が一致する反対側の実績を探す
    candidate_records = []
    for p in opposite_side_plans:
        candidate_records += list_daily_production_by_kitting_no(p["kitting_list_no"], lot_no)
    matching_a = [
        r for r in candidate_records
        if r["daily_qty"] == old_daily_qty and r["report_date"] == report_date
    ]

    secondary = None
    if len(matching_a) == 1:
        target = matching_a[0]
        secondary = {
            "method": "a", "ambiguous": False,
            "kitting_list_no": target["kitting_list_no"], "prod_log_id": target["prod_log_id"],
            "old_daily_qty": target["daily_qty"], "report_date": target["report_date"],
            "pre_existing_mismatch": False,
        }
    else:
        # b. 登録時の自動入力・NG入力欄の相手探しと同じ方法で1件に絞り込む
        opposite_plan = find_opposite_side_plan(
            lot_no, setup_file_no, side, current_plan_start_datetime=plan.get("plan_start_datetime"),
        )
        if opposite_plan is None:
            # 想定外（find_opposite_side_plan() の条件の差で見つからない等）。安全側に倒し、連動しない。
            result["secondary"] = {"method": "b", "ambiguous": True}
            return result

        existing = list_daily_production_by_kitting_no(opposite_plan["kitting_list_no"], lot_no)
        if len(existing) == 0:
            # c. 相手の計画に実績が無い
            secondary = {
                "method": "b", "ambiguous": False,
                "kitting_list_no": opposite_plan["kitting_list_no"], "prod_log_id": None,
                "old_daily_qty": None, "report_date": report_date, "pre_existing_mismatch": False,
                "plan_item_id": opposite_plan["plan_item_id"], "board_name": opposite_plan["board_name"],
            }
        elif len(existing) == 1:
            target = existing[0]
            pre_existing_mismatch = not (
                target["daily_qty"] == old_daily_qty and target["report_date"] == report_date
            )
            secondary = {
                "method": "b", "ambiguous": False,
                "kitting_list_no": target["kitting_list_no"], "prod_log_id": target["prod_log_id"],
                "old_daily_qty": target["daily_qty"], "report_date": target["report_date"],
                "pre_existing_mismatch": pre_existing_mismatch,
            }
        else:
            # 「1計画=1レコード」ルールに反する想定外のデータ。安全側に倒し連動しない。
            result["secondary"] = {"method": "b", "ambiguous": True}
            return result

    if action == "update":
        secondary["new_daily_qty"] = new_daily_qty
        secondary["action"] = "update" if secondary.get("prod_log_id") else "insert"
    else:
        secondary["action"] = "delete" if secondary.get("prod_log_id") else "noop"

    result["secondary"] = secondary
    return result


def apply_linked_correction(preview: dict, action: str, worker_id: str, record_history: bool = True):
    """
    build_linked_correction_preview() の内容（利用者が承認したもの）を、1つのトランザクションで確定する（D-112）。
    secondary が None か ambiguous なら、primary だけを変更する。
    """
    primary = preview["primary"]
    secondary = preview.get("secondary")

    changes = []
    if action == "update":
        changes.append({
            "action": "update", "prod_log_id": primary["prod_log_id"],
            "daily_qty": primary["new_daily_qty"], "report_date": primary["report_date"],
        })
    else:
        changes.append({"action": "delete", "prod_log_id": primary["prod_log_id"]})

    if secondary and not secondary.get("ambiguous") and secondary["action"] != "noop":
        if secondary["action"] == "update":
            changes.append({
                "action": "update", "prod_log_id": secondary["prod_log_id"],
                "daily_qty": secondary["new_daily_qty"], "report_date": secondary["report_date"],
            })
        elif secondary["action"] == "insert":
            changes.append({
                "action": "insert", "plan_item_id": secondary["plan_item_id"],
                "kitting_list_no": secondary["kitting_list_no"], "lot_id": primary["lot_no"],
                "group_id": secondary.get("board_name"), "report_date": secondary["report_date"],
                "daily_qty": secondary["new_daily_qty"], "worker_id": worker_id,
            })
        elif secondary["action"] == "delete":
            changes.append({"action": "delete", "prod_log_id": secondary["prod_log_id"]})

    apply_daily_production_changes(changes)

    # 反対側も同じ lot_no なので、履歴は1回の記録で両面分を反映する。
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
    - 反対側の面の実績は、今の削除と同じく build_linked_correction_preview() で特定し、連動して取り消す（D-112）
    - NG申告は、取り消す計画と反対側の面の計画の分を取り消す（面2の NG は面1にも加算されて保存されているため）
    - そのNG申告の計画・面に NG展開（scrap_records）が、または取り消す実績の計画・面に仕掛部品展開（wip_scrap_records）が
      確定済みなら、取り消せない（blockers に理由を入れる）
    - 登録待ちに戻す行は1行（取り消す実績の分だけ）。日付は実績CSVの書式にする
    戻り値: {"primary", "opposite"（None 可）, "ng_declarations", "blockers", "restored_row"}
    """
    linked = build_linked_correction_preview(prod_log_id, "delete")
    primary = linked["primary"]
    plan = primary["plan"]
    lot_no = primary["lot_no"]
    side = str(plan.get("production_side") or "").strip() if plan else ""
    row = get_production_daily_by_id(prod_log_id)

    primary_info = {
        "prod_log_id": prod_log_id, "kitting_list_no": primary["kitting_list_no"], "lot_no": lot_no,
        "production_side": side, "daily_qty": primary["old_daily_qty"], "report_date": primary["report_date"],
    }
    opposite_info = None
    ng_targets = [(primary["kitting_list_no"], side)] if side in ("1", "2") else []
    secondary = linked.get("secondary")
    if secondary and not secondary.get("ambiguous") and secondary.get("kitting_list_no"):
        opposite_side = "1" if side == "2" else "2"
        ng_targets.append((secondary["kitting_list_no"], opposite_side))
        if secondary.get("action") == "delete":
            opposite_info = {
                "prod_log_id": secondary["prod_log_id"], "kitting_list_no": secondary["kitting_list_no"],
                "lot_no": lot_no, "production_side": opposite_side,
                "daily_qty": secondary["old_daily_qty"], "report_date": secondary["report_date"],
            }

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
    for target in [primary_info] + ([opposite_info] if opposite_info else []):
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
        "primary": primary_info, "opposite": opposite_info, "ng_declarations": ng_declarations,
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
    if preview["opposite"]:
        prod_log_ids.append(preview["opposite"]["prod_log_id"])
    ng_keys = [(d["kitting_list_no"], int(d["production_side"]), d["lot_no"]) for d in preview["ng_declarations"]]
    pending_row_id = cancel_daily_production(
        prod_log_ids, ng_keys,
        restored_row=preview["restored_row"] if return_to_staging else None,
        restored_source=CANCELLED_REGISTRATION_SOURCE, imported_by=imported_by,
    )
    # 反対側も同じ lot_no なので、履歴は1回の記録で両面分を反映する（記録に失敗しても取り消しは失敗させない）。
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
    - 面2の実績がある file_no の面1は除く（同じ基板の仕掛を、面1・面2で二重に数えないため）
    - 複数バッチは、生産予定日が最も新しいものを代表にする（スナップショットは1行1 kitting_list_no のため）
    - 計画が見つからないロットは飛ばす。shortfall の「未確定」は対象外（実在の kitting_list_no が無いため）
    """
    rows = []
    for lot_no in lot_nos:
        try:
            lot_eval = evaluate_lot_status(lot_no)
        except ValueError:
            continue

        lot_completed = lot_eval["lot_completed"]

        items_by_key = {}
        for item in list_plan_items_by_lot(lot_no):
            key = (item["setup_file_no"], item["production_side"])
            items_by_key.setdefault(key, []).append(item)

        second_side_files = {
            file_no for (file_no, side) in lot_eval["file_actuals"]
            if str(side).strip() == "2"
        }

        for key, file_actual in lot_eval["file_actuals"].items():
            file_no, side = key
            if str(side).strip() == "1" and file_no in second_side_files:
                continue

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
                "lot_no": lot_no,
                "surplus_qty": wip_qty,
            })

    return rows