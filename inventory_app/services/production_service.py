import logging
from datetime import datetime
from models.kitting_plan import (
    find_plan_item_by_kitting_no,
    list_plan_items_by_lot,
    list_plan_items_for_all_lots,
    list_active_plan_items_by_kitting_no,
    find_opposite_side_plan,
    list_active_side_plans_for_file,
)
from models.production import (
    insert_daily_production,
    replace_daily_result,
    get_app_cumulative_qty,
    get_app_cumulative_qty_bulk,
    list_daily_production_by_kitting_no,
    get_production_daily_by_id,
    update_daily_production,
    delete_daily_production,
    list_daily_production_today,
    list_daily_production_range,
    apply_daily_production_changes,
)
from models.board_structure_master import get_board_structure
from models.lot_status_history import record_lot_status_snapshot
from models.scrap_records import list_scrap_summary_by_kitting_no
from models.ng_declarations import list_ng_declarations_latest

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


def _build_report_rows(records):
    """
    production_daily のレコードから、日報・月報共通の表示行と警告を作る。
    - 引落・仕掛・未完了は _evaluate_lot_status() の結果をそのまま使う（判定は全画面でそこに一本化）。判定できないロットは未完成扱い
    - lot_no は計画を引き直さず、実績の lot_id を使う（kitting_list_no は lot_no をまたいで重複するため）
    - 面2の計画があるロット・ファイルNoの面1の行は表示しない（D-8）。面1合計が面2合計を上回れば inconsistency_warnings に記録する（D-111）
    - 構成基板数が不足するロットには、不足分の「未確定」仮想行を追加する（kitting_list_no=""、数量は None でなく 0）
    戻り値は (report_rows, inconsistency_warnings, order_qty_inconsistency_warnings, unregistered_board_warnings,
    excess_file_no_warnings, board_count_inconsistency_warnings)。各要素の形と経緯は docs/domain/lot_completion.md。
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
            # 実績1件＝1行なので、実績の日付をそのまま使う（代表を選ぶ必要は無い）。
            "report_date": rec["report_date"],
        })

    # 面1の除外は「面2がアクティブな計画として存在するか」だけで判定する（D-8）。find_opposite_side_plan() は複数バッチを
    # 1件に絞る関数なので、ここで使うと誤った絞り込みに引き込まれる（D-111）。
    excluded_indices = set()
    file_keys_with_opposite = set()
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

        excluded_indices.add(idx)
        file_keys_with_opposite.add((lot_no, setup_file_no))

    # 不整合は、ロットNo・ファイルNoごとの面1合計と面2合計で比べる（D-111。計画どうしを1対1で対応づけないので、複数バッチでも誤検出しない）。
    inconsistency_warnings = []
    for lot_no, setup_file_no in sorted(file_keys_with_opposite):
        side_plans = list_active_side_plans_for_file(lot_no, setup_file_no)
        side1_plans = [p for p in side_plans if str(p.get("production_side") or "").strip() == "1"]
        side2_plans = [p for p in side_plans if str(p.get("production_side") or "").strip() == "2"]
        side1_total = sum(get_app_cumulative_qty(p["kitting_list_no"], lot_no) for p in side1_plans)
        side2_total = sum(get_app_cumulative_qty(p["kitting_list_no"], lot_no) for p in side2_plans)

        if side1_total > side2_total:
            inconsistency_warnings.append({
                "lot_no": lot_no,
                "setup_file_no": setup_file_no,
                "side1_total": side1_total,
                "side2_total": side2_total,
                "diff": side1_total - side2_total,
                "side1_kitting_list_nos": [p["kitting_list_no"] for p in side1_plans],
                "side2_kitting_list_nos": [p["kitting_list_no"] for p in side2_plans],
            })

    # 「NGの有無」の絞り込み用に、NG 実績か NG 申告がある (kitting_list_no, lot_no, production_side) を先にまとめて取得する（N+1 回避）。
    ng_exists_keys = {
        (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
        for s in list_scrap_summary_by_kitting_no() if s["record_count"] > 0
    } | {
        (d["kitting_list_no"], d["lot_no"] or "", str(d["production_side"]))
        for d in list_ng_declarations_latest() if (d["ng_qty"] or 0) > 0
    }

    report_rows = []
    seq = 1
    lot_status_cache = {}
    for idx, item in enumerate(enriched):
        if idx in excluded_indices:
            continue

        plan = item["plan"]
        kitting_list_no = item["kitting_list_no"]
        lot_no = item["lot_no"]
        daily_qty = item["daily_qty"]
        order_qty = plan["order_qty"] if plan else 0

        if lot_no not in lot_status_cache:
            try:
                lot_status_cache[lot_no] = evaluate_lot_status(lot_no)
            except ValueError:
                lot_status_cache[lot_no] = None
        lot_eval = lot_status_cache[lot_no]

        file_actual = None
        if lot_eval is not None and plan is not None:
            file_actual = lot_eval["file_actuals"].get(
                (plan["setup_file_no"], plan["production_side"])
            )

        if lot_eval is not None and file_actual is not None:
            completed = lot_eval["lot_completed"]
            surplus_qty = file_actual - completed
            lot_remaining = lot_eval["order_quantity"] - completed
            confirmation_note = lot_eval["status_remarks"]
            status_color_category = lot_eval["status_color_category"]
        else:
            completed = 0
            surplus_qty = daily_qty
            lot_remaining = order_qty
            # 判定できないロットは、誤った理由を出さないよう「確認事項」を空欄・色無しにする。
            confirmation_note = ""
            status_color_category = None

        side_for_ng_key = plan["production_side"] if plan else None
        has_ng = (kitting_list_no, lot_no or "", str(side_for_ng_key)) in ng_exists_keys

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
            "surplus_qty": surplus_qty,
            "lot_remaining": lot_remaining,
            "confirmation_note": confirmation_note,
            "status_color_category": status_color_category,
            "status": lot_eval["status"] if lot_eval is not None else None,
            "has_ng": has_ng,
            "report_date": item["report_date"],
        })
        seq += 1

    # 構成基板数チェックと発注数の不一致を、この帳票に実績があったロットごとに警告・仮想行へ振り分ける（判定は evaluate_lot_status() 側）。
    order_qty_inconsistency_warnings = []
    unregistered_board_warnings = []
    excess_file_no_warnings = []
    board_count_inconsistency_warnings = []
    for lot_no, lot_eval in lot_status_cache.items():
        if lot_eval is None:
            continue  # 計画自体が現存しない（ValueError）→ 判定不能としてスキップ

        if lot_eval["order_qty_inconsistent"]:
            order_qty_inconsistency_warnings.append({
                "lot_no": lot_no, "order_qty_values": lot_eval["order_qty_values"],
            })

        for board_name in lot_eval["unregistered_board_names"]:
            unregistered_board_warnings.append({
                "lot_no": lot_no,
                "board_name": board_name,
                "file_nos": lot_eval["visible_file_nos"],
            })

        status = lot_eval["status"]
        if status == "board_count_inconsistent":
            for board in lot_eval["boards"]:
                if board["board_count"] is None:
                    continue  # 未登録側は上のunregistered_board_warningsで扱い済み
                board_count_inconsistency_warnings.append({
                    "lot_no": lot_no,
                    "board_name": board["board_name"],
                    "board_count": board["board_count"],
                    "board_count_values": lot_eval["board_count_values"],
                    "file_nos": lot_eval["visible_file_nos"],
                })
        elif status == "excess":
            for board in lot_eval["boards"]:
                excess_file_no_warnings.append({
                    "lot_no": lot_no,
                    "board_name": board["board_name"],
                    "board_count": lot_eval["board_count"],
                    "file_nos": lot_eval["excess_file_nos"],
                })
        elif status == "shortfall":
            # 「未確定」仮想行の基板名。空だと画面で「未登録」と誤表示されるので、ロット内の登録済みの基板名の先頭を代表にする。
            registered_board_names = sorted({
                b["board_name"] for b in lot_eval["boards"] if b["board_count"] is not None
            })
            representative_board_name = registered_board_names[0] if registered_board_names else ""

            for entry in lot_eval["missing_file_entries"]:
                report_rows.append({
                    "seq": seq,
                    "kitting_list_no": "",
                    "file_no": entry["setup_file_no"],
                    "board_name": representative_board_name,
                    "production_side": None,
                    "mounting_line": None,
                    "lot_no": lot_no,
                    "daily_qty": 0,
                    "app_cumulative_qty": 0,
                    "order_qty": entry["order_qty"] or 0,
                    "lot_completed": entry["lot_completed"],
                    "surplus_qty": entry["surplus_qty"],
                    "lot_remaining": lot_eval["order_quantity"] - lot_eval["lot_completed"],
                    "confirmation_note": lot_eval["status_remarks"],
                    "status_color_category": lot_eval["status_color_category"],
                    "status": status,
                    # 仮想行には kitting_list_no が無いので、NG は無しとする。
                    "has_ng": False,
                    # 元の実績レコードが無いので、日付も無い。
                    "report_date": None,
                })
                seq += 1

    return (
        report_rows, inconsistency_warnings, order_qty_inconsistency_warnings,
        unregistered_board_warnings, excess_file_no_warnings,
        board_count_inconsistency_warnings,
    )


def build_daily_report():
    """本日入力された実績から、日報の表示データを作る。戻り値は _build_report_rows() と同じ。"""
    records = list_daily_production_today()
    return _build_report_rows(records)


def build_monthly_report(from_date: str, to_date: str):
    """期間（from_date〜to_date、両端を含む）の実績から、月報の表示データを作る。戻り値は _build_report_rows() と同じ。"""
    records = list_daily_production_range(from_date, to_date)
    return _build_report_rows(records)


def _compute_lot_completion(lot_no: str, plan_items: list, cumulative_by_pair: dict,
                              cutoff_date: str = None) -> dict:
    """
    calculate_lot_completion()・list_incomplete_lots() の共通ロジック。完成数＝(setup_file_no, production_side) ごとの実績合計の最小値。
    キーは2要素で、代入ではなく必ず加算する。同じファイルNo・面に別バッチが同時にアクティブなことがあり（実データで222件）、
    代入だと片方の実績が消える。3要素（kitting_list_no を含む）にすると完成数が少なく出る（CANONICAL_DESIGN_DECISIONS.md §5）。
    order_qty がファイルNoで食い違うロットがある（301件中3件）。そのときは先頭行の値を使い、order_qty_inconsistent で知らせる。
    cutoff_date は戻り値に記録するだけで、日付の絞り込みは呼び出し元が cumulative_by_pair で行う。
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
        "cutoff_date": cutoff_date,
    }


