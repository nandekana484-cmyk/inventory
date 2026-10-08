# ui/production_import_staging_window.py
"""
実績CSV取込のステージング一覧（「確認・選択・転記」方式）。

左右2ペイン構成（ui.ng_input_window.NgInputWindow・ui.wip_expansion_window.
WipExpansionWindowと同じ配置規則：左＝操作・詳細、右＝一覧）：
  - 右ペイン：models.production_import_staging.pending_csv_import_rows
    （DBへは未登録のCSV行一覧、_load_staged_rows_from_db()参照）を表示する。
    行を選択（<<TreeviewSelect>>）すると、その行の候補を左ペインへ表示する。
  - 左ペイン：models.kitting_plan.find_matching_plan_items()で再照合した候補
    一覧を表示する（ソート・ハイライトはui.plan_candidate_dialogの既存ロジック
    をそのまま再利用、後述）。候補をダブルクリックすると、親（parent、通常は
    ui.kitting_production_entry.KittingProductionEntryWindowのインスタンス）の
    search_plan()・entry_daily_qty・_pending_csv_row_removal・
    _pending_csv_report_dateへ直接転記・登録準備を行う。

以前は候補選択にui.plan_candidate_dialog.select_plan_candidate_by_lot()
（モーダルダイアログ、on_row_confirmedコールバック経由でparentに処理を
委譲）を使っていたが、本ウインドウが独立したTkinterのToplevelのまま
parentへの参照（self._parent、__init__()のparent引数）を既に保持していた
ため、モーダルダイアログを経由せず「親の同じメソッド・属性を直接呼ぶ」形に
書き換えることができた（parent.search_plan()等はウインドウの前面・
フォーカス状態に依存しないため、この直接呼び出しに支障は無い）。
select_plan_candidate_by_lot()自体・ui.plan_candidate_dialogモジュール自体は
変更していない（同モジュールの_show_candidate_list_dialog()・
select_plan_candidate()はui.ng_input_window.NgInputWindowが引き続き使用する
ため）。ソート・ハイライトロジック（_sort_candidates_by_closeness()・
_is_large_qty_diff()等）は、ロジックを重複させないためui.plan_candidate_dialog
から直接importして再利用する（下線始まりの非公開名だが、意図的な再利用）。

登録完了の検知：以前は呼び出し元が渡すremove_callbackコールバック経由
だったが、候補確定処理自体が本ウインドウ内で完結するようになったため、
_confirm_candidate()内で定義したremove_callbackをparent._pending_csv_
row_removalへ直接セットし、parent._perform_registration()の登録成功時に
それが直接呼ばれる形になった（本ウインドウ側からDBの状態を能動的に
ポーリングしない、という設計自体は変わらない）。

ステージングデータの永続化（models.production_import_staging）：CSVの生の
行データ（lot_no・product_name・daily_qty・report_date・worker_id）をDB
（pending_csv_import_rows）へ永続化する。候補（candidates/matched）自体は
DBに保存せず、_load_staged_rows_from_db()・_populate_candidates()が表示の
たびにmodels.kitting_plan.find_matching_plan_items()で再照合する（計画の
変更に追随できるよう、常に最新のDB状態を反映するため）。登録・除外が確定した
行はdelete_pending_csv_import_row()で即座に物理削除し、履歴としては残さない。
"""
import csv
import logging

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from services.production_import_service import (
    STAGING_STATUS_LABELS,
    EXCLUSION_REASON_LABELS,
    normalize_product_name,
    group_active_plan_items_by_lot,
)
from models.kitting_plan import (
    find_matching_plan_items, classify_side1_only_plan,
    SIDE1_ONLY_CLASS_SINGLE_SIDE, SIDE1_ONLY_CLASS_WAITING_SIDE2, SIDE1_ONLY_CLASS_UNREGISTERED,
    SIDE1_ONLY_CLASS_LABELS,
)
from models.production import list_daily_production_by_kitting_no
from models.production_import_staging import (
    list_pending_csv_import_rows,
    delete_pending_csv_import_row,
    upsert_pending_csv_import_row,
)
from models.lot_status_history import record_lot_status_snapshot

logger = logging.getLogger(__name__)
# ui.plan_candidate_dialog（モーダルダイアログ用の実装）から、候補の並べ替え・
# ハイライト判定・表示フォーマットのロジックのみをそのまま再利用する。
# ui.plan_candidate_dialog自体はui.ng_input_window.NgInputWindowが引き続き
# 使うモーダルダイアログ（select_plan_candidate()・_show_candidate_list_
# dialog()）のため変更しない。下線始まりの非公開名だが、ロジックを重複させて
# 二重管理になることを避けるため、あえて直接importする。
from ui.plan_candidate_dialog import (
    _sort_candidates_by_closeness,
    _is_large_qty_diff,
    _is_large_date_diff,
    _parse_flexible_date,
    _parse_plan_start_datetime,
    _format_qty,
    _format_planned_qty_cell,
    _format_side,
    _COLS as _CANDIDATE_COLS,
    _HEADERS as _CANDIDATE_HEADERS,
    _RIGHT_ALIGNED as _CANDIDATE_RIGHT_ALIGNED,
)
from ui.window_utils import center_window
from ui.highlight_colors import MISMATCH_RED

# "候補なし"（find_matching_plan_items()の候補が0件）行をCSV出力する際の理由欄。
# services.production_import_service.import_production_csv()のunmatched理由
# 文言と揃えている。
REASON_NO_CANDIDATES = "計画が見つからない（該当lot_noの計画なし）"

# 「不一致として除外」時、除外理由の入力を空欄のまま・キャンセルした場合の
# デフォルト文言。REASON_NO_CANDIDATES（機械判定による固定理由1種類のみ）とは
# 異なり、不一致は人間が個別に判断するため理由は行ごとに様々であり得る
# （例：ロットNoの入力ミス、対象外の製品、二重入力等）。そのため理由入力欄は
# 任意入力（自由記述）とし、何も入力されなかった場合のみこのデフォルト文言を
# 使う方針とした。
REASON_MISMATCH_DEFAULT = "不一致と判断（詳細理由未入力）"


_SIDE1_ONLY_CONFIRM_REASONS = {
    SIDE1_ONLY_CLASS_WAITING_SIDE2: "生産面マスターにより、この計画には後行面（面2）があることが分かっています（面2はまだ計画データに取り込まれていません）。",
    SIDE1_ONLY_CLASS_UNREGISTERED: "この計画のセットアップファイルNo・実装ラインの組み合わせは、生産面マスターに登録がありません（後行面があるかどうか不明です）。",
}


def _confirm_side1_only_if_applicable(parent, candidate):
    """
    candidate（find_matching_plan_items()の候補、kitting_plan_items 1行分）が
    面1のみの計画で、生産面マスターの分類（models.kitting_plan.
    classify_side1_only_plan()）が「面2待ち」または「生産面マスター未登録」の
    場合、理由を示した確認ダイアログを出す（2026-10-07新設、D-9x参照）。
    登録そのものは禁止しない（「はい」で続行できる）。分類が"a"（片面の製品）、
    またはNone（面2の計画自体、分類対象外）の場合は、ダイアログを出さず
    常にTrueを返す。

    「いいえ」を選んだ場合はFalseを返し、呼び出し元（_register_candidate_
    immediately()）は検索欄への転記・登録処理のいずれも行わず即座に戻ること
    （保留行もそのまま残る）。
    """
    classification = classify_side1_only_plan(candidate)
    if classification not in _SIDE1_ONLY_CONFIRM_REASONS:
        return True

    reason = _SIDE1_ONLY_CONFIRM_REASONS[classification]
    message = (
        f"{reason}\n\n"
        f"計画（{candidate['kitting_list_no']}）はこのまま登録できますが、"
        "本当にこの計画へ登録してよいか確認してください。続けますか？"
    )
    return messagebox.askyesno("面1のみの計画への登録", message, parent=parent.winfo_toplevel())


# Shift+S・Shift+Qで許容する、payout日（report_date）と生産予定日
# （plan_start_datetime）の差（日数、日付のみで比較）の上限。
# 「Shift+Qを追加する。Shift+Sとの違いは日付の条件だけ」という利用者決定に
# 基づき、両ショートカットの差は、唯一この定数の値の違いだけにする
# （2026-10-08追加、D-101）。
AUTO_REGISTER_MAX_DAYS_SHIFT_S = 1
AUTO_REGISTER_MAX_DAYS_SHIFT_Q = 4

# evaluate_auto_register_eligibility()の戻り値status。
AUTO_REGISTER_ELIGIBLE = "eligible"
AUTO_REGISTER_INELIGIBLE = "ineligible"

# 一覧（登録待ち一覧）の状態表示用：候補が単一に解決でき、数量・日付の両方の
# 条件を満たした行を、日付差でさらに2段階に分ける（2026-10-08追加、D-101）。
STATUS_ELIGIBLE_SHIFT_S = "eligible_shift_s"
STATUS_ELIGIBLE_SHIFT_Q = "eligible_shift_q"

# evaluate_auto_register_eligibility()が返す理由コードの一覧（登録できない
# 場合のreason）。STAGING_STATUS_LABELS（services.production_import_service）
# のキーとしても使うため、ここで一括管理する。
REASON_NO_CANDIDATES_CODE = "no_candidates"
REASON_PRODUCT_NAME_MISMATCH = "product_name_mismatch"
REASON_MULTIPLE_CANDIDATES = "multiple_candidates"
REASON_EXISTING = "existing"
REASON_DUPLICATE = "duplicate"
REASON_SIDE2_WAIT = "side2_wait"
REASON_SIDE_MASTER_UNREGISTERED = "side_master_unregistered"
REASON_QTY_MISMATCH = "qty_mismatch"
REASON_DATE_UNPARSEABLE = "date_unparseable"
REASON_DATE_DIFF_TOO_LARGE = "date_diff_too_large"

# _on_bulk_register()・_on_bulk_register_extended()の完了メッセージで、
# スキップ理由ごとの件数を表示する際のラベル（短い文言、2026-10-08追加）。
# 一覧の「状態」列（STAGING_STATUS_LABELS）とは文脈が違う（完了メッセージの
# 1行の中で使うため、より短い名詞句にしている）ため、別辞書にしている。
AUTO_REGISTER_SKIP_REASON_LABELS = {
    REASON_NO_CANDIDATES_CODE: "候補なし",
    REASON_PRODUCT_NAME_MISMATCH: "製品名不一致",
    REASON_MULTIPLE_CANDIDATES: "候補複数（未選択）",
    REASON_EXISTING: "既に実績登録済み",
    REASON_DUPLICATE: "同一計画への重複候補",
    REASON_SIDE2_WAIT: "面2待ち",
    REASON_SIDE_MASTER_UNREGISTERED: "生産面マスター未登録",
    REASON_QTY_MISMATCH: "数量不一致",
    REASON_DATE_UNPARSEABLE: "日付を解釈できない",
    REASON_DATE_DIFF_TOO_LARGE: "日付の差が許容日数を超える",
}


def _date_only_diff_days(report_date, plan_start_datetime):
    """
    report_date（CSVの払い出し日）とplan_start_datetime（候補の生産予定日）の
    差の日数（絶対値）を、日付だけで比較する（時刻は無視する、2026-10-08追加、
    D-101）。パース不能・どちらか欠落なら比較不能としてNone。

    旧ui.plan_candidate_dialog._compute_date_diff_days()は、
    plan_start_datetimeの時刻成分まで含めた生の経過時間を
    timedelta.daysでfloorしていたため、時刻成分が存在する場合に暦日の差と
    ずれることがあった（実データのplan_start_datetimeは全件に0時以外の
    時刻成分がある。例：report_date="2026-07-23"、
    plan_start_datetime="2026/07/21 23:50:00"の場合、暦日の差は2日だが、
    生の経過時間は1日10分のためfloorで1日と計算されてしまっていた）。
    本関数は両方の値を日付のみ（.date()）に正規化してから差を取ることで、
    この時刻成分によるずれを無くす。

    パースロジック自体（_parse_flexible_date()・_parse_plan_start_datetime()）は
    ui.plan_candidate_dialogのものをそのまま再利用する（日付形式の解釈を
    1箇所に集約し、ずれが生じないようにするため。本関数が変えるのは
    「パース後の差分計算の単位」のみ）。
    """
    reference = _parse_flexible_date(report_date)
    if reference is None:
        return None
    dt = _parse_plan_start_datetime(plan_start_datetime)
    if dt is None:
        return None
    return abs((dt.date() - reference.date()).days)


