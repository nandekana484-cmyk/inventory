"""現場実績CSVのテストで使う、CSV を作る道具。"""
from services.external_actual_csv import COLUMNS

FOLDER = "2026-10"
EXPORTED = "2026-10-20 18:00:00"


def line(version="1", exported=EXPORTED, db=FOLDER, kitting="0001-2-A-261001-01", lot="100001", file_no="0001",
         side="2", date="2026-10-20", qty="100", ng1="0", ng2="0", worker="W1"):
    return ",".join([version, exported, db, kitting, lot, file_no, side, date, qty, ng1, ng2, worker])


def write_csv(tmp_path, *lines, header=None, name="x.csv", encoding="utf-8-sig"):
    text = (header or ",".join(COLUMNS)) + "\r\n" + "".join(l + "\r\n" for l in lines)
    path = tmp_path / name
    path.write_text(text, encoding=encoding, newline="")
    return str(path)