def calculate_lot_completion(lot_no: str):
    """
    lot_no 単位でロット完成数・未完成数を算出する。計算の詳細は
    _compute_lot_completion()のdocstring参照。
    """
    plan_items = list_plan_items_by_lot(lot_no)
    if not plan_items:
        raise ValueError(f"ロットNo. {lot_no} の計画が見つかりません。")

    # 実績はまとめて取得する（N+1 回避）。組に lot_no を含めるのは、kitting_list_no が lot_no をまたいで重複し、
    # 別ロットの実績まで合算してしまうため（実データで完成数の取り違えを確認）。
    kitting_list_no_lot_pairs = [(item["kitting_list_no"], lot_no) for item in plan_items]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs)

    return _compute_lot_completion(lot_no, plan_items, cumulative_by_pair)


# 引落を0扱いにする構成基板数チェックの status（日報・月報・仕掛数量抽出・ロット進捗チェックで共通）。
# 不足（shortfall）は構成がそろわないので引落を確定できない。未登録・不一致・超過は比べられないので実績ベースのまま（利用者の決定）。
DRAWDOWN_ZERO_STATUSES = {"shortfall"}

# 引落は実績ベースのままでも、構成基板数と比べられないので「確認・修正が必要」（赤字）とする status。
NEEDS_REVIEW_STATUSES = {"unregistered", "board_count_inconsistent", "excess"}