def is_auto_confirmable(lot_no, product_name, planned_qty, daily_qty, plan_start_datetime, report_date,
                         max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_S):
    """
    調査（自動確定可能な実績の要件検証）で仮実装した判定を正式に実装したもの。

    要件：
      - 生産予定数（planned_qty）とCSVの実績数（daily_qty）が完全一致
      - 生産予定日（plan_start_datetime）とCSVの払い出し日（report_date）の
        差がmax_date_diff_days日以内（日付のみで比較し、時刻は無視する）

    lot_no・product_nameは本関数自体の判定には使わない（呼び出し元
    （_populate_candidates()）が既にfind_matching_plan_items()でlot_no・
    製品名の一致を確認した上で得た個々の候補について、追加の数量・日付条件を
    判定する用途のため）。is_already_registered(lot_no, product_name,
    daily_qty)と引数の並びを揃え、関連する判定関数群として一貫させるために
    残している。

    配置場所について：調査時の指示は「services/production_import_service.py
    （または適切な場所）」だったが、日数差の計算にui.plan_candidate_dialog.
    _parse_flexible_date()・_parse_plan_start_datetime()を使う必要があり、
    services層がui層のモジュールに依存する形になってしまう（このコードベースの
    他の箇所はすべてui→services→modelsの一方向依存）。呼び出し元がui.
    production_import_staging_window（本ファイル）のみであることも踏まえ、
    あえてservices/production_import_service.pyには置かず、本ファイルに
    配置した。

    max_date_diff_days（2026-10-08追加、D-101・Shift+Q新設に伴う判定の
    共通化）：既定値はShift+Sの許容日数（1日）のため、この引数を追加した前後で
    既存の呼び出し元（_populate_candidates()、常に既定値のまま呼ぶ）の動作は
    変わらない。evaluate_auto_register_eligibility()がShift+Q
    （AUTO_REGISTER_MAX_DAYS_SHIFT_Q=4）を判定する際に、本関数へこの値を
    明示的に渡す。

    日数差の計算方式について（2026-10-08修正、D-101）：以前は
    ui.plan_candidate_dialog._compute_date_diff_days()
    （plan_start_datetimeの時刻成分まで含めた生の経過時間をfloor）を使って
    いたが、_date_only_diff_days()（日付のみで比較、時刻は無視）に差し替えた。
    実データのplan_start_datetimeは全件に0時以外の時刻成分があり、旧方式では
    暦日の差が本来より少なく計算される境界ケースが起こりうることが判明した
    ため（詳細は_date_only_diff_days()のdocstring参照）。この修正により、
    是正された日数差が許容日数を超えることで対象から外れる行が実データで
    存在し得る（Shift+Sの判定結果への影響は、検証で実測した件数を参照）。

    以前の制約（修正済み）：調査時点の実データ（pending_csv_import_rows
    95件）では、report_dateの実運用値が"YYYY/M/D"形式（例："2026/3/18"、
    月日がゼロ埋めされていない）だったが、_parse_flexible_date()が
    対応する前の旧_parse_report_date()は"%Y-%m-%d"のみにしか対応しておらず、
    実データでは本関数が常にFalse（日付比較不能）を返していた。現在は
    "%Y-%m-%d"・"%Y/%m/%d"（ゼロ埋めの有無を問わない）の両方に対応済みのため、
    実データでも正しく判定できる。日付・数量いずれも比較不能な場合はFalse
    （安全側）に倒す設計はis_already_registered()と同じ考え方のまま変えて
    いない。
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


def evaluate_auto_register_eligibility(row, candidates, matched, plan_key_counts, max_date_diff_days):
    """
    1件の保留行（row）について、自動登録（Shift+S／Shift+Q）できるかどうかを、
    1か所にまとめて判定する（2026-10-08新設、D-101。Shift+Qの追加に伴い、
    一覧の状態表示（_load_staged_rows_from_db()）・Shift+S（_on_bulk_register()）・
    Shift+Q（_on_bulk_register_extended()）の3箇所がそれぞれ独自に持っていた
    判定ロジックを1つの関数に統合した）。

    判定順序（優先順位、既存のShift+S・一覧の状態表示の優先順位をそのまま
    踏襲）：
      1. 候補なし（lot_noが一致する計画が1件も無い）
      2. 製品名の不一致（lot_noは一致するが、製品名が一致する候補が無い。
         以前は「候補あり（要選択）」に一括で含まれていたが、原因を区別
         できるよう分離した）
      3. 候補が複数（製品名まで一致する候補が2件以上。どの計画かは人が選ぶ）
      4. 既に実績あり（その計画（kitting_list_no, lot_no）に既に
         production_daily行が1件以上ある。黙って上書きしないため対象外）
      5. 同じ計画への重複（同じ計画を候補とする保留行が、他にも一覧に存在する。
         どちらを登録すべきかは人が判断する）
      6. 面2待ち／生産面マスター未登録（候補の計画が面1のみで、生産面マスターの
         分類が「面2待ち」または「生産面マスター未登録」）
      7. 数量の不一致（計画数とCSVの実績数が完全一致しない）
      8. 日付を解釈できない（払い出し日・生産予定日のいずれかがパース不能。
         安全側に倒し、登録できない側とする）
      9. 日付の差が許容（max_date_diff_days）を超える
      10.（いずれにも該当しない）登録可能。日付の差が1日以内ならShift+Sで
         登録可、2日以上max_date_diff_days以下ならShift+Qでのみ登録可。

    日付の差は、日付だけで比較する（時刻は無視する、_date_only_diff_days()
    参照）。

    引数：
      row：{"lot_no", "product_name", "daily_qty", "report_date"}を持つ辞書
           （_load_staged_rows_from_db()が組み立てる保留行、または同じ形の
           辞書）。
      candidates・matched：models.kitting_plan.find_matching_plan_items()の
           戻り値をそのまま渡す（本関数自体はDBへの再照合を行わない。
           呼び出し側が、最新の状態を使うか・キャッシュ済みの値を使うかを
           決める。既存のコメント通り、一括登録系は必ず再照合した値を渡す
           こと）。
      plan_key_counts：{(kitting_list_no, lot_no): 件数}。現在対象にしている
           行全体のうち、単一候補に解決できる行だけを数えた辞書。呼び出し側が
           ループの外で1回だけ計算して渡す（既存のN+1回避パターンを維持）。
      max_date_diff_days：AUTO_REGISTER_MAX_DAYS_SHIFT_S（1）または
           AUTO_REGISTER_MAX_DAYS_SHIFT_Q（4）。

    戻り値：(status, reason, candidate, date_diff)
      status：AUTO_REGISTER_ELIGIBLE または AUTO_REGISTER_INELIGIBLE。
      reason：ineligibleの場合のみ、上記いずれかの理由コード（文字列）。
              eligibleの場合はNone。
      candidate：単一候補に解決できた場合のみ、その候補（dict）。それ以外は
              None（候補なし・製品名不一致・候補複数のいずれも、特定の1件に
              絞れていないため）。
      date_diff：日付の差（日数）を計算できた場合はその値（max_date_diff_days
              を超えて不合格になった場合も含む）。候補未解決・計算不能なら
              None。呼び出し側が、1日以内かどうかでShift+S/Shift+Qの表示を
              分けるために使う。
    """
    if not candidates:
        return AUTO_REGISTER_INELIGIBLE, REASON_NO_CANDIDATES_CODE, None, None
    if not matched:
        return AUTO_REGISTER_INELIGIBLE, REASON_PRODUCT_NAME_MISMATCH, None, None

    unique_kitting_nos = {c["kitting_list_no"] for c in matched}
    if len(unique_kitting_nos) > 1:
        return AUTO_REGISTER_INELIGIBLE, REASON_MULTIPLE_CANDIDATES, None, None

    candidate = matched[0]
    kitting_list_no = candidate["kitting_list_no"]
    lot_no = row["lot_no"]

    if list_daily_production_by_kitting_no(kitting_list_no, lot_no):
        return AUTO_REGISTER_INELIGIBLE, REASON_EXISTING, candidate, None

    if plan_key_counts.get((kitting_list_no, lot_no), 0) > 1:
        return AUTO_REGISTER_INELIGIBLE, REASON_DUPLICATE, candidate, None

    classification = classify_side1_only_plan(candidate)
    if classification == SIDE1_ONLY_CLASS_WAITING_SIDE2:
        return AUTO_REGISTER_INELIGIBLE, REASON_SIDE2_WAIT, candidate, None
    if classification == SIDE1_ONLY_CLASS_UNREGISTERED:
        return AUTO_REGISTER_INELIGIBLE, REASON_SIDE_MASTER_UNREGISTERED, candidate, None

    try:
        qty_mismatch = float(candidate.get("planned_qty")) != float(row.get("daily_qty"))
    except (TypeError, ValueError):
        qty_mismatch = True
    if qty_mismatch:
        return AUTO_REGISTER_INELIGIBLE, REASON_QTY_MISMATCH, candidate, None

    date_diff = _date_only_diff_days(row.get("report_date"), candidate.get("plan_start_datetime"))
    if date_diff is None:
        return AUTO_REGISTER_INELIGIBLE, REASON_DATE_UNPARSEABLE, candidate, None
    if date_diff > max_date_diff_days:
        return AUTO_REGISTER_INELIGIBLE, REASON_DATE_DIFF_TOO_LARGE, candidate, date_diff

    return AUTO_REGISTER_ELIGIBLE, None, candidate, date_diff


def _load_staged_rows_from_db():
    """
    models.production_import_staging.list_pending_csv_import_rows()で未処理行を
    DBから取得し、各行についてmodels.kitting_plan.find_matching_plan_items()で
    候補（candidates/matched）を再照合した上で、evaluate_auto_register_
    eligibility()で状態（status）を判定し、従来と同じ形の辞書リスト
    （{"pending_row_id", "row", "lot_no", "product_name", "daily_qty",
    "report_date", "worker_id", "candidates", "matched", "status"}）を組み立てる。

    候補をDBに保存せず必ず再照合する理由はmodels.production_import_stagingの
    docstring参照。plan_items_by_lot（services.production_import_service.
    group_active_plan_items_by_lot()）は本関数内で1回だけ取得し、未処理行数分の
    find_matching_plan_items()呼び出しで使い回す（CSV取込時と同じN+1回避）。

    status（2026-10-08、D-101で判定ロジックを統合）：evaluate_auto_register_
    eligibility()を、Shift+Qの許容日数（AUTO_REGISTER_MAX_DAYS_SHIFT_Q=4日）で
    評価する。4日以内という最も広い範囲で評価することで、合格した行については
    実際の日付差（date_diff）を使ってShift+S（1日以内）・Shift+Qのみ（2〜4日）の
    どちらに該当するかをここでさらに判定できる（4日を超えて不合格になった行は
    "date_diff_too_large"のまま、Shift+S・Shift+Qどちらでも対象外になる）。
    不合格の場合はevaluate_auto_register_eligibility()が返す理由コードを
    statusにそのまま使う（STAGING_STATUS_LABELSで表示ラベルに変換する）。

    "no_candidates"のみ、_unregistrable_rows（登録不可リストのCSV出力対象）の
    判定に使われているため、文字列自体は変更していない（旧実装からの既存の
    依存）。
    """
    pending_rows = list_pending_csv_import_rows()
    if not pending_rows:
        return []

    plan_items_by_lot = group_active_plan_items_by_lot()

    resolved = []
    for row in pending_rows:
        product_name_normalized = normalize_product_name(row["product_name"])
        candidates, matched = find_matching_plan_items(
            row["lot_no"], product_name_normalized, plan_items_by_lot,
        )
        resolved.append({
            "pending_row_id": row["pending_row_id"],
            "row": row.get("csv_row_no"),
            "lot_no": row["lot_no"],
            "product_name": row["product_name"],
            "daily_qty": row["daily_qty"],
            "report_date": row["report_date"],
            "worker_id": row["worker_id"],
            "candidates": candidates,
            "matched": matched,
        })

    # 重複判定用：単一候補に解決できる行だけを対象に、同じ(kitting_list_no,
    # lot_no)を候補とする行数を数える（D-6の一意キーはこのペアであり、
    # kitting_list_noだけではない。2026-10-06確認・修正の経緯を維持）。
    plan_key_counts = {}
    for item in resolved:
        if len(item["matched"]) == 1:
            key = (item["matched"][0]["kitting_list_no"], item["lot_no"])
            plan_key_counts[key] = plan_key_counts.get(key, 0) + 1

    staged_rows = []
    for item in resolved:
        eligibility, reason, _candidate, date_diff = evaluate_auto_register_eligibility(
            item, item["candidates"], item["matched"], plan_key_counts,
            max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_Q,
        )
        if eligibility == AUTO_REGISTER_ELIGIBLE:
            item["status"] = (
                STATUS_ELIGIBLE_SHIFT_S if date_diff <= AUTO_REGISTER_MAX_DAYS_SHIFT_S
                else STATUS_ELIGIBLE_SHIFT_Q
            )
        else:
            item["status"] = reason
        staged_rows.append(item)

    return staged_rows


def open_or_notify(parent, already_registered_rows=None):
    """
    未処理の保留行（pending_csv_import_rows）が1件でもあれば、または
    already_registered_rows（直前のCSV取込で「登録済み」と判定された行、
    parse_production_csv_for_staging()の戻り値）が1件でもあれば
    ProductionImportStagingWindowを開き、どちらも無ければ案内メッセージ
    のみ表示する（新規CSV取込直後・「実績CSV取込状況」ボタンからの再開、
    両方の入口から使う共通のエントリーポイント）。

    already_registered_rowsも判定条件に含める理由：CSVの全行が「登録済み」
    だった場合（pending_csv_import_rows側は0件）でも、登録済みリストだけは
    ユーザーに見せる価値がある（本当に想定通りの重複だったか確認できる）
    ため、この場合も「未処理の取込データはありません」で終わらせず
    ウインドウを開く。

    parent：ui.kitting_production_entry.KittingProductionEntryWindowの
    インスタンスを想定（search_plan()・entry_daily_qty・_pending_csv_row_
    removal・_pending_csv_report_dateを直接呼び出す/参照するため）。

    戻り値：開いたProductionImportStagingWindow、またはどちらも無く
    開かなかった場合はNone。
    """
    staged_rows = _load_staged_rows_from_db()
    if not staged_rows and not already_registered_rows:
        messagebox.showinfo("実績CSV取込状況", "未処理の取込データはありません。", parent=parent)
        return None
    return ProductionImportStagingWindow(parent, staged_rows, already_registered_rows=already_registered_rows)


class ProductionImportStagingWindow(tk.Toplevel):
    # 右ペイン（登録待ち一覧）の選択（<<TreeviewSelect>>、矢印キーでも発火）の
    # たびに毎回find_matching_plan_items()（DBアクセスを伴う）を実行しないよう
    # デバウンスする。ui.kitting_production_entry.KittingProductionEntryWindow.
    # PLAN_SELECT_DEBOUNCE_MSと同じ値・同じ考え方。
    CANDIDATE_SELECT_DEBOUNCE_MS = 200

    def __init__(self, parent, staged_rows, already_registered_rows=None):
        """
        parent：ui.kitting_production_entry.KittingProductionEntryWindowの
        インスタンス。左ペインの候補をダブルクリックした際、parent.
        search_plan()・parent.entry_daily_qty・parent._pending_csv_row_removal・
        parent._pending_csv_report_dateへ直接アクセスする（_confirm_candidate()
        参照）。

        staged_rows：_load_staged_rows_from_db()が組み立てる辞書のリスト
        （各要素は"pending_row_id"/"row"/"lot_no"/"product_name"/"daily_qty"/
        "report_date"/"worker_id"/"candidates"/"matched"/"status"を持つ）。
        本クラス自体はopen_or_notify()経由で呼ばれる想定で、直接staged_rowsを
        組み立てて渡すのは主にテスト用途。

        already_registered_rows：services.production_import_service.
        parse_production_csv_for_staging()の戻り値"already_registered_rows"
        （直前のCSV取込で「登録済み」と判定され、pending_csv_import_rowsへは
        保存されなかった行の詳細）。省略時（None）は空リストとして扱う
        （「実績CSV取込状況」ボタン経由など、直近のCSV取込を伴わずに開いた
        場合はこの情報自体が存在しないため）。
        """
        super().__init__(parent)
        self._parent = parent
        self.title("実績CSV取込：登録待ち一覧")
        self.geometry("1150x520")
        center_window(self, parent)

        self._row_by_iid = {}
        # "no_candidates"（候補なし＝登録不可）と判定された行を、一覧から消えても
        # 参照できるよう別途保持しておく（CSV出力ボタン用）。一覧本体
        # （self._row_by_iid）は登録完了のたびに行が消えていくが、こちらは
        # ウインドウを閉じるまで消さない。
        self._unregistrable_rows = [
            row for row in staged_rows if row.get("status") == "no_candidates"
        ]
        # 「不一致として除外」（右クリックメニュー）で個別に人間が判断した行を
        # 保持する別リスト。self._unregistrable_rows（機械判定の"no_candidates"
        # 分はCSV出力時にまとめて削除、人間判断で"候補なしとする"を選んだ分は
        # _apply_no_candidates()の時点で即座に削除、2026-09-23修正）と同様、
        # こちらも除外を選んだ時点で即座にpending_csv_import_rowsから削除する
        # （_mark_as_mismatched()参照）。
        self._mismatched_rows = []

        # 「登録済みリスト」：is_already_registered()が既にproduction_dailyへ
        # 同じ数量で登録済みと判定し、pending_csv_import_rowsへは一度も
        # 保存されなかった行（parse_production_csv_for_staging()の戻り値
        # "already_registered_rows"）。self._unregistrable_rows・
        # self._mismatched_rowsと異なり、対応するDB行自体がそもそも存在しない
        # （最初から永続化されていない）ため、CSV出力しても・行を削除しても
        # DB側で何かを消す操作は発生しない（あくまでメモリ上の一覧表示用）。
        self._already_registered_rows = list(already_registered_rows or [])
        # 「登録済みリスト」の詳細表示ウインドウ（on_show_already_registered_
        # list()で開く、別Toplevel）。一度開いたら使い回し、多重に開かない
        # （既存の他の一覧画面と同じ多重表示防止の考え方）。
        self._already_registered_window = None
        self._already_registered_tree = None
        self._already_registered_by_iid = {}

        # 左ペイン（候補一覧）の状態：右ペインで現在選択中の保留行（iid・row）と、
        # 候補Treeviewのiidからcandidateへのマッピング。
        self._current_staging_iid = None
        self._current_staging_row = None
        self._candidates_by_iid = {}
        self._candidate_select_debounce_id = None
        self._pending_candidate_select_iid = None

        # 右ペイン（登録待ち一覧）の列ソート状態：col -> 次にクリックした時に
        # 昇順にするかどうか（既存のNG一覧・仕掛一覧のsort_ng_list()/
        # sort_wip_list()と同じ、列ごとに独立したトグル方式）。
        self._staging_sort_states = {}

        self._create_widgets(staged_rows)
        self._update_status_label()
        self._clear_candidate_pane()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ウインドウを開いた直後、キーボードフォーカスを登録待ち一覧へ置く
        # （2026-10-06追加、Shift+Sが効かなくなる不具合の修正の一環。何も
        # 明示的にfocus_set()しないと、開いた直後はどのウィジェットにも
        # Tkのキーボードフォーカスが無い状態になりうる）。
        self.tree.focus_set()

    def _create_widgets(self, staged_rows):
        """
        左右2ペイン構成（container→left_frame（候補一覧）/right_frame
        （登録待ち一覧））。ui.ng_input_window.NgInputWindow・ui.wip_expansion_
        window.WipExpansionWindowと同じ配置規則（左：操作・詳細、右：一覧）に
        倣う：右ペインの登録待ち一覧が主たる一覧（選択の起点）、左ペインは
        その選択に反応して候補を表示する詳細ペイン、という位置付けのため。
        """
        container = ttk.Frame(self, padding=10)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Labelframe(container, text="候補一覧（右の登録待ち一覧から行を選択）", padding=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_frame = ttk.Labelframe(container, text="実績CSV：登録待ち一覧", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self._create_candidate_widgets(left_frame)
        self._create_staging_widgets(right_frame, staged_rows)

        btn_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="閉じる", command=self._on_close).pack(side=tk.RIGHT)

    def _create_candidate_widgets(self, left_frame):
        """
        左ペイン（候補一覧）。列構成・書式（_format_side()・_format_qty()）・
        列幅の考え方は、以前ui.plan_candidate_dialog.select_plan_candidate_
        by_lot()がモーダルダイアログとして表示していたものと同一
        （_CANDIDATE_COLS・_CANDIDATE_HEADERS・_CANDIDATE_RIGHT_ALIGNEDを
        そのままimportして使う）。

        pack順序について：右ペイン（_create_staging_widgets()）で発見した
        「pack順序でTkのcavityが消費される」既知のバグパターンと同じ構造が
        ここにも存在していたため、同じ対策を適用した。下部のヒントラベルを
        先に`side=tk.BOTTOM`でpackして領域を確保してから、Treeview＋
        スクロールバーのフレームを`expand=True, fill=tk.BOTH`で最後にpackする
        （`lbl_candidate_hint`は元々`tree_frame`より先・`side=tk.TOP`（既定）で
        packされており、こちらは問題なし：非拡張ウィジェットを拡張ウィジェット
        より先にTOP側でpackする分には、後続のpack呼び出しで正しく領域が
        再計算されるため）。`tree_frame`内でも、スクロールバーをTreeviewより
        先にpackする（同じ理由）。
        """
        self.lbl_candidate_hint = ttk.Label(
            left_frame, foreground="gray", wraplength=420, justify=tk.LEFT,
        )
        self.lbl_candidate_hint.pack(anchor=tk.W, pady=(0, 5))

        # --- 下部のヒントラベルを先にpackし、領域を確保する ---
        ttk.Label(
            left_frame,
            text=(
                "候補をダブルクリックすると、生産実績入力画面（親ウインドウ）に転記されます。\n"
                "右クリックすると、確認ダイアログ無しで即座に登録します。"
            ),
            foreground="gray",
        ).pack(side=tk.BOTTOM, anchor=tk.W, pady=(5, 0))

        # --- Treeview＋スクロールバーは、下部の領域確保後に最後にpackする ---
        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.tree_candidates = ttk.Treeview(
            tree_frame, columns=_CANDIDATE_COLS, show="headings", selectmode="browse",
        )
        for col in _CANDIDATE_COLS:
            self.tree_candidates.heading(col, text=_CANDIDATE_HEADERS[col])
            # 計画数（planned_qty）は_format_planned_qty_cell()で差分表記
            # （例："450（差-50）"）が付くことがあるため、他列より幅を広めに取る
            # （ui.plan_candidate_dialog._show_candidate_list_dialog()と同じ幅）。
            width = 160 if col == "planned_qty" else 100
            self.tree_candidates.column(
                col, width=width, anchor=tk.E if col in _CANDIDATE_RIGHT_ALIGNED else tk.W,
            )
        # 数量差・日付差が大きい候補の背景色を変える
        # （ui.plan_candidate_dialog._is_large_qty_diff()・_is_large_date_diff()
        # と同じ閾値・同じ配色。両方に該当する場合は"large_diff_both"を単独で
        # 割り当てる。3タグ併用によるTkinterの優先順位の曖昧さを避けるため。
        # 詳細はui.plan_candidate_dialog._show_candidate_list_dialog()の
        # docstring参照）。
        #
        # "auto_confirmable"（is_auto_confirmable()、緑系）は、上記の
        # large_diff系（黄・オレンジ・赤）とは数学的に排他（同時に該当し得ない）：
        # auto_confirmableは「数量差=0（完全一致）かつ日付差<=1日」を要求するが、
        # large_diffは「数量差が実績数の20%超」、large_date_diffは「日付差が
        # 3日以上」を要求するため、閾値の範囲が重ならない
        # （0は20%超になり得ず、1日以下は3日以上になり得ない）。そのため
        # 実際には両方に該当するケースは発生しないが、念のためauto_confirmable
        # を最優先で判定し、単独タグとして割り当てる（3タグ併用はしない）。
        self.tree_candidates.tag_configure("large_diff", background="#fff3cd")
        self.tree_candidates.tag_configure("large_date_diff", background="#ffd9a0")
        self.tree_candidates.tag_configure("large_diff_both", background=MISMATCH_RED)
        self.tree_candidates.tag_configure("auto_confirmable", background="#c8f7c5")
        # 生産面マスターによる分類（2026-10-07新設、D-9x参照）。既存のタグとは
        # 異なる色にし、優先順位は_populate_candidates()側で最優先に判定する。
        self.tree_candidates.tag_configure("needs_side2_wait", background="#cfe2ff")
        self.tree_candidates.tag_configure("side_master_unregistered", background="#e2e3e5")
        # 製品名不一致（2026-10-08追加）：ロットNoは一致するが製品名が一致する
        # 候補が無い場合（_populate_candidates()のusing_fallback）に表示する
        # 候補全件に付ける。既存の赤系ハイライト（"large_diff_both"と同じ値、
        # ui/highlight_colors.MISMATCH_RED）を再利用し、新しい色は定義しない。
        # 他の全タグより優先して割り当てる（_populate_candidates()参照）。
        self.tree_candidates.tag_configure("product_name_mismatch", background=MISMATCH_RED)

        # スクロールバーをTreeviewより先にpackする（右ペインと同じ順序）。
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_candidates.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.tree_candidates.configure(yscrollcommand=vsb.set)
        vsb.configure(command=self.tree_candidates.yview)

        self.tree_candidates.bind("<Double-1>", self._on_candidate_double_click)
        self.tree_candidates.bind("<Button-3>", self._on_candidate_right_click)

    def _create_staging_widgets(self, right_frame, staged_rows):
        """
        右ペイン（登録待ち一覧）。以前の単一ペイン版のTreeview・件数表示・CSV出力ボタンをそのまま移設。

        pack順序について（重要）：Tkの`pack()`はpackを呼んだ順にcavity（配置可能領域）を
        消費するため、`expand=True, fill=tk.BOTH`のTreeviewを他の兄弟ウィジェットより先に
        packすると、Treeviewが領域を先取りしてしまい、後からpackする縦スクロールバー・
        下部のラベル/ボタン類に割り当てる余地が残らなくなる（ラベル・ボタン自体は表示される
        場合もあるが、狭い右ペインではスクロールバーが1×1に潰れ`winfo_ismapped()`が
        Falseになる形で顕在化した）。`ui/kitting_production_entry.py`の計画一覧
        （`bottom_btn_frame`→`hsb_plan`→`vsb_plan`→`tree_plan_list`の順）・
        `ui/ng_input_window.py`のNG一覧と同じ対策として、下部に配置するウィジェット
        （ヒントラベル・件数ラベル・ボタン行）を先に`side=tk.BOTTOM`でpackして
        その分の領域を確保してから、Treeview＋スクロールバーのフレームを
        `expand=True, fill=tk.BOTH`で最後にpackする順序に変更した。
        `side=tk.BOTTOM`は「後から呼んだものほど内側（上）に積まれる」ため、
        最終的な見た目の並び順（Treeview→ヒント→件数→ボタン、上から下へ）を
        維持するには、下部要素は見た目と逆順（ボタン→件数→ヒントの順）でpackする。
        """
        cols = ("lot_no", "product_name", "report_date", "worker_id", "daily_qty", "status")

        # --- 下部に配置するウィジェットを先に生成・packし、領域を確保する ---
        action_frame = ttk.Frame(right_frame)
        action_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(action_frame, text="登録不可リストをCSV出力", command=self.on_export_unregistrable_csv).pack(
            side=tk.LEFT, padx=(0, 5),
        )
        ttk.Button(action_frame, text="不一致リストをCSV出力", command=self.on_export_mismatched_csv).pack(
            side=tk.LEFT, padx=(0, 5),
        )
        ttk.Button(action_frame, text="登録済みリストを表示", command=self.on_show_already_registered_list).pack(
            side=tk.LEFT,
        )
        # 「自動確定可能な行を一括登録（Shift+S）」ボタンは2026-10-07削除した
        # （画面修正5項目§2）。Shift+Sのショートカットの案内は、下の
        # ヒントラベルに小さく表示して残す。

        # 登録不可（machine判定）・不一致（人間の判断で除外）、それぞれの件数を
        # 分かりやすく常時表示する。件数が変わるたび_update_status_label()で
        # 更新する。
        self.lbl_status = ttk.Label(right_frame, foreground="gray")
        self.lbl_status.pack(side=tk.BOTTOM, anchor=tk.W, pady=(2, 5))

        ttk.Label(
            right_frame,
            text=(
                "行を選択すると左ペインに候補が表示されます（Ctrl+クリック・Shift+クリックで複数選択可）。\n"
                "右クリックで「不一致として除外」できます（複数選択中は選択中の全行が対象）。\n"
                "複数選択してShift+Sを押すと、日付の差が1日以内の行のみ一括で即時登録します。\n"
                "Shift+Qは日付の差が4日以内まで対象を広げます（登録前に件数を確認します）。"
            ),
            foreground="gray",
        ).pack(side=tk.BOTTOM, anchor=tk.W, pady=(5, 0))

        # --- Treeview＋スクロールバーは、下部の領域確保後に最後にpackする ---
        tree_frame = ttk.Frame(right_frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        # selectmode="extended"：Ctrl+クリック・Shift+クリックでの複数選択に対応する
        # （ttk.Treeviewのデフォルトも"extended"だが、複数選択対応であることを
        # 明示するため明記する）。一括登録（Shift+S）・一括不一致マーク
        # （複数選択時の右クリック）で使う。
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        # 列ヘッダークリックでのソート：既存のNG一覧（sort_ng_list()）・仕掛一覧
        # （sort_wip_list()）と同じ、command=lambda c=...: self.sort_staging_list(c)
        # というパターンをそのまま踏襲する。
        self.tree.heading("lot_no", text="ロットNo", command=lambda c="lot_no": self.sort_staging_list(c))
        self.tree.heading("product_name", text="製品名", command=lambda c="product_name": self.sort_staging_list(c))
        self.tree.heading("report_date", text="払い出し日（参考）", command=lambda c="report_date": self.sort_staging_list(c))
        self.tree.heading("worker_id", text="作業者", command=lambda c="worker_id": self.sort_staging_list(c))
        self.tree.heading("daily_qty", text="実績数", command=lambda c="daily_qty": self.sort_staging_list(c))
        self.tree.heading("status", text="状態", command=lambda c="status": self.sort_staging_list(c))
        # stretch=False（2026-10-07追加、画面修正5項目§5）：既定のstretch=True
        # のままだと、列の合計幅が表示領域より狭い／広い場合にTkが自動的に
        # 列幅を伸縮して表示領域に収めてしまい、本来欲しい「列の合計幅が表示
        # 領域を超えたら横スクロールで見る」という動作にならない（伸縮で
        # 常に収まってしまうため、水平スクロールバーがあっても実質動かせる
        # 余地が生まれない）。列幅をここで指定した値に固定し、収まらない分は
        # 横スクロールバー（hsb）で見る方式にする。
        self.tree.column("lot_no", width=100, anchor=tk.W, stretch=False)
        self.tree.column("product_name", width=180, anchor=tk.W, stretch=False)
        self.tree.column("report_date", width=120, anchor=tk.CENTER, stretch=False)
        self.tree.column("worker_id", width=90, anchor=tk.W, stretch=False)
        self.tree.column("daily_qty", width=70, anchor=tk.E, stretch=False)
        self.tree.column("status", width=160, anchor=tk.W, stretch=False)

        # 製品名不一致の行を赤系ハイライトで表示する（2026-10-08追加）。
        # アプリ内で既に「警告・不一致・要注意」の意味で使われている赤系
        # ハイライト（ui/plan_candidate_dialog.py・本ファイルの
        # "large_diff_both"〈数量差・日付差の両方が大きい候補〉、
        # ui/pdf_ocr_import_window.pyの"low_confidence"、
        # ui/production_side_master_window.pyの削除予定行と同じ値、
        # ui/highlight_colors.MISMATCH_RED）をそのまま採用した。新しい色は
        # 定義しない。タグの割り当ては_insert_staging_row()参照。
        self.tree.tag_configure("product_name_mismatch", background=MISMATCH_RED)

        # 横スクロールバー（2026-10-07追加、画面修正5項目§5）。縦スクロール
        # バー（vsb）と同じ理由で、Treeview（expand=True, fill=BOTH）より先に
        # packする必要がある（下部の領域を先に確保する）。
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal")
        hsb.pack(side=tk.BOTTOM, fill=tk.X)

        # スクロールバーをTreeviewより先にpackする（vsb_plan→tree_plan_listと同じ
        # 順序）。同じtree_frame内の兄弟同士でも、Treeview（expand=True, fill=BOTH）を
        # 先にpackするとスクロールバー分の領域が残らないため、この順序が必須。
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.configure(command=self.tree.yview)
        hsb.configure(command=self.tree.xview)

        for row in staged_rows:
            self._insert_staging_row(row)

        self.tree.bind("<<TreeviewSelect>>", self._on_staging_select)
        self.tree.bind("<Button-3>", self._on_right_click)
        # self.tree単体ではなく、ウインドウ全体（self）にbindする
        # （2026-10-06修正、Shift+Sが効かなくなる不具合の原因の1つ）。
        # self.tree.bind(...)だと、tree自身がTkのキーボードフォーカスを
        # 持っている時にしか発火しない。本ウインドウ内にテキスト入力欄は
        # 無いため、ウインドウ全体にbindしても「入力中の文字入力を妨げる」
        # 副作用は無く、ボタン等にフォーカスがあっても確実に発火する
        # （Tkのbindtagsにより、子ウィジェットの既定バインドタグには所属する
        # トップレベルの名前が含まれ、子ウィジェット自身に同じイベントの
        # バインドが無ければ、ここで登録したウインドウレベルの束縛まで
        # イベントが伝播してくる）。self.tree固有のバインドは残さない
        # （同じキー入力で二重に発火するのを避けるため）。
        self.bind("<Shift-S>", self._on_bulk_register)
        # Shift+Q（2026-10-08新設、D-101）：対象は選択中の行のみ（Shift+Sと
        # 同じ）で、日付の差の許容日数だけを緩める一括登録。以前
        # （2026-10-07）に実装した「選択・絞り込みに関係なく一覧の全行を
        # 対象にする」版のShift+Qは同日中に取り消した経緯がある（D-100参照。
        # 利用者の意図は対象行の広さではなく照合条件の厳しさの緩和であり、
        # 選択範囲を無視して登録できる動作は危険と判断されたため）。今回の
        # Shift+Qはその教訓を踏まえ、対象を選択中の行のみに限定している。
        # <Shift-Q>（CapsLockオフ時にShiftキーが生成するkeysym）・
        # <Shift-q>（CapsLockオン時に生成するkeysym、Shiftとの打ち消し合いで
        # 小文字になる）の両方をbindし、CapsLockの状態に左右されないようにする
        # （D-97で確認済みの同じ対策）。
        self.bind("<Shift-Q>", self._on_bulk_register_extended)
        self.bind("<Shift-q>", self._on_bulk_register_extended)

    def _insert_staging_row(self, row):
        """
        右ペイン（登録待ち一覧）へ1行挿入する共通処理。_create_staging_
        widgets()の初期表示・_revert_already_registered_rows()（登録済み
        リストから戻す訂正操作）の両方から呼ぶ（挿入ロジックの複製を避ける）。

        製品名不一致（status=="product_name_mismatch"）の行に赤系ハイライト
        （"product_name_mismatch"タグ、_create_staging_widgets()のtag_configure
        参照）を付ける（2026-10-08追加）。列ソート（sort_staging_list()、
        tree.move()のみで行自体は作り直さない）・再読込（load_staged_rows()等
        での全件再構築、本関数を再度呼ぶ）・登録後の一覧更新のいずれでも、
        本関数を経由して挿入された行は常にこのタグを保持する。
        """
        status = row.get("status")
        tags = ("product_name_mismatch",) if status == REASON_PRODUCT_NAME_MISMATCH else ()
        iid = self.tree.insert("", tk.END, tags=tags, values=(
            row.get("lot_no", ""),
            row.get("product_name", ""),
            row.get("report_date") or "",
            row.get("worker_id", ""),
            row.get("daily_qty", ""),
            STAGING_STATUS_LABELS.get(row.get("status"), row.get("status", "")),
        ))
        self._row_by_iid[iid] = row
        return iid

    def _update_status_label(self):
        """
        表示中（登録待ち一覧に現在残っている件数）・登録不可・不一致・
        登録済み、それぞれの件数表示を最新化する。「表示中」は既存の他一覧
        （例：ui.kitting_plan_import.KittingPlanImportWindow.lbl_status）と
        同じ「〜件」形式で、self.treeから都度実カウントする（絞り込み機能が
        本画面には無いため、常に「現在一覧に残っている件数」＝登録・除外で
        減っていく件数と一致する）。
        """
        self.lbl_status.config(
            text=(
                f"表示中：{len(self.tree.get_children())}件　"
                f"登録不可：{len(self._unregistrable_rows)}件　"
                f"不一致として除外：{len(self._mismatched_rows)}件　"
                f"登録済み：{len(self._already_registered_rows)}件"
            )
        )

    def sort_staging_list(self, col):
        """
        右ペイン（登録待ち一覧）の列ヘッダークリックによる昇順/降順ソート。
        既存のNG一覧（ui.ng_input_window.NgInputWindow.sort_ng_list()）・
        仕掛一覧（ui.wip_expansion_window.WipExpansionWindow.sort_wip_list()）
        と全く同じパターン（列ごとの昇順/降順トグル、Treeview.move()での
        並べ替え）をそのまま踏襲する。
        """
        numeric_cols = {"daily_qty"}

        def sort_key(value):
            if col in numeric_cols:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

        ascending = self._staging_sort_states.get(col, True)

        items = [
            (self.tree.set(iid, col), iid)
            for iid in self.tree.get_children("")
        ]
        items.sort(key=lambda t: sort_key(t[0]), reverse=not ascending)

        for index, (_, iid) in enumerate(items):
            self.tree.move(iid, "", index)

        self._staging_sort_states[col] = not ascending

    # ------------------------------------------------------------------
    # 左ペイン（候補一覧）
    # ------------------------------------------------------------------

    def _on_staging_select(self, event=None):
        """
        右ペイン（登録待ち一覧）の選択イベント。DBアクセスを伴う候補再照合
        （_populate_candidates()）は矢印キー連打中に毎回実行しないよう
        デバウンスする（ui.kitting_production_entry.KittingProductionEntryWindow.
        on_select_plan_list()と同じパターン）。
        """
        sel = self.tree.selection()
        if not sel:
            self._clear_candidate_pane()
            return

        self._pending_candidate_select_iid = sel[0]
        if self._candidate_select_debounce_id is not None:
            self.after_cancel(self._candidate_select_debounce_id)
        self._candidate_select_debounce_id = self.after(
            self.CANDIDATE_SELECT_DEBOUNCE_MS, self._on_staging_select_debounced,
        )

    def _on_staging_select_debounced(self):
        self._candidate_select_debounce_id = None
        iid = self._pending_candidate_select_iid
        row = self._row_by_iid.get(iid)
        if row is None:
            self._clear_candidate_pane()
            return

        self._current_staging_iid = iid
        self._current_staging_row = row
        self._populate_candidates(row)

    def _populate_candidates(self, row):
        """
        右ペインで選択された保留行(row)の候補を左ペインへ表示する。以前
        ui.plan_candidate_dialog.select_plan_candidate_by_lot()が担っていた
        判定・整形ロジックをそのまま踏襲する：
          - matched（製品名まで一致した候補）を優先表示し、0件の場合のみ
            candidates（lot_no一致の全件）へフォールバックする。
          - _sort_candidates_by_closeness()で、日数差・数量差の複合スコアで
            並べ替える。
          - _is_large_qty_diff()で数量差の大きい候補、_is_large_date_diff()で
            払い出し日と生産予定日の差が大きい候補をハイライトする（一覧からは
            除外しない）。両方に該当する場合の色の組み合わせ方はui.plan_
            candidate_dialog._show_candidate_list_dialog()のdocstring参照。
          - is_auto_confirmable()で、計画数と実績数が完全一致し、かつ生産予定日
            と払い出し日の差が24時間以内の候補を緑系でハイライトする
            （"auto_confirmable"、_create_candidate_widgets()のtag_configure
            コメント参照：数量差・日付差ハイライトとは数学的に排他のため優先順位の
            衝突は実質発生しないが、念のため最優先で判定する）。
          - _format_planned_qty_cell()で、計画数（planned_qty）セルに実績数との
            差分を併記する（例："450（差-50）"）。行全体のlarge_diffタグ
            （20%超のみ発火）とは独立に、差があれば常に表示する、より細かい
            粒度の指標。Tkinterの標準Treeviewはセル単位の背景色指定に対応して
            いないため、色ではなくテキストへの差分埋め込みで実現している
            （詳細はui.plan_candidate_dialog._format_planned_qty_cell()参照）。
        """
        for item in self.tree_candidates.get_children():
            self.tree_candidates.delete(item)
        self._candidates_by_iid = {}

        candidates = row.get("candidates") or []
        matched = row.get("matched") or []
        daily_qty = row.get("daily_qty")
        report_date = row.get("report_date")

        using_fallback = not matched
        effective_candidates = candidates if using_fallback else matched
        effective_candidates = _sort_candidates_by_closeness(effective_candidates, report_date, daily_qty)

        if not effective_candidates:
            hint = f"ロットNo. {row.get('lot_no')} に該当する計画が見つかりません。"
        else:
            hint = (
                f"ロットNo. {row.get('lot_no')}（製品名: {row.get('product_name')}）"
                f"の候補：{len(effective_candidates)}件"
            )
            if daily_qty is not None:
                hint += f"　当日実績数：{_format_qty(daily_qty)}（計画数と見比べてご確認ください）"
            if using_fallback:
                hint += "\n※ 製品名が完全一致する候補が無いため、ロットNo.が一致する全ての候補を表示しています。"
        self.lbl_candidate_hint.config(text=hint)

        for candidate in effective_candidates:
            auto_flag = is_auto_confirmable(
                row.get("lot_no"), row.get("product_name"),
                candidate.get("planned_qty"), daily_qty,
                candidate.get("plan_start_datetime"), report_date,
            )
            qty_flag = _is_large_qty_diff(candidate, daily_qty)
            date_flag = _is_large_date_diff(report_date, candidate.get("plan_start_datetime"))
            side1_only_classification = classify_side1_only_plan(candidate)
            if using_fallback:
                # 製品名不一致（2026-10-08追加）：この分岐で表示されている
                # 候補は、ロットNoは一致するが製品名が一致する候補が無い
                # （row["matched"]が空）ために表示されているフォールバック
                # 候補であり、全件が製品名不一致である。他の全条件より優先して
                # 割り当てる（要求「同じ行に複数の表示条件が当てはまる場合は、
                # 製品名不一致のハイライトを優先する」に対応）。
                tags = ("product_name_mismatch",)
            elif side1_only_classification == SIDE1_ONLY_CLASS_WAITING_SIDE2:
                tags = ("needs_side2_wait",)
            elif side1_only_classification == SIDE1_ONLY_CLASS_UNREGISTERED:
                tags = ("side_master_unregistered",)
            elif auto_flag:
                tags = ("auto_confirmable",)
            elif qty_flag and date_flag:
                tags = ("large_diff_both",)
            elif qty_flag:
                tags = ("large_diff",)
            elif date_flag:
                tags = ("large_date_diff",)
            else:
                tags = ()
            iid = self.tree_candidates.insert("", tk.END, tags=tags, values=(
                candidate.get("lot_no") or "",
                candidate.get("board_name") or "",
                candidate.get("setup_file_no") or "",
                _format_side(candidate.get("production_side")),
                candidate.get("plan_start_datetime") or "",
                _format_planned_qty_cell(candidate.get("planned_qty"), daily_qty),
                _format_qty(candidate.get("order_qty")),
            ))
            self._candidates_by_iid[iid] = candidate

    def _clear_candidate_pane(self):
        """
        左ペインを空にする（右ペインで選択が無い・選択中の行が削除された
        場合）。
        """
        for item in self.tree_candidates.get_children():
            self.tree_candidates.delete(item)
        self._candidates_by_iid = {}
        self._current_staging_iid = None
        self._current_staging_row = None
        self.lbl_candidate_hint.config(text="右の登録待ち一覧から行を選択してください。")

    def _on_candidate_double_click(self, event):
        iid = self.tree_candidates.identify_row(event.y)
        if not iid:
            return
        candidate = self._candidates_by_iid.get(iid)
        if candidate is None:
            return
        self._confirm_candidate(candidate)

    def _make_remove_callback(self, staging_iid):
        """
        _confirm_candidate()（ダブルクリック）・_register_candidate_immediately()
        （右クリック、Shift+Sの一括登録もここ経由）の両方で使う
        remove_callbackを組み立てる（登録成功時にparent._perform_registration()
        から呼ばれ、右ペイン・DBから該当行を消す）。staging_iidをクロージャで
        捕まえておくことで、確定操作の時点で選択されていた行を、その後別の行が
        選択されても正しく指し続ける。

        次の行の自動選択（2026-10-07追加、画面修正5項目§4）：ダブルクリック・
        右クリック・Shift+Sのいずれで登録しても、ここで共通に処理する
        ことで戻り方を統一する（以前は右クリック経路（_register_candidate_
        immediately()）だけが個別に次の行を計算していたため、ダブルクリック
        からの登録では次の行が選択されないという抜けがあった）。
        「現在の並び順・絞り込みで表示されている順」＝self.tree.next()/prev()が
        そのまま対応する（ソート・絞り込みの結果はTreeviewの行の並び順に
        反映されているため）。削除前のうちに次の行を記録しておく（削除後に
        next()を呼んでも、既に消えたiidを起点にはできない）。
        次の行が無い（登録した行が最後の行だった）場合はprev()にフォールバック
        することで、新しい最後の行を選択する。両方とも無い（一覧がこの1行のみ
        だった）場合はtree.selection_set(())で選択を空にする
        （一覧が空になった場合は何も選択しない、という要件に対応）。
        一括登録（Shift+S）では、この呼び出しごとの選択は後続の
        呼び出し・バッチ末尾の選択処理で上書きされるため、最終的な選択状態は
        バッチ単位の方針（_on_bulk_register()参照）の通りになる。
        """
        def remove_callback():
            if staging_iid in self._row_by_iid:
                pending_row_id = self._row_by_iid[staging_iid].get("pending_row_id")
                next_iid = self.tree.next(staging_iid) or self.tree.prev(staging_iid)
                self.tree.delete(staging_iid)
                del self._row_by_iid[staging_iid]
                # 登録済みになった行は履歴として残さず、pending_csv_import_rowsから
                # 即座に物理削除する（models.production_import_stagingの方針）。
                if pending_row_id is not None:
                    delete_pending_csv_import_row(pending_row_id)
                # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする
                # （存在しなくなった行の候補を表示し続けないため）。
                if self._current_staging_iid == staging_iid:
                    self._clear_candidate_pane()
                # 登録により一覧から1行減るため、件数表示（「表示中：N件」）を
                # 最新化する（単一登録・一括登録（Shift+S）いずれも
                # このremove_callback経由で行が消えるため、ここ1箇所への
                # 追加で全て反映される）。
                self._update_status_label()

                if next_iid and self.tree.exists(next_iid):
                    self.tree.selection_set(next_iid)
                    self.tree.see(next_iid)
                else:
                    self.tree.selection_set(())

        return remove_callback

    def _confirm_candidate(self, candidate):
        """
        左ペインの候補をダブルクリックで確定した際の処理。以前ui.kitting_
        production_entry.KittingProductionEntryWindow._on_csv_staging_row_
        confirmed()が担っていた処理のうち、候補選択（select_plan_candidate_
        by_lot()モーダルダイアログの呼び出しと戻り値の受け取り）より後の部分
        （転記・登録準備）を、本ウインドウのメソッドとして移植したもの。
        select_plan_candidate_by_lot()自体はこの経路では呼ばない（左ペイン
        での選択が既にその代替のため）。

        self._parentはui.kitting_production_entry.KittingProductionEntry
        Windowのインスタンスであり、そのsearch_plan()・entry_daily_qty・
        _pending_csv_row_removal・_pending_csv_report_dateを直接呼び出す/
        書き換える。これらはウインドウの前面・フォーカス状態に依存しない
        （前回の調査で確認済み）ため、本ウインドウを閉じずに実行できる。
        parent側は最後にentry_daily_qty.focus_set()を呼ぶことで（Windows上の
        本Tk環境では既に確認済みの通り）実質的に前面へ上がってくるため、
        ユーザーはそのまま生産実績入力画面で入力を続けられる。

        従来通り、実際の登録（_start_registration()→_perform_registration()）は
        呼ばない。あくまで転記のみで、ユーザーがNG面1/面2を確認した上で
        「登録」ボタンまたはEnterで確定する一直線フローに乗せる
        （即時登録したい場合は右クリック→_register_candidate_immediately()を
        使う）。
        """
        row = self._current_staging_row
        staging_iid = self._current_staging_iid
        if row is None or staging_iid is None:
            return

        self._parent.search_plan(candidate["kitting_list_no"], lot_no=candidate["lot_no"])
        self._parent.entry_daily_qty.delete(0, tk.END)
        self._parent.entry_daily_qty.insert(0, f"{row['daily_qty']:g}")
        self._parent._pending_csv_row_removal = self._make_remove_callback(staging_iid)
        self._parent._pending_csv_report_date = row.get("report_date")
        self._parent.entry_daily_qty.focus_set()

    def _on_candidate_right_click(self, event):
        """
        右クリックされた候補について、確認ダイアログを経由せず即座に登録する
        （右クリック＝即時登録、ダブルクリック＝従来の一直線フローへの転記、
        という使い分け）。
        """
        iid = self.tree_candidates.identify_row(event.y)
        if not iid:
            return
        self.tree_candidates.selection_set(iid)
        candidate = self._candidates_by_iid.get(iid)
        if candidate is None:
            return
        row = self._current_staging_row
        staging_iid = self._current_staging_iid
        if row is None or staging_iid is None:
            return
        self._register_candidate_immediately(row, staging_iid, candidate)

    def _register_candidate_immediately(self, row, staging_iid, candidate, record_history=True):
        """
        右クリックでの即時登録。_confirm_candidate()と同じ転記処理を行った上で、
        続けてparent._perform_registration()を直接呼び、確認ダイアログ
        （parent._show_registration_confirm_dialog()）を経由せず即座に登録を
        完了させる。

        record_history：Trueの場合（デフォルト）、parent._perform_registration()に
        そのまま渡り、登録成功後に即座にlot_status_historyへ記録される
        （右クリックでの単発即時登録は、この既定動作のまま変更しない）。
        _on_bulk_register()（Shift+S一括登録）からFalseを渡された場合は、
        この記録をスキップし、_on_bulk_register()側がバッチ処理の最後に
        distinctなlot_no単位でまとめて記録する（2026-09-30追加）。

        row・staging_iidを引数として明示的に受け取る（self._current_staging_
        row/iidを暗黙に参照しない）理由：_on_bulk_register()（Shift+S一括登録）
        から、右ペインで複数選択された各行に対して順番に呼び出すため。左ペイン
        の「現在表示中の候補」の選択状態（self._current_staging_row/iid）と、
        一括登録でこれから処理する行は必ずしも一致しない（一括登録は右ペインの
        複数選択に基づき、左ペインの表示は特定の1行のみを指すため）。

        確認ダイアログの回避方法について（検討結果）：parent._perform_
        registration(daily_qty, preview) 自体には確認ダイアログは含まれて
        いない。確認は、その手前の一直線フローの入口であるparent._start_
        registration()が、NG入力欄の検証（_validate_ng_inputs()）→preview
        の組み立て（_build_registration_preview()）→確認ダイアログ
        （_show_registration_confirm_dialog()）→承認されたら_perform_
        registration()、という順で行っている。したがって_perform_
        registration()自体には一切手を加えず、_start_registration()を
        経由しない（_build_registration_preview()を直接呼んでpreviewを
        組み立て、_perform_registration()へ直接渡す）ことで、確認ダイアログを
        自然にスキップできる。

        登録後の数量差チェック（2026-09-24追加）：右クリック即時登録は
        is_auto_confirmable()のような数量完全一致要件を課さないため（要件を
        満たさない候補でも即座に登録できる、AB-7参照）、実績数（daily_qty）と
        計画数（candidate["planned_qty"]）が異なる登録が発生しうる。この場合、
        従来の完了メッセージ（messagebox.showinfo）をそのまま出さず、完了内容に
        「数量を修正しますか？」という選択肢を追加したaskyesnoに差し替える。
        「はい」を選ぶと該当計画を再度開き、登録済みの実績数を実績記入欄へ
        転記してフォーカスを移す（そこから訂正して通常の一直線Enterフローで
        再登録すればoverwrite_daily_result()により上書きされる）。数量が一致
        していた場合、または「いいえ」を選んだ場合は、従来通りの完了メッセージ
        （元のmessagebox.showinfo()の内容をそのまま流用）のみ表示する。
        一括登録（Shift+S、_on_bulk_register()）はis_auto_confirmable()で
        数量完全一致の候補のみを対象にするため、この経路でここに到達する
        candidateは常にplanned_qty == daily_qtyであり、この分岐が発火する
        ことはない。

        NG面1・面2は対象にしない（save_qty_by_side={}で登録する）。
        is_auto_confirmable()の自動確定要件はNG数量を考慮しない実績数量・
        日付のみの判定であり、即時登録もその範囲（実績数量の登録のみ）に
        留めるのが安全なため（NG入力欄に他の計画から持ち越された値が
        誤って使われるリスクも避けられる。search_plan()が呼ぶ_setup_ng_
        side_ui()により、NG入力欄自体は新しい計画の状態に更新されるが、
        その値を今回のNG申告として使うかどうかは別問題であり、ここでは
        意図的に使わない）。

        面連動（反対側の面への自動登録）：_perform_registration()内部の
        _register_opposite_side_daily_result()呼び出しがそのまま働くため、
        通常の登録フローと同じく自動的に行われる。
        """
        parent = self._parent
        daily_qty = row["daily_qty"]
        kitting_list_no = candidate["kitting_list_no"]
        lot_no = candidate["lot_no"]
        planned_qty = candidate.get("planned_qty")

        # 既にこの計画に実績が登録されている場合、黙って上書きせず確認
        # ダイアログを出す（2026-10-06追加）。一括登録（_on_bulk_register()）は
        # この条件に該当する行を事前に除外しているため、通常は一括登録経由で
        # ここに到達することはないが、念のため経路を問わず常にこの確認を行う。
        # 「いいえ」の場合は何も変更せず（検索欄の転記も行わず）即座に戻る。
        # 保留行（staging_iid）もそのまま残る。
        if not parent.confirm_overwrite_if_existing(kitting_list_no, lot_no, daily_qty, row.get("report_date")):
            return

        # 候補の計画が面1のみで、生産面マスターの分類が「面2待ち」または
        # 「生産面マスター未登録」の場合、理由を示した確認ダイアログを出す
        # （2026-10-07新設、D-9x参照。登録そのものは禁止しない。一括登録は
        # この条件に該当する行を事前に除外しているため、通常は一括登録経由で
        # ここに到達することはないが、念のため経路を問わず常にこの確認を行う）。
        if not _confirm_side1_only_if_applicable(parent, candidate):
            return

        parent.search_plan(kitting_list_no, lot_no=lot_no)
        parent.entry_daily_qty.delete(0, tk.END)
        parent.entry_daily_qty.insert(0, f"{daily_qty:g}")
        parent._pending_csv_row_removal = self._make_remove_callback(staging_iid)
        parent._pending_csv_report_date = row.get("report_date")

        preview = parent._build_registration_preview(daily_qty, {})

        # _perform_registration()は登録完了のたびにmessagebox.showinfo()で
        # 完了メッセージ（アプリ入力累計・反対側連動・NG申告・エラー等を含む）
        # を表示するが、数量差があった場合はこれをそのまま出さず、修正への
        # 選択肢を追加したダイアログに差し替えたい。そのため、_on_bulk_
        # register()と同じ手法（messagebox.showinfo()の一時的な無効化、
        # try/finallyで必ず復元）で、いったん元の完了メッセージの内容だけを
        # 捕捉し、実際の表示は本メソッド側で行う（内容そのものは捨てず、
        # 数量が一致していた場合はそのまま流用する＝「従来通りの完了メッセージ
        # のみ表示」という要件を満たす）。
        captured = []
        original_showinfo = messagebox.showinfo
        messagebox.showinfo = lambda title, message, **kw: captured.append((title, message))
        try:
            parent._perform_registration(daily_qty, preview, record_history=record_history)
        finally:
            messagebox.showinfo = original_showinfo

        if captured:
            completion_title, completion_message = captured[0]
        else:
            # _perform_registration()がエラー等でmessagebox.showinfo()に
            # 到達しなかった場合（実績登録自体に失敗した場合）のフォールバック。
            completion_title, completion_message = "登録完了", f"実績を登録しました（実績数：{daily_qty:g}）。"

        try:
            mismatch = planned_qty is not None and float(daily_qty) != float(planned_qty)
        except (TypeError, ValueError):
            mismatch = False

        # 完了・数量差ダイアログのparentについて（2026-10-07修正、画面修正
        # 5項目§4）：以前はparent.winfo_toplevel()（生産実績入力画面）を
        # parentにしていたため、このダイアログを閉じると（モーダルダイアログは
        # 閉じた際にそのparentへフォーカス・前面を戻すOS/Tkの挙動のため）
        # 生産実績入力画面が前面に戻ってしまい、直前にparent._perform_
        # registration()内で行っていた登録待ち一覧への前面復帰・フォーカス
        # 復帰（from_csv_staging分岐、D-84）を上書きしてしまっていた
        # （ダブルクリックからの登録は、完了ダイアログの表示がparent._perform_
        # registration()内部でこの前面復帰より先に行われるため、この問題が
        # 起きなかった）。self.winfo_toplevel()（本ウインドウ自身）をparentに
        # 変更することで、ダイアログを閉じた後も本ウインドウが前面に残る。
        fix_in_entry_window = False
        if not mismatch:
            messagebox.showinfo(completion_title, completion_message, parent=self.winfo_toplevel())
        else:
            diff = float(daily_qty) - float(planned_qty)
            message = (
                f"{completion_message}\n\n"
                f"実績数（{daily_qty:g}）が計画数（{planned_qty:g}、差{diff:+g}）と異なります。"
                "数量を修正しますか？"
            )
            if messagebox.askyesno(completion_title, message, parent=self.winfo_toplevel()):
                # 該当計画を再度開き、登録済みの実績数（daily_qty）を実績記入欄へ
                # 転記した上でフォーカスを移す。ユーザーがここから値を訂正し、
                # 通常の一直線Enterフロー（Enter→確認ダイアログ→登録）で
                # 再登録すればoverwrite_daily_result()により上書きされる。
                # この場合のみ、ユーザーが明示的に選んだ行き先（生産実績入力
                # 画面）へフォーカスを渡したままにする（下のfix_in_entry_window
                # ガードで、登録待ち一覧への復帰処理をスキップする）。
                parent.search_plan(kitting_list_no, lot_no=lot_no)
                parent.entry_daily_qty.delete(0, tk.END)
                parent.entry_daily_qty.insert(0, f"{daily_qty:g}")
                parent.entry_daily_qty.focus_set()
                fix_in_entry_window = True

        # ダブルクリック・Shift+Sと同じ戻り方に揃えるため、ここでも
        # 登録待ち一覧ウインドウを前面に戻し、キーボードフォーカスも一覧へ
        # 渡す（2026-10-07追加、画面修正5項目§4）。次の行の選択自体は
        # remove_callback（_make_remove_callback()）側で既に行われている。
        if not fix_in_entry_window:
            self._refocus_staging_window()

    def _refocus_staging_window(self):
        """
        登録待ち一覧ウインドウ（本ウインドウ）を前面に戻し、キーボード
        フォーカスも一覧（self.tree）へ渡す。lift()だけではTkのキーボード
        フォーカスは移動しない（D-84と同じ注意点）ため、focus_force()・
        tree.focus_set()を併用する。最小化（アイコン化）されていた場合は
        先にdeiconify()する（ui.kitting_production_entry.KittingProduction
        EntryWindow._perform_registration()のiconic対策と同じ考え方）。
        """
        if self.state() == "iconic":
            self.deiconify()
        self.lift()
        self.focus_force()
        self.tree.focus_set()

    def _on_bulk_register(self, event=None):
        """
        Shift+Sショートカット：右ペイン（登録待ち一覧）で選択中の行のうち、
        evaluate_auto_register_eligibility()をAUTO_REGISTER_MAX_DAYS_SHIFT_S
        （1日以内）で判定して合格したものだけを一括で即時登録する。

        条件自体は従来から変更していない（候補が1件、製品名一致、数量が
        完全一致、日付の差が1日以内、既存実績・重複候補・面2待ち／生産面
        マスター未登録に当たらない。選択した行のみ対象）。本体の処理は
        _execute_bulk_register()に共通化した（2026-10-08、D-101。Shift+Qの
        追加に伴い、Shift+S・Shift+Qの一括登録ロジックの違いを「許容日数・
        事前確認の有無」だけに絞るため）。
        """
        self._execute_bulk_register(
            max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_S,
            shortcut_label="Shift+S",
            require_confirm=False,
            trigger_source="bulk_register",
        )

    def _on_bulk_register_extended(self, event=None):
        """
        Shift+Qショートカット（2026-10-08新設、D-101）：Shift+Sと同じ対象
        （右ペインで選択中の行のみ）・同じ条件だが、日付の差の許容日数だけを
        4日以内に緩める。それ以外の条件（候補が1件、製品名一致、数量が完全
        一致、既存実績・重複候補・面2待ち／生産面マスター未登録に当たらない）
        はShift+Sと完全に同一（evaluate_auto_register_eligibility()を
        共通で通るため）。

        利用者の決定により、選択に関係なく一覧の全行を対象にする動作は
        採用しない（以前Shift+Qの初期実装でこの動作を試みたが、同日中に
        取り消した経緯がある。D-100参照）。

        Shift+Sと異なり、実行前に対象件数（選択行数・登録される行数・その
        うち日付の差が2〜4日の行数＝Shift+Sでは登録されない行）を示す確認
        ダイアログを出す（require_confirm=True、_execute_bulk_register()
        参照）。
        """
        self._execute_bulk_register(
            max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_Q,
            shortcut_label="Shift+Q",
            require_confirm=True,
            trigger_source="bulk_register_shift_q",
        )

    def _execute_bulk_register(self, max_date_diff_days, shortcut_label, require_confirm, trigger_source):
        """
        Shift+S・Shift+Qの共通本体（2026-10-08新設、D-101）。右ペイン
        （登録待ち一覧）で選択中の行のうち、evaluate_auto_register_
        eligibility()をmax_date_diff_daysで判定して合格したものだけを
        一括で即時登録する。対象は選択中の行のみ（選択が無い場合は何もしない。
        Shift+S・Shift+Qいずれも同じ方針）。

        各対象行についてfind_matching_plan_items()で候補を都度再照合する
        （選択・表示時点でキャッシュされたrow["candidates"]/row["matched"]は
        古くなっている可能性があるため使わない。_load_staged_rows_from_db()の
        docstring参照）。group_active_plan_items_by_lot()は選択件数分の
        N+1を避けるためループの前に1回だけ取得する（CSV取込時と同じ考え方）。

        require_confirm：Trueの場合（Shift+Q）、実際に登録する前に対象件数
        （選択行数・登録される行数・そのうち日付の差が2日以上の行数＝
        AUTO_REGISTER_MAX_DAYS_SHIFT_S（1日）を超える、Shift+Sでは登録され
        ない行）を示す確認ダイアログを出し、「いいえ」の場合は何も変更せず
        終了する（この時点ではまだ候補の解決・判定のみで、登録・削除は一切
        行っていない）。Shift+S（require_confirm=False）はこの確認を出さず、
        従来通り即座に登録する。

        要件を満たさない行は理由ごとに件数を記録してスキップする（削除・
        除外等の副作用は一切与えない。ステージング一覧にそのまま残り続ける）。
        完了時にこの理由ごとの件数を表示する（2026-10-08追加、D-101。以前は
        Shift+Sの完了メッセージに理由の内訳が無く、「要件を満たさない」の
        合計件数しか分からなかった）。

        非同期化について（検討結果、Shift+S実装時から変更なし）：1件あたりの
        登録処理はDB書き込み数件程度で、選択件数が数十〜百件程度までは体感
        できる遅延にはならないと判断し、LoadingWindowによる非同期化は行わ
        なかった。

        完了通知の抑制について：_perform_registration()は登録成功のたびに
        messagebox.showinfo("登録完了", ...)を表示するが、複数件の一括操作で
        逐一クリックさせるのは一括操作の趣旨に反するため、本メソッドの実行中
        のみ一時的にmessagebox.showinfo()を無効化し、最後に1回だけ件数
        サマリを表示する（try/finallyで必ず元に戻す）。エラーダイアログ
        （messagebox.showerror）は抑制しない（個別の失敗は稀であり、必ず
        ユーザーの目に入るようにするため）。

        登録成功の判定：_perform_registration()自体は成功/失敗を戻り値で
        知らせないため、呼び出し後にiidがself._row_by_iidから消えているか
        （remove_callbackが実行されたか）で判定する。

        完了後の自動選択（2026-09-24追加）：複数行が同時に削除されるため、
        「対象行のうちTreeview上で最後だった行の次の行（対象行以外で最初に
        来る後続行）」を選択する方針を採用した（対象行が最初に選択されていた
        中の生き残りではなく、常に「一括処理した範囲の直後」を選ぶ方が、
        次にどこから作業を再開すべきかが分かりやすいと判断したため）。
        対象行はremove_callback（_make_remove_callback()）呼び出しのたびに
        個別の「次の行」選択も行うが、本メソッドの末尾で改めてselection_set()
        により上書きするため、最終的な選択状態はこの方針の通りになる。
        """
        selected_iids = list(self.tree.selection())
        if not selected_iids:
            return

        # 削除が始まる前に、対象行のうちTreeview上で最後の位置にある行を
        # 特定し、その直後（対象行を除く）を「一括処理後に選ぶべき行」として
        # 記録しておく（削除が進むとtree.next()等で辿れなくなるため）。
        # 対象範囲の直後に（対象以外の）行が無い場合＝対象行が一覧の末尾まで
        # 含んでいた場合は、単一行登録時の「最後の行だった場合は新しい最後の
        # 行を選択する」という方針に合わせ、対象範囲より前にある直近の
        # 非対象行（＝削除後の新しい最後の行）にフォールバックする
        # （2026-10-07追加、画面修正5項目§4）。
        ordered_all = list(self.tree.get_children())
        selected_set = set(selected_iids)
        indices = [ordered_all.index(iid) for iid in selected_iids if iid in ordered_all]
        next_after_batch_iid = None
        if indices:
            last_index = max(indices)
            for cand in ordered_all[last_index + 1:]:
                if cand not in selected_set:
                    next_after_batch_iid = cand
                    break
            if next_after_batch_iid is None:
                first_index = min(indices)
                for cand in reversed(ordered_all[:first_index]):
                    if cand not in selected_set:
                        next_after_batch_iid = cand
                        break

        plan_items_by_lot = group_active_plan_items_by_lot()

        # 重複判定の事前集計：現在一覧に残っている全行（選択中かどうかを
        # 問わない）について、候補が1件に定まるものだけを対象に、同じ
        # (kitting_list_no, lot_no)を候補とする行が複数あるかを数える
        # （画面表示用の判定（_load_staged_rows_from_db()）とは独立に、
        # ここでも都度最新の状態で再判定する。find_matching_plan_items()の
        # キャッシュ結果（row["matched"]）は選択・表示時点のものなので使わず、
        # このブロックでも都度再照合する）。
        plan_key_counts = {}
        for other_iid, other_row in self._row_by_iid.items():
            other_normalized = normalize_product_name(other_row["product_name"])
            _, other_matched = find_matching_plan_items(
                other_row["lot_no"], other_normalized, plan_items_by_lot,
            )
            if len(other_matched) == 1:
                plan_key = (other_matched[0]["kitting_list_no"], other_row["lot_no"])
                plan_key_counts[plan_key] = plan_key_counts.get(plan_key, 0) + 1

        # 対象行ごとに判定する（登録はまだ行わない。require_confirm=Trueの
        # 場合、この時点の結果で確認ダイアログの件数を組み立てる）。
        evaluated = []
        for iid in selected_iids:
            row = self._row_by_iid.get(iid)
            if row is None:
                continue
            product_name_normalized = normalize_product_name(row["product_name"])
            candidates, matched = find_matching_plan_items(
                row["lot_no"], product_name_normalized, plan_items_by_lot,
            )
            eligibility, reason, candidate, date_diff = evaluate_auto_register_eligibility(
                row, candidates, matched, plan_key_counts, max_date_diff_days,
            )
            evaluated.append((iid, row, eligibility, reason, candidate, date_diff))

        eligible_items = [e for e in evaluated if e[2] == AUTO_REGISTER_ELIGIBLE]

        if require_confirm:
            # Shift+Sでは登録されない行＝日付の差がAUTO_REGISTER_MAX_DAYS_
            # SHIFT_S（1日）を超える行（他の条件は全てShift+Sと同一のため、
            # 合格した行の中でこの差だけがShift+Sとの違いになる）。
            shift_s_extra_count = sum(
                1 for e in eligible_items
                if e[5] is not None and e[5] > AUTO_REGISTER_MAX_DAYS_SHIFT_S
            )
            confirm_message = (
                f"選択した行数：{len(selected_iids)}件\n"
                f"登録される行数：{len(eligible_items)}件\n"
                f"　うち日付の差が2〜4日の行数（Shift+Sでは登録されない行）：{shift_s_extra_count}件\n\n"
                f"{shortcut_label}で一括登録します。よろしいですか？"
            )
            if not messagebox.askyesno(f"{shortcut_label}一括登録の確認", confirm_message, parent=self):
                return

        registered_count = 0
        failed_count = 0
        skip_reason_counts = {}
        # 登録に成功した行のlot_noを集める（重複除去、set）。反対面連動
        # （_perform_registration()内部）で影響するlot_noも常に同一lot_noの
        # ため、この集合に別途追加する必要はない（2026-09-30追加、
        # バッチ末尾でのlot_status_historyまとめ記録に使う）。
        affected_lot_nos = set()

        original_showinfo = messagebox.showinfo
        messagebox.showinfo = lambda *a, **kw: None
        try:
            for iid, row, eligibility, reason, candidate, _date_diff in evaluated:
                if eligibility != AUTO_REGISTER_ELIGIBLE:
                    skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
                    continue

                # record_history=False：一括登録では行ごとの即時記録を抑制し、
                # バッチ処理の最後にdistinctなlot_no単位でまとめて記録する
                # （2026-09-30追加。行ごとに記録すると1件あたり数十msの
                # DB書き込みコストが積み重なり、100件規模で数秒の遅延になる
                # ことが判明したための対応）。
                self._register_candidate_immediately(row, iid, candidate, record_history=False)
                if iid not in self._row_by_iid:
                    registered_count += 1
                    affected_lot_nos.add(candidate["lot_no"])
                else:
                    failed_count += 1
        finally:
            messagebox.showinfo = original_showinfo

        # バッチ処理全体の最後に、影響を受けたdistinctなlot_noそれぞれについて
        # 1回だけlot_status_historyへ記録する（2026-09-30追加）。1件の記録
        # 失敗が他のlot_noの記録・一括登録の完了通知自体を妨げないよう、
        # lot_noごとに個別にtry/exceptで捕捉する
        # （services.production_service._record_lot_status_snapshot_safely()と
        # 同じ方針だが、呼び出し元がUI層のため同じ形をここで実装する）。
        for lot_no in affected_lot_nos:
            try:
                record_lot_status_snapshot(lot_no, trigger_source)
            except Exception:
                logger.exception(
                    "lot_status_historyの記録に失敗しました（lot_no=%s, "
                    "trigger_source=%s）。一括登録処理自体はそのまま続行します。",
                    lot_no, trigger_source,
                )

        skipped_count = sum(skip_reason_counts.values())
        message = f"{registered_count}件を登録しました。\n{skipped_count}件は要件を満たさないためスキップしました。"
        skip_reason_lines = [
            f"　・{AUTO_REGISTER_SKIP_REASON_LABELS.get(reason, reason)}：{count}件"
            for reason, count in sorted(skip_reason_counts.items(), key=lambda kv: -kv[1])
        ]
        if skip_reason_lines:
            message += "\n" + "\n".join(skip_reason_lines)
        if failed_count:
            message += f"\n{failed_count}件は登録中にエラーが発生しました（詳細は個別のエラーダイアログを参照）。"
        messagebox.showinfo(f"{shortcut_label}一括登録完了", message, parent=self)

        # 事前に記録しておいた「一括処理範囲の直後の行」を選択する。存在しない
        # （末尾の行まで含む一括登録だった等）場合は選択状態を空にする。
        if next_after_batch_iid and self.tree.exists(next_after_batch_iid):
            self.tree.selection_set(next_after_batch_iid)
            self.tree.see(next_after_batch_iid)
        else:
            self.tree.selection_set(())

        # 一括登録の完了後、登録待ち一覧ウインドウを前面に戻し、キーボード
        # フォーカスも一覧へ戻す（2026-10-06追加・2026-10-07
        # _refocus_staging_window()に統合、Shift+Sが効かなくなる不具合の
        # 修正の一環。_perform_registration()が登録のたびにparent.entry_
        # daily_qty.focus_set()を呼ぶため、ここで明示的に戻さないと、次に
        # ショートカットを押した際にフォーカスが残った親ウインドウの
        # 実績数入力欄へ文字が入力されてしまう）。
        self._refocus_staging_window()

    # ------------------------------------------------------------------
    # ウインドウを閉じる
    # ------------------------------------------------------------------

    def _on_close(self):
        """
        ウインドウを閉じる前に、失われると困るデータが残っていないか順番に
        確認する。

        1. 不一致リスト（self._mismatched_rows）：「不一致として除外」した
           際に入力した理由（自由記述）を含むデータで、対応する
           pending_csv_import_rowsの行は_apply_mismatch()の時点で既に
           DBから物理削除済みのため、**このウインドウのメモリ上にしか
           存在しない**。CSV出力せずに閉じると完全に失われる。そのため、
           1件以上残っている場合はブロッキングの確認ダイアログ（askyesno）
           で出力を促す（_confirm_and_export_mismatched_before_close()）。
           「いいえ」を選んだ場合はテキストの通りデータが失われる前提で
           閉じる操作を続行する。ファイル選択自体をキャンセルした場合は
           「出力を促したのに何も出力されないまま閉じる」事故になるため、
           閉じる操作自体を中止する（ダイアログを閉じずに残す）。

        2. 未登録行（self._row_by_iid、pending_csv_import_rows）：以前は
           これも確認ダイアログだったが、ステージングデータの永続化により
           閉じても失われなくなった（メインメニューの「実績CSV取込状況」
           からいつでも再開できる）ため、確認ゲート自体は不要と判断済み
           （既存の非ブロッキングな案内のみ、_show_closing_notice()参照）。
           不一致リストの確認（ブロッキング、ウインドウを閉じる**前**に
           完結させる必要がある）→ウインドウを閉じる→未登録行の案内
           （非ブロッキング、閉じた**後**に表示）という順序になる。

        登録不可リスト（self._unregistrable_rows）については、同様の確認は
        追加しない：不一致リストと異なり、対応するpending_csv_import_rowsの
        行はCSV出力するまでDBに残ったまま（削除されるのはon_export_
        unregistrable_csv()の実行時のみ）であり、ウインドウを閉じても実データ
        は失われず、再度開けば同じ内容（"no_candidates"の行）が再構築される
        （reasonも人間の自由記述ではなく固定文言REASON_NO_CANDIDATESのため、
        再現不能な情報が失われる心配も無い）。
        """
        if self._mismatched_rows:
            if not self._confirm_and_export_mismatched_before_close():
                return

        remaining = len(self._row_by_iid)
        parent = self._parent
        self.destroy()
        if remaining:
            self._show_closing_notice(parent, remaining)

    def _confirm_and_export_mismatched_before_close(self):
        """
        不一致リストが1件以上残っている状態で閉じようとした際に呼ばれる。

        「はい」：on_export_mismatched_csv()をその場で実行する。実際に
        CSVへ出力できた場合（戻り値True）のみ、閉じる操作を続行してよいと
        判断する（True）。ファイル選択をキャンセルした・書き込みエラーに
        なった場合（戻り値False）は、出力を促したのに何も出力されないまま
        閉じてしまうことになるため、閉じる操作自体を中止する（False）。

        「いいえ」：データが失われることを理解した上での選択として扱い、
        出力せずに閉じてよい（True）。

        戻り値：True＝このままウインドウを閉じてよい、False＝閉じる操作を
        中止する（ダイアログを閉じずに残す）。
        """
        count = len(self._mismatched_rows)
        if not messagebox.askyesno(
            "不一致リスト未出力",
            f"不一致リストが未出力です（{count}件）。CSV出力しますか？\n"
            "「いいえ」を選ぶと、このデータは失われます。",
            parent=self,
        ):
            return True

        return self.on_export_mismatched_csv()

    @staticmethod
    def _show_closing_notice(parent, remaining_count):
        """
        親ウインドウ（呼び出し元のui.kitting_production_entry.
        KittingProductionEntryWindow）上に、自動的に消える非ブロッキングの
        案内を表示する。messagebox（「OK」クリックを要求するモーダル）は
        「閉じる操作自体を妨げない」という要件に合わないため使わず、
        枠のみのToplevel + after()による自動destroy()で実装した
        （実装コストが低く、閉じる処理をブロックしない方法として採用）。

        親ウインドウが既に閉じられている等、表示できない場合は静かに諦める
        （案内が出せないこと自体はエラー扱いしない。データはDBに永続化
        済みのため、通知が出せなくても実害は無い）。
        """
        try:
            if parent is None or not parent.winfo_exists():
                return
            notice = tk.Toplevel(parent)
            notice.overrideredirect(True)
            notice.attributes("-topmost", True)
            ttk.Label(
                notice,
                text=(
                    f"{remaining_count}件が未処理のまま残っています。\n"
                    "メインメニューの「実績CSV取込状況」からいつでも再開できます。"
                ),
                padding=10, background="#fff3cd", relief=tk.SOLID, borderwidth=1,
            ).pack()
            parent.update_idletasks()
            x = parent.winfo_rootx() + 40
            y = parent.winfo_rooty() + 40
            notice.geometry(f"+{x}+{y}")
            notice.after(3000, notice.destroy)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 登録不可リスト（machine判定："no_candidates"）
    # ------------------------------------------------------------------

    def on_export_unregistrable_csv(self):
        """
        登録不可（"no_candidates"＝該当lot_noの計画が見つからなかった）行を
        CSV出力する。該当行が1件も無い場合は出力せずメッセージのみ表示する。

        出力成功後、該当行を一覧（self.tree・self._row_by_iid）・DB
        （pending_csv_import_rows、delete_pending_csv_import_row()）からも
        削除する（self._unregistrable_rows自体は保持したまま：ウインドウを
        閉じるまで参照可能にしておく従来の方針は変えない）。DBから削除しないと、
        CSVに出力済みでも「未処理」のまま残り続け、次回ステージング一覧を
        開いた際に再び表示されてしまうため。
        """
        if not self._unregistrable_rows:
            messagebox.showinfo("登録不可リスト", "登録不可の行はありません。", parent=self)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_unregistrable.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._unregistrable_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        REASON_NO_CANDIDATES,
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return

        iids_to_remove = [
            iid for iid, row in self._row_by_iid.items()
            if row.get("status") == "no_candidates"
        ]
        for iid in iids_to_remove:
            pending_row_id = self._row_by_iid[iid].get("pending_row_id")
            self.tree.delete(iid)
            del self._row_by_iid[iid]
            # 除外済み（CSV出力で対応済みとした）行は履歴として残さず、
            # pending_csv_import_rowsから即座に物理削除する
            # （models.production_import_stagingの方針。__init__()参照）。
            if pending_row_id is not None:
                delete_pending_csv_import_row(pending_row_id)
            # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする。
            if self._current_staging_iid == iid:
                self._clear_candidate_pane()

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)

    # ------------------------------------------------------------------
    # 不一致として除外（人間の個別判断）
    # ------------------------------------------------------------------

    def _on_right_click(self, event):
        """
        右クリックされた行に対して「不一致として除外」のコンテキストメニューを
        表示する。

        複数行選択時の一括対応：右クリックされた行が、現在複数選択されている
        行の一部であれば、選択されている全行が対象になる（一括不一致マーク、
        _mark_multiple_as_mismatched()）。それ以外（未選択の状態でクリック、
        または選択範囲外の行をクリック）の場合は、クリックされた行1件のみを
        選択し直した上で対象にする（多くのアプリの右クリックの慣習と同じ
        挙動、かつ既存の単一行動作をそのまま維持する）。
        """
        clicked_iid = self.tree.identify_row(event.y)
        if not clicked_iid:
            return

        current_selection = self.tree.selection()
        if len(current_selection) > 1 and clicked_iid in current_selection:
            target_iids = list(current_selection)
        else:
            self.tree.selection_set(clicked_iid)
            target_iids = [clicked_iid]

        menu = tk.Menu(self, tearoff=0)
        if len(target_iids) > 1:
            menu.add_command(
                label=f"不一致として除外（選択中の{len(target_iids)}件）",
                command=lambda: self._mark_multiple_as_mismatched(target_iids),
            )
            menu.add_command(
                label=f"候補なしとする（選択中の{len(target_iids)}件）",
                command=lambda: self._mark_multiple_as_no_candidates(target_iids),
            )
        else:
            menu.add_command(
                label="不一致として除外",
                command=lambda: self._mark_as_mismatched(target_iids[0]),
            )
            menu.add_command(
                label="候補なしとする",
                command=lambda: self._mark_as_no_candidates(target_iids[0]),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _apply_mismatch(self, iid, reason):
        """
        1行を「不一致として除外」する共通処理（理由の入力は含まない）。
        一覧（self.tree・self._row_by_iid）・DB（pending_csv_import_rows）から
        即座に削除し、self._mismatched_rows（不一致リストCSV出力用、ウインドウを
        閉じるまで保持）へ追加する。self._unregistrable_rows（機械判定の
        "no_candidates"分はCSV出力時にまとめて削除、人間判断"候補なしとする"分は
        _apply_no_candidates()の時点で即座に削除）とは別枠で管理する
        （__init__()のコメント参照。既存の登録不可リストと混同しないため）。
        _mark_as_mismatched()（単一行）・_mark_multiple_as_mismatched()
        （複数行一括）の両方から、理由決定後に呼ばれる。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        pending_row_id = row.get("pending_row_id")
        self.tree.delete(iid)
        del self._row_by_iid[iid]
        if pending_row_id is not None:
            delete_pending_csv_import_row(pending_row_id)
        # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする。
        if self._current_staging_iid == iid:
            self._clear_candidate_pane()

        self._mismatched_rows.append({**row, "mismatch_reason": reason})

    def _mark_as_mismatched(self, iid):
        """
        人間が「この行に一致する計画は無い／違う」と判断した場合の、単一行の
        除外操作。除外理由：人間の個別判断のため理由は様々であり得る
        （ロットNoの入力ミス・対象外の製品・二重入力等）。REASON_NO_CANDIDATES
        （機械判定による固定文言1種類）とは異なり、任意入力（自由記述）の
        ダイアログで尋ね、空欄・キャンセル時はREASON_MISMATCH_DEFAULTを使う。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        reason = simpledialog.askstring(
            "不一致として除外",
            f"ロットNo. {row.get('lot_no')} を不一致として除外します。\n"
            "除外理由（任意）：",
            parent=self,
        )
        reason = (reason or "").strip() or REASON_MISMATCH_DEFAULT

        self._apply_mismatch(iid, reason)
        self._update_status_label()

    def _mark_multiple_as_mismatched(self, iids):
        """
        複数選択された行を一括で「不一致として除外」する（右ペインを複数選択
        状態で右クリックした場合）。理由は全行共通で1回だけ尋ね
        （simpledialog.askstring()、_mark_as_mismatched()と同じ任意入力・
        デフォルトフォールバック方式）、全行に同じ理由を適用する。
        """
        valid_iids = [iid for iid in iids if iid in self._row_by_iid]
        if not valid_iids:
            return

        lot_no_preview = "、".join(self._row_by_iid[iid].get("lot_no", "") for iid in valid_iids[:5])
        if len(valid_iids) > 5:
            lot_no_preview += " 他"
        reason = simpledialog.askstring(
            "不一致として除外",
            f"選択中の{len(valid_iids)}件（ロットNo: {lot_no_preview}）を"
            "まとめて不一致として除外します。\n除外理由（任意、全件共通）：",
            parent=self,
        )
        reason = (reason or "").strip() or REASON_MISMATCH_DEFAULT

        for iid in valid_iids:
            self._apply_mismatch(iid, reason)
        self._update_status_label()

    def _apply_no_candidates(self, iid):
        """
        1行を人間判断で「候補なしとする」（登録不可リストへ移動）共通処理。

        既存の機械判定によるself._unregistrable_rows（"no_candidates"、
        find_matching_plan_items()の候補が実際に0件だった行）と**同じ
        リストにそのまま統合する**。CSV出力時（on_export_unregistrable_csv()）の
        理由欄は、機械判定・人間判断のいずれもREASON_NO_CANDIDATESという同じ
        固定文言になる（両者を区別する専用の理由文言は導入しなかった。
        _unregistrable_rowsは元々「reasonは1種類の固定文言のみ」という前提の
        設計（_apply_mismatch()のdocstring参照）で、区別を導入するには
        行ごとに理由を保持する形へ拡張する必要があり実装コストが上がるため、
        今回は既存のREASON_NO_CANDIDATESをそのまま流用する方を選んだ）。

        対応するpending_csv_import_rowsのDB行は、_apply_mismatch()と同様に
        ここで即座に物理削除する（2026-09-23修正：以前は「CSV出力時にのみ
        削除」としていたが、ウインドウを閉じて再度開くと、この関数で一覧
        （self._row_by_iid）からは消したのにDB行は残ったままだったため、
        find_matching_plan_items()の再照合で通常の登録待ち一覧へ復活して
        しまう不具合があった。機械判定の"no_candidates"（このメソッドを
        経由しない、_load_staged_rows_from_db()がstatus="no_candidates"と
        判定した行）は元々候補が無いため再照合しても復活せず、CSV出力時に
        削除する従来方針のままで問題ないが、こちらの人間判断による除外は
        候補が実在する行にも適用され得るため、即座に削除しないと復活する
        リスクがあった）。CSV出力（on_export_unregistrable_csv()）側は
        まだ一覧に残っている（＝機械判定分の）行だけをDBから削除する処理の
        ままのため、ここで既に削除済みの行に対して重複して削除を試みる
        ことはない（該当行は既にself._row_by_iidに存在しないため対象外）。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        pending_row_id = row.get("pending_row_id")
        self.tree.delete(iid)
        del self._row_by_iid[iid]
        if pending_row_id is not None:
            delete_pending_csv_import_row(pending_row_id)
        # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする。
        if self._current_staging_iid == iid:
            self._clear_candidate_pane()

        self._unregistrable_rows.append(row)

    def _mark_as_no_candidates(self, iid):
        """
        人間が「この行には対応する計画が無いものとして扱ってよい」と判断
        した場合の、単一行の登録不可リストへの移動。理由は固定文言
        （REASON_NO_CANDIDATES）のため自由記述の入力は求めないが、
        誤クリックによる意図しない移動を避けるため確認ダイアログを挟む
        （_mark_as_mismatched()が自由記述の入力自体を実質的な確認として
        機能させているのと同じ考え方）。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        if not messagebox.askyesno(
            "候補なしとする",
            f"ロットNo. {row.get('lot_no')} を候補なしとして登録不可リストに移動します。よろしいですか？",
            parent=self,
        ):
            return

        self._apply_no_candidates(iid)
        self._update_status_label()

    def _mark_multiple_as_no_candidates(self, iids):
        """
        複数選択された行を一括で「候補なしとする」（登録不可リストへ移動）。
        _mark_multiple_as_mismatched()と同様、複数選択中の右クリックから
        呼ばれる。理由の自由記述は無いため1回の確認ダイアログのみで済ませる。
        """
        valid_iids = [iid for iid in iids if iid in self._row_by_iid]
        if not valid_iids:
            return

        lot_no_preview = "、".join(self._row_by_iid[iid].get("lot_no", "") for iid in valid_iids[:5])
        if len(valid_iids) > 5:
            lot_no_preview += " 他"
        if not messagebox.askyesno(
            "候補なしとする",
            f"選択中の{len(valid_iids)}件（ロットNo: {lot_no_preview}）を"
            "まとめて候補なしとして登録不可リストに移動します。よろしいですか？",
            parent=self,
        ):
            return

        for iid in valid_iids:
            self._apply_no_candidates(iid)
        self._update_status_label()

    def on_export_mismatched_csv(self):
        """
        「不一致として除外」された行（self._mismatched_rows）をCSV出力する。
        on_export_unregistrable_csv()と同じ形式（utf-8-sig、列構成：lot_no・
        product_name・daily_qty・report_date・reason）だが、reason列は行ごとに
        個別入力された除外理由をそのまま使う（REASON_NO_CANDIDATESのような
        固定文言1種類ではない）。

        既にpending_csv_import_rowsからの削除・一覧からの除去は
        _mark_as_mismatched()の時点で完了済みのため、ここではCSVへの書き出しの
        みを行う（on_export_unregistrable_csv()と異なり、削除処理は無い）。

        戻り値：実際にCSVへの書き出しが完了した場合True、それ以外
        （出力対象が無い・ファイル選択をキャンセルした・書き込みエラー）は
        False。ボタン押下（既存の呼び出し方）では戻り値は使われないが、
        _on_close()（ウインドウを閉じる前に未出力の不一致リストがあれば
        出力を促す、_confirm_and_export_mismatched_before_close()参照）が
        「実際に出力できたか」を判定するために利用する。
        """
        if not self._mismatched_rows:
            messagebox.showinfo("不一致リスト", "不一致として除外した行はありません。", parent=self)
            return True

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_mismatched.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return False

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._mismatched_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        row.get("mismatch_reason", REASON_MISMATCH_DEFAULT),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return False

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)
        return True

    # ------------------------------------------------------------------
    # 登録済みリスト（is_already_registered()がTrueと判定した行）
    # ------------------------------------------------------------------

    def add_already_registered_rows(self, rows):
        """
        新たなCSV取込で見つかった「登録済み」行を、既存のself._already_
        registered_rowsへ追記する。ui.kitting_production_entry.
        KittingProductionEntryWindow.open_pending_csv_staging_window()が、
        本ウインドウが既に開いている状態でもう一度CSV取込された場合に呼ぶ
        （lift()するだけでは今回の判定結果が失われてしまうため）。

        件数表示・詳細一覧ウインドウ（開いていれば）の両方を最新化する。
        """
        if not rows:
            return
        self._already_registered_rows.extend(rows)
        self._update_status_label()
        if self._already_registered_window is not None and self._already_registered_window.winfo_exists():
            self._populate_already_registered_tree()

    def on_show_already_registered_list(self):
        """
        「登録済みリストを表示」ボタン。既に開いていれば前面に出すだけ
        （多重に開かない、既存の他画面と同じ考え方）。1度目はここで
        Toplevel・Treeview・CSV出力ボタン・右クリックメニューを構築する。
        """
        if self._already_registered_window is not None and self._already_registered_window.winfo_exists():
            self._already_registered_window.lift()
            return

        window = tk.Toplevel(self)
        window.title("実績CSV取込：対象外一覧（登録済み／数量0）")
        window.geometry("980x400")
        center_window(window, self)
        window.transient(self)
        # 親（本ウインドウ）が最小化状態だとtransientウインドウが実際には
        # 表示されない既知の問題（UI_WORKFLOW_FIXES_NOTES.mdグループO参照）
        # への対策。
        if self.state() == "iconic":
            self.deiconify()

        ttk.Label(
            window,
            text=(
                "通常のステージング一覧には表示されなかった行です（理由は「理由」列を参照）。\n"
                "右クリックで通常の一覧に戻して訂正できます（複数選択中は選択中の全行が対象）。"
            ),
            foreground="gray", padding=(10, 10, 10, 0),
        ).pack(anchor=tk.W)

        action_frame = ttk.Frame(window, padding=(10, 5, 10, 10))
        action_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(
            action_frame, text="対象外一覧をCSV出力", command=self.on_export_already_registered_csv,
        ).pack(side=tk.LEFT)

        tree_frame = ttk.Frame(window, padding=(10, 5, 10, 0))
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = (
            "lot_no", "product_name", "daily_qty", "report_date",
            "matched_kitting_list_no", "existing_qty", "reason",
        )
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        tree.heading("lot_no", text="ロットNo")
        tree.heading("product_name", text="製品名")
        tree.heading("daily_qty", text="CSVの実績数")
        tree.heading("report_date", text="払い出し日（参考）")
        tree.heading("matched_kitting_list_no", text="一致した計画（キッティングリストNo）")
        tree.heading("existing_qty", text="登録済みの数量")
        tree.heading("reason", text="対象外の理由")
        tree.column("lot_no", width=100, anchor=tk.W)
        tree.column("product_name", width=180, anchor=tk.W)
        tree.column("daily_qty", width=90, anchor=tk.E)
        tree.column("report_date", width=120, anchor=tk.CENTER)
        tree.column("matched_kitting_list_no", width=170, anchor=tk.W)
        tree.column("existing_qty", width=100, anchor=tk.E)
        tree.column("reason", width=140, anchor=tk.W)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        tree.configure(yscrollcommand=vsb.set)
        vsb.configure(command=tree.yview)

        tree.bind("<Button-3>", self._on_already_registered_right_click)

        self._already_registered_window = window
        self._already_registered_tree = tree
        self._populate_already_registered_tree()

    def _populate_already_registered_tree(self):
        """
        self._already_registered_rowsの内容で、登録済みリスト詳細ウインドウの
        Treeviewを作り直す（全件クリア→再挿入）。add_already_registered_
        rows()・_revert_already_registered_rows()（戻す操作）の後に呼ぶ。
        """
        tree = self._already_registered_tree
        for item in tree.get_children():
            tree.delete(item)
        self._already_registered_by_iid = {}

        for entry in self._already_registered_rows:
            iid = tree.insert("", tk.END, values=(
                entry.get("lot_no", ""),
                entry.get("product_name", ""),
                entry.get("daily_qty", ""),
                entry.get("report_date") or "",
                entry.get("matched_kitting_list_no") or "",
                entry.get("existing_qty") if entry.get("existing_qty") is not None else "",
                EXCLUSION_REASON_LABELS.get(entry.get("reason"), entry.get("reason") or ""),
            ))
            self._already_registered_by_iid[iid] = entry

    def _on_already_registered_right_click(self, event):
        """
        登録済みリスト詳細ウインドウの右クリックメニュー。「通常の一覧に
        戻す（訂正する）」の1操作のみを提供する（ui.production_import_
        staging_window.ProductionImportStagingWindow._on_right_click()と
        同じ、複数選択時は選択中の全行が対象になる判定ロジック）。

        「候補なしとする」「不一致として除外」相当の操作について：ユーザー
        指示では両方の名称が挙がっていたが、実際にこの一覧の行に必要なのは
        「この判定は違う、通常の一覧に戻して確認・訂正させてほしい」という
        単一の操作であり、戻した後は通常のステージング一覧側の既存機能
        （右クリックの「不一致として除外」「候補なしとする」）でそのまま
        対応できる。そのため、この一覧専用の別々の2操作としては実装せず、
        「通常の一覧に戻す」1操作に統一した（判定しやすい方を選んだ、との
        指示に基づく判断）。
        """
        tree = self._already_registered_tree
        clicked_iid = tree.identify_row(event.y)
        if not clicked_iid:
            return

        current_selection = tree.selection()
        if len(current_selection) > 1 and clicked_iid in current_selection:
            target_iids = list(current_selection)
        else:
            tree.selection_set(clicked_iid)
            target_iids = [clicked_iid]

        menu = tk.Menu(self._already_registered_window, tearoff=0)
        label = "通常の一覧に戻す（訂正する）"
        if len(target_iids) > 1:
            label += f"（選択中の{len(target_iids)}件）"
        menu.add_command(label=label, command=lambda: self._revert_already_registered_rows(target_iids))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _revert_already_registered_rows(self, iids):
        """
        登録済みリストの行を、通常のpending_csv_import_rows（候補未確定の
        状態）へ再度INSERTし、右ペイン（登録待ち一覧）へ戻す。

        実装方法（判定しやすい方を選択）：対象行ごとにupsert_pending_csv_
        import_row()を呼んでDBへ書き戻した上で、右ペインは全件を_load_
        staged_rows_from_db()で読み直す（対象行だけを個別にTreeviewへ
        差し込むより、既存の全件再読み込みロジックをそのまま再利用する方が
        単純で、本操作の頻度（稀な訂正操作）であれば性能上の懸念も無いため）。
        """
        entries = [self._already_registered_by_iid[iid] for iid in iids if iid in self._already_registered_by_iid]
        if not entries:
            return

        count = len(entries)
        lot_no_preview = "、".join(e.get("lot_no", "") for e in entries[:5])
        if count > 5:
            lot_no_preview += " 他"
        if not messagebox.askyesno(
            "通常の一覧に戻す",
            f"選択中の{count}件（ロットNo: {lot_no_preview}）を通常の登録待ち一覧に戻します。"
            "よろしいですか？",
            parent=self._already_registered_window,
        ):
            return

        for entry in entries:
            upsert_pending_csv_import_row({
                "csv_row_no": entry.get("csv_row_no"),
                "lot_no": entry["lot_no"],
                "product_name": entry["product_name"],
                "daily_qty": entry["daily_qty"],
                "report_date": entry.get("report_date"),
                "worker_id": entry.get("worker_id"),
            }, import_batch_id=entry.get("import_batch_id"))
            self._already_registered_rows.remove(entry)

        # 右ペイン（登録待ち一覧）を全件読み直す（戻した行を候補付きで表示
        # するには、他の保留行と同じくfind_matching_plan_items()での
        # 再照合が必要なため、_load_staged_rows_from_db()をそのまま使う）。
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._row_by_iid = {}
        for row in _load_staged_rows_from_db():
            self._insert_staging_row(row)

        self._populate_already_registered_tree()
        self._update_status_label()

    def on_export_already_registered_csv(self):
        """
        登録済みリスト（self._already_registered_rows）をCSV出力する。
        on_export_unregistrable_csv()・on_export_mismatched_csv()と同じ形式
        （utf-8-sig）だが、対応するpending_csv_import_rowsの行がそもそも
        存在しない（最初から永続化されていない）ため、出力後に一覧・DBから
        何かを削除する処理は無い（あくまでスナップショットの保存であり、
        出力後も一覧から消えず、必要なら引き続き「戻す」操作が行える）。
        """
        if not self._already_registered_rows:
            messagebox.showinfo("対象外一覧", "対象外と判定された行はありません。", parent=self._already_registered_window)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_excluded_rows.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self._already_registered_window,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "lot_no", "product_name", "daily_qty", "report_date",
                    "matched_kitting_list_no", "existing_qty", "reason",
                ])
                for entry in self._already_registered_rows:
                    writer.writerow([
                        entry.get("lot_no", ""),
                        entry.get("product_name", ""),
                        entry.get("daily_qty", ""),
                        entry.get("report_date") or "",
                        entry.get("matched_kitting_list_no") or "",
                        entry.get("existing_qty") if entry.get("existing_qty") is not None else "",
                        EXCLUSION_REASON_LABELS.get(entry.get("reason"), entry.get("reason") or ""),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self._already_registered_window)
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self._already_registered_window)
