"""
実績CSV取込の自動登録（Shift+S／Shift+Q）の判定と、保留行の読み込み。GUI に依存しない。

リファクタリング段階1で ui/production/production_import_staging_window.py から移した（関数の中身は変えていない）。
旧位置からは re-export しているので、既存の呼び出し側はそのまま動く。
判定の優先順位と各理由の意味は docs/domain/production_import_staging.md。
"""
from services.date_parsing import parse_flexible_date, parse_plan_start_datetime
from services.production_import_service import (
    normalize_product_name,
    group_active_plan_items_by_lot,
)
from models.kitting_plan import (
    find_matching_plan_items, classify_side1_only_plan,
    SIDE1_ONLY_CLASS_WAITING_SIDE2, SIDE1_ONLY_CLASS_UNREGISTERED,
)
from models.production import list_daily_production_by_kitting_no
from models.production_import_staging import list_pending_csv_import_rows
from services.lot_status_service import lot_completion_state
from services.lot_merge_service import active_plan_lot_nos, find_similar_lot_no, match_lot_of

# Shift+S・Shift+Q で許容する、払い出し日と生産予定日の差（日付のみで比較）。
# 両ショートカットの違いは、この日数の違いだけにする（利用者の決定、D-101）。
AUTO_REGISTER_MAX_DAYS_SHIFT_S = 1
AUTO_REGISTER_MAX_DAYS_SHIFT_Q = 4

# evaluate_auto_register_eligibility()の戻り値status。
AUTO_REGISTER_ELIGIBLE = "eligible"
AUTO_REGISTER_INELIGIBLE = "ineligible"

# 登録できる行を、日付の差で「Shift+S で可」「Shift+Q でのみ可」の2段階に分ける（D-101）。
STATUS_ELIGIBLE_SHIFT_S = "eligible_shift_s"
STATUS_ELIGIBLE_SHIFT_Q = "eligible_shift_q"

# 登録できない理由のコード。STAGING_STATUS_LABELS（production_import_service）のキーにも使う。
REASON_NO_CANDIDATES_CODE = "no_candidates"
REASON_PRODUCT_NAME_MISMATCH = "product_name_mismatch"
REASON_MULTIPLE_CANDIDATES = "multiple_candidates"
REASON_EXISTING = "existing"
REASON_DUPLICATE = "duplicate"
# 候補の計画のロットが完成済み（D-130）。候補には残すが、Shift+S・Shift+Q では登録しない。
REASON_LOT_COMPLETED = "lot_completed"
REASON_SIDE2_WAIT = "side2_wait"
REASON_SIDE_MASTER_UNREGISTERED = "side_master_unregistered"
REASON_QTY_MISMATCH = "qty_mismatch"
REASON_DATE_UNPARSEABLE = "date_unparseable"
REASON_DATE_DIFF_TOO_LARGE = "date_diff_too_large"
# 一括登録で「置き換えが起きる行を除いて登録」を選んだときに除いた行（判定の優先順位には入らない。D-126）。
REASON_OPPOSITE_OVERWRITE = "opposite_overwrite"

# 一括登録の完了メッセージで、スキップ理由ごとの件数に付ける短いラベル（一覧の「状態」列とは別）。
AUTO_REGISTER_SKIP_REASON_LABELS = {
    REASON_NO_CANDIDATES_CODE: "候補なし",
    REASON_PRODUCT_NAME_MISMATCH: "製品名不一致",
    REASON_MULTIPLE_CANDIDATES: "候補複数（未選択）",
    REASON_EXISTING: "既に実績登録済み",
    REASON_DUPLICATE: "同一計画への重複候補",
    REASON_LOT_COMPLETED: "ロット完成済み",
    REASON_SIDE2_WAIT: "面2待ち",
    REASON_SIDE_MASTER_UNREGISTERED: "生産面マスター未登録",
    REASON_QTY_MISMATCH: "数量不一致",
    REASON_DATE_UNPARSEABLE: "日付を解釈できない",
    REASON_DATE_DIFF_TOO_LARGE: "日付の差が許容日数を超える",
    REASON_OPPOSITE_OVERWRITE: "反対側の面の実績が置き換わるため除外",
}


def _date_only_diff_days(report_date, plan_start_datetime):
    """
    払い出し日と生産予定日の差の日数（絶対値）を、日付だけで比較する。どちらかが解釈できなければ None。
    時刻まで含めると暦日の差とずれる（実データの生産予定日には全件時刻がある。7/23 と 7/21 23:50 は2日だが、1日になってしまう）。
    """
    reference = parse_flexible_date(report_date)
    if reference is None:
        return None
    dt = parse_plan_start_datetime(plan_start_datetime)
    if dt is None:
        return None
    return abs((dt.date() - reference.date()).days)


def _is_qty_mismatch(planned_qty, daily_qty) -> bool:
    """計画数と CSV の実績数が一致しないか。比較できなければ不一致（安全側）とする。"""
    try:
        return float(planned_qty) != float(daily_qty)
    except (TypeError, ValueError):
        return True


