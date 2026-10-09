"""
services/production_entry_service.py の単体テスト。期待値は、移す前のコードが返していた値をそのまま固定している。
_resolve_csv_report_date と compute_ng_save_qty は、docs/domain/production_entry.md のルールを1つずつテストにしている。
"""
import types

import pytest

from services import production_entry_service as pes
from services.production_entry_service import (
    _resolve_csv_report_date,
    compute_ng_save_qty,
    is_plan_row_completed,
    plan_row_tag,
)

PLAN = {"kitting_list_no": "K1"}
BOTH_SIDES = {"1": PLAN, "2": {"kitting_list_no": "K2"}}


# ---------------------------------------------------------------- _resolve_csv_report_date
# ルール: 実績の日付は CSV の払い出し日のまま。必ず "YYYY-MM-DD" に正規化する。解釈できなければ None（登録側で実行日を使う）。

def test_rule_csv_slash_date_without_zero_padding_is_normalized():
    """CSV の日付は "2026/3/18" のようにスラッシュ区切り・ゼロ埋めなしのことがある。"""
    assert _resolve_csv_report_date("2026/3/18") == "2026-03-18"
    assert _resolve_csv_report_date("2026/9/5") == "2026-09-05"


def test_rule_standard_format_is_kept():
    assert _resolve_csv_report_date("2026-09-05") == "2026-09-05"
    assert _resolve_csv_report_date("2026-9-5") == "2026-09-05"


def test_rule_unparseable_date_returns_none():
    """解釈できない日付は None（登録側の register_daily_result() などが実行日を使う）。"""
    for value in (None, "", "abc", "2026/13/01", "2026.03.18", "2026/07/21 23:50:00"):
        assert _resolve_csv_report_date(value) is None


def test_rule_normalized_dates_sort_correctly_as_strings():
    """表記ゆれが残ると文字列の範囲検索（report_date >= ? AND <= ?）が壊れる。正規化後は文字列の順＝日付の順。"""
    raw = ["2026/3/18", "2026/3/9", "2026/10/1", "2026-02-28"]
    normalized = [_resolve_csv_report_date(v) for v in raw]
    assert sorted(normalized) == ["2026-02-28", "2026-03-09", "2026-03-18", "2026-10-01"]
    assert sorted(raw) != ["2026-02-28", "2026/3/9", "2026/3/18", "2026/10/1"]  # 正規化しないと順がずれる例


def test_surrounding_spaces_are_ignored():
    assert _resolve_csv_report_date("  2026/3/18 ") == "2026-03-18"


# ---------------------------------------------------------------- compute_ng_save_qty
# ルール: 面1の保存値＝面1欄＋面2欄、面2の保存値＝面2欄のみ、合計0の面は保存しない、過去の保存値には加算しない。

def test_rule_side1_saves_side1_plus_side2():
    assert compute_ng_save_qty({"1": 3.0, "2": 5.0}, BOTH_SIDES) == {"1": 8.0, "2": 5.0}


def test_rule_side1_gets_side2_value_even_if_side1_is_empty():
    """例: 面1欄=空・面2欄=5 → 面1へ5。"""
    assert compute_ng_save_qty({"1": 0.0, "2": 5.0}, BOTH_SIDES) == {"1": 5.0, "2": 5.0}


def test_rule_side2_saves_only_side2_value():
    """面1欄の値は面2に影響しない。"""
    assert compute_ng_save_qty({"1": 3.0, "2": 0.0}, BOTH_SIDES) == {"1": 3.0}
    assert compute_ng_save_qty({"1": 3.0, "2": 2.0}, BOTH_SIDES)["2"] == 2.0


def test_rule_zero_total_is_not_saved():
    """合計が0の面は保存しない（既存の NG 申告に触れない）。"""
    assert compute_ng_save_qty({"1": 0.0, "2": 0.0}, BOTH_SIDES) == {}
    assert compute_ng_save_qty({}, BOTH_SIDES) == {}


def test_rule_does_not_add_to_previous_values():
    """今回の入力だけで決まる（過去の保存値に加算しない）。同じ入力なら何度呼んでも同じ結果。"""
    own = {"1": 3.0, "2": 5.0}
    first = compute_ng_save_qty(own, BOTH_SIDES)
    second = compute_ng_save_qty(own, BOTH_SIDES)
    assert first == second == {"1": 8.0, "2": 5.0}
    assert own == {"1": 3.0, "2": 5.0}  # 入力を書き換えない


