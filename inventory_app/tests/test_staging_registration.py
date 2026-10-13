"""
ステージング画面（ui/production_import_staging_window.py）経由の登録を固定する（段階3b）。
画面は表示しない。ステージング画面・親画面（生産実績入力）とも、偽の self に本物のクラスのメソッドを結び付けて呼ぶ。
偽の self が肩代わりするのは、ウィジェット（Treeview・Entry・Label）と、DB を読まない画面の更新だけ。
失敗は SQLite のトリガーで起こす（関数の置き場所を問わずに効くようにするため）。
"""
import os
import sqlite3
import tkinter.messagebox
import types
from datetime import datetime

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.operation_log import list_operation_log
from models.production import insert_daily_production
from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row
from services.production_service import get_daily_history
from services.staging_eligibility import _load_staged_rows_from_db
from ui.kitting_production_entry import KittingProductionEntryWindow as KPE
from ui.production_import_staging_window import ProductionImportStagingWindow as PISW

CSV_DATE = "2026/7/23"
CSV_DATE_NORMALIZED = "2026-07-23"

# (kitting_list_no, lot_no, setup_file_no, 面, 基板名, 生産予定日)
PLANS = [
    ("K1", "L1", "F1", "1", "ProdA", "2026/07/22 08:00:00"),  # L1 は両面。候補は面2の K2 だけになる（面1は除外）
    ("K2", "L1", "F1", "2", "ProdA", "2026/07/22 08:00:00"),
    ("K3", "L2", "F3", "2", "ProdB", "2026/07/20 08:00:00"),  # 日付の差3日 → Shift+Q でのみ登録できる
    ("K4", "L3", "F4", "2", "ProdC", "2026/07/22 08:00:00"),
    ("K5", "L5", "F5", "2", "ProdE", "2026/07/22 08:00:00"),  # 既に実績あり
]
# (lot_no, 製品名, 実績数) → 状態
PENDING = [
    ("L1", "ProdA", 100),   # eligible_shift_s（K2）
    ("L2", "ProdB", 100),   # eligible_shift_q（K3）
    ("L3", "ProdC", 90),    # qty_mismatch（K4）
    ("L9", "ProdZ", 100),   # no_candidates
    ("L5", "ProdE", 100),   # existing（K5）
]


# ---------------------------------------------------------------- 偽のウィジェット

class _Recorder:
    def __init__(self, events, name):
        self._events = events
        self._name = name

    def __getattr__(self, method):
        return lambda *args, **kwargs: self._events.append((self._name, method))


class _FakeEntry:
    def __init__(self, events, name):
        self._events = events
        self._name = name
        self.text = ""

    def delete(self, first, last=None):
        self.text = ""

    def insert(self, index, value):
        self.text = value + self.text if index == 0 else self.text + value

    def get(self):
        return self.text

    def config(self, **kwargs):
        pass

    def focus_set(self):
        self._events.append((self._name, "focus_set"))


class _FakeTree:
    def __init__(self, events, name, iids=()):
        self._events = events
        self._name = name
        self.items = list(iids)
        self.selected = ()
        self.row_at_event = None

    def get_children(self, item=""):
        return tuple(self.items)

    def next(self, iid):
        i = self.items.index(iid)
        return self.items[i + 1] if i + 1 < len(self.items) else ""

    def prev(self, iid):
        i = self.items.index(iid)
        return self.items[i - 1] if i > 0 else ""

    def delete(self, iid):
        self.items.remove(iid)

    def exists(self, iid):
        return iid in self.items

    def selection(self):
        return self.selected

    def selection_set(self, items):
        self.selected = (items,) if isinstance(items, str) else tuple(items)

    def see(self, iid):
        pass

    def focus_set(self):
        self._events.append((self._name, "focus_set"))

    def identify_row(self, y):
        return self.row_at_event


# ---------------------------------------------------------------- 偽の画面

