"""
3画面のチェックボックス式絞り込みポップアップ（open_*_checkbox_filter_popup）の動きを固定する（段階6）。
本物の Tk のウィンドウを短い時間だけ作る（画面に一瞬出る）。Tk が使えない環境ではスキップする。
画面クラスの本体は作らず、空の Toplevel に、ポップアップが使う属性とメソッドだけを付けて呼ぶ。
"""
import tkinter as tk
import types
from tkinter import ttk

import pytest

from ui.kitting.kitting_production_entry import KittingProductionEntryWindow
from ui.ng_wip.ng_input_window import NgInputWindow
from ui.ng_wip.wip_expansion_window import WipExpansionWindow

SCREENS = {
    "plan": (KittingProductionEntryWindow, ("list_no", "lot_no", "board_name")),
    "ng": (NgInputWindow, ("kitting_list_no", "lot_no", "board_name")),
    "wip": (WipExpansionWindow, ("kitting_list_no", "lot_no", "board_name")),
}
ROWS = [
    ("K1", "L2", "BoardA"),
    ("K2", "L1", "BoardB"),
    ("K3", "L10", "boardA"),
    ("K4", "", "BoardA"),
    ("K5", "L1", "BoardC"),
]


# tk_root はテスト全体で共通（tests/conftest.py）。


@pytest.fixture
def root(tk_root):
    """テストごとに、ルートの下に作った画面（Toplevel）をすべて閉じる。"""
    yield tk_root
    for child in tk_root.winfo_children():
        child.destroy()


def _host(root, p):
    cls, cols = SCREENS[p]
    host = tk.Toplevel(root)
    host.geometry("+3000+3000")
    host.update()
    setattr(host, f"_{p}_filter_labels", {"lot_no": "ロットNo.", "board_name": "基板名", cols[0]: "No."})
    setattr(host, f"_{p}_col_index", {c: i for i, c in enumerate(cols)})
    setattr(host, f"_all_{p}_rows", list(ROWS))
    setattr(host, f"_{p}_filter_vars", {cols[0]: tk.StringVar(master=host)})
    setattr(host, f"_{p}_checkbox_filters", {})
    host.applied = []
    host.styled = []
    for name in (f"_{p}_filter_predicates", f"_{p}_row_matches", f"open_{p}_checkbox_filter_popup"):
        setattr(host, name, types.MethodType(getattr(cls, name), host))
    setattr(host, f"apply_{p}_filters", lambda event=None: host.applied.append(dict(getattr(host, f"_{p}_checkbox_filters"))))
    setattr(host, f"_update_{p}_filter_button_style", lambda col_key: host.styled.append(col_key))
    if p == "plan":
        host._plan_date_from_entry = None
        host._plan_date_to_entry = None
        host._plan_date_range_predicate = types.MethodType(KittingProductionEntryWindow._plan_date_range_predicate, host)
    return host


def _widgets(widget, kind):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, kind):
            found.append(child)
        found.extend(_widgets(child, kind))
    return found


def _checkbuttons(popup):
    return _widgets(popup, ttk.Checkbutton)


def _visible_texts(popup):
    popup.update()
    return [c.cget("text") for c in _checkbuttons(popup) if c.winfo_manager() == "pack"]


def _checked_texts(popup):
    return [c.cget("text") for c in _checkbuttons(popup) if c.instate(["selected"])]


def _button(popup, text):
    return next(b for b in _widgets(popup, ttk.Button) if b.cget("text") == text)


def _open(host, p, col_key):
    return getattr(host, f"open_{p}_checkbox_filter_popup")(col_key)


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_popup_layout_and_values(root, p):
    host = _host(root, p)
    popup = _open(host, p, "lot_no")
    try:
        assert popup.title() == "ロットNo. の絞り込み"
        assert popup.winfo_toplevel() is popup
        assert str(popup.transient()) == str(host)
        popup.update()
        assert (popup.winfo_width(), popup.winfo_height()) == (280, 420)
        # 候補は str() した値の重複なし・文字列の順。空の値も1つの候補になる。最初は全部チェック済み。
        assert _visible_texts(popup) == ["", "L1", "L10", "L2"]
        assert _checked_texts(popup) == ["", "L1", "L10", "L2"]
        assert [b.cget("text") for b in _widgets(popup, ttk.Button)] == ["全選択", "全解除", "OK", "キャンセル"]
        assert [l.cget("text") for l in _widgets(popup, ttk.Label)] == ["検索："]
        assert popup.focus_get() is _widgets(popup, ttk.Entry)[0]
    finally:
        popup.destroy()


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_popup_values_respect_other_filters(root, p):
    """候補は、この列以外の絞り込みを適用した後の値（エクセルのオートフィルタと同じ）。"""
    host = _host(root, p)
    getattr(host, f"_{p}_checkbox_filters")["board_name"] = {"BoardA"}
    getattr(host, f"_{p}_checkbox_filters")["lot_no"] = {"L2"}   # この列自身の絞り込みは候補に効かない
    popup = _open(host, p, "lot_no")
    try:
        assert _visible_texts(popup) == ["", "L2"]
        assert _checked_texts(popup) == ["L2"]   # 今の選択を反映する
    finally:
        popup.destroy()


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_popup_search_select_and_ok(root, p):
    host = _host(root, p)
    popup = _open(host, p, "board_name")
    entry = _widgets(popup, ttk.Entry)[0]
    entry.insert(0, " BOARDa ")
    assert _visible_texts(popup) == ["BoardA", "boardA"]   # 大文字・小文字を区別しない部分一致（前後の空白は除く）
    _button(popup, "全解除").invoke()                       # 全解除・全選択は、検索で隠れている候補にも効く
    assert _checked_texts(popup) == []
    _checkbuttons(popup)[0].invoke()                        # BoardA だけチェック
    _button(popup, "OK").invoke()
    assert not popup.winfo_exists()
    assert getattr(host, f"_{p}_checkbox_filters") == {"board_name": {"BoardA"}}
    assert host.styled == ["board_name"]
    assert host.applied == [{"board_name": {"BoardA"}}]


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_popup_all_selected_means_no_filter(root, p):
    host = _host(root, p)
    getattr(host, f"_{p}_checkbox_filters")["lot_no"] = {"L1"}
    popup = _open(host, p, "lot_no")
    _button(popup, "全選択").invoke()
    _button(popup, "OK").invoke()
    assert getattr(host, f"_{p}_checkbox_filters") == {}
    assert host.styled == ["lot_no"]
    assert host.applied == [{}]


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_popup_cancel_changes_nothing(root, p):
    host = _host(root, p)
    popup = _open(host, p, "lot_no")
    _button(popup, "全解除").invoke()
    _button(popup, "キャンセル").invoke()
    assert not popup.winfo_exists()
    assert getattr(host, f"_{p}_checkbox_filters") == {}
    assert host.styled == [] and host.applied == []


@pytest.mark.parametrize("p", ["plan", "ng", "wip"])
def test_popup_deiconifies_minimized_parent(root, p):
    host = _host(root, p)
    host.iconify()
    host.update()
    popup = _open(host, p, "lot_no")
    try:
        assert host.state() != "iconic"
    finally:
        popup.destroy()
