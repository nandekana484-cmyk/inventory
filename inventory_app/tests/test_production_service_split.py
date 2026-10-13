"""段階5の production_service.py の分割（re-export と import の向き）。"""
import ast
import inspect

import pytest

from services import production_service, lot_status_service

LOT_STATUS_NAMES = [
    "record_lot_status_snapshot", "_compute_lot_completion", "calculate_lot_completion",
    "DRAWDOWN_ZERO_STATUSES", "NEEDS_REVIEW_STATUSES", "_build_lot_status_remarks", "_lot_status_color_category",
    "_evaluate_lot_status", "evaluate_lot_status", "list_incomplete_lots", "check_lot_progress", "evaluate_all_lot_status",
]


@pytest.mark.parametrize("name", LOT_STATUS_NAMES)
def test_production_service_reexports_lot_status(name):
    assert getattr(production_service, name) is getattr(lot_status_service, name)


def _imported_modules(module):
    names = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_lot_status_service_does_not_import_production_service():
    """production_service が lot_status_service を import するので、逆向きにすると循環 import になる。"""
    imported = _imported_modules(lot_status_service)
    assert "services.production_service" not in imported
    assert not {m.split(".")[0] for m in imported} & {"ui", "tkinter", "tkcalendar"}


@pytest.mark.parametrize("name", ["_build_report_rows", "build_daily_report", "build_monthly_report"])
def test_production_service_reexports_report(name):
    from services import production_report_service
    assert getattr(production_service, name) is getattr(production_report_service, name)


def test_report_service_does_not_import_production_service():
    from services import production_report_service
    imported = _imported_modules(production_report_service)
    assert "services.production_service" not in imported
    assert not {m.split(".")[0] for m in imported} & {"ui", "tkinter", "tkcalendar"}
