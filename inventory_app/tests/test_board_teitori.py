"""
基板丁数の対応表（master.db の board_teitori_map とビュー v_board_teitori、D-191）。
偽の TSV フォルダと、使い捨ての月次DB・master.db を使う（conftest.py が config の3つのパスを一時フォルダに向ける）。
"""
import os
import sqlite3
import threading
import time

import pytest

import config
import services.board_teitori_runner as runner_module
import services.board_teitori_service as service
from db.init_db import init_database_at
from models import board_teitori_map as teitori_map
from models.db_common import get_master_connection
from models.kitting_plan import create_plan_batch, create_plan_version
from models.kitting_plan_setup_pairs import count_active_plan_rows_by_pair, has_plan_items_table
from models.monthly_db_readonly import open_monthly_db_readonly
from models.parts_attributes import upsert_parts_attributes
from services.bom_file_service import BOMFileIndex

HEADER = ["セットアップファイルNo.", "生産面", "実装ライン", "セットアップ部品種別", "96コード", "部品員数", "マスターCHK員数係数", "減数種別"]


def write_tsv(folder, file_no, rows, subfolder=None, header=None):
    """rows は (実装ライン, 種別, 96コード) のリスト。folder/<サブフォルダ>/<file_no>.tsv に書く。"""
    sub = os.path.join(folder, subfolder or file_no)
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, f"{file_no}.tsv")
    lines = ["\t".join(header or HEADER)]
    for line, kind, code in rows:
        lines.append("\t".join([file_no, "1", line, kind, code, "1", "1", ""]))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")
    return path


@pytest.fixture
def bom_folder(tmp_path):
    folder = str(tmp_path / "bom")
    os.makedirs(folder)
    write_tsv(folder, "161", [("L", "K", "96000001"), ("L", "P", "96111111")])                   # ok
    write_tsv(folder, "200", [("L", "P", "96111111")])                                            # no_k_row
    write_tsv(folder, "300", [("L", "K", "96000002"), ("L", "K", "96000003")])                    # multi_k
    write_tsv(folder, "400", [("L", "K", "96000004")], header=HEADER[:4] + ["96254924"] + HEADER[5:])   # read_error（破損ヘッダー）
    write_tsv(folder, "432_A4-1", [("L", "K", "96000005")], subfolder="432")                      # ambiguous_tsv（2件）
    write_tsv(folder, "432_A6", [("L", "K", "96000006")], subfolder="432")
    write_tsv(folder, "500", [("L", "K", "96000007")])                                            # code_not_in_master
    write_tsv(folder, "600", [("L", "K", "96000008")])                                            # teitori_invalid
    return folder


def make_monthly_db(name, plans):
    """APP_DATA_DIR/db/<name>/inventory.db を作り、plans [(kitting_list_no, lot_no, file_no, line, extra)] を入れる。"""
    path = os.path.join(config.APP_DATA_DIR, "db", name, "inventory.db")
    init_database_at(path)
    old = config.DB_PATH
    config.DB_PATH = path
    try:
        batch = create_plan_batch("t.csv", "T", len(plans))
        for kitting_no, lot_no, file_no, line, extra in plans:
            create_plan_version(batch, kitting_no, {
                "lot_no": lot_no, "setup_file_no": file_no, "mounting_line": line, "board_name": "B",
                "production_side": "1", "planned_qty": 10, "order_qty": 10, **extra,
            }, created_by="T")
    finally:
        config.DB_PATH = old
    return path


ALL_PLANS = [
    ("K-1", "L1", "0161", "L", {}), ("K-2", "L2", "0200", "L", {}), ("K-3", "L3", "0300", "L", {}),
    ("K-4", "L4", "0400", "L", {}), ("K-5", "L5", "0432", "L", {}), ("K-6", "L6", "0500", "L", {}),
    ("K-7", "L7", "0600", "L", {}), ("K-8", "L8", "0999", "L", {}),
]


