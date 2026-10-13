"""services/lot_status_history.py → services/lot_status_history_service.py の改名（段階4）。"""
from services import lot_status_history as old
from services import lot_status_history_service as new


def test_old_module_reexports_same_functions():
    for name in ("get_daily_drawdown", "get_drawdown_date_range", "_cutoff_for_date", "_previous_date", "_flatten_files"):
        assert getattr(old, name) is getattr(new, name), name


def test_screen_import_still_works():
    from ui.production.daily_drawdown_window import get_daily_drawdown, get_drawdown_date_range
    assert get_daily_drawdown is new.get_daily_drawdown
    assert get_drawdown_date_range is new.get_drawdown_date_range
