"""
現場実績CSVの登録前の一覧（差の計算・状態の判定）と登録（services/external_actual_service.py）。
合成データの月次DB で、行ごとの状態、実績・NG の保存、上書き、プレビュー後の変化、NG の部分失敗、DB の食い違いを確かめる。
"""
import os
import sqlite3

import pytest

import config
from db.init_db import init_database_at
from models import external_actual_rows as ear
from models.board_structure_master import upsert_board_structure
from models.kitting_plan import init_kitting_plan_tables, create_plan_batch, create_plan_version, find_plan_item_by_kitting_no
from models.ng_declarations import init_ng_declarations_table, get_ng_declaration, save_ng_declaration
from models.operation_log import list_operation_log
from models.production import insert_daily_production
from models.production_side_master import upsert_production_side
from models.workers import create_worker
from services import external_actual_service as svc
from services.production_service import get_daily_history, SIDE2_WAIT_MESSAGE

FOLDER = "test_month"
START = "2026/07/22 08:00:00"
ADMIN = {"worker_id": "A1", "name": "管理者"}
# (キッティングNo., ロットNo., ファイルNo., 面, 基板名)
PLANS = [
    ("K1", "L1", "0001", "1", "BoardA"), ("K2", "L1", "0001", "2", "BoardA"),     # 両面
    ("K1x", "L1", "0009", "1", "BoardA"),                                         # 別のファイルNo.の面1（面1のキッティングNo.の取り違えの確認用）
    ("K3", "L2", "0002", "2", "BoardB"),                                          # 面2だけ（面1の計画なし）
    ("K4", "L3", "0003", "1", "BoardC"),                                          # 片面の製品（面1だけ。生産面マスター未登録）
    ("K5", "L4", "0004", "1", "BoardD"),                                          # 面2待ち
    ("KC", "LC", "0005", "2", "B-LC"),                                            # 完成済みにするロット
]


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", os.path.join(config.APP_DATA_DIR, "db", FOLDER, "inventory.db"))
    init_database_at(config.DB_PATH)
    init_kitting_plan_tables()
    init_ng_declarations_table()
    batch = create_plan_batch("plan.csv", imported_by="test", row_count=len(PLANS))
    for kitting_no, lot_no, file_no, side, board in PLANS:
        create_plan_version(batch, kitting_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board, "production_side": side,
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": START, "mounting_line": "A",
        }, created_by="test")
    upsert_production_side("0004", "A", "1")
    upsert_production_side("0004", "B", "2")
    upsert_board_structure("B-LC", 1)
    create_worker("W1", "太郎")
    plan = find_plan_item_by_kitting_no("KC", "LC")
    insert_daily_production(plan["plan_item_id"], "KC", "LC", "B-LC", "2026-10-02", 100, "W1")


def add_rows(*overrides, target=FOLDER, exported="2026-10-20 18:00:00"):
    rows = []
    for number, extra in enumerate(overrides, start=2):
        row = {"csv_row_no": number, "kitting_list_no": "K2", "lot_no": "L1", "file_no": "0001", "side": "2",
               "report_date": "2026-10-20", "daily_qty": 99, "ng_side1": 0, "ng_side2": 1, "worker_id": "W1",
               "face1_kitting_list_no": "", "exported_at": exported}
        row.update(extra)
        rows.append(row)
    batch = {"source_file": "x.csv", "file_sha256": os.urandom(8).hex(), "exported_at": exported,
             "source_db_name": FOLDER, "target_db_name": target}
    ear.import_batch(batch, rows, "admin")


def preview():
    return {(e["kitting_list_no"], e["lot_no"]): e for e in svc.build_preview()}


def register(row_ids=None, snapshots=None):
    items = svc.build_preview()
    ids = row_ids if row_ids is not None else [e["row_id"] for e in items]
    snaps = snapshots if snapshots is not None else {e["row_id"]: e["snapshot"] for e in items}
    return svc.register_rows(ids, snaps, ADMIN)


def history(kitting_no, lot_no):
    return [(r["report_date"], r["daily_qty"], r["worker_id"]) for r in get_daily_history(kitting_no, lot_no)]


# ---------------------------------------------------------------- 状態

