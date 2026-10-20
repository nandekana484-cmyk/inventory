"""
構成基板数マスターの CSV 書き出し（D-182）。書き出したファイルをそのまま取り込むと差分が 0 になること。
"""
from models.board_structure_master import upsert_board_structure, list_board_structure, apply_board_structure_sync
from services.board_structure_csv_service import (
    build_board_structure_csv_bytes, export_board_structure_csv, parse_board_structure_csv,
)


def _seed():
    upsert_board_structure("BRD-A", 2)
    upsert_board_structure("BRD,COMMA", 1)
    upsert_board_structure("ｼｮｳﾒｲTOP", 21)
    upsert_board_structure("HALF", 1.5)
    upsert_board_structure("NOVALUE", None)


def _counts(plan):
    return tuple(len(plan[k]) for k in ("to_add", "to_update", "unchanged", "to_delete"))


def test_format_matches_source_csv():
    _seed()
    data = build_board_structure_csv_bytes(list_board_structure())
    text = data.decode("cp932")
    lines = text.split("\r\n")
    assert lines[0] == "基板名,構成数"
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "BRD-A,2" in lines and '"BRD,COMMA",1' in lines and "ｼｮｳﾒｲTOP,21" in lines
    assert "HALF,1.5" in lines and "NOVALUE," in lines


def test_export_then_import_has_no_changes(tmp_path):
    _seed()
    path = tmp_path / "out.csv"
    assert export_board_structure_csv(str(path), list_board_structure()) == 5
    result = parse_board_structure_csv(str(path))
    assert result["abort_reason"] is None
    assert _counts(result["plan"]) == (0, 0, 5, 0)


def test_one_edited_row_changes_only_that_row(tmp_path):
    _seed()
    path = tmp_path / "out.csv"
    export_board_structure_csv(str(path), list_board_structure())
    path.write_bytes(path.read_bytes().replace("BRD-A,2".encode("cp932"), "BRD-A,3".encode("cp932")))
    plan = parse_board_structure_csv(str(path))["plan"]
    assert _counts(plan) == (0, 1, 4, 0)
    assert plan["to_update"] == [{"board_name": "BRD-A", "old_value": 2, "new_value": 3.0}]
    apply_board_structure_sync(parse_board_structure_csv(str(path))["resolved_rows"])
    assert {r["board_name"]: r["board_count"] for r in list_board_structure()}["BRD-A"] == 3


def test_unencodable_name_is_refused_without_creating_file(tmp_path):
    path = tmp_path / "out.csv"
    try:
        export_board_structure_csv(str(path), [{"board_name": "BRD-①\U0001F600", "board_count": 1}])
    except ValueError as e:
        assert "書き出せません" in str(e)
    else:
        raise AssertionError("ValueError が出なかった")
    assert not path.exists()


def test_window_export_button(tk_root, tmp_path, monkeypatch):
    import ui.master.board_structure_import_window as w
    _seed()
    path = tmp_path / "out.csv"
    shown = []
    monkeypatch.setattr(w.filedialog, "asksaveasfilename", lambda **kw: str(path))
    monkeypatch.setattr(w.messagebox, "showinfo", lambda *a, **kw: shown.append(a))
    win = w.BoardStructureImportWindow(tk_root, {"name": "tester"})
    try:
        win.on_export_csv()
    finally:
        win.destroy()
    assert path.exists() and "（5件）" in shown[0][1]
