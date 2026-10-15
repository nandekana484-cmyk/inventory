"""
db/schema_version.py（月次DB の版の確認とマイグレーションの適用、段階7b）。
マイグレーションは、テスト専用の 014（tests/schema_migrations/m014_add_test_dummy_column.py）と、このファイルの中の偽物を使う。
本番の db/migrations/ にはまだマイグレーションが無い（最新＝基準＝13）。
"""
import os
import sqlite3
import types
from datetime import datetime

import pytest

import config
from db import migrations as production_migrations
from db import schema_version as sv
from db.init_db import init_database_at
from tests.schema_migrations import m014_add_test_dummy_column as m014

# 月次DB の表を作る関数（初めて使ったときに作られる表も含め、すべて作る）。master.db の表は入れない。
from models.bom_master import init_bom_master_table
from models.inventory import init_inventory_stock_table
from models.kitting_plan import init_kitting_plan_tables
from models.lot_status_history import init_lot_status_history_table
from models.ng_declarations import init_ng_declarations_table
from models.ng_exclusion_list import init_ng_exclusion_list_table
from models.operation_log import init_operation_log_table
from models.production_import_staging import init_csv_import_staging_tables
from models.scrap_records import init_scrap_records_table
from models.theoretical_inventory import init_theoretical_inventory_table
from models.wip_board_snapshot import init_wip_board_snapshot_table
from models.wip_exclusion_list import init_wip_exclusion_list_table
from models.wip_scrap_records import init_wip_scrap_records_table

MONTHLY_TABLE_INITS = (
    init_kitting_plan_tables, init_bom_master_table, init_inventory_stock_table, init_lot_status_history_table,
    init_ng_declarations_table, init_ng_exclusion_list_table, init_operation_log_table, init_csv_import_staging_tables,
    init_scrap_records_table, init_theoretical_inventory_table, init_wip_board_snapshot_table,
    init_wip_exclusion_list_table, init_wip_scrap_records_table,
)
NOW = datetime(2026, 10, 13, 9, 0, 0)


def _full_db(path, monkeypatch):
    """新しく作った DB に、月次DB のすべての表を作る（init_database_at() → 各 init_*()）。"""
    init_database_at(path)
    monkeypatch.setattr(config, "DB_PATH", path)
    for init in MONTHLY_TABLE_INITS:
        init()
    return path


def _schema(path):
    """表ごとの列（cid・名前・型・NOT NULL・既定値・主キー）とインデックス（名前・UNIQUE・列）。"""
    con = sqlite3.connect(path)
    try:
        result = {}
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            columns = [tuple(r) for r in con.execute(f"PRAGMA table_info({table})")]
            indexes = []
            for index in con.execute(f"PRAGMA index_list({table})").fetchall():
                cols = [r[2] for r in con.execute(f"PRAGMA index_info('{index[1]}')")]
                indexes.append((index[1], index[2], tuple(cols)))
            result[table] = (columns, sorted(indexes))
        return result
    finally:
        con.close()


def _version(path):
    con = sqlite3.connect(path)
    try:
        return sv.read_user_version(con)
    finally:
        con.close()


def _set_version(path, version):
    con = sqlite3.connect(path)
    try:
        sv.write_user_version(con, version)
        con.commit()
    finally:
        con.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    return _full_db(str(tmp_path / "m" / "inventory.db"), monkeypatch)


# ---------------------------------------------------------------- 本番のマイグレーション

def test_production_migrations():
    assert production_migrations.BASELINE_VERSION == 13
    assert production_migrations.LATEST_VERSION == 14
    assert [m.__name__ for m in production_migrations.load_migrations()] == ["db.migrations.m014_add_lot_merges"]


def test_validate_migrations_detects_gaps():
    m016 = types.SimpleNamespace(VERSION=16)
    with pytest.raises(ValueError):
        production_migrations.validate_migrations([m014, m016], 16)
    with pytest.raises(ValueError):
        production_migrations.validate_migrations([m014], 15)
    production_migrations.validate_migrations([m014], 14)


# ---------------------------------------------------------------- 基準（13）の確認

def test_new_full_db_passes_baseline_and_is_stamped_13(db_path):
    assert sv.baseline_problems(sqlite3.connect(db_path)) == []
    _set_version(db_path, 0)
    result = sv.ensure_schema(db_path, [], 13)        # 基準の確認だけを見るため、マイグレーションは無しにする
    assert result == {"from_version": 13, "to_version": 13, "applied": [], "stamped_baseline": True,
                      "backup_path": None, "deleted_backups": []}
    assert _version(db_path) == 13
    assert sv.list_migration_backups(db_path) == []


def test_minimal_db_passes_baseline(tmp_path):
    """init_database_at() だけの DB（初めて使ったときに作る表がまだ無い）も基準を満たす。"""
    path = str(tmp_path / "inventory.db")
    init_database_at(path)
    _set_version(path, 0)
    assert sv.ensure_schema(path)["stamped_baseline"] is True


