"""
ui/kitting/kitting_production_entry.py の _perform_registration() が DB に書き込む結果と、画面側の動き（呼び出し順・メッセージ）を固定する（段階3a）。
画面は表示しない。self の代わりに、_perform_registration() が使う属性・メソッドだけを持つ偽の画面を渡す。
失敗はトリガー（RAISE(ABORT)）で起こす。関数の置き場所を問わずに効くようにするため（モジュール属性の差し替えは使わない）。
"""
import os
import sqlite3
import tkinter.messagebox
from datetime import datetime

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.ng_declarations import init_ng_declarations_table, save_ng_declaration, get_ng_declaration
from models.operation_log import list_operation_log
from models.production import insert_daily_production
from services.production_service import get_daily_history
from ui.kitting.kitting_production_entry import KittingProductionEntryWindow as KPE

PLAN_START = "2026/07/22 08:00:00"


# ---------------------------------------------------------------- 準備

@pytest.fixture
def monthly_db(monkeypatch):
    """
    一時フォルダに月次DBを作り、両面の計画（K1=面1・K2=面2、L1・F1）と片面の計画（K3=面2、L2・F3）を入れる。
    DB_PATH は db/<フォルダ>/inventory.db にする。db/inventory.db（既定DB）だと log_operation() が何も書かない（D-71）。
    """
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    init_ng_declarations_table()
    batch_id = create_plan_batch("plan.csv", imported_by="test", row_count=3)
    for kitting_list_no, lot_no, file_no, side in (("K1", "L1", "F1", "1"), ("K2", "L1", "F1", "2"), ("K3", "L2", "F3", "2")):
        create_plan_version(batch_id, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": "BoardA", "production_side": side,
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": PLAN_START,
        }, created_by="test")


def _plan(kitting_list_no, lot_no):
    return find_plan_item_by_kitting_no(kitting_list_no, lot_no)


def _add_abort_trigger(name, table, when):
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN {when} BEGIN SELECT RAISE(ABORT, 'test failure'); END")


class _Recorder:
    """どのメソッドを呼んでも、events に (名前, メソッド名, 引数) を記録するだけの部品（ラベル・記入欄の代わり）。"""
    def __init__(self, events, name):
        self._events = events
        self._name = name

    def __getattr__(self, method):
        return lambda *args, **kwargs: self._events.append((self._name, method, args, kwargs))


class _FakeStagingWindow:
    def __init__(self, events):
        self._events = events
        self.tree = _Recorder(events, "staging.tree")

    def winfo_exists(self):
        return True

    def state(self):
        return "normal"

    def lift(self):
        self._events.append(("staging", "lift"))

    def focus_force(self):
        self._events.append(("staging", "focus_force"))


class _FakeEntryWindow:
    """_perform_registration() が self から使うものだけを持つ、生産実績入力画面の代わり。"""
    def __init__(self, plan, ng_side_plans, events, pending_report_date=None, removal=None, staging_window=None):
        self.current_worker = {"worker_id": "W1", "name": "作業者A"}
        self.current_plan = plan
        self._ng_side_plans = ng_side_plans
        self._pending_csv_report_date = pending_report_date
        self._pending_csv_row_removal = removal
        self._csv_staging_window = staging_window
        self._events = events
        self.lbl_app_cum = _Recorder(events, "lbl_app_cum")
        self.entry_daily_qty = _Recorder(events, "entry_daily_qty")

    def load_today_log(self):
        self._events.append(("load_today_log",))

    def _load_current_daily_qty(self, kitting_no, lot_no):
        self._events.append(("_load_current_daily_qty", kitting_no, lot_no))

    def _setup_ng_side_ui(self, plan):
        self._events.append(("_setup_ng_side_ui", plan["kitting_list_no"]))

    def _refresh_plan_list_for_lot(self, lot_no):
        self._events.append(("_refresh_plan_list_for_lot", lot_no))

    def winfo_toplevel(self):
        return self


@pytest.fixture
def events(monkeypatch):
    """呼び出し順の記録。messagebox は表示せずに記録する。"""
    recorded = []
    for name in ("showinfo", "showerror", "showwarning", "askyesno"):
        monkeypatch.setattr(
            tkinter.messagebox, name,
            lambda title, message, _name=name, **kw: recorded.append(("messagebox", _name, title, message)),
        )
    return recorded