def _build_lot_status_remarks(status: str, board_count_values, shortfall_count: int,
                                unregistered_board_names: list) -> str:
    """
    「確認事項」欄の理由の文言を組み立てる（日報・月報・ロット進捗チェックで共通。文言はここにだけ書く）。
    複数の理由は " / " でつなぐ。status が unregistered 以外で一部の基板名が未登録なら「一部未登録」を足す。
    """
    parts = []
    if status == "unregistered":
        parts.append("構成基板数マスタ未登録(引落は未検証)")
    elif status == "board_count_inconsistent":
        values_str = ", ".join(f"{v:g}" for v in (board_count_values or []))
        parts.append(f"構成基板数がロット内で不一致 {values_str}(引落は未検証)")
    elif status == "excess":
        parts.append("実ファイルNo数がマスタの構成基板数を超過(引落は未検証)")
    elif status == "shortfall":
        parts.append(f"構成基板数不足: 未確定{shortfall_count}件(引落0)")

    if status != "unregistered" and unregistered_board_names:
        parts.append("一部未登録: " + "、".join(unregistered_board_names))

    return " / ".join(parts)


def _lot_status_color_category(status: str, unregistered_board_names: list):
    """
    「確認事項」欄の文字色の区分を返す: "needs_review"（赤）／"shortfall"（オレンジ）／None。
    一部未登録と不足が両方ある場合は、赤（確認・修正が必要）を優先する（優先順位の指定は無く、実装時に決めた）。
    """
    if status in NEEDS_REVIEW_STATUSES or unregistered_board_names:
        return "needs_review"
    if status in DRAWDOWN_ZERO_STATUSES:
        return "shortfall"
    return None


