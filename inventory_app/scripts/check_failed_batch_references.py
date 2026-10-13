import sqlite3
import sys

sys.path.append(r"C:\work\inventory\inventory_app")
from config import DB_PATH

TARGET_BATCH_IDS = (10, 12, 13)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

placeholders = ", ".join("?" for _ in TARGET_BATCH_IDS)

print("--- 削除候補バッチ ---")

batches = cur.execute(
    f"""
    SELECT
        b.plan_batch_id,
        b.source_file,
        b.imported_at,
        b.imported_by,
        b.row_count,
        COALESCE(b.delete_flag, 0) AS delete_flag,
        COUNT(i.plan_item_id) AS actual_item_count
    FROM kitting_plan_batches AS b
    LEFT JOIN kitting_plan_items AS i
        ON i.plan_batch_id = b.plan_batch_id
    WHERE b.plan_batch_id IN ({placeholders})
    GROUP BY
        b.plan_batch_id,
        b.source_file,
        b.imported_at,
        b.imported_by,
        b.row_count,
        b.delete_flag
    ORDER BY b.plan_batch_id
    """,
    TARGET_BATCH_IDS,
).fetchall()

for row in batches:
    print(row)

print()
print("--- production_daily.plan_item_id からの参照確認 ---")

references = cur.execute(
    f"""
    SELECT
        pd.prod_log_id,
        pd.report_date,
        pd.daily_qty,
        pd.worker_id,
        pd.plan_item_id,
        pd.kitting_list_no,
        kpi.plan_batch_id,
        kpi.kitting_list_no AS plan_kitting_list_no,
        kpi.version,
        kpi.is_active
    FROM production_daily AS pd
    INNER JOIN kitting_plan_items AS kpi
        ON kpi.plan_item_id = pd.plan_item_id
    WHERE kpi.plan_batch_id IN ({placeholders})
    ORDER BY pd.prod_log_id
    """,
    TARGET_BATCH_IDS,
).fetchall()

if references:
    print("RESULT: 削除停止。削除候補バッチの計画明細を参照する実績があります。")
    for row in references:
        print(row)
    conn.close()
    raise SystemExit(1)

print("RESULT: production_daily からの参照はありません。")
print("削除候補バッチは、production_daily観点では削除可能です。")

conn.close()