def _both_sides_window(events, **kwargs):
    """両面の計画の面2（K2）を選んだ画面。面2がある面1（K1）は登録の対象にしない（D-167）。NG の欄は面1・面2の両方。"""
    p1, p2 = _plan("K1", "L1"), _plan("K2", "L1")
    return _FakeEntryWindow(p2, {"1": p1, "2": p2}, events, **kwargs)


def _register(window, daily_qty, save_qty_by_side=None, existing_daily_qty=None, record_history=True):
    preview = {"existing_daily_qty": existing_daily_qty, "save_qty_by_side": save_qty_by_side or {}}
    KPE._perform_registration(window, daily_qty, preview, record_history=record_history)


def _history(kitting_list_no, lot_no):
    return [(r["report_date"], r["daily_qty"], r["worker_id"]) for r in get_daily_history(kitting_list_no, lot_no)]


def _ng(kitting_list_no, side, lot_no="L1"):
    declaration = get_ng_declaration(kitting_list_no, side, lot_no=lot_no)
    return None if declaration is None else (declaration["ng_qty"], declaration["report_date"])


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _messages(events, name):
    return [e for e in events if e[0] == "messagebox" and e[1] == name]


# ---------------------------------------------------------------- 実績と NG の日付

def test_actual_uses_csv_date_and_ng_uses_today(monthly_db, events):
    window = _both_sides_window(events, pending_report_date="2026/3/18")
    _register(window, 90, {"1": 4.0})
    assert _history("K2", "L1") == [("2026-03-18", 90.0, "W1")]  # CSV の払い出し日を正規化した日付
    assert _ng("K1", 1) == (4.0, _today())                         # NG は登録した当日
    assert window._pending_csv_report_date is None                 # 使った CSV の日付はクリアする


def test_manual_entry_uses_today(monthly_db, events):
    _register(_both_sides_window(events), 90)
    assert _history("K2", "L1") == [(_today(), 90.0, "W1")]


def test_unparseable_csv_date_falls_back_to_today(monthly_db, events):
    _register(_both_sides_window(events, pending_report_date="2026.03.18"), 90)
    assert _history("K2", "L1") == [(_today(), 90.0, "W1")]


def test_overwrite_replaces_existing_and_keeps_its_date_when_manual(monthly_db, events):
    """既存の実績があれば上書き（1計画＝1レコード）。手入力（日付なし）なら既存の日付を引き継ぐ。"""
    p2 = _plan("K2", "L1")
    insert_daily_production(p2["plan_item_id"], "K2", "L1", "BoardA", "2026-07-20", 50, "W0")
    _register(_both_sides_window(events), 90, existing_daily_qty=50)
    assert _history("K2", "L1") == [("2026-07-20", 90.0, "W1")]


def test_overwrite_with_csv_date(monthly_db, events):
    p2 = _plan("K2", "L1")
    insert_daily_production(p2["plan_item_id"], "K2", "L1", "BoardA", "2026-07-20", 50, "W0")
    _register(_both_sides_window(events, pending_report_date="2026/7/23"), 90, existing_daily_qty=50)
    assert _history("K2", "L1") == [("2026-07-23", 90.0, "W1")]


# ---------------------------------------------------------------- 面1には書かない（D-169。面1への自動入力は廃止）

def test_side2_registration_writes_nothing_to_side1(monthly_db, events):
    _register(_both_sides_window(events, pending_report_date="2026/3/18"), 90)
    assert _history("K2", "L1") == [("2026-03-18", 90.0, "W1")]
    assert _history("K1", "L1") == []
    assert "反対側" not in _messages(events, "showinfo")[0][3]


def test_side2_registration_keeps_old_side1_record_untouched(monthly_db, events):
    """古い版で面1に入った写しがあっても、面2の登録では書き換えない（計算には使わない）。"""
    p1 = _plan("K1", "L1")
    insert_daily_production(p1["plan_item_id"], "K1", "L1", "BoardA", "2026-07-20", 30, "W0")
    _register(_both_sides_window(events, pending_report_date="2026/7/23"), 90)
    assert _history("K1", "L1") == [("2026-07-20", 30.0, "W0")]


