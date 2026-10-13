"""
3画面（生産実績入力の計画一覧・NG 一覧・仕掛一覧）の絞り込み・並べ替えの判定を固定する（段階6）。
画面は表示しない。偽の self に、本物のクラスのメソッドを結び付けて呼ぶ。ウィジェットは値を返すだけの偽物にする。
"""
import types

import pytest

from ui.kitting_production_entry import KittingProductionEntryWindow
from ui.ng_input_window import NgInputWindow
from ui.wip_expansion_window import WipExpansionWindow


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _DateEntry:
    def __init__(self, text=""):
        self.text = text

    def get(self):
        return self.text

    def delete(self, first, last=None):
        self.text = ""


class _Tree:
    """sort_*_list() が使う Treeview の機能だけ（get_children・set・move）。"""
    def __init__(self, cols, rows):
        self.cols = cols
        self.order = [f"I{i}" for i in range(len(rows))]
        self.values = {iid: dict(zip(cols, row)) for iid, row in zip(self.order, rows)}

    def get_children(self, item=""):
        return tuple(self.order)

    def set(self, iid, col):
        return self.values[iid][col]

    def move(self, iid, parent, index):
        self.order.remove(iid)
        self.order.insert(index, iid)

    def column_values(self, col):
        return [self.values[iid][col] for iid in self.order]


PLAN_COLS = ("list_no", "lot_no", "plan_start_datetime", "file_no", "board_name",
             "planned_qty", "order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining")

SCREENS = {
    "plan": dict(
        cls=KittingProductionEntryWindow, cols=PLAN_COLS,
        text_cols=("list_no", "planned_qty", "order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining"),
        populate="_populate_plan_list_tree", tree="tree_plan_list", sort_states="plan_sort_states",
    ),
    "ng": dict(
        cls=NgInputWindow, cols=NgInputWindow.NG_LIST_COLUMNS,
        text_cols=("kitting_list_no", "declared_ng_qty", "part_count", "record_count", "last_report_date"),
        populate="_populate_ng_tree", tree="tree_ng_list", sort_states="_ng_sort_states",
    ),
    "wip": dict(
        cls=WipExpansionWindow, cols=WipExpansionWindow.WIP_LIST_COLUMNS,
        text_cols=("kitting_list_no", "surplus_qty", "created_at"),
        populate="_populate_wip_tree", tree="tree_wip_list", sort_states="_wip_sort_states",
    ),
}


def _screen(p, rows):
    spec = SCREENS[p]
    cls = spec["cls"]
    fake = types.SimpleNamespace()
    setattr(fake, f"_{p}_filter_vars", {c: _Var() for c in spec["text_cols"]})
    setattr(fake, f"_{p}_checkbox_filters", {})
    setattr(fake, f"_{p}_col_index", {c: i for i, c in enumerate(spec["cols"])})
    setattr(fake, f"_all_{p}_rows", rows)
    fake.populated = []
    setattr(fake, spec["populate"], lambda shown: fake.populated.append(shown))
    for name in (f"_{p}_filter_predicates", f"_{p}_row_matches", f"apply_{p}_filters", f"sort_{p}_list"):
        setattr(fake, name, types.MethodType(getattr(cls, name), fake))
    if p == "plan":
        fake._plan_date_from_entry = _DateEntry()
        fake._plan_date_to_entry = _DateEntry()
        fake._hide_completed_var = _Var(False)
        fake._plan_date_range_predicate = types.MethodType(cls._plan_date_range_predicate, fake)
    setattr(fake, spec["sort_states"], {})
    return fake


def _row(p, **values):
    return tuple(values.get(c, "") for c in SCREENS[p]["cols"])


def _apply(fake, p):
    getattr(fake, f"apply_{p}_filters")()
    return fake.populated[-1]


# ---------------------------------------------------------------- 3画面に共通の判定

@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_no_filter_passes_the_same_list(p):
    rows = [_row(p, lot_no="L1"), _row(p, lot_no="L2")]
    fake = _screen(p, rows)
    assert _apply(fake, p) is rows   # 絞り込みが無ければ、全件のリストをそのまま渡す


@pytest.mark.parametrize("p, col", [("plan", "list_no"), ("ng", "kitting_list_no"), ("wip", "kitting_list_no")])
def test_text_filter_is_case_insensitive_substring_and_stripped(p, col):
    rows = [_row(p, **{col: "ABC-001"}), _row(p, **{col: "xyz-002"}), _row(p, **{col: "abc-003"})]
    fake = _screen(p, rows)
    getattr(fake, f"_{p}_filter_vars")[col].set("  aBc ")
    assert [r[SCREENS[p]["cols"].index(col)] for r in _apply(fake, p)] == ["ABC-001", "abc-003"]
    getattr(fake, f"_{p}_filter_vars")[col].set("   ")
    assert _apply(fake, p) is rows   # 空白だけは絞り込みなし


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_checkbox_filter_is_exact_match_on_str_value(p):
    rows = [_row(p, lot_no="L1"), _row(p, lot_no="L10"), _row(p, lot_no=""), _row(p, lot_no=None)]
    fake = _screen(p, rows)
    getattr(fake, f"_{p}_checkbox_filters")["lot_no"] = {"L1", ""}
    index = SCREENS[p]["cols"].index("lot_no")
    assert [r[index] for r in _apply(fake, p)] == ["L1", ""]
    getattr(fake, f"_{p}_checkbox_filters")["lot_no"] = {"None"}   # 値は str() してから比べる
    assert [r[index] for r in _apply(fake, p)] == [None]


