"""
services/production_service.py の帳票・ロット状態の出力を、スナップショット（tests/snapshots/production_service.json）で固定する（段階5）。
段階5で関数を production_report_service・lot_status_service へ移しても、出力がそのまま一致することを確かめる。

テストデータのケース:
  L1: 両面の計画（K1=面1・K2=面2、F1）。面1の実績100 > 面2の実績80（面1・面2の不整合の警告、面1の行は表示しない）
  L2: 同じファイルNo・同じ面（F2・面2）に別バッチの計画が2件（K3・K4）。完成数は2要素のキーで加算する（60＋40＝100）
  L3: NG のある計画（K5。NG 申告と NG 実績）。構成基板数2に対してファイルNoが1つ（不足）
  L4: 実績の無い未完了のロット（K6）。構成基板数マスタ未登録
  L5: 本日の日付の実績（K7。日報の対象）
スナップショットを作り直すときは、環境変数 UPDATE_SNAPSHOTS=1 を付けて実行する（中身を確かめてからコミットすること）。
"""
import json
import os
import pathlib
from datetime import datetime

import pytest

import config
from db.init_db import init_database_at
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.ng_declarations import save_ng_declaration
from models.production import insert_daily_production
from models.scrap_records import replace_scrap_records
from services import production_service

SNAPSHOT = pathlib.Path(__file__).resolve().parent / "snapshots" / "production_service.json"
TODAY_PLACEHOLDER = "<TODAY>"


@pytest.fixture
def report_db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()

    def plan(batch_id, kitting_list_no, lot_no, file_no, side, board, order_qty=100, start="2026/07/22 08:00:00"):
        create_plan_version(batch_id, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board, "production_side": side,
            "planned_qty": order_qty, "order_qty": order_qty, "plan_start_datetime": start,
            "mounting_line": "A",
        }, created_by="test")

    def actual(kitting_list_no, lot_no, report_date, qty):
        p = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
        insert_daily_production(p["plan_item_id"], kitting_list_no, lot_no, p["board_name"], report_date, qty, "W1")

    batch1 = create_plan_batch("plan1.csv", imported_by="test", row_count=6)
    plan(batch1, "K1", "L1", "F1", "1", "BoardA")
    plan(batch1, "K2", "L1", "F1", "2", "BoardA")
    plan(batch1, "K3", "L2", "F2", "2", "BoardB", start="2026/07/20 08:00:00")
    plan(batch1, "K5", "L3", "F3", "2", "BoardC")
    plan(batch1, "K6", "L4", "F4", "2", "BoardD")
    plan(batch1, "K7", "L5", "F5", "2", "BoardA", order_qty=30)
    batch2 = create_plan_batch("plan2.csv", imported_by="test", row_count=1)
    plan(batch2, "K4", "L2", "F2", "2", "BoardB", start="2026/07/21 08:00:00")

    upsert_board_structure("BoardA", 1)
    upsert_board_structure("BoardB", 1)
    upsert_board_structure("BoardC", 2)

    actual("K1", "L1", "2026-07-22", 100)
    actual("K2", "L1", "2026-07-23", 80)
    actual("K3", "L2", "2026-07-20", 60)
    actual("K4", "L2", "2026-07-21", 40)
    actual("K5", "L3", "2026-07-24", 90)
    actual("K7", "L5", datetime.now().strftime("%Y-%m-%d"), 30)

    save_ng_declaration("K5", "F3", 2, 5, "2026-07-24", lot_no="L3")
    replace_scrap_records("K5", "F3", 2, [{"part_no": "P1", "ng_qty": 5}], "2026-07-24", lot_no="L3")


def _normalize(value, today):
    """JSON にできる形にそろえる（tuple → list、tuple のキー → 文字列、set → 並べたリスト、今日の日付 → <TODAY>）。"""
    if isinstance(value, dict):
        return {(k if isinstance(k, str) else repr(k)): _normalize(v, today) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v, today) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_normalize(v, today) for v in value), key=repr)
    if isinstance(value, str) and today in value:
        return value.replace(today, TODAY_PLACEHOLDER)
    return value


def _outputs():
    ps = production_service
    lots = ["L1", "L2", "L3", "L4", "L5"]
    return {
        "build_daily_report": ps.build_daily_report(),
        "build_monthly_report_all": ps.build_monthly_report("2026-07-01", "2099-12-31"),
        "build_monthly_report_part": ps.build_monthly_report("2026-07-21", "2026-07-23"),
        "calculate_lot_completion": {lot: ps.calculate_lot_completion(lot) for lot in lots},
        "evaluate_lot_status": {lot: ps.evaluate_lot_status(lot) for lot in lots},
        "evaluate_lot_status_cutoff_0721": {lot: ps.evaluate_lot_status(lot, cutoff_date="2026-07-21") for lot in lots},
        "evaluate_all_lot_status": ps.evaluate_all_lot_status(),
        "evaluate_all_lot_status_cutoff_0722": ps.evaluate_all_lot_status(cutoff_date="2026-07-22"),
        "list_incomplete_lots": ps.list_incomplete_lots(),
        "check_lot_progress": ps.check_lot_progress(),
        "build_wip_extraction_rows": ps.build_wip_extraction_rows(lots),
        "status_constants": {
            "DRAWDOWN_ZERO_STATUSES": ps.DRAWDOWN_ZERO_STATUSES,
            "NEEDS_REVIEW_STATUSES": ps.NEEDS_REVIEW_STATUSES,
        },
    }


def test_snapshot(report_db):
    today = datetime.now().strftime("%Y-%m-%d")
    actual = _normalize(_outputs(), today)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for key in expected:
        assert actual[key] == expected[key], key
    assert set(actual) == set(expected)


def test_snapshot_covers_required_cases():
    """必須のケース（両面・同じファイルNo・面の複数バッチの加算・NG・未完了）がスナップショットに入っていること。"""
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    completion = expected["calculate_lot_completion"]
    assert completion["L1"]["file_actuals"] == {"('F1', '1')": 100, "('F1', '2')": 80}   # 両面
    assert completion["L2"]["file_actuals"] == {"('F2', '2')": 100}                        # 2バッチの加算（60＋40）
    assert completion["L2"]["completed_quantity"] == 100
    assert [lot["lot_no"] for lot in expected["list_incomplete_lots"]] == ["L1", "L3", "L4"]  # 未完了
    monthly_rows = expected["build_monthly_report_all"][0]
    assert any(row.get("kitting_list_no") == "K5" and row.get("has_ng") for row in monthly_rows)  # NG のある計画
