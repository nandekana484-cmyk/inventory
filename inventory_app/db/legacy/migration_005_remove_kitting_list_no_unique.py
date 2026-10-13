"""
migration_005:
kitting_plan_items.kitting_list_no の UNIQUE 制約を除去する。

計画バージョン管理では同一 kitting_list_no の複数バージョンを
保持する必要があるため、SQLiteのテーブル再作成で制約を除去する。

移行後は、同一 kitting_list_no に対して is_active=1 のレコードが
同時に複数存在しないよう、部分ユニークインデックスを作成する。
"""

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import DB_PATH


OLD_COLUMNS = [
    "plan_item_id",
    "plan_batch_id",
    "kitting_list_no",
    "delete_flag",
    "setup_file_no",
    "lot_no",
    "mounting_line",
    "board_name",
    "planned_qty",
    "cumulative_qty_external",
    "order_qty",
    "production_side",
    "status",
    "plan_start_datetime",
    "plan_end_datetime",
    "deadline",
    "actual_start_datetime",
    "actual_end_datetime",
    "created_at",
    "updated_at",
    "version",
    "is_active",
    "previous_plan_item_id",
    "created_by",
]


def main():
    conn = sqlite3.connect(DB_PATH)

    try:
        # テーブル再作成中は参照整合性チェックを一時無効化する。
        # PRAGMA foreign_keys はトランザクション開始前に実行する必要がある。
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")

        cur = conn.cursor()

        actual_columns = [
            row[1]
            for row in cur.execute(
                "PRAGMA table_info(kitting_plan_items)"
            ).fetchall()
        ]

        missing_columns = set(OLD_COLUMNS) - set(actual_columns)
        if missing_columns:
            raise RuntimeError(
                "kitting_plan_items に想定列がありません。"
                "migration_004 の適用状況を確認してください: "
                + ", ".join(sorted(missing_columns))
            )

        print("Creating replacement table...")

        cur.execute("""
            CREATE TABLE kitting_plan_items_new (
                plan_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_batch_id INTEGER NOT NULL,
                kitting_list_no TEXT NOT NULL,
                delete_flag INTEGER DEFAULT 0,
                setup_file_no TEXT,
                lot_no TEXT,
                mounting_line TEXT,
                board_name TEXT,
                planned_qty REAL DEFAULT 0,
                cumulative_qty_external REAL DEFAULT 0,
                order_qty REAL DEFAULT 0,
                production_side TEXT,
                status TEXT,
                plan_start_datetime TEXT,
                plan_end_datetime TEXT,
                deadline TEXT,
                actual_start_datetime TEXT,
                actual_end_datetime TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                version INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                previous_plan_item_id INTEGER,
                created_by TEXT,
                FOREIGN KEY (plan_batch_id)
                    REFERENCES kitting_plan_batches(plan_batch_id)
            )
        """)

        column_csv = ", ".join(OLD_COLUMNS)

        print("Copying existing records...")
        cur.execute(f"""
            INSERT INTO kitting_plan_items_new ({column_csv})
            SELECT
                plan_item_id,
                plan_batch_id,
                kitting_list_no,
                delete_flag,
                setup_file_no,
                lot_no,
                mounting_line,
                board_name,
                planned_qty,
                cumulative_qty_external,
                order_qty,
                production_side,
                status,
                plan_start_datetime,
                plan_end_datetime,
                deadline,
                actual_start_datetime,
                actual_end_datetime,
                created_at,
                updated_at,
                COALESCE(version, 1),
                COALESCE(is_active, 1),
                previous_plan_item_id,
                created_by
            FROM kitting_plan_items
        """)

        old_count = cur.execute(
            "SELECT COUNT(*) FROM kitting_plan_items"
        ).fetchone()[0]

        new_count = cur.execute(
            "SELECT COUNT(*) FROM kitting_plan_items_new"
        ).fetchone()[0]

        if old_count != new_count:
            raise RuntimeError(
                f"件数不一致のため中止します: old={old_count}, new={new_count}"
            )

        print(f"Copied records: {new_count}")

        print("Replacing old table...")
        cur.execute("DROP TABLE kitting_plan_items")
        cur.execute(
            "ALTER TABLE kitting_plan_items_new RENAME TO kitting_plan_items"
        )

        # 検索性能用インデックス
        cur.execute("""
            CREATE INDEX idx_kitting_plan_items_batch_id
            ON kitting_plan_items(plan_batch_id)
        """)

        cur.execute("""
            CREATE INDEX idx_kitting_plan_items_kitting_version
            ON kitting_plan_items(kitting_list_no, version DESC)
        """)

        # 同一キッティングリストNo.でアクティブ版が複数存在することを防止する。
        cur.execute("""
            CREATE UNIQUE INDEX uq_kitting_plan_items_active_kitting_no
            ON kitting_plan_items(kitting_list_no)
            WHERE is_active = 1
        """)

        conn.commit()
        print("migration_005 completed successfully.")

    except Exception:
        conn.rollback()
        print("migration_005 failed. Transaction rolled back.", file=sys.stderr)
        raise

    finally:
        conn.execute("PRAGMA foreign_keys = ON")

        foreign_key_errors = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

        if foreign_key_errors:
            print(
                "WARNING: foreign_key_check detected the following rows:",
                foreign_key_errors
            )
        else:
            print("foreign_key_check: OK")

        conn.close()


if __name__ == "__main__":
    main()