def _legacy_db(tmp_path):
    """002 より前の形（production_daily に plan_item_id・kitting_list_no が無い、scrap_records に lot_no が無い）。"""
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE production_daily (prod_log_id INTEGER PRIMARY KEY, report_date TEXT)")
        con.execute("CREATE TABLE scrap_records (id INTEGER PRIMARY KEY, kitting_list_no TEXT, is_unplanned INTEGER)")
        con.execute("CREATE TABLE kitting_plan_items (id INTEGER PRIMARY KEY, kitting_list_no TEXT UNIQUE, lot_no TEXT,"
                    " version INTEGER, is_active INTEGER, previous_plan_item_id INTEGER, created_by TEXT, created_at TEXT)")
    return path


def test_legacy_db_is_refused_without_changes(tmp_path):
    path = _legacy_db(tmp_path)
    with pytest.raises(sv.LegacyDatabaseError) as info:
        sv.ensure_schema(path, [m014], 14, now=NOW)
    assert info.value.problems == [
        "production_daily.plan_item_id", "production_daily.kitting_list_no", "scrap_records.lot_no",
        "kitting_plan_items の kitting_list_no だけの UNIQUE（005 が未適用）",
    ]
    assert _version(path) == 0
    assert sv.list_migration_backups(path) == []


def test_missing_required_table_is_reported(tmp_path):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()
    with pytest.raises(sv.LegacyDatabaseError) as info:
        sv.ensure_schema(path)
    assert info.value.problems == ["production_daily（表が無い）"]


def test_check_version_readonly_does_not_write(tmp_path):
    path = _legacy_db(tmp_path)
    before = os.path.getmtime(path), os.path.getsize(path)
    version, problems = sv.check_version_readonly(path)
    assert version == 0 and len(problems) == 4
    assert (os.path.getmtime(path), os.path.getsize(path)) == before


# ---------------------------------------------------------------- 適用（テスト専用の 014）

def test_apply_014_from_13(db_path):
    _set_version(db_path, 13)
    result = sv.ensure_schema(db_path, [m014], 14, now=NOW)
    assert result["from_version"] == 13 and result["to_version"] == 14 and result["applied"] == [14]
    assert result["backup_path"] == db_path + ".bak_v13_to_v14_20261013_090000"
    assert _version(db_path) == 14
    assert "test_dummy" in [r[1] for r in sqlite3.connect(db_path).execute("PRAGMA table_info(ng_declarations)")]
    # バックアップは適用の前の形（版 13・列なし）
    assert _version(result["backup_path"]) == 13
    assert "test_dummy" not in [r[1] for r in sqlite3.connect(result["backup_path"]).execute("PRAGMA table_info(ng_declarations)")]
    # 2回目は何もしない（バックアップも取らない）
    again = sv.ensure_schema(db_path, [m014], 14, now=NOW)
    assert again["applied"] == [] and again["backup_path"] is None
    assert sv.list_migration_backups(db_path) == [result["backup_path"]]


def test_apply_014_from_0_stamps_baseline_first(db_path):
    _set_version(db_path, 0)
    result = sv.ensure_schema(db_path, [m014], 14, now=NOW)
    assert result["stamped_baseline"] is True and result["applied"] == [14]
    assert result["backup_path"].endswith(".bak_v13_to_v14_20261013_090000")


