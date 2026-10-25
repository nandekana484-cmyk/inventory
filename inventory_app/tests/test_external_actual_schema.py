"""現場実績CSVの表（マイグレーション 015、db/schema.sql）と models/external_actual_rows.py。"""
import sqlite3

import pytest

import config
from db import schema_version as sv
from db.init_db import init_database_at
from db.migrations import LATEST_VERSION
from db.migrations import m015_add_external_actual_tables as m015
from models import external_actual_rows as ear

OBJECTS = ("external_actual_batches", "external_actual_rows",
           "uq_external_actual_rows_pending", "idx_external_actual_rows_batch")


def _version(path):
    with sqlite3.connect(path) as con:
        return con.execute("PRAGMA user_version").fetchone()[0]


def _definitions(path):
    with sqlite3.connect(path) as con:
        return sorted(con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall())


def _as_v14(path):
    """版 14（現場実績CSVの表が無い）の DB と同じ形にする。"""
    with sqlite3.connect(path) as con:
        con.execute("DROP TABLE external_actual_rows")
        con.execute("DROP TABLE external_actual_batches")
        con.execute("PRAGMA user_version = 14")


@pytest.fixture
def month_db(tmp_path):
    path = str(tmp_path / "db" / "2026-10" / "inventory.db")
    init_database_at(path)
    config.DB_PATH = path          # conftest が終わったら戻す
    return path


def test_new_db_has_tables_and_latest_version(month_db):
    assert _version(month_db) == LATEST_VERSION == 15
    assert ear.tables_exist()
    assert ear.get_db_user_version() == 15


def test_migration_015_gives_the_same_shape_as_a_new_db(month_db, tmp_path):
    fresh = _definitions(month_db)
    _as_v14(month_db)
    other = [d for d in _definitions(month_db) if d[1] not in OBJECTS]
    assert [d for d in fresh if d[1] not in OBJECTS] == other          # 既存の表・インデックスは変わっていない

    result = sv.ensure_schema(month_db)
    assert (result["from_version"], result["to_version"], result["applied"]) == (14, 15, [15])
    assert _definitions(month_db) == fresh                              # 新しく作った形と一致する


def test_migration_015_twice_is_safe(month_db):
    before = _definitions(month_db)
    with sqlite3.connect(month_db) as con:
        m015.upgrade(con)
        m015.upgrade(con)
    assert _definitions(month_db) == before


def _batch(sha="a" * 64, exported="2026-10-20 18:00:00"):
    return {"source_file": "x.csv", "file_sha256": sha, "exported_at": exported,
            "source_db_name": "2026-10", "target_db_name": "2026-10"}


def _row(kitting="0001-1-A-261001-01", lot="100001", qty=10, exported="2026-10-20 18:00:00", **extra):
    row = {"csv_row_no": 2, "kitting_list_no": kitting, "lot_no": lot, "file_no": "0001", "side": "1",
           "report_date": "2026-10-20", "daily_qty": qty, "ng_side1": 0, "ng_side2": 0, "worker_id": "W1",
           "exported_at": exported}
    row.update(extra)
    return row


def test_resend_replaces_pending_row_and_keeps_registered(month_db):
    first = ear.import_batch(_batch("1" * 64), [_row(qty=10), _row(kitting="K2", qty=5)], "admin")
    assert first["replaced"] == []
    pending = ear.list_pending_rows()
    assert [r["daily_qty"] for r in pending] == [10, 5] and pending[0]["target_db_name"] == "2026-10"

    assert ear.mark_row_registered(pending[0]["row_id"], "admin") is True
    assert ear.mark_row_registered(pending[0]["row_id"], "admin") is False      # 2回目は何も変えない

    second = ear.import_batch(_batch("2" * 64), [_row(qty=12), _row(kitting="K2", qty=6)], "admin")
    assert second["replaced"] == [("K2", "100001")]                             # 登録済みの行は置き換え扱いにならない
    assert sorted((r["kitting_list_no"], r["daily_qty"]) for r in ear.list_pending_rows()) == [
        ("0001-1-A-261001-01", 12), ("K2", 6)]
    with sqlite3.connect(month_db) as con:
        assert con.execute("SELECT COUNT(*) FROM external_actual_rows WHERE status='registered'").fetchone()[0] == 1


def test_partial_unique_index_allows_only_one_pending_per_plan(month_db):
    ear.import_batch(_batch(), [_row()], "admin")
    with sqlite3.connect(month_db) as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO external_actual_rows (batch_id, csv_row_no, kitting_list_no, lot_no, file_no, side, "
                        "report_date, daily_qty, worker_id, exported_at) VALUES (1, 3, ?, '100001', '0001', '1', "
                        "'2026-10-20', 1, 'W1', 'x')", ("0001-1-A-261001-01",))


def test_import_batch_is_all_or_nothing(month_db):
    bad = _row(kitting="K9", side="3")                                          # CHECK 違反
    with pytest.raises(sqlite3.IntegrityError):
        ear.import_batch(_batch(), [_row(), bad], "admin")
    assert ear.list_pending_rows() == [] and ear.find_batch_by_sha256("a" * 64) is None


def test_latest_exported_at_covers_pending_and_registered(month_db):
    ear.import_batch(_batch(), [_row(exported="2026-10-20 18:00:00")], "admin")
    row_id = ear.list_pending_rows()[0]["row_id"]
    ear.mark_row_registered(row_id, "admin")
    ear.import_batch(_batch("3" * 64), [_row(exported="2026-10-21 09:00:00")], "admin")
    key = ("0001-1-A-261001-01", "100001")
    assert ear.get_latest_exported_at_by_plan([key, ("none", "none")]) == {key: "2026-10-21 09:00:00"}


def test_count_pending_rows_without_tables_is_zero(month_db):
    _as_v14(month_db)
    assert ear.count_pending_rows() == 0
    assert ear.tables_exist() is False
