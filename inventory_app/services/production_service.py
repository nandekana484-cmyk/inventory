# services/production_service.py
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
    """
    record_lot_status_snapshot()を呼び、失敗しても例外を外へ伝播させない
    （実績の登録・修正という本来の処理を、履歴記録の失敗で失敗させないための
    ラッパー。record_lot_status_snapshot()自体は素直に例外を送出する設計の
    ため、呼び出し側の4関数それぞれで同じ形のtry/exceptを書く代わりに、
    重複コード削減のための共通ヘルパーとしてここに1つだけ定義する。
    「共通フックの一元化」ではなく、5箇所への個別呼び出し追加という方針
    自体は変えていない点に注意）。
    """
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
                            report_date: str = None, check_duplicate: bool = False,
                            record_history: bool = True):
    """
    当日実績を1件追加登録する。
    戻り値：更新後のアプリ内累計

    record_history：Trueの場合（デフォルト、既存の呼び出し元は挙動が変わらない）、
    登録成功後にそのロットの状態スナップショットをlot_status_historyへ即座に
    記録する。Falseを渡すと、この記録をスキップする（呼び出し元が自分で
    まとめて記録する場合に使う。2026-09-30追加：ui.production_import_staging_
    window.py::_on_bulk_register()のように、多数の行を連続処理する際、行ごとに
    record_lot_status_snapshot()を呼ぶと1件あたり数十msのDB書き込み
    （commit()の同期コスト）が積み重なり、100件規模で数秒の遅延になることが
    判明したための対応。バッチ処理側で影響を受けたlot_noの集合をため、
    処理の最後にlot_no単位で1回ずつ記録し直すことで、記録回数を
    「登録行数」から「distinctなlot_no数」へ減らす）。

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

    # 実績登録が成功した後に、そのロットの状態スナップショットを
    # lot_status_history へ記録する（2026-09-30追加）。記録自体が失敗しても
    # この登録処理は失敗させない（_record_lot_status_snapshot_safely()参照）。
    if record_history:
        _record_lot_status_snapshot_safely(lot_no, "register")

    return get_app_cumulative_qty(kitting_list_no, lot_no)


def overwrite_daily_result(kitting_list_no: str, lot_no: str, daily_qty: float, worker_id: str,
                             report_date: str = None, record_history: bool = True):
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

    report_date：明示的に指定されなかった場合（None）、以前は本関数がここで
    実行日（今日）へフォールバックしていたが、2026-09-29の調査で「手動で
    数量を修正・再登録するたびに、CSV由来の正しい日付が意図せず今日に
    書き換わってしまう」問題が判明したため、**Noneのまま
    models.production.replace_daily_result()へ渡す**ように変更した。
    replace_daily_result()側で、削除対象となる既存行のreport_dateを自動的に
    引き継ぐ（該当行が無い場合のみ今日にフォールバックする、詳細は
    replace_daily_result()のdocstring参照）。
    戻り値：更新後のアプリ内累計

    record_history：register_daily_result()と同じ（デフォルトTrueで即座に記録、
    Falseでスキップ）。理由・使い方もregister_daily_result()のdocstring参照。
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

    # register_daily_result()と同じ理由でlot_status_historyへ記録する
    # （2026-09-30追加）。
    if record_history:
        _record_lot_status_snapshot_safely(lot_no, "overwrite")

    return get_app_cumulative_qty(kitting_list_no, lot_no)


def register_opposite_side_daily_result(plan: dict, daily_qty: float, worker_id: str,
                                          report_date: str = None, record_history: bool = True):
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

    record_history：register_daily_result()/overwrite_daily_result()へそのまま
    渡す（デフォルトTrueで既存の呼び出し元の挙動は変わらない。2026-09-30追加、
    ui.kitting_production_entry.KittingProductionEntryWindow._perform_
    registration()がFalseを渡した場合、反対側への登録でもlot_status_historyへの
    即時記録をスキップする。反対側は常に主たる面と同一lot_noのため、呼び出し元が
    まとめて記録する対象からこの反対側分が漏れることはない）。
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

    lot_status_historyへの記録（2026-09-30追加）：本関数はprod_log_idしか
    受け取らないため、lot_no特定のためにUPDATE実行前に対象行を一度SELECTして
    おく（UPDATE自体はdaily_qtyしか変更せずkitting_list_no・lot_idは変わらない
    ため、UPDATE前後どちらで取得しても値自体は同じだが、対象行が万一既に
    削除されていた場合（同時実行等）でも「行が見つからない」ことを検出できる
    よう、実行前に取得する）。対象行が見つからない場合は、更新自体は従来通り
    実行した上で、履歴記録はスキップする（UPDATE文自体は対象0件でもエラーには
    ならないため、この場合のUPDATEは実質何もしない）。
    """
    row = get_production_daily_by_id(prod_log_id)
    update_daily_production(prod_log_id, daily_qty)
    if row is not None:
        _record_lot_status_snapshot_safely(row["lot_id"], "update")


def delete_daily_result(prod_log_id: int):
    """
    実績1件（prod_log_id指定）を削除する。

    lot_status_historyへの記録（2026-09-30追加）：DELETE実行後には対象行が
    無くなり参照できなくなるため、lot_no特定のため必ずDELETEの前に対象行を
    SELECTしておく。対象行が見つからない場合（既に削除済み等）は、削除処理・
    履歴記録のいずれもスキップする。
    """
    row = get_production_daily_by_id(prod_log_id)
    delete_daily_production(prod_log_id)
    if row is not None:
        _record_lot_status_snapshot_safely(row["lot_id"], "delete")


