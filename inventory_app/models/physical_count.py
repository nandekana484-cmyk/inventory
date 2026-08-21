import sqlite3
from datetime import datetime

from config import DB_PATH
from services.audit_service import write_audit


def get_connection():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _get_columns(con, table_name):
    rows = con.execute(
        f"PRAGMA table_info([{table_name}])"
    ).fetchall()

    return {row["name"] for row in rows}


def init_physical_count_table():
    """正式なphysical_countテーブルを使用する。"""
    with get_connection() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS physical_count (
                count_id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id TEXT NOT NULL,
                code96 TEXT NOT NULL,
                counted_qty REAL NOT NULL,
                counted_at TEXT NOT NULL,
                worker_id TEXT,
                count_date TEXT,
                is_checked INTEGER DEFAULT 1,
                updated_at TEXT
            )
            """
        )

        columns = _get_columns(con, "physical_count")

        if "count_date" not in columns:
            con.execute(
                "ALTER TABLE physical_count ADD COLUMN count_date TEXT"
            )

        if "is_checked" not in columns:
            con.execute(
                """
                ALTER TABLE physical_count
                ADD COLUMN is_checked INTEGER DEFAULT 1
                """
            )

        if "updated_at" not in columns:
            con.execute(
                "ALTER TABLE physical_count ADD COLUMN updated_at TEXT"
            )

        con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            ux_physical_count_date_part
            ON physical_count(count_date, part_id)
            """
        )

        con.commit()


def save_physical_count(
    count_date: str,
    part_id: str,
    code96: str,
    qty: float,
    is_checked: int,
    worker_id: str,
):
    """
    棚卸数を登録または更新する。
    code96は部品マスタの値を優先する。
    """
    init_physical_count_table()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as con:
        master = con.execute(
            """
            SELECT code96
            FROM parts
            WHERE part_id = ?
            """,
            (part_id,),
        ).fetchone()

        if master and master["code96"]:
            code96 = master["code96"]

        if not code96:
            raise ValueError(
                f"部品ID {part_id} の96コードが不明です。"
            )

        existing = con.execute(
            """
            SELECT count_id, counted_qty, is_checked
            FROM physical_count
            WHERE count_date = ? AND part_id = ?
            """,
            (count_date, part_id),
        ).fetchone()

        if existing:
            con.execute(
                """
                UPDATE physical_count
                SET
                    code96 = ?,
                    counted_qty = ?,
                    is_checked = ?,
                    worker_id = ?,
                    counted_at = ?,
                    updated_at = ?
                WHERE count_id = ?
                """,
                (
                    code96,
                    qty,
                    is_checked,
                    worker_id,
                    now,
                    now,
                    existing["count_id"],
                ),
            )

            write_audit(
                con,
                action="PHYSICAL_COUNT_UPDATE",
                detail=f"棚卸数量更新: part_id={part_id}",
                worker_id=worker_id,
                table_name="physical_count",
                record_pk=existing["count_id"],
                operation_type="UPDATE",
                field_name="counted_qty",
                old_value=existing["counted_qty"],
                new_value=qty,
            )

        else:
            cur = con.execute(
                """
                INSERT INTO physical_count (
                    part_id,
                    code96,
                    counted_qty,
                    counted_at,
                    worker_id,
                    count_date,
                    is_checked,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    part_id,
                    code96,
                    qty,
                    now,
                    worker_id,
                    count_date,
                    is_checked,
                    now,
                ),
            )

            write_audit(
                con,
                action="PHYSICAL_COUNT_INSERT",
                detail=f"棚卸数量登録: part_id={part_id}",
                worker_id=worker_id,
                table_name="physical_count",
                record_pk=cur.lastrowid,
                operation_type="INSERT",
                field_name="counted_qty",
                new_value=qty,
            )

        con.commit()


def get_all_parts_for_count():
    """有効な部品一覧を取得する。"""
    with get_connection() as con:
        rows = con.execute(
            """
            SELECT
                part_id,
                code96,
                shelf_type,
                shape_category,
                is_high_value
            FROM parts
            WHERE is_active = 1
            ORDER BY shelf_type, code96, part_id
            """
        ).fetchall()

        return [dict(row) for row in rows]


def get_counts_by_date(count_date: str):
    """指定日の棚卸結果を取得する。"""
    init_physical_count_table()

    with get_connection() as con:
        rows = con.execute(
            """
            SELECT
                part_id,
                code96,
                counted_qty,
                is_checked,
                worker_id,
                updated_at
            FROM physical_count
            WHERE count_date = ?
            """,
            (count_date,),
        ).fetchall()

        return {
            row["part_id"]: dict(row)
            for row in rows
        }