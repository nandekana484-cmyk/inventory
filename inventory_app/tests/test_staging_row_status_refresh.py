"""
登録待ち一覧の1行の状態を、実績を直した後に判定し直す（D-122）。
判定は一覧を開くときと同じ手順で、ほかの行の辞書は変えない。
"""
import os

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production
from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row
from models.production_side_master import upsert_production_side
from services.staging_eligibility import _load_staged_rows_from_db, reevaluate_staged_row


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=0)
    create_plan_version(batch, "K1", {
        "lot_no": "L1", "setup_file_no": "0901", "board_name": "ProdA", "production_side": "1",
        "planned_qty": 300, "order_qty": 1000, "plan_start_datetime": "2026/10/01 08:00:00", "mounting_line": "A",
    }, created_by="test")
    upsert_production_side("0901", "A", "1")  # 片面の製品
    csv_batch = create_csv_import_batch("actual.csv")
    for qty, date in ((300, "2026/10/2"), (250, "2026/10/3")):
        upsert_pending_csv_import_row({
            "csv_row_no": 2, "lot_no": "L1", "product_name": "ProdA", "daily_qty": qty, "report_date": date,
            "worker_id": "W1",
        }, import_batch_id=csv_batch)
    return _load_staged_rows_from_db()


def test_status_follows_registration_made_after_loading(staged):
    assert [r["status"] for r in staged] == ["duplicate", "duplicate"]
    p = find_plan_item_by_kitting_no("K1", "L1")
    insert_daily_production(p["plan_item_id"], "K1", "L1", "ProdA", "2026-10-02", 300, "W1")

    target = staged[1]
    refreshed = reevaluate_staged_row(target, staged)

    assert refreshed["status"] == "existing"
    assert [c["kitting_list_no"] for c in refreshed["matched"]] == ["K1"]
    # 引数の辞書（一覧が持っている行）は変えない
    assert target["status"] == "duplicate" and staged[0]["status"] == "duplicate"


def test_same_result_as_reloading(staged):
    p = find_plan_item_by_kitting_no("K1", "L1")
    insert_daily_production(p["plan_item_id"], "K1", "L1", "ProdA", "2026-10-02", 300, "W1")
    reloaded = _load_staged_rows_from_db()
    for i, row in enumerate(staged):
        assert reevaluate_staged_row(row, staged)["status"] == reloaded[i]["status"]


def test_target_must_be_one_of_all_rows(staged):
    with pytest.raises(ValueError):
        reevaluate_staged_row(dict(staged[0]), staged)
