"""
services・ui にあった SQL の挙動を固定する（段階4の作業4で models へ移す前の値）。
対象: backup_service.validate_monthly_db_backup()、master_merge_service.merge_master_from_backup()、
db_migration_carryover の4関数、lot_status_history.get_drawdown_date_range()、
kitting_import_service の row_count 更新、ui/kitting/kitting_plan_import.py の _load_items_for_batch()。
"""
import os
import shutil
import sqlite3
import types

import pytest

import config
from db.init_db import init_database_at
from models.board_structure_master import upsert_board_structure, get_board_structure
from models.kitting_plan import (
    init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no,
    list_plan_batches,
)
from models.production import insert_daily_production
from models.workers import upsert_worker
from services import db_migration_carryover as carryover
from services.backup_service import validate_monthly_db_backup
from services.lot_status_history import get_drawdown_date_range
from services.master_merge_service import merge_master_from_backup


@pytest.fixture
def monthly_db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()


def _add_plan(batch_id, kitting_list_no, lot_no, side="2", start="2026/07/22 08:00:00"):
    create_plan_version(batch_id, kitting_list_no, {
        "lot_no": lot_no, "setup_file_no": "F1", "board_name": "BoardA", "production_side": side,
        "planned_qty": 100, "order_qty": 100, "plan_start_datetime": start,
    }, created_by="test")


def _add_actual(kitting_list_no, lot_no, report_date, qty):
    plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
    insert_daily_production(plan["plan_item_id"], kitting_list_no, lot_no, "BoardA", report_date, qty, "W1")


# ---------------------------------------------------------------- backup_service.validate_monthly_db_backup

def test_backup_valid_monthly_db(monthly_db):
    assert validate_monthly_db_backup(config.DB_PATH) == {"ok": True, "reason": ""}


def test_backup_missing_file(tmp_path):
    assert validate_monthly_db_backup(str(tmp_path / "none.db")) == {"ok": False, "reason": "ファイルが見つかりません。"}


def test_backup_not_sqlite(tmp_path):
    path = tmp_path / "text.db"
    path.write_text("this is not a database" * 100, encoding="utf-8")
    result = validate_monthly_db_backup(str(path))
    assert result["ok"] is False
    assert result["reason"].startswith("SQLiteデータベースとして開けませんでした：")


def test_backup_master_db_is_rejected(tmp_path):
    path = tmp_path / "master_like.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE board_structure_master (board_name TEXT)")
    result = validate_monthly_db_backup(str(path))
    assert result["ok"] is False
    assert result["reason"].startswith("これはマスタDBのバックアップのようです。")


def test_backup_unrelated_db_is_rejected(tmp_path):
    path = tmp_path / "other.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE something (x TEXT)")
    result = validate_monthly_db_backup(str(path))
    assert result["ok"] is False
    assert result["reason"].startswith("月次DBのテーブル構成と一致しません")


# ---------------------------------------------------------------- master_merge_service.merge_master_from_backup

def test_merge_master_adds_only_missing_keys(tmp_path):
    upsert_board_structure("BoardA", 2)
    upsert_board_structure("BoardB", 3)
    upsert_worker("W1", "作業者A", "admin", 1)
    backup = tmp_path / "master_backup.db"
    shutil.copyfile(config.MASTER_DB_PATH, backup)
    with sqlite3.connect(config.MASTER_DB_PATH) as con:
        con.execute("DELETE FROM board_structure_master WHERE board_name = 'BoardB'")
    upsert_board_structure("BoardA", 9)  # 既存キーは上書きしない

    result = merge_master_from_backup(str(backup))
    assert result == {
        "board_structure_master": 1, "parts_attributes": 0, "workers": 0,
        "parts": 0, "final_products": 0, "production_side_master": 0,
    }
    assert get_board_structure("BoardB")["board_count"] == 3
    assert get_board_structure("BoardA")["board_count"] == 9


def test_merge_master_table_exists_helper(tmp_path):
    path = tmp_path / "x.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE workers (worker_id TEXT)")
        from services.master_merge_service import _table_exists
        assert _table_exists(con, "workers") is True
        assert _table_exists(con, "parts") is False


# ---------------------------------------------------------------- db_migration_carryover

@pytest.fixture
def carry_data(monthly_db):
    batch_id = create_plan_batch("plan.csv", imported_by="test", row_count=3)
    _add_plan(batch_id, "K1", "L1", side="1")
    _add_plan(batch_id, "K2", "L1", side="2", start="2026/07/23 08:00:00")
    _add_plan(batch_id, "K1", "L2")                       # 同じ kitting_list_no の別ロット
    _add_plan(batch_id, "K1", "L1", side="1", start="2026/07/24 08:00:00")  # K1/L1 の新バージョン（旧版は無効になる）
    _add_actual("K1", "L1", "2026-07-23", 50)
    _add_actual("K2", "L1", "2026-07-24", 40)
    _add_actual("K1", "L2", "2026-07-25", 10)


