import os
import sqlite3

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


def init_snapshot_table():
    """
    正式なスナップショット関連テーブルを準備する。

    通常はschema.sqlまたはmigrationで作成するが、
    開発中のDB互換性のため不足列だけ補完する。
    """
    with get_connection() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshot_batches (
                batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date TEXT NOT NULL,
                source_file TEXT,
                imported_by TEXT,
                imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
                row_count INTEGER DEFAULT 0
            )
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_snapshot (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_id TEXT NOT NULL,
                code96 TEXT NOT NULL,
                qty REAL NOT NULL,
                snapshot_date TEXT NOT NULL,
                source TEXT,
                batch_id INTEGER
            )
            """
        )

        columns = _get_columns(con, "stock_snapshot")

        if "batch_id" not in columns:
            con.execute(
                "ALTER TABLE stock_snapshot ADD COLUMN batch_id INTEGER"
            )

        con.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_stock_snapshot_batch
            ON stock_snapshot(batch_id)
            """
        )

        con.commit()


def insert_snapshot_items(
    snapshot_date: str,
    items: list,
    source_file: str = None,
    imported_by: str = None,
) -> int:
    """
    1回のCSV取込を1バッチとして保存する。

    itemsの形式：
    [
        {
            "part_id": "...",
            "code96": "...",
            "snapshot_qty": 100
        }
    ]
    """
    init_snapshot_table()

    source_name = os.path.basename(source_file) if source_file else None

    with get_connection() as con:
        cur = con.cursor()

        cur.execute(
            """
            INSERT INTO snapshot_batches (
                snapshot_date,
                source_file,
                imported_by,
                row_count
            )
            VALUES (?, ?, ?, 0)
            """,
            (
                snapshot_date,
                source_name,
                imported_by,
            ),
        )

        batch_id = cur.lastrowid
        inserted_count = 0

        for item in items:
            cur.execute(
                """
                INSERT INTO stock_snapshot (
                    batch_id,
                    part_id,
                    code96,
                    qty,
                    snapshot_date,
                    source
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    item.get("part_id"),
                    item.get("code96"),
                    item.get("snapshot_qty", 0),
                    snapshot_date,
                    "csv",
                ),
            )
            inserted_count += 1

        cur.execute(
            """
            UPDATE snapshot_batches
            SET row_count = ?
            WHERE batch_id = ?
            """,
            (inserted_count, batch_id),
        )

        write_audit(
            con,
            action="SNAPSHOT_IMPORT",
            detail=f"在庫スナップショット取込: {inserted_count}件",
            worker_id=imported_by,
            table_name="snapshot_batches",
            record_pk=batch_id,
            operation_type="INSERT",
            new_value=f"row_count={inserted_count}",
        )

        con.commit()
        return inserted_count


def get_snapshot_history():
    """スナップショット取込履歴を取得する。"""
    init_snapshot_table()

    with get_connection() as con:
        rows = con.execute(
            """
            SELECT
                batch_id,
                snapshot_date,
                source_file,
                imported_by,
                imported_at,
                row_count
            FROM snapshot_batches
            ORDER BY batch_id DESC
            """
        ).fetchall()

        return [dict(row) for row in rows]


def get_latest_snapshot_batch():
    """後から取り込まれた最新バッチを取得する。"""
    init_snapshot_table()

    with get_connection() as con:
        row = con.execute(
            """
            SELECT
                batch_id,
                snapshot_date,
                source_file,
                imported_by,
                imported_at,
                row_count
            FROM snapshot_batches
            ORDER BY batch_id DESC
            LIMIT 1
            """
        ).fetchone()

        return dict(row) if row else None