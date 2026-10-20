"""
登録待ち一覧の改善（D-176〜D-178）：同じ計画への重複の確認、似たロットNoの候補、登録した後のほかの行の状態の更新。
画面は表示しない。偽の画面は test_staging_registration.py のもの（状態の列を書き換えられるよう、Treeview の set・item だけ足す）。
"""
import os
import types

import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version
from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row, list_pending_csv_import_rows
from services.production_import_service import parse_production_csv_for_staging
from services.production_service import get_daily_history
from services.staging_eligibility import _load_staged_rows_from_db
from ui.production import production_import_staging_window as psw
from ui.production.duplicate_plan_dialog import build_duplicate_lines, DUPLICATE_SUM, DUPLICATE_MANUAL
from tests.test_staging_registration import (  # noqa: F401 （fixture）
    _FakeParent, _FakeStaging, _FakeTree, _messages, events, answers,
)

# (kitting_list_no, lot_no, setup_file_no, 面, 基板名, 予定数)
PLANS = [
    ("KD", "332650", "0292", "2", "ProdD", 100),       # 同じ計画を2行（7/15 の 66、8/6 の 34）が候補にする
    ("KS", "326611-", "0188", "1", "ProdS", 1186),     # 326611 の行の、似たロットNoの計画
    ("KZ", "500000", "0500", "1", "ProdZ", 10),        # 似たロットNoの対象外（500000-1 は末尾が「-」だけではない）
]
# (lot_no, 製品名, 数量, 日付)
PENDING = [
    ("332650", "ProdD", 66, "2026/7/15"),
    ("332650", "ProdD", 34, "2026/8/6"),
    ("326611", "ProdS", 1186, "2026/7/14"),
    ("500000-1", "ProdZ", 10, "2026/7/14"),
]


class _Tree(_FakeTree):
    """偽の Treeview に、状態の列を書き換える set・item を足したもの。"""
    def __init__(self, events, name, iids=(), staging=None):
        super().__init__(events, name, iids)
        self._staging = staging
        self.status = {}

    def set(self, iid, col, value):
        self.status[iid] = value

    def item(self, iid, **kwargs):
        return None


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", "test_month", "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    create_csv_import_batch("actual.csv", imported_by="test")
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=len(PLANS))
    for kitting_list_no, lot_no, file_no, side, board, qty in PLANS:
        create_plan_version(batch, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board, "production_side": side,
            "planned_qty": qty, "order_qty": qty, "plan_start_datetime": "2026/07/14 08:00:00", "mounting_line": "A",
        }, created_by="test")
    for i, (lot_no, product, qty, date) in enumerate(PENDING, start=1):
        upsert_pending_csv_import_row({"csv_row_no": i, "lot_no": lot_no, "product_name": product,
                                       "daily_qty": qty, "report_date": date, "worker_id": "W1"})


@pytest.fixture
def screens(db, events):
    parent = _FakeParent(events)
    staging = _FakeStaging(parent, events, _load_staged_rows_from_db())
    staging.tree = _Tree(events, "staging.tree", staging._row_by_iid)
    parent._csv_staging_window = staging
    return parent, staging


def _iid_of(staging, lot_no, qty):
    return next(iid for iid, r in staging._row_by_iid.items() if r["lot_no"] == lot_no and r["daily_qty"] == qty)


def _label(staging, iid):
    return staging.tree.status.get(iid) or psw.staging_status_label(staging._row_by_iid[iid])


def _history(kitting_list_no, lot_no):
    return [(r["report_date"], r["daily_qty"]) for r in get_daily_history(kitting_list_no, lot_no)]


def _pending():
    return sorted((r["lot_no"], r["daily_qty"]) for r in list_pending_csv_import_rows())


def _double_click(staging, iid):
    staging._current_staging_iid = iid
    staging._current_staging_row = staging._row_by_iid[iid]
    staging.tree_candidates.row_at_event = "C1"
    staging._candidates_by_iid = {"C1": staging._row_by_iid[iid]["matched"][0]}
    staging._on_candidate_double_click(types.SimpleNamespace(y=0))