@pytest.fixture
def master(bom_folder):
    upsert_parts_attributes("96000001", 5)
    upsert_parts_attributes("96000008", 0)
    return bom_folder


def view_by_pair():
    return {(r["setup_file_no"], r["mounting_line"]): r for r in teitori_map.list_view_rows()}


# ---------------------------------------------------------------- 8種類の status

def test_all_eight_statuses(master):
    path = make_monthly_db("m1", ALL_PLANS)
    summary = service.refresh_normal([path], folder=master)
    assert summary["pairs"] == 8 and summary["written"] == 8
    rows = view_by_pair()
    assert {k[0]: r["status"] for k, r in rows.items()} == {
        "0161": "ok", "0200": "no_k_row", "0300": "multi_k", "0400": "read_error", "0432": "ambiguous_tsv",
        "0500": "code_not_in_master", "0600": "teitori_invalid", "0999": "tsv_not_found",
    }
    assert set(r["status"] for r in rows.values()) == set(teitori_map.VIEW_STATUSES)
    assert rows[("0161", "L")]["teitori"] == 5 and rows[("0161", "L")]["part_no"] == "96000001"
    assert all(r["teitori"] is None for k, r in rows.items() if k[0] != "0161")
    assert set(teitori_map.list_view_rows()[0]) == {"setup_file_no", "mounting_line", "part_no", "teitori", "status", "fetched_at"}


def test_map_table_only_has_tsv_statuses(master):
    path = make_monthly_db("m1", ALL_PLANS)
    service.refresh_normal([path], folder=master)
    with get_master_connection() as con:
        statuses = {r[0] for r in con.execute("SELECT status FROM board_teitori_map")}
        assert statuses == set(teitori_map.MAP_STATUSES)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO board_teitori_map (setup_file_no, mounting_line, status, fetched_at) "
                        "VALUES ('1', 'L', 'code_not_in_master', 'x')")


@pytest.mark.parametrize("value, expected", [(5, 5), (5.0, 5), (1, 1), (0, None), (-2, None), (2.5, None), (None, None), ("abc", None)])
def test_view_teitori_is_integer_ge_1_only_when_ok(master, value, expected):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    service.refresh_normal([path], folder=master)
    with get_master_connection() as con:
        con.execute("UPDATE parts_attributes SET teitori = ? WHERE part_no = '96000001'", (value,))
        con.commit()
    row = view_by_pair()[("0161", "L")]
    assert row["teitori"] == expected
    assert row["status"] == ("ok" if expected is not None else "teitori_invalid")


def test_line_in_tsv_is_normalized_before_comparing(tmp_path):
    folder = str(tmp_path / "bom2")
    write_tsv(folder, "161", [("ｌ ", "K", "96000001")])           # 全角小文字＋空白
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    service.refresh_normal([path], folder=folder)
    assert view_by_pair()[("0161", "L")]["part_no"] == "96000001"


# ---------------------------------------------------------------- 差分（更新日時）

def count_reads(monkeypatch):
    calls = []
    original = BOMFileIndex.read_tsv

    def counting(self, file_no):
        calls.append(file_no)
        return original(self, file_no)

    monkeypatch.setattr(BOMFileIndex, "read_tsv", counting)
    return calls


def test_unchanged_mtime_is_not_reread_and_changed_is_(master, monkeypatch):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {}), ("K-2", "L2", "0500", "L", {})])
    calls = count_reads(monkeypatch)
    first = service.refresh_normal([path], folder=master)
    assert first["targets"] == 2 and sorted(set(calls)) == ["161", "500"]

    calls.clear()
    second = service.refresh_normal([path], folder=master)
    assert second["targets"] == 0 and second["written"] == 0 and calls == []

    tsv = os.path.join(master, "161", "161.tsv")
    st = os.stat(tsv)
    os.utime(tsv, (st.st_atime, st.st_mtime + 10))
    third = service.refresh_normal([path], folder=master)
    assert third["targets"] == 1 and calls == ["161"]


