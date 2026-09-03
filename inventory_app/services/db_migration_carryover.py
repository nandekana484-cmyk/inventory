# services/db_migration_carryover.py
"""
「新しいデータベースを作成」時に、未完了ロット（services.production_service.
list_incomplete_lots()）を旧DBから新DBへ引き継ぐ処理。

対象読者：ui/main_window.py::on_create_database()から呼ばれる想定。

コピー対象・非対象（ユーザー確認済みの方針）：
  - コピーする：kitting_plan_items（未完了lot_noに属する現在アクティブな行のみ）、
    production_daily（同じ範囲の実績。lot_remaining_quantityの計算に累計実績が
    必要なため）。
  - コピーしない：scrap_records・ng_declarations（NG履歴・監査証跡）、
    wip_board_snapshot（ある時点のスナップショットのため、DBをまたいで
    持ち越す性質のものではない）。

config.DB_PATHの扱いについて：
本モジュールの各関数はモデル層（models.kitting_plan / models.production /
services.production_service）の既存関数をそのまま使うが、これらは全て
config.DB_PATH（アプリ全体で共有されるグローバルな「現在のDBパス」）経由で
接続する設計のため、2つのDBを同時に読み書きするには config.set_db_path() で
これを一時的に切り替える必要がある。carry_over_incomplete_lots()の契約は
「呼び出し時点のconfig.DB_PATHが旧DB（old_db_path）であることを前提とし、
戻る時点ではconfig.DB_PATHを必ずnew_db_pathにする」（読み取りフェーズ・書き込み
フェーズいずれで失敗しても、最終的にnew_db_pathへ切り替える。新DBファイル自体は
呼び出し元が事前にinit_database_at()で作成済みであり、それが以後アプリの
「現在のDB」になるべきだから）。
"""
import os
from datetime import datetime

import config
from models.kitting_plan import (
    get_connection as get_plan_connection, create_plan_batch, create_plan_version,
    init_kitting_plan_tables,
)
from models.production import get_connection as get_production_connection, replace_daily_result
from services.production_service import list_incomplete_lots

# plan_start_datetimeの実データ形式（"YYYY/MM/DD HH:MM:SS"、スラッシュ区切り＋時刻付き。
# UI_WORKFLOW_FIXES_NOTES.md/PRODUCTION_NG_ENHANCEMENTS_NOTES.md等で既出のkitting_plan_items
# 標準形式と同一）。
_PLAN_START_DATETIME_FORMAT = "%Y/%m/%d %H:%M:%S"

# ロットNo重複疑いと判定する経過日数のしきい値（約1年。うるう年を厳密には考慮しない、
# 「実装しやすい方針」としての単純な日数比較）。
_LOT_NO_DUPLICATE_THRESHOLD_DAYS = 365


def _parse_plan_start_datetime(value):
    """plan_start_datetimeを日時にパースする。None・空文字・パース不能な場合はNoneを返す。"""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), _PLAN_START_DATETIME_FORMAT)
    except (ValueError, TypeError):
        return None


def _fetch_plan_items_for_lot(lot_no: str) -> list:
    """
    指定lot_noに属する、現在アクティブなkitting_plan_items行を全列（SELECT *）で
    取得する（models.kitting_plan.list_plan_items_by_lot()と同じWHERE条件）。

    呼び出し時点でconfig.DB_PATHが指している方のDBから読み取る
    （carry_over_incomplete_lots()内では旧DB接続時に呼ぶ）。
    """
    with get_plan_connection() as con:
        cur = con.execute("""
            SELECT * FROM kitting_plan_items
            WHERE lot_no = ? AND delete_flag = 0 AND COALESCE(is_active, 1) = 1
        """, (lot_no,))
        return [dict(row) for row in cur.fetchall()]


