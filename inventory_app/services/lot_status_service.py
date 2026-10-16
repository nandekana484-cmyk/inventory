"""
ロット状態の評価（ロット完成数、構成基板数との照合、引落・仕掛・未生産、未完了ロット、ロット進捗チェック）と、ロット履歴の記録。
段階5で services/production_service.py から移した（関数の中身は変えていない）。production_service.py からも re-export している。
完成数の集計キーは (setup_file_no, production_side) の2要素で、代入ではなく加算する（docs/fragile_spots.md 2）。
ロットの統合（D-136・D-150）: 統合したロットは「統合後のロット」（統合先のロットNo）単位でまとめて評価する。
統合の無いロットの計算は変えない（merge_ctx が None の経路は統合の前と同じ）。業務ルールは docs/domain/lot_merge.md。
"""
from models.kitting_plan import list_plan_items_for_all_lots
from models.production import get_app_cumulative_qty_bulk, get_app_cumulative_qty
from models.board_structure_master import get_board_structure
from models.lot_status_history import insert_lot_status_snapshot
from services.lot_merge_service import load_lot_merge_map
# 統合後のロットの行を作る部分は lot_merge_items_service.py へ移した（中身は同じ）。旧位置からも参照できるよう import しておく。
from services.lot_merge_items_service import (  # noqa: F401 （re-export）
    _representative_order_qty, _side_of, _merge_lot_items, _group_items_by_merged_lot,
    _load_lot_items, _pairs_of, _merge_result_fields,
)


def record_lot_status_snapshot(lot_no: str, trigger_source: str) -> int:
    """
    evaluate_lot_status(lot_no) を呼び、その結果を lot_status_history に1行記録する（models.lot_status_history.insert_lot_status_snapshot()）。
    戻り値：INSERTした行のid。段階4で models/lot_status_history.py から移した（models が services を import していたため）。
    失敗時（対象lot_noの計画が見つからない場合のValueError、DB書き込みエラー等）は例外をそのまま送出する。
    実績の登録・修正を失敗させたくない呼び出し元は、_record_lot_status_snapshot_safely() のように try/except で捕まえること。
    統合したロットは、統合後のロット（統合先のロットNo）の状態として記録する（D-150）。
    """
    lot_status = evaluate_lot_status(lot_no)
    return insert_lot_status_snapshot(lot_status["lot_no"], lot_status, trigger_source)


def _compute_lot_completion(lot_no: str, plan_items: list, cumulative_by_pair: dict,
                              cutoff_date: str = None, merge_ctx: dict = None) -> dict:
    """
    calculate_lot_completion()・list_incomplete_lots() の共通ロジック。完成数＝(setup_file_no, production_side) ごとの実績合計の最小値。
    キーは2要素で、代入ではなく必ず加算する。同じファイルNo・面に別バッチが同時にアクティブなことがあり（実データで222件）、
    代入だと片方の実績が消える。3要素（kitting_list_no を含む）にすると完成数が少なく出る（CANONICAL_DESIGN_DECISIONS.md §5）。
    order_qty がファイルNoで食い違うロットがある（301件中3件）。そのときは先頭行の値を使い、order_qty_inconsistent で知らせる。
    cutoff_date は戻り値に記録するだけで、日付の絞り込みは呼び出し元が cumulative_by_pair で行う。
    merge_ctx（統合したロット、_merge_lot_items()）があれば、実績を計画ごとの元のロットNoで引き、side1_extra を面1にも足し、
    side1_replaced の面1の実績は使わない（D-150・D-157）。
    """
    order_qty_values = sorted({item["order_qty"] for item in plan_items})
    order_qty_inconsistent = len(order_qty_values) > 1
    order_qty = plan_items[0]["order_qty"] if order_qty_inconsistent else order_qty_values[0]

    file_actuals = {}
    for item in plan_items:
        kitting_list_no = item["kitting_list_no"]
        key = (item["setup_file_no"], item["production_side"])
        source_lot = lot_no if merge_ctx is None else item["lot_no"]
        if merge_ctx is not None and (kitting_list_no, source_lot) in merge_ctx["side1_replaced"]:
            continue
        file_actuals[key] = file_actuals.get(key, 0) + cumulative_by_pair[(kitting_list_no, source_lot)]
    if merge_ctx is not None:
        for pair, side1_key in merge_ctx["side1_extra"]:
            file_actuals[side1_key] = file_actuals.get(side1_key, 0) + cumulative_by_pair[pair]

    completed = min(file_actuals.values())
    remaining = order_qty - completed

    result = {
        "lot_no": lot_no,
        "order_quantity": order_qty,
        "order_qty_inconsistent": order_qty_inconsistent,
        "order_qty_values": order_qty_values,
        "completed_quantity": completed,
        "remaining_quantity": remaining,
        "file_actuals": file_actuals,
        "cutoff_date": cutoff_date,
    }
    if merge_ctx is not None:
        result.update(_merge_result_fields(merge_ctx))
    return result


