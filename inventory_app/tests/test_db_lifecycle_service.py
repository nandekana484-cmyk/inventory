"""services/db_lifecycle_service.py（段階7a）。画面からの使われ方は test_db_lifecycle_main_window.py で固定している。"""
import ast
import inspect
import os

import config
from services import db_lifecycle_service as svc
from services.app_settings_service import save_last_db_path


def _make_db(folder):
    path = svc.db_path_for_folder(folder)
    svc.create_db_file(path)
    return path


# ---------------------------------------------------------------- 起動時の DB パスの復元（以前は MainWindow.__init__() の中）

def test_restore_last_db_path_switches_when_file_exists():
    default = config.DB_PATH
    path = _make_db("LAST")
    save_last_db_path(path)
    assert svc.restore_last_db_path() is None
    assert config.DB_PATH == path
    config.DB_PATH = default


def test_restore_last_db_path_returns_missing_path():
    default = config.DB_PATH
    missing = svc.db_path_for_folder("GONE")
    save_last_db_path(missing)
    assert svc.restore_last_db_path() == missing
    assert config.DB_PATH == default          # 切り替えない（既定DB のまま）


def test_restore_last_db_path_without_record_or_same_path():
    default = config.DB_PATH
    assert svc.restore_last_db_path() is None  # 記憶が無い
    save_last_db_path(default)
    assert svc.restore_last_db_path() is None  # 今と同じ（ファイルが無くても知らせない）
    assert config.DB_PATH == default


# ---------------------------------------------------------------- フォルダ名・パス

def test_paths_and_folder_checks():
    assert svc.db_path_for_folder("A") == os.path.join(config.APP_DATA_DIR, "db", "A", "inventory.db")
    assert svc.db_folder_path("A") == os.path.join(config.APP_DATA_DIR, "db", "A")
    _make_db("A")
    assert svc.check_new_db_folder("") == svc.NEW_FOLDER_EMPTY
    assert svc.check_new_db_folder("Db") == svc.NEW_FOLDER_RESERVED
    assert svc.check_new_db_folder("A") == svc.NEW_FOLDER_EXISTS
    assert svc.check_new_db_folder("B") is None
    assert svc.list_db_folders() == ["A"]


def test_service_does_not_import_ui_or_gui():
    tree = ast.parse(inspect.getsource(svc))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    assert not modules & {"ui", "tkinter", "tkcalendar"}


# ---------------------------------------------------------------- 段階7b: 新規作成した DB の版

def test_new_db_is_stamped_latest_version():
    import sqlite3
    from db.migrations import LATEST_VERSION
    from db.schema_version import ensure_schema
    path = _make_db("NEWVER")
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == LATEST_VERSION == 15
    result = ensure_schema(path)          # 開くときの確認では何もしない
    assert result["stamped_baseline"] is False and result["applied"] == [] and result["backup_path"] is None
