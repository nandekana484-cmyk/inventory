"""
plan_start_datetime の解釈の複製を services/date_parsing._parse_plan_start_datetime() に置き換えても、結果が変わらないことの確認（段階2）。
_old_* は置き換える前のコードをそのまま写したもの。
"""
from datetime import datetime

import pytest

from services import db_migration_carryover, production_service
from services.date_parsing import _parse_plan_start_datetime


def _old_carryover_parse(value):
    """置き換え前の services/db_migration_carryover.py の _parse_plan_start_datetime()。"""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y/%m/%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _old_service_parse_dt(value):
    """置き換え前の services/production_service.py の _pick_representative_plan_item() 内の parse_dt。"""
    try:
        return datetime.strptime(str(value).strip(), "%Y/%m/%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


INPUTS = [
    # 空の値・None
    None, "", 0, 0.0, False, [], {},
    # 空白だけ・空白つき
    " ", "   ", "\t", "\n", "　",
    " 2026/07/21 23:50:00", "2026/07/21 23:50:00 ", "  2026/07/21 23:50:00  ",
    "\t2026/07/21 23:50:00\r\n", "　2026/07/21 23:50:00　",
    "2026/07/21  23:50:00",
    # 正しい形式
    "2026/07/21 23:50:00", "2026/7/1 8:05:00", "2026/12/31 00:00:00", "2024/02/29 12:00:00",
    # 不正な形式・存在しない日時
    "2026-07-21 23:50:00", "2026/07/21", "2026/07/21 23:50", "2026/07/21 23:50:00.000",
    "2026/07/21T23:50:00", "2026/13/01 00:00:00", "2026/02/30 00:00:00", "2025/02/29 00:00:00",
    "2026/07/21 24:00:00", "abc", "None", "0",
    # 文字列以外
    12345, 1.5, True, datetime(2026, 7, 21, 23, 50, 0), b"2026/07/21 23:50:00",
]


@pytest.mark.parametrize("value", INPUTS, ids=repr)
def test_carryover_parse_matches_date_parsing(value):
    expected = _old_carryover_parse(value)
    assert _parse_plan_start_datetime(value) == expected
    assert db_migration_carryover._parse_plan_start_datetime(value) == expected


@pytest.mark.parametrize("value", INPUTS, ids=repr)
def test_service_parse_dt_matches_date_parsing(value):
    assert _parse_plan_start_datetime(value) == _old_service_parse_dt(value)


def _old_pick_representative_plan_item(items):
    """置き換え前の _pick_representative_plan_item() の本体（parse_dt は上の _old_service_parse_dt）。"""
    parseable = [(_old_service_parse_dt(item.get("plan_start_datetime")), item) for item in items]
    parseable = [(dt, item) for dt, item in parseable if dt is not None]
    if parseable:
        return max(parseable, key=lambda pair: pair[0])[1]
    return items[-1]


def _items(*values):
    return [{"id": i, "plan_start_datetime": v} for i, v in enumerate(values)]


@pytest.mark.parametrize("items", [
    _items("2026/07/21 23:50:00"),
    _items("2026/07/20 08:00:00", "2026/07/21 08:00:00", "2026/07/19 08:00:00"),
    _items(" 2026/07/22 08:00:00 ", "2026/07/21 08:00:00"),        # 空白つきが最新
    _items("2026/07/21 08:00:00", "2026/07/21 08:00:00"),          # 同じ日時（先の1件）
    _items(None, "", "abc"),                                       # どれも解釈できない（最後の1件）
    _items(None, "2026/07/20 08:00:00", "2026-07-30 08:00:00"),    # 解釈できるものだけで選ぶ
    [{"id": 0}, {"id": 1, "plan_start_datetime": "2026/07/20 08:00:00"}],  # キーが無い
])
def test_pick_representative_plan_item_unchanged(items):
    assert production_service._pick_representative_plan_item(items) is _old_pick_representative_plan_item(items)
