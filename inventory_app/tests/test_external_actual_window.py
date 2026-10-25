"""
現場CSV取込画面（ui/production/external_actual_window.py）と、生産実績入力画面の「現場CSV取込」ボタン。
実際にウインドウを作る（Tk が使えない環境ではスキップ）。別スレッドの処理は、画面を回して終わるのを待つ。
"""
import time
import tkinter as tk
from tkinter import ttk

import pytest

from models import external_actual_rows as ear
from models.kitting_plan import find_plan_item_by_kitting_no
from models.production import insert_daily_production
from services import external_actual_service as svc
from services.production_service import get_daily_history
from tests.external_actual_helpers import line, write_csv
from tests.test_external_actual_register import FOLDER
from tests.test_external_actual_register import add_rows, db  # noqa: F401 （fixture）
from ui.common.checkable_treeview import CheckableTreeview
from ui.kitting.kitting_production_entry import KittingProductionEntryWindow
from ui.production import external_actual_window as module
from ui.production.external_actual_window import ExternalActualWindow

WORKER = {"worker_id": "A1", "name": "管理者"}


class _Messages:
    """messagebox の代わり。呼ばれた種類と本文を記録し、確認には answer を返す。"""
    def __init__(self):
        self.calls = []
        self.answer = True

    def _record(self, kind):
        def call(title, message, **kwargs):
            self.calls.append((kind, title, message))
            return self.answer
        return call

    def __getattr__(self, name):
        return self._record(name)


class _Parent(tk.Toplevel):
    """生産実績入力画面の代わり。表示中の計画と、登録後の更新の呼び出しだけを持つ。"""
    def __init__(self, root):
        super().__init__(root)
        self.current_plan = None
        self.refreshed = []

    def refresh_after_external_registration(self, lot_nos, new_cumulative=None):
        self.refreshed.append((list(lot_nos), new_cumulative))


@pytest.fixture
def root(tk_root):
    yield tk_root
    for child in tk_root.winfo_children():
        child.destroy()


@pytest.fixture
def messages(monkeypatch):
    fake = _Messages()
    monkeypatch.setattr(module, "messagebox", fake)
    return fake


def pump(window, condition=None, timeout=15):
    """別スレッドの処理が終わる（画面が busy でなくなる）まで画面を回す。"""
    end = time.time() + timeout
    while time.time() < end:
        window.update()
        if not window._busy and (condition is None or condition()):
            return
        time.sleep(0.01)
    raise AssertionError("画面の処理が終わりませんでした")


def open_window(root, parent=None):
    parent = parent or _Parent(root)
    window = ExternalActualWindow(parent, WORKER)
    pump(window)
    return window, parent


def iid_of(window, kitting_list_no, lot_no):
    return next(i for i, item in window._item_by_iid.items() if (item["kitting_list_no"], item["lot_no"]) == (kitting_list_no, lot_no))


def find_buttons(widget, text):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button) and child.cget("text") == text:
            found.append(child)
        found += find_buttons(child, text)
    return found


# ---------------------------------------------------------------- 一覧

