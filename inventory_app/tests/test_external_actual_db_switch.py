"""
現場実績CSVの登録待ちと、月次DB の切り替え・前月からの引き継ぎ。
登録待ちは月次DB ごとに持ち、別の月のDB に持ち越さない。バッチの取り込み先DB が今のDB と違うときは登録しない。
"""
import datetime
import os
import shutil

import pytest

import config
from db.init_db import init_database_at
from models import external_actual_rows as ear
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version
from models.workers import create_worker
from services import external_actual_service as svc
from services.db_migration_carryover import carry_over_incomplete_lots
from tests.external_actual_helpers import line, write_csv

ADMIN = {"worker_id": "A1", "name": "管理者"}


def _make_month(folder):
    path = os.path.join(config.APP_DATA_DIR, "db", folder, "inventory.db")
    init_database_at(path)
    return path


def _use(path):
    config.DB_PATH = path          # conftest が終わったら戻す


def _add_plan(kitting_no="K3", lot_no="L2", file_no="0002", side="2"):
    init_kitting_plan_tables()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=1)
    create_plan_version(batch, kitting_no, {
        "lot_no": lot_no, "setup_file_no": file_no, "board_name": "BoardB", "production_side": side,
        "planned_qty": 100, "order_qty": 100, "plan_start_datetime": "2026/10/01 08:00:00", "mounting_line": "A"}, created_by="test")


@pytest.fixture
def months(tmp_path):
    september, october = _make_month("2026-09"), _make_month("2026-10")
    create_worker("W1", "太郎")
    return september, october


def _import(tmp_path, folder, name="x.csv", **overrides):
    return svc.import_external_actual_file(
        write_csv(tmp_path, line(db=folder, kitting="K3", lot="L2", file_no="0002", qty="100", ng2="0", **overrides), name=name), "admin")


def test_pending_rows_belong_to_their_own_month_db(months, tmp_path):
    september, october = months
    _use(september)
    _add_plan()
    assert _import(tmp_path, "2026-09")["imported"] == 1 and len(svc.build_preview()) == 1

    _use(october)                                                                  # 10月のDB に切り替える
    _add_plan()
    assert ear.list_pending_rows() == [] and svc.build_preview() == []            # 9月の登録待ちは見えない
    rejected = _import(tmp_path, "2026-09", name="again.csv")                      # 9月のCSV は、10月のDB には取り込めない
    assert rejected["imported"] == 0 and "元のDB「2026-09」が今のDB「2026-10」と違います" in rejected["errors"][0]["messages"][0]
    assert _import(tmp_path, "2026-10", name="oct.csv")["imported"] == 1

    _use(september)                                                                # 9月のDB に戻すと、9月の登録待ちだけが残っている
    assert [r["target_db_name"] for r in ear.list_pending_rows()] == ["2026-09"]


def test_row_ids_of_another_db_are_not_registered(months, tmp_path):
    september, october = months
    _use(september)
    _add_plan()
    _import(tmp_path, "2026-09")
    row_id = ear.list_pending_rows()[0]["row_id"]
    _use(october)
    _add_plan()
    (result,) = svc.register_rows([row_id], {}, ADMIN)["results"]
    assert result["status"] == svc.RESULT_SKIPPED


def test_batch_copied_into_another_folder_is_red_and_refused(months, tmp_path):
    """9月のDB のファイルを10月のフォルダへ丸ごとコピーした（バックアップの呼び出しなど）。バッチの取り込み先は 2026-09 のまま。"""
    september, october = months
    _use(september)
    _add_plan()
    _import(tmp_path, "2026-09")
    shutil.copyfile(september, october)
    _use(october)
    (item,) = svc.build_preview()
    assert item["state"] == "red" and "取り込んだDB（2026-09）が今のDB（2026-10）と違います" in item["remarks"]
    with pytest.raises(svc.ExternalActualRefused, match="2026-09"):
        svc.register_rows([item["row_id"]], {}, ADMIN)


def test_carry_over_does_not_copy_pending_rows_but_counts_them(months, tmp_path):
    september, october = months
    _use(september)
    _add_plan()                                                                    # 実績が無いので未完了のロット
    _import(tmp_path, "2026-09")
    summary = carry_over_incomplete_lots(september, october, imported_by="W1", reference_date=datetime.date(2026, 10, 25))
    assert summary["lots_copied"] == 1 and summary["external_pending_rows_not_copied"] == 1
    assert config.DB_PATH == october and ear.list_pending_rows() == []             # 新しいDB には引き継がない
    _use(september)
    assert len(ear.list_pending_rows()) == 1                                       # 引き継ぎ元には残っている


def test_carry_over_counts_zero_when_old_db_has_no_tables_or_no_rows(months):
    september, october = months
    _use(september)
    _add_plan()
    assert carry_over_incomplete_lots(september, october, imported_by="W1", reference_date=datetime.date(2026, 10, 25))[
        "external_pending_rows_not_copied"] == 0
