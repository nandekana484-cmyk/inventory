"""
ロット統合 第1段階（D-145）: 表とマイグレーション 014、統合の情報を読む関数、統合・解除、前月からの引き継ぎ。
データは scripts/make_lot_merge_sample_db.py の合成データ（実データは含まない）。
第1段階では集計に反映しないので、統合の前後で、ロット進捗・引落・実績レポート・登録待ちの結果が変わらないことも確かめる。
"""
import importlib.util
import os
import sqlite3

import pytest

import config
from db import schema_version as sv
from db.init_db import init_database_at
from db.migrations import LATEST_VERSION
from db.migrations import m014_add_lot_merges as m014
from models import lot_merges as lm
from models.operation_log import list_operation_log
from services import lot_merge_service as svc

_SPEC = importlib.util.spec_from_file_location(
    "make_lot_merge_sample_db",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "make_lot_merge_sample_db.py"))
sample = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sample)


@pytest.fixture
def sample_db():
    """合成データの月次DB と master.db を一時フォルダに作り、config をそこに向ける（conftest が終わったら戻す）。"""
    db_path, _ = sample.build(config.APP_DATA_DIR)
    return db_path


def _schema(path):
    with sqlite3.connect(path) as con:
        return sorted(con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall())


def _counts(path):
    with sqlite3.connect(path) as con:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {n: con.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0] for n in names}


def _version(path):
    with sqlite3.connect(path) as con:
        return con.execute("PRAGMA user_version").fetchone()[0]


def _as_v130(path):
    """v1.3.0 で作った DB と同じ形にする（lot_merges が無く、版 13）。v1.3.0 のコードで作った DB での確認は §52 の検証で行った。"""
    with sqlite3.connect(path) as con:
        con.execute("DROP TABLE lot_merges")
        con.execute("PRAGMA user_version = 13")


# ---------------------------------------------------------------- 表とマイグレーション 014

def test_new_db_has_lot_merges_and_latest_version(tmp_path):
    path = str(tmp_path / "new" / "inventory.db")
    init_database_at(path)
    assert _version(path) == LATEST_VERSION == 14
    with sqlite3.connect(path) as con:
        assert [r[1] for r in con.execute("PRAGMA table_info(lot_merges)")] == [
            "member_lot", "target_lot", "merged_at", "merged_by", "note"]


def test_migration_014_matches_schema_sql_and_keeps_existing_tables(sample_db, tmp_path):
    fresh = str(tmp_path / "fresh" / "inventory.db")
    init_database_at(fresh)
    _as_v130(sample_db)
    before_schema, before_counts = _schema(sample_db), _counts(sample_db)

    result = sv.ensure_schema(sample_db)
    assert (result["from_version"], result["to_version"], result["applied"]) == (13, 14, [14])
    assert _version(sample_db) == 14
    after_schema, after_counts = _schema(sample_db), _counts(sample_db)
    added = [s for s in after_schema if s not in before_schema]
    assert [s[1] for s in added] == ["idx_lot_merges_target_lot", "lot_merges"]
    assert all(s in after_schema for s in before_schema)                 # 既存の表・インデックスの定義は変わらない
    assert {k: v for k, v in after_counts.items() if k != "lot_merges"} == before_counts
    assert after_counts["lot_merges"] == 0
    # 新しく作った DB（schema.sql）の lot_merges と、014 を当てた DB の lot_merges は同じ定義
    fresh_defs = [s for s in _schema(fresh) if s[2] == "lot_merges"]
    assert fresh_defs == added


def test_migration_014_twice_is_safe(sample_db):
    with sqlite3.connect(sample_db) as con:
        m014.upgrade(con)
        m014.upgrade(con)


def test_table_rejects_invalid_pairs(sample_db):
    with sqlite3.connect(sample_db) as con:
        for member, target in (("410001-1", "410001"), ("420002K", "420002"), ("1-", "2"), ("-", ""), ("12--", "12-")):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute("INSERT INTO lot_merges (member_lot, target_lot, merged_at) VALUES (?, ?, 'x')", (member, target))