def is_auto_confirmable(lot_no, product_name, planned_qty, daily_qty, plan_start_datetime, report_date,
                         max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_S):
    """
    数量が完全一致し、払い出し日と生産予定日の差が max_date_diff_days 日以内（日付のみで比較）なら True。
    比較できない場合は False（安全側）。lot_no・product_name は判定に使わない（呼び出し元で一致を確認済み）。
    以前は ui/production/production_import_staging_window.py にあった（日付の解釈が ui.kitting.plan_candidate_dialog にあったため）。段階1で services へ移した。
    """
    try:
        if float(planned_qty) != float(daily_qty):
            return False
    except (TypeError, ValueError):
        return False

    date_diff = _date_only_diff_days(report_date, plan_start_datetime)
    if date_diff is None or date_diff > max_date_diff_days:
        return False

    return True


def evaluate_auto_register_eligibility(row, candidates, matched, plan_key_counts, max_date_diff_days,
                                       lot_state_cache=None):
    """
    1件の保留行が自動登録（Shift+S／Shift+Q）できるかを判定する。一覧の状態表示・Shift+S・Shift+Q の共通の判定（D-101）。
    判定の優先順位:
      1. 候補なし  2. 製品名の不一致  3. 候補が複数  4. 既に実績あり  5. 同じ計画への重複
      5a. 候補のロットが完成済み（D-130）
      6. 面2待ち／生産面マスター未登録  7. 数量の不一致  8. 日付を解釈できない  9. 日付の差が許容を超える
      10. 登録可（差が1日以内なら Shift+S、2日以上なら Shift+Q のみ）
    lot_state_cache（lot_no→lot_completion_state() の結果）を渡すと、同じロットの評価を使い回す。
    candidates・matched は find_matching_plan_items() の戻り値。一括登録では必ず再照合した値を渡すこと。
    plan_key_counts は {(kitting_list_no, lot_no): 単一候補に解決できる行の数}（ループの外で1回だけ計算する）。
    戻り値は (status, reason, candidate, date_diff)。各理由の意味は docs/domain/production_import_staging.md。
    """
    if not candidates:
        return AUTO_REGISTER_INELIGIBLE, REASON_NO_CANDIDATES_CODE, None, None
    if not matched:
        return AUTO_REGISTER_INELIGIBLE, REASON_PRODUCT_NAME_MISMATCH, None, None

    # 計画は (計画No, 元のロットNo) で区別する。統合したロットで計画Noを共有する X・X- の計画は、別の候補として「複数」になる（D-161）。
    unique_plan_keys = {(c["kitting_list_no"], match_lot_of(c, row["lot_no"])) for c in matched}
    if len(unique_plan_keys) > 1:
        return AUTO_REGISTER_INELIGIBLE, REASON_MULTIPLE_CANDIDATES, None, None

    candidate = matched[0]
    kitting_list_no = candidate["kitting_list_no"]
    lot_no = match_lot_of(candidate, row["lot_no"])

    if list_daily_production_by_kitting_no(kitting_list_no, lot_no):
        return AUTO_REGISTER_INELIGIBLE, REASON_EXISTING, candidate, None

    if plan_key_counts.get((kitting_list_no, lot_no), 0) > 1:
        return AUTO_REGISTER_INELIGIBLE, REASON_DUPLICATE, candidate, None

    # ロットが完成した後も、実績の無い計画は候補に残る（計画ごとの完了判定のため）。自動では登録しない（D-130）。
    lot_state = lot_completion_state(candidate["lot_no"], lot_state_cache if lot_state_cache is not None else {})
    if lot_state is not None and lot_state["completed"]:
        return AUTO_REGISTER_INELIGIBLE, REASON_LOT_COMPLETED, candidate, None

    classification = classify_side1_only_plan(candidate)
    if classification == SIDE1_ONLY_CLASS_WAITING_SIDE2:
        return AUTO_REGISTER_INELIGIBLE, REASON_SIDE2_WAIT, candidate, None
    if classification == SIDE1_ONLY_CLASS_UNREGISTERED:
        return AUTO_REGISTER_INELIGIBLE, REASON_SIDE_MASTER_UNREGISTERED, candidate, None

    if _is_qty_mismatch(candidate.get("planned_qty"), row.get("daily_qty")):
        return AUTO_REGISTER_INELIGIBLE, REASON_QTY_MISMATCH, candidate, None

    date_diff = _date_only_diff_days(row.get("report_date"), candidate.get("plan_start_datetime"))
    if date_diff is None:
        return AUTO_REGISTER_INELIGIBLE, REASON_DATE_UNPARSEABLE, candidate, None
    if date_diff > max_date_diff_days:
        return AUTO_REGISTER_INELIGIBLE, REASON_DATE_DIFF_TOO_LARGE, candidate, date_diff

    return AUTO_REGISTER_ELIGIBLE, None, candidate, date_diff