def _right(staging, iid):
    staging._current_staging_iid = iid
    staging._current_staging_row = staging._row_by_iid[iid]
    staging.tree_candidates.row_at_event = "C1"
    staging._candidates_by_iid = {"C1": staging._row_by_iid[iid]["matched"][0]}
    staging._on_candidate_right_click(types.SimpleNamespace(y=0))


# ---------------------------------------------------------------- 状態の文言（D-176・D-177）

def test_status_labels(screens):
    _, staging = screens
    assert _label(staging, _iid_of(staging, "332650", 66)) == "要確認（同一計画への重複候補）（相手：8/6 の 34）"
    assert _label(staging, _iid_of(staging, "332650", 34)) == "要確認（同一計画への重複候補）（相手：7/15 の 66）"
    assert _label(staging, _iid_of(staging, "326611", 1186)) == "要確認（似たロットNo）"
    assert _label(staging, _iid_of(staging, "500000-1", 10)) == "候補なし"


# ---------------------------------------------------------------- 重複の確認（D-176）と状態の即時更新（D-178）

def _register_first(parent, staging):
    """66 の行をダブルクリック → 生産実績入力の登録（重複の相手はまだ未登録なので、通常の登録）。"""
    _double_click(staging, _iid_of(staging, "332650", 66))
    parent._start_registration()


def test_first_registration_updates_partner_status_immediately(screens):
    parent, staging = screens
    partner = _iid_of(staging, "332650", 34)
    _register_first(parent, staging)
    assert _history("KD", "332650") == [("2026-07-15", 66.0)]
    assert _label(staging, partner) == "要確認（既に実績あり）（登録済み：7/15 の 66）"


@pytest.mark.parametrize("path", ["right", "double"])
def test_sum_registration(screens, events, monkeypatch, path):
    parent, staging = screens
    _register_first(parent, staging)
    asked = []
    monkeypatch.setattr(psw, "ask_duplicate_resolution", lambda p, info: (asked.append(info), DUPLICATE_SUM)[1])
    iid = _iid_of(staging, "332650", 34)
    (_right if path == "right" else _double_click)(staging, iid)
    info, = asked
    assert build_duplicate_lines(info) == [
        "この計画には、すでに実績が登録されています。どちらが正しいかを確かめてください。",
        "",
        "・計画：KD（ロットNo. 332650）",
        "・今の実績：7/15　数量 66",
        "・登録しようとしている行：8/6　数量 34",
        "",
        "「合計して登録」：合計 100（日付 8/6）を、この計画の1件の実績にします。",
        "「手入力で対応」：登録せずに、この計画を生産実績入力で開きます（登録待ちの行は残します）。",
    ]
    assert _history("KD", "332650") == [("2026-08-06", 100.0)]
    assert iid not in staging._row_by_iid
    assert ("332650", 34.0) not in _pending()
    assert _messages(events, "showinfo")[-1][0] == "登録完了"


def test_manual_keeps_row_and_opens_plan(screens, monkeypatch):
    parent, staging = screens
    _register_first(parent, staging)
    monkeypatch.setattr(psw, "ask_duplicate_resolution", lambda p, info: DUPLICATE_MANUAL)
    iid = _iid_of(staging, "332650", 34)
    _right(staging, iid)
    assert _history("KD", "332650") == [("2026-07-15", 66.0)]
    assert iid in staging._row_by_iid and ("332650", 34.0) in _pending()
    assert parent.current_plan["kitting_list_no"] == "KD"
    assert parent._pending_csv_row_removal is None       # 生産実績入力で登録しても、この行は消さない


def test_cancel_changes_nothing(screens, monkeypatch):
    parent, staging = screens
    _register_first(parent, staging)
    monkeypatch.setattr(psw, "ask_duplicate_resolution", lambda p, info: None)
    _right(staging, _iid_of(staging, "332650", 34))
    assert _history("KD", "332650") == [("2026-07-15", 66.0)]
    assert ("332650", 34.0) in _pending()