def test_014_is_idempotent_and_skips_missing_table(tmp_path, db_path):
    con = sqlite3.connect(db_path)
    m014.upgrade(con)
    m014.upgrade(con)   # 2回目は何もしない
    assert [r[1] for r in con.execute("PRAGMA table_info(ng_declarations)")].count("test_dummy") == 1
    con.close()
    minimal = str(tmp_path / "minimal.db")
    init_database_at(minimal)
    con = sqlite3.connect(minimal)
    m014.upgrade(con)   # 表が無ければ何もしない
    assert [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE name = 'ng_declarations'")] == []
    con.close()


def test_new_db_and_migrated_db_have_the_same_shape(tmp_path, monkeypatch):
    """
    手順どおり「014 と同じコミットで CREATE TABLE も直した」とき、新しく作った DB と、13 に 014 を当てた DB の形が一致する。
    新しく作る側は、ng_declarations の CREATE TABLE の末尾に 014 の列を足したものとして作る。
    """
    migrated = _full_db(str(tmp_path / "migrated" / "inventory.db"), monkeypatch)
    _set_version(migrated, 13)
    sv.ensure_schema(migrated, [m014], 14, now=NOW)

    fresh = _full_db(str(tmp_path / "fresh" / "inventory.db"), monkeypatch)
    con = sqlite3.connect(fresh)
    create_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name = 'ng_declarations'").fetchone()[0]
    index_sqls = [r[0] for r in con.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name = 'ng_declarations' AND sql IS NOT NULL")]
    new_create_sql = create_sql.rstrip().rstrip(")").rstrip() + f",\n    {m014.COLUMN_DEF}\n)"
    con.execute("DROP TABLE ng_declarations")
    con.execute(new_create_sql)
    for sql in index_sqls:
        con.execute(sql)
    sv.write_user_version(con, 14)
    con.commit()
    con.close()

    assert _schema(fresh) == _schema(migrated)
    assert _version(fresh) == _version(migrated) == 14


def test_too_new_db_is_refused(db_path):
    _set_version(db_path, 15)
    with pytest.raises(sv.DatabaseTooNewError) as info:
        sv.ensure_schema(db_path, [m014], 14, now=NOW)
    assert (info.value.db_version, info.value.app_version) == (15, 14)
    assert _version(db_path) == 15


# ---------------------------------------------------------------- 失敗したとき

def _failing_015():
    def upgrade(con):
        con.execute("ALTER TABLE production_daily ADD COLUMN half_done TEXT")
        raise RuntimeError("途中で失敗")
    return types.SimpleNamespace(VERSION=15, upgrade=upgrade)


def test_failure_rolls_back_only_the_failed_migration(db_path):
    _set_version(db_path, 13)
    with pytest.raises(sv.MigrationFailedError) as info:
        sv.ensure_schema(db_path, [m014, _failing_015()], 15, now=NOW)
    error = info.value
    assert (error.version, error.applied_version) == (15, 14)
    assert str(error.cause) == "途中で失敗"
    assert error.backup_path == db_path + ".bak_v13_to_v15_20261013_090000" and os.path.exists(error.backup_path)
    assert _version(db_path) == 14                                     # 014 は適用済み、015 は巻き戻した
    cols = [r[1] for r in sqlite3.connect(db_path).execute("PRAGMA table_info(production_daily)")]
    assert "half_done" not in cols


def test_rebuild_migration_turns_foreign_keys_off_outside_transaction(db_path):
    seen = []

    def upgrade(con):
        seen.append(con.execute("PRAGMA foreign_keys").fetchone()[0])
        con.execute("CREATE TABLE ng_exclusion_list_new AS SELECT * FROM ng_exclusion_list")
        con.execute("DROP TABLE ng_exclusion_list")
        con.execute("ALTER TABLE ng_exclusion_list_new RENAME TO ng_exclusion_list")

    m014_rebuild = types.SimpleNamespace(VERSION=14, upgrade=upgrade, REBUILDS_TABLES=True)
    _set_version(db_path, 13)
    sv.ensure_schema(db_path, [m014_rebuild], 14, now=NOW)
    assert seen == [0]
    assert _version(db_path) == 14


def test_rebuild_migration_fails_on_foreign_key_violation(db_path):
    def upgrade(con):
        con.execute("CREATE TABLE parent_x (id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE child_x (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent_x(id))")
        con.execute("INSERT INTO child_x VALUES (1, 99)")

    bad = types.SimpleNamespace(VERSION=14, upgrade=upgrade, REBUILDS_TABLES=True)
    _set_version(db_path, 13)
    with pytest.raises(sv.MigrationFailedError) as info:
        sv.ensure_schema(db_path, [bad], 14, now=NOW)
    assert "外部キーの不整合" in str(info.value.cause)
    assert _version(db_path) == 13
    assert [r[0] for r in sqlite3.connect(db_path).execute("SELECT name FROM sqlite_master WHERE name = 'child_x'")] == []


# ---------------------------------------------------------------- 自動バックアップの世代

def test_prune_keeps_three_newest_automatic_backups(db_path):
    folder = os.path.dirname(db_path)
    names = [
        "inventory.db.bak_v13_to_v14_20261001_090000",
        "inventory.db.bak_v13_to_v14_20261002_090000",
        "inventory.db.bak_v14_to_v15_20261003_090000",
        "inventory.db.bak_v14_to_v15_20261003_090000_1",
        "inventory.db.bak_v15_to_v16_20261004_090000",
        "inventory.db.bak_migration010_20260901",          # 手で取ったバックアップは消さない
        "other.db.bak_v13_to_v14_20261001_090000",         # 別の DB のものは消さない
    ]
    for name in names:
        open(os.path.join(folder, name), "w").close()
    deleted = sv.prune_migration_backups(db_path)
    assert sorted(os.path.basename(p) for p in deleted) == names[:2]
    assert [os.path.basename(p) for p in sv.list_migration_backups(db_path)] == names[2:5]
    assert os.path.exists(os.path.join(folder, names[5])) and os.path.exists(os.path.join(folder, names[6]))


def test_backup_name_gets_suffix_when_taken_in_same_second(db_path):
    _set_version(db_path, 13)
    first = db_path + ".bak_v13_to_v14_20261013_090000"
    open(first, "w").close()
    result = sv.ensure_schema(db_path, [m014], 14, now=NOW)
    assert result["backup_path"] == first + "_1"


def test_successful_apply_prunes_old_backups(db_path):
    folder = os.path.dirname(db_path)
    for day in ("01", "02", "03"):
        open(os.path.join(folder, f"inventory.db.bak_v12_to_v13_202610{day}_090000"), "w").close()
    _set_version(db_path, 13)
    result = sv.ensure_schema(db_path, [m014], 14, now=NOW)
    assert [os.path.basename(p) for p in result["deleted_backups"]] == ["inventory.db.bak_v12_to_v13_20261001_090000"]
    assert len(sv.list_migration_backups(db_path)) == 3
