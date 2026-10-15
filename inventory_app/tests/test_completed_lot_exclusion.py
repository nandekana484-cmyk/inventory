"""
完成済みのロットの計画を、自動登録（Shift+S・Shift+Q）の対象から外す（D-130）。
完成の判定は未完了ロットの抽出と同じ（is_lot_incomplete()）。発注数が空欄になるロット（同一ファイル内の不一致）は完成済みにしない。
"""
import os

import pytest

import config
from db.init_db import init_database_at
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production
from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row
from models.production_side_master import upsert_production_side
from services.lot_status_service import lot_completion_state, preview_completed_lot_registration, list_incomplete_lots
from services.production_service import search_plan_by_kitting_no
from services.staging_eligibility import _load_staged_rows_from_db


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=0)

    def plan(k, lot, file_no, planned, order, start):
        create_plan_version(batch, k, {
            "lot_no": lot, "setup_file_no": file_no, "board_name": f"B-{lot}", "production_side": "1",
            "planned_qty": planned, "order_qty": order, "plan_start_datetime": start, "mounting_line": "A",
        }, created_by="test")
        upsert_board_structure(f"B-{lot}", 1)
        upsert_production_side(file_no, "A", "1")

    def actual(k, lot, qty):
        p = find_plan_item_by_kitting_no(k, lot)
        insert_daily_production(p["plan_item_id"], k, lot, f"B-{lot}", "2026-10-02", qty, "W1")

    # L1: P1 に 1000 で完成、P2 は未登録。L2: P1 に 800（未完成）、P2 は未登録。
    for lot, qty in (("L1", 1000), ("L2", 800)):
        plan(f"P1{lot}", lot, f"F{lot}", 1000, 1000, "2026/10/01 08:00:00")
        plan(f"P2{lot}", lot, f"F{lot}", 200, 1000, "2026/10/10 08:00:00")
        actual(f"P1{lot}", lot, qty)
    # L3: 同じファイルNo・面の計画で発注数が食い違う（発注数が空欄になる）。実績は多いが完成済みにしない
    plan("P1L3", "L3", "FL3", 100, 100, "2026/10/01 08:00:00")
    plan("P2L3", "L3", "FL3", 100, 120, "2026/10/10 08:00:00")
    actual("P1L3", "L3", 500)

    csv_batch = create_csv_import_batch("actual.csv")
    for lot in ("L1", "L2"):
        upsert_pending_csv_import_row({"csv_row_no": 2, "lot_no": lot, "product_name": f"B-{lot}", "daily_qty": 200,
                                       "report_date": "2026/10/10", "worker_id": "W1"}, import_batch_id=csv_batch)


def test_completion_state_uses_incomplete_lot_rule(db):
    assert lot_completion_state("L1")["completed"] is True
    assert lot_completion_state("L2")["completed"] is False
    assert lot_completion_state("L3")["completed"] is False
    assert lot_completion_state("無いロット") is None
    incomplete = {r["lot_no"] for r in list_incomplete_lots()}
    assert "L1" not in incomplete and "L2" in incomplete


def test_staging_status_is_lot_completed(db):
    status = {r["lot_no"]: r["status"] for r in _load_staged_rows_from_db()}
    assert status["L1"] == "lot_completed"
    assert status["L2"] == "multiple_candidates"  # 未完成のロットは今までどおり（P1・P2 の両方が候補）


def test_registration_preview_shows_new_total(db):
    info = preview_completed_lot_registration(search_plan_by_kitting_no("P2L1", "L1")[0], 200)
    assert info == {"lot_no": "L1", "lot_completed": 1000, "order_quantity": 1000, "new_total": 1200}
    assert preview_completed_lot_registration(search_plan_by_kitting_no("P2L2", "L2")[0], 200) is None


def test_correction_shows_only_when_total_exceeds_order_and_increases(db):
    # L1 の P1 は 1000 で完成済み。P1 自体を直す場合（既存の実績あり）は、合計が発注数を超えて増えるときだけ（D-133）
    p1 = search_plan_by_kitting_no("P1L1", "L1")[0]
    assert preview_completed_lot_registration(p1, 900) is None      # 減る
    assert preview_completed_lot_registration(p1, 1000) is None     # 変わらない
    info = preview_completed_lot_registration(p1, 1100)             # 発注数を超えて増える
    assert info["new_total"] == 1100


def test_candidate_of_completed_lot_is_not_green(db, tk_root):
    # 候補一覧で、完成済みロットの計画を「自動確定可能」の緑にしない（D-132）。未完成のロットは今までどおり
    import tkinter as tk
    from ui.production.production_import_staging_window import ProductionImportStagingWindow
    host = tk.Toplevel(tk_root)
    try:
        rows = _load_staged_rows_from_db()
        w = ProductionImportStagingWindow(host, rows)
        tags = {}
        for row in rows:
            w._populate_candidates(row)
            tags[row["lot_no"]] = {w._candidates_by_iid[c]["kitting_list_no"]: w.tree_candidates.item(c, "tags")
                                   for c in w.tree_candidates.get_children()}
        assert "auto_confirmable" not in tags["L1"]["P2L1"]
        assert "auto_confirmable" in tags["L2"]["P2L2"]
    finally:
        host.destroy()
