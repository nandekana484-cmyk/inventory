"""
services/staging_eligibility.py の単体テスト。期待値は、移す前のコードが返していた値をそのまま固定している。
DB は conftest.py が一時フォルダへ差し替えた config.DB_PATH・MASTER_DB_PATH を使う（実 DB は開かない）。
"""
import pytest

import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version
from models.production import insert_daily_production
from models.production_side_master import upsert_production_side
from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row
from services import staging_eligibility as se
from services.staging_eligibility import (
    AUTO_REGISTER_ELIGIBLE,
    AUTO_REGISTER_INELIGIBLE,
    AUTO_REGISTER_MAX_DAYS_SHIFT_S,
    AUTO_REGISTER_MAX_DAYS_SHIFT_Q,
    REASON_NO_CANDIDATES_CODE,
    REASON_PRODUCT_NAME_MISMATCH,
    REASON_MULTIPLE_CANDIDATES,
    REASON_EXISTING,
    REASON_DUPLICATE,
    REASON_SIDE2_WAIT,
    REASON_SIDE_MASTER_UNREGISTERED,
    REASON_QTY_MISMATCH,
    REASON_DATE_UNPARSEABLE,
    REASON_DATE_DIFF_TOO_LARGE,
    STATUS_ELIGIBLE_SHIFT_S,
    STATUS_ELIGIBLE_SHIFT_Q,
    _date_only_diff_days,
    _is_qty_mismatch,
    is_auto_confirmable,
    evaluate_auto_register_eligibility,
    _load_staged_rows_from_db,
)


@pytest.fixture
def monthly_db():
    """一時フォルダに月次DBを作る（実績・計画・保留行のテーブル）。"""
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    create_csv_import_batch("test.csv", imported_by="test")


def _candidate(**overrides):
    candidate = {
        "kitting_list_no": "K1",
        "lot_no": "L1",
        "setup_file_no": "F1",
        "production_side": "2",  # 面2は生産面マスターの分類の対象外（classify_side1_only_plan() が None）
        "planned_qty": 100,
        "plan_start_datetime": "2026/07/22 23:50:00",
    }
    candidate.update(overrides)
    return candidate


def _row(**overrides):
    row = {"lot_no": "L1", "product_name": "ProdA", "daily_qty": 100, "report_date": "2026/7/23"}
    row.update(overrides)
    return row


# ---------------------------------------------------------------- 定数

def test_constants():
    assert AUTO_REGISTER_MAX_DAYS_SHIFT_S == 1
    assert AUTO_REGISTER_MAX_DAYS_SHIFT_Q == 4
    assert (AUTO_REGISTER_ELIGIBLE, AUTO_REGISTER_INELIGIBLE) == ("eligible", "ineligible")
    assert (STATUS_ELIGIBLE_SHIFT_S, STATUS_ELIGIBLE_SHIFT_Q) == ("eligible_shift_s", "eligible_shift_q")
    codes = [
        REASON_NO_CANDIDATES_CODE, REASON_PRODUCT_NAME_MISMATCH, REASON_MULTIPLE_CANDIDATES, REASON_EXISTING,
        REASON_DUPLICATE, REASON_SIDE2_WAIT, REASON_SIDE_MASTER_UNREGISTERED, REASON_QTY_MISMATCH,
        REASON_DATE_UNPARSEABLE, REASON_DATE_DIFF_TOO_LARGE,
    ]
    assert codes == [
        "no_candidates", "product_name_mismatch", "multiple_candidates", "existing", "duplicate",
        "side2_wait", "side_master_unregistered", "qty_mismatch", "date_unparseable", "date_diff_too_large",
    ]
    # 一括登録の完了メッセージのラベルは、理由コードを全部カバーしている
    assert set(se.AUTO_REGISTER_SKIP_REASON_LABELS) == set(codes)


# ---------------------------------------------------------------- _date_only_diff_days

