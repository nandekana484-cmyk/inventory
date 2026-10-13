"""
ui/main_window.py の、別スレッドで実行する4つの処理の動きを固定する（段階6）。
生産実績入力画面を開く・新しい DB の作成（前月からの引き継ぎあり）・バックアップ・マスタデータの取り込み。
本物の Tk のウィンドウ（空の Toplevel）に、MainWindow のメソッドを結び付けて呼ぶ。重い処理（引き継ぎ・バックアップ・
取り込み・画面の読み込み）は偽物に差し替え、成功と失敗の両方で、メッセージ・ボタンの有効化・操作履歴・読み込み中の表示を確かめる。
"""
import time
import tkinter as tk
import tkinter.filedialog
import tkinter.messagebox
import types

import pytest

from ui import main_window as mw
from ui.loading_window import LoadingWindow


# tk_root はテスト全体で共通（tests/conftest.py）。


class _Host(tk.Toplevel):
    """空の Toplevel。定義していないメソッドは MainWindow のものを結び付けて使う。"""
    def __getattr__(self, name):
        attr = mw.MainWindow.__dict__.get(name)
        if isinstance(attr, types.FunctionType):
            return types.MethodType(attr, self)
        raise AttributeError(name)


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture
def host(tk_root, monkeypatch):
    events = []
    h = _Host(tk_root)
    h.geometry("+3000+3000")
    h.events = events
    h.current_worker = {"worker_id": "W1", "name": "作業者A", "role": "admin"}
    h._open_windows = {}
    h._kitting_entry_loading = False
    h._confirm_proceed_despite_exported_db = lambda: True
    h._set_menu_enabled = lambda enabled: events.append(("menu", enabled))
    h._update_current_db_label = lambda: events.append(("db_label",))
    h._load_db_folders = lambda: events.append(("load_db_folders",))
    h._has_open_child_windows = lambda: False
    h._acquire_lock_with_corruption_handling = lambda path: True
    h._release_current_lock = lambda: events.append(("release_lock",))
    h.new_db_folder_var = _Var("")
    h.db_folder_var = _Var("")
    h.carry_over_var = _Var(True)
    # MainWindow.__init__() で用意している属性（偽の画面には __init__ が無いので、ここで用意する）
    import queue
    for name in ("_create_db_result_queue", "_backup_result_queue", "_merge_master_result_queue"):
        setattr(h, name, queue.Queue())
    h._create_db_loading_window = None
    h._backup_loading_window = None
    h._backup_destination_folder = None
    h._merge_master_loading_window = None

    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(tkinter.messagebox, name,
                            lambda title, message, _n=name, **kw: events.append(("messagebox", _n, title, message)))
    monkeypatch.setattr(tkinter.messagebox, "askyesno", lambda *a, **kw: True)
    monkeypatch.setattr(mw, "log_operation", lambda name, op, detail=None: events.append(("log", name, op, detail)))
    monkeypatch.setattr(mw, "record_db_lifecycle_event",
                        lambda op_type, folder, worker_name=None, source_info=None: events.append(("lifecycle", op_type, folder, source_info)))
    yield h
    for child in h.winfo_children():
        child.destroy()
    h.destroy()


