"""services/production_entry_service.register_actual_and_ng() の戻り値（段階3a）。DB への書き込みの中身は test_perform_registration.py で固定している。"""
from services.production_entry_service import register_actual_and_ng
from tests.test_perform_registration import _add_abort_trigger, _plan, monthly_db  # noqa: F401 （fixture）


def _call(plan, ng_side_plans, save_qty_by_side=None, existing_daily_qty=None):
    return register_actual_and_ng(
        plan, ng_side_plans, 90, save_qty_by_side or {}, existing_daily_qty, "W1", "作業者A", report_date="2026-03-18",
    )


def test_success(monthly_db):
    p1, p2 = _plan("K1", "L1"), _plan("K2", "L1")
    assert _call(p2, {"1": p1, "2": p2}, {"1": 8.0, "2": 5.0}) == {
        "actual_registered": True,
        "actual_error": None,
        "new_cumulative": 90.0,
        "declared_sides": ["1", "2"],
        "errors": [],
    }


def test_single_side_plan(monthly_db):
    p3 = _plan("K3", "L2")
    result = _call(p3, {"1": None, "2": p3})
    assert result["declared_sides"] == []


def test_ng_failure_is_listed_in_errors(monthly_db):
    _add_abort_trigger("fail_ng_side1", "ng_declarations", "NEW.production_side = 1")
    p1, p2 = _plan("K1", "L1"), _plan("K2", "L1")
    result = _call(p2, {"1": p1, "2": p2}, {"1": 8.0, "2": 5.0})
    assert result["actual_registered"] is True
    assert result["declared_sides"] == ["2"]
    assert result["errors"] == ["面1のNG登録に失敗しました：test failure"]


def test_actual_failure_returns_error_and_stops(monthly_db):
    _add_abort_trigger("fail_k2", "production_daily", "NEW.kitting_list_no = 'K2'")
    p1, p2 = _plan("K1", "L1"), _plan("K2", "L1")
    result = _call(p2, {"1": p1, "2": p2}, {"1": 8.0})
    assert result["actual_registered"] is False
    assert result["new_cumulative"] is None
    assert str(result["actual_error"]) == "test failure"
    assert result["declared_sides"] == [] and result["errors"] == []


def test_side1_with_side2_is_refused(monthly_db):
    """面2がある面1（K1）への登録は、サービスの入口で断る（D-170）。何も書かない。"""
    p1, p2 = _plan("K1", "L1"), _plan("K2", "L1")
    result = _call(p1, {"1": p1, "2": p2}, {"1": 8.0})
    assert result["actual_registered"] is False and result["declared_sides"] == []
    assert "同じロット・ファイルNoに面2の計画（K2）があるため、面1には登録できません" in str(result["actual_error"])