def test_fetch_plan_items_for_lot(carry_data):
    items = carryover._fetch_plan_items_for_lot("L1")
    assert sorted((i["kitting_list_no"], i["plan_start_datetime"]) for i in items) == [
        ("K1", "2026/07/24 08:00:00"), ("K2", "2026/07/23 08:00:00"),
    ]
    assert carryover._fetch_plan_items_for_lot("NONE") == []


def test_fetch_existing_plan_start_datetimes_includes_inactive(carry_data):
    assert sorted(carryover._fetch_existing_plan_start_datetimes_for_lot("L1")) == [
        "2026/07/22 08:00:00", "2026/07/23 08:00:00", "2026/07/24 08:00:00",
    ]
    assert carryover._fetch_existing_plan_start_datetimes_for_lot("NONE") == []


def test_fetch_production_daily_for_lot(carry_data):
    rows = carryover._fetch_production_daily_for_lot("L1", ["K1", "K2"])
    assert sorted((r["kitting_list_no"], r["lot_id"], r["daily_qty"]) for r in rows) == [
        ("K1", "L1", 50.0), ("K2", "L1", 40.0),
    ]
    assert carryover._fetch_production_daily_for_lot("L1", []) == []
    assert carryover._fetch_production_daily_for_lot("L2", ["K1"])[0]["daily_qty"] == 10.0


def test_lot_already_migrated(carry_data):
    plan_items = carryover._fetch_plan_items_for_lot("L1")
    production_rows = carryover._fetch_production_daily_for_lot("L1", ["K1", "K2"])
    assert carryover._lot_already_migrated("L1", plan_items, production_rows) is True
    assert carryover._lot_already_migrated("L1", [], production_rows) is False
    assert carryover._lot_already_migrated("L1", plan_items + [{"kitting_list_no": "K9"}], production_rows) is False
    assert carryover._lot_already_migrated("L1", plan_items, production_rows + [{"kitting_list_no": "K9"}]) is False
    assert carryover._lot_already_migrated("L1", plan_items, []) is True


# ---------------------------------------------------------------- lot_status_history.get_drawdown_date_range

def test_drawdown_date_range(carry_data):
    assert get_drawdown_date_range() == ("2026-07-23", "2026-07-25")


def test_drawdown_date_range_empty(monthly_db):
    assert get_drawdown_date_range() == (None, None)


# ---------------------------------------------------------------- kitting_import_service の row_count 更新

def test_kitting_import_updates_row_count(monthly_db, tmp_path):
    from services.kitting_import_service import import_kitting_plan_csv
    header = "キッティングリストNo,ロットNo,セットアップファイルNo,基板名,予定生産数,累計生産数,発注数,生産面,ステータス,実装予定開始日時"
    csv_path = tmp_path / "plan.csv"
    csv_path.write_text(
        header + "\n"
        "K1,L1,F1,BoardA,100,0,100,2,未着手,2026/07/22 08:00:00\n"
        "K2,L1,F1,BoardA,100,0,100,1,未着手,2026/07/22 08:00:00\n",
        encoding="utf-8-sig",
    )
    result = import_kitting_plan_csv(str(csv_path))
    batches = list_plan_batches(include_deleted=False)
    assert [(b["source_file"], b["row_count"]) for b in batches][-1] == ("plan.csv", 2)


# ---------------------------------------------------------------- ui/kitting/kitting_plan_import.py の _load_items_for_batch

class _FakeTree:
    def __init__(self):
        self.rows = []

    def get_children(self):
        return list(range(len(self.rows)))

    def delete(self, iid):
        pass

    def insert(self, parent, index, values):
        self.rows.append(values)


def test_kitting_plan_import_load_items_for_batch(monthly_db):
    from ui.kitting.kitting_plan_import import KittingPlanImportWindow
    batch_id = create_plan_batch("plan.csv", imported_by="test", row_count=2)
    _add_plan(batch_id, "K2", "L1")
    _add_plan(batch_id, "K1", "L1", side="1")
    other_batch = create_plan_batch("plan2.csv", imported_by="test", row_count=1)
    _add_plan(other_batch, "K3", "L3")

    texts = []
    label = types.SimpleNamespace(config=lambda **kw: texts.append(kw["text"]))
    fake = types.SimpleNamespace(tree_items=_FakeTree(), lbl_status=label, result_label=label)
    fake._get_db_connection = types.MethodType(KittingPlanImportWindow._get_db_connection, fake)
    KittingPlanImportWindow._load_items_for_batch(fake, batch_id)
    assert fake.tree_items.rows == [
        ("K1", "L1", "F1", "BoardA", "100", "0", "100", "1", "", "2026/07/22 08:00:00", "", "", "", ""),
        ("K2", "L1", "F1", "BoardA", "100", "0", "100", "2", "", "2026/07/22 08:00:00", "", "", "", ""),
    ]
    assert len(texts) == 2 and "2 件" in texts[0]