def test_single_side_plan_registration(monthly_db, events):
    p3 = _plan("K3", "L2")
    window = _FakeEntryWindow(p3, {"2": p3, "1": None}, events)
    _register(window, 90)
    assert _history("K3", "L2") == [(_today(), 90.0, "W1")]
    message = _messages(events, "showinfo")[0][3]
    assert "反対側" not in message


# ---------------------------------------------------------------- NG の保存値

def test_ng_save_values_side1_is_side1_plus_side2(monthly_db, events):
    window = _both_sides_window(events)
    save_qty = KPE._compute_ng_save_qty(window, {"1": 3.0, "2": 5.0})
    _register(window, 90, save_qty)
    assert _ng("K1", 1) == (8.0, _today())
    assert _ng("K2", 2) == (5.0, _today())


def test_ng_side1_empty_gets_side2_value(monthly_db, events):
    window = _both_sides_window(events)
    _register(window, 90, KPE._compute_ng_save_qty(window, {"1": 0.0, "2": 5.0}))
    assert _ng("K1", 1) == (5.0, _today())
    assert _ng("K2", 2) == (5.0, _today())


def test_ng_zero_total_is_not_saved_and_existing_is_kept(monthly_db, events):
    save_ng_declaration("K1", "F1", 1, 7.0, "2026-07-20", lot_no="L1")
    window = _both_sides_window(events)
    _register(window, 90, KPE._compute_ng_save_qty(window, {"1": 0.0, "2": 0.0}))
    assert _ng("K1", 1) == (7.0, "2026-07-20")  # 合計0の面は保存しない（既存の申告に触れない）
    assert _ng("K2", 2) is None


def test_ng_replaces_previous_value_without_adding(monthly_db, events):
    save_ng_declaration("K1", "F1", 1, 7.0, "2026-07-20", lot_no="L1")
    window = _both_sides_window(events)
    _register(window, 90, KPE._compute_ng_save_qty(window, {"1": 2.0, "2": 0.0}))
    assert _ng("K1", 1) == (2.0, _today())


def test_ng_completion_message_lists_declared_sides(monthly_db, events):
    window = _both_sides_window(events)
    _register(window, 90, {"1": 8.0, "2": 5.0})
    message = _messages(events, "showinfo")[0][3]
    assert "面1：NG数量8を申告しました" in message
    assert "面2：NG数量5を申告しました" in message


# ---------------------------------------------------------------- 実績は成功・NG は失敗

def test_ng_failure_keeps_actual_and_removes_pending_row(monthly_db, events):
    _add_abort_trigger("fail_ng_side1", "ng_declarations", "NEW.production_side = 1")
    removed = []
    window = _both_sides_window(events, pending_report_date="2026/3/18", removal=lambda: removed.append(True))
    _register(window, 90, {"1": 8.0, "2": 5.0})

    assert _history("K2", "L1") == [("2026-03-18", 90.0, "W1")]  # 実績は残る（ロールバックしない）
    assert _history("K1", "L1") == []
    assert _ng("K2", 2) == (5.0, _today())                         # 失敗しなかった面は保存される
    assert removed == [True]                                       # 保留行は NG の成否を問わず消す
    assert window._pending_csv_row_removal is None

    (_, _, title, message), = _messages(events, "showinfo")
    assert title == "登録完了"
    assert "面1のNG登録に失敗しました：test failure" in message
    assert "以下の項目でエラーが発生しました（登録できた分はそのまま残っています）：" in message
    assert "面2：NG数量5を申告しました" in message
    assert "面1：NG数量" not in message
    assert _messages(events, "showerror") == []


# ---------------------------------------------------------------- 実績の登録の失敗

def test_actual_failure_writes_nothing_and_keeps_pending_row(monthly_db, events):
    _add_abort_trigger("fail_k2", "production_daily", "NEW.kitting_list_no = 'K2'")
    removed = []
    window = _both_sides_window(events, pending_report_date="2026/3/18", removal=lambda: removed.append(True))
    _register(window, 90, {"1": 8.0, "2": 5.0})

    assert _history("K2", "L1") == []
    assert _history("K1", "L1") == []
    assert _ng("K1", 1) is None and _ng("K2", 2) is None
    assert removed == []                       # 保留行は消さない
    assert window._pending_csv_row_removal is not None
    assert window._pending_csv_report_date is None
    assert list_operation_log() == []
    (_, _, title, message), = _messages(events, "showerror")
    assert title == "登録エラー"
    assert message.startswith("実績の登録に失敗しました：")
    assert _messages(events, "showinfo") == []
    assert not any(e[0] in ("lbl_app_cum", "load_today_log", "entry_daily_qty") for e in events)


