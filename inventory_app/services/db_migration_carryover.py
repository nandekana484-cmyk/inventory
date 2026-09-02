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

import config
from models.kitting_plan import get_connection as get_plan_connection, create_plan_batch, create_plan_version
from models.production import get_connection as get_production_connection, replace_daily_result
from services.production_service import list_incomplete_lots


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

    scrap_records・ng_declarations・wip_board_snapshotはコピーしない
    （モジュールdocstring参照）。

    戻り値：{
        "lots_copied": int,                  # コピーしたlot_no件数
        "kitting_plan_items_copied": int,    # コピーしたkitting_plan_items行数
        "production_daily_copied": int,      # コピーしたproduction_daily行数
        "lot_nos": [lot_no, ...],            # コピーしたlot_noの一覧
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
    }

    source_label = f"carry_over:{os.path.basename(os.path.dirname(old_db_path)) or old_db_path}"

    for lot, plan_items, production_rows in lots_data:
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