def calculate_lot_completion(lot_no: str):
    """
    lot_no 単位でロット完成数・未完成数を算出する。計算の詳細は
    _compute_lot_completion()のdocstring参照。統合したロットなら、統合後のロットの値を返す（D-150）。
    """
    lot_no, plan_items, merge_ctx = _load_lot_items(lot_no, load_lot_merge_map())

    # 実績はまとめて取得する（N+1 回避）。組に lot_no を含めるのは、kitting_list_no が lot_no をまたいで重複し、
    # 別ロットの実績まで合算してしまうため（実データで完成数の取り違えを確認）。
    kitting_list_no_lot_pairs = _pairs_of(lot_no, plan_items, merge_ctx)
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs)

    return _compute_lot_completion(lot_no, plan_items, cumulative_by_pair, merge_ctx=merge_ctx)


# 引落を0扱いにする構成基板数チェックの status（日報・月報・仕掛数量抽出・ロット進捗チェックで共通）。
# 不足（shortfall）は構成がそろわないので引落を確定できない。未登録・不一致・超過は比べられないので実績ベースのまま（利用者の決定）。
DRAWDOWN_ZERO_STATUSES = {"shortfall"}


# 引落は実績ベースのままでも、構成基板数と比べられないので「確認・修正が必要」（赤字）とする status。
NEEDS_REVIEW_STATUSES = {"unregistered", "board_count_inconsistent", "excess"}

