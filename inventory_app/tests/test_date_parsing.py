"""services/date_parsing.py の単体テスト。期待値は、移す前のコードが返していた値をそのまま固定している。"""
from datetime import datetime

import pytest

from services import date_parsing
from services.date_parsing import _parse_flexible_date, _parse_plan_start_datetime, _REPORT_DATE_FORMATS


class TestParsePlanStartDatetime:
    @pytest.mark.parametrize("value, expected", [
        ("2026/07/21 23:50:00", datetime(2026, 7, 21, 23, 50, 0)),
        ("  2026/07/21 23:50:00  ", datetime(2026, 7, 21, 23, 50, 0)),  # 前後の空白は除く
        ("2026/7/1 8:05:00", datetime(2026, 7, 1, 8, 5, 0)),             # ゼロ埋めなしも解釈できる
    ])
    def test_parses_slash_datetime(self, value, expected):
        assert _parse_plan_start_datetime(value) == expected

    @pytest.mark.parametrize("value", [
        None, "", "   ", "2026-07-21 23:50:00", "2026/07/21", "2026/07/21 23:50", "abc", 12345,
    ])
    def test_returns_none_when_not_parseable(self, value):
        assert _parse_plan_start_datetime(value) is None


class TestParseFlexibleDate:
    def test_formats(self):
        assert _REPORT_DATE_FORMATS == ("%Y-%m-%d", "%Y/%m/%d")

    @pytest.mark.parametrize("value, expected", [
        ("2026-09-05", datetime(2026, 9, 5)),
        ("2026/9/5", datetime(2026, 9, 5)),     # 実運用の CSV の形式（ゼロ埋めなし）
        ("2026/3/18", datetime(2026, 3, 18)),
        ("2026/03/18", datetime(2026, 3, 18)),
        ("2026-9-5", datetime(2026, 9, 5)),      # ハイフンでもゼロ埋めなしを解釈できる
        ("  2026-09-05 ", datetime(2026, 9, 5)),
    ])
    def test_parses_supported_formats(self, value, expected):
        assert _parse_flexible_date(value) == expected

    @pytest.mark.parametrize("value", [
        None, "", 0, "2026.09.05", "20260905", "2026/07/21 23:50:00", "2026-13-01", "abc",
    ])
    def test_returns_none_when_not_parseable(self, value):
        assert _parse_flexible_date(value) is None


def test_old_location_reexports_same_functions():
    """旧位置（ui.plan_candidate_dialog）から import しても、同じ関数オブジェクトが返る。"""
    from ui import plan_candidate_dialog
    assert plan_candidate_dialog._parse_flexible_date is date_parsing._parse_flexible_date
    assert plan_candidate_dialog._parse_plan_start_datetime is date_parsing._parse_plan_start_datetime
    assert plan_candidate_dialog._REPORT_DATE_FORMATS is date_parsing._REPORT_DATE_FORMATS


def test_new_module_does_not_import_gui():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(date_parsing))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not names & {"tkinter", "tkcalendar", "ui"}
