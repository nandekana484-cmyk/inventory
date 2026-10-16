"""
ロット統合 第3段階（D-160〜D-163）: 実績CSVとの照合。合成データ（scripts/make_lot_merge_sample_db.py）で確かめる。
- 統合した組では、CSV の行のロットNoが X でも X- でも、両方の計画を候補にする（計画の lot_no は元のまま）
- 計画・候補のキーは (計画No, 元のロットNo)。計画Noを共有する 226793・226793- で別のロットを見ない・選ばない
- 「候補なし」の行に「似たロットNoあり」を示す（末尾「-」だけ）
- 面2を登録したときの面1への自動入力（D-113）と、その確認（D-126）は統合をまたがない
"""
import os

import pytest

import config
from models.kitting_plan import find_plan_item_by_kitting_no
from models.production import list_daily_production_by_kitting_no
from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row
from services import lot_merge_service as svc
from services.production_import_service import group_active_plan_items_by_lot, is_already_registered
from services.production_service import preview_opposite_side_overwrite, register_opposite_side_daily_result
from services.staging_eligibility import (
    REASON_MULTIPLE_CANDIDATES, REASON_NO_CANDIDATES_CODE, _count_plan_keys, _load_staged_rows_from_db,
)
from tests.test_lot_merge import sample_db  # noqa: F401 （fixture）


def _add_pending(rows):
    batch = create_csv_import_batch("matching.csv", imported_by="W1")
    for i, (lot_no, product_name, qty, report_date) in enumerate(rows, start=10):
        upsert_pending_csv_import_row({"csv_row_no": i, "lot_no": lot_no, "product_name": product_name,
                                       "daily_qty": qty, "report_date": report_date, "worker_id": "W1"},
                                      import_batch_id=batch)


def _staged(lot_no, qty):
    return next(r for r in _load_staged_rows_from_db() if r["lot_no"] == lot_no and r["daily_qty"] == qty)


def _plan_keys(items):
    return sorted((c["kitting_list_no"], c["lot_no"]) for c in items)


# ---------------------------------------------------------------- 候補の集め方（D-160）

@pytest.mark.parametrize("csv_lot", ["163326", "163326-"])
def test_both_lots_are_candidates_whichever_lot_the_csv_row_has(sample_db, csv_lot):
    _add_pending([(csv_lot, "BRD-1633", 7, "2026/07/16")])
    before = _plan_keys(_staged(csv_lot, 7)["matched"])
    assert {lot for _k, lot in before} == {csv_lot}
    svc.merge_lots("163326-", "作業者A")
    after = _plan_keys(_staged(csv_lot, 7)["matched"])
    assert {lot for _k, lot in after} == {"163326", "163326-"}
    assert set(before) <= set(after)
    # 計画の行の lot_no は元のまま（統合先のロットNoに書き換えない）
    assert all(c["lot_no"] in ("163326", "163326-") for c in group_active_plan_items_by_lot()["163326-"])


def test_unmerged_grouping_is_unchanged(sample_db):
    before = group_active_plan_items_by_lot()
    svc.merge_lots("163326-", "作業者A")
    after = group_active_plan_items_by_lot()
    for lot in set(before) - {"163326", "163326-"}:
        assert _plan_keys(after[lot]) == _plan_keys(before[lot])


# ---------------------------------------------------------------- キー (計画No, 元のロットNo)（D-161）

def test_shared_kitting_list_no_is_not_mixed_up(sample_db):
    """226793 と 226793- は同じ計画No（K226793）。統合すると2つの候補になり、自動登録の対象にしない（別のロットへ登録しない）。"""
    _add_pending([("226793-", "BRD-2267", 20, "2026/07/21")])
    assert _staged("226793-", 20)["status"] != REASON_MULTIPLE_CANDIDATES     # 統合前は 226793- の計画だけ
    svc.merge_lots("226793-", "作業者A")
    row = _staged("226793-", 20)
    assert _plan_keys(row["matched"]) == [("K226793", "226793"), ("K226793", "226793-")]
    assert row["status"] == REASON_MULTIPLE_CANDIDATES


def test_already_registered_uses_plan_lot(sample_db):
    """登録済みの判定は、候補の計画の元のロットNoで実績を見る（226793 の 120 を 226793- の実績として見ない・見落とさない）。"""
    svc.merge_lots("226793-", "作業者A")
    result = is_already_registered("226793-", "BRD-2267", 120, report_date="2026/07/11")
    assert (result["already_registered"], result["matched_kitting_list_no"]) == (True, "K226793")
    assert is_already_registered("226793-", "BRD-2267", 20, report_date="2026/07/21")["already_registered"] is False


