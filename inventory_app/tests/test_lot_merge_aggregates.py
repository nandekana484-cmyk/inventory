"""
ロット統合 第2段階（D-150〜D-154）: 統合後のロット単位の集計。合成データ（scripts/make_lot_merge_sample_db.py）に、
利用者の例（D-136）のロットを足して確かめる。統合していないロットの計算が変わらないことは、既存のスナップショットのテストでも確かめている。
"""
import datetime
import os

import pytest

import config
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production
from services import lot_merge_service as svc
from services.lot_status_history import get_daily_drawdown
from services.lot_merge_items_service import _merge_lot_items
from services.lot_status_service import lot_completion_state
from services.production_service import (
    build_monthly_report, build_wip_extraction_rows, calculate_lot_completion, check_lot_progress, evaluate_lot_status,
)
from tests.test_lot_merge import sample, sample_db  # noqa: F401 （fixture）

# 利用者の例（D-136）: 500500（発注数 1000）を両面 950 生産（NG 50）。不足の 50 を 500500-（面2の計画だけ）で追加生産。
EXAMPLE_PLANS = [
    ("K500500-1", "500500", "F5000", "BRD-5000", "1", 1000),
    ("K500500-2", "500500", "F5000", "BRD-5000", "2", 1000),
    ("K500500M-2", "500500-", "F5000", "BRD-5000", "2", 50),
]
EXAMPLE_ACTUALS = [
    ("K500500-1", "500500", "2026-07-02", 950),
    ("K500500-2", "500500", "2026-07-02", 950),
    ("K500500M-2", "500500-", "2026-07-09", 50),
]


@pytest.fixture
def example_db(sample_db):
    batch = create_plan_batch("example.csv", imported_by="W1", row_count=len(EXAMPLE_PLANS))
    for kitting_list_no, lot_no, file_no, board_name, side, order_qty in EXAMPLE_PLANS:
        create_plan_version(batch, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board_name, "production_side": side,
            "order_qty": order_qty, "planned_qty": order_qty, "plan_start_datetime": "2026/07/01 08:00:00",
            "mounting_line": "A",
        }, created_by="W1")
    upsert_board_structure("BRD-5000", 1)
    for kitting_list_no, lot_no, report_date, qty in EXAMPLE_ACTUALS:
        plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
        insert_daily_production(plan["plan_item_id"], kitting_list_no, lot_no, plan["board_name"], report_date, qty, "W1")
    return sample_db


def _lot(lot_no):
    return next(r for r in check_lot_progress() if r["lot_no"] == lot_no)


def _drawdown(date, lot_no):
    return [(r["daily_drawdown"], r["cumulative_lot_completed"]) for r in get_daily_drawdown(date) if r["lot_no"] == lot_no]


# ---------------------------------------------------------------- 利用者の例・引落の日付

def test_user_example_merge_and_unmerge(example_db):
    assert (_lot("500500")["lot_completed"], _lot("500500")["order_quantity"]) == (950, 1000)
    assert _drawdown("2026-07-09", "500500") == []
    assert lot_completion_state("500500")["completed"] is False

    svc.merge_lots("500500-", "作業者A")
    lot = _lot("500500")
    assert (lot["lot_completed"], lot["order_quantity"], lot["order_quantity"] - lot["lot_completed"]) == (1000, 1000, 0)
    assert lot["display_lot_no"] == "500500（+500500-）"
    assert "500500-" not in {r["lot_no"] for r in check_lot_progress()}
    assert _drawdown("2026-07-02", "500500") == [(950, 950)]
    assert _drawdown("2026-07-09", "500500") == [(50, 1000)]       # 500500- の実績の日に +50
    # 完成済みロットの扱い（D-130）も統合後のロットで判定する（どちらのロットNoで聞いても同じ）
    assert lot_completion_state("500500")["completed"] is True
    assert lot_completion_state("500500-")["lot_completed"] == 1000

    svc.unmerge_lot("500500-", "作業者A")
    assert (_lot("500500")["lot_completed"], _lot("500500-")["lot_completed"]) == (950, 50)
    assert "display_lot_no" not in _lot("500500")
    assert _drawdown("2026-07-09", "500500") == []


def test_drawdown_dates_stay_on_actual_dates(sample_db):
    """D-136(10): 163326 の 7/10 の 99 は 7/10 のまま。163326- の 1 台（7/15）は、統合の後から 7/15 に +1、7月の合計は 100。"""
    svc.merge_lots("163326-", "作業者A")
    assert _drawdown("2026-07-10", "163326") == [(99, 99)]
    assert _drawdown("2026-07-15", "163326") == [(1, 100)]
    july = [datetime.date(2026, 7, d).isoformat() for d in range(1, 32)]
    assert sum(r["daily_drawdown"] for d in july for r in get_daily_drawdown(d) if r["lot_no"] == "163326") == 100
    assert not any(r["lot_no"] == "163326-" for d in july for r in get_daily_drawdown(d))