def build_linked_correction_preview(prod_log_id: int, action: str, new_daily_qty: float = None):
    """
    実績の修正・削除（ui.kitting_production_entry.ActualCorrectionWindow）で、
    両面の計画があるロットの場合に反対側の面の実績へ連動させる変更内容を
    事前に組み立てる（2026-10-08新設、D-112。DBへの書き込みは一切行わない、
    確認ダイアログ表示用）。

    action："update"（修正）または"delete"（削除）。
    new_daily_qty：action="update"の場合の新しい数量（必須）。"delete"の場合は
    無視する。

    相手の実績の特定方法（優先順位）：
      a. 同じロットNo・ファイルNoの反対側の面の計画（複数バッチあり得る）の
         実績のうち、**修正・削除前**の数量・日付が完全に一致するものが
         ちょうど1件だけあれば、それを相手とする（複数バッチがあっても、
         たまたま値が一致する行が複数あれば一意に決められないため除外し、
         bへ進む）。
      b. aで決まらない場合、登録時の自動入力（register_opposite_side_daily_
         result()）・NG入力欄の相手探し（_setup_ng_side_ui()）と同じ方法
         （find_opposite_side_plan()に、選択中の計画のplan_start_datetimeを
         渡す）で相手の計画を1件に絞り込む。その計画に実績が無ければ
         （c、後述）、実績が1件だけあればそれを相手とする。実績が2件以上
         ある場合（「1計画=1レコード」ルールに反する想定外のデータ）は、
         安全側に倒し連動しない（ambiguous=True、呼び出し元は反対側を
         変更せず、その旨を確認ダイアログで示すこと）。
      c. 相手の計画に実績が1件も無い場合：修正（update）のときは新規登録、
         削除（delete）のときは何もしない（noop）。

    戻り値：{
      "primary": {"prod_log_id", "kitting_list_no", "lot_no", "old_daily_qty",
                   "new_daily_qty"（deleteの場合None）, "report_date", "plan"},
      "has_opposite_plan": bool（反対側の面の計画自体が無い＝片面のみの計画
                   の場合False。この場合secondaryはNone、連動なしで従来通り）,
      "secondary": None、または {
          "method": "a"|"b", "ambiguous"（bool、Trueの場合は連動しない）,
          "action": "update"|"insert"|"delete"|"noop",
          "kitting_list_no", "prod_log_id"（既存行がある場合のみ）,
          "old_daily_qty"（既存行がある場合のみ）, "new_daily_qty",
          "report_date", "pre_existing_mismatch"（bool、method="b"で、連動前の
          実績がprimaryの変更前の値と一致していなかった場合True。この場合、
          呼び出し元は確認ダイアログで目立たせて示すこと）,
      },
    }
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
            # list_active_side_plans_for_file()では見つかったが、
            # find_opposite_side_plan()のCOALESCE条件のわずかな差異で
            # 見つからない等の想定外のケース。安全側に倒し連動しない。
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
    build_linked_correction_preview()の戻り値（利用者が確認ダイアログで承認した
    内容）を、1つのトランザクションで確定する（2026-10-08新設、D-112）。

    secondaryがNone、またはambiguous=Trueの場合は、primaryのみを変更する
    （連動しない、従来どおりの単独修正・削除と同じ結果になる）。
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

    # lot_status_historyの更新（登録時と同じ考え方）。反対側も同一lot_noのため、
    # 1回の記録で両面分をまとめて反映する。
    if record_history:
        _record_lot_status_snapshot_safely(primary["lot_no"], f"correction_{action}")


def _build_report_rows(records):
    """
    production_daily のレコード群から、日報・月報共通の表示データを構築する。
    kitting_list_no をキーに計画情報（setup_file_no / board_name / lot_no / order_qty）と
    突き合わせ、通し番号・生産数・累計数を付与した辞書のリストを返す。

    「引落数量」（lot_completed）・「仕掛数量」（surplus_qty）・「未完了数」
    （lot_remaining）は、services.production_service.calculate_lot_completion()
    （lot_noに属する全setup_file_no×production_side単位の実績累計を合算した上で
    最小値を取る、正しいロジック）をそのまま使う。以前はこの関数独自に
    「その日/期間に実績登録があった行（daily_qty）だけを対象にした最小値」を
    計算していたが、これは同一lot_no内に実績登録が無い（未生産の）file_noが
    ある場合、その未生産file_noがそもそも集計対象に含まれず、生産済みの
    file_noのdaily_qtyだけを見て誤って「引落済み」と判定してしまう不具合が
    あった（調査により実データ・lot_no=110068等で確認済み。calculate_lot_
    completion()なら未生産file_noの実績0が正しく最小値に反映され、引落数量が
    0のまま＝全て仕掛のままになる）。同一lot_noに属する行が複数あっても
    calculate_lot_completion()の呼び出しはlot_no単位でキャッシュし、
    重複計算を避ける（ui.kitting_production_entry.py::_fetch_plan_list_rows()
    相当の既存パターンと同じ考え方）。

    calculate_lot_completion()がValueError（対象lot_noの計画が1件も見つから
    ない。実績はあるが、その後計画自体が削除・無効化されたlot_noが理論上
    あり得る）を送出した場合、またはこの行のfile_no・面の組み合わせが
    calculate_lot_completion()の返すfile_actualsに見つからない場合
    （plan自体が見つからない・delete_flag=1等で計画一覧から外れている場合）は、
    正しい完成数を判定できないため、安全側（未完成扱い）にフォールバックする：
    lot_completed=0、surplus_qty=daily_qty、lot_remaining=order_qtyとする
    （build_wip_extraction_rows()・_collect_order_qty_inconsistencies()の
    ValueError時「判定不能としてスキップ」と同じ考え方だが、こちらは実績
    自体は既に登録済みのデータのため行ごと非表示にはせず、保守的な値のまま
    表示を継続する）。

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

    ただし、面1の実績数量の合計が面2の実績数量の合計を上回っている場合は
    「不整合」として扱い、除外はするが黙って消さず、戻り値のinconsistency_
    warningsに記録する（ui.kitting_production_entry.py::ActualCorrection
    Windowが面連動を行わず片面のみを修正・削除できるため、面1・面2の実績が
    食い違う状態を作れる。調査により確認済み。2026-10-08、D-111でこの片面の
    み修正・削除する経路自体に両面連動を追加したが、連動前の既存データ・
    連動対象を特定できない例外的なケースのため、本警告自体は残す）。
    面2計画が存在しない（片面のみの計画）場合は、比較対象が無いため
    除外・警告いずれも行わない。

    判定単位について（2026-10-08改訂、D-111）：以前は面1の実績1レコードごとに
    find_opposite_side_plan()で「相手の面2計画」を1件に絞り込み、その1件との
    数量比較で判定していた。find_opposite_side_plan()は、同一(lot_no,
    setup_file_no)に複数の面2バッチ（日付違い・実装ライン違い）がアクティブな
    場合、plan_start_datetimeが最も近い（本関数の呼び出しではヒント自体を
    渡していなかったため、実際には最も古い）1件を選ぶ仕組みのため、本来の
    対応する組ではない別バッチどうしを比較してしまい、実データで104件
    全件が誤検出となっていたことが調査で判明した（詳細は調査記録参照）。
    この判定を、計画どうしの1対1の対応づけを経由しない方式に変更した：
    list_active_side_plans_for_file()で(lot_no, setup_file_no)に属する
    現在アクティブな面1・面2の計画を全件取得し、それぞれの
    get_app_cumulative_qty()の**合計**（_compute_lot_completion()の
    file_actuals、(setup_file_no, production_side)単位の合算と同じ考え方）を
    比較する。1対1の組を特定する必要が無いため、複数バッチがあっても
    誤検出しない。

    構成基板数チェック・引落ルール（2026-09-28、_evaluate_lot_status()へ
    判定ロジックを集約）：以前は本関数が独自にget_board_structure()を呼んで
    構成基板数の一致/不足/超過/未登録を判定していたが、日報・月報
    （本関数）・仕掛数量抽出（build_wip_extraction_rows()）・ロット進捗
    チェック（check_lot_progress()）の3機能でそれぞれ別々に（またはチェック
    自体が存在しない状態で）判定が行われており、同一ロットで引落・仕掛の
    値が食い違う問題が実データ（lot_no=260079）で確認された（2026-09-28の
    調査報告参照）。この判定・引落ルールをservices.production_service.
    _evaluate_lot_status()に一本化し、本関数はその結果をそのまま使う。

    - lot_completed（引落）：_evaluate_lot_status()が返す"lot_completed"
      （DRAWDOWN_ZERO_STATUSESにそのロットのstatusが含まれる場合は0、
      それ以外は実績ベースの完成数）をそのまま使う。lot_remaining（未完了数）
      は、以前と同じ式「発注数（ロット全体の代表order_quantity）－引落」の
      まま、引落の値だけがこの新しいルールに従う。
    - マスタ未登録（board_structure_masterに該当board_nameが無い）：
      unregistered_board_warningsに記録する（board_name単位、代表1件方式
      ではなく実際に未登録の全board_nameを個別に記録する）。仮想行は追加
      しない。
    - 構成基板数が複数種類に分かれている（board_count_inconsistent）：
      board_count_inconsistency_warningsに記録する（board_name単位、
      登録されているboard_nameそれぞれについて、その値と全体の値一覧
      （board_count_values）を記録する）。仮想行は追加せず、引落は
      DRAWDOWN_ZERO_STATUSESに含めていないため実績ベースの値のまま。
    - 構成基板数 > 実file_no数（shortfall）：不足分だけ「未確定」の仮想行を
      追加する。仮想行はkitting_list_no=""（既存のon_row_double_click()の
      「kitting_list_noが空なら何もしない」ガードがそのまま機能する）、
      数量系フィールドは_evaluate_lot_status()のmissing_file_entriesの値を
      そのまま使う（ui.daily_report_window._row_to_values()が":.0f"で
      書式化するため、Noneや文字列を入れるとCSV/PDF/Treeview表示のいずれ
      でもValueErrorになる点に注意し、Noneは0へ変換する）。
    - 構成基板数 < 実file_no数（excess）：excess_file_no_warningsに記録する
      （board_name単位）。仮想行は追加せず、自動的な補正（ファイルNoの
      付け替えの統合等）も行わない。引落はDRAWDOWN_ZERO_STATUSESに含めて
      いないため実績ベースの値のまま。
    - 一致する場合：追加処理なし。

    仮想行・警告はreport_rows構築の本ループの後、lot_completion_cacheに
    登場したdistinctなlot_no単位で追加する（report_rowsはproduction_daily
    のレコード順であり、同一lot_noの行が連続して並ぶ保証が無いため、
    特定の行の直後に挿入するのではなく、全ての実データ行を構築し終えた後に
    lot_no単位でまとめて追加する方式とした）。seqは本ループで使った
    カウンタをそのまま引き継いで連番を振る。

    order_qty_inconsistent（発注数がfile_no間で不一致、_evaluate_lot_
    status()が_compute_lot_completion()経由で既に算出・パススルー済み）は、
    引き続き戻り値に含める（以前はui.monthly_report_window._collect_order_
    qty_inconsistencies()が別途calculate_lot_completion()を呼び直して同じ
    情報を得ていたが、二重計算を避けるため本関数の戻り値をそのまま使う
    設計を維持している）。

    確認事項欄・文字色（2026-09-28追加）：unregistered・board_count_
    inconsistent・excessは、構成基板数との比較自体が成立しない、または
    自動補正を行わないため、その引落は「実績はあるが構成基板数との整合性が
    未検証」な値のまま表示される。確認済みの数値と見分けがつくよう、
    各report_row（仮想行含む）に"confirmation_note"（理由文言、
    _build_lot_status_remarks()参照）・"status_color_category"
    （"needs_review"|"shortfall"|None、_lot_status_color_category()参照）を
    追加した。文言・色分けの定義はこの2つの関数（services層）に一本化し、
    ui.unified_report_window（2026-10-01に日報・月報を統合した実績レポート
    画面、旧ui.daily_report_window.DailyReportWindow・ui.monthly_report_window.
    MonthlyReportWindowを置き換えた）・ui.lot_progress_windowの各画面は
    この値をそのまま使う（画面ごとに文言・色を定義しない）。

    戻り値：(report_rows, inconsistency_warnings, order_qty_inconsistency_warnings,
             unregistered_board_warnings, excess_file_no_warnings,
             board_count_inconsistency_warnings) のタプル。
      report_rows：[{"seq", "kitting_list_no", ..., "confirmation_note",
                    "status_color_category", "status"（2026-10-01追加。
                    "match"|"shortfall"|"excess"|"unregistered"|
                    "board_count_inconsistent"|None（lot_eval自体が無い
                    判定不能ケース）。ui.unified_report_window.pyの状態
                    絞り込みで使う）, "has_ng"（2026-10-01追加。bool、
                    scrap_records・ng_declarationsのいずれかにこの
                    (kitting_list_no, lot_no, production_side)の実績・申告が
                    あればTrue。「未確定」仮想行は常にFalse）, "report_date"
                    （2026-10-01追加。rec（production_daily）のreport_dateを
                    そのまま保持。「未確定」仮想行は元レコードが無いためNone）}, ...]
                    （面1省略・構成基板数チェックの仮想行追加後、seqは
                    表示される行のみで1から振り直す）
      inconsistency_warnings（2026-10-08改訂、D-111。ロットNo・ファイルNo単位の
      合計比較に変更）：[{"lot_no", "setup_file_no", "side1_total", "side2_total",
                        "diff"（side1_total - side2_total）,
                        "side1_kitting_list_nos"（該当する面1の計画No一覧）,
                        "side2_kitting_list_nos"（該当する面2の計画No一覧）}, ...]
      order_qty_inconsistency_warnings：[{"lot_no", "order_qty_values"}, ...]
      unregistered_board_warnings：[{"lot_no", "board_name", "file_nos"}, ...]
                                    （file_nosはロット全体・面1省略後のdistinct
                                    file_no一覧、ui.unified_report_window.py
                                    のCSV出力機能で使用）
      excess_file_no_warnings：[{"lot_no", "board_name", "board_count", "file_nos"}, ...]
      board_count_inconsistency_warnings：[{"lot_no", "board_name", "board_count",
                                            "board_count_values", "file_nos"}, ...]
                                            （2026-09-28追加。unregistered_board_
                                            warnings・excess_file_no_warningsとは
                                            意味が異なる別カテゴリのため、混同
                                            しないよう別リストとした）
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
            # 登録日（2026-10-01追加）。rec（production_daily）自身が持つ
            # report_dateをそのまま保持する。本関数の行はkitting_list_no単位
            # （＝production_dailyの1レコード単位）であり、複数バッチの
            # report_dateを1行に集約する必要はない（build_wip_extraction_rows()
            # の_pick_representative_plan_item()のような代表選定は不要）。
            "report_date": rec["report_date"],
        })

    # 面1省略：D-8の既存ルールのまま、計画どうしの1対1の対応づけは経由せず
    # 「面2がアクティブな計画として存在するか」だけで判定する
    # （find_opposite_side_plan()は複数バッチがある場合に1件へ絞り込む関数の
    # ため、除外の判定自体にも使うと誤った絞り込みに引き込まれる。2026-10-08
    # 改訂、D-111）。
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

    # 不整合判定：ロットNo・ファイルNoごとの面1合計／面2合計の比較
    # （2026-10-08改訂、D-111。計画どうしの1対1の対応づけを経由しないため、
    # 複数バッチがあっても誤検出しない。本関数のdocstring参照）。
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

    # NG（仕損）の有無の事前一括取得（2026-10-01追加、ui.unified_report_window.py
    # の「NGの有無」絞り込み用）。(kitting_list_no, lot_no, production_side)を
    # キーに、仕損展開実績（scrap_records、record_count>0）またはNG申告
    # （ng_declarations、ng_qty>0）のいずれかが存在する組の集合を作る
    # （ui.ng_input_window._fetch_ng_list_rows()と同じ「申告 ∪ 展開済み」の
    # 考え方。本関数の行単位でO(1)判定できるよう事前にsetへまとめておき、
    # 行数分のN+1クエリを避ける）。
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
            # lot_evalが無い（計画自体が現存しない等、判定不能）場合は、
            # 構成基板数チェック自体が成立しないため「確認事項」は空欄・
            # 色付けも無しとする（未検証の引落数値かどうかを判定する材料が
            # 無いため、誤った理由を表示しないことを優先した）。
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

    # 構成基板数チェック・発注数不一致の収集（docstring参照）。判定ロジック
    # 自体はevaluate_lot_status()（_evaluate_lot_status()の単一ロット版）に
    # 一本化済みのため、ここではその結果を警告リスト・仮想行へ振り分ける
    # だけで、get_board_structure()等への問い合わせは行わない。
    # lot_status_cacheに登場した（＝この帳票に実データ行が1件以上あった）
    # distinctなlot_no単位で処理する。
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
            # 「未確定」仮想行のboard_name表示用：shortfallは判定がロット単位のため
            # 特定のboard_nameに紐づかないが、report_rowsの"board_name"列は
            # ui.daily_report_window._format_board_count()がget_board_structure()を
            # 引くのに使う。空文字列のままだと「未登録」と誤表示される（shortfallは
            # 未登録ではなく、構成基板数自体は判明しているため）。ロット内の
            # 登録済みboard_name（status=="shortfall"の時点でregistered_countsは
            # 必ず1種類に統一されている）のうち先頭のものを代表として表示に使う。
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
                    # 「未確定」仮想行はkitting_list_no自体が存在しない（まだ
                    # 計画・実績のどちらも無い）ため、NGの有無は判定しようが
                    # なくFalse固定とする。
                    "has_ng": False,
                    # 元になるproduction_dailyレコードが存在しないため、
                    # report_dateに相当する値も存在しない（2026-10-01追加）。
                    "report_date": None,
                })
                seq += 1

    return (
        report_rows, inconsistency_warnings, order_qty_inconsistency_warnings,
        unregistered_board_warnings, excess_file_no_warnings,
        board_count_inconsistency_warnings,
    )