def _evaluate_lot_status(lot_no: str, plan_items: list, cumulative_by_pair: dict,
                           cutoff_date: str = None, board_structure_cache: dict = None) -> dict:
    """
    1ロットの構成基板数チェック（一致/不足/超過/未登録/board_count不一致）と進捗（引落・仕掛・未生産）を評価する。
    日報・月報・仕掛数量抽出・ロット進捗チェックで共通の、唯一の実装（以前は機能ごとに判定が食い違った）。
    判定の手順:
      1. _compute_lot_completion() で実績ベースの完成数と file_actuals を得る
      2. 面2があれば面1を除いた、ロット全体の file_no の集合を作る
      3. 基板名ごとに構成基板数マスタを引き、未登録の基板名をすべて集める（代表1件だけにしない）
      4. status: 登録が無い→unregistered、登録値が複数種類→board_count_inconsistent、
         それ以外は file_no の数と比べて match／shortfall／excess
      5. 引落: DRAWDOWN_ZERO_STATUSES（shortfall）なら0、それ以外は実績ベースの完成数
      6. file_no・面ごとの仕掛・未生産を、基板名ごとにまとめる
      7. shortfall なら、不足分の「未確定」エントリを足す
    cutoff_date は _compute_lot_completion() に渡すだけ。board_structure_cache を渡すと、マスタの検索結果を使い回す
    （1057ロットで5〜10秒かかっていたのを、1秒程度にするため）。戻り値のキーは docs/domain/lot_completion.md。
    """
    lot_info = _compute_lot_completion(lot_no, plan_items, cumulative_by_pair, cutoff_date=cutoff_date)

    second_side_setup_files = {
        it["setup_file_no"] for it in plan_items if str(it["production_side"]).strip() == "2"
    }
    visible_items = [
        it for it in plan_items
        if not (str(it["production_side"]).strip() == "1" and it["setup_file_no"] in second_side_setup_files)
    ]
    visible_file_nos = sorted({it["setup_file_no"] for it in visible_items})

    board_names = sorted({it["board_name"] for it in plan_items})
    registered_counts = {}
    unregistered_board_names = []
    for board_name in board_names:
        if board_structure_cache is not None:
            if board_name not in board_structure_cache:
                board_structure_cache[board_name] = get_board_structure(board_name) if board_name else None
            board_structure = board_structure_cache[board_name]
        else:
            board_structure = get_board_structure(board_name) if board_name else None
        if board_structure is None or board_structure.get("board_count") is None:
            unregistered_board_names.append(board_name)
        else:
            registered_counts[board_name] = board_structure["board_count"]

    distinct_registered_values = sorted(set(registered_counts.values()))

    if not registered_counts:
        status = "unregistered"
        board_count = None
        board_count_values = None
        shortfall_count = None
        excess_file_nos = None
    elif len(distinct_registered_values) > 1:
        status = "board_count_inconsistent"
        board_count = None
        board_count_values = distinct_registered_values
        shortfall_count = None
        excess_file_nos = None
    else:
        board_count = distinct_registered_values[0]
        board_count_values = None
        diff = int(round(board_count)) - len(visible_file_nos)
        if diff == 0:
            status, shortfall_count, excess_file_nos = "match", None, None
        elif diff > 0:
            status, shortfall_count, excess_file_nos = "shortfall", diff, None
        else:
            status, shortfall_count, excess_file_nos = "excess", None, visible_file_nos

    lot_completed = 0 if status in DRAWDOWN_ZERO_STATUSES else lot_info["completed_quantity"]

    boards = []
    for board_name in board_names:
        board_visible_items = [it for it in visible_items if it["board_name"] == board_name]

        files_grouped = {}
        for it in board_visible_items:
            key = (it["setup_file_no"], it["production_side"])
            files_grouped.setdefault(key, []).append(it)

        files = []
        for (setup_file_no, side), batch_items in files_grouped.items():
            file_actual = lot_info["file_actuals"].get((setup_file_no, side), 0)
            order_qty = sum(bi["order_qty"] for bi in batch_items)
            files.append({
                "setup_file_no": setup_file_no,
                "production_side": side,
                "order_qty": order_qty,
                "file_actual": file_actual,
                "lot_completed": lot_completed,
                "surplus_qty": file_actual - lot_completed,
                "not_produced_qty": order_qty - file_actual,
            })

        boards.append({
            "board_name": board_name,
            "board_count": registered_counts.get(board_name),
            "files": files,
        })

    missing_file_entries = []
    if status == "shortfall":
        for _ in range(shortfall_count):
            missing_file_entries.append({
                "setup_file_no": "未確定",
                "production_side": None,
                "order_qty": None,
                "file_actual": None,
                "lot_completed": 0,
                "surplus_qty": 0,
                "not_produced_qty": lot_info["order_quantity"],
            })

    status_remarks = _build_lot_status_remarks(
        status, board_count_values, shortfall_count, unregistered_board_names,
    )
    status_color_category = _lot_status_color_category(status, unregistered_board_names)

    return {
        "lot_no": lot_no,
        "status": status,
        "board_count": board_count,
        "board_count_values": board_count_values,
        "visible_file_nos": visible_file_nos,
        "shortfall_count": shortfall_count,
        "excess_file_nos": excess_file_nos,
        "unregistered_board_names": unregistered_board_names,
        "boards": boards,
        "missing_file_entries": missing_file_entries,
        "lot_completed": lot_completed,
        "status_remarks": status_remarks,
        "status_color_category": status_color_category,
        "order_quantity": lot_info["order_quantity"],
        "file_actuals": lot_info["file_actuals"],
        "order_qty_inconsistent": lot_info["order_qty_inconsistent"],
        "order_qty_values": lot_info["order_qty_values"],
        "cutoff_date": cutoff_date,
    }