def test_rule_side1_own_share_is_side1_minus_side2():
    """面1固有分＝面1保存値−面2保存値。保存値から入力欄の面1の値を復元できる（_setup_ng_side_ui() の前提）。"""
    for own1, own2 in [(3.0, 5.0), (0.0, 5.0), (4.0, 1.0)]:
        saved = compute_ng_save_qty({"1": own1, "2": own2}, BOTH_SIDES)
        assert saved["1"] - saved.get("2", 0.0) == own1


def test_side_without_plan_is_not_saved():
    """反対側の計画が無い面（片面のみの計画）は保存しない。"""
    assert compute_ng_save_qty({"1": 3.0}, {"1": PLAN, "2": None}) == {"1": 3.0}
    assert compute_ng_save_qty({"1": 3.0, "2": 5.0}, {"1": PLAN, "2": None}) == {"1": 8.0}
    assert compute_ng_save_qty({"2": 4.0}, {"1": None, "2": PLAN}) == {"2": 4.0}
    assert compute_ng_save_qty({"1": 3.0, "2": 4.0}, {"1": None, "2": None}) == {}


def test_old_method_delegates_to_new_function():
    """旧 KittingProductionEntryWindow._compute_ng_save_qty() は、self._ng_side_plans を渡して新しい関数を呼ぶ。"""
    from ui.kitting_production_entry import KittingProductionEntryWindow
    fake_self = types.SimpleNamespace(_ng_side_plans=BOTH_SIDES)
    assert KittingProductionEntryWindow._compute_ng_save_qty(fake_self, {"1": 3.0, "2": 5.0}) == {"1": 8.0, "2": 5.0}


# ---------------------------------------------------------------- is_plan_row_completed
# ルール: 「入力済みを隠す」は、行の (file_no, 面) の実績合計が、その行の発注数以上かで判定する。

FILE_NO_INDEX = 3
ORDER_QTY_INDEX = 6


def _plan_row(file_no="F1", order_qty="100", side="1"):
    # cols_plan（11列）＋隠し要素2つ（分類・production_side）。発注数は表示用の文字列。
    return ("K1", "L1", "2026/07/22 08:00:00", file_no, "BoardA", "100", order_qty, "0", "0", "0", "0", "", side)


@pytest.mark.parametrize("file_actuals, row, expected", [
    ({("F1", "1"): 100}, _plan_row(), True),                     # 実績合計＝発注数
    ({("F1", "1"): 150}, _plan_row(), True),
    ({("F1", "1"): 99}, _plan_row(), False),
    ({("F1", "2"): 100}, _plan_row(side="1"), False),             # 面が違えば別の合計
    ({("F1", "2"): 100}, _plan_row(side="2"), True),
    ({}, _plan_row(), False),                                    # 実績0（未登録）は未完了
    ({}, _plan_row(order_qty="0"), True),                        # 発注数0なら 0 >= 0 で入力済み扱い（現在の挙動）
])
def test_is_plan_row_completed(file_actuals, row, expected):
    lot_info = {"file_actuals": file_actuals, "completed_quantity": 0}
    assert is_plan_row_completed(row, lot_info, FILE_NO_INDEX, ORDER_QTY_INDEX) is expected


def test_is_plan_row_completed_does_not_use_lot_completed_quantity():
    """ロット完成数（completed_quantity）とは比べない。比べると実績0のロットが 0 >= 0 で「完了」と誤判定される。"""
    lot_info = {"file_actuals": {}, "completed_quantity": 0}
    assert is_plan_row_completed(_plan_row(order_qty="100"), lot_info, FILE_NO_INDEX, ORDER_QTY_INDEX) is False


# ---------------------------------------------------------------- plan_row_tag

@pytest.mark.parametrize("classification, expected", [
    ("b", "needs_side2_wait"),            # 面2待ち
    ("c", "side_master_unregistered"),    # 生産面マスター未登録
    ("a", ""),                            # 片面の製品は色を付けない
    ("", ""),
    (None, ""),
    ("B", ""),
])
def test_plan_row_tag(classification, expected):
    assert plan_row_tag(classification) == expected


# ---------------------------------------------------------------- 旧位置と依存

def test_old_location_reexports_resolve_csv_report_date():
    from ui import kitting_production_entry as old
    assert old._resolve_csv_report_date is pes._resolve_csv_report_date


def test_new_module_does_not_import_gui():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(pes))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not names & {"tkinter", "tkcalendar", "ui"}
