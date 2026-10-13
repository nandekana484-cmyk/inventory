"""
メイン画面の DB 操作（DB フォルダの一覧・切り替え・新規作成（引き継ぎなし）・バックアップからの復元・削除）が、
DB とファイルに対して行うことを固定する（段階7a）。すべて一時フォルダ（tests/conftest.py の APP_DATA_DIR）の中で行い、
本物の db/ には触れない。画面は空の Toplevel に MainWindow のメソッドを結び付けて呼び、ダイアログは記録するだけにする。
ロックは本物（services.db_lock_service）を使う。
"""
import os
import sqlite3
import tkinter as tk
import tkinter.filedialog
import tkinter.messagebox
import tkinter.simpledialog
import types

import pytest

import config
from db.init_db import init_database_at
from models.db_lifecycle_log import list_db_lifecycle_log
from models.kitting_plan import init_kitting_plan_tables
from models.operation_log import list_operation_log
from services.db_lock_service import acquire_lock, get_lock_info
from ui import main_window as mw


class _Host(tk.Toplevel):
    """空の Toplevel。定義していないメソッドは MainWindow のものを結び付けて使う（staticmethod・classmethod も）。"""
    def __getattr__(self, name):
        attr = mw.MainWindow.__dict__.get(name)
        if isinstance(attr, types.FunctionType):
            return types.MethodType(attr, self)
        if isinstance(attr, staticmethod):
            return attr.__func__
        if isinstance(attr, classmethod):
            return types.MethodType(attr.__func__, type(self))
        raise AttributeError(name)


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combobox:
    def __init__(self):
        self.values = None
        self.current_index = None

    def __setitem__(self, key, value):
        assert key == "values"
        self.values = value

    def current(self, index):
        self.current_index = index


def _db_path(folder):
    return os.path.join(config.APP_DATA_DIR, "db", folder, "inventory.db")


def _make_db(folder, with_plan_tables=True):
    path = _db_path(folder)
    init_database_at(path)
    if with_plan_tables:
        saved = config.DB_PATH
        config.DB_PATH = path
        try:
            init_kitting_plan_tables()
        finally:
            config.DB_PATH = saved
    return path


def _tables(path):
    with sqlite3.connect(path) as con:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _lifecycle():
    return [(r["operation_type"], r["target_db_folder"], r["worker_name"], r["source_info"]) for r in list_db_lifecycle_log()]


def _operation_log_of(path):
    saved = config.DB_PATH
    config.DB_PATH = path
    try:
        return [(r["operation_name"], r["detail"]) for r in list_operation_log()]
    finally:
        config.DB_PATH = saved


@pytest.fixture
def host(tk_root, monkeypatch):
    events = []
    answers = {"askyesno": [], "askstring": [], "askopenfilename": []}
    h = _Host(tk_root)
    h.geometry("+3000+3000")
    h.events = events
    h.answers = answers
    h.current_worker = {"worker_id": "W1", "name": "作業者A", "role": "admin"}
    h._worker_name = "作業者A"
    h._pc_name = "PC1"
    h._lock_acquired = False
    h.db_folder_var = _Var("")
    h.new_db_folder_var = _Var("")
    h.carry_over_var = _Var(False)
    h.db_folder_combobox = _Combobox()
    h._has_open_child_windows = lambda: False
    h._update_current_db_label = lambda: events.append(("db_label", config.DB_PATH))
    h._set_menu_enabled = lambda enabled: events.append(("menu", enabled))

    def record(name):
        def fake(*args, **kwargs):
            title, message = (args + (None, None))[:2]
            events.append(("messagebox", name, title, message))
            if name == "askyesno":
                return answers["askyesno"].pop(0) if answers["askyesno"] else True
            return None
        return fake

    for name in ("showinfo", "showwarning", "showerror", "askyesno"):
        monkeypatch.setattr(tkinter.messagebox, name, record(name))
    monkeypatch.setattr(tkinter.simpledialog, "askstring",
                        lambda *a, **kw: answers["askstring"].pop(0) if answers["askstring"] else kw.get("initialvalue"))
    monkeypatch.setattr(tkinter.filedialog, "askopenfilename",
                        lambda **kw: answers["askopenfilename"].pop(0) if answers["askopenfilename"] else "")
    yield h
    for child in h.winfo_children():
        child.destroy()
    h.destroy()