class _FakeParent:
    """生産実績入力画面の代わり。定義していないメソッドは、本物の KittingProductionEntryWindow のものを使う。"""
    def __init__(self, events):
        self._events = events
        self.current_worker = {"worker_id": "W1", "name": "作業者A"}
        self.current_plan = None
        self._pending_csv_row_removal = None
        self._pending_csv_report_date = None
        self._csv_staging_window = None
        self._ng_side_plans = {"1": None, "2": None}
        self._ng_side_entries = {s: _FakeEntry(events, f"ng_entry_{s}") for s in ("1", "2")}
        self._ng_side_labels = {s: _Recorder(events, f"ng_label_{s}") for s in ("1", "2")}
        self.entry_daily_qty = _FakeEntry(events, "entry_daily_qty")
        self.confirm_dialog_previews = []

    def __getattr__(self, name):
        if name.startswith(("lbl_", "btn_")):
            return _Recorder(self._events, name)
        attr = getattr(KPE, name)  # 無ければ AttributeError
        if isinstance(attr, types.FunctionType):
            return types.MethodType(attr, self)
        raise AttributeError(name)

    def load_today_log(self):
        self._events.append(("parent", "load_today_log"))

    def _refresh_plan_list_for_lot(self, lot_no):
        self._events.append(("parent", "_refresh_plan_list_for_lot", lot_no))

    def _show_registration_confirm_dialog(self, preview):
        self.confirm_dialog_previews.append(preview)
        return True

    def winfo_toplevel(self):
        return self

    def state(self):
        return "normal"


class _FakeStaging:
    """ステージング画面の代わり。定義していないメソッドは、本物の ProductionImportStagingWindow のものを使う。"""
    def __init__(self, parent, events, rows):
        self._parent = parent
        self._events = events
        self._row_by_iid = {}
        for row in rows:
            self._row_by_iid[f"I{row['pending_row_id']}"] = row
        self.tree = _FakeTree(events, "staging.tree", self._row_by_iid)
        self.tree_candidates = _FakeTree(events, "staging.tree_candidates")
        self._current_staging_iid = None
        self._current_staging_row = None
        self._candidates_by_iid = {}

    def __getattr__(self, name):
        attr = getattr(PISW, name)
        if isinstance(attr, (types.FunctionType, staticmethod)):
            return types.MethodType(attr, self) if isinstance(attr, types.FunctionType) else attr
        raise AttributeError(name)

    def _update_status_label(self):
        self._events.append(("staging", "_update_status_label"))

    def _clear_candidate_pane(self):
        self._current_staging_iid = None
        self._current_staging_row = None
        self._events.append(("staging", "_clear_candidate_pane"))

    def _populate_candidates(self, row):
        self._events.append(("staging", "_populate_candidates"))

    def winfo_toplevel(self):
        return self

    def state(self):
        return "normal"

    def deiconify(self):
        self._events.append(("staging", "deiconify"))

    def lift(self):
        self._events.append(("staging", "lift"))

    def focus_force(self):
        self._events.append(("staging", "focus_force"))

    def winfo_exists(self):
        return True


# ---------------------------------------------------------------- 準備

@pytest.fixture
def monthly_db(monkeypatch):
    """db/<フォルダ>/inventory.db に月次DBを作る（既定DBだと操作履歴が書かれない）。"""
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    create_csv_import_batch("actual.csv", imported_by="test")
    batch_id = create_plan_batch("plan.csv", imported_by="test", row_count=len(PLANS))
    for kitting_list_no, lot_no, file_no, side, board, start in PLANS:
        create_plan_version(batch_id, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board, "production_side": side,
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": start,
        }, created_by="test")
    p5 = find_plan_item_by_kitting_no("K5", "L5")
    insert_daily_production(p5["plan_item_id"], "K5", "L5", "ProdE", "2026-07-20", 50, "W0")
    for i, (lot_no, product_name, qty) in enumerate(PENDING, start=1):
        upsert_pending_csv_import_row({
            "csv_row_no": i, "lot_no": lot_no, "product_name": product_name,
            "daily_qty": qty, "report_date": CSV_DATE, "worker_id": "W1",
        })


@pytest.fixture
def events(monkeypatch):
    """messagebox は表示せずに記録する。askyesno は True を返す（答えを変えるときは answers を使う）。"""
    recorded = []

    def make(name):
        def fake(title, message, **kwargs):
            recorded.append(("messagebox", name, title, message))
            return True if name == "askyesno" else None
        return fake

    for name in ("showinfo", "showerror", "showwarning", "askyesno"):
        monkeypatch.setattr(tkinter.messagebox, name, make(name))
    return recorded