def test_list_shows_rows_with_state_colors_and_only_registerable_rows_can_be_checked(db, root, messages):
    plan = find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(plan["plan_item_id"], "K2", "L1", "BoardA", "2026-10-20", 99, "W1")       # 変更なし（グレー）になる
    add_rows({"kitting_list_no": "K3", "lot_no": "L2", "file_no": "0002", "daily_qty": 100, "ng_side2": 0},        # 緑
             {"kitting_list_no": "K3", "lot_no": "L2x", "file_no": "0002"},                                      # 赤（計画なし）
             {"kitting_list_no": "K2", "lot_no": "L1", "daily_qty": 99, "ng_side2": 0},                          # 同じ計画の後の行 = 登録待ちはこれ
             {"kitting_list_no": "KC", "lot_no": "LC", "file_no": "0005", "daily_qty": 120, "ng_side2": 0})        # 黄（完成済み）
    window, _ = open_window(root)
    tags = {item["lot_no"]: window.table.tree.item(iid, "tags")[0] for iid, item in window._item_by_iid.items()}
    assert tags["L2"] == "green" and tags["L2x"] == "red" and tags["LC"] == "yellow" and tags["L1"] == "gray"

    for kitting, lot in (("K3", "L2x"), ("K2", "L1")):                                                      # 赤とグレーは選べない
        window.table.set_checked(iid_of(window, kitting, lot), True)
    assert window.table.get_checked_iids() == []
    window.table.set_checked(iid_of(window, "K3", "L2"), True)
    window.table.set_checked(iid_of(window, "KC", "LC"), True)                                              # 黄は1行ずつなら選べる
    assert len(window.table.get_checked_iids()) == 2

    window._select_green()
    assert [window._item_by_iid[i]["state"] for i in window.table.get_checked_iids()] == ["green"]         # 黄は「緑を選ぶ」では選ばれない


def test_cells_show_now_arrow_new_and_difference(db, root, messages):
    plan = find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(plan["plan_item_id"], "K2", "L1", "BoardA", "2026-10-10", 90, "W1")
    add_rows({"daily_qty": 99, "ng_side2": 1})
    window, _ = open_window(root)
    values = window.table.tree.item(next(iter(window._item_by_iid)), "values")
    assert values[1] == "上書き" and values[6] == "90→99（+9）" and values[7] == "－→0" and values[8] == "－→1"
    assert values[9] == "100" and "2026-10-10 から 2026-10-20 に変わります" in values[10]


# ---------------------------------------------------------------- 取り込み

def test_import_flow_stores_rows_and_shows_errors_as_red_rows(db, root, messages, monkeypatch, tmp_path):
    csv_path = write_csv(tmp_path, line(db=FOLDER, kitting="K3", lot="L2", file_no="0002", qty="100", ng2="0"),
                         line(db=FOLDER, side="9", kitting="K9", lot="L9"))
    monkeypatch.setattr(module.filedialog, "askopenfilename", lambda **kwargs: csv_path)
    window, _ = open_window(root)
    window.on_import()
    pump(window, lambda: len(ear.list_pending_rows()) == 1 and window._items)
    kind, title, text = messages.calls[-1]
    assert (kind, title) == ("showwarning", "取り込みました") and "登録待ちにしました：1 行" in text and "実績・NG にはまだ書いていません" in text
    states = sorted(item["state"] for item in window._item_by_iid.values())
    assert states == ["green", "red"]
    error_iid = next(i for i, item in window._item_by_iid.items() if item["row_id"] is None)
    assert "面が 1 か 2 ではありません（9）" in window._item_by_iid[error_iid]["remarks"][0]
    assert get_daily_history("K3", "L2") == []                                                              # 取り込んだだけでは登録しない


def test_import_refused_for_fatal_header_and_for_wrong_format(db, root, messages, monkeypatch, tmp_path):
    bad = write_csv(tmp_path, line(version="1"))
    monkeypatch.setattr(module.filedialog, "askopenfilename", lambda **kwargs: bad)
    window, _ = open_window(root)
    window.on_import()
    pump(window, lambda: messages.calls)
    assert messages.calls[-1][:2] == ("showerror", "取り込めません") and "書式版が 2 ではありません" in messages.calls[-1][2]
    assert ear.list_pending_rows() == []

    production = tmp_path / "prod.csv"
    production.write_text("払い出し日,基板構成数\r\n2026/10/20,2\r\n", encoding="utf-8-sig")
    monkeypatch.setattr(module.filedialog, "askopenfilename", lambda **kwargs: str(production))
    messages.answer = False                                                                                 # 「続けない」
    before = len(messages.calls)
    window.on_import()
    window.update()
    assert messages.calls[before][:2] == ("askyesno", "CSVフォーマットの確認") and "実績CSV" in messages.calls[before][2]
    assert len(messages.calls) == before + 1                                                                # 取り込みは始まらない


