"""
面2を登録したとき、連動で面1の既存の実績が置き換わるかの判定（D-126、services.production_service.preview_opposite_side_overwrites()）。
判定は DB に書き込まず、register_actual_and_ng() で実際に登録した結果と一致すること。
"""
import os

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production, list_daily_production_by_kitting_no
from services.production_entry_service import register_actual_and_ng
from services.production_service import (
    preview_opposite_side_overwrite, preview_opposite_side_overwrites, search_plan_by_kitting_no,
)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=0)
    # L1: 面1（K1）と面2（K2）。L2: 面1が1件（K3）、面2が2件（K4・K5、同じファイルNo）
    for k, lot, side, start in (("K1", "L1", "1", "2026/10/01 08:00:00"), ("K2", "L1", "2", "2026/10/05 08:00:00"),
                                ("K3", "L2", "1", "2026/10/01 08:00:00"), ("K4", "L2", "2", "2026/10/05 08:00:00"),
                                ("K5", "L2", "2", "2026/10/06 08:00:00")):
        create_plan_version(batch, k, {
            "lot_no": lot, "setup_file_no": "F1", "board_name": "B", "production_side": side,
            "planned_qty": 1000, "order_qty": 1000, "plan_start_datetime": start, "mounting_line": "A",
        }, created_by="test")


def _actual(k, lot, date, qty):
    p = find_plan_item_by_kitting_no(k, lot)
    insert_daily_production(p["plan_item_id"], k, lot, "B", date, qty, "W1")


def _plan(k, lot):
    return search_plan_by_kitting_no(k, lot)[0]


def test_change_is_reported_when_qty_or_date_differs(db):
    _actual("K1", "L1", "2026-10-02", 800)
    change = preview_opposite_side_overwrite(_plan("K2", "L1"), 700, "2026-10-06")
    assert change == {"kitting_list_no": "K1", "lot_no": "L1", "production_side": "1",
                      "old": [("2026-10-02", 800)], "new_date": "2026-10-06", "new_qty": 700}
    # 日付だけ違う／数量だけ違う場合も置き換わる
    assert preview_opposite_side_overwrite(_plan("K2", "L1"), 800, "2026-10-06") is not None
    assert preview_opposite_side_overwrite(_plan("K2", "L1"), 700, "2026-10-02") is not None


def test_no_change_when_same_content_or_no_existing(db):
    assert preview_opposite_side_overwrite(_plan("K2", "L1"), 700, "2026-10-06") is None  # 面1に実績が無い
    _actual("K1", "L1", "2026-10-02", 800)
    assert preview_opposite_side_overwrite(_plan("K2", "L1"), 800, "2026-10-02") is None  # 同じ内容


def test_manual_entry_keeps_existing_date(db):
    # 手入力（日付の指定なし）では、上書きは面1の既存の日付を引き継ぐ（replace_daily_result()）
    _actual("K1", "L1", "2026-10-02", 800)
    change = preview_opposite_side_overwrite(_plan("K2", "L1"), 700, None)
    assert (change["new_date"], change["new_qty"]) == ("2026-10-02", 700)
    assert preview_opposite_side_overwrite(_plan("K2", "L1"), 800, None) is None


def test_preview_matches_actual_registration(db):
    _actual("K1", "L1", "2026-10-02", 800)
    change = preview_opposite_side_overwrite(_plan("K2", "L1"), 700, "2026-10-06")
    register_actual_and_ng(_plan("K2", "L1"), {}, 700, {}, None, "W1", "test", report_date="2026-10-06")
    after = [(r["report_date"], r["daily_qty"]) for r in list_daily_production_by_kitting_no("K1", "L1")]
    assert after == [(change["new_date"], change["new_qty"])]


def test_bulk_counts_records_written_earlier_in_same_batch(db):
    # 面2の K4・K5 が同じ面1の K3 を相手にする。DB にはまだ面1の実績が無いが、K4 の登録で入った面1を K5 が置き換える
    changes = preview_opposite_side_overwrites([
        ("row1", _plan("K4", "L2"), 300, "2026-10-06"),
        ("row2", _plan("K5", "L2"), 200, "2026-10-07"),
    ])
    assert set(changes) == {"row2"}
    assert changes["row2"]["old"] == [("2026-10-06", 300)]
    assert (changes["row2"]["new_date"], changes["row2"]["new_qty"]) == ("2026-10-07", 200)


def test_message_format():
    from ui.production.opposite_side_overwrite_dialog import format_opposite_change
    change = {"kitting_list_no": "K1", "lot_no": "L1", "production_side": "1",
              "old": [("2026-10-02", 800)], "new_date": "2026-10-06", "new_qty": 700.0}
    assert format_opposite_change(change) == "面1の実績が 800（10/2） → 700（10/6）に変わります"


def test_reverse_direction_is_not_confirmed(db):
    # 面1の登録で面2が変わる逆向きは、確認の対象にしない（利用者の決定の範囲外。今までどおり確認なしで連動する）
    _actual("K2", "L1", "2026-10-06", 700)
    assert preview_opposite_side_overwrite(_plan("K1", "L1"), 800, "2026-10-02") is None
