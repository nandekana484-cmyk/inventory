"""
DB を開くときの形の確認とマイグレーションの適用（段階7b）を、メイン画面の入口（切り替え・取り込み・起動）から確かめる。
画面の作り方と一時フォルダの DB は test_db_lifecycle_main_window.py と同じ。本番のマイグレーションはまだ無いので、
適用の場面では、テスト専用の 014（tests/schema_migrations/）を本番のものの代わりに差し込む。
"""
import os
import sqlite3
import types

import pytest

import config
from db import schema_version as sv
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
    assert _version(target) == 13


def test_switch_to_unversioned_baseline_db_stamps_13(host, current_db):
    target = _make_db("V0")
    _set_version(target, 0)
    _switch(host, "V0")
    assert config.DB_PATH == target
    assert _version(target) == 13
    assert _messages(host) == [("showinfo", "完了", "データベースを切り替えました。")]
    assert sv.list_migration_backups(target) == []


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
    assert "（データベースの版：99／このアプリが扱える版：13）" in message
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
