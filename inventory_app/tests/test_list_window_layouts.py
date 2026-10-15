"""
画面の作りの確認（実際にウインドウを生成する）。
- 実績CSV取込の登録待ち一覧: 左右の幅をドラッグで変えられ、どちらも最小幅より狭くならない（D-123）
- 実績レポート: 縦スクロールバー（D-124）
- 日々の引落一覧: 構成基板数の列、ロットごとの縞と状態の文字色、「面」の列なし（D-125）
"""
import os
import tkinter as tk
from tkinter import ttk

import pytest

import config
from db.init_db import init_database_at
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production
from ui.common import lot_row_style
from ui.production.daily_drawdown_window import DailyDrawdownWindow, DRAWDOWN_HEADERS
from ui.production.production_import_staging_window import ProductionImportStagingWindow
from ui.reports.unified_report_window import UnifiedReportWindow


@pytest.fixture
def root(tk_root):
    yield tk_root
    for child in tk_root.winfo_children():
        child.destroy()


@pytest.fixture
def month_db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()


def test_staging_panes_are_resizable_with_minimum_widths(root):
    host = tk.Toplevel(root)
    w = ProductionImportStagingWindow(host, [])
    w.update()
    paned = w._paned
    assert isinstance(paned, ttk.Panedwindow) and len(paned.panes()) == 2
    left, right = (w.nametowidget(p) for p in paned.panes())
    # 既定の配分は以前の pack 配置と同じ（左ペインは中身の必要幅）
    assert paned.sashpos(0) == left.winfo_reqwidth()

    paned.sashpos(0, 500)
    paned.event_generate("<ButtonRelease-1>", x=500, y=5)
    w.update()
    assert paned.sashpos(0) == 500 and left.winfo_width() == 500

    # 左端・右端まで寄せても、どちらのペインも最小幅で止まる（右はサッシュの太さを差し引いた実際の幅で確かめる）
    paned.sashpos(0, 10)
    paned.event_generate("<B1-Motion>", x=10, y=5)
    w.update()
    assert paned.sashpos(0) == w._LEFT_PANE_MIN_WIDTH_PX == left.winfo_width()
    paned.sashpos(0, paned.winfo_width())
    paned.event_generate("<B1-Motion>", x=paned.winfo_width(), y=5)
    w.update()
    assert right.winfo_width() == w._RIGHT_PANE_MIN_WIDTH_PX


def test_report_has_vertical_scrollbar(root, month_db):
    w = UnifiedReportWindow(root)
    w.update()
    scrollbars = [c for c in w.tree.master.winfo_children() if isinstance(c, ttk.Scrollbar)]
    assert len(scrollbars) == 1 and str(scrollbars[0].cget("orient")) == "vertical"
    assert w.tree.cget("yscrollcommand")
    assert scrollbars[0].winfo_ismapped() and w.tree.winfo_ismapped()


def test_drawdown_columns_and_colors(root, month_db):
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=0)
    for lot_no, board, count in (("L1", "BoardA", 1), ("L2", "BoardB", 2), ("L3", "BoardC", None)):
        create_plan_version(batch, f"K-{lot_no}", {
            "lot_no": lot_no, "setup_file_no": "F1", "board_name": board, "production_side": "1",
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": "2026/10/01 08:00:00", "mounting_line": "A",
        }, created_by="test")
        if count is not None:
            upsert_board_structure(board, count)
        p = find_plan_item_by_kitting_no(f"K-{lot_no}", lot_no)
        insert_daily_production(p["plan_item_id"], f"K-{lot_no}", lot_no, board, "2026-10-02", 100, "W1")

    w = DailyDrawdownWindow(root)
    w.update()
    assert "面" not in DRAWDOWN_HEADERS
    assert [w.tree.heading(c, "text") for c in w.tree["columns"]] == DRAWDOWN_HEADERS

    rows = [w.tree.item(i) for i in w.tree.get_children()]
    by_lot = {r["values"][0]: r for r in rows}
    # L1: 一致、L2: 構成基板数2に対してファイル1つ（不足・引落0なので行が出ない）、L3: マスタ未登録（赤）
    assert set(by_lot) == {"L1", "L3"}
    assert str(by_lot["L1"]["values"][1]) == "1" and by_lot["L3"]["values"][1] == ""
    assert by_lot["L1"]["tags"] == [lot_row_style.LOT_STRIPE_TAG_B]
    assert by_lot["L3"]["tags"] == [lot_row_style.LOT_STRIPE_TAG_A, lot_row_style.STATUS_COLOR_TAG_NEEDS_REVIEW]
    # 色はロット進捗チェックと同じ定義
    assert str(w.tree.tag_configure(lot_row_style.LOT_STRIPE_TAG_B, "background")) == lot_row_style.LOT_STRIPE_COLOR_B
    assert str(w.tree.tag_configure(lot_row_style.STATUS_COLOR_TAG_NEEDS_REVIEW, "foreground")) == lot_row_style.STATUS_TEXT_COLOR_NEEDS_REVIEW


def test_shared_style_reexported_from_old_location():
    from ui.reports import daily_report_window
    assert daily_report_window.configure_lot_stripe_tags is lot_row_style.configure_lot_stripe_tags
    assert daily_report_window.configure_status_color_tags is lot_row_style.configure_status_color_tags