# ---------------------------------------------------------------- 守る約束（計画Noの共有・発注数・基板名）

def test_shared_kitting_list_no_is_not_double_counted(sample_db):
    """226793 と 226793- は同じ計画No（K226793）。実績は計画ごとに元のロットNoで引くので、120 が 240 にならない。"""
    svc.merge_lots("226793-", "作業者A")
    lot = _lot("226793")
    assert lot["file_actuals"] == {("F2267", "1"): 120}
    assert lot["lot_completed"] == 120
    assert calculate_lot_completion("226793-")["completed_quantity"] == 120


def test_order_qty_aligned_to_target(sample_db):
    """163326-（170）は統合先の 1000 にそろえる。「発注数不一致」にならない（D-121・D-127）。"""
    svc.merge_lots("163326-", "作業者A")
    lot = _lot("163326")
    assert lot["order_quantity"] == 1000 and lot["order_qty_inconsistent"] is False
    assert lot["order_qty_inconsistent_between_files"] is False and lot["progress_color_category"] is None
    assert [(f["setup_file_no"], f["order_qty"], f["file_actual"], f["source_lot_nos"]) for b in lot["boards"] for f in b["files"]] == [
        ("F1633", 1000, 100, ["163326", "163326-"])]


def test_board_name_aligned_to_target(sample_db):
    """348588- の基板名の表記ゆれ（CG-160M）は統合先の CA-G160M にそろえ、同じファイルNoを1行で数える。"""
    svc.merge_lots("348588-", "作業者A")
    lot = _lot("348588")
    assert lot["status"] == "match" and lot["unregistered_board_names"] == []
    assert [(b["board_name"], f["setup_file_no"], f["file_actual"]) for b in lot["boards"] for f in b["files"]] == [
        ("CA-G160M", "F3485", 10)]


def test_unmerged_lots_are_unchanged(sample_db):
    before = {r["lot_no"]: r for r in check_lot_progress()}
    for member in sample.EXPECTED_CANDIDATES:
        svc.merge_lots(member, "作業者A")
    after = {r["lot_no"]: r for r in check_lot_progress()}
    for lot_no in ("410001", "410001-1", "410001-2", "420002K", "430003.1", "440004-", "450005"):
        assert after[lot_no] == before[lot_no]


# ---------------------------------------------------------------- 面2を面1にも数える条件（D-151）

def _item(kitting, lot, file_no, side, order=100, board="B"):
    return {"kitting_list_no": kitting, "lot_no": lot, "setup_file_no": file_no, "production_side": side,
            "order_qty": order, "board_name": board}


def test_side2_counted_as_side1_only_when_member_has_no_side1():
    target = [_item("T1", "X", "F1", "1"), _item("T2", "X", "F1", "2"), _item("T3", "X", "F2", "2")]
    _, ctx = _merge_lot_items("X", target + [_item("M2", "X-", "F1", "2", 10)], ["X-"])
    assert ctx["side1_extra"] == [(("M2", "X-"), ("F1", "1"))]
    # 統合先に面1の計画が無いファイルNo（F2）には数えない
    _, ctx = _merge_lot_items("X", target + [_item("M3", "X-", "F2", "2", 10)], ["X-"])
    assert ctx["side1_extra"] == []
    # 統合されるロットに面1の計画が1件でもあれば、数えない（利用者の決定は「面2の計画しか無い場合」）
    _, ctx = _merge_lot_items("X", target + [_item("M2", "X-", "F1", "2", 10), _item("M1", "X-", "F9", "1", 10)], ["X-"])
    assert ctx["side1_extra"] == []


# ---------------------------------------------------------------- 表示（D-152）

def test_display_labels_in_three_screens(sample_db):
    from ui.production.lot_progress_window import build_display_rows
    svc.merge_lots("163326-", "作業者A")
    rows = [r for r in build_display_rows(check_lot_progress()) if r["lot_no"].startswith("163326")]
    assert [(r["lot_no"], r["setup_file_no"]) for r in rows] == [("163326（+163326-）", "F1633（163326・163326-）")]

    drawdown = [r for r in get_daily_drawdown("2026-07-15") if r["lot_no"] == "163326"]
    assert [(r["lot_label"], r["file_label"]) for r in drawdown] == [("163326（+163326-）", "F1633（163326・163326-）")]

    report = [r for r in build_monthly_report("2026-07-01", "2026-07-31")[0] if r["lot_no"] in ("163326", "163326-")]
    assert [(r["lot_no"], r["lot_label"], r["file_label"], r["order_qty"], r["lot_completed"]) for r in report] == [
        ("163326", "163326（+163326-）", "F1633", 1000, 100),
        ("163326-", "163326（+163326-）", "F1633（163326-）", 1000, 100),
    ]