def build_daily_report():
    """
    本日（report_date = 今日）入力された実績を元に、日報表示用のデータを構築する。
    戻り値は_build_report_rows()と同じ (report_rows, inconsistency_warnings,
    order_qty_inconsistency_warnings, unregistered_board_warnings,
    excess_file_no_warnings, board_count_inconsistency_warnings) タプル。
    """
    records = list_daily_production_today()
    return _build_report_rows(records)


def build_monthly_report(from_date: str, to_date: str):
    """
    指定期間（report_date が from_date～to_date、両端含む）の実績を元に、
    月報表示用のデータを構築する。列構成・集計ロジックは日報（build_daily_report）と共通。
    戻り値は_build_report_rows()と同じ (report_rows, inconsistency_warnings,
    order_qty_inconsistency_warnings, unregistered_board_warnings,
    excess_file_no_warnings, board_count_inconsistency_warnings) タプル。
    """
    records = list_daily_production_range(from_date, to_date)
    return _build_report_rows(records)


def _compute_lot_completion(lot_no: str, plan_items: list, cumulative_by_pair: dict,
                              cutoff_date: str = None) -> dict:
    """
    calculate_lot_completion()・list_incomplete_lots()の共通ロジック。

    plan_itemsは同一lot_noに属する計画行のリスト、cumulative_by_pairは
    (kitting_list_no, lot_no) -> アプリ内累計 の辞書（呼び出し元が
    get_app_cumulative_qty_bulk()で事前に一括取得したものをそのまま渡す。
    calculate_lot_completion()は対象lot_no1件分のみ、list_incomplete_lots()は
    全lot_no分をまとめて1回のバルク取得で済ませており、取得方法自体は
    呼び出し元ごとに異なるためこの関数の責務には含めない）。

    cutoff_date（2026-10-09追加、日々の引落一覧の払出し日ベース化のため。
    D-115/D-29改訂参照）：本関数自体は日付の絞り込みを一切行わない
    （plan_items・cumulative_by_pairをそのまま使う、従来と同じ計算）。
    呼び出し元が「その日付までの実績だけを反映したcumulative_by_pair」
    （get_app_cumulative_qty_bulk(..., cutoff_date=cutoff_date)で取得したもの）を
    渡した場合に、その条件を戻り値にそのまま記録しておくための受け渡し専用の
    引数（呼び出し元がどの条件で計算したかを追跡できるようにする）。
    省略時（None）は従来と完全に同じ動作・結果になる（cumulative_by_pair自体が
    絞り込まれていない限り、本関数の計算内容は何も変わらないため）。

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


# 構成基板数チェックのstatusのうち、引落を0扱いにするものの集合（2026-09-28
# 追加、日報・月報・仕掛数量抽出・ロット進捗チェックで共通のルールとして
# 集約。ユーザー確定のルール：ファイルNoが構成基板数に対して不足している
# （shortfall）ロットは、実績がどうであれ引落を確定させられない（構成が
# 揃っていないため）。unregistered（マスタ未登録）・board_count_inconsistent
# （登録済みboard_countがboard_name間で食い違う）は、構成基板数との比較
# 自体が成立しない＝判定できないため、従来通りcalculate_lot_completion()の
# 実績ベースの値をそのまま使う（0にはしない）。excess（実file_no数が
# board_countを上回る）も、自動的な補正は行わず従来通りの値を使う。
# 将来ルールを変更する場合はこの集合だけを変更すればよい。
DRAWDOWN_ZERO_STATUSES = {"shortfall"}

# 引落が実績ベースのまま（DRAWDOWN_ZERO_STATUSESに含まれない）でも、構成
# 基板数との比較自体が成立しない・信頼できないため「確認・修正が必要」と
# 扱うstatusの集合（2026-09-28追加）。日報・月報・ロット進捗チェックの
# 3画面で共通の文字色（赤）判定に使う。
NEEDS_REVIEW_STATUSES = {"unregistered", "board_count_inconsistent", "excess"}


def _build_lot_status_remarks(status: str, board_count_values, shortfall_count: int,
                                unregistered_board_names: list) -> str:
    """
    ロットのstatus・付随情報から、「確認事項」欄に表示する理由文言を組み立てる
    （2026-09-28追加）。日報・月報・ロット進捗チェックの3画面が同じ文言を
    表示できるよう、文言の定義をこの関数1箇所（services層）に集約する
    （画面ごとに文言を書かない）。

    - unregistered：「構成基板数マスタ未登録(引落は未検証)」。
    - board_count_inconsistent：「構成基板数がロット内で不一致 4, 5(引落は
      未検証)」のように、実際の値（board_count_values）を昇順で列挙する。
    - excess：「実ファイルNo数がマスタの構成基板数を超過(引落は未検証)」。
    - shortfall：「構成基板数不足: 未確定N件(引落0)」（Nはshortfall_count）。
    - match：主理由は無し（空文字列のまま）。
    - 上記に加え、statusが"unregistered"以外（＝一部のboard_nameだけが
      未登録の状態がありうる）でunregistered_board_namesが非空の場合、
      「一部未登録: 基板名…」を追加する（match・shortfallでも表示され得る）。
      status=="unregistered"の場合は、そもそも主理由が「マスタ未登録」を
      既に表しているため、この追加は行わない（二重表示を避ける）。

    戻り値：確認事項の文言（該当理由が無ければ空文字列）。複数理由がある
    場合は" / "で連結する。
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
    ロットのstatus・unregistered_board_namesから、「確認事項」欄の文字色
    カテゴリを判定する（2026-09-28追加、日報・月報・ロット進捗チェックで
    共通）。戻り値："needs_review"（赤、確認・修正が必要）|"shortfall"
    （オレンジ、不足のため引落0）|None（色付け無し）。

    優先順位：NEEDS_REVIEW_STATUSES（unregistered/board_count_inconsistent/
    excess）またはunregistered_board_namesが非空（一部未登録）のいずれかに
    該当すれば"needs_review"（赤）を優先する。shortfallは、それ自体に該当
    する場合のみ"shortfall"（オレンジ）。一部未登録とshortfallが同時に
    成立するロットは、"確認・修正が必要"の方がより重要度が高いと判断し、
    赤を優先する（ユーザーからの明示的な優先順位の指定は無かったため、
    この関数の実装判断として記録する）。
    """
    if status in NEEDS_REVIEW_STATUSES or unregistered_board_names:
        return "needs_review"
    if status in DRAWDOWN_ZERO_STATUSES:
        return "shortfall"
    return None


def _evaluate_lot_status(lot_no: str, plan_items: list, cumulative_by_pair: dict,
                           cutoff_date: str = None, board_structure_cache: dict = None) -> dict:
    """
    構成基板数チェック（一致/不足/超過/未登録/board_count不一致）とロット
    進捗（引落・仕掛・未生産）を1ロット分まとめて評価する、日報・月報
    （_build_report_rows()）・仕掛数量抽出（build_wip_extraction_rows()）・
    ロット進捗チェック（check_lot_progress()）で共通利用する唯一の実装
    （2026-09-28、3機能に分散していた構成基板数判定・引落0判定を集約）。

    cutoff_date（2026-10-09追加）：_compute_lot_completion()へそのまま渡す
    だけの受け渡し専用の引数（本関数自体は日付の絞り込みを行わない。
    呼び出し元がcumulative_by_pairを事前に日付で絞り込んでおくこと）。
    日々の引落一覧（services.lot_status_history.get_daily_drawdown()）が、
    対象日・前日それぞれで本関数を呼び分けるために使う。省略時（None）は
    従来と完全に同じ動作・結果になる。既存の呼び出し元（_build_report_rows()・
    build_wip_extraction_rows()・check_lot_progress()）はいずれもこの引数を
    渡さないため、挙動は変わらない。

    board_structure_cache（2026-10-09追加）：{board_name: get_board_structure()の
    戻り値, ...}の辞書を渡すと、get_board_structure()の代わりにこの辞書を
    読み書きして使う（同一board_nameの再検索を避ける、呼び出し元が複数ロット・
    複数回にわたって使い回すための任意のメモ化キャッシュ）。省略時（None）は
    従来通り毎回get_board_structure()を呼ぶ（既存の呼び出し元はいずれもこの
    引数を渡さないため挙動は変わらない）。evaluate_all_lot_status()・
    services.lot_status_history.get_daily_drawdown()が、1回の表示の中で
    構成基板数マスタの検索結果を使い回すために使う（実データで1057ロットに
    対し素のget_board_structure()呼び出しのみでは1回の表示に5〜10秒かかって
    いたことが判明したため、1秒程度に収める目的で追加した）。

    経緯：以前は_build_report_rows()とcheck_lot_progress()がそれぞれ独立に
    構成基板数チェック（get_board_structure()呼び出し・shortfall/excess/
    unregistered判定）を実装しており、build_wip_extraction_rows()は構成
    基板数を一切考慮していなかった。この結果、同一ロット（例：lot_no=260079、
    構成基板数2に対し実file_no数1のshortfall）について、check_lot_progress()
    は引落0・仕掛800と判定する一方、日報・月報は引落800・仕掛0のまま、
    仕掛数量抽出は対象行自体が0件（wip_qty<=0で除外）という、3機能間で
    食い違う結果になっていた（2026-09-28の調査報告参照）。本関数へ判定
    ロジックを一本化することでこの食い違いを解消する。

    経緯：以前は_build_report_rows()とcheck_lot_progress()がそれぞれ独立に
    構成基板数チェック（get_board_structure()呼び出し・shortfall/excess/
    unregistered判定）を実装しており、build_wip_extraction_rows()は構成
    基板数を一切考慮していなかった。この結果、同一ロット（例：lot_no=260079、
    構成基板数2に対し実file_no数1のshortfall）について、check_lot_progress()
    は引落0・仕掛800と判定する一方、日報・月報は引落800・仕掛0のまま、
    仕掛数量抽出は対象行自体が0件（wip_qty<=0で除外）という、3機能間で
    食い違う結果になっていた（2026-09-28の調査報告参照）。本関数へ判定
    ロジックを一本化することでこの食い違いを解消する。

    引数：
      lot_no：対象ロット。
      plan_items：そのlot_noに属する計画行のリスト（list_plan_items_by_lot()
                  またはlist_plan_items_for_all_lots()をlot_noでグルーピング
                  したもの、いずれも同じ列構成）。
      cumulative_by_pair：(kitting_list_no, lot_no) -> アプリ内累計実績の
                  辞書（get_app_cumulative_qty_bulk()で事前に一括取得した
                  ものをそのまま渡す。_compute_lot_completion()と同じ契約）。

    判定ロジック：
      1. _compute_lot_completion()で、実績ベースの完成数（completed_quantity、
         全setup_file_no×面の実績最小値）・代表order_quantity・file_actuals
         （(setup_file_no, side) -> 合算実績）を得る。
      2. ロット全体（board_name横断）で、面1省略後（面2があれば対応する
         面1を除外する既存ルール）のdistinct setup_file_no集合
         （visible_file_nos）を算出する。
      3. board_name単位で構成基板数マスタ（get_board_structure()）を検索し、
         未登録のboard_nameをunregistered_board_namesに集約する（代表1件
         方式には戻さない、2026-09-26に発見・修正済みの欠陥）。
      4. status判定：
         - 登録済みのboard_nameが1つも無い → "unregistered"。
         - 登録済みのboard_countが複数種類に分かれている → "board_count_
           inconsistent"（board_count_valuesに値の一覧、比較自体は行わない）。
         - 上記以外（登録済みboard_countが1種類）→ visible_file_nosの件数と
           比較し、一致="match"、不足（board_count超過分）="shortfall"
           （shortfall_countを設定）、超過（実file_no数の方が多い）=
           "excess"（excess_file_nosに実際のfile_no一覧を設定）。
      5. 引落（lot_completed）：DRAWDOWN_ZERO_STATUSESにstatusが含まれる
         （現状はshortfallのみ）場合は0、それ以外はcompleted_quantityを
         そのまま使う（ユーザー確定ルール：不足の場合のみ引落を確定させ
         られないとみなし、未登録・board_count不一致・超過は判定できない
         または自動補正しない方針のため、実績ベースの値をそのまま使う）。
      6. ファイルNo単位（同一setup_file_no・面に複数バッチが同時にアクティブ
         な場合は合算）の仕掛（surplus_qty=file_actual-lot_completed）・
         未生産（not_produced_qty=order_qty-file_actual）を、board_name単位
         にグルーピングして算出する。
      7. shortfallの場合のみ、不足数分の「未確定」仮想エントリ
         （missing_file_entries、setup_file_no="未確定"、引落0・仕掛0・
         未生産=ロットの代表order_quantity）を追加する。

    戻り値：{
      "lot_no", "status"（"match"|"shortfall"|"excess"|"unregistered"|
                          "board_count_inconsistent"）,
      "board_count"（float|None、match/shortfall/excessのみ）,
      "board_count_values"（[float,...]|None、board_count_inconsistentのみ）,
      "visible_file_nos"（[str,...]、ロット全体・面1省略後）,
      "shortfall_count"（int|None）, "excess_file_nos"（[str,...]|None）,
      "unregistered_board_names"（[str,...]、無ければ空リスト）,
      "boards"：[{"board_name", "board_count"（そのboard_name自身の登録値、
                  未登録ならNone）, "files": [{"setup_file_no",
                  "production_side", "order_qty", "file_actual",
                  "lot_completed", "surplus_qty", "not_produced_qty"}, ...]},
                 ...],
      "missing_file_entries"：[同上の形式のリスト、setup_file_no="未確定"],
      "lot_completed"（このロットの引落、DRAWDOWN_ZERO_STATUSES適用後の値、
                       _build_report_rows()のlot_remaining算出用）,
      "status_remarks"（2026-09-28追加。「確認事項」欄に表示する理由文言。
                        _build_lot_status_remarks()参照。matchかつ未登録
                        board_nameも無い場合は空文字列）,
      "status_color_category"（2026-09-28追加。"needs_review"|"shortfall"|
                        None。_lot_status_color_category()参照。日報・月報・
                        ロット進捗チェックの文字色判定に使う共通の値）,
      "order_quantity"（lot_info["order_quantity"]のパススルー）,
      "file_actuals"（lot_info["file_actuals"]のパススルー、呼び出し元が
                      個別の(setup_file_no, side)を引く場合に使う）,
      "order_qty_inconsistent" / "order_qty_values"（lot_infoのパススルー、
                      _build_report_rows()のorder_qty_inconsistency_warnings
                      構築用。以前は呼び出し元がcalculate_lot_completion()を
                      別途呼んでいたが、本関数の戻り値をそのまま使えば
                      二重計算を避けられる）,
    }
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
    _evaluate_lot_status()の単一ロット版（calculate_lot_completion()と
    calculate_lot_completion()/_compute_lot_completion()の関係と同じ）。
    _build_report_rows()・build_wip_extraction_rows()のように、lot_no単位で
    都度呼び出す（かつlot_no件数分の呼び出しをキャッシュする）使い方に
    対応する。全lot_noをまとめて評価する場合はcheck_lot_progress()の
    ようにlist_plan_items_for_all_lots()＋get_app_cumulative_qty_bulk()の
    一括取得パターンを使うこと（本関数をlot_no件数分ループ呼び出しすると
    calculate_lot_completion()のループ呼び出しと同様のN+1になる）。

    cutoff_date（2026-10-09追加）："YYYY-MM-DD"を指定すると、report_dateが
    この日付以前の実績だけを使って完成数・状態を評価する
    （get_app_cumulative_qty_bulk(..., cutoff_date=cutoff_date)で絞り込んだ
    cumulative_by_pairを組み立て、_evaluate_lot_status()へそのまま渡す。
    plan_items・構成基板数マスタ参照は現在の値をそのまま使う＝日付では
    絞り込めない、D-115/D-29改訂の調査で確認済みの制約）。省略時（None）は
    従来と完全に同じ動作・結果になる（get_app_cumulative_qty_bulk()が
    cutoff_date省略時と同じSQLを発行するため）。
    """
    plan_items = list_plan_items_by_lot(lot_no)
    if not plan_items:
        raise ValueError(f"ロットNo. {lot_no} の計画が見つかりません。")

    kitting_list_no_lot_pairs = [(item["kitting_list_no"], lot_no) for item in plan_items]
    cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs, cutoff_date=cutoff_date)

    return _evaluate_lot_status(lot_no, plan_items, cumulative_by_pair, cutoff_date=cutoff_date)


def list_incomplete_lots():
    """
    distinctなlot_no全件について、_evaluate_lot_status()の判定結果を元に
    未完了ロットのみを一覧で返す（DB間コピー機能の「未完了lot_noの抽出」用）。

    2026-10-01、判定ロジックを_compute_lot_completion()直接呼び出しから
    _evaluate_lot_status()経由に変更した。以前は
    構成基板数チェックを一切行わない_compute_lot_completion()のremaining_
    quantity（実績の素の最小値ベース）のみで未完了判定していたため、日報・
    月報・仕掛数量抽出・ロット進捗チェックの4機能（いずれも_evaluate_lot_
    status()に判定一本化済み）と食い違う結果になり得た。具体的には、構成
    基板数不足（shortfall）のロットは、他の4機能ではlot_completed=0（引落
    未確定）として扱われるにもかかわらず、本関数は実績が発注数に達して
    いれば「完了」とみなし、引き継ぎ対象から誤って除外してしまう可能性が
    あった（理論上のシナリオ。実データでの発生は確認していない）。

    未完了とみなす条件（ユーザー確定の方針）：
      - status=="shortfall"：引落が0扱いのため、remaining_quantityの値に
        関わらず無条件で未完了とする。
      - status=="match"：従来通りremaining_quantity（=order_quantity－
        lot_completed）<= 0 かどうかで完了判定する（退行なし）。
      - status in ("unregistered", "board_count_inconsistent", "excess")：
        これらは構成基板数との整合性チェック自体が成立しない、または
        自動補正を行わないため、_evaluate_lot_status()が従来通り実績
        ベースの値をそのままlot_completedとして返す（以前確定した方針）。
        その値でremaining_quantityを計算し、通常通り完了判定する。

    calculate_lot_completion()・check_lot_progress()と同じ理由で、
    lot_no件数分のN+1呼び出し（evaluate_lot_status()を都度呼ぶとlist_
    plan_items_by_lot()がlot_no件数分発生する）を避けるため、全lot_no分の
    計画行をlist_plan_items_for_all_lots()で1回のSELECTにまとめて取得し、
    アプリ内累計もget_app_cumulative_qty_bulk()で一括取得した上で、
    Python側でlot_noごとにグルーピングして_evaluate_lot_status()の内部
    実装（_evaluate_lot_status()、plan_items・cumulative_by_pairを直接
    受け取るバルク対応版）に渡す（check_lot_progress()と全く同じ
    バルク取得パターン）。

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
              "completed_quantity"（_evaluate_lot_status()のlot_completed、
              shortfallの場合は0）, "remaining_quantity",
              "status"（2026-10-01追加。_evaluate_lot_status()の判定結果、
              呼び出し元が未完了の理由を把握できるよう追加した新規キー。
              carry_over_incomplete_lots()は既存の"lot_no"・
              "kitting_list_nos"キーのみ参照しているため、このキー追加は
              既存の呼び出し元に影響しない）}, ...]
             lot_no昇順。
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
    日報・月報（production_dailyの実績データ、report_date範囲に依存する
    _build_report_rows()）とは独立に、**現在アクティブな全ロット**を対象に、
    構成基板数チェック（一致/不足/超過/未登録/board_count不一致）とロット
    進捗（引落・仕掛・未生産）をまとめて算出する（2026-09-26追加、2026-09-28
    判定ロジックを_evaluate_lot_status()へ集約）。

    経緯：_build_report_rows()の構成基板数チェックは、その帳票の対象期間に
    production_dailyの実績があるlot_noのみを起点にループしていたため、
    (a)対象期間に実績が無いロットは構成基板数の不足・未登録があっても一切
    検知されない、(b)同一lot_no内に複数のboard_nameが存在する場合、最初に
    見つかった1件（代表）のみしかチェックされず、他のboard_nameが未登録でも
    見逃される、という2つの問題が実データで確認されている（2026-09-26の
    調査報告参照）。本関数はこの2点を解消するため、(a)実績データではなく
    list_plan_items_for_all_lots()（現在アクティブな全計画の一括取得）を
    起点にする。

    2026-09-28、判定ロジック自体（構成基板数の比較・引落0判定）を
    _evaluate_lot_status()へ切り出した。日報・月報（_build_report_rows()）・
    仕掛数量抽出（build_wip_extraction_rows()）も同じ関数を使うようになった
    ため、以前は本関数とcalculate_lot_completion()にしか反映されていなかった
    構成基板数の状態・引落ルールが3機能で統一された（詳細な経緯・判定基準は
    _evaluate_lot_status()のdocstring参照）。本関数自体は、全アクティブロットに
    対する一括データ取得（list_plan_items_for_all_lots()＋
    get_app_cumulative_qty_bulk()、list_incomplete_lots()と同じ最適化パターン）
    と、_evaluate_lot_status()の呼び出しのみを担う。

    戻り値：_evaluate_lot_status()の戻り値のリスト（lot_no昇順）。各要素の
    形式は_evaluate_lot_status()のdocstring参照。
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
    check_lot_progress()と同じ一括取得パターン（list_plan_items_for_all_lots()＋
    get_app_cumulative_qty_bulk()、全ロット分を1回のクエリでまとめて取得）で、
    現在アクティブな全ロットについて_evaluate_lot_status()を呼ぶ、cutoff_date
    対応版（2026-10-09追加、日々の引落一覧の払出し日ベース化（D-115/D-29改訂）の
    ため）。

    check_lot_progress()自体は変更していない（既存の呼び出し元・挙動に影響を
    与えないよう、新規関数として追加した）。本関数はcutoff_date（省略可）を
    get_app_cumulative_qty_bulk()へそのまま渡す点のみがcheck_lot_progress()との
    違いで、それ以外のロジックは完全に同一。cutoff_date省略時（None）は
    check_lot_progress()と完全に同じ結果を返す。

    board_structure_cache（2026-10-09追加）：_evaluate_lot_status()へそのまま
    渡すメモ化キャッシュ（省略可）。services.lot_status_history.
    get_daily_drawdown()が、対象日・前日の2回の呼び出しをまたいで構成基板数
    マスタの検索結果を使い回すために、呼び出し元であらかじめ用意した1つの
    辞書を渡す（1057ロット規模の実データで、キャッシュ無しでは1回の表示に
    5〜10秒かかっていたのが、1回の表示（今回・前日の2回の本関数呼び出し）
    全体でキャッシュを共有することで1秒未満に収まることを確認済み）。
    省略時（None）は毎回get_board_structure()を呼ぶ従来通りの動作になる。

    呼び出し元：services.lot_status_history.get_daily_drawdown()
    （対象日・前日のそれぞれでcutoff_dateを指定して本関数を1回ずつ呼び、
    lot_noごとのlot_completedを比較する）。

    戻り値：_evaluate_lot_status()の戻り値のリスト（lot_no昇順）。
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
    仕掛数量（= evaluate_lot_status()のfile_actuals[key] - lot_completed）を
    算出し、models.wip_board_snapshot.save_wip_snapshot()にそのまま渡せる
    行のリストを返す（ui.unified_report_window.UnifiedReportWindow.on_extract_wip()
    から呼ばれる）。

    2026-09-28、引落（lot_completed）の算出をevaluate_lot_status()経由に
    変更した。以前はcalculate_lot_completion()のcompleted_quantity（構成
    基板数を考慮しない、純粋な実績最小値）をそのまま使っていたため、
    構成基板数が不足している（shortfall）ロットは、実績が発注数に達して
    いても仕掛数量が0と算出され、抽出結果から漏れていた（実データ・
    lot_no=260079で確認済み、2026-09-28の調査報告参照）。evaluate_lot_
    status()の"lot_completed"はDRAWDOWN_ZERO_STATUSES（現状はshortfallのみ）
    に該当するロットで0を返すため、この問題が解消される。

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
    evaluate_lot_status()がValueErrorを送出するため、その lot_no は
    スキップする（calculate_lot_completion()と同じ挙動）。

    構成基板数が不足（shortfall）しているロットの「未確定」（実体の無い
    file_no）は、setup_file_no自体が存在しないため本関数の対象外のまま
    とする（wip_board_snapshotは実在するkitting_list_noを前提とするスキーマ
    のため、代表的なkitting_list_noを持たない仮想エントリを抽出することは
    今回のスコープに含めない）。
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