# ---------------------------------------------------------------- 操作履歴

def test_operation_log_records_actual_registration(monthly_db, events):
    _register(_both_sides_window(events), 90, {"1": 4.0})
    logs = list_operation_log()
    assert len(logs) == 1  # NG は別に記録しない
    assert logs[0]["worker_name"] == "作業者A"
    assert logs[0]["operation_name"] == "生産実績登録"
    assert logs[0]["detail"] == "K2 / ロットNo. L1 / 90"


def test_operation_log_formats_fractional_qty(monthly_db, events):
    _register(_both_sides_window(events), 12.5)
    assert list_operation_log()[0]["detail"] == "K2 / ロットNo. L1 / 12.5"


# ---------------------------------------------------------------- 画面側の動き（順序）

def test_screen_updates_order_for_manual_entry(monthly_db, events):
    _register(_both_sides_window(events), 90)
    names = [e[:2] if e[0] in ("lbl_app_cum", "entry_daily_qty", "messagebox") else e for e in events]
    assert names == [
        ("lbl_app_cum", "config"),
        ("load_today_log",),
        ("_load_current_daily_qty", "K2", "L1"),
        ("_setup_ng_side_ui", "K2"),
        ("_refresh_plan_list_for_lot", "L1"),
        ("messagebox", "showinfo"),
        ("entry_daily_qty", "focus_set"),   # 手入力では記入欄へ戻す
    ]
    assert events[0][3] == {"text": "90"}
    assert _messages(events, "showinfo")[0][3].splitlines()[0] == "実績を登録しました。アプリ入力累計：90"


def test_screen_updates_order_for_csv_staging(monthly_db, events):
    staging = _FakeStagingWindow(events)
    window = _both_sides_window(
        events, pending_report_date="2026/3/18", removal=lambda: events.append(("removal",)), staging_window=staging,
    )
    _register(window, 90)
    names = [e[:2] if e[0] in ("lbl_app_cum", "entry_daily_qty", "messagebox", "staging.tree") else e for e in events]
    assert names == [
        ("removal",),                       # 画面の更新より前に保留行を消す
        ("lbl_app_cum", "config"),
        ("load_today_log",),
        ("_load_current_daily_qty", "K2", "L1"),
        ("_setup_ng_side_ui", "K2"),
        ("_refresh_plan_list_for_lot", "L1"),
        ("messagebox", "showinfo"),
        ("staging", "lift"),                # CSV 経由なら一覧を手前に戻し、フォーカスも渡す（D-84）
        ("staging", "focus_force"),
        ("staging.tree", "focus_set"),
    ]


def test_cumulative_label_shows_new_cumulative(monthly_db, events):
    p2 = _plan("K2", "L1")
    insert_daily_production(p2["plan_item_id"], "K2", "L1", "BoardA", "2026-07-20", 50, "W0")
    _register(_both_sides_window(events), 30)  # 既存ありでも existing_daily_qty=None なら追記（呼び出し側の判定に従う）
    assert events[0][:2] == ("lbl_app_cum", "config")
    assert events[0][3] == {"text": "80"}
    assert sorted(_history("K2", "L1")) == [("2026-07-20", 50.0, "W0"), (_today(), 30.0, "W1")]


# ---------------------------------------------------------------- record_history

def _lot_history_count():
    with sqlite3.connect(config.DB_PATH) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'lot_status_history'").fetchone():
            return 0
        return con.execute("SELECT COUNT(*) FROM lot_status_history").fetchone()[0]


def test_record_history_false_writes_no_lot_history(monthly_db, events):
    _register(_both_sides_window(events), 90, record_history=False)
    assert _lot_history_count() == 0
    assert _history("K2", "L1") == [(_today(), 90.0, "W1")]
    assert _history("K1", "L1") == []


def test_record_history_true_writes_lot_history(monthly_db, events):
    _register(_both_sides_window(events), 90)
    assert _lot_history_count() > 0
