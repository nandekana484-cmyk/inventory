"""
DB を開くときの形の確認とマイグレーションの適用（段階7b）を、メイン画面の入口（切り替え・取り込み・起動）から確かめる。
画面の作り方と一時フォルダの DB は test_db_lifecycle_main_window.py と同じ。適用の場面の多くは、
テスト専用の 014（tests/schema_migrations/）を本番のもの（D-145 の 014）の代わりに差し込む。
"""
import os
import sqlite3
import types

import pytest

import config
from db import schema_version as sv
from db.migrations import LATEST_VERSION
from services.db_lock_service import acquire_lock, get_lock_info
from tests.schema_migrations import m014_add_test_dummy_column as m014
from tests.test_db_lifecycle_main_window import (  # noqa: F401 （fixture）
    host, current_db, _make_db, _messages, _db_path,
)


def _version(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA user_version").fetchone()[0]
    finally:
        con.close()


def _set_version(path, version):
    con = sqlite3.connect(path)
    try:
        con.execute(f"PRAGMA user_version = {version}")
        con.commit()
    finally:
        con.close()


def _columns(path, table):
    con = sqlite3.connect(path)
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    finally:
        con.close()


@pytest.fixture
def with_test_014(monkeypatch):
    """本番のマイグレーションの代わりに、テスト専用の 014 を使う（最新＝14）。"""
    monkeypatch.setattr(sv, "load_migrations", lambda: [m014])
    monkeypatch.setattr(sv, "LATEST_VERSION", 14)


def _failing_015():
    def upgrade(con):
        raise RuntimeError("015 で失敗")
    return types.SimpleNamespace(VERSION=15, upgrade=upgrade)


@pytest.fixture
def with_failing_015(monkeypatch):
    monkeypatch.setattr(sv, "load_migrations", lambda: [m014, _failing_015()])
    monkeypatch.setattr(sv, "LATEST_VERSION", 15)


def _legacy(path):
    """版の記録が無く、基準（13）の列も足りない DB（production_daily に plan_item_id が無い）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE production_daily (prod_log_id INTEGER PRIMARY KEY, report_date TEXT, kitting_list_no TEXT)")


# ---------------------------------------------------------------- 切り替え

def _switch(host, folder):
    host.db_folder_var.set(folder)
    host.on_switch_database()


def test_switch_to_current_version_db_has_no_extra_dialog(host, current_db):
    target = _make_db("V13")
    _switch(host, "V13")
    assert config.DB_PATH == target
    assert _messages(host) == [("showinfo", "完了", "データベースを切り替えました。")]
    assert _version(target) == LATEST_VERSION


def test_switch_to_unversioned_baseline_db_stamps_13(host, current_db):
    """版の記録が無い基準の形の DB は、13 を書いてから本番のマイグレーション（014〜）を当てる（D-145 で 014 が入った）。"""
    target = _make_db("V0")
    _set_version(target, 0)
    _switch(host, "V0")
    assert config.DB_PATH == target
    assert _version(target) == LATEST_VERSION == 14
    backups = sv.list_migration_backups(target)
    assert len(backups) == 1 and ".bak_v13_to_v14_" in backups[0]
    (m1, m2) = [m[2] for m in _messages(host)]
    assert m1.startswith("データベースを新しい形に更新しました（版 13 → 14）。") and backups[0] in m1
    assert m2 == "データベースを切り替えました。"


def test_switch_applies_migration_with_backup(host, current_db, with_test_014):
    target = _make_db("OLD13")
    _create_ng_declarations(target)
    _set_version(target, 13)
    _switch(host, "OLD13")
    assert config.DB_PATH == target
    assert _version(target) == 14
    assert "test_dummy" in _columns(target, "ng_declarations")
    backups = sv.list_migration_backups(target)
    assert len(backups) == 1 and ".bak_v13_to_v14_" in backups[0]
    (k1, t1, m1), (k2, t2, m2) = _messages(host)
    assert (k1, t1) == ("showinfo", "データベースの更新")
    assert m1.startswith("データベースを新しい形に更新しました（版 13 → 14）。") and backups[0] in m1
    assert (k2, t2, m2) == ("showinfo", "完了", "データベースを切り替えました。")


def _create_ng_declarations(path):
    """テスト専用の 014 が列を足す表を、先に作っておく（初めて使ったときに作られる表なので）。"""
    from models.ng_declarations import init_ng_declarations_table
    saved = config.DB_PATH
    config.DB_PATH = path
    try:
        init_ng_declarations_table()
    finally:
        config.DB_PATH = saved


def _tables(path):
    con = sqlite3.connect(path)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _assert_not_switched(host, current_db, target):
    assert config.DB_PATH == current_db
    assert get_lock_info(target) is None                      # 切り替え先のロックは放す
    assert get_lock_info(current_db)["worker_name"] == "作業者A"  # 今のロックはそのまま
    assert host._lock_acquired is True


def test_switch_to_legacy_db_is_refused(host, current_db):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    target = _db_path("LEGACY")
    _legacy(target)
    _switch(host, "LEGACY")
    _assert_not_switched(host, current_db, target)
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "データベースを開けません")
    assert message.startswith("このデータベースは古い形式のため開けません。新しいデータベースを作成し直してください。")
    assert "・production_daily.plan_item_id" in message
    assert message.endswith("切り替えずに、今のデータベースのまま続けます。")
    assert _version(target) == 0


def test_switch_to_too_new_db_asks_to_update_app(host, current_db):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    target = _make_db("NEWER")
    _set_version(target, 99)
    _switch(host, "NEWER")
    _assert_not_switched(host, current_db, target)
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "データベースを開けません")
    assert f"（データベースの版：99／このアプリが扱える版：{LATEST_VERSION}）" in message
    assert "アプリを最新版に更新してください。" in message
    assert _version(target) == 99


def test_switch_migration_failure_keeps_current_db(host, current_db, with_failing_015):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    target = _make_db("FAILS")
    _set_version(target, 13)
    _switch(host, "FAILS")
    _assert_not_switched(host, current_db, target)
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "データベースの更新に失敗")
    assert message.startswith("データベースを新しい形に更新できませんでした（版 15 の適用中）。\nこのデータベースは版 14 まで更新されています。")
    backups = sv.list_migration_backups(target)
    assert len(backups) == 1 and backups[0] in message
    assert "詳細：015 で失敗" in message
    assert _version(target) == 14


# ---------------------------------------------------------------- バックアップからの取り込み

from tests.test_db_lifecycle_main_window import backup_file, _lifecycle  # noqa: E402,F401 （fixture）


def _restore(host, backup_path, folder):
    host.answers["askopenfilename"] = [backup_path]
    host.answers["askstring"] = [folder]
    host.on_restore_from_backup()


def test_restore_current_version_backup(host, current_db, backup_file):
    _restore(host, backup_file, "R13")
    target = _db_path("R13")
    assert config.DB_PATH == target
    assert _version(target) == LATEST_VERSION
    assert [m[:2] for m in _messages(host)] == [("showinfo", "取り込み完了")]


def test_restore_applies_migration_to_the_copy_only(host, current_db, backup_file, with_test_014):
    _create_ng_declarations(backup_file)
    _set_version(backup_file, 13)
    _restore(host, backup_file, "R14")
    target = _db_path("R14")
    assert config.DB_PATH == target
    assert _version(target) == 14 and "test_dummy" in _columns(target, "ng_declarations")
    assert _version(backup_file) == 13 and "test_dummy" not in _columns(backup_file, "ng_declarations")  # 原本は変えない
    assert [m[:2] for m in _messages(host)] == [("showinfo", "データベースの更新"), ("showinfo", "取り込み完了")]
    assert len(sv.list_migration_backups(target)) == 1


def test_restore_legacy_backup_removes_folder(host, current_db, tmp_path):
    acquire_lock(current_db, "作業者A", "PC1")
    host._lock_acquired = True
    legacy = str(tmp_path / "legacy_backup.db")
    with sqlite3.connect(legacy) as con:
        con.execute("CREATE TABLE production_daily (prod_log_id INTEGER PRIMARY KEY, report_date TEXT)")
        con.execute("CREATE TABLE kitting_plan_items (id INTEGER PRIMARY KEY, kitting_list_no TEXT)")
    _restore(host, legacy, "RLEGACY")
    assert not os.path.exists(os.path.join(config.APP_DATA_DIR, "db", "RLEGACY"))
    assert config.DB_PATH == current_db and get_lock_info(current_db)["worker_name"] == "作業者A"
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "データベースを開けません")
    assert message.endswith("取り込んだデータベース（このフォルダの中のバックアップを含む）は削除しました。元のバックアップのファイルは変わっていません。")
    assert _lifecycle() == []


def test_restore_too_new_backup_removes_folder(host, current_db, backup_file):
    _set_version(backup_file, 20)
    _restore(host, backup_file, "RNEW")
    assert not os.path.exists(os.path.join(config.APP_DATA_DIR, "db", "RNEW"))
    assert config.DB_PATH == current_db
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "データベースを開けません")
    assert "アプリを最新版に更新してください。" in message


def test_restore_migration_failure_removes_folder(host, current_db, backup_file, with_failing_015):
    _set_version(backup_file, 13)
    _restore(host, backup_file, "RFAIL")
    assert not os.path.exists(os.path.join(config.APP_DATA_DIR, "db", "RFAIL"))
    assert config.DB_PATH == current_db
    assert [m[:2] for m in _messages(host)] == [("showerror", "データベースの更新に失敗")]
    assert _version(backup_file) == 13


# ---------------------------------------------------------------- 起動時のつなぎ直し

from services.app_settings_service import load_last_db_path, save_last_db_path  # noqa: E402


@pytest.fixture
def default_db(monkeypatch):
    """起動したときの状態（config.DB_PATH は既定DB）。"""
    path = os.path.join(config.APP_DATA_DIR, "db", "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def _startup(host):
    ok = host._connect_db_at_startup()
    host._report_startup_db_problems()
    return ok


def test_startup_reconnects_to_current_version_db(host, default_db):
    last = _make_db("LAST")
    save_last_db_path(last)
    assert _startup(host) is True
    assert config.DB_PATH == last and host._lock_acquired is True
    assert get_lock_info(last)["worker_name"] == "作業者A"
    assert _messages(host) == []


def test_startup_without_last_db_stays_default(host, default_db):
    assert _startup(host) is True
    assert config.DB_PATH == default_db and host._lock_acquired is False
    assert not os.path.exists(default_db + ".lock")
    assert _messages(host) == []


def test_startup_missing_last_db_warns_as_before(host, default_db):
    missing = _db_path("GONE")
    save_last_db_path(missing)
    assert _startup(host) is True
    assert config.DB_PATH == default_db
    assert _messages(host) == [("showwarning", "データベース接続の復元に失敗",
                                f"前回使用していたデータベースが見つかりませんでした：\n{missing}\n\n"
                                "デフォルトのローカルデータベースに接続しました。")]


def test_startup_locked_by_other_stops(host, default_db):
    last = _make_db("BUSY")
    acquire_lock(last, "他の人", "PC9")
    save_last_db_path(last)
    assert host._connect_db_at_startup() is False
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showerror", "データベース使用中")
    assert message.startswith("このデータベースは他の利用者が使用中のため起動できません。\n\n作業者：他の人")


def test_startup_applies_migration_and_reports_after_ui(host, default_db, with_test_014):
    last = _make_db("OLDLAST")
    _create_ng_declarations(last)
    _set_version(last, 13)
    save_last_db_path(last)
    assert host._connect_db_at_startup() is True
    assert _messages(host) == []                                    # UI を作り終えるまでは出さない
    host._report_startup_db_problems()
    assert config.DB_PATH == last and _version(last) == 14
    (kind, title, message), = _messages(host)
    assert (kind, title) == ("showinfo", "データベースの更新")
    assert message.startswith("データベースを新しい形に更新しました（版 13 → 14）。")


@pytest.mark.parametrize("make_problem, title, phrase", [
    (lambda path: _legacy(path), "データベースを開けません", "古い形式のため開けません"),
    (lambda path: (_make_db("TOONEW"), _set_version(path, 50)), "データベースを開けません", "アプリを最新版に更新してください。"),
])
def test_startup_unopenable_db_falls_back_to_default(host, default_db, make_problem, title, phrase):
    folder = "TOONEW" if "アプリ" in phrase else "OLDFMT"
    last = _db_path(folder)
    make_problem(last)
    save_last_db_path(last)
    assert host._connect_db_at_startup() is True
    assert config.DB_PATH == default_db                              # 既定DB（未選択）で起動する
    assert host._lock_acquired is False and get_lock_info(last) is None
    assert load_last_db_path() == default_db                         # 次の起動も未選択から
    assert _messages(host) == []
    host._report_startup_db_problems()
    (kind, got_title, message), = _messages(host)
    assert (kind, got_title) == ("showerror", title)
    assert phrase in message
    assert message.endswith("データベースを選択していない状態で起動しました。データベースを選び直すか、新しく作成してください。")


def test_startup_migration_failure_falls_back_to_default(host, default_db, with_failing_015):
    last = _make_db("FAILLAST")
    _set_version(last, 13)
    save_last_db_path(last)
    host._connect_db_at_startup()
    host._report_startup_db_problems()
    assert config.DB_PATH == default_db and get_lock_info(last) is None
    assert [m[:2] for m in _messages(host)] == [("showerror", "データベースの更新に失敗")]
    assert _version(last) == 14
