"""scripts/check_db_versions.py（他のPCへ移る前に、DB が新しいアプリで開けるかを読み取り専用で調べる）。"""
import importlib.util
import os
import sqlite3

from db.init_db import init_database_at

_SPEC = importlib.util.spec_from_file_location(
    "check_db_versions", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "check_db_versions.py"))
cdv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cdv)


def _set_version(path, version):
    with sqlite3.connect(path) as con:
        con.execute(f"PRAGMA user_version = {version}")


def test_check_folder(tmp_path):
    root = tmp_path / "db"
    init_database_at(str(root / "A_latest" / "inventory.db"))
    init_database_at(str(root / "B_unversioned" / "inventory.db"))
    _set_version(root / "B_unversioned" / "inventory.db", 0)
    init_database_at(str(root / "C_too_new" / "inventory.db"))
    _set_version(root / "C_too_new" / "inventory.db", 99)
    (root / "D_legacy").mkdir()
    with sqlite3.connect(root / "D_legacy" / "inventory.db") as con:
        con.execute("CREATE TABLE production_daily (prod_log_id INTEGER PRIMARY KEY)")
    (root / "E_empty_folder").mkdir()
    init_database_at(str(root / "inventory.db"))           # 既定DB は調べない
    before = {p: os.path.getmtime(p) for p in root.rglob("inventory.db")}

    results = [(os.path.basename(os.path.dirname(p)), ok, msg) for p, ok, msg in cdv.check_folder(str(root))]
    assert [(name, ok) for name, ok, _ in results] == [
        ("A_latest", True), ("B_unversioned", True), ("C_too_new", False), ("D_legacy", False),
    ]
    assert results[0][2] == "開ける: 版 14（最新）"
    assert results[1][2] == "開ける: 版の記録なし。開くと 13 を書き、14 までマイグレーションを適用する（自動でバックアップを取る）"
    assert "アプリを最新版に更新する" in results[2][2]
    assert "古い形式" in results[3][2] and "production_daily.plan_item_id" in results[3][2]
    assert {p: os.path.getmtime(p) for p in root.rglob("inventory.db")} == before   # 書き込まない


def test_judge_with_future_migrations():
    assert cdv.judge(13, [], latest=15) == (True, "開ける: 版 13。開くと 15 までマイグレーションを適用する（自動でバックアップを取る）")
    assert cdv.judge(0, [], latest=15)[1].startswith("開ける: 版の記録なし。開くと 13 を書き、15 までマイグレーションを適用する")


def test_missing_folder(tmp_path):
    assert cdv.check_folder(str(tmp_path / "none")) == []
