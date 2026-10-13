"""生産実績入力画面の、ステージング画面から呼ばれる公開メソッド（段階3b）。"""
from tests.test_staging_registration import _FakeParent, monthly_db, events  # noqa: F401 （fixture）


def test_load_staged_row_transfers_without_focus(monthly_db, events):
    parent = _FakeParent(events)
    callback = lambda: None  # noqa: E731
    parent.load_staged_row("K2", "L1", daily_qty=100, report_date="2026/7/23", on_registered=callback)
    assert parent.current_plan["kitting_list_no"] == "K2"
    assert parent.entry_daily_qty.get() == "100"
    assert parent._pending_csv_report_date == "2026/7/23"
    assert parent._pending_csv_row_removal is callback
    assert ("entry_daily_qty", "focus_set") not in events


def test_load_staged_row_keeps_entry_when_qty_is_none(monthly_db, events):
    parent = _FakeParent(events)
    parent.load_staged_row("K5", "L5", focus_entry=True)
    assert parent.entry_daily_qty.get() == "50"   # search_plan() が入れた登録済みの値
    assert parent._pending_csv_row_removal is None and parent._pending_csv_report_date is None
    assert events[-1] == ("entry_daily_qty", "focus_set")


def test_refresh_after_external_registration(monthly_db, events):
    parent = _FakeParent(events)
    parent.load_staged_row("K5", "L5", daily_qty=7, report_date="2026/7/23", on_registered=lambda: None)
    del events[:]
    parent.refresh_after_external_registration(["L5", "L1"], new_cumulative=80)
    assert parent._pending_csv_row_removal is None and parent._pending_csv_report_date is None
    assert parent.entry_daily_qty.get() == "50"
    assert [e[:2] if len(e) > 2 and e[0] != "parent" else e for e in events if e[0] in ("lbl_app_cum", "parent")] == [
        ("lbl_app_cum", "config"),
        ("parent", "load_today_log"),
        ("parent", "_refresh_plan_list_for_lot", "L5"),
        ("parent", "_refresh_plan_list_for_lot", "L1"),
    ]
