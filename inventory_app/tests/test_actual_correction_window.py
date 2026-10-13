"""ui/kitting/actual_correction_window.py（段階2で ui/kitting/kitting_production_entry.py から移した）の移動の確認。"""
import ast
import inspect

from ui.kitting import actual_correction_window as acw


def test_old_location_reexports_same_class():
    """旧位置（ui.kitting.kitting_production_entry）から import しても、同じクラスが返る。"""
    from ui.kitting import kitting_production_entry as old
    assert old.ActualCorrectionWindow is acw.ActualCorrectionWindow


def test_new_module_does_not_import_old_module():
    """新しいモジュールが旧位置を import すると循環 import になる。"""
    tree = ast.parse(inspect.getsource(acw))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    assert "ui.kitting.kitting_production_entry" not in modules
