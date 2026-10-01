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
    持ち越す性質のものではない）、wip_scrap_records（仕掛の仕損展開実績。
    wip_board_snapshotと同じく、ある時点のスナップショットに対する
    追加実績という性質のため、スナップショット本体を持ち越さない以上、
    その実績記録だけを単独で持ち越す意味が無い。2026-10-01、記載漏れを
    解消。従来からコード上はコピー対象外だったが、本docstringの列挙に
    テーブル名自体が記載されていなかった）。

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
import logging
import os
from datetime import datetime

import config
from models.kitting_plan import (
    get_connection as get_plan_connection, create_plan_batch, create_plan_version,
    init_kitting_plan_tables,
)
from models.production import (
    get_connection as get_production_connection, replace_daily_result,
)
from models.lot_status_history import record_lot_status_snapshot
from services.production_service import list_incomplete_lots

logger = logging.getLogger(__name__)

# plan_start_datetimeの実データ形式（"YYYY/MM/DD HH:MM:SS"、スラッシュ区切り＋時刻付き。
# UI_WORKFLOW_FIXES_NOTES.md/PRODUCTION_NG_ENHANCEMENTS_NOTES.md等で既出のkitting_plan_items
# 標準形式と同一）。
_PLAN_START_DATETIME_FORMAT = "%Y/%m/%d %H:%M:%S"

# ロットNo重複疑いと判定する経過日数のしきい値（約1年。うるう年を厳密には考慮しない、
# 「実装しやすい方針」としての単純な日数比較）。
_LOT_NO_DUPLICATE_THRESHOLD_DAYS = 365

# 「未着手」（旧DB側production_dailyに実績が1件も無い）計画を引き継ぐかどうかの
# 判定に使う、plan_start_datetime（実装予定日）の上限日数（2026-10-01追加）。
# 引継ぎ日（carry_over_incomplete_lots()実行日）からこの日数より先の実装予定日を
# 持つ未着手計画は、新DBへ持ち越さない（まだ先の話で、当面のDBでは不要な計画
# データが無制限に積み上がることを避けるため）。既に実績がある計画（＝着手済み）
# には、この上限を適用しない（ロット単位で引き継ぐ以上、着手済みの計画は
# plan_start_datetimeに関わらず常に引き継ぐ。下記_filter_plan_items_for_
# migration()参照）。
_UNSTARTED_PLAN_UPCOMING_LIMIT_DAYS = 50


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


def _lot_already_migrated(lot_no: str, plan_items: list, production_rows: list) -> bool:
    """
    新DB側（書き込み先）に、このlot_noの計画・実績が前回の実行で既に完全に
    コピー済みかどうかを判定する。carry_over_incomplete_lots()の再実行時に、
    前回既に成功したlotを二重に処理しない（版だけが無駄に積み重なる、
    または再コピーで時間を浪費する）ためのスキップ判定に使う。

    「完全にコピー済み」の判定基準（いずれも満たす場合のみTrue）：
      - 旧DB側の対象kitting_list_no全てについて、新DB側に同じkitting_list_no・
        lot_noの組み合わせでアクティブな計画行（is_active=1）が存在すること。
      - production_daily側でコピー対象だった各kitting_list_noについても、
        新DB側に同じkitting_list_no・lot_noの実績行が存在すること。
    いずれか1つでも欠けていれば「未完了（前回途中で失敗した）」とみなしFalseを返す
    （呼び出し元は続きから処理させる。create_plan_version()はkitting_list_no・
    lot_no単位で旧バージョンを無効化してから新バージョンを追加する設計のため、
    既に存在する項目を再度処理しても二重登録＝複数アクティブ行にはならないが、
    無駄なバージョン増加・再コピーを避けるため、完全に完了しているlotは
    このチェックで事前にスキップする）。

    plan_itemsが空（該当lotに計画行が無い異常系）の場合は、判定材料が無いため
    安全側に倒してFalse（スキップしない＝通常通り処理させる）を返す。
    """
    if not plan_items:
        return False

    # 新DBがまだ一度もcreate_plan_batch()等を呼ばれていない真っさらな状態だと
    # kitting_plan_itemsテーブル自体が存在しないため、
    # _fetch_existing_plan_start_datetimes_for_lot()と同様に事前に存在を保証する。
    # production_dailyについては、本関数の呼び出し前提（carry_over_incomplete_
    # lots()のdocstring参照）として、呼び出し元が事前にinit_database_at()で
    # schema.sql一式（production_daily含む）を作成済みであるため、ここで
    # 改めて存在を保証する必要は無い（2026-10-01、production_records廃止に伴い
    # init_production_table()の呼び出しを削除。この呼び出しは元々
    # production_records（廃止済みの別テーブル）を作成するだけで、
    # production_dailyの存在を保証してはいなかった。コメントの記載が
    # 誤っていたため、合わせて修正した）。
    init_kitting_plan_tables()

    with get_plan_connection() as con:
        for item in plan_items:
            row = con.execute("""
                SELECT 1 FROM kitting_plan_items
                WHERE kitting_list_no = ? AND COALESCE(lot_no, '') = ?
                  AND COALESCE(is_active, 1) = 1
                LIMIT 1
            """, (item["kitting_list_no"], lot_no)).fetchone()
            if row is None:
                return False

    if production_rows:
        expected_kitting_list_nos = {row["kitting_list_no"] for row in production_rows}
        with get_production_connection() as con:
            for kitting_list_no in expected_kitting_list_nos:
                row = con.execute("""
                    SELECT 1 FROM production_daily
                    WHERE kitting_list_no = ? AND COALESCE(lot_id, '') = COALESCE(?, '')
                    LIMIT 1
                """, (kitting_list_no, lot_no)).fetchone()
                if row is None:
                    return False

    return True


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