def test_reimport_after_sum_does_not_restore_rows(screens, monkeypatch, tmp_path):
    parent, staging = screens
    _register_first(parent, staging)
    monkeypatch.setattr(psw, "ask_duplicate_resolution", lambda p, info: DUPLICATE_SUM)
    _right(staging, _iid_of(staging, "332650", 34))
    csv_path = tmp_path / "actual.csv"
    csv_path.write_text("ロットNo,製品名,実績数,払い出し日\n332650,ProdD,66,2026/7/15\n332650,ProdD,34,2026/8/6\n",
                        encoding="utf-8-sig")
    result = parse_production_csv_for_staging(str(csv_path), "W1")
    assert result["already_registered_count"] == 2 and result["imported_count"] == 0
    assert [lot for lot, _q in _pending()].count("332650") == 0


def test_reimport_without_sum_keeps_second_row(screens, tmp_path):
    """合計していなければ（66 だけ登録）、34 の行は再取込で登録待ちに残る（合計の規則には当たらない）。"""
    parent, staging = screens
    _register_first(parent, staging)
    csv_path = tmp_path / "actual.csv"
    csv_path.write_text("ロットNo,製品名,実績数,払い出し日\n332650,ProdD,66,2026/7/15\n332650,ProdD,34,2026/8/6\n",
                        encoding="utf-8-sig")
    result = parse_production_csv_for_staging(str(csv_path), "W1")
    assert result["already_registered_count"] == 1 and result["imported_count"] == 1


# ---------------------------------------------------------------- 似たロットNoの候補（D-177）

def test_similar_lot_candidate_is_marked(screens):
    _, staging = screens
    row = staging._row_by_iid[_iid_of(staging, "326611", 1186)]
    assert row["status"] == "similar_lot" and row["similar_lot_no"] == "326611-"
    assert [(c["kitting_list_no"], c["lot_no"], c[psw.SIMILAR_LOT_MARK]) for c in row["matched"]] == [("KS", "326611-", "326611")]


@pytest.mark.parametrize("answer", [True, False])
def test_similar_lot_right_click_asks_and_uses_csv_date(screens, events, answers, answer):
    _, staging = screens
    answers.append(answer)
    iid = _iid_of(staging, "326611", 1186)
    _right(staging, iid)
    ask = [m for m in _messages(events, "askyesno") if m[0] == "ロットNoが違う計画への登録"]
    assert ask == [("ロットNoが違う計画への登録", "ロットNoが違います（CSV：326611、計画：326611-）。この計画に登録しますか？")]
    assert _history("KS", "326611-") == ([("2026-07-14", 1186.0)] if answer else [])
    assert (iid in staging._row_by_iid) is (not answer)


def test_similar_lot_double_click_transfers_with_csv_date(screens, events):
    parent, staging = screens
    _double_click(staging, _iid_of(staging, "326611", 1186))
    assert (parent.current_plan["kitting_list_no"], parent.current_plan["lot_no"]) == ("KS", "326611-")
    parent._start_registration()
    assert _history("KS", "326611-") == [("2026-07-14", 1186.0)]
    assert ("326611", 1186.0) not in _pending()


def test_reimport_after_similar_registration(screens, events, tmp_path):
    _, staging = screens
    _right(staging, _iid_of(staging, "326611", 1186))
    csv_path = tmp_path / "actual.csv"
    csv_path.write_text("ロットNo,製品名,実績数,払い出し日\n326611,ProdS,1186,2026/7/14\n", encoding="utf-8-sig")
    assert parse_production_csv_for_staging(str(csv_path), "W1")["already_registered_count"] == 1


def test_shift_s_does_not_register_similar_or_duplicate(screens, events):
    _, staging = screens
    staging.tree.selection_set(list(staging._row_by_iid))
    staging._on_bulk_register()
    assert _history("KD", "332650") == [] and _history("KS", "326611-") == []
    title, message = _messages(events, "showinfo")[-1]
    assert "・似たロットNo：1件" in message and "・同一計画への重複候補：2件" in message and "・候補なし：1件" in message
