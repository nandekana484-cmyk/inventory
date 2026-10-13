"""段階6の作業1: _ 始まりの名前に公開名を付けた（旧名は同じものを指す別名）。"""
import importlib

import pytest


@pytest.mark.parametrize("module, old, new", [
    ("services.date_parsing", "_parse_plan_start_datetime", "parse_plan_start_datetime"),
    ("services.date_parsing", "_parse_flexible_date", "parse_flexible_date"),
    ("services.production_entry_service", "_resolve_csv_report_date", "resolve_csv_report_date"),
    ("services.csv_parsing_common", "_open_csv_with_fallback", "open_csv_with_fallback"),
    ("ui.daily_report_window", "_row_to_values", "report_row_to_values"),
    ("services.db_migration_carryover", "_parse_plan_start_datetime", "parse_plan_start_datetime"),
    ("ui.kitting_production_entry", "_resolve_csv_report_date", "resolve_csv_report_date"),
])
def test_old_name_is_alias(module, old, new):
    m = importlib.import_module(module)
    assert getattr(m, old) is getattr(m, new)
