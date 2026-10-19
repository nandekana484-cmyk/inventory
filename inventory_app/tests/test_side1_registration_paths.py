"""
面2がある面1・「面2待ち」の面1を、登録の対象から外す（D-170）。合成データで、登録に至る経路を1つずつ確かめる。
画面は表示しない（実績修正の画面だけは Tk のウィンドウを直接作り、クリックはしない）。偽の画面は test_staging_registration.py のもの。
"""
import os
import types
from datetime import datetime

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production
from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row
from models.production_side_master import upsert_production_side
from services.lot_status_service import preview_completed_lot_registration
from services.production_service import (
    get_daily_history, register_daily_result, overwrite_daily_result, Side1RegistrationRefused, SIDE2_WAIT_MESSAGE,
)
from services.staging_eligibility import _load_staged_rows_from_db
from ui.kitting.kitting_production_entry import KittingProductionEntryWindow as KPE
from tests.test_staging_registration import (  # noqa: F401 （fixture）
    _FakeParent, _FakeStaging, _right_click, _select, _select_row, _candidate, _messages, events,
)

CSV_DATE = "2026/7/23"
# (kitting_list_no, lot_no, setup_file_no, 面, 基板名)
PLANS = [
    ("K1", "L1", "0101", "1", "ProdA"), ("K2", "L1", "0101", "2", "ProdA"),       # 両面
    ("K6", "L6", "0601", "1", "ProdW"),                                           # 面2待ち（マスターに面2あり）
    ("K7", "L7", "0701", "1", "ProdS"),                                           # 片面の製品（マスターに面2なし）
    ("K8", "L8", "0801", "1", "ProdM"), ("K8a", "L8", "0801", "2", "ProdM"), ("K8b", "L8", "0801", "2", "ProdM"),
]
PENDING = [("L1", "ProdA", 100), ("L6", "ProdW", 100), ("L7", "ProdS", 100)]


def _today():
    return datetime.now().strftime("%Y-%m-%d")


@pytest.fixture
def side_db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    create_csv_import_batch("actual.csv", imported_by="test")
    batch_id = create_plan_batch("plan.csv", imported_by="test", row_count=len(PLANS))
    for kitting_list_no, lot_no, file_no, side, board in PLANS:
        create_plan_version(batch_id, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board, "production_side": side,
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": "2026/07/22 08:00:00", "mounting_line": "A",
        }, created_by="test")
    upsert_production_side("0601", "A", "1")
    upsert_production_side("0601", "B", "2")
    upsert_production_side("0701", "A", "1")
    for i, (lot_no, product_name, qty) in enumerate(PENDING, start=1):
        upsert_pending_csv_import_row({
            "csv_row_no": i, "lot_no": lot_no, "product_name": product_name,
            "daily_qty": qty, "report_date": CSV_DATE, "worker_id": "W1",
        })


@pytest.fixture
def screens(side_db, events):
    parent = _FakeParent(events)
    staging = _FakeStaging(parent, events, _load_staged_rows_from_db())
    parent._csv_staging_window = staging
    return parent, staging


def _history(kitting_list_no, lot_no):
    return [(r["report_date"], r["daily_qty"]) for r in get_daily_history(kitting_list_no, lot_no)]


# ---------------------------------------------------------------- 一覧・候補

def test_plan_list_hides_side1_with_side2(side_db):
    kitting_nos = {row[0] for row in KPE._fetch_plan_list_rows()}
    assert kitting_nos == {"K2", "K6", "K7", "K8a", "K8b"}


def test_staging_candidates_and_statuses(screens):
    _, staging = screens
    rows = {row["lot_no"]: row for row in staging._row_by_iid.values()}
    assert [c["kitting_list_no"] for c in rows["L1"]["candidates"]] == ["K2"]
    assert rows["L6"]["status"] == "side2_wait"
    assert rows["L7"]["status"] == "eligible_shift_s"


# ---------------------------------------------------------------- 計画の検索（search_plan）

def test_search_plan_switches_side1_to_side2(screens, events):
    parent, _ = screens
    parent.search_plan("K1", lot_no="L1")
    assert parent.current_plan["kitting_list_no"] == "K2"
    (title, message), = _messages(events, "showinfo")
    assert title == "面2の計画に切り替えました" and "面2の計画 K2 を表示します" in message


def test_search_plan_with_two_side2_asks_to_choose(screens, events):
    parent, _ = screens
    parent.search_plan("K8", lot_no="L8")
    assert parent.current_plan is None
    (title, message), = _messages(events, "showwarning")
    assert "面2の計画が複数あります（K8a、K8b）" in message


# ---------------------------------------------------------------- 生産実績入力の登録

def _register_in_parent(parent, kitting_list_no, lot_no, qty="100"):
    parent.search_plan(kitting_list_no, lot_no=lot_no)
    parent.entry_daily_qty.delete(0)
    parent.entry_daily_qty.insert(0, qty)
    parent._start_registration()


def test_side2_wait_is_refused_with_guidance(screens, events):
    parent, _ = screens
    _register_in_parent(parent, "K6", "L6")
    assert _history("K6", "L6") == []
    (title, message), = _messages(events, "showwarning")
    assert message == SIDE2_WAIT_MESSAGE + "。"
    assert parent.confirm_dialog_previews == []


