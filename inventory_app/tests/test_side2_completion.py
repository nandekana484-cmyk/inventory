"""
完成数の規則（D-167、案X'）: 元のロット・ファイルNoに面2の有効な計画があれば面2の実績の合計だけ、無ければ面1の実績の合計を使う。
合成データで、面2の計画が2つあるファイルNo（複数回の面2の登録）と、片面のファイルNoが混ざるロットを確かめる。
"""
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.production import insert_daily_production
from services.lot_status_history import get_daily_drawdown
from services.production_service import build_wip_extraction_rows, calculate_lot_completion, check_lot_progress
from tests.test_lot_merge import sample_db  # noqa: F401 （fixture）

# 実データの 616629 と同じ形：F1 は面1×2・面2×2、面2に 5000（7/14）と 3600（7/21）。古い版の自動入力で、
# 面1には片方（3600）だけが残っている。F2 は片面の製品（面1だけ）で 9000。
PLANS = [
    ("A-1-a", "F1", "1", "2026/07/11 05:11:00"), ("A-1-b", "F1", "1", "2026/07/16 01:28:00"),
    ("A-2-a", "F1", "2", "2026/07/13 17:20:00"), ("A-2-b", "F1", "2", "2026/07/16 22:19:00"),
    ("B-1", "F2", "1", "2026/07/10 08:00:00"),
]
ACTUALS = [("A-2-a", "2026-07-14", 5000), ("A-2-b", "2026-07-21", 3600), ("A-1-b", "2026-07-21", 3600),
           ("B-1", "2026-07-12", 9000)]


def _build(lot="616000", order_qty=28600):
    batch = create_plan_batch("x.csv", imported_by="W1", row_count=len(PLANS))
    for kitting_list_no, file_no, side, start in PLANS:
        create_plan_version(batch, kitting_list_no, {
            "lot_no": lot, "setup_file_no": file_no, "board_name": "BRD-X", "production_side": side,
            "order_qty": order_qty, "planned_qty": 5000, "plan_start_datetime": start, "mounting_line": "A",
        }, created_by="W1")
    upsert_board_structure("BRD-X", 2)
    for kitting_list_no, report_date, qty in ACTUALS:
        plan = find_plan_item_by_kitting_no(kitting_list_no, lot)
        insert_daily_production(plan["plan_item_id"], kitting_list_no, lot, plan["board_name"], report_date, qty, "W1")
    return lot


def test_two_side2_batches_are_summed(sample_db):
    lot = _build()
    info = calculate_lot_completion(lot)
    # F1 は面2の 5000＋3600。面1（3600）は使わない。F2 は面2が無いので面1の 9000
    assert info["file_actuals"] == {("F1", "2"): 8600, ("F2", "1"): 9000}
    assert info["completed_quantity"] == 8600


def test_lot_progress_rows_and_drawdown_dates(sample_db):
    lot = _build()
    result = next(r for r in check_lot_progress() if r["lot_no"] == lot)
    assert result["status"] == "match" and result["lot_completed"] == 8600
    assert sorted((f["setup_file_no"], f["production_side"]) for b in result["boards"] for f in b["files"]) == \
        [("F1", "2"), ("F2", "1")]
    # 引落は面2の実績の日付で増える（7/14 に 5000、7/21 に 3600）
    assert [r["daily_drawdown"] for r in get_daily_drawdown("2026-07-14") if r["lot_no"] == lot] == [5000, 5000]
    assert [r["daily_drawdown"] for r in get_daily_drawdown("2026-07-21") if r["lot_no"] == lot] == [3600, 3600]


def test_wip_has_no_side1_row_for_two_sided_file(sample_db):
    lot = _build()
    rows = build_wip_extraction_rows([lot])
    # F2（片面）だけ 9000 − 8600 の仕掛。F1 は面2の合計＝完成数なので仕掛なし。面1の行は出さない
    assert [(r["file_no"], r["production_side"], r["surplus_qty"]) for r in rows] == [("F2", "1", 400)]