@pytest.fixture
def answers(monkeypatch, events):
    """askyesno の答えを積むためのリスト。events の fake が参照するものと同じにする。"""
    queue = []

    def fake(title, message, **kwargs):
        events.append(("messagebox", "askyesno", title, message))
        return queue.pop(0) if queue else True

    monkeypatch.setattr(tkinter.messagebox, "askyesno", fake)
    return queue


@pytest.fixture
def screens(monthly_db, events):
    parent = _FakeParent(events)
    staging = _FakeStaging(parent, events, _load_staged_rows_from_db())
    parent._csv_staging_window = staging
    return parent, staging


def _iid(staging, lot_no):
    return next(iid for iid, row in staging._row_by_iid.items() if row["lot_no"] == lot_no)


def _candidate(staging, lot_no):
    return staging._row_by_iid[_iid(staging, lot_no)]["matched"][0]


def _select_row(staging, lot_no):
    iid = _iid(staging, lot_no)
    staging._current_staging_iid = iid
    staging._current_staging_row = staging._row_by_iid[iid]
    return iid


def _history(kitting_list_no, lot_no):
    return [(r["report_date"], r["daily_qty"], r["worker_id"]) for r in get_daily_history(kitting_list_no, lot_no)]


def _pending_lots():
    with sqlite3.connect(config.DB_PATH) as con:
        return sorted(r[0] for r in con.execute("SELECT lot_no FROM pending_csv_import_rows"))


def _lot_history(trigger_source=None):
    with sqlite3.connect(config.DB_PATH) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'lot_status_history'").fetchone():
            return []
        rows = con.execute("SELECT lot_no, trigger_source FROM lot_status_history ORDER BY id").fetchall()
    return [lot for lot, source in rows if trigger_source is None or source == trigger_source]


def _add_abort_trigger(name, table, when):
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute(f"CREATE TRIGGER {name} BEFORE INSERT ON {table} WHEN {when} BEGIN SELECT RAISE(ABORT, 'test failure'); END")


def _messages(events, name):
    return [(e[2], e[3]) for e in events if e[0] == "messagebox" and e[1] == name]


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def test_fixture_statuses(screens):
    _, staging = screens
    assert {row["lot_no"]: row["status"] for row in staging._row_by_iid.values()} == {
        "L1": "eligible_shift_s", "L2": "eligible_shift_q", "L3": "qty_mismatch", "L9": "no_candidates", "L5": "existing",
    }


# ---------------------------------------------------------------- ダブルクリック・Enter での転記

def test_double_click_transfers_row_without_writing(screens, events):
    parent, staging = screens
    _select_row(staging, "L1")
    staging._confirm_candidate(_candidate(staging, "L1"))

    assert parent.current_plan["kitting_list_no"] == "K2"
    assert parent.current_plan["lot_no"] == "L1"
    assert parent.entry_daily_qty.get() == "100"
    assert parent._pending_csv_report_date == CSV_DATE
    assert callable(parent._pending_csv_row_removal)
    assert events[-1] == ("entry_daily_qty", "focus_set")
    assert _history("K2", "L1") == []
    assert _pending_lots() == ["L1", "L2", "L3", "L5", "L9"]