def test_non_ok_pairs_are_retried_and_new_pairs_added(master):
    path = make_monthly_db("m1", [("K-1", "L1", "0999", "L", {})])
    assert service.refresh_normal([path], folder=master)["by_status"]["tsv_not_found"] == 1
    write_tsv(master, "999", [("L", "K", "96000001")])
    again = service.refresh_normal([path], folder=master)
    assert again["targets"] == 1 and again["by_status"]["ok"] == 1
    assert view_by_pair()[("0999", "L")]["status"] == "ok"

    make_monthly_db("m2", [("K-9", "L9", "0161", "L", {})])
    added = service.refresh_normal([path, os.path.join(config.APP_DATA_DIR, "db", "m2", "inventory.db")], folder=master)
    assert added["targets"] == 1 and ("0161", "L") in view_by_pair()


def test_changing_parts_attributes_changes_view_without_reading_tsv(master, monkeypatch):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    service.refresh_normal([path], folder=master)
    assert view_by_pair()[("0161", "L")]["teitori"] == 5
    calls = count_reads(monkeypatch)
    upsert_parts_attributes("96000001", 7)
    assert view_by_pair()[("0161", "L")]["teitori"] == 7 and calls == []


def test_rows_are_never_deleted(master):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {}), ("K-2", "L2", "0500", "L", {})])
    service.refresh_normal([path], folder=master)
    only_one = make_monthly_db("m2", [("K-3", "L3", "0161", "L", {})])
    service.refresh_normal([only_one], folder=master)
    assert len(view_by_pair()) == 2


def test_missing_bom_folder_writes_nothing(tmp_path):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    with pytest.raises(service.BomFolderUnavailableError):
        service.refresh_normal([path], folder=str(tmp_path / "nowhere"))
    assert view_by_pair() == {}


# ---------------------------------------------------------------- 組の集め方

def test_pairs_are_normalized_and_deduplicated_and_filtered(master):
    path = make_monthly_db("m1", [
        ("K-1", "L1", "0161", "L", {}),
        ("K-2", "L2", " ０１６１ ", "ｌ", {}),          # 全角・空白・小文字
        ("K-3", "L3", "161", "L ", {}),                 # ゼロ埋め無し
        ("K-4", "L4", "0200", "L", {"delete_flag": 1}),  # delete_flag=1 は対象外
        ("K-5", "L5", "0300", "L", {}),
        ("K-6", "L6", "", "L", {}),                      # 空欄の組は対象外
    ])
    # 取り込み直しで古い版になった行は対象外
    config_old = config.DB_PATH
    config.DB_PATH = path
    try:
        create_plan_version(create_plan_batch("t2.csv", "T", 1), "K-5", {"lot_no": "L5", "setup_file_no": "0301", "mounting_line": "L"})
    finally:
        config.DB_PATH = config_old
    con = open_monthly_db_readonly(path)
    try:
        assert count_active_plan_rows_by_pair(con) == {("0161", "L"): 3, ("0301", "L"): 1}
    finally:
        con.close()
    summary = service.refresh_normal([path], folder=master)
    assert summary["pairs"] == 2
    assert set(view_by_pair()) == {("0161", "L"), ("0301", "L")}


def test_old_monthly_db_without_plan_table_gives_no_pairs_read_only(tmp_path):
    old_db = str(tmp_path / "old.db")
    with sqlite3.connect(old_db) as con:
        con.execute("CREATE TABLE something (x TEXT)")
    before = open(old_db, "rb").read()
    con = open_monthly_db_readonly(old_db)
    try:
        assert has_plan_items_table(con) is False
        assert count_active_plan_rows_by_pair(con) == {}
        with pytest.raises(sqlite3.OperationalError):
            con.execute("CREATE TABLE y (a)")        # 書き込めない
    finally:
        con.close()
    assert open(old_db, "rb").read() == before and not os.path.exists(old_db + "-journal")


