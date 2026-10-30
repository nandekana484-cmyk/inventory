# models/board_teitori_map.py
"""
計画ごとの丁取り数を引ける対応表（master.db。現場アプリが読む。D-191、docs/domain/board_teitori.md）。

表 board_teitori_map は、(ファイルNo., 実装ライン) ごとに「BOM TSV の K行の96コード」と、その取得結果（status）を持つ。
丁取り数そのものは持たず、ビュー v_board_teitori が parts_attributes（基板丁数マスター）と結び付けて返す
（マスターを変えると、TSV を読み直さなくてもビューの結果が変わる）。
マイグレーションは無く、ほかのマスタ表と同じく使うときに作る。ジャーナルモードは変えない（delete のまま。WAL にしない）。
"""
import re

from models.db_common import get_master_connection
from models.parts_attributes import init_parts_attributes_table

TABLE_NAME = "board_teitori_map"
VIEW_NAME = "v_board_teitori"

# 表の status（TSV から決まる値だけ）。
STATUS_OK = "ok"
STATUS_TSV_NOT_FOUND = "tsv_not_found"
STATUS_AMBIGUOUS_TSV = "ambiguous_tsv"
STATUS_READ_ERROR = "read_error"
STATUS_NO_K_ROW = "no_k_row"
STATUS_MULTI_K = "multi_k"
MAP_STATUSES = (
    STATUS_OK, STATUS_TSV_NOT_FOUND, STATUS_AMBIGUOUS_TSV, STATUS_READ_ERROR, STATUS_NO_K_ROW, STATUS_MULTI_K,
)
# ビューの status は、上に加えてマスター側の状態の2つ。
STATUS_CODE_NOT_IN_MASTER = "code_not_in_master"
STATUS_TEITORI_INVALID = "teitori_invalid"
VIEW_STATUSES = MAP_STATUSES + (STATUS_CODE_NOT_IN_MASTER, STATUS_TEITORI_INVALID)

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    setup_file_no   TEXT NOT NULL,
    mounting_line   TEXT NOT NULL,
    part_no         TEXT,
    status          TEXT NOT NULL CHECK (status IN ({", ".join(repr(s) for s in MAP_STATUSES)})),
    resolved_file_no TEXT,
    tsv_mtime       REAL,
    fetched_at      TEXT NOT NULL,
    PRIMARY KEY (setup_file_no, mounting_line)
)
"""

# teitori は status が ok のときだけ、1以上の整数。数値でない値・小数・0以下は teitori_invalid。
_CREATE_VIEW_SQL = f"""
CREATE VIEW {VIEW_NAME} AS
SELECT
    m.setup_file_no AS setup_file_no,
    m.mounting_line AS mounting_line,
    m.part_no AS part_no,
    CASE WHEN m.status = 'ok'
              AND typeof(p.teitori) IN ('integer', 'real')
              AND p.teitori >= 1 AND p.teitori = CAST(p.teitori AS INTEGER)
         THEN CAST(p.teitori AS INTEGER) END AS teitori,
    CASE WHEN m.status <> 'ok' THEN m.status
         WHEN p.part_no IS NULL THEN 'code_not_in_master'
         WHEN typeof(p.teitori) IN ('integer', 'real')
              AND p.teitori >= 1 AND p.teitori = CAST(p.teitori AS INTEGER) THEN 'ok'
         ELSE 'teitori_invalid' END AS status,
    m.fetched_at AS fetched_at
FROM {TABLE_NAME} m
LEFT JOIN parts_attributes p ON p.part_no = m.part_no
"""


def _squash(sql: str) -> str:
    return re.sub(r"\s+", " ", sql or "").strip()


def init_board_teitori_tables():
    """
    表とビューを作る（既存があれば何もしない）。ビューは今の定義と比べ、違うときだけ作り直す。
    ビューは parts_attributes を参照するので、先にその表を作る。
    """
    init_parts_attributes_table()
    with get_master_connection() as con:
        con.execute(_CREATE_TABLE_SQL)
        row = con.execute("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?", (VIEW_NAME,)).fetchone()
        if row is None or _squash(row[0]) != _squash(_CREATE_VIEW_SQL):
            con.execute("BEGIN")
            con.execute(f"DROP VIEW IF EXISTS {VIEW_NAME}")
            con.execute(_CREATE_VIEW_SQL)
            con.execute("COMMIT")
        con.commit()


def load_map_state() -> dict:
    """{(setup_file_no, mounting_line): {"status", "resolved_file_no", "tsv_mtime"}}。表の全行。"""
    init_board_teitori_tables()
    with get_master_connection() as con:
        rows = con.execute(
            f"SELECT setup_file_no, mounting_line, status, resolved_file_no, tsv_mtime FROM {TABLE_NAME}"
        ).fetchall()
    return {
        (r["setup_file_no"], r["mounting_line"]): {
            "status": r["status"], "resolved_file_no": r["resolved_file_no"], "tsv_mtime": r["tsv_mtime"],
        }
        for r in rows
    }


def upsert_map_rows(rows: list):
    """
    rows（dict のリスト。setup_file_no, mounting_line, part_no, status, resolved_file_no, tsv_mtime, fetched_at）を
    1つのトランザクションで書き込む。行は消さない。呼び出し元が 50 組以下ずつに分けて呼ぶこと（書き込みを短くするため）。
    """
    if not rows:
        return
    init_board_teitori_tables()
    with get_master_connection() as con:
        con.executemany(
            f"""
            INSERT INTO {TABLE_NAME}
                (setup_file_no, mounting_line, part_no, status, resolved_file_no, tsv_mtime, fetched_at)
            VALUES (:setup_file_no, :mounting_line, :part_no, :status, :resolved_file_no, :tsv_mtime, :fetched_at)
            ON CONFLICT(setup_file_no, mounting_line) DO UPDATE SET
                part_no = excluded.part_no,
                status = excluded.status,
                resolved_file_no = excluded.resolved_file_no,
                tsv_mtime = excluded.tsv_mtime,
                fetched_at = excluded.fetched_at
            """,
            rows,
        )
        con.commit()


def list_view_rows() -> list:
    """ビュー v_board_teitori の全行（dict のリスト）。確認・テスト用。"""
    init_board_teitori_tables()
    with get_master_connection() as con:
        cur = con.execute(
            f"SELECT setup_file_no, mounting_line, part_no, teitori, status, fetched_at FROM {VIEW_NAME} "
            "ORDER BY setup_file_no, mounting_line"
        )
        return [dict(r) for r in cur.fetchall()]
