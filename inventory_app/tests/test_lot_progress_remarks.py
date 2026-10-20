"""
ロット進捗チェックの備考（不足の内訳 D-179・面2待ち D-180）と、色の凡例の部品（D-181）。合成データで確かめる。
"""
import os

import pytest

import config
from db.init_db import init_database_at
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version
from models.production_side_master import upsert_production_side
from services.lot_status_remarks import format_shortfall_detail, format_side2_wait_remark
from services.lot_status_service import check_lot_progress
from ui.production.lot_progress_window import build_display_rows, STATUS_FILTER_OPTIONS

# (kitting_list_no, lot_no, setup_file_no, 面, 基板名)
PLANS = [
    ("A1", "L1", "0459", "2", "BRD-A"),                                  # 構成基板数 2 に対しファイルNo 1件 → 不足
    ("B1", "L2", "0400", "2", "BRD-B1"), ("B2", "L2", "0401", "2", "BRD-B2"),   # 構成基板数 3 に対し2件
    ("C1", "L3", "0766", "1", "BRD-C"), ("C2", "L3", "0765", "2", "BRD-C"),     # 0766 は面2待ち
    ("D1", "L4", "0741", "1", "BRD-D"), ("D2", "L4", "0742", "1", "BRD-D"),     # 0741・0742 とも面2待ち
]


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=len(PLANS))
    for kitting_list_no, lot_no, file_no, side, board in PLANS:
        create_plan_version(batch, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board, "production_side": side,
            "planned_qty": 10, "order_qty": 10, "plan_start_datetime": "2026/07/14 08:00:00", "mounting_line": "A",
        }, created_by="test")
    upsert_board_structure("BRD-A", 2)
    upsert_board_structure("BRD-B1", 3)
    upsert_board_structure("BRD-B2", 3)
    upsert_board_structure("BRD-C", 2)
    upsert_board_structure("BRD-D", 2)
    for file_no in ("0766", "0741", "0742"):
        upsert_production_side(file_no, "A", "1")
        upsert_production_side(file_no, "B", "2")


def _lots():
    return {lot["lot_no"]: lot for lot in check_lot_progress()}


def test_shortfall_detail(db):
    lots = _lots()
    assert format_shortfall_detail(lots["L1"]) == "構成基板数 2 に対し、計画のファイルNo は 1 件（0459：BRD-A）"
    assert format_shortfall_detail(lots["L2"]) == "構成基板数 3 に対し、計画のファイルNo は 2 件（0400：BRD-B1、0401：BRD-B2）"


def test_side2_wait_file_nos(db):
    lots = _lots()
    assert lots["L3"]["side2_wait_file_nos"] == ["0766"]
    assert lots["L4"]["side2_wait_file_nos"] == ["0741", "0742"]
    assert lots["L1"]["side2_wait_file_nos"] == []
    assert format_side2_wait_remark(["0741", "0742"]) == "面2待ち（ファイル0741・0742）"


def test_display_rows_remarks_and_filter_flag(db):
    rows = {r["lot_no"]: r for r in build_display_rows(check_lot_progress()) if r["setup_file_no"] != "未確定"}
    assert rows["L1"]["remarks"] == "構成基板数不足: 未確定1件(引落0) / 構成基板数 2 に対し、計画のファイルNo は 1 件（0459：BRD-A）"
    assert rows["L3"]["remarks"] == "面2待ち（ファイル0766）"
    assert rows["L3"]["_side2_wait"] is True and rows["L1"]["_side2_wait"] is False
    assert "面2待ち" in STATUS_FILTER_OPTIONS


def test_color_legend_swatches(tk_root):
    from ui.common.color_legend import build_color_legend
    frame = build_color_legend(tk_root, [("面2待ち", "#cfe2ff", None), ("登録済み", None, "gray")])
    (t1, l1), (t2, l2) = frame.swatches
    assert (t1, str(l1.cget("background"))) == ("面2待ち", "#cfe2ff")
    assert (t2, str(l2.cget("foreground"))) == ("登録済み", "gray")
    frame.destroy()