def test_plan_table_without_delete_flag_uses_is_active_only(tmp_path):
    old_db = str(tmp_path / "v1.db")
    with sqlite3.connect(old_db) as con:
        con.execute("CREATE TABLE kitting_plan_items (setup_file_no TEXT, mounting_line TEXT, is_active INTEGER)")
        con.executemany("INSERT INTO kitting_plan_items VALUES (?, ?, ?)", [("161", "L", 1), ("161", "L", None), ("200", "L", 0)])
    con = open_monthly_db_readonly(old_db)
    try:
        assert count_active_plan_rows_by_pair(con) == {("0161", "L"): 2}
    finally:
        con.close()


def test_refresh_all_monthly_skips_unreadable_and_planless_dbs(master):
    make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    make_monthly_db("m2", [("K-2", "L2", "0500", "L", {})])
    bad = os.path.join(config.APP_DATA_DIR, "db", "bad")
    os.makedirs(bad)
    with open(os.path.join(bad, "inventory.db"), "wb") as f:
        f.write(b"not a database" * 100)
    planless = os.path.join(config.APP_DATA_DIR, "db", "planless")
    os.makedirs(planless)
    with sqlite3.connect(os.path.join(planless, "inventory.db")) as con:
        con.execute("CREATE TABLE something (x TEXT)")
    # 既定DB（db/inventory.db）は対象外
    init_database_at(os.path.join(config.APP_DATA_DIR, "db", "inventory.db"))

    summary = service.refresh_all_monthly(folder=master)
    assert summary["dbs"] == 4 and len(summary["skipped_dbs"]) == 2 and summary["pairs"] == 2
    assert summary["rows_by_status"]["ok"] == 2
    assert summary["view_by_status"]["ok"] == 1 and summary["view_by_status"]["code_not_in_master"] == 1


def test_refresh_all_monthly_rereads_everything(master, monkeypatch):
    make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    service.refresh_all_monthly(folder=master)
    calls = count_reads(monkeypatch)
    again = service.refresh_all_monthly(folder=master)
    assert again["targets"] == 1 and calls == ["161"]


def test_default_db_path_is_skipped(master):
    default_path = os.path.join(config.APP_DATA_DIR, "db", "inventory.db")
    init_database_at(default_path)
    summary = service.refresh_normal([default_path], folder=master)
    assert summary["dbs"] == 0 and teitori_map.list_view_rows() == []


# ---------------------------------------------------------------- 背景の取得

def test_default_db_does_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(runner_module, "_runner", runner_module.BoardTeitoriRunner(work=lambda paths: calls.append(paths)))
    assert config.is_default_db()
    assert runner_module.request_board_teitori_refresh() is False
    runner_module._runner.wait_idle(1)
    assert calls == [] and runner_module._runner.runs == 0


def test_request_captures_db_path_at_request_time(monkeypatch, tmp_path):
    seen = []
    release = threading.Event()

    def work(paths):
        seen.append(list(paths))
        release.wait(5)

    monkeypatch.setattr(runner_module, "_runner", runner_module.BoardTeitoriRunner(work=work))
    first = str(tmp_path / "db" / "a" / "inventory.db")
    second = str(tmp_path / "db" / "b" / "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", first)
    assert runner_module.request_board_teitori_refresh() is True
    monkeypatch.setattr(config, "DB_PATH", second)         # 走っている間に DB が切り替わる
    runner_module.request_board_teitori_refresh()
    release.set()
    assert runner_module._runner.wait_idle(5)
    assert seen == [[first], [second]]