def _resolve_candidates(rows, plan_items_by_lot):
    """保留行ごとに候補を再照合し、candidates・matched を付けた辞書を返す（行の元の辞書は変えない）。"""
    resolved = []
    for row in rows:
        product_name_normalized = normalize_product_name(row["product_name"])
        candidates, matched = find_matching_plan_items(
            row["lot_no"], product_name_normalized, plan_items_by_lot,
        )
        resolved.append(dict(row, candidates=candidates, matched=matched))
    return resolved


def _count_plan_keys(resolved):
    """単一候補に解決できる行について、同じ (kitting_list_no, lot_no) を候補とする行数を数える（D-6 の一意キーはこのペア）。"""
    plan_key_counts = {}
    for item in resolved:
        if len(item["matched"]) == 1:
            key = (item["matched"][0]["kitting_list_no"], match_lot_of(item["matched"][0], item["lot_no"]))
            plan_key_counts[key] = plan_key_counts.get(key, 0) + 1
    return plan_key_counts


def _status_of(item, plan_key_counts, lot_state_cache=None):
    """
    一覧の「状態」。最も広い Shift+Q の許容日数（4日）で判定し、合格した行を日付の差で「Shift+S で可」「Shift+Q でのみ可」に分ける。
    "no_candidates" は登録不可リストの CSV 出力の判定に使うので、文字列を変えないこと。
    """
    eligibility, reason, _candidate, date_diff = evaluate_auto_register_eligibility(
        item, item["candidates"], item["matched"], plan_key_counts,
        max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_Q, lot_state_cache=lot_state_cache,
    )
    if eligibility == AUTO_REGISTER_ELIGIBLE:
        return STATUS_ELIGIBLE_SHIFT_S if date_diff <= AUTO_REGISTER_MAX_DAYS_SHIFT_S else STATUS_ELIGIBLE_SHIFT_Q
    return reason


def _load_staged_rows_from_db():
    """
    未処理の保留行を DB から読み、候補を再照合して状態を判定した辞書のリストを作る。plan_items_by_lot は1回だけ取得して使い回す。
    """
    pending_rows = list_pending_csv_import_rows()
    if not pending_rows:
        return []

    rows = [{
        "pending_row_id": row["pending_row_id"],
        "row": row.get("csv_row_no"),
        "lot_no": row["lot_no"],
        "product_name": row["product_name"],
        "daily_qty": row["daily_qty"],
        "report_date": row["report_date"],
        "worker_id": row["worker_id"],
    } for row in pending_rows]
    resolved = _resolve_candidates(rows, group_active_plan_items_by_lot())
    plan_key_counts = _count_plan_keys(resolved)
    lot_state_cache = {}
    plan_lot_nos = None
    for item in resolved:
        item["status"] = _status_of(item, plan_key_counts, lot_state_cache)
        if item["status"] == REASON_NO_CANDIDATES_CODE:
            plan_lot_nos = active_plan_lot_nos() if plan_lot_nos is None else plan_lot_nos
            _set_similar_lot_no(item, plan_lot_nos)
    return resolved


def _set_similar_lot_no(item, plan_lot_nos=None):
    """「候補なし」の行に、末尾「-」を付けた／外したロットの計画があれば similar_lot_no を足す（D-162。表示だけで、状態は変えない）。"""
    similar = find_similar_lot_no(item["lot_no"], plan_lot_nos)
    if similar is not None:
        item["similar_lot_no"] = similar


def reevaluate_staged_row(target_row, all_rows):
    """
    一覧を開いたまま実績が変わったとき（候補一覧から実績修正画面で直したときなど）に、1行の状態を実際の判定に合わせ直す（D-122）。
    判定は一覧を開くときと同じ手順で行う。「同じ計画への重複」は一覧のほかの行にも左右されるので、all_rows（一覧の全行）も
    いまの計画で照合し直して数える。戻り値は target_row の candidates・matched・status を新しくした辞書（引数の辞書は変えない）。
    target_row は all_rows の要素そのもの（同じオブジェクト）を渡すこと。
    """
    resolved = _resolve_candidates(all_rows, group_active_plan_items_by_lot())
    plan_key_counts = _count_plan_keys(resolved)
    for row, item in zip(all_rows, resolved):
        if row is target_row:
            item["status"] = _status_of(item, plan_key_counts)
            item.pop("similar_lot_no", None)   # 前の判定のときの表示を持ち越さない
            if item["status"] == REASON_NO_CANDIDATES_CODE:
                _set_similar_lot_no(item)
            return item
    raise ValueError("target_row が all_rows に含まれていません。")


def load_staged_row(pending_row_id):
    """
    登録待ちの行を1行だけ、一覧に入れる形（候補・状態は未判定）で返す。無ければ None。
    実績の取り消しで作り直した行を、開いている一覧に足すときに使う（状態は reevaluate_staged_row() で決める。D-129）。
    """
    for row in list_pending_csv_import_rows():
        if row["pending_row_id"] == pending_row_id:
            return {
                "pending_row_id": row["pending_row_id"], "row": row.get("csv_row_no"), "lot_no": row["lot_no"],
                "product_name": row["product_name"], "daily_qty": row["daily_qty"], "report_date": row["report_date"],
                "worker_id": row["worker_id"], "candidates": [], "matched": [], "status": None,
            }
    return None