def _pump(host, until, timeout=5.0):
    """Tk のイベントを回しながら、until() が真になるのを待つ（after(200) のポーリングを進めるため）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        host.update()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timeout")


def _loading_windows(host):
    return [w for w in host.winfo_children() if isinstance(w, LoadingWindow) and w.winfo_exists()]


def _loading_text(window):
    """読み込み中の表示の文言（LoadingWindow の中の ttk.Label）。"""
    def walk(w):
        for c in w.winfo_children():
            yield c
            yield from walk(c)
    return [c.cget("text") for c in walk(window) if c.winfo_class() == "TLabel"][0]


def _messages(host):
    return [e for e in host.events if e[0] == "messagebox"]


# ---------------------------------------------------------------- 生産実績入力画面を開く

class _FakeEntryWindow:
    fetch_result = ["row1"]
    created = []

    def __init__(self, parent, current_worker, preloaded_plan_rows=None):
        self.preloaded_plan_rows = preloaded_plan_rows
        self.protocols = {}
        _FakeEntryWindow.created.append(self)

    @staticmethod
    def _fetch_plan_list_rows():
        if isinstance(_FakeEntryWindow.fetch_result, Exception):
            raise _FakeEntryWindow.fetch_result
        return _FakeEntryWindow.fetch_result

    def protocol(self, name, func):
        self.protocols[name] = func

    def destroy(self):
        pass


def test_open_entry_success(host, monkeypatch):
    monkeypatch.setattr(mw, "KittingProductionEntryWindow", _FakeEntryWindow)
    _FakeEntryWindow.fetch_result = ["row1", "row2"]
    _FakeEntryWindow.created = []
    ready = []
    host.open_kitting_production_entry(on_ready=ready.append)
    assert host._kitting_entry_loading is True
    loading = _loading_windows(host)
    assert len(loading) == 1 and loading[0].title() == "読み込み中"
    assert _loading_text(loading[0]) == "生産実績画面を読み込んでいます…"
    host.open_kitting_production_entry()            # 読み込み中は二重に起こさない
    assert len(_loading_windows(host)) == 1
    _pump(host, lambda: ready)
    window = _FakeEntryWindow.created[0]
    assert window.preloaded_plan_rows == ["row1", "row2"]
    assert host._open_windows == {"kitting_production_entry": window}
    assert "WM_DELETE_WINDOW" in window.protocols
    assert host._kitting_entry_loading is False
    assert _loading_windows(host) == []
    assert _messages(host) == []


def test_open_entry_error_shows_exception_text(host, monkeypatch):
    monkeypatch.setattr(mw, "KittingProductionEntryWindow", _FakeEntryWindow)
    _FakeEntryWindow.fetch_result = RuntimeError("読み込み失敗")
    _FakeEntryWindow.created = []
    host.open_kitting_production_entry()
    _pump(host, lambda: _messages(host))
    assert _messages(host) == [("messagebox", "showerror", "エラー", "生産実績入力画面の読み込みに失敗しました：\n読み込み失敗")]
    assert host._kitting_entry_loading is False
    assert _loading_windows(host) == []
    assert _FakeEntryWindow.created == []


# ---------------------------------------------------------------- 新しい DB の作成（引き継ぎあり）

SUMMARY = {
    "lots_copied": 2, "kitting_plan_items_copied": 5, "production_daily_copied": 7,
    "failed_lot_nos": [], "skipped_lot_nos": ["L9"], "duplicate_lot_warnings": [],
}


def test_create_db_with_carry_over_success(host, monkeypatch):
    calls = []

    def fake_carry_over(old, new, imported_by=None):
        calls.append((old, new, imported_by))
        return dict(SUMMARY)

    monkeypatch.setattr(mw, "carry_over_incomplete_lots", fake_carry_over)
    host.new_db_folder_var.set("2026-08")
    host.on_create_database()
    assert host.events[:2] == [("release_lock",), ("menu", False)]
    loading = _loading_windows(host)
    assert len(loading) == 1 and _loading_text(loading[0]) == "前月からの未完了分を引き継いでいます…"
    _pump(host, lambda: _messages(host))
    assert calls[0][2] == "W1"
    assert calls[0][1].endswith("2026-08\\inventory.db") or calls[0][1].endswith("2026-08/inventory.db")
    assert [e[0] for e in host.events] == [
        "release_lock", "menu", "menu", "db_label", "log", "lifecycle", "messagebox", "load_db_folders",
    ]
    assert ("menu", True) in host.events
    assert host.events[4] == ("log", "作業者A", "未完了計画のDB間引き継ぎ", "成功2件 / スキップ1件 / 失敗0件")
    assert host.events[5][1:3] == (mw.OPERATION_TYPE_CREATE_WITH_CARRY_OVER, "2026-08")
    (_, kind, title, message), = _messages(host)
    assert (kind, title) == ("showinfo", "完了")
    assert message.startswith("新しいデータベースを作成しました。\n未完了ロット 2件・計画行 5件・実績 7件を引き継ぎました。")
    assert "スキップしました" in message
    assert host._create_db_loading_window is None
    assert _loading_windows(host) == []
    assert host.db_folder_var.get() == "2026-08" and host.new_db_folder_var.get() == ""


def test_create_db_with_carry_over_error(host, monkeypatch):
    def fake_carry_over(old, new, imported_by=None):
        raise ValueError("引き継ぎ失敗")

    monkeypatch.setattr(mw, "carry_over_incomplete_lots", fake_carry_over)
    host.new_db_folder_var.set("2026-09")
    host.on_create_database()
    _pump(host, lambda: _messages(host))
    (_, kind, title, message), = _messages(host)
    assert kind == "showerror"
    # エラーの文言は「例外の文字列＋改行＋traceback」
    assert "引き継ぎ失敗\nTraceback (most recent call last):" in message
    assert message.rstrip().endswith("ValueError: 引き継ぎ失敗")
    assert ("menu", True) in host.events and ("db_label",) in host.events
    assert not any(e[0] in ("log", "lifecycle") for e in host.events)
    assert _loading_windows(host) == []


def test_create_db_error_message_title(host, monkeypatch):
    monkeypatch.setattr(mw, "carry_over_incomplete_lots", lambda *a, **kw: (_ for _ in ()).throw(ValueError("x")))
    host.new_db_folder_var.set("2026-10")
    host.on_create_database()
    _pump(host, lambda: _messages(host))
    title, message = _messages(host)[0][2:]
    assert title == "引き継ぎエラー"
    assert message.startswith(
        "未完了分の引き継ぎ中にエラーが発生しました。\n新しいデータベース自体は作成済みです（切り替え済み）。\n\nx\nTraceback"
    )


# ---------------------------------------------------------------- バックアップ

def test_backup_success(host, monkeypatch):
    monkeypatch.setattr(tkinter.filedialog, "askdirectory", lambda **kw: "C:/backup")
    monkeypatch.setattr(mw, "backup_databases", lambda folder: {
        "inventory_backup_path": f"{folder}/inv.db", "master_backup_path": None, "skipped": ["master"],
    })
    host.on_backup_databases()
    assert host.events == [("menu", False)]
    assert _loading_text(_loading_windows(host)[0]) == "データベースをバックアップしています…"
    _pump(host, lambda: _messages(host))
    assert host.events[:3] == [("menu", False), ("menu", True), ("log", "作業者A", "手動バックアップ", "保存先: C:/backup")]
    assert _messages(host) == [("messagebox", "showinfo", "バックアップ完了",
                                "以下のファイルを保存しました。\n\n・月次DB：C:/backup/inv.db\n"
                                "\n※ 次のDBはまだ一度も作成されていないためスキップしました：\n  マスタDB")]
    assert host._backup_loading_window is None
    assert _loading_windows(host) == []


def test_backup_error(host, monkeypatch):
    monkeypatch.setattr(tkinter.filedialog, "askdirectory", lambda **kw: "C:/backup")

    def fail(folder):
        raise OSError("書き込めません")

    monkeypatch.setattr(mw, "backup_databases", fail)
    host.on_backup_databases()
    _pump(host, lambda: _messages(host))
    (_, kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "バックアップエラー")
    assert message.startswith("バックアップ中にエラーが発生しました。\n\n書き込めません\nTraceback (most recent call last):")
    assert host.events[:2] == [("menu", False), ("menu", True)]
    assert _loading_windows(host) == []


def test_backup_cancelled_does_nothing(host, monkeypatch):
    monkeypatch.setattr(tkinter.filedialog, "askdirectory", lambda **kw: "")
    host.on_backup_databases()
    assert host.events == [] and _loading_windows(host) == []


# ---------------------------------------------------------------- マスタデータの取り込み

def test_merge_master_success(host, monkeypatch):
    monkeypatch.setattr(tkinter.filedialog, "askopenfilename", lambda **kw: "C:/master_backup_1.db")
    monkeypatch.setattr(mw, "merge_master_from_backup", lambda path: {"workers": 2, "parts": 0})
    host.on_merge_master_from_backup()
    assert _loading_text(_loading_windows(host)[0]) == "マスタデータを取り込んでいます…"
    _pump(host, lambda: _messages(host))
    assert host.events[:3] == [("menu", False), ("menu", True), ("log", "作業者A", "マスタデータ取り込み", "作業者:2件, 部品マスタ:0件")]
    assert _messages(host) == [("messagebox", "showinfo", "取り込み完了",
                                "マスタデータの取り込みが完了しました。\n\n・作業者：2件追加\n・部品マスタ：変更なし")]
    assert host._merge_master_loading_window is None
    assert _loading_windows(host) == []


def test_merge_master_error(host, monkeypatch):
    monkeypatch.setattr(tkinter.filedialog, "askopenfilename", lambda **kw: "C:/master_backup_1.db")

    def fail(path):
        raise RuntimeError("壊れています")

    monkeypatch.setattr(mw, "merge_master_from_backup", fail)
    host.on_merge_master_from_backup()
    _pump(host, lambda: _messages(host))
    (_, kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "取り込みエラー")
    assert message.startswith("マスタデータの取り込み中にエラーが発生しました。\n\n壊れています\nTraceback (most recent call last):")
    assert host.events[:2] == [("menu", False), ("menu", True)]
    assert _loading_windows(host) == []


def test_merge_master_requires_admin(host):
    host.current_worker["role"] = "operator"
    host.on_merge_master_from_backup()
    assert _messages(host) == [("messagebox", "showerror", "権限がありません", "この操作はadmin役割の作業者のみ実行できます。")]
    assert _loading_windows(host) == []