def _filter_plan_items_for_migration(plan_items: list, production_rows: list, reference_date) -> dict:
    """
    ロット単位で引き継ぐplan_items（_fetch_plan_items_for_lot()の結果、
    そのlot_noに属する全kitting_list_noの計画行）のうち、実際にコピーする
    行を選別する（2026-10-01追加）。

    方針（ユーザー確定）：
      - 既に実績がある（production_rowsにそのkitting_list_noの行が1件以上
        存在する）計画は、着手済みとみなし、plan_start_datetimeに関わらず
        常に含める（ロット単位で引き継ぐという方針により、同一ロットの
        完了済みfile_noを除外しないのと同じ考え方）。
      - 実績が1件も無い（未着手の）計画は、plan_start_datetimeをパースし、
        reference_date（引継ぎ日、通常は本関数呼び出し時点の日付）からの
        日数差が_UNSTARTED_PLAN_UPCOMING_LIMIT_DAYS（50日）以内であれば
        含める。50日より先（未来）の場合は除外する。過去日付（既に予定日を
        過ぎているのにまだ未着手）は日数差が負になるため、50日以内の条件を
        満たし、常に含まれる（却って優先して引き継ぐべき対象のため、これは
        意図した挙動）。
      - plan_start_datetimeが空・パース不能（形式不正）な未着手計画は、
        「安全側」の扱いとして**除外せず含める**。理由：50日上限の目的は
        「先の話でまだ不要な計画データが無制限に積み上がるのを防ぐ」ことで
        あり、判定不能なデータを誤って除外すると、実は近日中に着手予定
        だった計画が新DBから silently 失われるリスクがある（本来の計画・
        実績データが1行も新DB側に存在しなくなり、後から気づいて復旧する
        手段が無い）。一方、誤って含めてしまっても、実害は「本来除外したい
        計画が1件余分に残る」という软らかい失敗で済み、人が後から
        気づいて対処できる。_check_lot_no_duplicate()が判定不能を「重複で
        ない」と断定せず警告に残す方針と同じ、「データを自動的に失わない」
        という本アプリ全体の既存方針に揃えた。この判定不能だった行は
        戻り値のundetermined_kitting_list_nosに記録し、呼び出し元
        （carry_over_incomplete_lots()）がsummaryに集約する。

    戻り値：{"included_items": [plan_itemの辞書, ...]（コピー対象）,
             "excluded_kitting_list_nos": [str, ...]（50日超過で除外した
             未着手計画のkitting_list_no一覧）,
             "undetermined_kitting_list_nos": [str, ...]（plan_start_datetime
             が判定不能だったため、安全側で含めた未着手計画のkitting_list_no
             一覧）}
    """
    kitting_list_nos_with_actual = {row["kitting_list_no"] for row in production_rows}

    included_items = []
    excluded_kitting_list_nos = []
    undetermined_kitting_list_nos = []

    for item in plan_items:
        kitting_list_no = item["kitting_list_no"]
        if kitting_list_no in kitting_list_nos_with_actual:
            included_items.append(item)  # 着手済み：常に含める
            continue

        parsed = _parse_plan_start_datetime(item.get("plan_start_datetime"))
        if parsed is None:
            included_items.append(item)  # 判定不能：安全側で含める
            undetermined_kitting_list_nos.append(kitting_list_no)
            continue

        days_ahead = (parsed.date() - reference_date).days
        if days_ahead <= _UNSTARTED_PLAN_UPCOMING_LIMIT_DAYS:
            included_items.append(item)
        else:
            excluded_kitting_list_nos.append(kitting_list_no)

    return {
        "included_items": included_items,
        "excluded_kitting_list_nos": excluded_kitting_list_nos,
        "undetermined_kitting_list_nos": undetermined_kitting_list_nos,
    }


