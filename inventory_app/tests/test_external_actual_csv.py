"""現場実績CSV（書式版 1）の解析と、DB を見ない検査（services/external_actual_csv.py）。"""
import pytest

from services.external_actual_csv import COLUMNS, parse_external_actual_csv, parse_exported_at

from tests.external_actual_helpers import EXPORTED, FOLDER, line, write_csv


def parse(tmp_path, *lines, **kwargs):
    return parse_external_actual_csv(write_csv(tmp_path, *lines, **kwargs), FOLDER)


def only_messages(result):
    return [m for e in result["errors"] for m in e["messages"]]


def test_valid_rows_are_parsed(tmp_path):
    result = parse(tmp_path, line(), line(kitting="0002-1-A-261001-01", lot="100002", file_no="0002", side="1", ng1="3", ng2=""))
    assert result["fatal"] is None and result["errors"] == [] and result["total_rows"] == 2
    first, second = result["rows"]
    assert (first["csv_row_no"], first["side"], first["daily_qty"], first["exported_at"]) == (2, "2", 100, EXPORTED)
    assert (second["csv_row_no"], second["ng_side1"], second["ng_side2"]) == (3, 3, 0)         # 空は0


def test_header_problems_reject_the_whole_file(tmp_path):
    assert len(COLUMNS) == 13 and COLUMNS[-1] == "面1のキッティングNo."
    bad = ",".join(COLUMNS[:-1])                                                              # 面1のキッティングNo. が無い（書式版1の12列。読む処理は無い）
    result = parse(tmp_path, line(version="1")[:-1], header=bad)
    assert result["fatal"].startswith("ヘッダーが現場実績CSVの形ではありません。足りない列：面1のキッティングNo.") and result["rows"] == []
    reordered = ",".join(COLUMNS[1:] + COLUMNS[:1])
    assert "列の順番が違います" in parse(tmp_path, line(), header=reordered)["fatal"]


@pytest.mark.parametrize("version", ["1", "3", "10", "", "2.0"])
def test_format_version_other_than_2_rejects_the_whole_file(tmp_path, version):
    result = parse(tmp_path, line(), line(version=version, lot="100002"))
    assert "書式版が 2 ではありません（行 3）" in result["fatal"] and result["rows"] == []


def test_source_db_mismatch_stops_only_that_row(tmp_path):
    result = parse(tmp_path, line(), line(db="2026-09", lot="100002"))
    assert [r["lot_no"] for r in result["rows"]] == ["100001"]
    assert only_messages(result) == ["元のDB「2026-09」が今のDB「2026-10」と違います"]


@pytest.mark.parametrize("kwargs, expected", [
    ({"kitting": "1.2E+5"}, "キッティングNo.「1.2E+5」は指数表記です"),
    ({"lot": "1.2E+5"}, "ロットNo.「1.2E+5」は指数表記です"),
    ({"file_no": "569"}, "ファイルNo.「569」は先頭の0が落ちています"),
    ({"kitting": "569-1-A-261001-01"}, "キッティングNo.「569-1-A-261001-01」は先頭の0が落ちています"),
    ({"side": "3"}, "面が 1 か 2 ではありません（3）"),
    ({"date": "2026/10/20"}, "日付が YYYY-MM-DD の形ではありません（2026/10/20）"),
    ({"date": "2026-02-30"}, "日付が YYYY-MM-DD の形ではありません（2026-02-30）"),
    ({"date": "2026-1-5"}, "日付が YYYY-MM-DD の形ではありません（2026-1-5）"),
    ({"qty": "-1"}, "生産数が負の値です（-1）"),
    ({"qty": ""}, "生産数が空です"),
    ({"qty": "12a"}, "生産数が整数ではありません（12a）"),
    ({"ng1": "-2"}, "NG面1が負の値です（-2）"),
    ({"ng2": "x"}, "NG面2が整数ではありません（x）"),
    ({"worker": ""}, "担当者IDが空です"),
    ({"exported": "昨日"}, "書き出し日時が読めません（昨日）"),
])
def test_row_errors_stop_only_that_row(tmp_path, kwargs, expected):
    result = parse(tmp_path, line(**kwargs), line(lot="100002", kitting="0002-2-A-261001-01", file_no="0002"))
    assert [r["lot_no"] for r in result["rows"]] == ["100002"]
    assert any(expected in m for m in only_messages(result)), only_messages(result)