def _fetch_existing_plan_start_datetimes_for_lot(lot_no: str) -> list:
    """
    書き込み先DB（呼び出し時点でconfig.DB_PATHが指している方）に、指定lot_noを
    持つkitting_plan_items行が既に存在するか確認し、その各行のplan_start_datetime
    （生の文字列）をリストで返す（無ければ空リスト）。

    is_active・delete_flagでは絞り込まない：無効化済み・削除フラグ付きの行でも
    「そのlot_noが過去にこのDBで使われていた」という事実自体が重複疑いの根拠に
    なるため、現在アクティブな行だけに限定すると見逃しが生じる。

    新DBがまだ一度もcreate_plan_batch()等を呼ばれていない真っさらな状態だと
    kitting_plan_itemsテーブル自体が存在しないため、create_plan_batch()と
    同様にinit_kitting_plan_tables()で事前に存在を保証する。
    """
    init_kitting_plan_tables()
    with get_plan_connection() as con:
        cur = con.execute(
            "SELECT plan_start_datetime FROM kitting_plan_items WHERE lot_no = ?",
            (lot_no,),
        )
        return [row["plan_start_datetime"] for row in cur.fetchall()]


def _check_lot_no_duplicate(lot_no: str, old_plan_start_datetimes: list) -> dict:
    """
    書き込み先DB（新DB）に既に同じlot_noの行が存在するかを確認し、重複疑いの
    有無を判定する（carry_over_incomplete_lots()の書き込みフェーズから、対象
    lot_noごとに呼ぶ）。

    old_plan_start_datetimes：引き継ぎ元（旧DB）側で、このlot_noに属する
    kitting_plan_items行が持つplan_start_datetime（生の文字列）のリスト。

    判定方針：
    - 新DB側に該当lot_noの行が1つも無ければ、通常の（重複ではない）ケースとして
      Noneを返す（呼び出し元は警告リストに追加しない）。
    - 新DB側の各行のplan_start_datetimeと、旧DB側の各plan_start_datetimeの
      全組み合わせを比較する。いずれかの組でパース可能かつ差が
      _LOT_NO_DUPLICATE_THRESHOLD_DAYS（365日）以上離れていれば「重複疑いあり」
      （"suspected_duplicate"）と判定する。日数差が最大の組を代表値として返す
      （最も疑わしい＝別ロットの可能性が高い組み合わせをユーザーに提示するため）。
    - 「重複疑いあり」に該当する組が無く、かつ新DB側・旧DB側どちらかに
      パース不能（None・空欄・形式不正）なplan_start_datetimeが1件でも含まれる
      場合は「判定不能」（"undetermined"）とする（実装しやすさを優先し、
      パース不能な行は「重複でない」と断定せず、人が確認できるよう警告に含める
      方針を採用した）。
    - 上記いずれにも該当しない（＝新DB側に行はあるが、全ての組み合わせが
      パース可能かつ365日未満の差）場合はNoneを返す（通常の月またぎ再利用として
      警告しない）。

    戻り値：Noneまたは
    {"reason": "suspected_duplicate" | "undetermined",
     "existing_plan_start_datetime": 新DB側の代表値（生文字列、無ければNone),
     "old_plan_start_datetime": 旧DB側の代表値（生文字列、無ければNone)}
    """
    existing_raw_list = _fetch_existing_plan_start_datetimes_for_lot(lot_no)
    if not existing_raw_list:
        return None

    old_raw_list = old_plan_start_datetimes or [None]
    existing_raw_list = existing_raw_list or [None]

    best_suspected = None  # (day_diff, existing_raw, old_raw)
    has_undetermined = False

    for existing_raw in existing_raw_list:
        existing_dt = _parse_plan_start_datetime(existing_raw)
        for old_raw in old_raw_list:
            old_dt = _parse_plan_start_datetime(old_raw)
            if existing_dt is None or old_dt is None:
                has_undetermined = True
                continue
            day_diff = abs((old_dt - existing_dt).days)
            if day_diff >= _LOT_NO_DUPLICATE_THRESHOLD_DAYS:
                if best_suspected is None or day_diff > best_suspected[0]:
                    best_suspected = (day_diff, existing_raw, old_raw)

    if best_suspected is not None:
        _, existing_raw, old_raw = best_suspected
        return {
            "reason": "suspected_duplicate",
            "existing_plan_start_datetime": existing_raw,
            "old_plan_start_datetime": old_raw,
        }

    if has_undetermined:
        return {
            "reason": "undetermined",
            "existing_plan_start_datetime": existing_raw_list[0],
            "old_plan_start_datetime": old_raw_list[0],
        }

    return None


