"""
record_lot_status_snapshot() が lot_status_history に書く内容（段階4で models → services の逆転を解消する前の挙動を固定する）。
services.production_service 経由で呼ぶ（段階4の前は models から import した名前、後は services に置いた関数）。
"""
import json
import os

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.lot_status_history import list_lot_status_history
from models.production import insert_daily_production
from services import production_service


@pytest.fixture
def monthly_db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    batch_id = create_plan_batch("plan.csv", imported_by="test", row_count=2)
    for kitting_list_no, side in (("K1", "1"), ("K2", "2")):
        create_plan_version(batch_id, kitting_list_no, {
            "lot_no": "L1", "setup_file_no": "F1", "board_name": "BoardA", "production_side": side,
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": "2026/07/22 08:00:00",
        }, created_by="test")
    for kitting_list_no, qty in (("K1", 100), ("K2", 60)):
        plan = find_plan_item_by_kitting_no(kitting_list_no, "L1")
        insert_daily_production(plan["plan_item_id"], kitting_list_no, "L1", "BoardA", "2026-07-23", qty, "W1")


def _rows(lot_no):
    rows = list_lot_status_history(lot_no)
    for row in rows:
        row.pop("id")
        assert row.pop("recorded_at")
        row["files_detail"] = json.loads(row["files_detail"])
    return rows


def test_record_lot_status_snapshot_row(monthly_db):
    row_id = production_service.record_lot_status_snapshot("L1", "test_trigger")
    assert isinstance(row_id, int)
    assert _rows("L1") == [{
        "lot_no": "L1", "status": "unregistered", "board_count": None, "visible_file_no_count": 1,
        "files_detail": [{
            "board_name": "BoardA", "setup_file_no": "F1", "production_side": "2", "order_qty": 100.0,
            "file_actual": 60, "lot_completed": 60, "surplus_qty": 0, "not_produced_qty": 40.0,
        }],
        "total_surplus_qty": 0.0, "total_not_produced_qty": 40.0, "trigger_source": "test_trigger",
    }]


def test_record_lot_status_snapshot_appends(monthly_db):
    production_service.record_lot_status_snapshot("L1", "a")
    production_service.record_lot_status_snapshot("L1", "b")
    assert [r["trigger_source"] for r in _rows("L1")] == ["a", "b"]


def test_registration_records_snapshot_via_service(monthly_db):
    """実績の登録（record_history=True）は、登録の後に履歴を1行記録する。"""
    production_service.overwrite_daily_result("K2", "L1", 70, "W1", report_date="2026-07-24")
    rows = _rows("L1")
    assert [r["trigger_source"] for r in rows] == ["overwrite"]
    assert rows[0]["total_not_produced_qty"] == 30.0