def _messages(host):
    return [e[1:] for e in host.events if e[0] == "messagebox"]


@pytest.fixture
def current_db(monkeypatch):
    """今の DB（db/CUR/inventory.db）。ロックは持っていない状態から始める。"""
    path = _make_db("CUR")
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


# ---------------------------------------------------------------- DB フォルダの一覧

def test_load_db_folders(host):
    _make_db("B")
    _make_db("A")
    os.makedirs(os.path.join(config.APP_DATA_DIR, "db", "EMPTY"))      # inventory.db の無いフォルダは出さない
    with open(os.path.join(config.APP_DATA_DIR, "db", "inventory.db"), "w"):
        pass                                                            # 既定DB（フォルダではない）
    host._load_db_folders()
    assert host.db_folder_combobox.values == ["A", "B"]
    assert host.db_folder_combobox.current_index == 0


def test_load_db_folders_without_db_root(host):
    host._load_db_folders()
    assert host.db_folder_combobox.values == []
    assert host.db_folder_combobox.current_index is None


# ---------------------------------------------------------------- 切り替え

def test_switch_database(host, current_db):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    target = _make_db("NEXT")
    host.db_folder_var.set("NEXT")
    host.on_switch_database()
    assert config.DB_PATH == target
    assert host._lock_acquired is True
    assert get_lock_info(target)["worker_name"] == "作業者A"
    assert get_lock_info(current_db) is None                           # 前の DB のロックは放す
    assert ("db_label", target) in host.events
    assert _messages(host) == [("showinfo", "完了", "データベースを切り替えました。")]


def test_switch_database_same_or_empty(host, current_db):
    host.db_folder_var.set("CUR")
    host.on_switch_database()
    host.db_folder_var.set("  ")
    host.on_switch_database()
    assert _messages(host) == [
        ("showinfo", "情報", "既にこのデータベースを使用中です。"),
        ("showwarning", "警告", "切り替え先のフォルダを選択してください。"),
    ]
    assert config.DB_PATH == current_db


def test_switch_database_locked_by_other(host, current_db):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    target = _make_db("BUSY")
    acquire_lock(target, "他の人", "PC9")
    host.db_folder_var.set("BUSY")
    host.on_switch_database()
    assert config.DB_PATH == current_db                               # 切り替えない
    assert get_lock_info(current_db)["worker_name"] == "作業者A"       # 今のロックも放さない
    assert get_lock_info(target)["worker_name"] == "他の人"
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "切替不可")
    assert message.startswith("このデータベースは他の利用者が使用中です。\n\n作業者：他の人\nPC：PC9\n")


def test_switch_database_corrupted_lock_asks_then_forces(host, current_db):
    target = _make_db("BROKEN")
    with open(target + ".lock", "w", encoding="utf-8") as f:
        f.write("{not json")
    host.db_folder_var.set("BROKEN")
    host.answers["askyesno"] = [False]
    host.on_switch_database()
    assert config.DB_PATH == current_db
    assert [m[:2] for m in _messages(host)] == [("askyesno", "ロックファイル異常"), ("showerror", "切替不可")]
    host.events.clear()
    host.answers["askyesno"] = [True]
    host.on_switch_database()
    assert config.DB_PATH == target
    assert get_lock_info(target)["worker_name"] == "作業者A"
    assert [m[:2] for m in _messages(host)] == [("askyesno", "ロックファイル異常"), ("showinfo", "完了")]


# ---------------------------------------------------------------- 新規作成（引き継ぎなし）

