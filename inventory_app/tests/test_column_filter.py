"""ui/column_filter.py の判定（ウィジェットを使わない部分）。3画面での使われ方は test_list_filters.py で固定している。"""
import ast
import inspect

from ui import column_filter as cf

COL_INDEX = {"a": 0, "b": 1}


def test_build_filter_predicates():
    predicates = cf.build_filter_predicates({"a": " Ab ", "b": "  "}, {"b": {"x"}})
    assert list(predicates) == ["a", "b"]
    assert predicates["a"]("XABY") and not predicates["a"]("A-B")
    assert predicates["b"]("x") and not predicates["b"]("X")


def test_checkbox_overrides_text_for_same_column():
    predicates = cf.build_filter_predicates({"a": "z"}, {"a": {"q"}})
    assert predicates["a"]("q") and not predicates["a"]("z")


def test_row_matches_and_filter_rows():
    rows = [("K1", None), ("K2", "L1")]
    predicates = cf.build_filter_predicates({"b": "none"}, {})
    assert cf.row_matches(rows[0], predicates, COL_INDEX) is True
    assert cf.filter_rows(rows, predicates, COL_INDEX) == [rows[0]]
    assert cf.filter_rows(rows, {}, COL_INDEX) is rows


def test_date_range_predicate():
    assert cf.date_range_predicate("", "") is None
    predicate = cf.date_range_predicate("2026-07-21", "2026/07/22")
    assert [predicate(v) for v in ("2026/07/20 23:59:59", "2026/07/21 00:00:00", "2026-07-22", "2026/07/23")] == \
        [False, True, True, False]


def test_make_sort_key():
    numeric = cf.make_sort_key("qty", {"qty"})
    assert sorted(["10", "", "9", "x"], key=numeric) == ["", "x", "9", "10"]
    text = cf.make_sort_key("name", {"qty"})
    assert sorted(["10", "9"], key=text) == ["10", "9"]


def test_judgement_part_does_not_import_tkinter_at_module_level():
    """判定の関数はウィジェットを使わない（このモジュールの先頭で tkinter を import しない）。"""
    tree = ast.parse(inspect.getsource(cf))
    top_imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("tkinter" in ast.unparse(n) for n in top_imports)
