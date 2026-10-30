"""
生産実績入力の計画一覧の絞り込み「古い計画を隠す」「計画終了日が来たものだけ」（D-192）。
表示だけを変える。計画の is_active・delete_flag・計算は変えない。
"""
import os
import sqlite3
import tkinter as tk
from datetime import date

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import create_plan_batch, create_plan_version, mark_batch_deleted
from models.kitting_plan_batches import fetch_latest_csv_batch_scope
from models.production import insert_daily_production
from services import production_entry_service as svc
from ui.kitting.kitting_production_entry import KittingProductionEntryWindow


# ---------------------------------------------------------------- 日時の解釈・B の判定

@pytest.mark.parametrize("value, expected", [
    ("2026/07/13 17:19:59", "2026/07/13 17:19:59"),
    ("2026-07-13 17:19:59", "2026/07/13 17:19:59"),
    ("2026/7/3 8:05", "2026/07/03 08:05:00"),
    ("2026-07-03T08:05:09", "2026/07/03 08:05:09"),
    ("2026/07/03", "2026/07/03 00:00:00"),
    ("  2026/07/03 08:05:09  ", "2026/07/03 08:05:09"),
    (None, None), ("", None), ("  ", None), ("abc", None), ("2026/13/01 00:00:00", None), ("2026/02/30", None), (20260703, None),
])
def test_normalize_plan_datetime(value, expected):
    assert svc.normalize_plan_datetime(value) == expected


TODAY = date(2026, 10, 30)


@pytest.mark.parametrize("end, expected", [
    ("2026/10/29 08:00:00", True),
    ("2026/10/30 00:00:00", True),
    ("2026/10/30 23:59:59", True),            # 今日の夕方に終わる計画も表示する
    ("2026/10/31 00:00:00", False),
    ("2026-11-05 10:00:00", False),
    ("", True), (None, True), ("日付ではない", True),
])
def test_is_plan_ended(end, expected):
    assert svc.is_plan_ended(end, TODAY) is expected


# ---------------------------------------------------------------- A の判定（純粋関数）

def _scope(keys=(("K1", "L1"),), period=("2026/07/03 00:00:00", "2026/07/13 17:19:59")):
    return {"period": period, "keys": set(keys)}


def _plan(kitting="K9", lot="L9", start="2026/07/05 08:00:00", end="2026/07/06 08:00:00"):
    return {"kitting_list_no": kitting, "lot_no": lot, "plan_start_datetime": start, "plan_end_datetime": end}


def test_stale_plan_inside_period_and_not_in_latest():
    assert svc.is_stale_plan(_plan(), 0, _scope()) is True


@pytest.mark.parametrize("start, end, expected", [
    ("2026/07/03 00:00:00", "2026/07/13 17:19:59", True),     # 端ちょうどは収まる
    ("2026/07/02 23:59:59", "2026/07/06 08:00:00", False),    # 開始が期間より前
    ("2026/07/05 08:00:00", "2026/07/13 17:20:00", False),    # 終了が期間の最大より後（日付は同じで時刻だけ後）
    ("2026/07/05 08:00:00", "2026/07/14 00:00:00", False),
])
def test_period_edges_are_compared_down_to_the_time(start, end, expected):
    assert svc.is_stale_plan(_plan(start=start, end=end), 0, _scope()) is expected


def test_stale_judgement_skips_empty_or_unparseable_dates_keys_in_latest_and_actuals():
    assert svc.is_stale_plan(_plan(end=""), 0, _scope()) is False
    assert svc.is_stale_plan(_plan(start=None), 0, _scope()) is False
    assert svc.is_stale_plan(_plan(end="xx"), 0, _scope()) is False
    assert svc.is_stale_plan(_plan(kitting="K1", lot="L1"), 0, _scope()) is False     # 最新の取り込みにある
    assert svc.is_stale_plan(_plan(), 5, _scope()) is False                          # 実績のある計画は隠さない
    assert svc.is_stale_plan(_plan(), 0, None) is False
    assert svc.is_stale_plan(_plan(lot=None), 0, _scope(keys=[("K9", "")])) is False  # ロットNo. が空は '' で比べる


def _row(end, mark, side="1"):
    return ("K", "L", "s", "f", "b", "1", "1", "0", "1", "0", "1", "", end, mark, side)