def test_overlapping_requests_never_run_concurrently_and_coalesce(monkeypatch, tmp_path):
    state = {"current": 0, "max": 0}
    lock = threading.Lock()

    def work(paths):
        with lock:
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
        time.sleep(0.2)
        with lock:
            state["current"] -= 1

    runner = runner_module.BoardTeitoriRunner(work=work)
    monkeypatch.setattr(runner_module, "_runner", runner)
    path = str(tmp_path / "db" / "a" / "inventory.db")
    monkeypatch.setattr(config, "DB_PATH", path)
    for _ in range(6):
        runner_module.request_board_teitori_refresh()
        time.sleep(0.02)
    assert runner.wait_idle(5)
    assert state["max"] == 1
    assert runner.runs == 2          # 最初の1回と、溜まった依頼をまとめた後の1回


def test_failure_is_logged_not_raised_and_next_request_runs(monkeypatch, tmp_path, caplog):
    def work(paths):
        raise RuntimeError("boom")

    runner = runner_module.BoardTeitoriRunner(work=work)
    monkeypatch.setattr(runner_module, "_runner", runner)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "db" / "a" / "inventory.db"))
    runner_module.request_board_teitori_refresh()
    assert runner.wait_idle(5)
    assert "基板丁数の対応表の更新に失敗しました" in caplog.text
    runner_module.request_board_teitori_refresh()
    assert runner.wait_idle(5) and runner.runs == 2