@pytest.mark.parametrize("report_date, plan_start, expected", [
    ("2026/7/23", "2026/07/21 23:50:00", 2),   # 時刻を無視して暦日で数える（経過時間なら1日になる例）
    ("2026-07-23", "2026/07/23 08:00:00", 0),
    ("2026/7/21", "2026/07/23 00:00:00", 2),   # 絶対値
    ("2026/7/23", "2026/07/18 10:00:00", 5),
])
def test_date_only_diff_days(report_date, plan_start, expected):
    assert _date_only_diff_days(report_date, plan_start) == expected


@pytest.mark.parametrize("report_date, plan_start", [
    (None, "2026/07/21 23:50:00"),
    ("abc", "2026/07/21 23:50:00"),
    ("2026/7/23", None),
    ("2026/7/23", "2026-07-21 23:50:00"),
])
def test_date_only_diff_days_none_when_unparseable(report_date, plan_start):
    assert _date_only_diff_days(report_date, plan_start) is None


# ---------------------------------------------------------------- _is_qty_mismatch

@pytest.mark.parametrize("planned, daily, expected", [
    (100, 100, False),
    ("100", 100.0, False),
    (100, 99, True),
    (None, 100, True),      # 比較できなければ不一致（安全側）
    ("abc", 100, True),
    (100, None, True),
])
def test_is_qty_mismatch(planned, daily, expected):
    assert _is_qty_mismatch(planned, daily) is expected


# ---------------------------------------------------------------- is_auto_confirmable

@pytest.mark.parametrize("planned, daily, plan_start, report_date, kwargs, expected", [
    (100, 100, "2026/07/22 23:50:00", "2026/7/23", {}, True),                     # 差1日・既定は Shift+S（1日）
    (100, 100, "2026/07/21 23:50:00", "2026/7/23", {}, False),                    # 差2日
    (100, 100, "2026/07/21 23:50:00", "2026/7/23", {"max_date_diff_days": 4}, True),
    (100, 100, "2026/07/18 23:50:00", "2026/7/23", {"max_date_diff_days": 4}, False),  # 差5日
    (100, 99, "2026/07/23 00:00:00", "2026/7/23", {}, False),                     # 数量の不一致
    ("x", 100, "2026/07/23 00:00:00", "2026/7/23", {}, False),                    # 数量を比べられない
    (100, 100, "bad", "2026/7/23", {}, False),                                    # 日付を解釈できない
])
def test_is_auto_confirmable(planned, daily, plan_start, report_date, kwargs, expected):
    assert is_auto_confirmable("L1", "ProdA", planned, daily, plan_start, report_date, **kwargs) is expected


# ---------------------------------------------------------------- evaluate_auto_register_eligibility（理由コードを全種類）

def _evaluate(row=None, candidates=None, matched=None, plan_key_counts=None, max_days=AUTO_REGISTER_MAX_DAYS_SHIFT_S):
    row = row or _row()
    if candidates is None:
        candidates = [_candidate()]
    if matched is None:
        matched = list(candidates)
    return evaluate_auto_register_eligibility(row, candidates, matched, plan_key_counts or {}, max_days)


def test_reason_no_candidates(monthly_db):
    assert _evaluate(candidates=[], matched=[]) == (AUTO_REGISTER_INELIGIBLE, "no_candidates", None, None)


def test_reason_product_name_mismatch(monthly_db):
    assert _evaluate(matched=[]) == (AUTO_REGISTER_INELIGIBLE, "product_name_mismatch", None, None)


def test_reason_multiple_candidates(monthly_db):
    cands = [_candidate(), _candidate(kitting_list_no="K2")]
    assert _evaluate(candidates=cands) == (AUTO_REGISTER_INELIGIBLE, "multiple_candidates", None, None)


def test_same_kitting_no_twice_is_not_multiple(monthly_db):
    """製品名まで一致した候補が同じ kitting_list_no だけなら、「候補が複数」にはならない。"""
    c = _candidate()
    status, reason, candidate, date_diff = _evaluate(candidates=[c, dict(c)])
    assert (status, reason, date_diff) == (AUTO_REGISTER_ELIGIBLE, None, 1)


