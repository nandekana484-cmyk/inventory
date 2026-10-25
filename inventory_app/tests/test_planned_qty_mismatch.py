from services.production_entry_service import build_planned_qty_mismatch_lines


def _plan(kitting_list_no, planned_qty):
    return {"kitting_list_no": kitting_list_no, "planned_qty": planned_qty}


def test_matching_total_gives_no_lines():
    plans = {"1": _plan("K1", 100), "2": _plan("K2", 100)}
    assert build_planned_qty_mismatch_lines(plans, 98, {"1": 2.0, "2": 2.0}) == []


def test_side_without_saved_ng_counts_ng_as_zero():
    plans = {"1": _plan("K1", 100), "2": _plan("K2", 100)}
    lines = build_planned_qty_mismatch_lines(plans, 98, {"2": 2.0})
    assert lines == ["面1（K1）：実績98 + NG0 = 98（予定生産数100と不一致）"]


def test_missing_side_plan_is_skipped():
    assert build_planned_qty_mismatch_lines({"1": None, "2": _plan("K2", 50)}, 50, {}) == []