def test_import_of_tmp_file_is_refused_with_message_before_anything_else(db, root, messages, monkeypatch, tmp_path):
    tmp = write_csv(tmp_path, line(), name="actuals_20261020_180000_0001.tmp")
    monkeypatch.setattr(module.filedialog, "askopenfilename", lambda **kwargs: tmp)
    window, _ = open_window(root)
    window.on_import()
    window.update()
    kind, title, message = messages.calls[-1]
    assert (kind, title) == ("showwarning", "現場CSV取込") and "拡張子が .csv ではない" in message and ".tmp" in message
    assert not window._busy and ear.list_pending_rows() == []                  # 取り込みは始まらず、形式の確認も出ない
    assert all(call[0] != "askyesno" for call in messages.calls)


def test_select_green_does_not_select_side_master_unregistered_yellow_row(db, root, messages):
    """「緑をすべて選ぶ」は、生産面マスター未登録の面1（黄）を選ばない。1行ずつ自分で選べば登録できる（D-189）。"""
    add_rows({"kitting_list_no": "K4", "lot_no": "L3", "file_no": "0003", "side": "1", "ng_side1": 0, "ng_side2": 0, "daily_qty": 100},
             {"kitting_list_no": "K3", "lot_no": "L2", "file_no": "0002", "side": "2", "ng_side1": 0, "ng_side2": 0, "daily_qty": 100})
    window, _ = open_window(root)
    pump(window, lambda: window._items)
    window._select_green()
    states = {window._item_by_iid[i]["kitting_list_no"]: window._item_by_iid[i]["state"] for i in window._item_by_iid}
    checked = {window._item_by_iid[i]["kitting_list_no"] for i in window.table.get_checked_iids()}
    assert states == {"K4": "yellow", "K3": "green"} and checked == {"K3"}
    iid = next(i for i, item in window._item_by_iid.items() if item["kitting_list_no"] == "K4")
    window.table.set_checked(iid, True)
    assert iid in window.table.get_checked_iids()


# ---------------------------------------------------------------- 登録

def test_register_flow_updates_parent_and_list(db, root, messages, monkeypatch):
    add_rows({"kitting_list_no": "K3", "lot_no": "L2", "file_no": "0002", "daily_qty": 100, "ng_side2": 0})
    parent = _Parent(root)
    parent.current_plan = {"kitting_list_no": "K3", "lot_no": "L2"}
    window, _ = open_window(root, parent)
    monkeypatch.setattr(ExternalActualWindow, "_show_results", lambda self, results: setattr(self, "shown", results))
    monkeypatch.setattr(ExternalActualWindow, "_confirm_registration", lambda self, selected: [i["row_id"] for i in selected])
    window._select_green()
    window.on_register()
    pump(window, lambda: parent.refreshed and not window._item_by_iid)
    assert [r["status"] for r in window.shown] == ["registered"]
    assert parent.refreshed == [(["L2"], 100)]                                                              # 表示中の計画の累計を渡す
    assert get_daily_history("K3", "L2")[0]["daily_qty"] == 100 and ear.list_pending_rows() == []


def test_register_with_nothing_selected_only_tells(db, root, messages):
    add_rows({})
    window, parent = open_window(root)
    window.on_register()
    assert messages.calls[-1][:2] == ("showinfo", "現場CSV取込") and parent.refreshed == []