def test_ids_are_normalized_with_nfkc_and_trimmed_but_worker_id_is_kept_as_is(tmp_path):
    result = parse(tmp_path, line(kitting=" ０００１-２-Ａ-261001-01 ", lot="１００００１", worker=" W1"))
    row = result["rows"][0]
    assert (row["kitting_list_no"], row["lot_no"], row["worker_id"]) == ("0001-2-A-261001-01", "100001", " W1")


def test_side1_row_with_ng_side2_is_refused(tmp_path):
    result = parse(tmp_path, line(side="1", ng2="2"), line(side="1", ng1="2", lot="100002", kitting="0002-1-A-261001-01", file_no="0002"))
    assert [r["lot_no"] for r in result["rows"]] == ["100002"]
    assert "面1の行に NG面2 が入っています" in only_messages(result)[0]


def test_wrong_column_count_stops_the_row(tmp_path):
    result = parse(tmp_path, line() + ",extra", line(lot="100002", kitting="0002-2-A-261001-01", file_no="0002"))
    assert [r["lot_no"] for r in result["rows"]] == ["100002"] and "列の数が 13 ではありません（14 列）" in only_messages(result)[0]


def test_face1_kitting_no_is_read_normalized_and_may_be_empty(tmp_path):
    result = parse(tmp_path, line(face1=" ０００１-１-Ａ-261001-01 "), line(lot="100002", kitting="0002-2-A-261001-01", file_no="0002"))
    assert [r["face1_kitting_list_no"] for r in result["rows"]] == ["0001-1-A-261001-01", ""]


def test_face1_kitting_no_on_a_side1_row_or_damaged_by_excel_stops_the_row(tmp_path):
    result = parse(tmp_path, line(side="1", face1="0001-1-A-261001-01"), line(lot="100002", face1="1.2E+5"),
                   line(lot="100003", face1="569-1-A-261001-01"))
    assert result["rows"] == []
    messages = only_messages(result)
    assert "面1の行に面1のキッティングNo.が入っています" in messages[0]
    assert "面1のキッティングNo.「1.2E+5」は指数表記です" in messages[1]
    assert "面1のキッティングNo.「569-1-A-261001-01」は先頭の0が落ちています" in messages[2]


def test_duplicate_rows_with_different_face1_kitting_no_warn(tmp_path):
    result = parse(tmp_path, line(exported="2026-10-20 10:00:00", face1="K1"), line(exported="2026-10-20 18:00:00", face1="K9"))
    assert result["rows"][0]["face1_kitting_list_no"] == "K9" and len(result["warnings"]) == 1


def test_duplicates_use_newest_export_and_warn_only_when_content_differs(tmp_path):
    older = line(exported="2026-10-20 10:00:00", qty="50")
    newer = line(exported="2026-10-20 18:00:00", qty="80")
    result = parse(tmp_path, newer, older)
    assert [(r["csv_row_no"], r["daily_qty"]) for r in result["rows"]] == [(2, 80)]
    assert len(result["warnings"]) == 1 and "行 2・3" in result["warnings"][0] and "行 2 を使います" in result["warnings"][0]

    same = parse(tmp_path, line(exported="2026-10-20 10:00:00"), line(exported="2026-10-20 18:00:00"), name="y.csv")
    assert [r["csv_row_no"] for r in same["rows"]] == [3] and same["warnings"] == []


def test_blank_lines_are_skipped_and_row_numbers_follow_the_file(tmp_path):
    result = parse(tmp_path, "", line())
    assert result["total_rows"] == 1 and result["rows"][0]["csv_row_no"] == 3


def test_cp932_file_is_read(tmp_path):
    result = parse(tmp_path, line(), encoding="cp932", name="sjis.csv")
    assert result["fatal"] is None and len(result["rows"]) == 1


@pytest.mark.parametrize("text, expected", [
    ("2026-10-20 18:00:00", "2026-10-20 18:00:00"), ("2026-10-20T18:00:00", "2026-10-20 18:00:00"),
    ("2026/10/20 18:00", "2026-10-20 18:00:00"), ("", None), ("x", None),
])
def test_parse_exported_at(text, expected):
    assert parse_exported_at(text) == expected


def test_sha256_is_of_the_file_bytes(tmp_path):
    first = parse(tmp_path, line())
    second = parse(tmp_path, line(), name="copy.csv")
    third = parse(tmp_path, line(qty="1"), name="other.csv")
    assert first["file_sha256"] == second["file_sha256"] != third["file_sha256"]