def test_duplicate_count_uses_plan_lot():
    plan = {"kitting_list_no": "K1", "lot_no": "X-"}
    items = [{"lot_no": "X", "matched": [plan]}, {"lot_no": "X-", "matched": [plan]}]
    assert _count_plan_keys(items) == {("K1", "X-"): 2}       # CSV のロットNoが違っても、同じ計画への重複として数える
    assert _count_plan_keys([{"lot_no": "Y", "matched": [{"kitting_list_no": "K2", "lot_no": "Y"}]}]) == {("K2", "Y"): 1}


def test_shift_s_q_target_and_exclusion(sample_db):
    """統合で候補が増えると、自動登録の対象から外れる（設計どおり）。統合していなければ従来どおり。"""
    _add_pending([("137599-", "BRD-1375", 50, "2026/07/20")])
    assert _staged("137599-", 50)["status"] == "eligible_shift_s"     # 計画数 50、実装予定 7/20
    svc.merge_lots("137599-", "作業者A")
    assert _staged("137599-", 50)["status"] == REASON_MULTIPLE_CANDIDATES
    svc.unmerge_lot("137599-", "作業者A")
    assert _staged("137599-", 50)["status"] == "eligible_shift_s"


# ---------------------------------------------------------------- 似たロットNoあり（D-162）

def test_similar_lot_no_on_rows_without_candidates(sample_db):
    _add_pending([("450005-", "BRD-4500", 1, "2026/07/16"), ("430003-", "BRD-4300", 1, "2026/07/16"),
                  ("410001-3", "BRD-4100", 1, "2026/07/16"), ("999999", "X", 1, "2026/07/16")])
    rows = {r["lot_no"]: r for r in _load_staged_rows_from_db()}
    assert rows["450005-"]["status"] == REASON_NO_CANDIDATES_CODE and rows["450005-"]["similar_lot_no"] == "450005"
    assert rows["430003-"]["similar_lot_no"] == "430003"
    assert "similar_lot_no" not in rows["410001-3"]                    # 「-1」「-2」などは対象外
    assert "similar_lot_no" not in rows["999999"]
    assert "similar_lot_no" not in rows["163326-"]                     # 候補がある行には付けない
    from ui.production.production_import_staging_window import staging_status_label
    assert staging_status_label(rows["450005-"]) == "候補なし（似たロットNoあり：450005）"
    assert staging_status_label(rows["999999"]) == "候補なし"


def test_find_similar_lot_no_rules():
    lots = {"326611-", "500500", "410001-1", "420002K"}
    assert svc.find_similar_lot_no("326611", lots) == "326611-"
    assert svc.find_similar_lot_no("500500-", lots) == "500500"
    assert svc.find_similar_lot_no("410001", lots) is None
    assert svc.find_similar_lot_no("420002", lots) is None
    assert svc.find_similar_lot_no("123", lots) is None


# ---------------------------------------------------------------- 統合をまたがない処理（D-163）

def test_side1_auto_fill_does_not_cross_merge(sample_db):
    """163326-（面2の計画だけ）に登録しても、統合先 163326 の面1へは自動入力しない。置き換えの確認も出ない（D-113・D-126）。"""
    svc.merge_lots("163326-", "作業者A")
    plan = find_plan_item_by_kitting_no("K163326M-2", "163326-")
    assert preview_opposite_side_overwrite(plan, 50, "2026-07-20") is None
    assert register_opposite_side_daily_result(plan, 50, "W1", report_date="2026-07-20") is False
    assert [r["daily_qty"] for r in list_daily_production_by_kitting_no("K163326-1", "163326")] == [99]
    # 統合先の面2を登録すると、統合先の面1へは従来どおり自動入力する
    target_plan = find_plan_item_by_kitting_no("K163326-2", "163326")
    assert register_opposite_side_daily_result(target_plan, 120, "W1", report_date="2026-07-20") is True
    assert [r["daily_qty"] for r in list_daily_production_by_kitting_no("K163326-1", "163326")] == [120]


def test_registration_candidates_keep_side1_hiding_per_lot(sample_db):
    """登録の候補の面1を隠す処理（list_active_plan_items）は元のロットのまま。163326 の面1は 163326 の面2があるので隠れる。"""
    before = _plan_keys(group_active_plan_items_by_lot()["163326"])
    svc.merge_lots("163326-", "作業者A")
    after = _plan_keys(group_active_plan_items_by_lot()["163326"])
    assert ("K163326-1", "163326") not in before and ("K163326-1", "163326") not in after
    assert after == sorted(before + [("K163326M-2", "163326-")])


# ---------------------------------------------------------------- 既定DB（未選択）

def test_default_db_creates_no_file(monkeypatch):
    default = os.path.join(config.APP_DATA_DIR, "db", "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", default)
    assert svc.active_plan_lot_nos() == set()
    assert svc.find_similar_lot_no("326611") is None
    assert svc.merge_plan_items_by_lot({"1": []}) == {"1": []}
    assert not os.path.exists(os.path.dirname(default))
