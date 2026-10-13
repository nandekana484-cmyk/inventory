"""
NG 一覧・仕掛一覧の行の組み立てと、在庫差異レポート前の未処理件数（段階4で services へ移す前の挙動を固定する）。
期待値は、移す前のコードが返した値をそのまま書いている。
"""
import os

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version
from models.ng_declarations import save_ng_declaration
from models.ng_exclusion_list import mark_ng_excluded
from models.scrap_records import replace_scrap_records
from models.wip_board_snapshot import save_wip_snapshot
from models.wip_exclusion_list import mark_wip_excluded
from models.wip_scrap_records import save_wip_scrap_records
from services.unprocessed_check_service import check_unprocessed_items
from ui.ng_wip.ng_input_window import NgInputWindow
from ui.ng_wip.wip_expansion_window import WipExpansionWindow


@pytest.fixture
def monthly_db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()


@pytest.fixture
def ng_data(monthly_db):
    batch_id = create_plan_batch("plan.csv", imported_by="test", row_count=2)
    for kitting_list_no, lot_no, board in (("K1", "L1", "BoardA"), ("K2", "L2", "BoardB")):
        create_plan_version(batch_id, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": "F1", "board_name": board, "production_side": "2",
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": "2026/07/22 08:00:00",
        }, created_by="test")
    save_ng_declaration("K1", "F1", 2, 3, "2026-07-20", lot_no="L1")                       # 未展開
    save_ng_declaration("K2", "F1", 2, 2, "2026-07-20", lot_no="L2")                       # 展開済み
    replace_scrap_records("K2", "F1", 2, [{"part_no": "P1", "ng_qty": 2}], "2026-07-21", lot_no="L2")
    replace_scrap_records("K3", "F3", 1, [{"part_no": "P1", "ng_qty": 1}, {"part_no": "P2", "ng_qty": 4}],
                          "2026-07-22", lot_no="L3")                                         # 展開済み（申告記録なし）
    save_ng_declaration("F9", "F9", 1, 5, "2026-07-23", is_unplanned=True)                  # 計画外・未展開
    save_ng_declaration("K4", "F4", 1, 1.5, "2026-07-24", lot_no="L4")                      # 未展開だが対象外
    mark_ng_excluded("K4", 1, lot_no="L4", reason="test")


@pytest.fixture
def wip_data(monthly_db):
    save_wip_snapshot([
        {"kitting_list_no": "W1", "file_no": "F1", "board_name": "BoardA", "production_side": "1",
         "mounting_line": "A", "lot_no": "L1", "surplus_qty": 10},                          # 未確定
        {"kitting_list_no": "W2", "file_no": "F2", "board_name": "BoardB", "production_side": "2",
         "mounting_line": None, "lot_no": None, "surplus_qty": 2.5},                         # 確定済み
        {"kitting_list_no": "W3", "file_no": "F3", "board_name": "BoardC", "production_side": "1",
         "mounting_line": "B", "lot_no": "L3", "surplus_qty": 4},                            # 未確定だが対象外
    ])
    save_wip_scrap_records("W2", "F2", 2, [{"part_no": "P1", "qty": 1}], lot_no=None)
    save_wip_scrap_records("W9", "F9", 1, [{"part_no": "P1", "qty": 1}], lot_no="L9")       # スナップショットなし
    mark_wip_excluded("W3", "L3", "F3", 1, reason="test")


NG_ROWS = [
    ("F9", "", "", "F9", "面1", "計画外", "未展開", "5", "0", "0", "2026-07-23", ""),
    ("K1", "L1", "BoardA", "F1", "面2", "", "未展開", "3", "0", "0", "2026-07-20", ""),
    ("K2", "L2", "BoardB", "F1", "面2", "", "展開済み", "2", "1", "1", "2026-07-21", ""),
    ("K3", "L3", "", "F3", "面1", "", "展開済み（申告記録なし）", "", "2", "2", "2026-07-22", ""),
    ("K4", "L4", "", "F4", "面1", "", "未展開", "1.5", "0", "0", "2026-07-24", "対象外"),
]

# created_at（スナップショットの保存時刻）は比べないので None にしている。
WIP_ROWS = [
    ("W1", "BoardA", "F1", "1", "L1", "A", "10", "未確定", None, ""),
    ("W2", "BoardB", "F2", "2", "", "", "2.5", "確定済み", None, ""),
    ("W3", "BoardC", "F3", "1", "L3", "B", "4", "未確定", None, "対象外"),
    ("W9", "", "F9", "1", "L9", "", "", "確定済み(スナップショットなし)", "", ""),
]


def _without_created_at(rows):
    index = WipExpansionWindow.WIP_LIST_COLUMNS.index("created_at")
    result = []
    for row in rows:
        row = list(row)
        if row[index]:
            row[index] = None
        result.append(tuple(row))
    return result


def test_ng_list_columns():
    assert NgInputWindow.NG_LIST_COLUMNS == (
        "kitting_list_no", "lot_no", "board_name", "file_no", "side",
        "is_unplanned", "status", "declared_ng_qty", "part_count",
        "record_count", "last_report_date", "excluded",
    )


def test_wip_list_columns():
    assert WipExpansionWindow.WIP_LIST_COLUMNS == (
        "kitting_list_no", "board_name", "file_no", "side", "lot_no",
        "mounting_line", "surplus_qty", "status", "created_at", "excluded",
    )
    assert WipExpansionWindow.STATUS_CONFIRMED_ORPHAN == "確定済み(スナップショットなし)"


def test_ng_list_rows(ng_data):
    assert NgInputWindow._fetch_ng_list_rows() == NG_ROWS


def test_wip_list_rows(wip_data):
    assert _without_created_at(WipExpansionWindow._fetch_wip_list_rows()) == WIP_ROWS


def test_empty_lists(monthly_db):
    assert NgInputWindow._fetch_ng_list_rows() == []
    assert WipExpansionWindow._fetch_wip_list_rows() == []
    assert check_unprocessed_items() == {"ng_unprocessed_count": 0, "wip_unprocessed_count": 0}


def test_unprocessed_counts(ng_data, wip_data):
    """未処理＝NG は「未展開」、仕掛は「未確定」で、どちらも対象外でないもの。"""
    assert check_unprocessed_items() == {"ng_unprocessed_count": 2, "wip_unprocessed_count": 1}


# ---------------------------------------------------------------- 段階4: services へ移した後の構造

def test_screen_constants_refer_to_services():
    from services import ng_list_service, wip_list_service
    assert NgInputWindow.NG_LIST_COLUMNS is ng_list_service.NG_LIST_COLUMNS
    assert WipExpansionWindow.WIP_LIST_COLUMNS is wip_list_service.WIP_LIST_COLUMNS
    assert WipExpansionWindow.STATUS_CONFIRMED_ORPHAN is wip_list_service.STATUS_CONFIRMED_ORPHAN


def test_services_do_not_import_ui_or_gui():
    import ast
    import inspect
    from services import ng_list_service, wip_list_service, unprocessed_check_service
    for module in (ng_list_service, wip_list_service, unprocessed_check_service):
        names = set()
        for node in ast.walk(ast.parse(inspect.getsource(module))):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        assert not names & {"tkinter", "tkcalendar", "ui"}, module.__name__
