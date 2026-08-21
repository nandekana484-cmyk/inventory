import sqlite3
import sys

sys.path.append(r"C:\work\inventory\inventory_app")
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

rows = cur.execute("""
    SELECT
        b.plan_batch_id,
        b.source_file,
        b.imported_at,
        b.imported_by,
        b.row_count AS recorded_row_count,
        COALESCE(b.delete_flag, 0) AS delete_flag,
        COUNT(i.plan_item_id) AS actual_item_count,
        SUM(
            CASE
                WHEN COALESCE(i.is_active, 1) = 1 THEN 1
                ELSE 0
            END
        ) AS active_item_count,
        MIN(i.plan_item_id) AS min_plan_item_id,
        MAX(i.plan_item_id) AS max_plan_item_id
    FROM kitting_plan_batches AS b
    LEFT JOIN kitting_plan_items AS i
        ON i.plan_batch_id = b.plan_batch_id
    GROUP BY
        b.plan_batch_id,
        b.source_file,
        b.imported_at,
        b.imported_by,
        b.row_count,
        b.delete_flag
    ORDER BY b.plan_batch_id DESC
""").fetchall()

print(
    'batch_id | source_file | imported_at | imported_by | '
    'recorded_count | actual_count | active_count | '
    'delete_flag | min_item_id | max_item_id'
)
print('-' * 160)

for row in rows:
    (
        batch_id,
        source_file,
        imported_at,
        imported_by,
        recorded_count,
        delete_flag,
        actual_count,
        active_count,
        min_item_id,
        max_item_id,
    ) = row

    print(
        f'{batch_id} | {source_file} | {imported_at} | {imported_by} | '
        f'{recorded_count} | {actual_count} | {active_count or 0} | '
        f'{delete_flag} | {min_item_id} | {max_item_id}'
    )

conn.close()