def _fetch_production_daily_for_lot(lot_no: str, kitting_list_nos: list) -> list:
    """
    指定lot_noに属するkitting_list_no一覧（list_incomplete_lots()の
    "kitting_list_nos"）に対応するproduction_daily行を全列（SELECT *）で取得する。

    kitting_list_noだけでなくlot_id（=lot_no）も条件に含める理由：実DBで同一
    kitting_list_noが複数の異なるlot_noにまたがって存在するケースが478件確認
    されており、kitting_list_noだけで絞り込むと別ロットの実績まで誤って
    含めてしまうため（models.production.get_app_cumulative_qty()等と同じ理由）。
    """
    if not kitting_list_nos:
        return []
    with get_production_connection() as con:
        placeholders = ", ".join("?" for _ in kitting_list_nos)
        cur = con.execute(f"""
            SELECT * FROM production_daily
            WHERE kitting_list_no IN ({placeholders})
              AND COALESCE(lot_id, '') = COALESCE(?, '')
        """, (*kitting_list_nos, lot_no))
        return [dict(row) for row in cur.fetchall()]


def carry_over_incomplete_lots(old_db_path: str, new_db_path: str, imported_by: str = "carry_over") -> dict:
    """
    旧DB（old_db_path）の未完了ロット（list_incomplete_lots()、
    lot_remaining_quantity > 0）を、新DB（new_db_path）へコピーする。

    呼び出し前提：new_db_pathには既にinit_database_at()でスキーマ一式が
    作成済みであること（本関数はスキーマ作成を行わない）。

    処理の流れ：
      1. 【読み取りフェーズ】config.DB_PATHをold_db_pathへ切り替え、
         list_incomplete_lots()で未完了lot_no一覧を取得。各lot_noについて
         _fetch_plan_items_for_lot()・_fetch_production_daily_for_lot()で
         関連行を全てメモリ上に読み出す（旧DBへは読み取りのみ、一切書き込まない）。
      2. 【書き込みフェーズ】config.DB_PATHをnew_db_pathへ切り替え、lot_noごとに
         以下を実行：
           a. create_plan_batch()で新しいplan_batch_idを1つ発行する
              （そのlot_noに属する全kitting_plan_items行が共有する）。
           b. 各kitting_plan_items行について create_plan_version() で新規登録する
              （旧DBのplan_item_id・plan_batch_id・version・is_active・
              previous_plan_item_idは一切使わず、新DB側で version=1・is_active=1
              として新しいplan_item_idが採番される。created_byは旧行の値を
              そのまま引き継ぎ、元の作成者情報を保持する）。
           c. kitting_list_no単位で「新しいplan_item_id」の対応表を作り、
              対応するproduction_daily行を replace_daily_result() で新規登録する
              （新DBは空のため実質新規追加だが、delete-then-insertの実装を
              流用することで「1計画=1レコード」ルールにも自然に従う）。

    ロットNo重複チェック（各lot_noのcreate_plan_version()呼び出し前）：
    新DB（コピー先）に、これから引き継ごうとしているlot_noと同じlot_noを持つ
    kitting_plan_items行が既に存在するか確認する。存在し、かつその行の
    plan_start_datetimeが引き継ぎ元（旧DB）側の対応する行と1年（365日）以上
    離れている場合、「lot_no重複の疑いあり」として記録する（1年未満の差は、
    同一ロットの通常の月またぎ再利用とみなし警告しない）。判定不能
    （plan_start_datetimeがNone・パース不能）な組み合わせが含まれる場合は、
    誤って「重複でない」と断定しないよう「判定不能」として同様に記録する
    （_check_lot_no_duplicate()参照）。

    この重複チェックは、既に別の計画で使われているlot_noを、意図せず同一lot_no
    として扱ってしまうリスク（calculate_lot_completion(lot_no)等、lot_no単位で
    複数kitting_list_noを意図的に集約する関数が、無関係な計画を誤って同一ロット
    として合算してしまう）への注意喚起であり、コピー処理自体を止めるものではない
    （検知しても引き継ぎは通常通り続行する）。

    scrap_records・ng_declarations・wip_board_snapshotはコピーしない
    （モジュールdocstring参照）。

    戻り値：{
        "lots_copied": int,                  # コピーしたlot_no件数
        "kitting_plan_items_copied": int,    # コピーしたkitting_plan_items行数
        "production_daily_copied": int,      # コピーしたproduction_daily行数
        "lot_nos": [lot_no, ...],            # コピーしたlot_noの一覧
        "duplicate_lot_warnings": [           # ロットNo重複の疑いがあったlot_no一覧
            {"lot_no": str, "reason": "suspected_duplicate" | "undetermined",
             "old_plan_start_datetime": str または None,
             "existing_plan_start_datetime": str または None},
            ...
        ],
    }
    """
    try:
        config.set_db_path(old_db_path)
        incomplete_lots = list_incomplete_lots()

        lots_data = []
        for lot in incomplete_lots:
            plan_items = _fetch_plan_items_for_lot(lot["lot_no"])
            production_rows = _fetch_production_daily_for_lot(lot["lot_no"], lot["kitting_list_nos"])
            lots_data.append((lot, plan_items, production_rows))
    finally:
        # 読み取りフェーズの成否に関わらず、以後は新DBを「現在のDB」とする
        # （new_db_pathは呼び出し元が既に作成済みで、引き継ぎの成否によらず
        # 以後のアプリの接続先になるべきもののため）。
        config.set_db_path(new_db_path)

    summary = {
        "lots_copied": 0,
        "kitting_plan_items_copied": 0,
        "production_daily_copied": 0,
        "lot_nos": [],
        "duplicate_lot_warnings": [],
    }

    source_label = f"carry_over:{os.path.basename(os.path.dirname(old_db_path)) or old_db_path}"

    for lot, plan_items, production_rows in lots_data:
        # create_plan_version()で新DBへ書き込む前に、新DB側の既存状態に対して
        # ロットNo重複チェックを行う（このlot自身の書き込みで状態が変わる前に
        # 確認する必要があるため、create_plan_batch()より前で行う）。
        old_plan_start_datetimes = [item.get("plan_start_datetime") for item in plan_items]
        duplicate_check = _check_lot_no_duplicate(lot["lot_no"], old_plan_start_datetimes)
        if duplicate_check is not None:
            summary["duplicate_lot_warnings"].append({"lot_no": lot["lot_no"], **duplicate_check})

        batch_id = create_plan_batch(source_label, imported_by, len(plan_items))

        kitting_list_no_to_new_plan_item_id = {}
        for item in plan_items:
            new_plan_item_id = create_plan_version(
                batch_id, item["kitting_list_no"], dict(item), created_by=item.get("created_by"),
            )
            kitting_list_no_to_new_plan_item_id[item["kitting_list_no"]] = new_plan_item_id
            summary["kitting_plan_items_copied"] += 1

        for row in production_rows:
            new_plan_item_id = kitting_list_no_to_new_plan_item_id.get(row["kitting_list_no"])
            replace_daily_result(
                new_plan_item_id, row["kitting_list_no"], row["lot_id"], row["group_id"],
                row["report_date"], row["daily_qty"], row["worker_id"],
            )
            summary["production_daily_copied"] += 1

        summary["lots_copied"] += 1
        summary["lot_nos"].append(lot["lot_no"])

    return summary