@pytest.mark.parametrize("p, text_col", [("plan", "list_no"), ("ng", "kitting_list_no"), ("wip", "kitting_list_no")])
def test_filters_are_and_and_none_value_is_text_none(p, text_col):
    rows = [_row(p, lot_no="L1", **{text_col: "K1"}), _row(p, lot_no="L2", **{text_col: "K1"}),
            _row(p, lot_no="L1", **{text_col: None})]
    fake = _screen(p, rows)
    getattr(fake, f"_{p}_checkbox_filters")["lot_no"] = {"L1"}
    getattr(fake, f"_{p}_filter_vars")[text_col].set("k")
    assert _apply(fake, p) == [rows[0]]
    getattr(fake, f"_{p}_filter_vars")[text_col].set("non")   # None は "None" として部分一致する
    assert _apply(fake, p) == [rows[2]]


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_predicates_keys(p):
    fake = _screen(p, [])
    text_col = SCREENS[p]["text_cols"][0]
    getattr(fake, f"_{p}_filter_vars")[text_col].set("x")
    getattr(fake, f"_{p}_checkbox_filters")["lot_no"] = set()
    predicates = getattr(fake, f"_{p}_filter_predicates")()
    assert list(predicates) == [text_col, "lot_no"]
    assert predicates["lot_no"]("anything") is False   # 何も選ばない＝どれも通さない


# ---------------------------------------------------------------- 並べ替え

SORT_CASES = {
    "plan": ("planned_qty", "list_no"),
    "ng": ("declared_ng_qty", "kitting_list_no"),
    "wip": ("surplus_qty", "kitting_list_no"),
}


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_sort_numeric_column(p):
    numeric_col, _ = SORT_CASES[p]
    rows = [_row(p, **{numeric_col: v}) for v in ("10", "9", "", "abc", "100", "-1.5")]
    fake = _screen(p, rows)
    tree = _Tree(SCREENS[p]["cols"], rows)
    setattr(fake, SCREENS[p]["tree"], tree)
    getattr(fake, f"sort_{p}_list")(numeric_col)
    # 数値にできない値は -inf（先頭）。同じキーは元の順のまま。
    assert tree.column_values(numeric_col) == ["", "abc", "-1.5", "9", "10", "100"]
    assert getattr(fake, SCREENS[p]["sort_states"]) == {numeric_col: False}
    getattr(fake, f"sort_{p}_list")(numeric_col)   # 2回目は降順
    assert tree.column_values(numeric_col) == ["100", "10", "9", "-1.5", "", "abc"]
    assert getattr(fake, SCREENS[p]["sort_states"]) == {numeric_col: True}


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_sort_text_column_is_plain_string_order(p):
    _, text_col = SORT_CASES[p]
    rows = [_row(p, **{text_col: v}) for v in ("K10", "K2", "k1", "", "K1")]
    fake = _screen(p, rows)
    tree = _Tree(SCREENS[p]["cols"], rows)
    setattr(fake, SCREENS[p]["tree"], tree)
    getattr(fake, f"sort_{p}_list")(text_col)
    assert tree.column_values(text_col) == ["", "K1", "K10", "K2", "k1"]


@pytest.mark.parametrize("p, numeric", [
    ("plan", {"planned_qty", "order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining"}),
    ("ng", {"declared_ng_qty", "part_count", "record_count"}),
    ("wip", {"surplus_qty"}),
])
def test_numeric_columns_per_screen(p, numeric):
    """数値として並べる列は画面ごとに違う。それ以外の列は、数字でも文字列として並べる。"""
    for col in SCREENS[p]["cols"]:
        rows = [_row(p, **{col: v}) for v in ("10", "9")]
        fake = _screen(p, rows)
        tree = _Tree(SCREENS[p]["cols"], rows)
        setattr(fake, SCREENS[p]["tree"], tree)
        getattr(fake, f"sort_{p}_list")(col)
        expected = ["9", "10"] if col in numeric else ["10", "9"]
        assert tree.column_values(col) == expected, col


# ---------------------------------------------------------------- 計画一覧だけの期間指定

@pytest.mark.parametrize("from_text, to_text, expected", [
    ("", "", ["2026/07/20 08:00:00", "2026/07/21 23:59:59", "2026/07/22 00:00:00", "2026-07-21", "", "2026/7/21 08:00:00"]),
    ("2026-07-21", "", ["2026/07/21 23:59:59", "2026/07/22 00:00:00", "2026-07-21", "2026/7/21 08:00:00"]),
    ("", "2026-07-21", ["2026/07/20 08:00:00", "2026/07/21 23:59:59", "2026-07-21", ""]),
    ("2026-07-21", "2026-07-21", ["2026/07/21 23:59:59", "2026-07-21"]),
    ("2026/07/21", "2026/07/21", ["2026/07/21 23:59:59", "2026-07-21"]),
    (" 2026-07-22 ", "", ["2026/07/22 00:00:00", "2026/7/21 08:00:00"]),
])
def test_plan_date_range(from_text, to_text, expected):
    """先頭10文字を「/」区切りにそろえて文字列で比べる（ゼロ埋めなしの値は文字列の順になる）。"""
    values = ["2026/07/20 08:00:00", "2026/07/21 23:59:59", "2026/07/22 00:00:00", "2026-07-21", "", "2026/7/21 08:00:00"]
    rows = [_row("plan", plan_start_datetime=v) for v in values]
    fake = _screen("plan", rows)
    fake._plan_date_from_entry.text = from_text
    fake._plan_date_to_entry.text = to_text
    shown = _apply(fake, "plan")
    assert [r[2] for r in shown] == expected
    assert ("plan_start_datetime" in fake._plan_filter_predicates()) == bool(from_text.strip() or to_text.strip())


def test_plan_date_range_with_entries_missing():
    fake = _screen("plan", [])
    fake._plan_date_from_entry = None
    fake._plan_date_to_entry = None
    assert fake._plan_date_range_predicate() is None
