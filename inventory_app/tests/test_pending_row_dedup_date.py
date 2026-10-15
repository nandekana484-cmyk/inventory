"""登録待ちの行の重複判定で、日付の書き方の違い（2026/7/14・2026/07/14・2026-07-14）を同じ日付とみなす（D-134）。保存値は変えない。"""
import os

import pytest

import config
from db.init_db import init_database_at
from models.production_import_staging import (
    create_csv_import_batch, upsert_pending_csv_import_row, list_pending_csv_import_rows, _same_report_date,
)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)


@pytest.mark.parametrize("a, b, same", [
    ("2026/7/14", "2026/07/14", True), ("2026/7/14", "2026-07-14", True), ("2026/7/14", "2026/7/15", False),
    ("不明", "不明", True), ("不明", "2026/7/14", False), (None, None, True),
])
def test_same_report_date(a, b, same):
    assert _same_report_date(a, b) is same


def test_reimport_with_different_date_notation_does_not_duplicate(db):
    first = create_csv_import_batch("a.csv")
    row = {"csv_row_no": 2, "lot_no": "L1", "product_name": "ProdA", "daily_qty": 300, "worker_id": "W1"}
    upsert_pending_csv_import_row(dict(row, report_date="2026/7/14"), import_batch_id=first)
    second = create_csv_import_batch("b.csv")
    upsert_pending_csv_import_row(dict(row, report_date="2026/07/14"), import_batch_id=second)
    rows = list_pending_csv_import_rows()
    assert len(rows) == 1 and rows[0]["report_date"] == "2026/7/14"  # 保存されている値は書き換えない
