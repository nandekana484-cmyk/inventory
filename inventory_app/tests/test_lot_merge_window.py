"""
ロット統合の画面（D-145）。合成データ（scripts/make_lot_merge_sample_db.py）で、候補の一覧・確認ダイアログ・在庫値出力済みの警告・操作履歴を確かめる。
ダイアログは差し替えて、出した文言と答えを記録する（画面は自動でクリックしない。メソッドを直接呼ぶ）。
"""
import pytest

from models.lot_merges import list_lot_merges
from models.operation_log import OPERATION_NAME_INVENTORY_DIFF_EXPORT, list_operation_log, log_operation
from tests.test_lot_merge import sample
from ui.production import lot_merge_window as lmw
from ui.production import lot_progress_window as lpw

WORKER = {"worker_id": "W1", "name": "作業者A", "role": "operator"}


@pytest.fixture
def dialogs(monkeypatch):
    """messagebox を差し替える。answers に askyesno の答えを順に積む（空なら True）。"""
    record = {"calls": [], "answers": []}

    def fake(kind):
        def f(title, message, **kwargs):
            record["calls"].append((kind, title, message))
            if kind == "askyesno":
                return record["answers"].pop(0) if record["answers"] else True
            return None
        return f

    for kind in ("askyesno", "showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(lmw.messagebox, kind, fake(kind))
    return record


@pytest.fixture
def window(tk_root, dialogs):
    sample.build(__import__("config").APP_DATA_DIR)
    w = lmw.LotMergeWindow(tk_root, current_worker=WORKER)
    w.geometry("+3000+3000")
    w.update()
    yield w
    for child in tk_root.winfo_children():
        child.destroy()


def _rows(w):
    return [w.tree.item(iid, "values") for iid in w.tree.get_children()]


def _select(w, lot_no):
    iid = next(i for i in w.tree.get_children() if w.tree.item(i, "values")[1] == lot_no)
    w.tree.selection_set(iid)


def test_lists_candidate_pairs(window):
    lots = {r[1] for r in _rows(window)}
    assert lots == {lot for pair in sample.EXPECTED_CANDIDATES.items() for lot in pair}
    assert window.lbl_count.cget("text") == "4組（統合済み 0組・未統合 4組）"
    member_rows = [r for r in _rows(window) if r[1] == "163326-"]
    assert member_rows == [("未統合", "163326-", "統合される", "BRD-1633", "F1633", "2", "170", "1", "一致")]


def test_merge_with_confirmation(window, dialogs):
    _select(window, "163326-")
    window.on_merge()
    kind, title, message = dialogs["calls"][0]
    assert (kind, title) == ("askyesno", "ロット統合")
    assert message.startswith("163326- を 163326 へ統合しますか？")
    assert "統合すると、163326- の実績の日付の引落が、後から増えることがあります（例：7/15 に +1）。" in message
    # 第2段階（D-153）: 統合した場合の試算を出し、「この版では集計にまだ反映されません」は出さない
    assert "・統合後の 163326（+163326-）：発注数 1000（163326 の値）、完成数 100、残り 900" in message
    assert "この版では" not in message
    assert dialogs["calls"][-1] == ("showinfo", "ロット統合", "163326- を 163326 へ統合しました。")
    assert [(r["member_lot"], r["merged_by"]) for r in list_lot_merges()] == [("163326-", "作業者A")]
    assert (list_operation_log()[0]["worker_name"], list_operation_log()[0]["operation_name"]) == ("作業者A", "ロット統合")
    assert {r[0] for r in _rows(window) if r[1] in ("163326", "163326-")} == {f"統合済み（{list_lot_merges()[0]['merged_at'][:10]}）"}
    assert window.lbl_count.cget("text") == "4組（統合済み 1組・未統合 3組）"


def test_merge_cancelled_changes_nothing(window, dialogs):
    dialogs["answers"] = [False]
    _select(window, "137599")          # 統合先の行を選んでも、その組を統合する
    window.on_merge()
    assert dialogs["calls"][0][2].startswith("137599- を 137599 へ統合しますか？")
    assert list_lot_merges() == []
    assert [r for r in list_operation_log() if r["operation_name"] == "ロット統合"] == []


def test_unmerge_with_confirmation(window, dialogs):
    _select(window, "226793-")
    window.on_merge()
    dialogs["calls"].clear()
    _select(window, "226793-")
    window.on_unmerge()
    kind, title, message = dialogs["calls"][0]
    assert (kind, title) == ("askyesno", "ロット統合の解除")
    assert "統合している間に登録した実績などはそのまま残ります。" in message
    assert list_lot_merges() == []
    assert list_operation_log()[0]["operation_name"] == "ロット統合解除"


def test_unmerge_of_unmerged_pair_does_nothing(window, dialogs):
    _select(window, "348588-")
    window.on_unmerge()
    assert dialogs["calls"] == [("showinfo", "ロット統合", "348588- は統合されていません。")]


def test_exported_db_warns_before_merge(window, dialogs):
    log_operation("作業者A", OPERATION_NAME_INVENTORY_DIFF_EXPORT, "test")
    dialogs["answers"] = [False]
    _select(window, "163326-")
    window.on_merge()
    assert dialogs["calls"] == [(
        "askyesno", "出力済みデータベース", "このDBは在庫値出力済みです。入力した内容はこの古いDBに保存されます。\n続けますか？")]
    assert list_lot_merges() == []

    dialogs["calls"].clear()
    dialogs["answers"] = [True, True]                   # 警告で続ける → 統合の確認で「はい」
    window.on_merge()
    assert [c[1] for c in dialogs["calls"]] == ["出力済みデータベース", "ロット統合", "ロット統合"]
    assert len(list_lot_merges()) == 1


def test_lot_progress_window_opens_merge_window(tk_root, dialogs, monkeypatch):
    sample.build(__import__("config").APP_DATA_DIR)
    monkeypatch.setattr(lpw.LotProgressWindow, "refresh", lambda self: None)   # 別スレッドの集計はここでは要らない
    progress = lpw.LotProgressWindow(tk_root, current_worker=WORKER)
    try:
        progress.update()
        frames = [f for f in progress.winfo_children() if f.winfo_children()]
        texts = [[c.cget("text") for c in f.winfo_children() if c.winfo_class() == "TButton"] for f in frames]
        assert ["CSV出力", "ロット統合"] in texts
        progress.open_lot_merge_window()
        merge_window = progress._lot_merge_window
        merge_window.update()
        assert isinstance(merge_window, lmw.LotMergeWindow) and merge_window.current_worker == WORKER
        progress.open_lot_merge_window()                 # 2回目は同じ画面を前面に出すだけ
        assert progress._lot_merge_window is merge_window
    finally:
        for child in tk_root.winfo_children():
            child.destroy()