def test_is_valid_merge_pair():
    assert lm.is_valid_merge_pair("123456-", "123456")
    for member, target in (("123456-1", "123456"), ("123456K", "123456"), ("123456.1", "123456"), ("123456--", "123456-"),
                           ("-", ""), ("123456-", " 123456"), ("123457-", "123456"), (None, "1")):
        assert not lm.is_valid_merge_pair(member, target), (member, target)
    assert svc.target_lot_for_member("123456-") == "123456"
    for lot in ("123456", "123456-1", "123456K", "123456.1", "-", "123456--"):
        assert svc.target_lot_for_member(lot) is None, lot


def test_insert_checks_pair_before_writing(sample_db):
    with pytest.raises(ValueError):
        lm.insert_lot_merge("410001-1", "410001", "作業者A")
    assert lm.list_lot_merges() == []


# ---------------------------------------------------------------- 候補・統合・解除

def test_candidates(sample_db):
    candidates = svc.list_merge_candidates()
    assert {c["member_lot"]: c["target_lot"] for c in candidates} == sample.EXPECTED_CANDIDATES
    listed = {lot for c in candidates for lot in (c["member_lot"], c["target_lot"])}
    assert not listed & set(sample.EXPECTED_NOT_CANDIDATES)
    assert all(not c["merged"] for c in candidates)
    pair = next(c for c in candidates if c["member_lot"] == "163326-")
    member = pair["lots"]["163326-"]
    assert [(f["setup_file_no"], f["production_side"], f["order_qty"], f["file_actual"])
            for b in member["boards"] for f in b["files"]] == [("F1633", "2", 170, 1)]
    assert pair["lots"]["163326"]["order_quantity"] == 1000
    # 表記ゆれの基板名は、統合しない今の計算のまま（マスタ未登録）
    assert next(c for c in candidates if c["member_lot"] == "348588-")["lots"]["348588-"]["status"] == "unregistered"


def test_merge_and_unmerge(sample_db):
    row = svc.merge_lots("163326-", "作業者A")
    assert (row["member_lot"], row["target_lot"], row["merged_by"]) == ("163326-", "163326", "作業者A")
    merge_map = svc.load_lot_merge_map()
    assert merge_map.target_of("163326-") == "163326"
    assert merge_map.target_of("163326") == "163326"
    assert merge_map.target_of("410001-1") == "410001-1"
    assert merge_map.members_of("163326") == ["163326-"]
    assert next(c for c in svc.list_merge_candidates() if c["member_lot"] == "163326-")["merged"] is True
    assert (list_operation_log()[0]["operation_name"], list_operation_log()[0]["detail"]) == ("ロット統合", "163326- → 163326")

    with pytest.raises(svc.LotMergeError, match="すでに統合済み"):
        svc.merge_lots("163326-", "作業者A")

    removed = svc.unmerge_lot("163326-", "作業者A")
    assert removed["member_lot"] == "163326-"
    assert svc.load_lot_merge_map().rows == []
    assert (list_operation_log()[0]["operation_name"], list_operation_log()[0]["detail"]) == (
        "ロット統合解除", "163326- → 163326 を解除")
    with pytest.raises(svc.LotMergeError, match="統合されていません"):
        svc.unmerge_lot("163326-", "作業者A")


@pytest.mark.parametrize("lot, message", [
    ("410001-1", "統合できるロットNo.ではありません"),
    ("420002K", "統合できるロットNo.ではありません"),
    ("430003.1", "統合できるロットNo.ではありません"),
    ("440004-", "統合先の 440004 に有効な計画がありません"),
    ("450005-", "450005- に有効な計画がありません"),
])
def test_merge_refuses_invalid(sample_db, lot, message):
    with pytest.raises(svc.LotMergeError, match=message):
        svc.merge_lots(lot, "作業者A")
    assert lm.list_lot_merges() == []
    assert [r for r in list_operation_log() if r["operation_name"] == "ロット統合"] == []