def test_create_database_without_carry_over(host, current_db):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    host.new_db_folder_var.set(" NEW ")
    host.on_create_database()
    new_path = _db_path("NEW")
    assert config.DB_PATH == new_path
    assert {"production_daily", "kitting_plan_items", "kitting_plan_batches"} <= _tables(new_path)
    assert get_lock_info(new_path)["worker_name"] == "作業者A"
    assert get_lock_info(current_db) is None
    assert host._lock_acquired is True
    assert _lifecycle() == [("create", "NEW", "作業者A", None)]
    assert [e[0] for e in host.events] == ["db_label", "messagebox"]
    assert _messages(host) == [("showinfo", "完了", "新しいデータベースを作成しました。")]
    assert host.db_folder_combobox.values == ["CUR", "NEW"]
    assert host.db_folder_var.get() == "NEW" and host.new_db_folder_var.get() == ""


@pytest.mark.parametrize("folder, expected", [
    ("", ("showwarning", "警告", "作成するフォルダ名を入力してください。")),
    ("DB", ("showwarning", "警告", "フォルダ名「DB」は予約されているため使用できません。")),
    ("CUR", ("showwarning", "警告", "フォルダ「CUR」のデータベースは既に存在します。")),
])
def test_create_database_rejects(host, current_db, folder, expected):
    host.new_db_folder_var.set(folder)
    host.on_create_database()
    assert _messages(host) == [expected]
    assert config.DB_PATH == current_db
    assert _lifecycle() == []


def test_create_database_locked_by_other_keeps_file(host, current_db):
    new_path = _db_path("RACE")
    os.makedirs(os.path.dirname(new_path))
    acquire_lock(new_path, "他の人", "PC9")
    host.new_db_folder_var.set("RACE")
    host.on_create_database()
    assert os.path.isfile(new_path)                                    # 作った DB ファイルは残る（以前から）
    assert config.DB_PATH == current_db
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "作成不可")
    assert message.startswith("新しいデータベースは既に他の利用者が使用中です。\n\n作業者：他の人")
    assert _lifecycle() == []


# ---------------------------------------------------------------- バックアップからの復元

@pytest.fixture
def backup_file(tmp_path):
    """月次DB のバックアップ（一時フォルダの外側の tmp_path に置く）。目印の行を1つ入れておく。"""
    path = str(tmp_path / "inventory_backup.db")
    init_database_at(path)
    saved = config.DB_PATH
    config.DB_PATH = path
    try:
        init_kitting_plan_tables()
    finally:
        config.DB_PATH = saved
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO kitting_plan_batches (source_file, imported_by, row_count) VALUES ('marker.csv', 'x', 7)")
    return path


def test_restore_from_backup(host, current_db, backup_file):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    host.answers["askopenfilename"] = [backup_file]
    host.answers["askstring"] = [" RESTORED "]
    host.on_restore_from_backup()
    new_path = _db_path("RESTORED")
    assert config.DB_PATH == new_path
    with sqlite3.connect(new_path) as con:
        assert con.execute("SELECT source_file, row_count FROM kitting_plan_batches").fetchall() == [("marker.csv", 7)]
    assert get_lock_info(new_path)["worker_name"] == "作業者A"
    assert get_lock_info(current_db) is None
    assert _operation_log_of(new_path) == [
        ("バックアップからの取り込み", f"元ファイル: {backup_file} / 取り込み先: RESTORED"),
    ]
    assert _lifecycle() == [("restore_from_backup", "RESTORED", "作業者A", backup_file)]
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showinfo", "取り込み完了")
    assert message.startswith("バックアップからデータベースを取り込み、切り替えました。")
    assert host.db_folder_combobox.values == ["CUR", "RESTORED"]
    assert host.db_folder_var.get() == "RESTORED"


def test_restore_cancel_and_invalid(host, current_db, tmp_path):
    host.on_restore_from_backup()                                      # ファイル選択をキャンセル
    bad = tmp_path / "bad.db"
    bad.write_text("not a db" * 50, encoding="utf-8")
    host.answers["askopenfilename"] = [str(bad)]
    host.on_restore_from_backup()
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "取り込み不可")
    assert message.startswith("SQLiteデータベースとして開けませんでした：")
    assert config.DB_PATH == current_db