def test_reason_existing(monthly_db):
    insert_daily_production(None, "K1", "L1", None, "2026-07-20", 50, "W1")
    c = _candidate()
    assert _evaluate(candidates=[c]) == (AUTO_REGISTER_INELIGIBLE, "existing", c, None)


def test_existing_checks_lot_no(monthly_db):
    """別ロットの同じ kitting_list_no の実績は「既に実績あり」にしない（kitting_list_no は lot_no をまたいで重複する）。"""
    insert_daily_production(None, "K1", "OTHER_LOT", None, "2026-07-20", 50, "W1")
    assert _evaluate()[0] == AUTO_REGISTER_ELIGIBLE


def test_reason_duplicate(monthly_db):
    c = _candidate()
    assert _evaluate(candidates=[c], plan_key_counts={("K1", "L1"): 2}) == (
        AUTO_REGISTER_INELIGIBLE, "duplicate", c, None,
    )


def test_reason_side2_wait(monthly_db):
    upsert_production_side("F2", "A", "1")
    upsert_production_side("F2", "B", "2")  # 別ラインでも、ファイルNo単位で後行面ありになる
    c = _candidate(setup_file_no="F2", production_side="1")
    assert _evaluate(candidates=[c]) == (AUTO_REGISTER_INELIGIBLE, "side2_wait", c, None)


def test_reason_side_master_unregistered(monthly_db):
    c = _candidate(setup_file_no="F3", production_side="1")
    assert _evaluate(candidates=[c]) == (AUTO_REGISTER_INELIGIBLE, "side_master_unregistered", c, None)


def test_single_side_product_is_eligible(monthly_db):
    """面1だけで、生産面マスターに後行面なしの登録があれば（片面の製品）、登録できる。"""
    upsert_production_side("F4", "A", "1")
    c = _candidate(setup_file_no="F4", production_side="1")
    assert _evaluate(candidates=[c]) == (AUTO_REGISTER_ELIGIBLE, None, c, 1)


def test_reason_qty_mismatch(monthly_db):
    c = _candidate(planned_qty=90)
    assert _evaluate(candidates=[c]) == (AUTO_REGISTER_INELIGIBLE, "qty_mismatch", c, None)


def test_reason_date_unparseable(monthly_db):
    c = _candidate()
    assert _evaluate(row=_row(report_date="abc"), candidates=[c]) == (
        AUTO_REGISTER_INELIGIBLE, "date_unparseable", c, None,
    )


def test_reason_date_diff_too_large(monthly_db):
    c = _candidate(plan_start_datetime="2026/07/18 10:00:00")
    assert _evaluate(candidates=[c], max_days=AUTO_REGISTER_MAX_DAYS_SHIFT_Q) == (
        AUTO_REGISTER_INELIGIBLE, "date_diff_too_large", c, 5,
    )


def test_eligible_shift_s(monthly_db):
    c = _candidate()
    assert _evaluate(candidates=[c]) == (AUTO_REGISTER_ELIGIBLE, None, c, 1)


def test_eligible_only_with_shift_q(monthly_db):
    c = _candidate(plan_start_datetime="2026/07/20 09:00:00")
    assert _evaluate(candidates=[c], max_days=AUTO_REGISTER_MAX_DAYS_SHIFT_S) == (
        AUTO_REGISTER_INELIGIBLE, "date_diff_too_large", c, 3,
    )
    assert _evaluate(candidates=[c], max_days=AUTO_REGISTER_MAX_DAYS_SHIFT_Q) == (AUTO_REGISTER_ELIGIBLE, None, c, 3)


def test_priority_qty_before_date(monthly_db):
    """数量の不一致は、日付の判定より先に返る（優先順位 7 → 8）。"""
    c = _candidate(planned_qty=90)
    assert _evaluate(row=_row(report_date="abc"), candidates=[c])[1] == "qty_mismatch"