def test_manual_refresh_and_background_share_one_lock(master, monkeypatch):
    make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    running = {"now": 0, "max": 0}
    original = service._refresh

    def tracking(*args, **kwargs):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        time.sleep(0.15)
        try:
            return original(*args, **kwargs)
        finally:
            running["now"] -= 1

    monkeypatch.setattr(service, "_refresh", tracking)
    path = os.path.join(config.APP_DATA_DIR, "db", "m1", "inventory.db")
    threads = [threading.Thread(target=service.refresh_normal, args=([path], master)),
               threading.Thread(target=service.refresh_all_monthly, args=(master,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert running["max"] == 1


# ---------------------------------------------------------------- master.db への書き込み

def test_write_succeeds_while_another_connection_reads_and_journal_stays_delete(master):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    service.refresh_normal([path], folder=master)

    reader = sqlite3.connect(f"file:{config.MASTER_DB_PATH}?mode=ro", uri=True, timeout=5)
    reader.execute("PRAGMA query_only = ON")
    assert reader.execute("SELECT COUNT(*) FROM v_board_teitori").fetchone()[0] == 1   # 読み終えた接続が開いたまま

    held = threading.Event()

    def hold_read_briefly():
        con = sqlite3.connect(f"file:{config.MASTER_DB_PATH}?mode=ro", uri=True, timeout=5)
        con.execute("BEGIN")
        con.execute("SELECT * FROM board_teitori_map").fetchall()
        held.set()
        time.sleep(0.4)
        con.rollback()
        con.close()

    thread = threading.Thread(target=hold_read_briefly)
    thread.start()
    assert held.wait(5)
    teitori_map.upsert_map_rows([{
        "setup_file_no": "0161", "mounting_line": "L", "part_no": "96000001", "status": "ok",
        "resolved_file_no": "161", "tsv_mtime": 1.0, "fetched_at": "2026-10-30 10:00:00",
    }])
    thread.join()
    assert reader.execute("SELECT tsv_mtime FROM board_teitori_map").fetchone()[0] == 1.0
    reader.close()
    with get_master_connection() as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_writes_are_chunked_to_50_pairs_or_fewer(master, monkeypatch):
    plans = [(f"K-{i}", f"L{i}", f"{1000 + i}", "L", {}) for i in range(120)]
    path = make_monthly_db("m1", plans)
    sizes = []
    original = service.upsert_map_rows
    monkeypatch.setattr(service, "upsert_map_rows", lambda rows: (sizes.append(len(rows)), original(rows)))
    summary = service.refresh_normal([path], folder=master)
    assert summary["written"] == 120 and sizes == [50, 50, 20]


# ---------------------------------------------------------------- ビューの作り直し

def test_view_is_recreated_only_when_definition_changes(master, monkeypatch):
    teitori_map.init_board_teitori_tables()

    def schema_version():
        with get_master_connection() as con:
            return con.execute("PRAGMA schema_version").fetchone()[0]

    before = schema_version()
    teitori_map.init_board_teitori_tables()
    assert schema_version() == before                         # 同じ定義なら作り直さない

    monkeypatch.setattr(teitori_map, "_CREATE_VIEW_SQL", teitori_map._CREATE_VIEW_SQL.replace("m.fetched_at AS fetched_at", "m.fetched_at AS fetched_at, m.status AS raw_status"))
    teitori_map.init_board_teitori_tables()
    assert schema_version() != before
    with get_master_connection() as con:
        columns = [r[1] for r in con.execute("PRAGMA table_info(v_board_teitori)")]
    assert columns[-1] == "raw_status"
    after = schema_version()
    teitori_map.init_board_teitori_tables()
    assert schema_version() == after


def test_master_merge_does_not_include_the_new_tables():
    from services import master_merge_service
    source = open(master_merge_service.__file__, encoding="utf-8").read()
    assert "board_teitori" not in source


# ---------------------------------------------------------------- 共有フォルダを読めないときのエラー（D-191 の追記）

import types

from ui import main_window


class _FakeMessageBox:
    def __init__(self):
        self.calls = []

    def showerror(self, title, message, **kwargs):
        self.calls.append(("error", title, message))

    def showinfo(self, title, message, **kwargs):
        self.calls.append(("info", title, message))


@pytest.fixture
def boxes(monkeypatch):
    fake = _FakeMessageBox()
    monkeypatch.setattr(main_window, "messagebox", fake)
    monkeypatch.setattr(main_window, "log_operation", lambda *a, **k: None)
    return fake


def _expected_message(folder):
    return ("基板丁数の対応表を更新できませんでした。"
            f"部品表の共有フォルダ（{folder}）を読めません。"
            "ネットワークを確かめてから、メインメニューの『基板丁数の対応表を再取得』を押してください。")


def test_unreadable_folder_raises_with_folder_and_writes_nothing(tmp_path):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    missing = str(tmp_path / "nowhere")
    with pytest.raises(service.BomFolderUnavailableError) as info:
        service.refresh_normal([path], folder=missing)
    assert info.value.folder == missing
    assert service.folder_unavailable_message(missing) == _expected_message(missing)
    assert teitori_map.list_view_rows() == []
    empty = tmp_path / "empty"
    empty.mkdir()                                              # フォルダはあるが TSV が1つも見えない
    with pytest.raises(service.BomFolderUnavailableError):
        service.refresh_all_monthly(folder=str(empty))
    assert teitori_map.list_view_rows() == []


def test_background_refresh_with_unreadable_folder_queues_the_error_and_writes_nothing(monkeypatch, tmp_path):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    missing = str(tmp_path / "nowhere")
    monkeypatch.setattr(config, "BOM_FOLDER_PATH", missing)
    monkeypatch.setattr(config, "DB_PATH", path)                 # 既定DB ではない月次DB
    runner = runner_module.BoardTeitoriRunner()
    monkeypatch.setattr(runner_module, "_runner", runner)
    assert runner_module.request_board_teitori_refresh() is True
    assert runner.wait_idle(10)
    assert runner_module.pop_board_teitori_errors() == [_expected_message(missing)]
    assert runner_module.pop_board_teitori_errors() == []        # 取り出した分は消える
    assert teitori_map.list_view_rows() == []


def test_background_refresh_with_readable_folder_queues_no_error(monkeypatch, master):
    path = make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    monkeypatch.setattr(config, "BOM_FOLDER_PATH", master)
    monkeypatch.setattr(config, "DB_PATH", path)
    runner = runner_module.BoardTeitoriRunner()
    monkeypatch.setattr(runner_module, "_runner", runner)
    runner_module.request_board_teitori_refresh()
    assert runner.wait_idle(10)
    assert runner_module.pop_board_teitori_errors() == []
    assert len(teitori_map.list_view_rows()) == 1


def test_background_unexpected_failure_is_log_only(monkeypatch, tmp_path):
    runner = runner_module.BoardTeitoriRunner(work=lambda paths: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(runner_module, "_runner", runner)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "db" / "a" / "inventory.db"))
    runner_module.request_board_teitori_refresh()
    assert runner.wait_idle(5)
    assert runner_module.pop_board_teitori_errors() == []


def test_repeated_folder_errors_are_shown_once(monkeypatch, tmp_path):
    runner = runner_module.BoardTeitoriRunner(
        work=lambda paths: (_ for _ in ()).throw(service.BomFolderUnavailableError("X:/bom")))
    monkeypatch.setattr(runner_module, "_runner", runner)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "db" / "a" / "inventory.db"))
    for _ in range(3):
        runner_module.request_board_teitori_refresh()
        assert runner.wait_idle(5)
    assert runner_module.pop_board_teitori_errors() == [_expected_message("X:/bom")]


