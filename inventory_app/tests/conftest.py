# tests/conftest.py
"""
テスト実行時、実DB（config.DB_PATH・config.MASTER_DB_PATH）に一切書き込まないよう、
全テスト共通でconfig.APP_DATA_DIR・config.DB_PATH・config.MASTER_DB_PATHを
使い捨ての一時ディレクトリへ差し替える。

APP_DATA_DIRも合わせて差し替えるのは、DB_PATH・MASTER_DB_PATHの隔離だけでは
不十分なケースがあるため（例：services.app_settings_serviceはAPP_DATA_DIR配下の
app_settings.jsonを直接参照する。DB_PATHだけ隔離してもAPP_DATA_DIRが実環境を
指していると、そちら経由で実環境に書き込みが発生し得る）。
"""
import os
import shutil
import sys
import tempfile

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config


@pytest.fixture(autouse=True)
def isolate_real_db(monkeypatch):
    tmp_dir = tempfile.mkdtemp(prefix="inventory_app_test_")
    try:
        monkeypatch.setattr(config, "APP_DATA_DIR", tmp_dir)
        monkeypatch.setattr(config, "DB_PATH", os.path.join(tmp_dir, "db", "inventory.db"))
        monkeypatch.setattr(config, "MASTER_DB_PATH", os.path.join(tmp_dir, "db", "master.db"))
        yield
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