def test_priority_existing_before_duplicate(monthly_db):
    """既に実績ありは、同じ計画への重複より先に返る（優先順位 4 → 5）。"""
    insert_daily_production(None, "K1", "L1", None, "2026-07-20", 50, "W1")
    assert _evaluate(plan_key_counts={("K1", "L1"): 2})[1] == "existing"


# ---------------------------------------------------------------- _load_staged_rows_from_db

def _add_plan(batch_id, kitting_list_no, lot_no, board_name, planned_qty, plan_start, side="2", file_no="F1"):
    create_plan_version(batch_id, kitting_list_no, {
        "lot_no": lot_no, "setup_file_no": file_no, "board_name": board_name, "production_side": side,
        "planned_qty": planned_qty, "order_qty": planned_qty, "plan_start_datetime": plan_start,
    }, created_by="test")


def _add_pending(lot_no, product_name, daily_qty, report_date):
    upsert_pending_csv_import_row({
        "csv_row_no": 1, "lot_no": lot_no, "product_name": product_name,
        "daily_qty": daily_qty, "report_date": report_date, "worker_id": "W1",
    })


def test_load_staged_rows_empty(monthly_db):
    assert _load_staged_rows_from_db() == []


def test_load_staged_rows_statuses(monthly_db):
    batch_id = create_plan_batch("plan.csv", "test", 2)
    _add_plan(batch_id, "K1", "L1", "ProdA", 100, "2026/07/22 23:50:00")
    _add_plan(batch_id, "K2", "L2", "ProdB", 100, "2026/07/20 09:00:00")
    _add_pending("L1", "ＰＲＯＤａ", 100, "2026/7/23")   # 製品名は正規化して照合する → Shift+S で可
    _add_pending("L2", "ProdB", 100, "2026/7/23")        # 差3日 → Shift+Q でのみ可
    _add_pending("L9", "ProdZ", 100, "2026/7/23")        # 計画なし
    _add_pending("L1", "Other", 100, "2026/7/24")        # ロットは一致、製品名が不一致

    rows = _load_staged_rows_from_db()
    statuses = {(r["lot_no"], r["product_name"]): r["status"] for r in rows}
    assert statuses == {
        ("L1", "ＰＲＯＤａ"): "eligible_shift_s",
        ("L2", "ProdB"): "eligible_shift_q",
        ("L9", "ProdZ"): "no_candidates",
        ("L1", "Other"): "product_name_mismatch",
    }
    first = next(r for r in rows if r["lot_no"] == "L2")
    assert set(first) == {
        "pending_row_id", "row", "lot_no", "product_name", "daily_qty", "report_date",
        "worker_id", "candidates", "matched", "status",
    }
    assert [c["kitting_list_no"] for c in first["matched"]] == ["K2"]


def test_load_staged_rows_duplicate_counts_only_single_candidate_rows(monthly_db):
    """同じ計画を候補とする保留行が2件あれば、どちらも「同じ計画への重複」になる。"""
    batch_id = create_plan_batch("plan.csv", "test", 1)
    _add_plan(batch_id, "K1", "L1", "ProdA", 100, "2026/07/22 23:50:00")
    _add_pending("L1", "ProdA", 100, "2026/7/23")
    _add_pending("L1", "ProdA", 100, "2026/7/22")
    assert [r["status"] for r in _load_staged_rows_from_db()] == ["duplicate", "duplicate"]


# ---------------------------------------------------------------- 旧位置と依存

def test_old_location_reexports_same_objects():
    from ui import production_import_staging_window as old
    for name in ("_date_only_diff_days", "_is_qty_mismatch", "is_auto_confirmable",
                 "evaluate_auto_register_eligibility", "_load_staged_rows_from_db",
                 "AUTO_REGISTER_SKIP_REASON_LABELS", "AUTO_REGISTER_MAX_DAYS_SHIFT_S"):
        assert getattr(old, name) is getattr(se, name)


def test_new_module_does_not_import_gui():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(se))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert not names & {"tkinter", "tkcalendar", "ui"}