def test_main_window_poll_shows_the_queued_error_on_the_main_thread(monkeypatch, boxes):
    runner = runner_module.BoardTeitoriRunner()
    monkeypatch.setattr(runner_module, "_runner", runner)
    runner._add_error(_expected_message("X:/bom"))
    scheduled = []
    fake = types.SimpleNamespace(after=lambda ms, fn: scheduled.append((ms, fn)), winfo_toplevel=lambda: "top")
    fake._poll_board_teitori_errors = types.MethodType(main_window.MainWindow._poll_board_teitori_errors, fake)
    main_window.MainWindow._poll_board_teitori_errors(fake)
    assert boxes.calls == [("error", "基板丁数の対応表", _expected_message("X:/bom"))]
    assert scheduled and scheduled[0][0] == main_window.BOARD_TEITORI_ERROR_POLL_MS       # また確かめる
    main_window.MainWindow._poll_board_teitori_errors(fake)
    assert len(boxes.calls) == 1                                                            # 同じ文を重ねて出さない


def _manual(monkeypatch):
    """on_refresh_board_teitori_map() を、別スレッドを使わず同期で動かす。"""
    def sync(parent, message, work_fn, on_success, on_error):
        try:
            result = work_fn()
        except Exception as e:
            on_error(e, "tb")
        else:
            on_success(result)
    monkeypatch.setattr(main_window, "run_in_background", sync)
    return types.SimpleNamespace(current_worker={"name": "t"}, winfo_toplevel=lambda: "top")


def test_manual_refresh_with_unreadable_folder_shows_the_error_and_writes_nothing(monkeypatch, boxes, tmp_path):
    make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    missing = str(tmp_path / "nowhere")
    monkeypatch.setattr(config, "BOM_FOLDER_PATH", missing)
    main_window.MainWindow.on_refresh_board_teitori_map(_manual(monkeypatch))
    assert boxes.calls == [("error", "エラー", _expected_message(missing))]
    assert teitori_map.list_view_rows() == []


def test_manual_refresh_with_readable_folder_shows_no_error(monkeypatch, boxes, master):
    make_monthly_db("m1", [("K-1", "L1", "0161", "L", {})])
    monkeypatch.setattr(config, "BOM_FOLDER_PATH", master)
    main_window.MainWindow.on_refresh_board_teitori_map(_manual(monkeypatch))
    assert [c[0] for c in boxes.calls] == ["info"]


def test_manual_refresh_unexpected_failure_still_shows_a_dialog(monkeypatch, boxes):
    monkeypatch.setattr(main_window, "refresh_all_monthly", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    main_window.MainWindow.on_refresh_board_teitori_map(_manual(monkeypatch))
    assert boxes.calls[0][0] == "error" and "再取得に失敗しました" in boxes.calls[0][2] and "boom" in boxes.calls[0][2]