def test_single_side_product_registers_on_side1(screens, events):
    parent, _ = screens
    _register_in_parent(parent, "K7", "L7")
    assert _history("K7", "L7") == [(_today(), 100.0)]


def test_parent_registers_side2_after_switch(screens, events):
    parent, _ = screens
    _register_in_parent(parent, "K1", "L1")
    assert _history("K2", "L1") == [(_today(), 100.0)] and _history("K1", "L1") == []


# ---------------------------------------------------------------- 登録待ち一覧（右クリック・ダブルクリック・Shift+S）

def test_right_click_side2_wait_is_refused(screens, events):
    _, staging = screens
    _right_click(staging, "L6")
    assert _history("K6", "L6") == []
    assert (_messages(events, "showwarning")[0][1]) == SIDE2_WAIT_MESSAGE + "。"


def test_double_click_side2_wait_is_not_transferred(screens, events):
    parent, staging = screens
    _select_row(staging, "L6")
    staging._confirm_candidate(_candidate(staging, "L6"))
    assert parent.current_plan is None
    assert _messages(events, "showwarning")[0][1] == SIDE2_WAIT_MESSAGE + "。"


def test_shift_s_excludes_side2_wait_and_registers_others(screens, events):
    _, staging = screens
    _select(staging, "L1", "L6", "L7")
    staging._on_bulk_register()
    assert _history("K2", "L1") == [("2026-07-23", 100.0)] and _history("K1", "L1") == []
    assert _history("K7", "L7") == [("2026-07-23", 100.0)]
    assert _history("K6", "L6") == []
    title, message = _messages(events, "showinfo")[-1]
    assert "・面2待ち：1件" in message


# ---------------------------------------------------------------- サービスの入口（二重の安全策）

def test_service_refuses_side1_with_side2_and_side2_wait(side_db):
    with pytest.raises(Side1RegistrationRefused, match="面2の計画（K2）があるため"):
        register_daily_result("K1", "L1", 10, "W1")
    with pytest.raises(Side1RegistrationRefused, match="面2の計画（K2）があるため"):
        overwrite_daily_result("K1", "L1", 10, "W1")
    with pytest.raises(Side1RegistrationRefused, match="面2の計画を取り込んでから"):
        register_daily_result("K6", "L6", 10, "W1")
    assert register_daily_result("K7", "L7", 10, "W1") == 10
    assert _history("K1", "L1") == [] and _history("K6", "L6") == []


# ---------------------------------------------------------------- 本日の実績履歴

class _HistoryTree:
    def __init__(self):
        self.rows = {}

    def get_children(self):
        return tuple(self.rows)

    def delete(self, iid):
        self.rows.pop(iid)

    def insert(self, parent, index, values):
        iid = f"H{len(self.rows)}"
        self.rows[iid] = values
        return iid


def test_today_log_hides_old_side1_copy(side_db):
    """古い版で入った面1の写しが今日の日付にあり、面2の実績が今日に無くても、面1の行は出さない（以前は面2が今日にあるときだけ隠した）。"""
    p1, p7 = find_plan_item_by_kitting_no("K1", "L1"), find_plan_item_by_kitting_no("K7", "L7")
    insert_daily_production(p1["plan_item_id"], "K1", "L1", "ProdA", _today(), 30, "W0")
    insert_daily_production(p7["plan_item_id"], "K7", "L7", "ProdS", _today(), 40, "W0")
    window = types.SimpleNamespace(tree=_HistoryTree())
    KPE.load_today_log(window)
    assert [values[0] for values in window.tree.rows.values()] == ["K7"]


# ---------------------------------------------------------------- 実績修正の画面

def test_correction_window_is_read_only_for_hidden_side1(tk_root, side_db):
    from ui.kitting.actual_correction_window import ActualCorrectionWindow
    p1, p2 = find_plan_item_by_kitting_no("K1", "L1"), find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(p1["plan_item_id"], "K1", "L1", "ProdA", "2026-07-20", 30, "W0")
    insert_daily_production(p2["plan_item_id"], "K2", "L1", "ProdA", "2026-07-20", 30, "W0")
    for kitting_list_no, read_only in (("K1", True), ("K2", False)):
        w = ActualCorrectionWindow(tk_root, kitting_list_no, "L1")
        w.geometry("+3000+3000")
        w.update()
        w.tree.selection_set(w.tree.get_children()[0])
        w.on_select_history(None)
        assert w.read_only is read_only
        states = {str(b.cget("state")) for b in (w.btn_update, w.btn_delete, w.btn_cancel_registration)}
        assert states == ({"disabled"} if read_only else {"normal"})
        w.destroy()


# ---------------------------------------------------------------- 完成済みロットの確認（D-130・D-133・D-156）

def test_completed_lot_check_works_on_side2(side_db):
    p2 = find_plan_item_by_kitting_no("K2", "L1")
    assert preview_completed_lot_registration(p2, 100) is None   # まだ実績0
    register_daily_result("K2", "L1", 100, "W1")
    # 面2の 100 で完成済み（面1の実績が無くても完成数に効く）。直して増やすときだけ確認する
    assert preview_completed_lot_registration(p2, 120) == {
        "lot_no": "L1", "lot_completed": 100, "order_quantity": 100, "new_total": 120,
    }
    assert preview_completed_lot_registration(p2, 90) is None