def evaluate_lot_status(lot_no: str, cutoff_date: str = None) -> dict:
    """
    _evaluate_lot_status() の1ロット版。全ロットを評価するときは、これをループで呼ばず check_lot_progress() のように一括取得すること（N+1 になる）。
    cutoff_date を指定すると、その日以前の実績だけで評価する（計画と構成基板数マスタは現在の値のまま。日付では絞り込めない）。
    """
    plan_items = list_plan_items_by_lot(lot_no)
    if not plan_items:
        raise ValueError(f"ロットNo. {lot_no} の計画が見つかりません。")

    kitting_list_no_lot_pairs = [(item["kitting_list_no"], lot_no) for item in plan_items]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs, cutoff_date=cutoff_date)

    return _evaluate_lot_status(lot_no, plan_items, cumulative_by_pair, cutoff_date=cutoff_date)


def list_incomplete_lots():
    """
    全ロットを _evaluate_lot_status() で評価し、未完了のロットだけを返す（DB 新規作成時の引き継ぎ用）。lot_no 昇順。
    未完了の条件（利用者の決定）: shortfall は引落0なので常に未完了。それ以外は remaining_quantity > 0 なら未完了。
    計画は list_plan_items_for_all_lots() で一括取得する。list_active_plan_items() は面1や完了済みを除くので、完成数の計算には使えない。
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
        lot_eval = _evaluate_lot_status(lot_no, items, cumulative_by_pair)
        status = lot_eval["status"]
        order_quantity = lot_eval["order_quantity"]
        completed_quantity = lot_eval["lot_completed"]
        remaining_quantity = order_quantity - completed_quantity

        if status == "shortfall":
            is_incomplete = True  # 引落0扱いのため、remaining_quantityの値に関わらず無条件で未完了
        else:
            is_incomplete = remaining_quantity > 0

        if not is_incomplete:
            continue

        results.append({
            "lot_no": lot_no,
            "kitting_list_nos": sorted({item["kitting_list_no"] for item in items}),
            "order_quantity": order_quantity,
            "completed_quantity": completed_quantity,
            "remaining_quantity": remaining_quantity,
            "status": status,
        })

    results.sort(key=lambda r: r["lot_no"])
    return results


def check_lot_progress():
    """
    現在アクティブな全ロットについて _evaluate_lot_status() を呼ぶ（ロット進捗チェック用）。lot_no 昇順。
    日報・月報と違い、実績ではなく計画を起点にするので、対象期間に実績が無いロットの不足・未登録も検知できる。
    """
    plan_items = list_plan_items_for_all_lots()

    items_by_lot = {}
    for item in plan_items:
        items_by_lot.setdefault(item["lot_no"], []).append(item)

    kitting_list_no_lot_pairs = [
        (item["kitting_list_no"], item["lot_no"]) for item in plan_items
    ]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs)

    results = [
        _evaluate_lot_status(lot_no, items, cumulative_by_pair)
        for lot_no, items in items_by_lot.items()
    ]

    results.sort(key=lambda r: r["lot_no"])
    return results


def evaluate_all_lot_status(cutoff_date: str = None, board_structure_cache: dict = None) -> list:
    """
    check_lot_progress() と同じ処理に、cutoff_date と board_structure_cache を足したもの（日々の引落一覧用）。
    省略時は check_lot_progress() と同じ結果を返す。check_lot_progress() 自体を変えないよう、別の関数にした。
    """
    plan_items = list_plan_items_for_all_lots()

    items_by_lot = {}
    for item in plan_items:
        items_by_lot.setdefault(item["lot_no"], []).append(item)

    kitting_list_no_lot_pairs = [
        (item["kitting_list_no"], item["lot_no"]) for item in plan_items
    ]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs, cutoff_date=cutoff_date)

    results = [
        _evaluate_lot_status(
            lot_no, items, cumulative_by_pair,
            cutoff_date=cutoff_date, board_structure_cache=board_structure_cache,
        )
        for lot_no, items in items_by_lot.items()
    ]

    results.sort(key=lambda r: r["lot_no"])
    return results


def _pick_representative_plan_item(items: list):
    """
    同じ (setup_file_no, production_side) の複数バッチから、仕掛スナップショットの代表を1件選ぶ。
    生産予定日が最も新しいもの（手元に残る仕掛の実体に近い）。解釈できなければ最後の1件。
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