def test_filter_rows_counts_only_rows_removed_by_stale():
    rows = [_row("2026/10/01 00:00:00", "1"), _row("2026/10/01 00:00:00", ""), _row("2026/12/01 00:00:00", "1"), _row("", "1")]
    assert svc.filter_plan_rows_by_end_and_stale(rows, False, False, TODAY) == (rows, 0)
    kept, hidden = svc.filter_plan_rows_by_end_and_stale(rows, True, False, TODAY)
    assert len(kept) == 1 and hidden == 3
    kept, hidden = svc.filter_plan_rows_by_end_and_stale(rows, False, True, TODAY)       # B だけ：未来の1件が消える
    assert len(kept) == 3 and hidden == 0
    kept, hidden = svc.filter_plan_rows_by_end_and_stale(rows, True, True, TODAY)        # AND：B で消えた行は A の件数に入らない
    assert len(kept) == 1 and hidden == 2


# ---------------------------------------------------------------- 最新の取り込み（models）

@pytest.fixture
def db(monkeypatch):
    path = os.path.join(config.APP_DATA_DIR, "db", "stale_month", "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    init_database_at(path)
    return path


def set_imported_at(batch_id, imported_at):
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute("UPDATE kitting_plan_batches SET imported_at = ? WHERE plan_batch_id = ?", (imported_at, batch_id))


def add_batch(rows, source="plan.csv", imported_at=None):
    """rows: [(キッティングNo., ロットNo., 開始, 終了, 追加の列)]。"""
    batch = create_plan_batch(source, "T", len(rows))
    for kitting, lot, start, end, extra in rows:
        create_plan_version(batch, kitting, {
            "lot_no": lot, "setup_file_no": "0001", "mounting_line": "L", "board_name": "B", "production_side": "1",
            "planned_qty": 10, "order_qty": 10, "plan_start_datetime": start, "plan_end_datetime": end, **extra,
        }, created_by="T")
    if imported_at:
        set_imported_at(batch, imported_at)
    return batch


def test_no_batches_gives_none(db):
    assert fetch_latest_csv_batch_scope() is None
    assert svc.load_stale_plan_scope() is None


def test_latest_batch_rules(db):
    first = add_batch([("K1", "L1", "2026/07/01 00:00:00", "2026/07/02 00:00:00", {})], imported_at="2026-10-01 09:00:00")
    second = add_batch([("K2", "L2", "2026/07/03 00:00:00", "2026/07/04 00:00:00", {})], imported_at="2026-10-02 09:00:00")
    assert fetch_latest_csv_batch_scope()["plan_batch_id"] == second
    # 前月引き継ぎは最新に数えない
    add_batch([("K3", "L3", "2026/07/05 00:00:00", "2026/07/06 00:00:00", {})], source="carry_over:202609", imported_at="2026-10-03 09:00:00")
    assert fetch_latest_csv_batch_scope()["plan_batch_id"] == second
    # 削除したバッチも数えない。復元すれば戻る
    mark_batch_deleted(second, True)
    assert fetch_latest_csv_batch_scope()["plan_batch_id"] == first
    mark_batch_deleted(second, False)
    assert fetch_latest_csv_batch_scope()["plan_batch_id"] == second
    # 同じ時刻なら plan_batch_id が大きい方
    third = add_batch([("K4", "L4", "2026/07/07 00:00:00", "2026/07/08 00:00:00", {})], imported_at="2026-10-02 09:00:00")
    assert fetch_latest_csv_batch_scope()["plan_batch_id"] == third


def test_period_uses_every_row_of_the_batch_including_delete_flag_1(db):
    add_batch([
        ("K1", "L1", "2026/07/05 08:00:00", "2026/07/06 08:00:00", {}),
        ("K2", "L2", "2026/07/01 08:00:00", "2026/07/09 20:00:00", {"delete_flag": 1}),     # 明細の「削除」の列が1の行も期間に入る
    ])
    scope = svc.load_stale_plan_scope()
    assert scope["period"] == ("2026/07/01 08:00:00", "2026/07/09 20:00:00")
    assert scope["keys"] == {("K1", "L1"), ("K2", "L2")}


def test_period_is_computed_after_normalizing_mixed_formats(db):
    add_batch([
        ("K1", "L1", "2026-07-02 08:00:00", "2026/07/06 08:00:00", {}),
        ("K2", "L2", "2026/7/1 08:00", "2026/07/05 08:00:00", {}),
        ("K3", "L3", "解釈できない", "", {}),
    ])
    assert svc.load_stale_plan_scope()["period"] == ("2026/07/01 08:00:00", "2026/07/06 08:00:00")


# ---------------------------------------------------------------- 出し直し全体（A で古い計画だけが隠れる）

FIRST = [
    ("A1", "L1", "2026/07/03 08:00:00", "2026/07/04 08:00:00", {}),
    ("A2", "L2", "2026/07/05 08:00:00", "2026/07/06 08:00:00", {}),
    ("A3", "L3", "2026/07/07 08:00:00", "2026/07/08 08:00:00", {}),
    ("A4", "L4", "2026/07/08 08:00:00", "2026/07/09 08:00:00", {}),
    ("EARLY", "L5", "2026/07/01 08:00:00", "2026/07/02 08:00:00", {}),      # 再取り込みの期間より前に始まる
    ("LATE", "L6", "2026/07/10 08:00:00", "2026/07/13 17:19:59", {}),       # 再取り込みの期間の最大より後に終わる
    ("NOEND", "L7", "2026/07/06 08:00:00", ""),
]


def stale_keys():
    scope = svc.load_stale_plan_scope()
    from models.kitting_plan import list_active_plan_items
    return {(p["kitting_list_no"], p["lot_no"]) for p in list_active_plan_items(include_completed=True)
            if svc.is_stale_plan(p, p["app_cumulative_qty"], scope)}


def seed_reissue():
    first_rows = [r if len(r) == 5 else (*r, {}) for r in FIRST]
    add_batch(first_rows, imported_at="2026-10-01 09:00:00")
    # 出し直し：A2・A3 のキッティングNo.の日付を変える。A1・A4 はそのまま。EARLY・LATE・NOEND は載せない。
    second = [
        ("A1", "L1", "2026/07/03 08:00:00", "2026/07/04 08:00:00", {}),
        ("A2-NEW", "L2", "2026/07/05 08:00:00", "2026/07/06 08:00:00", {}),
        ("A3-NEW", "L3", "2026/07/07 08:00:00", "2026/07/08 08:00:00", {}),
        ("A4", "L4", "2026/07/08 08:00:00", "2026/07/09 08:00:00", {}),
    ]
    return add_batch(second, imported_at="2026-10-02 09:00:00")


def test_first_import_only_hides_nothing(db):
    add_batch([r if len(r) == 5 else (*r, {}) for r in FIRST])
    assert stale_keys() == set()


def test_reissue_hides_only_the_old_plans(db):
    seed_reissue()
    assert stale_keys() == {("A2", "L2"), ("A3", "L3")}      # EARLY（開始が期間より前）・LATE（終了が最大より後）・NOEND（終了日が空）は隠れない


def test_old_plan_with_actuals_is_not_hidden(db):
    seed_reissue()
    from models.kitting_plan import find_plan_item_by_kitting_no
    plan = find_plan_item_by_kitting_no("A2", "L2")
    insert_daily_production(plan["plan_item_id"], "A2", "L2", "B", "2026-10-02", 3, "W1")
    assert stale_keys() == {("A3", "L3")}


def test_deleting_the_newest_batch_uses_the_previous_one_and_restore_comes_back(db):
    seed_reissue()
    newest = fetch_latest_csv_batch_scope()["plan_batch_id"]
    mark_batch_deleted(newest, True)
    assert stale_keys() == set()                              # 1つ前の取り込みが最新になり、そこに無い有効な計画は無い
    mark_batch_deleted(newest, False)
    assert stale_keys() == {("A2", "L2"), ("A3", "L3")}


def test_reimporting_the_same_content_keeps_new_versions_visible(db):
    seed_reissue()
    again = [
        ("A1", "L1", "2026/07/03 08:00:00", "2026/07/04 08:00:00", {}),
        ("A2-NEW", "L2", "2026/07/05 08:00:00", "2026/07/06 08:00:00", {}),
        ("A3-NEW", "L3", "2026/07/07 08:00:00", "2026/07/08 08:00:00", {}),
        ("A4", "L4", "2026/07/08 08:00:00", "2026/07/09 08:00:00", {}),
    ]
    add_batch(again, imported_at="2026-10-03 09:00:00")
    assert stale_keys() == {("A2", "L2"), ("A3", "L3")}      # 新しい版が最新の取り込みに入るので、隠れない


def test_filters_do_not_change_plans(db):
    seed_reissue()
    with sqlite3.connect(config.DB_PATH) as con:
        before = con.execute("SELECT plan_item_id, is_active, delete_flag, updated_at FROM kitting_plan_items ORDER BY 1").fetchall()
    KittingProductionEntryWindow._fetch_plan_list_rows()
    svc.load_stale_plan_scope()
    with sqlite3.connect(config.DB_PATH) as con:
        assert con.execute("SELECT plan_item_id, is_active, delete_flag, updated_at FROM kitting_plan_items ORDER BY 1").fetchall() == before


# ---------------------------------------------------------------- 一覧の行の形と画面

def test_list_rows_keep_production_side_last_and_carry_end_and_mark(db):
    seed_reissue()
    rows = {row[0]: row for row in KittingProductionEntryWindow._fetch_plan_list_rows()}
    assert len(rows["A2"]) == 15
    assert rows["A2"][-1] == "1"                                           # production_side
    assert rows["A2"][-2] == "1" and rows["A2"][-3] == "2026/07/06 08:00:00"
    assert rows["A1"][-2] == "" and rows["NOEND"][-3] == "" and rows["NOEND"][-2] == ""
    assert svc.is_plan_row_completed(rows["A1"], {"file_actuals": {("0001", "1"): 10}}, 3, 6) is True   # 既存の判定が壊れていない


@pytest.fixture
def window(db, tk_root):
    seed_reissue()
    host = tk.Toplevel(tk_root)
    entry = KittingProductionEntryWindow(host, {"worker_id": "A1", "name": "管理者"}, preloaded_plan_rows=KittingProductionEntryWindow._fetch_plan_list_rows())
    entry.update()
    yield entry
    host.destroy()


def shown(entry):
    return {entry.tree_plan_list.item(i, "values")[0] for i in entry.tree_plan_list.get_children()}


def test_window_opens_with_both_filters_off(window):
    assert window._hide_stale_var.get() is False and window._only_ended_var.get() is False
    assert {"A2", "A3", "A2-NEW", "EARLY"} <= shown(window)
    assert window._stale_hidden_label.cget("text") == ""


def test_hide_stale_filter_shows_the_count_and_ands_with_other_filters(window):
    window._hide_stale_var.set(True)
    window.apply_plan_filters()
    assert "A2" not in shown(window) and "A3" not in shown(window) and "A1" in shown(window)
    assert window._stale_hidden_label.cget("text") == "古い計画 2件を隠しています"
    window._plan_filter_vars["list_no"].set("A2")                          # 文字の絞り込みと AND：A2 だけが A の対象
    window.apply_plan_filters()
    assert shown(window) == {"A2-NEW"}
    assert window._stale_hidden_label.cget("text") == "古い計画 1件を隠しています"
    window.clear_plan_filters()
    assert window._hide_stale_var.get() is False and window._stale_hidden_label.cget("text") == ""
    assert {"A2", "A3"} <= shown(window)


def test_only_ended_filter_uses_today(window):
    window._only_ended_var.set(True)
    window.apply_plan_filters()
    assert shown(window) >= {"A1", "NOEND"}                                # 2026年7月の計画は終了日が来ている。終了日が空の計画も表示
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute("UPDATE kitting_plan_items SET plan_end_datetime = '2999/01/01 00:00:00' WHERE kitting_list_no = 'A1'")
    window.load_plan_list()
    assert window._only_ended_var.get() is False and "A1" in shown(window)    # 更新でオフに戻る
    window._only_ended_var.set(True)
    window.apply_plan_filters()
    assert "A1" not in shown(window) and "A4" in shown(window)


def test_partial_refresh_keeps_the_stale_mark_and_drops_it_when_actuals_arrive(window):
    row = next(r for r in window._all_plan_rows if r[0] == "A2")
    assert row[-2] == "1"
    window._refresh_plan_list_rows_of_lot("L2")
    assert next(r for r in window._all_plan_rows if r[0] == "A2")[-2] == "1"          # 印を引き継ぐ
    from models.kitting_plan import find_plan_item_by_kitting_no
    plan = find_plan_item_by_kitting_no("A2", "L2")
    insert_daily_production(plan["plan_item_id"], "A2", "L2", "B", "2026-10-02", 3, "W1")
    window._refresh_plan_list_rows_of_lot("L2")
    refreshed = next(r for r in window._all_plan_rows if r[0] == "A2")
    assert refreshed[-2] == "" and refreshed[-1] == "1"
    window._hide_stale_var.set(True)
    window.apply_plan_filters()
    assert "A2" in shown(window) and window._stale_hidden_label.cget("text") == "古い計画 1件を隠しています"
