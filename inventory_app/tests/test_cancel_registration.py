"""
実績の取り消し（D-129、services.production_service.build_cancellation_preview()・apply_cancellation()）。
面2の実績を取り消すと、連動して入った面1の実績と、両面の NG申告も取り消す。登録待ちに戻す行は1行で、日付は実績CSVの書式。
1つのトランザクションで行い、途中で失敗したら何も変わらない。
"""
import os
import sqlite3

import pytest

import config
from db.init_db import init_database_at
from models.db_common import get_connection
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version
from models.ng_declarations import get_ng_declaration
from models.production import list_daily_production_by_kitting_no
from models.production_import_staging import list_pending_csv_import_rows
from models.scrap_records import save_scrap_record
from services.production_entry_service import register_actual_and_ng
from services.production_import_service import parse_production_csv_for_staging
from services.production_service import (
    search_plan_by_kitting_no, build_cancellation_preview, apply_cancellation, to_csv_date_format,
    CANCELLED_REGISTRATION_SOURCE,
)


@pytest.fixture
def registered(monkeypatch):
    """面1（K1）・面2（K2）の計画に、面2から 300・NG 5 を登録した状態（面1に連動）。"""
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=0)
    for k, side, start in (("K1", "1", "2026/10/01 08:00:00"), ("K2", "2", "2026/10/05 08:00:00")):
        create_plan_version(batch, k, {
            "lot_no": "L1", "setup_file_no": "F1", "board_name": "ProdA", "production_side": side,
            "planned_qty": 300, "order_qty": 1000, "plan_start_datetime": start, "mounting_line": "A",
        }, created_by="test")
    k2 = search_plan_by_kitting_no("K2", "L1")[0]
    k1 = search_plan_by_kitting_no("K1", "L1")[0]
    register_actual_and_ng(k2, {"1": k1, "2": k2}, 300, {"1": 5.0, "2": 5.0}, None, "W1", "test", report_date="2026-10-06")
    return list_daily_production_by_kitting_no("K2", "L1")[0]["prod_log_id"]


def _state():
    return (
        [(r["kitting_list_no"], r["daily_qty"]) for k in ("K1", "K2") for r in list_daily_production_by_kitting_no(k, "L1")],
        [get_ng_declaration(k, s, "L1") is not None for k, s in (("K1", 1), ("K2", 2))],
        [(r["daily_qty"], r["report_date"]) for r in list_pending_csv_import_rows()],
    )


def test_preview_contents(registered):
    preview = build_cancellation_preview(registered)
    assert preview["primary"]["kitting_list_no"] == "K2" and preview["primary"]["production_side"] == "2"
    assert preview["opposite"]["kitting_list_no"] == "K1" and preview["opposite"]["production_side"] == "1"
    assert sorted((d["kitting_list_no"], d["ng_qty"]) for d in preview["ng_declarations"]) == [("K1", 5.0), ("K2", 5.0)]
    assert preview["restored_row"] == {"lot_no": "L1", "product_name": "ProdA", "daily_qty": 300,
                                       "report_date": "2026/10/6", "worker_id": "W1"}
    assert preview["blockers"] == []


def test_cancel_and_return_to_staging(registered):
    pending_row_id = apply_cancellation(build_cancellation_preview(registered), True, imported_by="W1")
    assert _state() == ([], [False, False], [(300.0, "2026/10/6")])
    (row,) = list_pending_csv_import_rows()
    assert row["pending_row_id"] == pending_row_id and row["csv_row_no"] is None
    with get_connection() as con:
        source = con.execute("SELECT source_file FROM csv_import_batches WHERE import_batch_id = ?",
                             (row["import_batch_id"],)).fetchone()[0]
    assert source == CANCELLED_REGISTRATION_SOURCE


def test_reimport_does_not_duplicate_restored_row(registered, tmp_path):
    apply_cancellation(build_cancellation_preview(registered), True, imported_by="W1")
    csv_path = tmp_path / "actual.csv"
    csv_path.write_text("ロットNo,製品名,実績数,払い出し日\nL1,ProdA,300,2026/10/6\n", encoding="utf-8-sig")
    parse_production_csv_for_staging(str(csv_path), "W1")
    assert len(list_pending_csv_import_rows()) == 1


def test_cancel_only(registered):
    assert apply_cancellation(build_cancellation_preview(registered), False) is None
    assert _state() == ([], [False, False], [])


def test_blocked_when_ng_already_expanded(registered):
    save_scrap_record("K2", "F1", 2, "96-0001", 5, "2026-10-07", lot_no="L1")
    preview = build_cancellation_preview(registered)
    assert len(preview["blockers"]) == 1 and "NG展開" in preview["blockers"][0]
    before = _state()
    with pytest.raises(ValueError):
        apply_cancellation(preview, True)
    assert _state() == before


def test_failure_rolls_back_everything(registered):
    before = _state()
    preview = build_cancellation_preview(registered)
    list_pending_csv_import_rows()  # 表を作っておく
    with get_connection() as con:
        con.execute("CREATE TRIGGER fail BEFORE INSERT ON pending_csv_import_rows BEGIN SELECT RAISE(ABORT, 'x'); END")
        con.commit()
    with pytest.raises(sqlite3.DatabaseError):
        apply_cancellation(preview, True)
    assert _state() == before


@pytest.mark.parametrize("value, expected", [("2026-07-14", "2026/7/14"), ("2026-10-06", "2026/10/6"), ("不明", "不明")])
def test_csv_date_format(value, expected):
    assert to_csv_date_format(value) == expected


def test_reimport_with_zero_padded_date_does_not_duplicate(registered, tmp_path):
    apply_cancellation(build_cancellation_preview(registered), True, imported_by="W1")
    csv_path = tmp_path / "actual.csv"
    csv_path.write_text("ロットNo,製品名,実績数,払い出し日\nL1,ProdA,300,2026/10/06\n", encoding="utf-8-sig")
    parse_production_csv_for_staging(str(csv_path), "W1")
    assert len(list_pending_csv_import_rows()) == 1