def test_new_row_is_green_and_ng_side1_follows_side2(db):
    add_rows({"ng_side1": 0, "ng_side2": 1, "face1_kitting_list_no": "K1"})
    item = preview()[("K2", "L1")]
    assert (item["state"], item["label"], item["qty_now"], item["qty_new"]) == ("green", "新規", None, 99)
    assert item["remarks"] == [] and item["planned_qty"] == 100
    # 面2の NG 1 は面1にも連動して保存される（既存の compute_ng_save_qty）。実績＋NG = 100 で予定数と一致する
    assert svc.evaluate_rows(ear.list_pending_rows())[0]["_save_qty"] == {"1": 1.0, "2": 1.0}


def test_register_new_row_writes_actual_and_ng_and_marks_registered(db):
    add_rows({"ng_side1": 2, "ng_side2": 1, "daily_qty": 97, "face1_kitting_list_no": "K1"})
    outcome = register()
    (result,) = outcome["results"]
    assert result["status"] == svc.RESULT_REGISTERED and result["new_cumulative"] == 97 and result["declared_sides"] == ["1", "2"]
    assert history("K2", "L1") == [("2026-10-20", 97, "W1")]                              # 担当者・日付はCSVのもの
    assert get_ng_declaration("K1", 1, lot_no="L1")["ng_qty"] == 3                         # 面1の保存値 = 面1の2 + 面2の1
    assert get_ng_declaration("K2", 2, lot_no="L1")["ng_qty"] == 1
    assert ear.list_pending_rows() == [] and outcome["lot_nos"] == ["L1"]
    log = [r for r in list_operation_log() if r["operation_name"] == "現場CSV取込登録"]
    assert len(log) == 1 and log[0]["worker_name"] == "管理者"
    assert "実績 なし→97" in log[0]["detail"] and "NG面1の保存値 なし→3" in log[0]["detail"] and "CSVの担当者 W1" in log[0]["detail"]
    with sqlite3.connect(config.DB_PATH) as con:
        assert con.execute("SELECT COUNT(*) FROM lot_status_history WHERE trigger_source = 'external_csv'").fetchone()[0] == 1
        assert con.execute("SELECT status, registered_by FROM external_actual_rows").fetchone() == ("registered", "A1")
    again = preview()
    assert again == {}                                                                       # 登録済みは一覧に出ない


def test_overwrite_shows_now_and_new_and_logs_both(db):
    plan = find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(plan["plan_item_id"], "K2", "L1", "BoardA", "2026-10-10", 50, "W1")
    add_rows({"daily_qty": 99, "ng_side2": 1})
    item = preview()[("K2", "L1")]
    assert (item["state"], item["label"], item["qty_now"], item["is_overwrite"]) == ("green", "上書き", 50, True)
    assert any("2026-10-10 から 2026-10-20 に変わります" in r for r in item["remarks"])
    register()
    assert history("K2", "L1") == [("2026-10-20", 99, "W1")]
    log = [r for r in list_operation_log() if r["operation_name"] == "現場CSV取込登録"][0]
    assert "実績 50→99" in log["detail"]


def test_older_date_planned_mismatch_and_completed_lot_are_yellow(db):
    plan = find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(plan["plan_item_id"], "K2", "L1", "BoardA", "2026-10-25", 50, "W1")
    add_rows({"report_date": "2026-10-20", "daily_qty": 99, "ng_side2": 1},
             {"kitting_list_no": "K3", "lot_no": "L2", "file_no": "0002", "daily_qty": 90, "ng_side2": 0},
             {"kitting_list_no": "KC", "lot_no": "LC", "file_no": "0005", "daily_qty": 120, "ng_side2": 0})
    items = preview()
    assert items[("K2", "L1")]["state"] == "yellow" and any("より古い日付" in r for r in items[("K2", "L1")]["remarks"])
    assert items[("K3", "L2")]["state"] == "yellow" and any("予定生産数100と不一致" in r for r in items[("K3", "L2")]["remarks"])
    done = items[("KC", "LC")]
    assert done["state"] == "yellow" and any("完成済みロット" in r for r in done["remarks"])


def test_same_values_are_gray(db):
    plan = find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(plan["plan_item_id"], "K2", "L1", "BoardA", "2026-10-20", 99, "W1")
    save_ng_declaration("K1", "0001", 1, 1, "2026-10-20", lot_no="L1")
    save_ng_declaration("K2", "0001", 2, 1, "2026-10-20", lot_no="L1")
    add_rows({"daily_qty": 99, "ng_side2": 1})
    item = preview()[("K2", "L1")]
    assert (item["state"], item["label"]) == ("gray", "変更なし")


