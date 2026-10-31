"""
古い計画の判定のビュー v_plan_stale（マイグレーション 016、models/plan_stale_view.py、D-193）。
画面の「古い計画を隠す」はこのビューを読む。現場アプリは月次DB を mode=ro で読む。
"""
import os
import queue
import random
import sqlite3
import types

import pytest

import config
from db import schema_version as sv
from db.init_db import init_database_at
from db.migrations import LATEST_VERSION, load_migrations
from db.migrations import m016_add_plan_stale_view as m016
from models import plan_stale_view as psv
from models.kitting_plan import create_plan_batch, create_plan_version, init_kitting_plan_tables, mark_batch_deleted
from models.kitting_plan_batches import count_stale_plans, fetch_stale_plan_keys
from models.production import insert_daily_production
from services import production_entry_service as svc
from services.kitting_import_service import import_kitting_plan_csv

COLUMNS = ["kitting_list_no", "lot_no", "is_stale", "has_legacy_actuals", "replacement_kitting_list_no",
           "replacement_count", "latest_batch_id", "judge_reason", "judged_at"]


@pytest.fixture
def db(monkeypatch):
    path = os.path.join(config.APP_DATA_DIR, "db", "view_month", "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    init_database_at(path)
    init_kitting_plan_tables()
    return path


def set_imported_at(batch_id, imported_at):
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute("UPDATE kitting_plan_batches SET imported_at = ? WHERE plan_batch_id = ?", (imported_at, batch_id))


def add_batch(rows, source="plan.csv", imported_at=None):
    """rows: [(キッティングNo., ロットNo., 開始, 終了)] か、末尾に追加の列の dict を足したもの。ファイルNo.・面は 0001・1。"""
    batch = create_plan_batch(source, "T", len(rows))
    for row in rows:
        kitting, lot, start, end = row[:4]
        extra = row[4] if len(row) > 4 else {}
        create_plan_version(batch, kitting, {
            "lot_no": lot, "setup_file_no": "0001", "mounting_line": "L", "board_name": "B", "production_side": "1",
            "planned_qty": 10, "order_qty": 10, "plan_start_datetime": start, "plan_end_datetime": end, **extra,
        }, created_by="T")
    if imported_at:
        set_imported_at(batch, imported_at)
    return batch


def view(path=None):
    con = sqlite3.connect(path or config.DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        return {(r["kitting_list_no"], r["lot_no"]): dict(r) for r in con.execute("SELECT * FROM v_plan_stale")}
    finally:
        con.close()


def stale(rows=None):
    return {k for k, r in (rows or view()).items() if r["is_stale"]}


# ---------------------------------------------------------------- 定義・マイグレーション・版

def test_migration_sql_is_identical_to_the_model_constant():
    assert m016.CREATE_VIEW == psv.CREATE_VIEW_SQL
    assert m016.VERSION == 16 == LATEST_VERSION
    assert [m.VERSION for m in load_migrations()][-1] == 16


def test_new_db_has_the_view_with_the_columns(db):
    with sqlite3.connect(db) as con:
        assert [r[1] for r in con.execute("PRAGMA table_info(v_plan_stale)")] == COLUMNS
        assert con.execute("PRAGMA user_version").fetchone()[0] == 16


def _definitions(path):
    with sqlite3.connect(path) as con:
        return sorted(con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall())


def _as_v15(path):
    with sqlite3.connect(path) as con:
        con.execute("DROP VIEW v_plan_stale")
        con.execute("PRAGMA user_version = 15")


def test_migration_016_opens_a_v15_db_with_a_backup_and_gives_the_same_shape(db, tmp_path):
    fresh = _definitions(db)
    migrated = str(tmp_path / "db" / "migrated" / "inventory.db")
    init_database_at(migrated)
    old = config.DB_PATH
    config.DB_PATH = migrated
    init_kitting_plan_tables()
    config.DB_PATH = old
    _as_v15(migrated)
    result = sv.ensure_schema(migrated)
    assert (result["from_version"], result["to_version"], result["applied"]) == (15, 16, [16])
    assert result["backup_path"] and os.path.exists(result["backup_path"])           # マイグレーションの前にバックアップが取られる
    assert _definitions(migrated) == fresh                                            # 新しく作った DB と、ビューの定義まで一致
    again = sv.ensure_schema(migrated)
    assert again["applied"] == [] and _definitions(migrated) == fresh


def test_migration_016_does_nothing_without_plan_tables_and_init_creates_the_view_later(tmp_path):
    path = str(tmp_path / "db" / "noplans" / "inventory.db")
    init_database_at(path)                                                             # 計画の表が無い（版は最新の印だけ）
    with sqlite3.connect(path) as con:
        con.execute("PRAGMA user_version = 15")
    sv.ensure_schema(path)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name = 'v_plan_stale'").fetchone() is None
        assert con.execute("PRAGMA user_version").fetchone()[0] == 16
    config.DB_PATH = path
    init_kitting_plan_tables()
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE name = 'v_plan_stale'").fetchone() is not None


def test_a_v16_db_is_refused_by_an_app_whose_latest_version_is_15(db):
    with pytest.raises(sv.DatabaseTooNewError) as info:
        sv.ensure_schema(db, [m for m in load_migrations() if m.VERSION <= 15], 15)
    assert (info.value.db_version, info.value.app_version) == (16, 15)


def test_view_is_recreated_only_when_the_definition_changes(db, monkeypatch):
    def schema_version():
        with sqlite3.connect(db) as con:
            return con.execute("PRAGMA schema_version").fetchone()[0]

    before = schema_version()
    with sqlite3.connect(db) as con:
        assert psv.create_plan_stale_view(con) is False
    assert schema_version() == before
    monkeypatch.setattr(psv, "CREATE_VIEW_SQL", psv.CREATE_VIEW_SQL.replace("judged_at\nFROM judged j", "judged_at,\n       1 AS extra\nFROM judged j"))
    with sqlite3.connect(db) as con:
        assert psv.create_plan_stale_view(con) is True
        con.commit()
    assert schema_version() != before


def test_rebuilding_a_table_needs_the_view_dropped_first(db):
    """kitting_plan_items を作り直すマイグレーションは、先にビューを消し、最後に作り直す（docs/db.md）。"""
    def rebuild(con):
        con.execute("CREATE TABLE kitting_plan_items_new AS SELECT * FROM kitting_plan_items")
        con.execute("DROP TABLE kitting_plan_items")
        con.execute("ALTER TABLE kitting_plan_items_new RENAME TO kitting_plan_items")

    with sqlite3.connect(db, isolation_level=None) as con:
        con.execute("BEGIN")
        with pytest.raises(sqlite3.OperationalError):             # ビューを消さないと、RENAME が失敗する
            rebuild(con)
        con.execute("ROLLBACK")

        con.execute("BEGIN")
        psv.drop_plan_stale_view(con)
        rebuild(con)
        assert psv.create_plan_stale_view(con) is True
        con.execute("COMMIT")
        assert con.execute("SELECT COUNT(*) FROM v_plan_stale").fetchone()[0] == 0


# ---------------------------------------------------------------- 日時のそろえ（Python の normalize_plan_datetime() と同じ結果）

FIXED = ["2026/07/13 17:19:59", "2026-07-13 17:19:59", "2026/7/3 8:05", "2026-07-03T08:05:09", "2026/07/03", "  2026/07/03 08:05:09  ",
         None, "", "  ", "abc", "2026/13/01 00:00:00", "2026/02/30", "2026/02/29", "2024/02/29 23:59:59", "2026/07/03 24:00:00",
         "2026/07/03 08:60:00", "2026/07/03 08:05:60", "2026/07/03  08:05", "2026/07/03 8:5", "2026/07/03 08:05:09:11", "2026/7/3",
         "2026/07/03 08", "20260703", "2026/07/03/04", "2026/00/10", "2026/10/00", "26/07/03", "2026/07/03\t08:05", "2026/07/03 08:05\n",
         "2026-07-03 08:05", "2026/07/03T08:05", "2026/07/03 1:02:03", "2026/07/03 001:02", "2026/07/05T", "T2026/07/03 08:05:09"]


def _random_values(count=3000):
    rnd = random.Random(1)
    alphabet = "0123456789/-: T"
    values = []
    for _ in range(count):
        chars = list(rnd.choice(["2026/07/03 08:05:09", "2026-7-3 8:05", "2026/07/03", "2026/1/1T1:02:03"]))
        for _ in range(rnd.randint(0, 3)):
            i = rnd.randrange(len(chars) + 1)
            op = rnd.random()
            if op < .4 and chars:
                chars[min(i, len(chars) - 1)] = rnd.choice(alphabet)
            elif op < .7:
                chars.insert(i, rnd.choice(alphabet))
            elif chars:
                del chars[min(i, len(chars) - 1)]
        values.append("".join(chars))
    return values


@pytest.fixture
def bare_view():
    """ビューだけを確かめる最小の表（kitting_plan_items・kitting_plan_batches・production_daily）。"""
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE kitting_plan_items (plan_item_id INTEGER PRIMARY KEY, plan_batch_id INT, kitting_list_no TEXT, lot_no TEXT,
            setup_file_no TEXT, production_side TEXT, plan_start_datetime TEXT, plan_end_datetime TEXT,
            is_active INT DEFAULT 1, delete_flag INT DEFAULT 0);
        CREATE TABLE kitting_plan_batches (plan_batch_id INTEGER PRIMARY KEY, source_file TEXT, imported_at TEXT, delete_flag INT DEFAULT 0);
        CREATE TABLE production_daily (kitting_list_no TEXT, lot_id TEXT, daily_qty INT);
    """)
    con.execute(psv.CREATE_VIEW_SQL)
    yield con
    con.close()


def test_view_datetime_normalization_matches_python(bare_view):
    con = bare_view
    values = FIXED + _random_values()
    mismatches = []
    for v in values:
        expected = svc.normalize_plan_datetime(v)
        con.execute("DELETE FROM kitting_plan_items")
        con.execute("DELETE FROM kitting_plan_batches")
        con.execute("INSERT INTO kitting_plan_batches VALUES (1, 'p.csv', '2026-10-01 09:00:00', 0)")
        if expected is not None:
            # 最新の取り込みに v だけを入れる（期間は expected から expected）。同じ時刻を正規の書式で書いた別の計画は、期間に収まる。
            con.execute("INSERT INTO kitting_plan_items VALUES (1, 1, 'A', 'L', 'F', '1', ?, ?, 1, 0)", (v, v))
            con.execute("INSERT INTO kitting_plan_items VALUES (2, 0, 'B', 'L', 'F', '1', ?, ?, 1, 0)", (expected, expected))
            reason = con.execute("SELECT judge_reason FROM v_plan_stale WHERE kitting_list_no = 'B'").fetchone()[0]
            if reason != "stale":
                mismatches.append((v, expected, reason))
        else:
            con.execute("INSERT INTO kitting_plan_items VALUES (1, 1, 'A', 'L', 'F', '1', '2026/07/01 00:00:00', '2026/07/31 00:00:00', 1, 0)")
            con.execute("INSERT INTO kitting_plan_items VALUES (2, 0, 'B', 'L', 'F', '1', ?, ?, 1, 0)", (v, v))
            reason = con.execute("SELECT judge_reason FROM v_plan_stale WHERE kitting_list_no = 'B'").fetchone()[0]
            if reason != "date_unparsable":
                mismatches.append((v, expected, reason))
    assert mismatches == []


def test_known_difference_full_width_digits_are_not_parsed_by_the_view(bare_view):
    """Python の正規表現は全角数字も数字として読むが、ビューは読まない（解釈できない値＝隠さない側）。実データには出ない。"""
    assert svc.normalize_plan_datetime("２０２６/07/03") == "2026/07/03 00:00:00"
    con = bare_view
    con.execute("INSERT INTO kitting_plan_batches VALUES (1, 'p.csv', '2026-10-01 09:00:00', 0)")
    con.execute("INSERT INTO kitting_plan_items VALUES (1, 1, 'A', 'L', 'F', '1', '2026/07/01 00:00:00', '2026/07/31 00:00:00', 1, 0)")
    con.execute("INSERT INTO kitting_plan_items VALUES (2, 0, 'B', 'L', 'F', '1', '２０２６/07/03', '２０２６/07/04', 1, 0)")
    assert con.execute("SELECT judge_reason FROM v_plan_stale WHERE kitting_list_no = 'B'").fetchone()[0] == "date_unparsable"


# ---------------------------------------------------------------- 判定

FIRST = [
    ("A1", "L1", "2026/07/03 08:00:00", "2026/07/04 08:00:00"),
    ("A2", "L2", "2026/07/05 08:00:00", "2026/07/06 08:00:00"),
    ("A3", "L3", "2026/07/07 08:00:00", "2026/07/08 08:00:00"),
    ("A4", "L4", "2026/07/08 08:00:00", "2026/07/09 08:00:00"),
    ("EARLY", "L5", "2026/07/01 08:00:00", "2026/07/02 08:00:00"),
    ("LATE", "L6", "2026/07/10 08:00:00", "2026/07/13 17:19:59"),
    ("NOEND", "L7", "2026/07/06 08:00:00", ""),
]
SECOND = [
    ("A1", "L1", "2026/07/03 08:00:00", "2026/07/04 08:00:00"),
    ("A2-NEW", "L2", "2026/07/05 08:00:00", "2026/07/06 08:00:00"),
    ("A3-NEW", "L3", "2026/07/07 08:00:00", "2026/07/08 08:00:00"),
    ("A4", "L4", "2026/07/08 08:00:00", "2026/07/09 08:00:00"),
]


def seed_reissue():
    add_batch(FIRST, imported_at="2026-10-01 09:00:00")
    return add_batch(SECOND, imported_at="2026-10-02 09:00:00")


def test_first_import_hides_nothing(db):
    add_batch(FIRST, imported_at="2026-10-01 09:00:00")
    rows = view()
    assert stale(rows) == set() and len(rows) == 7
    assert {k[0]: r["judge_reason"] for k, r in rows.items()} == {
        **{k: "in_latest_batch" for k in ("A1", "A2", "A3", "A4", "EARLY", "LATE")}, "NOEND": "date_unparsable"}   # 日時が解釈できないものは、最新の取り込みにあっても date_unparsable


def test_reissue_marks_only_the_old_plans_and_gives_reasons(db):
    seed_reissue()
    rows = view()
    assert stale(rows) == {("A2", "L2"), ("A3", "L3")}
    assert {k[0]: r["judge_reason"] for k, r in rows.items()} == {
        "A1": "in_latest_batch", "A2": "stale", "A2-NEW": "in_latest_batch", "A3": "stale", "A3-NEW": "in_latest_batch",
        "A4": "in_latest_batch", "EARLY": "outside_period", "LATE": "outside_period", "NOEND": "date_unparsable"}


@pytest.mark.parametrize("start, end, expected", [
    ("2026/07/03 00:00:00", "2026/07/13 17:19:59", True),       # 端ちょうどは収まる
    ("2026/07/02 23:59:59", "2026/07/06 08:00:00", False),      # 開始が期間より前
    ("2026/07/05 08:00:00", "2026/07/13 17:20:00", False),      # 終了が期間の最大より後（日付は同じで時刻だけ後）
    ("2026/07/05 08:00:00", "2026/07/14 00:00:00", False),
    ("2026-07-05 08:00:00", "2026-07-06 08:00:00", True),       # "-" 区切りの書式も同じ
])
def test_period_edges_are_compared_down_to_the_time(db, start, end, expected):
    add_batch([("OLD", "L9", start, end)], imported_at="2026-10-01 09:00:00")
    add_batch([("N1", "L1", "2026/07/03 00:00:00", "2026/07/04 00:00:00"), ("N2", "L2", "2026/07/12 00:00:00", "2026/07/13 17:19:59")],
              imported_at="2026-10-02 09:00:00")
    assert (("OLD", "L9") in stale()) is expected


def test_empty_and_unparseable_dates_are_not_judged(db):
    add_batch([("U1", "L1", "", "2026/07/05 00:00:00"), ("U2", "L2", "xx", "yy"), ("U3", "L3", None, None)], imported_at="2026-10-01 09:00:00")
    add_batch([("N1", "L9", "2026/07/01 00:00:00", "2026/07/31 00:00:00")], imported_at="2026-10-02 09:00:00")
    rows = view()
    assert stale(rows) == set()
    assert {k[0]: r["judge_reason"] for k, r in rows.items() if k[0].startswith("U")} == {
        "U1": "date_unparsable", "U2": "date_unparsable", "U3": "date_unparsable"}


def test_no_latest_batch_and_no_period(db):
    add_batch([("C1", "L1", "2026/07/03 00:00:00", "2026/07/04 00:00:00")], source="carry_over:202609", imported_at="2026-10-01 09:00:00")
    rows = view()                                                     # 前月引き継ぎだけ：最新の取り込みが無い
    assert {r["judge_reason"] for r in rows.values()} == {"no_latest_batch"} and stale(rows) == set()
    assert {r["latest_batch_id"] for r in rows.values()} == {None} and {r["judged_at"] for r in rows.values()} == {None}
    add_batch([("P1", "L9", "xx", "yy")], imported_at="2026-10-02 09:00:00")      # 最新の取り込みの日時が全部解釈できない
    rows = view()
    assert {r["judge_reason"] for k, r in rows.items() if k[0] == "C1"} == {"no_period"} and stale(rows) == set()


def test_latest_batch_rules(db):
    first = add_batch([("K1", "L1", "2026/07/01 00:00:00", "2026/07/02 00:00:00")], imported_at="2026-10-01 09:00:00")
    second = add_batch([("K2", "L2", "2026/07/03 00:00:00", "2026/07/04 00:00:00")], imported_at="2026-10-02 09:00:00")
    latest = lambda: {r["latest_batch_id"] for r in view().values()}
    assert latest() == {second}
    add_batch([("K3", "L3", "2026/07/05 00:00:00", "2026/07/06 00:00:00")], source="carry_over:202609", imported_at="2026-10-03 09:00:00")
    assert latest() == {second}                                        # 前月引き継ぎは数えない
    mark_batch_deleted(second, True)
    assert latest() == {first}                                         # 削除したバッチは数えない
    mark_batch_deleted(second, False)
    assert latest() == {second}                                        # 復元で戻る
    third = add_batch([("K4", "L4", "2026/07/07 00:00:00", "2026/07/08 00:00:00")], imported_at="2026-10-02 09:00:00")
    assert third > second and latest() == {third}                      # 同じ時刻なら plan_batch_id が大きい方


def test_deleting_the_newest_batch_judges_by_the_previous_one_and_restore_comes_back(db):
    newest = seed_reissue()
    mark_batch_deleted(newest, True)
    assert stale() == set()                                            # 古い版は有効に戻らない既存の動きのまま
    mark_batch_deleted(newest, False)
    assert stale() == {("A2", "L2"), ("A3", "L3")}


def test_reimporting_the_same_content_keeps_the_new_versions_out_of_stale(db):
    seed_reissue()
    add_batch(SECOND, imported_at="2026-10-03 09:00:00")
    assert stale() == {("A2", "L2"), ("A3", "L3")}


def test_delete_flag_1_rows_count_for_the_period_but_are_not_view_rows(db):
    add_batch([("K1", "L1", "2026/07/05 08:00:00", "2026/07/06 08:00:00"),
               ("K2", "L2", "2026/07/01 08:00:00", "2026/07/09 20:00:00", {"delete_flag": 1})], imported_at="2026-10-02 09:00:00")
    add_batch([("OLD", "L9", "2026/07/02 00:00:00", "2026/07/03 00:00:00")], imported_at="2026-10-01 09:00:00")
    rows = view()
    assert ("K2", "L2") not in rows                                    # delete_flag=1 の計画は、ビューの行にならない
    assert ("OLD", "L9") in stale(rows)                                # 期間は delete_flag=1 の行（07/01 〜 07/09）も入れて決まる


def test_has_legacy_actuals_is_the_sum_of_daily_qty_above_zero(db):
    seed_reissue()
    from models.kitting_plan import find_plan_item_by_kitting_no
    insert_daily_production(find_plan_item_by_kitting_no("A2", "L2")["plan_item_id"], "A2", "L2", "B", "2026-10-02", 3, "W1")
    insert_daily_production(find_plan_item_by_kitting_no("A3", "L3")["plan_item_id"], "A3", "L3", "B", "2026-10-02", 0, "W1")
    rows = view()
    assert rows[("A2", "L2")]["has_legacy_actuals"] == 1 and rows[("A2", "L2")]["is_stale"] == 1       # is_stale に実績の条件は入らない
    assert rows[("A3", "L3")]["has_legacy_actuals"] == 0                                              # 合計 0 は「実績なし」（画面と同じ）
    assert rows[("A1", "L1")]["has_legacy_actuals"] == 0
    assert fetch_stale_plan_keys() == {("A3", "L3")}                   # 画面が隠すのは、実績のない古い計画だけ
    assert count_stale_plans() == 2


def test_replacement_fields_only_for_stale_plans(db):
    def row(kitting, lot, side="1", file_no="0001"):
        return kitting, lot, "2026/07/05 08:00:00", "2026/07/06 08:00:00", {"production_side": side, "setup_file_no": file_no}

    add_batch([row("O1", "L1"), row("O2", "L2"), row("O3", "L3"), row("S1", "L4")], imported_at="2026-10-01 09:00:00")
    add_batch([row("N1", "L1"), row("N2a", "L2"), row("N2b", "L2"), row("S1", "L4"), row("S1-SIBLING", "L4")],
              imported_at="2026-10-02 09:00:00")
    rows = view()
    pick = lambda k, l: (rows[(k, l)]["replacement_kitting_list_no"], rows[(k, l)]["replacement_count"])
    assert pick("O1", "L1") == ("N1", 1)                               # 候補が1つ
    assert pick("O2", "L2") == (None, 2)                               # 候補が2つ以上：キッティングNo.は NULL
    assert pick("O3", "L3") == (None, 0)                               # 置き換え先が無い
    assert rows[("S1", "L4")]["is_stale"] == 0 and pick("S1", "L4") == (None, 0)            # 古くない計画は、兄弟の計画があっても NULL と 0
    assert pick("N2a", "L2") == (None, 0)
    # 面・ファイルNo.が違う計画は候補にならない（P1 は古い計画だが、同じ面・ファイルNo.の計画が最新の取り込みに無い）
    add_batch([row("P1", "L5", side="2")], imported_at="2026-10-03 09:00:00")
    add_batch([row("Q1", "L5", side="1"), row("Q2", "L5", file_no="0002", side="2")], imported_at="2026-10-04 09:00:00")
    rows = view()
    assert rows[("P1", "L5")]["is_stale"] == 1 and pick("P1", "L5") == (None, 0)


def test_judged_at_is_the_imported_at_of_the_latest_batch(db):
    seed_reissue()
    assert {r["judged_at"] for r in view().values()} == {"2026-10-02 09:00:00"}
    mark_batch_deleted(max(r["latest_batch_id"] for r in view().values()), True)
    assert {r["judged_at"] for r in view().values()} == {"2026-10-01 09:00:00"}


def test_reads_with_mode_ro_and_a_db_without_the_view_means_no_stale_plans(db, tmp_path):
    seed_reissue()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    assert con.execute("SELECT COUNT(*) FROM v_plan_stale WHERE is_stale = 1").fetchone()[0] == 2
    con.close()
    with sqlite3.connect(db) as c:
        c.execute("DROP VIEW v_plan_stale")                            # 古い月の DB（この版のアプリで一度も開いていない）
    assert fetch_stale_plan_keys() == set() and count_stale_plans() == 0


# ---------------------------------------------------------------- 計画CSVの取り込みの完了メッセージ

HEADER = ",".join(["a"] * 16)


def _csv(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(HEADER + "\n")
        for kitting, lot, start, end in rows:
            f.write(",".join([kitting, "0", "0001", lot, "L", "B", "10", "0", "10", "1", "", start, end, "", "", ""]) + "\n")
    return str(path)


class _Box:
    def __init__(self):
        self.calls = []

    def showinfo(self, title, message, **kwargs):
        self.calls.append(("info", title, message))

    def showerror(self, title, message, **kwargs):
        self.calls.append(("error", title, message))

    def showwarning(self, title, message, **kwargs):
        self.calls.append(("warning", title, message))


def test_import_result_counts_stale_plans_and_the_completion_message_shows_it(db, tmp_path, monkeypatch):
    from ui.kitting import kitting_plan_import as screen

    first = import_kitting_plan_csv(_csv(tmp_path / "p1.csv", [(k, l, s, e) for k, l, s, e in FIRST if e]), "T")
    assert first["stale_plan_count"] == 0
    second = import_kitting_plan_csv(_csv(tmp_path / "p2.csv", SECOND), "T")
    assert second["stale_plan_count"] == count_stale_plans() == 2

    box = _Box()
    monkeypatch.setattr(screen, "messagebox", box)
    monkeypatch.setattr(screen, "log_operation", lambda *a, **k: None)
    monkeypatch.setattr(screen, "find_newly_hidden_side1_plans", lambda batch_id: [])
    labels = types.SimpleNamespace(config=lambda **k: None)
    results = queue.Queue()
    results.put((True, {**second, "file": "p2.csv"}))
    fake = types.SimpleNamespace(
        _result_queue=results, _loading_window=None, btn_import=labels, lbl_status=labels, result_label=labels,
        current_worker={"name": "t"}, winfo_toplevel=lambda: "top", _load_batch_list=lambda **k: None,
        after=lambda ms, fn: None,
    )
    fake._poll_result_queue = types.MethodType(screen.KittingPlanImportWindow._poll_result_queue, fake)
    screen.KittingPlanImportWindow._poll_result_queue(fake)
    assert box.calls[0][0] == "info" and "古い計画と判定された計画：2件" in box.calls[0][2]
    assert "4件のキッティング計画を取り込みました。" in box.calls[0][2]


# ---------------------------------------------------------------- is_stale と judge_reason の対応・ロットNo. が空の行・judged_at の書式

def _mismatch_count(path=None):
    with sqlite3.connect(path or config.DB_PATH) as con:
        return con.execute("SELECT COUNT(*) FROM v_plan_stale WHERE (is_stale = 1) <> (judge_reason = 'stale')").fetchone()[0]


def test_is_stale_is_1_exactly_when_judge_reason_is_stale(db):
    """is_stale=1 なら stale、stale なら is_stale=1。判定の局面を変えながら、そのつど確かめる。"""
    from models.kitting_plan import find_plan_item_by_kitting_no
    add_batch(FIRST, imported_at="2026-10-01 09:00:00")
    assert _mismatch_count() == 0 and not stale()
    add_batch(SECOND, imported_at="2026-10-02 09:00:00")
    rows = view()
    assert stale(rows) and {r["judge_reason"] for r in rows.values()} >= {"stale", "in_latest_batch", "outside_period", "date_unparsable"}
    assert _mismatch_count() == 0
    insert_daily_production(find_plan_item_by_kitting_no("A2", "L2")["plan_item_id"], "A2", "L2", "B", "2026-10-02", 3, "W1")
    assert _mismatch_count() == 0 and ("A2", "L2") in stale()                                 # 実績があっても is_stale と stale は対応する
    add_batch([("C1", "L9", "2026/07/03 00:00:00", "2026/07/04 00:00:00")], source="carry_over:202609", imported_at="2026-10-05 09:00:00")
    assert _mismatch_count() == 0
    add_batch([("P1", "L8", "xx", "yy")], imported_at="2026-10-06 09:00:00")                   # 期間が決まらない
    rows = view()
    assert not stale(rows) and "no_period" in {r["judge_reason"] for r in rows.values()} and _mismatch_count() == 0
    for r in rows.values():
        assert r["is_stale"] in (0, 1) and (r["is_stale"] == 1) == (r["judge_reason"] == "stale")


def _insert_raw_plan(kitting, lot, batch_id, start="2026/07/05 08:00:00", end="2026/07/06 08:00:00", active=1):
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute(
            "INSERT INTO kitting_plan_items (plan_batch_id, kitting_list_no, lot_no, setup_file_no, production_side, "
            "plan_start_datetime, plan_end_datetime, version, is_active) VALUES (?, ?, ?, '0001', '1', ?, ?, 1, ?)",
            (batch_id, kitting, lot, start, end, active))


def test_import_never_writes_null_lot_no_and_empty_lot_no_is_one_row_per_kitting_list_no(db, tmp_path):
    """取り込みの経路：ロットNo. が空の行は、保留の表には入らず、空文字のまま kitting_plan_items に入る（NULL にはならない）。"""
    first = import_kitting_plan_csv(_csv(tmp_path / "p1.csv", [("E1", "", "2026/07/05 08:00:00", "2026/07/06 08:00:00"),
                                                              ("E2", "L2", "2026/07/05 08:00:00", "2026/07/06 08:00:00")]), "T")
    assert first["inserted"] == 2 and first["pending_saved_count"] == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM kitting_plan_items WHERE lot_no IS NULL").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM kitting_plan_items WHERE lot_no = ''").fetchone()[0] == 1
    import_kitting_plan_csv(_csv(tmp_path / "p2.csv", [("E1", "", "2026/07/05 08:00:00", "2026/07/06 08:00:00")]), "T")   # 取り込み直し
    rows = view()
    assert [k for k in rows if k[0] == "E1"] == [("E1", "")]          # 古い版は無効になり、有効な行は1つだけ


def test_view_returns_null_and_empty_lot_no_as_one_empty_key_and_never_two_rows_for_a_key(db):
    batch = add_batch([("N1", "L1", "2026/07/05 08:00:00", "2026/07/06 08:00:00")], imported_at="2026-10-02 09:00:00")
    _insert_raw_plan("NULLLOT", None, batch)                           # 古い DB にある lot_no が NULL の行（004 の列追加で NULL になった行など）
    _insert_raw_plan("EMPTYLOT", "", batch)
    rows = view()
    assert ("NULLLOT", "") in rows and ("EMPTYLOT", "") in rows         # NULL も空文字も、lot_no は '' で1行ずつ返る
    assert all(k[1] is not None for k in rows)
    # 同じ kitting_list_no で有効な行を2つ（NULL と空文字、NULL と NULL、空文字と空文字）入れようとしても、部分ユニークインデックスが断る
    for lot in (None, ""):
        with pytest.raises(sqlite3.IntegrityError):
            _insert_raw_plan("NULLLOT", lot, batch)
        with pytest.raises(sqlite3.IntegrityError):
            _insert_raw_plan("EMPTYLOT", lot, batch)
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM (SELECT kitting_list_no, lot_no FROM v_plan_stale GROUP BY 1, 2 HAVING COUNT(*) > 1)").fetchone()[0] == 0
    # 無効な行（古い版）は有効な行と同じキーでもよく、ビューには出ない
    _insert_raw_plan("NULLLOT", None, batch, active=0)
    assert len([k for k in view() if k[0] == "NULLLOT"]) == 1


def test_null_lot_no_actuals_and_staleness_use_the_empty_key(db):
    first = create_plan_batch("plan.csv", "T", 1)
    set_imported_at(first, "2026-10-01 09:00:00")
    _insert_raw_plan("OLD0", None, first)
    add_batch([("N1", "L1", "2026/07/01 00:00:00", "2026/07/31 00:00:00")], imported_at="2026-10-02 09:00:00")
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO production_daily (plan_item_id, kitting_list_no, lot_id, report_date, daily_qty, worker_id) VALUES (1, 'OLD0', NULL, '2026-10-02', 4, 'W1')")
    row = view()[("OLD0", "")]
    assert row["is_stale"] == 1 and row["has_legacy_actuals"] == 1


def test_view_never_returns_a_key_twice_on_a_busy_database(db):
    seed_reissue()
    add_batch(SECOND, imported_at="2026-10-03 09:00:00")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT COUNT(*) FROM (SELECT kitting_list_no, lot_no FROM v_plan_stale GROUP BY 1, 2 HAVING COUNT(*) > 1)").fetchone()[0] == 0
        assert con.execute("SELECT COUNT(*) FROM v_plan_stale").fetchone()[0] == con.execute(
            "SELECT COUNT(*) FROM kitting_plan_items WHERE COALESCE(is_active,1)=1 AND COALESCE(delete_flag,0)=0").fetchone()[0]


def test_judged_at_has_the_format_of_imported_at(db):
    import re
    import_kitting_plan_csv(_csv(os.path.join(os.path.dirname(db), "p.csv"), [("J1", "L1", "2026/07/05 08:00:00", "2026/07/06 08:00:00")]), "T")
    with sqlite3.connect(db) as con:
        imported_at = con.execute("SELECT imported_at FROM kitting_plan_batches").fetchone()[0]       # 既定値 datetime('now','localtime')
    judged_at = view()[("J1", "L1")]["judged_at"]
    assert judged_at == imported_at and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", judged_at)