# 発注数の食い違いの文言（ロット進捗チェックの備考に出す。実績レポートの「確認事項」には出さない。D-127）。
ORDER_QTY_REMARK_BETWEEN_FILES = "発注数不一致(ファイル間)"
ORDER_QTY_REMARK_WITHIN_FILE = "発注数不一致(同一ファイル内)"


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
                           cutoff_date: str = None, board_structure_cache: dict = None,
                           merge_ctx: dict = None) -> dict:
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
    merge_ctx（統合したロット）では、plan_items は _merge_lot_items() でそろえた行。面1を隠す判定・基板名・発注数の食い違いも
    統合後のロットで判定し、ファイルNoの行に元のロットNo（source_lot_nos）を足す（D-150）。
    """
    lot_info = _compute_lot_completion(lot_no, plan_items, cumulative_by_pair, cutoff_date=cutoff_date,
                                       merge_ctx=merge_ctx)

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
            # 発注数はロットの値が各バッチに同じ値で入っているので、バッチをまたいで足さない（D-121）。
            # 値が食い違うときは決められないので、発注数・未生産を空欄（None）にして知らせる。
            batch_order_qty_values = {bi["order_qty"] for bi in batch_items}
            order_qty_inconsistent = len(batch_order_qty_values) > 1
            order_qty = None if order_qty_inconsistent else batch_order_qty_values.pop()
            file_entry = {
                "setup_file_no": setup_file_no,
                "production_side": side,
                "order_qty": order_qty,
                "order_qty_inconsistent": order_qty_inconsistent,
                "file_actual": file_actual,
                "lot_completed": lot_completed,
                "surplus_qty": file_actual - lot_completed,
                "not_produced_qty": None if order_qty is None else order_qty - file_actual,
            }
            if merge_ctx is not None:
                file_entry["source_lot_nos"] = sorted({bi["lot_no"] for bi in batch_items})
            files.append(file_entry)

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

    # 発注数がファイルNoによって違うか（表示している行＝面1を除いた後で比べる）。値はファイルNoごとのまま表示する（D-127）。
    order_qty_sets_by_file = {}
    for it in visible_items:
        order_qty_sets_by_file.setdefault(it["setup_file_no"], set()).add(it["order_qty"])
    order_qty_inconsistent_between_files = len({frozenset(v) for v in order_qty_sets_by_file.values()}) > 1
    order_qty_inconsistent_within_file = any(
        f["order_qty_inconsistent"] for board in boards for f in board["files"]
    )
    # ロット進捗チェック・日々の引落一覧の文字色。発注数の食い違いは構成基板数不一致と同じ赤にする。
    # 実績レポートは status_color_category を使い続ける（発注数の食い違いを出さない）。
    progress_color_category = (
        "needs_review" if order_qty_inconsistent_between_files or order_qty_inconsistent_within_file
        else status_color_category
    )

    result = {
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
        "order_qty_inconsistent_between_files": order_qty_inconsistent_between_files,
        "progress_color_category": progress_color_category,
        "order_quantity": lot_info["order_quantity"],
        "file_actuals": lot_info["file_actuals"],
        "order_qty_inconsistent": lot_info["order_qty_inconsistent"],
        "order_qty_values": lot_info["order_qty_values"],
        "cutoff_date": cutoff_date,
    }
    if merge_ctx is not None:
        result.update(_merge_result_fields(merge_ctx))
    return result


def evaluate_lot_status(lot_no: str, cutoff_date: str = None, merge_map=None) -> dict:
    """
    _evaluate_lot_status() の1ロット版。全ロットを評価するときは、これをループで呼ばず check_lot_progress() のように一括取得すること（N+1 になる）。
    cutoff_date を指定すると、その日以前の実績だけで評価する（計画と構成基板数マスタは現在の値のまま。日付では絞り込めない）。
    統合したロット（統合されるロットNoを渡した場合も）は、統合後のロットを評価する（戻り値の lot_no は統合先。D-150）。
    merge_map（services.lot_merge_service.LotMergeMap）を渡すとそれを使う（省略時は今の DB の統合の情報。試算では仮の情報を渡す）。
    """
    merge_map = merge_map if merge_map is not None else load_lot_merge_map()
    lot_no, plan_items, merge_ctx = _load_lot_items(lot_no, merge_map)

    kitting_list_no_lot_pairs = _pairs_of(lot_no, plan_items, merge_ctx)
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs, cutoff_date=cutoff_date)

    return _evaluate_lot_status(lot_no, plan_items, cumulative_by_pair, cutoff_date=cutoff_date, merge_ctx=merge_ctx)


def is_lot_incomplete(lot_eval: dict) -> bool:
    """
    _evaluate_lot_status() の結果から、ロットが未完了かを返す（未完了ロットの抽出・完成済みロットの自動登録の除外で共通。D-130）。
    未完了の条件（利用者の決定）: shortfall は引落0なので常に未完了。それ以外は 発注数 − 完成数 > 0 なら未完了。
    """
    if lot_eval["status"] in DRAWDOWN_ZERO_STATUSES:
        return True
    return lot_eval["order_quantity"] - lot_eval["lot_completed"] > 0


def lot_completion_state(lot_no: str, cache: dict = None):
    """
    ロットが完成済みかを、未完了ロットの抽出と同じ定義（is_lot_incomplete()）で返す（D-130）。計画が無ければ None。
    同じ (ファイルNo, 面) の中で発注数が食い違うロット（発注数が空欄になる。D-121）は、完成済みと判定しない。
    戻り値: {"completed": bool, "lot_completed", "order_quantity", "file_actuals"}。cache（lot_no→結果）を渡すと使い回す。
    後日のロット統合では、evaluate_lot_status() を統合後のロットに切り替えればここも切り替わる。
    """
    if cache is not None and lot_no in cache:
        return cache[lot_no]
    try:
        lot_eval = evaluate_lot_status(lot_no)
    except ValueError:
        state = None
    else:
        within_file_inconsistent = any(
            f["order_qty_inconsistent"] for board in lot_eval["boards"] for f in board["files"]
        )
        state = {
            "completed": not within_file_inconsistent and not is_lot_incomplete(lot_eval),
            "lot_completed": lot_eval["lot_completed"],
            "order_quantity": lot_eval["order_quantity"],
            "file_actuals": lot_eval["file_actuals"],
        }
        if "display_lot_no" in lot_eval:   # 確認の文で「500500（+500500-）」と出すため（D-159）
            state["display_lot_no"] = lot_eval["display_lot_no"]
    if cache is not None:
        cache[lot_no] = state
    return state


def preview_completed_lot_registration(plan: dict, daily_qty: float):
    """
    plan（計画1件）に daily_qty を登録する前に、そのロットが完成済みなら確認用の内容を返す（D-130）。完成していなければ None。
    new_total は、登録した後の、その計画の (ファイルNo, 面) の実績の合計（完成数の計算に使う値。1計画＝1レコードなので既存分は置き換わる）。
    その計画に既に実績がある（数量を直す）場合は、直した後の合計が発注数を超え、かつ今より増えるときだけ返す（D-133）。
    """
    state = lot_completion_state(plan["lot_no"])
    if state is None or not state["completed"]:
        return None
    file_key = (plan["setup_file_no"], plan["production_side"])
    current_total = state["file_actuals"].get(file_key, 0)
    own_existing = get_app_cumulative_qty(plan["kitting_list_no"], plan["lot_no"])
    new_total = current_total - own_existing + daily_qty
    if own_existing and not (new_total > state["order_quantity"] and new_total > current_total):
        return None
    return {
        "lot_no": state.get("display_lot_no", plan["lot_no"]),
        "lot_completed": state["lot_completed"],
        "order_quantity": state["order_quantity"],
        "new_total": new_total,
    }


def preview_completed_lot_correction(plan: dict, old_daily_qty: float, new_daily_qty: float):
    """
    実績修正の「修正」で数量を増やす前に、ロット（統合後のロット）が修正の前に完成済みなら確認用の内容を返す（D-156）。
    形は preview_completed_lot_registration() と同じ。減らす・変えない修正、未完成のロット、計画が無いときは None。
    """
    if plan is None or new_daily_qty <= old_daily_qty:
        return None
    state = lot_completion_state(plan["lot_no"])
    if state is None or not state["completed"]:
        return None
    current_total = state["file_actuals"].get((plan["setup_file_no"], plan["production_side"]), 0)
    return {"lot_no": state.get("display_lot_no", plan["lot_no"]), "lot_completed": state["lot_completed"],
            "order_quantity": state["order_quantity"], "new_total": current_total - old_daily_qty + new_daily_qty}


def list_incomplete_lots():
    """
    全ロットを _evaluate_lot_status() で評価し、未完了のロットだけを返す（DB 新規作成時の引き継ぎ用）。lot_no 昇順。
    未完了の条件（利用者の決定）: shortfall は引落0なので常に未完了。それ以外は remaining_quantity > 0 なら未完了。
    計画は list_plan_items_for_all_lots() で一括取得する。list_active_plan_items() は面1や完了済みを除くので、完成数の計算には使えない。
    """
    plan_items = list_plan_items_for_all_lots()

    items_by_lot = _group_items_by_merged_lot(plan_items, load_lot_merge_map())

    kitting_list_no_lot_pairs = [
        (item["kitting_list_no"], item["lot_no"]) for item in plan_items
    ]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs)

    results = []
    for lot_no, (items, merge_ctx) in items_by_lot.items():
        lot_eval = _evaluate_lot_status(lot_no, items, cumulative_by_pair, merge_ctx=merge_ctx)
        status = lot_eval["status"]
        order_quantity = lot_eval["order_quantity"]
        completed_quantity = lot_eval["lot_completed"]
        remaining_quantity = order_quantity - completed_quantity

        if not is_lot_incomplete(lot_eval):
            continue

        entry = {
            "lot_no": lot_no,
            "kitting_list_nos": sorted({item["kitting_list_no"] for item in items}),
            "order_quantity": order_quantity,
            "completed_quantity": completed_quantity,
            "remaining_quantity": remaining_quantity,
            "status": status,
        }
        if merge_ctx is not None:
            # 引き継ぎは元のロットごとに計画・実績を写すので、元のロットNoごとの計画Noを渡す（D-150）。
            entry["merged_lot_nos"] = list(merge_ctx["member_lots"])
            entry["kitting_list_nos_by_lot"] = {}
            for item in items:
                entry["kitting_list_nos_by_lot"].setdefault(item["lot_no"], set()).add(item["kitting_list_no"])
            entry["kitting_list_nos_by_lot"] = {k: sorted(v) for k, v in entry["kitting_list_nos_by_lot"].items()}
        results.append(entry)

    results.sort(key=lambda r: r["lot_no"])
    return results


def check_lot_progress():
    """
    現在アクティブな全ロットについて _evaluate_lot_status() を呼ぶ（ロット進捗チェック用）。lot_no 昇順。
    日報・月報と違い、実績ではなく計画を起点にするので、対象期間に実績が無いロットの不足・未登録も検知できる。
    """
    plan_items = list_plan_items_for_all_lots()

    items_by_lot = _group_items_by_merged_lot(plan_items, load_lot_merge_map())

    kitting_list_no_lot_pairs = [
        (item["kitting_list_no"], item["lot_no"]) for item in plan_items
    ]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs)

    results = [
        _evaluate_lot_status(lot_no, items, cumulative_by_pair, merge_ctx=merge_ctx)
        for lot_no, (items, merge_ctx) in items_by_lot.items()
    ]

    results.sort(key=lambda r: r["lot_no"])
    return results


def evaluate_all_lot_status(cutoff_date: str = None, board_structure_cache: dict = None) -> list:
    """
    check_lot_progress() と同じ処理に、cutoff_date と board_structure_cache を足したもの（日々の引落一覧用）。
    省略時は check_lot_progress() と同じ結果を返す。check_lot_progress() 自体を変えないよう、別の関数にした。
    """
    plan_items = list_plan_items_for_all_lots()

    items_by_lot = _group_items_by_merged_lot(plan_items, load_lot_merge_map())

    kitting_list_no_lot_pairs = [
        (item["kitting_list_no"], item["lot_no"]) for item in plan_items
    ]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs, cutoff_date=cutoff_date)

    results = [
        _evaluate_lot_status(
            lot_no, items, cumulative_by_pair,
            cutoff_date=cutoff_date, board_structure_cache=board_structure_cache, merge_ctx=merge_ctx,
        )
        for lot_no, (items, merge_ctx) in items_by_lot.items()
    ]

    results.sort(key=lambda r: r["lot_no"])
    return results
