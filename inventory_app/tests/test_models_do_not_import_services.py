"""models は services・ui を import しない（段階4で models → services の逆転を解消した）。"""
import ast
import pathlib

import pytest

MODELS_DIR = pathlib.Path(__file__).resolve().parent.parent / "models"


@pytest.mark.parametrize("path", sorted(MODELS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_model_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [node.module]
        else:
            continue
        for module in modules:
            assert module.split(".")[0] not in ("services", "ui", "tkinter", "tkcalendar"), f"{path.name}:{node.lineno} {module}"


def test_normalize_product_name_moved_to_models():
    from models.kitting_plan import normalize_product_name
    from services import production_import_service
    assert production_import_service.normalize_product_name is normalize_product_name
    assert normalize_product_name("  ＡＢＣ　 ｄｅｆ ") == "abc def"
    assert normalize_product_name(None) == ""
