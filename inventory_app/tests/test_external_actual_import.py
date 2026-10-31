"""現場実績CSVの取り込み（登録待ちにためる）: services/external_actual_service.import_external_actual_file()。"""
import sqlite3

import pytest

import config
from db.init_db import init_database_at
from models import external_actual_rows as ear
from services import external_actual_service as svc
from tests.external_actual_helpers import FOLDER, line, write_csv



@pytest.fixture
def month_db():
    path = f"{config.APP_DATA_DIR}/db/{FOLDER}/inventory.db"
    init_database_at(path)
    config.DB_PATH = path          # conftest が終わったら戻す
    return path


def _import(tmp_path, *lines, name="x.csv"):
    return svc.import_external_actual_file(write_csv(tmp_path, *lines, name=name), "admin")


def test_default_db_is_refused(tmp_path):
    init_database_at(config.DB_PATH)                              # 既定DB（db/inventory.db）
    with pytest.raises(svc.ExternalActualRefused, match="未選択"):
        _import(tmp_path, line())
    assert config.is_default_db()


@pytest.mark.parametrize("name", ["actuals_20261020_180000_0001.tmp", "x.txt", "x", "x.csv.tmp"])
def test_file_that_is_not_csv_is_refused_with_reason_and_stores_nothing(tmp_path, month_db, name):
    """現場アプリが書き出し中の .tmp など、拡張子が .csv でないファイルは取り込まない（D-189）。"""
    path = write_csv(tmp_path, line(), name=name)
    with pytest.raises(svc.ExternalActualRefused, match=r"拡張子が \.csv ではない"):
        svc.import_external_actual_file(path, "admin")
    assert ear.list_pending_rows() == []


def test_csv_extension_is_case_insensitive(tmp_path, month_db):
    assert _import(tmp_path, line(), name="ACTUALS.CSV")["imported"] == 1


def test_db_version_mismatch_is_refused(tmp_path, month_db):
    with sqlite3.connect(month_db) as con:
        con.execute("PRAGMA user_version = 14")
    with pytest.raises(svc.ExternalActualRefused, match="版（14）がこのアプリの版（16）と違う"):
        _import(tmp_path, line())
    assert svc.check_db_ready() is not None


def test_rows_are_stored_as_pending_with_batch_info(tmp_path, month_db):
    result = _import(tmp_path, line(), line(lot="100002", kitting="0002-2-A-261001-01", file_no="0002", qty="7"))
    assert (result["imported"], result["replaced"], result["errors"], result["stale"]) == (2, [], [], [])
    pending = ear.list_pending_rows()
    assert [(r["lot_no"], r["daily_qty"], r["target_db_name"], r["status"]) for r in pending] == [
        ("100001", 100, FOLDER, "pending"), ("100002", 7, FOLDER, "pending")]
    assert pending[0]["source_file"] == "x.csv"


def test_resend_replaces_the_pending_row(tmp_path, month_db):
    _import(tmp_path, line(qty="10", exported="2026-10-20 10:00:00"))
    second = _import(tmp_path, line(qty="20", exported="2026-10-20 18:00:00"), name="y.csv")
    assert second["replaced"] == [("0001-2-A-261001-01", "100001")]
    assert [r["daily_qty"] for r in ear.list_pending_rows()] == [20]


def test_older_export_does_not_overwrite_newer_row(tmp_path, month_db):
    _import(tmp_path, line(qty="20", exported="2026-10-20 18:00:00"))
    old = _import(tmp_path, line(qty="10", exported="2026-10-20 10:00:00"), name="old.csv")
    assert old["imported"] == 0 and old["batch_id"] is None
    assert old["stale"][0]["messages"][0].startswith("取り込み済みの書き出し（2026-10-20 18:00:00）より古い")
    assert any("古い書き出しの行が 1 行" in w for w in old["warnings"])
    assert [r["daily_qty"] for r in ear.list_pending_rows()] == [20]


def test_same_exported_at_is_not_stale(tmp_path, month_db):
    _import(tmp_path, line(qty="10"))
    again = _import(tmp_path, line(qty="12"), name="y.csv")
    assert again["imported"] == 1 and again["stale"] == []


def test_same_file_twice_warns_but_is_harmless(tmp_path, month_db):
    _import(tmp_path, line())
    again = _import(tmp_path, line())
    assert again["already_imported"]["source_file"] == "x.csv"
    assert any("取り込み済み" in w for w in again["warnings"])
    assert len(ear.list_pending_rows()) == 1


def test_fatal_or_row_errors_store_nothing_for_those_rows(tmp_path, month_db):
    fatal = _import(tmp_path, line(version="1"))
    assert fatal["fatal"] and fatal["imported"] == 0 and ear.list_pending_rows() == []
    mixed = _import(tmp_path, line(), line(db="2026-09", lot="100002"), name="m.csv")
    assert mixed["imported"] == 1 and len(mixed["errors"]) == 1


def test_detect_wrong_format_warns_for_production_and_plan_csv(tmp_path):
    production = tmp_path / "prod.csv"
    production.write_text("払い出し日,基板構成数,ロットNo\r\n2026/10/20,2,1\r\n", encoding="utf-8-sig")
    plan = tmp_path / "plan.csv"
    plan.write_text("キッティングリストNo,削除フラグ,セットアップファイルNo.\r\nA,0,1\r\n", encoding="utf-8-sig")
    assert any("実績CSV" in w for w in svc.detect_wrong_format_warnings(str(production)))
    assert any("キッティング計画CSV" in w for w in svc.detect_wrong_format_warnings(str(plan)))
    assert svc.detect_wrong_format_warnings(write_csv(tmp_path, line())) == []