def test_merge_and_log_are_one_transaction(sample_db, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("操作履歴の書き込みに失敗")
    monkeypatch.setattr(lm, "insert_operation_log", broken)
    with pytest.raises(RuntimeError):
        svc.merge_lots("137599-", "作業者A")
    assert lm.list_lot_merges() == []


def test_merge_map_reads_table_once(sample_db, monkeypatch):
    svc.merge_lots("163326-", "作業者A")
    calls = []
    real = svc.list_lot_merges
    monkeypatch.setattr(svc, "list_lot_merges", lambda: calls.append(1) or real())
    merge_map = svc.load_lot_merge_map()
    for lot in ("163326-", "163326", "137599-", "x"):
        merge_map.target_of(lot)
        merge_map.members_of(lot)
    assert len(calls) == 1


def _aggregates():
    """第1段階で変わってはいけない画面の計算結果（ロット進捗・日々の引落・実績レポート・登録待ち）。"""
    from services.lot_status_history import get_daily_drawdown
    from services.production_service import build_monthly_report, check_lot_progress
    from services.staging_eligibility import _load_staged_rows_from_db
    drawdown = {d: get_daily_drawdown(d) for d in ("2026-07-10", "2026-07-15", "2026-07-22", "2026-07-31")}
    staged = [{k: v for k, v in r.items() if k != "candidates"} for r in _load_staged_rows_from_db()]
    return check_lot_progress(), drawdown, build_monthly_report("2026-07-01", "2026-07-31"), staged


def test_merge_does_not_change_aggregates_yet(sample_db):
    before = _aggregates()
    for member in sample.EXPECTED_CANDIDATES:
        svc.merge_lots(member, "作業者A")
    assert len(lm.list_lot_merges()) == 4
    assert _aggregates() == before


# ---------------------------------------------------------------- 既定DB（未選択）

def test_default_db_is_not_touched(monkeypatch):
    default = os.path.join(config.APP_DATA_DIR, "db", "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", default)
    assert config.is_default_db()
    assert svc.load_lot_merge_map().rows == []
    assert svc.list_merge_candidates() == []
    with pytest.raises(svc.LotMergeError):
        svc.merge_lots("163326-", "作業者A")
    with pytest.raises(svc.LotMergeError):
        svc.unmerge_lot("163326-", "作業者A")
    assert not os.path.exists(os.path.dirname(default))


# ---------------------------------------------------------------- 前月からの引き継ぎ

def _new_month_db():
    path = os.path.join(config.APP_DATA_DIR, "db", "NEXT", "inventory.db")
    init_database_at(path)
    return path


def test_carry_over_copies_merge_when_both_lots_are_carried(sample_db):
    from services.db_migration_carryover import carry_over_incomplete_lots
    svc.merge_lots("163326-", "作業者A")          # 163326（99/1000）と 163326-（1/170）はどちらも未完了
    new_db = _new_month_db()
    summary = carry_over_incomplete_lots(sample_db, new_db, imported_by="W1", reference_date=__import__("datetime").date(2026, 7, 31))
    assert {"163326", "163326-"} <= set(summary["lot_nos"])
    assert summary["lot_merges_copied"] == 1 and summary["lot_merges_not_copied"] == []
    assert config.DB_PATH == new_db
    copied = lm.list_lot_merges()
    assert [(r["member_lot"], r["target_lot"], r["merged_by"]) for r in copied] == [("163326-", "163326", "作業者A")]


def test_carry_over_skips_merge_when_only_one_lot_is_carried(sample_db):
    from services.db_migration_carryover import carry_over_incomplete_lots
    svc.merge_lots("137599-", "作業者A")
    # 137599 を完成させる（500/500）。137599- は未完了のまま
    from models.kitting_plan import find_plan_item_by_kitting_no
    from models.production import replace_daily_result
    p = find_plan_item_by_kitting_no("K137599-1", "137599")
    replace_daily_result(p["plan_item_id"], "K137599-1", "137599", "BRD-1375", "2026-07-08", 500, "W1")
    new_db = _new_month_db()
    summary = carry_over_incomplete_lots(sample_db, new_db, imported_by="W1", reference_date=__import__("datetime").date(2026, 7, 31))
    assert "137599" not in summary["lot_nos"] and "137599-" in summary["lot_nos"]
    assert summary["lot_merges_copied"] == 0
    assert summary["lot_merges_not_copied"] == [{"member_lot": "137599-", "target_lot": "137599", "carried_lot": "137599-"}]
    assert lm.list_lot_merges() == []