def carry_over_incomplete_lots(old_db_path: str, new_db_path: str, imported_by: str = "carry_over",
                                reference_date=None) -> dict:
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

    scrap_records・ng_declarations・wip_board_snapshot・wip_scrap_recordsは
    コピーしない（モジュールdocstring参照）。

    途中でのエラー・再実行について：
    lot_noごとの書き込み処理は個別にtry/exceptで捕捉する。あるlot_noの処理中に
    例外が発生した場合、その例外を外へ伝播させず（呼び出し元にトレースバックの
    ままアプリを止めさせず）、"failed_lot_nos"に記録した上で、以降の未処理lot
    （まだ着手していないもの）も「前段の失敗により未処理」として同じく
    "failed_lot_nos"に記録し、そこで処理を打ち切る（同じ失敗が続く可能性が高い
    状況で残りのlotを闇雲に試行し続けても無意味なため）。それまでに成功していた
    lot_noは"lot_nos"にそのまま残り、ロールバックはしない（新DB側は既に
    commit済みのため）。

    本関数を同じ引数（old_db_path・new_db_path）でもう一度呼び直せば、
    "続きから"処理できる：各lot_noについて、新DB側に計画・実績が既に完全に
    コピー済みであれば_lot_already_migrated()がTrueを返し、"skipped_lot_nos"に
    記録してそのlotの書き込み処理自体をスキップする（二重登録を避ける。
    create_plan_version()自体もkitting_list_no・lot_no単位で旧バージョンを
    無効化してから新バージョンを追加する設計のため、仮にスキップせず再処理
    しても複数のアクティブ行が並立することはないが、無駄なバージョン増加・
    再コピーを避けるためにここで事前にスキップする）。前回失敗した/未着手だった
    lotは通常通り処理される。

    未着手計画の50日上限（2026-10-01追加、_filter_plan_items_for_migration()
    参照）：実績が1件も無い（旧DB側production_dailyに行が無い）計画は、
    plan_start_datetime（実装予定日）がreference_date（引継ぎ日、省略時は
    本関数呼び出し時点の日付）から_UNSTARTED_PLAN_UPCOMING_LIMIT_DAYS
    （50日）以内のものだけをコピー対象とする。既に実績がある計画は、
    ロット単位で引き継ぐ方針（下記）により、この上限を適用せず常にコピーする。
    この絞り込みは、lot_noごとの処理ループの先頭（_lot_already_migrated()の
    判定より前）で行う：除外された計画は新DB側に存在しないのが正しい状態
    のため、"既に完了済みかどうか"の判定（_lot_already_migrated()）も、
    絞り込み後の対象行だけを見て行う必要がある（絞り込み前の全plan_itemsで
    判定すると、除外した行が新DB側に存在しないことを理由に「未完了（前回
    失敗した）」と誤判定され、スキップされず毎回無駄に再処理されてしまう）。

    引き継ぎ単位のロット統一（2026-10-01、調査により既存実装で対応済みと
    確認。念のため明記）：_fetch_plan_items_for_lot()はlot_no単位の全
    アクティブ計画行を無条件に取得し、_fetch_production_daily_for_lot()も
    そのlot_noの全kitting_list_noに対応する実績を取得するため、完了済み・
    未完了を問わずロット全体が単位としてコピーされる（ファイルNo単位で
    個別に完了判定して間引く処理は行っていない）。50日上限によるフィルタ
    （上記）は、この「ロット全体を単位とする」方針とは独立した、未着手計画
    固有の別軸の絞り込みである（着手済みの計画はロットの一部であれば
    常にコピーされ、50日を超えていても除外されない）。

    戻り値：{
        "lots_copied": int,                  # 今回の呼び出しで新規にコピーしたlot_no件数
        "kitting_plan_items_copied": int,    # 今回コピーしたkitting_plan_items行数
        "production_daily_copied": int,      # 今回コピーしたproduction_daily行数
        "lot_nos": [lot_no, ...],            # 今回新規にコピーしたlot_noの一覧
        "skipped_lot_nos": [lot_no, ...],    # 前回までに完了済みのためスキップしたlot_no
        "failed_lot_nos": [                   # 失敗した（前段の失敗で未処理になったものを含む）lot_no
            {"lot_no": str, "error": str},
            ...
        ],
        "duplicate_lot_warnings": [           # ロットNo重複の疑いがあったlot_no一覧
            {"lot_no": str, "reason": "suspected_duplicate" | "undetermined",
             "old_plan_start_datetime": str または None,
             "existing_plan_start_datetime": str または None},
            ...
        ],
        "excluded_unstarted_items": [         # 2026-10-01追加。50日超過のため
                                               # 除外した未着手計画
            {"lot_no": str, "kitting_list_no": str, "plan_start_datetime": str},
            ...
        ],
        "undetermined_plan_start_datetime_items": [  # 2026-10-01追加。
                                               # plan_start_datetimeが判定不能
                                               # だったため安全側で含めた未着手計画
            {"lot_no": str, "kitting_list_no": str},
            ...
        ],
        "lots_with_no_items_to_copy": [lot_no, ...],  # 2026-10-01追加。
                                               # 全ての計画が50日超過で除外され、
                                               # 結果的に今回コピー対象の計画が
                                               # 1件も残らなかったlot_no
                                               # （lot_nos・skipped_lot_nos・
                                               # failed_lot_nosのいずれにも
                                               # 含まれない）
    }
    """
    reference_date = reference_date or datetime.now().date()

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
        "skipped_lot_nos": [],
        "failed_lot_nos": [],
        "duplicate_lot_warnings": [],
        "excluded_unstarted_items": [],
        "undetermined_plan_start_datetime_items": [],
        "lots_with_no_items_to_copy": [],
    }

    source_label = f"carry_over:{os.path.basename(os.path.dirname(old_db_path)) or old_db_path}"

    for idx, (lot, plan_items, production_rows) in enumerate(lots_data):
        lot_no = lot["lot_no"]
        try:
            filter_result = _filter_plan_items_for_migration(plan_items, production_rows, reference_date)
            included_items = filter_result["included_items"]

            for kitting_list_no in filter_result["excluded_kitting_list_nos"]:
                original_item = next(i for i in plan_items if i["kitting_list_no"] == kitting_list_no)
                summary["excluded_unstarted_items"].append({
                    "lot_no": lot_no,
                    "kitting_list_no": kitting_list_no,
                    "plan_start_datetime": original_item.get("plan_start_datetime"),
                })
            for kitting_list_no in filter_result["undetermined_kitting_list_nos"]:
                summary["undetermined_plan_start_datetime_items"].append({
                    "lot_no": lot_no, "kitting_list_no": kitting_list_no,
                })

            if not included_items:
                # このlot_noに属する計画が全て50日超過の未着手計画だった場合、
                # 今回は引き継ぎ対象外とする（skipped_lot_nosは「前回までに
                # 完了済み」の意味のため使わない。failed_lot_nosもエラーでは
                # ないため使わない）。
                summary["lots_with_no_items_to_copy"].append(lot_no)
                continue

            if _lot_already_migrated(lot_no, included_items, production_rows):
                summary["skipped_lot_nos"].append(lot_no)
                continue

            # create_plan_version()で新DBへ書き込む前に、新DB側の既存状態に対して
            # ロットNo重複チェックを行う（このlot自身の書き込みで状態が変わる前に
            # 確認する必要があるため、create_plan_batch()より前で行う）。
            old_plan_start_datetimes = [item.get("plan_start_datetime") for item in included_items]
            duplicate_check = _check_lot_no_duplicate(lot_no, old_plan_start_datetimes)
            if duplicate_check is not None:
                summary["duplicate_lot_warnings"].append({"lot_no": lot_no, **duplicate_check})

            batch_id = create_plan_batch(source_label, imported_by, len(included_items))

            kitting_list_no_to_new_plan_item_id = {}
            for item in included_items:
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

            # このlot_noの計画・実績が新DB側へ全てコピーされた時点で、
            # lot_status_historyへ状態スナップショットを記録する（2026-09-30
            # 追加）。config.DB_PATHは既にnew_db_pathへ切り替わっているため
            # （このtryブロックの外側、読み取りフェーズ直後のfinallyで切替済み）、
            # evaluate_lot_status()は新DB側の内容を見て計算する。記録の失敗は
            # 引き継ぎ処理自体を失敗させない（services.production_service.
            # register_daily_result()等と同じ方針）。
            try:
                record_lot_status_snapshot(lot_no, "carryover")
            except Exception:
                logger.exception(
                    "lot_status_historyの記録に失敗しました（lot_no=%s, "
                    "trigger_source=carryover）。引き継ぎ処理自体はそのまま続行します。",
                    lot_no,
                )

            summary["lots_copied"] += 1
            summary["lot_nos"].append(lot_no)
        except Exception as e:
            summary["failed_lot_nos"].append({"lot_no": lot_no, "error": str(e)})
            for remaining_lot, _remaining_items, _remaining_rows in lots_data[idx + 1:]:
                summary["failed_lot_nos"].append({
                    "lot_no": remaining_lot["lot_no"],
                    "error": "前段のロットの失敗により未処理のまま処理を打ち切りました。",
                })
            break

    return summary
