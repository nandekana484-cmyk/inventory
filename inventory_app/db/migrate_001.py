import os
import sys
import sqlite3

# プロジェクトルートをimport対象に追加
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)
sys.path.insert(0, PROJECT_ROOT)

from config import DB_PATH


def get_columns(con, table_name):
    rows = con.execute(
        f"PRAGMA table_info([{table_name}])"
    ).fetchall()
    return {row[1] for row in rows}


def table_exists(con, table_name):
    row = con.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,)
    ).fetchone()
    return row is not None


def add_column_if_missing(con, table_name, column_name, definition):
    columns = get_columns(con, table_name)

    if column_name not in columns:
        con.execute(
            f"ALTER TABLE [{table_name}] "
            f"ADD COLUMN [{column_name}] {definition}"
        )
        print(f"[追加] {table_name}.{column_name}")


def migrate_snapshot_tables(con):
    print("\n[スナップショットテーブル移行]")

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

    # 正式テーブルにbatch_idを追加
    add_column_if_missing(
        con,
        "stock_snapshot",
        "batch_id",
        "INTEGER"
    )

    con.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_stock_snapshot_batch
        ON stock_snapshot(batch_id)
        """
    )

    target_count = con.execute(
        "SELECT COUNT(*) FROM stock_snapshot"
    ).fetchone()[0]

    legacy_exists = table_exists(con, "inventory_snapshots")

    if not legacy_exists:
        print("[情報] 旧inventory_snapshotsは存在しません")
        return

    legacy_count = con.execute(
        "SELECT COUNT(*) FROM inventory_snapshots"
    ).fetchone()[0]

    if legacy_count == 0:
        print("[情報] 旧inventory_snapshotsにデータはありません")
        return

    # 正式テーブルに既にデータがあれば二重移行しない
    if target_count > 0:
        print(
            "[スキップ] stock_snapshotに既存データがあるため、"
            "旧データの自動移行を行いません"
        )
        return

    print(f"[移行] inventory_snapshots {legacy_count}件")

    dates = con.execute(
        """
        SELECT DISTINCT snapshot_date
        FROM inventory_snapshots
        ORDER BY snapshot_date
        """
    ).fetchall()

    total_migrated = 0

    for date_row in dates:
        snapshot_date = date_row[0]

        cur = con.execute(
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
                "legacy:inventory_snapshots",
                "MIGRATION",
            )
        )

        batch_id = cur.lastrowid

        rows = con.execute(
            """
            SELECT
                part_id,
                code96,
                snapshot_qty,
                snapshot_date
            FROM inventory_snapshots
            WHERE snapshot_date = ?
            ORDER BY snapshot_id
            """,
            (snapshot_date,)
        ).fetchall()

        inserted = 0

        for row in rows:
            part_id = row[0]
            code96 = row[1]
            qty = row[2] or 0
            row_date = row[3]

            # code96が空の場合は部品マスタから補完
            if not code96 and part_id:
                master_row = con.execute(
                    """
                    SELECT code96
                    FROM parts
                    WHERE part_id = ?
                    """,
                    (part_id,)
                ).fetchone()

                if master_row:
                    code96 = master_row[0]

            con.execute(
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
                    part_id,
                    code96,
                    qty,
                    row_date,
                    "legacy_migration",
                )
            )

            inserted += 1
            total_migrated += 1

        con.execute(
            """
            UPDATE snapshot_batches
            SET row_count = ?
            WHERE batch_id = ?
            """,
            (inserted, batch_id)
        )

        print(
            f"  基準日={snapshot_date}, "
            f"batch_id={batch_id}, 件数={inserted}"
        )

    print(f"[完了] スナップショット移行件数: {total_migrated}")


def migrate_physical_count(con):
    print("\n[実地棚卸テーブル移行]")

    # 正式テーブルに旧画面が必要とする項目を追加
    add_column_if_missing(
        con,
        "physical_count",
        "count_date",
        "TEXT"
    )

    add_column_if_missing(
        con,
        "physical_count",
        "is_checked",
        "INTEGER DEFAULT 1"
    )

    add_column_if_missing(
        con,
        "physical_count",
        "updated_at",
        "TEXT"
    )

    if not table_exists(con, "physical_counts"):
        print("[情報] 旧physical_countsは存在しません")
        return

    legacy_count = con.execute(
        "SELECT COUNT(*) FROM physical_counts"
    ).fetchone()[0]

    if legacy_count == 0:
        print("[情報] 旧physical_countsにデータはありません")
        return

    target_count = con.execute(
        "SELECT COUNT(*) FROM physical_count"
    ).fetchone()[0]

    if target_count > 0:
        print(
            "[スキップ] physical_countに既存データがあるため、"
            "旧データの自動移行を行いません"
        )
        return

    con.execute(
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
        SELECT
            part_id,
            code96,
            counted_qty,
            COALESCE(updated_at, count_date),
            worker_id,
            count_date,
            is_checked,
            updated_at
        FROM physical_counts
        """
    )

    print(f"[完了] 実地棚卸移行件数: {legacy_count}")

    # 同一日・同一部品を重複登録しないための制約
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        ux_physical_count_date_part
        ON physical_count(count_date, part_id)
        """
    )


def migrate_audit_log(con):
    print("\n[監査ログテーブル拡張]")

    add_column_if_missing(
        con,
        "audit_log",
        "table_name",
        "TEXT"
    )

    add_column_if_missing(
        con,
        "audit_log",
        "record_pk",
        "TEXT"
    )

    add_column_if_missing(
        con,
        "audit_log",
        "operation_type",
        "TEXT"
    )

    add_column_if_missing(
        con,
        "audit_log",
        "field_name",
        "TEXT"
    )

    add_column_if_missing(
        con,
        "audit_log",
        "old_value",
        "TEXT"
    )

    add_column_if_missing(
        con,
        "audit_log",
        "new_value",
        "TEXT"
    )

    con.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_audit_log_table_record
        ON audit_log(table_name, record_pk)
        """
    )

    con.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_audit_log_created_at
        ON audit_log(created_at)
        """
    )

    print("[完了] audit_logを拡張しました")


def main():
    print("========================================")
    print("DB Migration 001")
    print("========================================")
    print(f"対象DB: {DB_PATH}")

    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(
            f"DBが見つかりません: {DB_PATH}"
        )

    con = sqlite3.connect(DB_PATH)

    try:
        con.execute("PRAGMA foreign_keys = OFF")
        con.execute("BEGIN")

        migrate_snapshot_tables(con)
        migrate_physical_count(con)
        migrate_audit_log(con)

        con.commit()
        print("\n[成功] Migration 001が完了しました")

    except Exception:
        con.rollback()
        print("\n[失敗] エラーが発生したためロールバックしました")
        raise

    finally:
        con.close()


if __name__ == "__main__":
    main()