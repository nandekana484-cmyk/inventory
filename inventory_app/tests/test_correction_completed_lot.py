"""
実績修正画面の「修正」で、完成済みロットの数量を増やすときの確認（D-156、D-149 の解決）。
合成データ（scripts/make_lot_merge_sample_db.py と、利用者の例のロット 500500・500500-）で、ダイアログを差し替えて確かめる。
画面は自動でクリックせず、ウィンドウを直接作って on_update() を呼ぶ。
"""
import pytest

from models.production import list_daily_production_by_kitting_no
from services import lot_merge_service as svc
from services.lot_status_service import preview_completed_lot_correction
from models.kitting_plan import find_plan_item_by_kitting_no
from tests.test_lot_merge import sample_db  # noqa: F401 （fixture）
from tests.test_lot_merge_aggregates import example_db  # noqa: F401 （fixture）
from ui.kitting import actual_correction_window as acw

WORKER = {"worker_id": "W1", "name": "作業者A", "role": "operator"}
LINE = "【完成済みのロット】"


@pytest.fixture
def dialogs(monkeypatch):
    record = {"calls": [], "answers": []}

    def fake(kind):
        def f(title, message, **kwargs):
            record["calls"].append((kind, title, message))
            if kind == "askyesno":
                return record["answers"].pop(0) if record["answers"] else True
            return None
        return f

    for kind in ("askyesno", "showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(acw.messagebox, kind, fake(kind))
    return record


def _correct(tk_root, kitting_list_no, lot_no, new_qty):
    w = acw.ActualCorrectionWindow(tk_root, kitting_list_no, lot_no, current_worker=WORKER)
    w.geometry("+3000+3000")
    w.update()
    iid = w.tree.get_children()[0]
    w.tree.selection_set(iid)
    w.on_select_history(None)
    w.entry_edit_qty.delete(0, "end")
    w.entry_edit_qty.insert(0, str(new_qty))
    w.on_update()
    w.destroy()


def _qty(kitting_list_no, lot_no):
    return [r["daily_qty"] for r in list_daily_production_by_kitting_no(kitting_list_no, lot_no)]


def _confirm(dialogs):
    return next(m for kind, title, m in dialogs["calls"] if kind == "askyesno" and title == "修正の確認")


def test_completed_lot_increase_asks_and_no_changes_nothing(tk_root, sample_db, dialogs):
    # 450005 は 100／100 で完成済み
    dialogs["answers"] = [False]
    _correct(tk_root, "K450005-1", "450005", 120)
    message = _confirm(dialogs)
    assert LINE in message
    assert "・ロット 450005 は完成済みです（完成数 100／発注数 100）。登録すると、完成数の計算に使う実績の合計が 120 になります。" in message
    assert _qty("K450005-1", "450005") == [100]


def test_completed_lot_increase_yes_updates(tk_root, sample_db, dialogs):
    _correct(tk_root, "K450005-1", "450005", 120)
    assert LINE in _confirm(dialogs)
    assert _qty("K450005-1", "450005") == [120]


def test_completed_lot_decrease_does_not_ask(tk_root, sample_db, dialogs):
    _correct(tk_root, "K450005-1", "450005", 90)
    assert LINE not in _confirm(dialogs)
    assert _qty("K450005-1", "450005") == [90]


def test_same_qty_does_not_ask(sample_db):
    plan = find_plan_item_by_kitting_no("K450005-1", "450005")
    assert preview_completed_lot_correction(plan, 100, 100) is None


def test_incomplete_lot_increase_does_not_ask(tk_root, sample_db, dialogs):
    # 137599 は 300／500 で未完成
    _correct(tk_root, "K137599-1", "137599", 350)
    assert LINE not in _confirm(dialogs)
    assert _qty("K137599-1", "137599") == [350]


def test_merged_lot_completed_after_merge_asks(tk_root, example_db, dialogs):
    # 500500 単独は 950／1000 で未完成。500500- と統合すると 1000／1000 で完成済み（統合後のロットで判定する）
    dialogs["answers"] = [False]
    _correct(tk_root, "K500500-1", "500500", 960)
    assert LINE not in _confirm(dialogs)
    dialogs["calls"].clear()
    svc.merge_lots("500500-", "作業者A")
    dialogs["answers"] = [False]
    _correct(tk_root, "K500500-1", "500500", 960)
    # 面1には 500500- の面2の 50 も数える（D-151）ので、面1の合計は 950＋50 → 960＋50
    assert "・ロット 500500（+500500-） は完成済みです（完成数 1000／発注数 1000）。登録すると、完成数の計算に使う実績の合計が 1010 になります。" in _confirm(dialogs)
    assert _qty("K500500-1", "500500") == [950]


def test_opposite_side_link_and_completed_lot_in_one_dialog(tk_root, example_db, dialogs):
    svc.merge_lots("500500-", "作業者A")
    dialogs["answers"] = [False]
    _correct(tk_root, "K500500-2", "500500", 960)
    asks = [c for c in dialogs["calls"] if c[0] == "askyesno"]
    assert len(asks) == 1
    message = asks[0][2]
    assert "【連動して変わる反対側の実績】" in message and "数量：950 → 960" in message
    assert "・ロット 500500（+500500-） は完成済みです（完成数 1000／発注数 1000）。登録すると、完成数の計算に使う実績の合計が 1010 になります。" in message
    assert _qty("K500500-2", "500500") == [950] and _qty("K500500-1", "500500") == [950]