def test_unmerged_report_rows_have_no_label_keys(sample_db):
    rows = build_monthly_report("2026-07-01", "2026-07-31")[0]
    assert all("lot_label" not in r and "file_label" not in r for r in rows)
    assert all("lot_label" not in r for r in get_daily_drawdown("2026-07-15"))


# ---------------------------------------------------------------- 履歴・仕掛・計画一覧・試算

def test_snapshot_is_recorded_for_merged_lot(sample_db):
    from models.lot_status_history import list_lot_status_history
    from services.lot_status_service import record_lot_status_snapshot
    svc.merge_lots("163326-", "作業者A")
    record_lot_status_snapshot("163326-", "register")
    assert len(list_lot_status_history("163326")) == 1
    assert list_lot_status_history("163326-") == []


def test_wip_extraction_counts_merged_lot_once(sample_db):
    svc.merge_lots("163326-", "作業者A")
    rows = build_wip_extraction_rows(["163326", "163326-"])
    # 面2がある F1633 の面1は除く。面2は 100（99＋1）−引落 100＝0 なので仕掛なし。二重に数えない
    assert rows == []
    svc.merge_lots("348588-", "作業者A")
    assert build_wip_extraction_rows(["348588", "348588-"]) == []


def test_plan_list_completed_uses_aligned_order_qty(sample_db):
    from services.production_entry_service import is_plan_row_completed
    svc.merge_lots("163326-", "作業者A")
    lot_info = calculate_lot_completion("163326-")
    assert lot_info["aligned_order_qty"] == 1000
    row = ("K163326M-2", "163326-", "", "F1633", "BRD-1633", "170", "170", "1", "169", "100", "900", "", "2")
    assert is_plan_row_completed(row, lot_info, 3, 6) is False        # 170 ではなく 1000 と比べる


def test_merged_lot_nos(sample_db):
    assert svc.merged_lot_nos("163326-") == ["163326-"]
    svc.merge_lots("163326-", "作業者A")
    assert svc.merged_lot_nos("163326-") == ["163326", "163326-"]
    assert svc.merged_lot_nos("163326") == ["163326", "163326-"]


def test_preview_merge(sample_db):
    preview = svc.preview_merge("163326-")
    assert preview["before"]["163326"] == {"order_quantity": 1000, "completed": 99, "remaining": 901, "status": "match"}
    assert preview["before"]["163326-"]["order_quantity"] == 170
    assert preview["after"] == {"order_quantity": 1000, "completed": 100, "remaining": 900, "status": "match"}
    assert svc.load_lot_merge_map().rows == []          # 試算では書かない
    from ui.production.lot_merge_window import format_merge_preview
    assert format_merge_preview(preview).splitlines()[-1] == "・統合後の 163326（+163326-）：発注数 1000（163326 の値）、完成数 100、残り 900"


def test_evaluate_with_explicit_unmerged_map(sample_db):
    svc.merge_lots("163326-", "作業者A")
    assert evaluate_lot_status("163326-", merge_map=svc.LotMergeMap([]))["lot_completed"] == 1
    assert evaluate_lot_status("163326-")["lot_no"] == "163326"


# ---------------------------------------------------------------- 引き継ぎ（D-154）

def test_carry_over_copies_plans_and_actuals_of_all_merged_lots(sample_db):
    from models.lot_merges import list_lot_merges
    from models.production import list_daily_production_by_kitting_no
    from services.db_migration_carryover import carry_over_incomplete_lots
    from db.init_db import init_database_at
    svc.merge_lots("163326-", "作業者A")
    new_db = os.path.join(config.APP_DATA_DIR, "db", "NEXT", "inventory.db")
    init_database_at(new_db)
    summary = carry_over_incomplete_lots(sample_db, new_db, imported_by="W1", reference_date=datetime.date(2026, 7, 31))
    assert summary["lot_nos"].index("163326") + 1 == summary["lot_nos"].index("163326-")
    assert [(r["member_lot"], r["target_lot"]) for r in list_lot_merges()] == [("163326-", "163326")]
    assert [r["daily_qty"] for r in list_daily_production_by_kitting_no("K163326M-2", "163326-")] == [1]
    assert find_plan_item_by_kitting_no("K163326M-2", "163326-")["order_qty"] == 170     # 保存値は変えない
    assert _lot("163326")["lot_completed"] == 100


# ---------------------------------------------------------------- 既定DB（未選択）

def test_default_db_creates_no_file(monkeypatch):
    default = os.path.join(config.APP_DATA_DIR, "db", "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", default)
    assert svc.merged_lot_nos("163326-") == ["163326-"]
    with pytest.raises(svc.LotMergeError):
        svc.preview_merge("163326-")
    assert not os.path.exists(os.path.dirname(default))
