"""
ロット進捗チェック・日々の引落一覧の発注数（D-121）。
発注数はロットの値が各計画に同じ値で入っているので、同じ (ファイルNo, 面) に計画Noが複数あっても足さない。
同じ (ファイルNo, 面) の中で値が食い違うときは、発注数・未生産を空欄にし、ロット進捗チェックの備考に「発注数不一致(同一ファイル内)」と出す。
ファイルNoによって値が違うときは、値はそのまま表示し、そのロットの行の備考に「発注数不一致(ファイル間)」と出す（D-127）。
どちらも文字色は赤（構成基板数不一致と同じ区分）で、実績レポートには出さない。
"""
import os

import pytest

import config
from db.init_db import init_database_at
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production
from services.lot_status_history_service import get_daily_drawdown
from services.lot_status_service import check_lot_progress, evaluate_lot_status
from ui.production.daily_drawdown_window import _drawdown_row_to_values
from ui.production.lot_progress_window import build_display_rows


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=0)

    def plan(kitting_list_no, lot_no, file_no, planned_qty, order_qty, start):
        create_plan_version(batch, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": f"Board-{lot_no}", "production_side": "1",
            "planned_qty": planned_qty, "order_qty": order_qty, "plan_start_datetime": start, "mounting_line": "A",
        }, created_by="test")
        upsert_board_structure(f"Board-{lot_no}", 1)

    def actual(kitting_list_no, lot_no, report_date, qty):
        p = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
        insert_daily_production(p["plan_item_id"], kitting_list_no, lot_no, p["board_name"], report_date, qty, "W1")

    # 前回の調査【D】ケース1: 計画数1000に800、別の計画No（計画数200）に200。発注数は両方とも1000
    plan("K1", "L1", "F1", 1000, 1000, "2026/10/01 08:00:00")
    plan("K2", "L1", "F1", 200, 1000, "2026/10/10 08:00:00")
    actual("K1", "L1", "2026-10-02", 800)
    actual("K2", "L1", "2026-10-11", 200)
    # ケース3: 800 ＋ 別の計画Noで300（合計が発注数を超える）
    plan("K3", "L3", "F3", 1000, 1000, "2026/10/01 08:00:00")
    plan("K4", "L3", "F3", 200, 1000, "2026/10/10 08:00:00")
    actual("K3", "L3", "2026-10-02", 800)
    actual("K4", "L3", "2026-10-11", 300)
    # 同じ (ファイルNo, 面) の中で発注数が食い違う（実データでは0件）
    plan("K5", "L5", "F5", 100, 100, "2026/10/01 08:00:00")
    plan("K6", "L5", "F5", 100, 120, "2026/10/10 08:00:00")
    actual("K5", "L5", "2026-10-02", 50)
    # ファイルNoによって発注数が違う（実データの 166248・516526 と同じ形）
    plan("K7", "L7", "F7", 10, 10, "2026/10/01 08:00:00")
    plan("K8", "L7", "F8", 300, 300, "2026/10/01 08:00:00")
    actual("K7", "L7", "2026-10-02", 10)
    actual("K8", "L7", "2026-10-02", 10)
    upsert_board_structure("Board-L7", 2)  # ファイルNoが2つなので、構成基板数は一致にしておく


def _rows(lot_no):
    return [r for r in build_display_rows(check_lot_progress()) if r["lot_no"] == lot_no]


def test_order_qty_is_not_summed_across_plans(db):
    (row1,) = _rows("L1")
    assert (row1["order_qty"], row1["actual_qty"], row1["not_produced_qty"]) == ("1000", "1000", "0")
    (row3,) = _rows("L3")
    assert (row3["order_qty"], row3["actual_qty"], row3["not_produced_qty"]) == ("1000", "1100", "-100")
    assert "発注数不一致" not in row1["remarks"]
    assert row1["_status_color_category"] is None


def test_inconsistent_order_qty_in_same_file_is_blank_with_remark(db):
    (row,) = _rows("L5")
    assert row["order_qty"] == ""
    assert row["not_produced_qty"] == ""
    assert row["remarks"] == "発注数不一致(同一ファイル内)"
    assert row["_status_color_category"] == "needs_review"
    (f,) = evaluate_lot_status("L5")["boards"][0]["files"]
    assert f["order_qty"] is None and f["order_qty_inconsistent"] is True and f["not_produced_qty"] is None


def test_drawdown_not_produced_follows_lot_order_qty(db):
    by_date = {}
    for d in ("2026-10-02", "2026-10-11"):
        by_date[d] = {r["lot_no"]: r for r in get_daily_drawdown(d)}
    assert by_date["2026-10-02"]["L1"]["not_produced_qty"] == 200
    assert by_date["2026-10-11"]["L1"]["not_produced_qty"] == 0
    assert by_date["2026-10-11"]["L3"]["not_produced_qty"] == -100
    # 発注数が決まらない行の未生産は、0 ではなく空欄
    l5 = by_date["2026-10-02"]["L5"]
    assert l5["not_produced_qty"] is None
    assert _drawdown_row_to_values(l5)[-1] == ""


def test_order_qty_differs_between_files(db):
    rows = _rows("L7")
    assert sorted(r["order_qty"] for r in rows) == ["10", "300"]  # 足したり代表値に置き換えたりしない
    assert all(r["remarks"] == "発注数不一致(ファイル間)" for r in rows)
    assert all(r["_status_color_category"] == "needs_review" for r in rows)
    lot = evaluate_lot_status("L7")
    assert lot["order_qty_inconsistent_between_files"] is True
    # 実績レポートが使う区分・文言は変えない
    assert lot["status_color_category"] is None and "発注数" not in lot["status_remarks"]
    # 日々の引落一覧はロット進捗チェックと同じ区分
    (dd,) = [r for r in get_daily_drawdown("2026-10-02") if r["lot_no"] == "L7"][:1]
    assert dd["status_color_category"] == "needs_review"