@pytest.mark.parametrize("override, expected", [
    ({"kitting_list_no": "ZZ"}, "計画がありません"),
    ({"side": "1"}, "面が計画（面2）と違います"),
    ({"file_no": "0009"}, "ファイルNo.が計画（0001）と違います"),
    ({"worker_id": "W9"}, "担当者ID「W9」が作業者マスターにありません"),
    ({"kitting_list_no": "K1", "side": "1", "ng_side2": 0}, "面1です"),                       # 面2がある面1
    ({"kitting_list_no": "K5", "lot_no": "L4", "file_no": "0004", "side": "1", "ng_side2": 0}, SIDE2_WAIT_MESSAGE),
    ({"kitting_list_no": "K3", "lot_no": "L2", "file_no": "0002", "ng_side1": 1}, "面1のキッティングNo.が空のため、NG面1（1）を保存する面1の計画が分かりません"),
    ({"face1_kitting_list_no": "ZZ"}, "面1のキッティングNo.「ZZ」（ロットNo. L1）の計画がありません"),
    ({"face1_kitting_list_no": "K4"}, "面1のキッティングNo.「K4」（ロットNo. L1）の計画がありません"),              # ロットNo.が違う（K4 は L3）
    ({"face1_kitting_list_no": "K2"}, "面1のキッティングNo.「K2」の計画は面2です"),
    ({"face1_kitting_list_no": "K1x"}, "面1のキッティングNo.「K1x」の計画のファイルNo.（0009）がCSVの0001と違います"),
])
def test_red_rows_cannot_be_registered(db, override, expected):
    add_rows(override)
    (item,) = svc.build_preview()
    assert item["state"] == "red" and item["snapshot"] is None and any(expected in r for r in item["remarks"]), item["remarks"]
    (result,) = svc.register_rows([item["row_id"]], {}, ADMIN)["results"]
    assert result["status"] == svc.RESULT_REFUSED
    assert len(ear.list_pending_rows()) == 1 and get_daily_history(item["kitting_list_no"], item["lot_no"]) == []


def test_empty_face1_kitting_no_with_zero_ng_side1_is_registerable_and_face1_ng_is_not_saved(db):
    add_rows({"ng_side1": 0, "ng_side2": 1, "face1_kitting_list_no": ""})
    (item,) = svc.build_preview()
    assert item["state"] == "green"
    register()
    assert get_ng_declaration("K2", 2, lot_no="L1")["ng_qty"] == 1 and get_ng_declaration("K1", 1, lot_no="L1") is None


def test_face1_is_decided_by_the_csv_column_not_guessed(db):
    """面1の計画が2つあるロット（K1・K1x）でも、CSV の面1のキッティングNo.の計画にNGを保存する。"""
    add_rows({"ng_side1": 2, "ng_side2": 0, "face1_kitting_list_no": "K1", "daily_qty": 98})
    register()
    assert get_ng_declaration("K1", 1, lot_no="L1")["ng_qty"] == 2 and get_ng_declaration("K1x", 1, lot_no="L1") is None


def test_single_sided_product_side1_row_is_registerable(db):
    add_rows({"kitting_list_no": "K4", "lot_no": "L3", "file_no": "0003", "side": "1", "ng_side1": 2, "ng_side2": 0, "daily_qty": 98})
    (item,) = svc.build_preview()
    assert item["state"] == "green"
    register()
    assert get_ng_declaration("K4", 1, lot_no="L3")["ng_qty"] == 2


def test_zero_ng_does_not_touch_existing_declaration(db):
    save_ng_declaration("K2", "0001", 2, 3, "2026-10-01", lot_no="L1")
    add_rows({"daily_qty": 97, "ng_side1": 0, "ng_side2": 0})
    item = preview()[("K2", "L1")]
    assert svc.NG_ZERO_REMARK in item["remarks"] and item["ng2_now"] == 3 and item["ng2_new"] == 0
    register()
    assert get_ng_declaration("K2", 2, lot_no="L1")["ng_qty"] == 3                         # NG 0 は既存の申告を変えない


def test_ng_side1_shows_only_the_own_part(db):
    save_ng_declaration("K1", "0001", 1, 5, "2026-10-01", lot_no="L1")                    # 面1の保存値 5 = 面1の固有分 3 + 面2の 2
    save_ng_declaration("K2", "0001", 2, 2, "2026-10-01", lot_no="L1")
    add_rows({"daily_qty": 95, "ng_side1": 3, "ng_side2": 2, "face1_kitting_list_no": "K1"})
    item = preview()[("K2", "L1")]
    assert (item["ng1_now"], item["ng1_new"], item["ng2_now"], item["ng2_new"]) == (3.0, 3, 2.0, 2)