def test_double_click_then_register_in_parent(screens, events):
    """転記した後、親の画面で登録（Enter・登録ボタン → 確認ダイアログ → 登録）すると、CSV の日付で登録され、保留行が消える。"""
    parent, staging = screens
    iid = _select_row(staging, "L1")
    staging._confirm_candidate(_candidate(staging, "L1"))
    parent._start_registration()

    assert _history("K2", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _history("K1", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]  # 反対側の面も連動
    assert _pending_lots() == ["L2", "L3", "L5", "L9"]
    assert iid not in staging._row_by_iid and not staging.tree.exists(iid)
    assert staging.tree.selection() == (_iid(staging, "L2"),)   # 次の行が選ばれる
    assert parent._pending_csv_row_removal is None
    assert _messages(events, "showinfo") == [(
        "登録完了", "実績を登録しました。アプリ入力累計：100\n反対側の面にも同じ数量を連動登録しました。",
    )]
    assert parent.confirm_dialog_previews[0]["new_report_date"] == CSV_DATE_NORMALIZED


def test_double_click_enter_key_uses_same_handler(screens, events):
    """候補の Enter キーとダブルクリックは同じ _confirm_candidate() に行き着く（登録済みでない候補）。"""
    parent, staging = screens
    _select_row(staging, "L1")
    staging.tree_candidates.row_at_event = "C1"
    staging._candidates_by_iid = {"C1": _candidate(staging, "L1")}
    staging._on_candidate_double_click(types.SimpleNamespace(y=0))
    assert parent.current_plan["kitting_list_no"] == "K2"
    assert parent._pending_csv_report_date == CSV_DATE


# ---------------------------------------------------------------- 右クリックの即時登録

def _right_click(staging, lot_no):
    _select_row(staging, lot_no)
    staging.tree_candidates.row_at_event = "C1"
    staging._candidates_by_iid = {"C1": _candidate(staging, lot_no)}
    staging._on_candidate_right_click(types.SimpleNamespace(y=0))


def test_right_click_registers_immediately(screens, events):
    parent, staging = screens
    iid = _iid(staging, "L1")
    _right_click(staging, "L1")

    assert _history("K2", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _history("K1", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _pending_lots() == ["L2", "L3", "L5", "L9"]
    assert not staging.tree.exists(iid)
    assert staging.tree.selection() == (_iid(staging, "L2"),)
    assert _messages(events, "showinfo") == [(
        "登録完了", "実績を登録しました。アプリ入力累計：100\n反対側の面にも同じ数量を連動登録しました。",
    )]
    assert _messages(events, "showerror") == []
    # 親の画面は登録した計画を表示し、CSV 由来の登録待ちは残らない。
    assert parent.current_plan["kitting_list_no"] == "K2"
    assert parent._pending_csv_row_removal is None
    assert parent._pending_csv_report_date is None
    assert ("parent", "_refresh_plan_list_for_lot", "L1") in events
    # 一覧を前面に戻し、フォーカスも渡す（最後の3つ）。
    assert events[-3:] == [("staging", "lift"), ("staging", "focus_force"), ("staging.tree", "focus_set")]
    # 操作履歴は1件、ロット履歴は通常の登録（record_history=True）として記録される。
    logs = list_operation_log()
    assert [(l["operation_name"], l["detail"]) for l in logs] == [("生産実績登録", "K2 / ロットNo. L1 / 100")]
    assert set(_lot_history("register")) == {"L1"}


def test_right_click_refuses_already_registered_candidate(screens, events):
    _, staging = screens
    _right_click(staging, "L5")
    assert _messages(events, "showinfo")[0][0] == "即時登録できません"
    assert "既に実績が登録されている" in _messages(events, "showinfo")[0][1]
    assert _history("K5", "L5") == [("2026-07-20", 50.0, "W0")]
    assert _pending_lots() == ["L1", "L2", "L3", "L5", "L9"]


def test_right_click_refuses_qty_mismatch(screens, events):
    _, staging = screens
    _right_click(staging, "L3")
    assert _messages(events, "showinfo")[0][0] == "即時登録できません"
    assert "一致しない" in _messages(events, "showinfo")[0][1]
    assert _history("K4", "L3") == []


def test_immediate_with_qty_difference_asks_to_fix_no(screens, events, answers):
    """計画数と実績数が違う即時登録（右クリックでは弾くが、関数としては受け付ける）: 完了文に続けて「数量を修正しますか？」と尋ねる。"""
    parent, staging = screens
    iid = _select_row(staging, "L3")
    answers.append(False)
    staging._register_candidate_immediately(staging._row_by_iid[iid], iid, _candidate(staging, "L3"))

    assert _history("K4", "L3") == [(CSV_DATE_NORMALIZED, 90.0, "W1")]
    assert _messages(events, "showinfo") == []
    (title, message), = _messages(events, "askyesno")
    assert title == "登録完了"
    assert message == (
        "実績を登録しました。アプリ入力累計：90\n\n"
        "実績数（90）が計画数（100、差-10）と異なります。数量を修正しますか？"
    )
    assert events[-3:] == [("staging", "lift"), ("staging", "focus_force"), ("staging.tree", "focus_set")]


def test_immediate_with_qty_difference_asks_to_fix_yes(screens, events, answers):
    parent, staging = screens
    iid = _select_row(staging, "L3")
    answers.append(True)
    staging._register_candidate_immediately(staging._row_by_iid[iid], iid, _candidate(staging, "L3"))

    # 計画を開き直して記入欄に実績数を入れ、記入欄にフォーカスを渡す。一覧へは戻さない。
    assert parent.current_plan["kitting_list_no"] == "K4"
    assert parent.entry_daily_qty.get() == "90"
    assert parent._pending_csv_row_removal is None
    assert events[-1] == ("entry_daily_qty", "focus_set")
    assert ("staging", "focus_force") not in events[events.index(("messagebox", "askyesno", "登録完了", _messages(events, "askyesno")[0][1])):]


def test_immediate_actual_failure(screens, events):
    """実績の登録に失敗: エラーを出し、保留行は残す。完了文は既定の文言になる。"""
    parent, staging = screens
    _add_abort_trigger("fail_k2", "production_daily", "NEW.kitting_list_no = 'K2'")
    iid = _iid(staging, "L1")
    _right_click(staging, "L1")

    assert _history("K2", "L1") == [] and _history("K1", "L1") == []
    assert _pending_lots() == ["L1", "L2", "L3", "L5", "L9"]
    assert staging.tree.exists(iid)
    (title, message), = _messages(events, "showerror")
    assert title == "登録エラー" and message.startswith("実績の登録に失敗しました：")
    assert _messages(events, "showinfo") == [("登録完了", "実績を登録しました（実績数：100）。")]
    # 親には、その行を消すコールバックが残る（日付はクリアされる）。
    assert callable(parent._pending_csv_row_removal)
    assert parent._pending_csv_report_date is None
    assert list_operation_log() == []


def test_immediate_opposite_failure_still_removes_row(screens, events):
    parent, staging = screens
    _add_abort_trigger("fail_k1", "production_daily", "NEW.kitting_list_no = 'K1'")
    _right_click(staging, "L1")
    assert _history("K2", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _history("K1", "L1") == []
    assert _pending_lots() == ["L2", "L3", "L5", "L9"]
    (title, message), = _messages(events, "showinfo")
    assert title == "登録完了"
    assert message == (
        "実績を登録しました。アプリ入力累計：100\n\n"
        "以下の項目でエラーが発生しました（登録できた分はそのまま残っています）：\n"
        "反対側の面への実績連動登録に失敗しました：test failure"
    )


# ---------------------------------------------------------------- Shift+S・Shift+Q の一括登録

def _select(staging, *lots):
    staging.tree.selection_set([_iid(staging, lot) for lot in lots])


def test_shift_s_registers_only_eligible_rows_and_summarizes(screens, events):
    parent, staging = screens
    _select(staging, "L1", "L2", "L3", "L9", "L5")
    staging._on_bulk_register()

    assert _history("K2", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _history("K1", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _history("K3", "L2") == [] and _history("K4", "L3") == []
    assert _pending_lots() == ["L2", "L3", "L5", "L9"]
    assert _messages(events, "showinfo") == [(
        "Shift+S一括登録完了",
        "1件を登録しました。\n4件は要件を満たさないためスキップしました。\n"
        "　・日付の差が許容日数を超える：1件\n　・数量不一致：1件\n　・候補なし：1件\n　・既に実績登録済み：1件",
    )]
    assert _messages(events, "askyesno") == []                    # Shift+S は確認しない
    assert staging.tree.selection() == ()                          # 全行を選んでいたので、選ぶ行が無い
    assert events[-3:] == [("staging", "lift"), ("staging", "focus_force"), ("staging.tree", "focus_set")]


def test_shift_s_selects_row_after_processed_range(screens, events):
    _, staging = screens
    _select(staging, "L1", "L2")
    staging._on_bulk_register()
    assert staging.tree.selection() == (_iid(staging, "L3"),)


def test_shift_s_selects_row_before_when_range_reaches_end(screens, events):
    _, staging = screens
    _select(staging, "L9", "L5")
    staging._on_bulk_register()
    assert staging.tree.selection() == (_iid(staging, "L3"),)


def test_shift_s_records_lot_history_once_per_lot_at_the_end(screens, events):
    _, staging = screens
    _select(staging, "L1", "L2", "L3")
    staging._on_bulk_register()
    assert _lot_history("bulk_register") == ["L1"]
    assert _lot_history("register") == [] and _lot_history("overwrite") == []  # 行ごとには記録しない


def test_shift_s_operation_log_per_registered_row(screens, events):
    _, staging = screens
    _select(staging, "L1", "L2")
    staging._on_bulk_register()
    assert [(l["operation_name"], l["detail"]) for l in list_operation_log()] == [("生産実績登録", "K2 / ロットNo. L1 / 100")]


def test_shift_q_confirms_then_registers_with_wider_date_range(screens, events, answers):
    parent, staging = screens
    _select(staging, "L1", "L2", "L3")
    answers.append(True)
    staging._on_bulk_register_extended()

    (title, message), = _messages(events, "askyesno")
    assert title == "Shift+Q一括登録の確認"
    assert message == (
        "選択した行数：3件\n登録される行数：2件\n"
        "　うち日付の差が2〜4日の行数（Shift+Sでは登録されない行）：1件\n\n"
        "Shift+Qで一括登録します。よろしいですか？"
    )
    assert _history("K2", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _history("K3", "L2") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _pending_lots() == ["L3", "L5", "L9"]
    assert _messages(events, "showinfo") == [(
        "Shift+Q一括登録完了",
        "2件を登録しました。\n1件は要件を満たさないためスキップしました。\n　・数量不一致：1件",
    )]
    assert sorted(_lot_history("bulk_register_shift_q")) == ["L1", "L2"]
    assert staging.tree.selection() == (_iid(staging, "L9"),)
    # 親の計画一覧は、登録したロットの行が更新される。
    refreshed = {e[2] for e in events if e[:2] == ("parent", "_refresh_plan_list_for_lot")}
    assert refreshed == {"L1", "L2"}
    # 親の画面は、最後に登録した計画を表示し、CSV 由来の登録待ちは残らない。
    assert parent.current_plan["kitting_list_no"] == "K3"
    assert parent._pending_csv_row_removal is None and parent._pending_csv_report_date is None


def test_shift_q_cancel_changes_nothing(screens, events, answers):
    _, staging = screens
    _select(staging, "L1", "L2")
    answers.append(False)
    staging._on_bulk_register_extended()
    assert _history("K2", "L1") == [] and _history("K3", "L2") == []
    assert _pending_lots() == ["L1", "L2", "L3", "L5", "L9"]
    assert _messages(events, "showinfo") == []
    assert staging.tree.selection() == (_iid(staging, "L1"), _iid(staging, "L2"))


def test_bulk_failure_is_counted_and_shown(screens, events, answers):
    _, staging = screens
    _add_abort_trigger("fail_k3", "production_daily", "NEW.kitting_list_no = 'K3'")
    _select(staging, "L1", "L2")
    answers.append(True)
    staging._on_bulk_register_extended()

    assert _history("K2", "L1") == [(CSV_DATE_NORMALIZED, 100.0, "W1")]
    assert _history("K3", "L2") == []
    assert _pending_lots() == ["L2", "L3", "L5", "L9"]
    assert len(_messages(events, "showerror")) == 1               # 行ごとのエラーは止めない
    assert _messages(events, "showinfo") == [(
        "Shift+Q一括登録完了",
        "1件を登録しました。\n0件は要件を満たさないためスキップしました。\n"
        "1件は登録中にエラーが発生しました（詳細は個別のエラーダイアログを参照）。",
    )]
    assert _lot_history("bulk_register_shift_q") == ["L1"]
    assert staging.tree.selection() == (_iid(staging, "L3"),)


def test_bulk_with_nothing_selected_does_nothing(screens, events):
    _, staging = screens
    staging._on_bulk_register()
    assert events == []