@pytest.mark.parametrize("answer, expected", [
    (None, None),                                                      # キャンセル: 何も出さない
    ("", None),
    ("   ", ("showwarning", "警告", "フォルダ名を入力してください。")),
    ("db", ("showwarning", "警告", "フォルダ名「db」は予約されているため使用できません。")),
    ("CUR", ("showwarning", "警告", "フォルダ「CUR」のデータベースは既に存在します。")),
])
def test_restore_folder_name_checks(host, current_db, backup_file, answer, expected):
    host.answers["askopenfilename"] = [backup_file]
    host.answers["askstring"] = [answer]
    host.on_restore_from_backup()
    assert _messages(host) == ([expected] if expected else [])
    assert config.DB_PATH == current_db
    assert _lifecycle() == []


def test_restore_failure_removes_new_folder(host, current_db, backup_file, monkeypatch):
    """コピーの途中で失敗したら、作りかけのフォルダを消してからエラーを出す。検証の後でバックアップを消して、本当に失敗させる。"""
    host.answers["askopenfilename"] = [backup_file]

    def remove_backup_then_answer(*args, **kwargs):
        os.remove(backup_file)
        return "FAIL"

    monkeypatch.setattr(tkinter.simpledialog, "askstring", remove_backup_then_answer)
    host.on_restore_from_backup()
    assert not os.path.exists(os.path.join(config.APP_DATA_DIR, "db", "FAIL"))
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "取り込みエラー")
    assert message.startswith("バックアップの取り込み中にエラーが発生しました：\n")
    assert config.DB_PATH == current_db
    assert _lifecycle() == []


def test_restore_lock_failure_removes_new_folder(host, current_db, backup_file):
    """コピーした DB のロックを他の人が持っていたら（先にロックファイルだけがある）、切り替えずにフォルダを消す。"""
    new_path = _db_path("LOCKED")
    os.makedirs(os.path.dirname(new_path))
    acquire_lock(new_path, "他の人", "PC9")
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    host.answers["askopenfilename"] = [backup_file]
    host.answers["askstring"] = ["LOCKED"]
    host.on_restore_from_backup()
    assert not os.path.exists(os.path.join(config.APP_DATA_DIR, "db", "LOCKED"))
    assert config.DB_PATH == current_db
    assert get_lock_info(current_db)["worker_name"] == "作業者A"
    assert [m[:2] for m in _messages(host)] == [("showerror", "切替不可")]
    assert _lifecycle() == []


# ---------------------------------------------------------------- 削除

def test_delete_database(host, current_db):
    target = _make_db("OLD")
    with open(target + ".lock.tmp", "w"):
        pass
    host.db_folder_var.set("OLD")
    host.on_delete_local_database()
    assert not os.path.exists(target) and not os.path.exists(target + ".lock.tmp")
    assert os.path.isdir(os.path.dirname(target))                     # フォルダ自体は残す
    assert _operation_log_of(current_db) == [("月別DB削除", target)]  # 今の DB の操作履歴に残す
    assert _lifecycle() == [("delete", "OLD", "作業者A", None)]
    assert [m[:2] for m in _messages(host)] == [("askyesno", "確認"), ("showinfo", "削除完了")]
    assert host.db_folder_combobox.values == ["CUR"]
    assert ("db_label", current_db) in host.events


def test_delete_current_database_is_refused(host, current_db):
    host.db_folder_var.set("CUR")
    host.on_delete_local_database()
    assert os.path.exists(current_db)
    assert [m[:2] for m in _messages(host)] == [("showerror", "削除できません")]
    assert _lifecycle() == []


def test_delete_database_cancelled_and_locked_warning(host, current_db):
    target = _make_db("BUSY")
    acquire_lock(target, "他の人", "PC9")
    host.db_folder_var.set("BUSY")
    host.answers["askyesno"] = [False]
    host.on_delete_local_database()
    assert os.path.exists(target)
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("askyesno", "警告：使用中の可能性があります")
    assert "ロック保持者：他の人（PC9）" in message
    host.events.clear()
    host.db_folder_var.set("")
    host.on_delete_local_database()
    assert _messages(host) == [("showwarning", "警告", "削除するフォルダを選択してください。")]