# ---------------------------------------------------------------- 登録の安全策

def test_value_changed_after_preview_is_not_registered(db):
    add_rows({"daily_qty": 97, "ng_side2": 1})
    items = svc.build_preview()
    plan = find_plan_item_by_kitting_no("K2", "L1")
    insert_daily_production(plan["plan_item_id"], "K2", "L1", "BoardA", "2026-10-19", 10, "W1")   # プレビューの後で、誰かが登録した
    outcome = svc.register_rows([items[0]["row_id"]], {items[0]["row_id"]: items[0]["snapshot"]}, ADMIN)
    assert outcome["results"][0]["status"] == svc.RESULT_CHANGED
    assert outcome["results"][0]["message"] == "プレビュー後に値が変わりました。一覧を更新して確認してください"
    assert history("K2", "L1") == [("2026-10-19", 10, "W1")] and len(ear.list_pending_rows()) == 1


def test_batch_from_another_db_stops_everything(db):
    add_rows({}, {"kitting_list_no": "K3", "lot_no": "L2", "file_no": "0002"}, target="2026-09")
    with pytest.raises(svc.ExternalActualRefused, match="2026-09"):
        register()
    assert get_daily_history("K2", "L1") == []


def test_default_db_and_version_mismatch_refuse_registration(db):
    add_rows({})
    items = svc.build_preview()
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute("PRAGMA user_version = 14")
    with pytest.raises(svc.ExternalActualRefused, match="版"):
        svc.register_rows([items[0]["row_id"]], {}, ADMIN)


def test_ng_failure_after_actual_is_partial_and_row_stays_pending(db):
    with sqlite3.connect(config.DB_PATH) as con:
        con.execute("CREATE TRIGGER fail_ng BEFORE INSERT ON ng_declarations WHEN NEW.production_side = 1 "
                    "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    add_rows({"daily_qty": 97, "ng_side1": 1, "ng_side2": 1, "face1_kitting_list_no": "K1"})
    outcome = register()
    (result,) = outcome["results"]
    assert result["status"] == svc.RESULT_PARTIAL and "面1のNG登録に失敗しました" in result["message"]
    assert history("K2", "L1") == [("2026-10-20", 97, "W1")]                              # 実績は登録できている
    assert get_ng_declaration("K2", 2, lot_no="L1")["ng_qty"] == 1
    assert len(ear.list_pending_rows()) == 1                                               # 再登録できるよう登録待ちのまま
    assert outcome["lot_nos"] == ["L1"]


def test_registering_the_same_row_twice_does_nothing_the_second_time(db):
    add_rows({"daily_qty": 97})
    row_id = svc.build_preview()[0]["row_id"]
    svc.register_rows([row_id], {}, ADMIN)
    (second,) = svc.register_rows([row_id], {}, ADMIN)["results"]
    assert second["status"] == svc.RESULT_SKIPPED and len(history("K2", "L1")) == 1


def test_preview_does_not_query_per_row(db, monkeypatch):
    """計画のある行の解決と今の値の読み出しは一括。行を増やしても DB に接続する回数は変わらない（完成済みの判定はロットごとなので止めて数える）。"""
    batch = create_plan_batch("more.csv", imported_by="test", row_count=40)
    for i in range(40):
        create_plan_version(batch, f"P{i}", {
            "lot_no": f"M{i}", "setup_file_no": "0100", "board_name": "BoardM", "production_side": "2",
            "planned_qty": 100, "order_qty": 100, "plan_start_datetime": START, "mounting_line": "A"}, created_by="test")
    monkeypatch.setattr(svc, "preview_completed_lot_registration", lambda *args, **kwargs: None)
    connections = []
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connections.append(args[0]) or real_connect(*args, **kwargs))

    def count_connections(rows):
        add_rows(*rows)
        connections.clear()
        evaluated = svc.build_preview()
        assert all(e["state"] == "green" for e in evaluated), [e["remarks"] for e in evaluated]
        return len(connections)

    few = count_connections([{"kitting_list_no": f"P{i}", "lot_no": f"M{i}", "file_no": "0100", "ng_side2": 0, "daily_qty": 100} for i in range(3)])
    many = count_connections([{"kitting_list_no": f"P{i}", "lot_no": f"M{i}", "file_no": "0100", "ng_side2": 0, "daily_qty": 100} for i in range(3, 40)])
    assert few > 0 and few == many