def test_confirm_dialog_defaults_to_cancel_counts_and_lets_overwrite_rows_be_dropped(db, root, messages):
    plan = find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(plan["plan_item_id"], "K2", "L1", "BoardA", "2026-10-10", 90, "W1")
    add_rows({"daily_qty": 99, "ng_side2": 1}, {"kitting_list_no": "K3", "lot_no": "L2", "file_no": "0002", "daily_qty": 100, "ng_side2": 0})
    window, _ = open_window(root)
    selected = [item for item in window._item_by_iid.values() if item["state"] != "red"]
    assert {item["label"] for item in selected} == {"上書き", "新規"}
    seen = {}

    def inspect_then_confirm():
        dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
        label = next(c for c in dialog.winfo_children() if isinstance(c, ttk.Label))
        seen["summary"] = label.cget("text")
        table = next(c for c in dialog.winfo_children() if isinstance(c, CheckableTreeview))
        seen["overwrite_rows"] = len(table.tree.get_children())
        dialog.update()
        seen["focus"] = str(dialog.tk.call("focus", "-lastfor", str(dialog)))
        seen["cancel"] = str(find_buttons(dialog, "キャンセル")[0])
        table.set_checked(table.tree.get_children()[0], False)                                              # 上書きの行を外す
        find_buttons(dialog, "登録する")[0].invoke()

    window.after(300, inspect_then_confirm)
    ids = window._confirm_registration(selected)
    assert "新規 1 行／上書き 1 行／完成済みロット 0 行" in seen["summary"] and seen["overwrite_rows"] == 1
    assert seen["focus"] == seen["cancel"]                                                                  # 初めに選ばれているのはキャンセル
    assert ids == [next(i["row_id"] for i in selected if i["label"] == "新規")]                              # 外した上書きの行は含まれない


def test_confirm_dialog_cancel_returns_none(db, root, messages):
    add_rows({"daily_qty": 99})
    window, _ = open_window(root)
    selected = list(window._item_by_iid.values())

    def cancel():
        dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
        find_buttons(dialog, "キャンセル")[0].invoke()

    window.after(300, cancel)
    assert window._confirm_registration(selected) is None


def test_results_dialog_lists_every_row(db, root, messages):
    add_rows({})
    window, _ = open_window(root)
    results = [{"status": svc.RESULT_REGISTERED, "kitting_list_no": "K2", "lot_no": "L1", "message": "登録しました"},
               {"status": svc.RESULT_PARTIAL, "kitting_list_no": "K3", "lot_no": "L2", "message": "NGエラー"}]
    seen = {}

    def inspect():
        dialog = next(w for w in window.winfo_children() if isinstance(w, tk.Toplevel))
        tree = next(c for c in dialog.winfo_children() if isinstance(c, ttk.Treeview))
        seen["rows"] = [tree.item(i, "values") for i in tree.get_children()]
        find_buttons(dialog, "閉じる")[0].invoke()

    window.after(300, inspect)
    window._show_results(results)
    assert [row[0] for row in seen["rows"]] == ["登録", "NGエラー"]


# ---------------------------------------------------------------- 生産実績入力画面

def test_entry_screen_has_button_and_locks_register_while_window_is_open(db, root, messages):
    host = tk.Toplevel(root)
    entry = KittingProductionEntryWindow(host, {"worker_id": "A1", "name": "管理者"}, preloaded_plan_rows=[])
    entry.update()
    assert entry.btn_external_actual.cget("text") == "現場CSV取込"
    entry.current_plan = {"kitting_list_no": "K3", "lot_no": "L2"}
    entry._set_plan_buttons(True)
    assert str(entry.btn_register.cget("state")) == "normal"

    entry.open_external_actual_window()
    pump(entry._external_actual_window)
    assert str(entry.btn_register.cget("state")) == "disabled" and str(entry.btn_correction.cget("state")) == "disabled"
    entry._set_plan_buttons(True)                                                                           # 計画を選び直しても押せない
    assert str(entry.btn_register.cget("state")) == "disabled"
    first = entry._external_actual_window
    entry.open_external_actual_window()
    assert entry._external_actual_window is first                                                           # 多重に開かない

    first._close()
    assert str(entry.btn_register.cget("state")) == "normal" and str(entry.btn_correction.cget("state")) == "normal"
    entry.current_plan = None
    entry.open_external_actual_window()
    pump(entry._external_actual_window)
    entry._external_actual_window._close()
    assert str(entry.btn_register.cget("state")) == "disabled"                                              # 計画が無ければ閉じても無効のまま
