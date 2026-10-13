import sqlite3
import sys

sys.path.append(r"C:\work\inventory\inventory_app")
from config import DB_PATH

TEST_BATCH_IDS = (14, 15)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

placeholders = ", ".join("?" for _ in TEST_BATCH_IDS)

print("--- テストバッチ計画明細 ---")

items = cur.execute(
    f"""
    SELECT
        plan_item_id,
        plan_batch_id,
        kitting_list_no,
        version,
        is_active
    FROM kitting_plan_items
    WHERE plan_batch_id IN ({placeholders})
    ORDER BY plan_batch_id, plan_item_id
    """,
    TEST_BATCH_IDS,
).fetchall()

for row in items:
    print(row)

print()
print("--- production_daily からの参照確認 ---")

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
        kpi.version
    FROM production_daily AS pd
    INNER JOIN kitting_plan_items AS kpi
        ON kpi.plan_item_id = pd.plan_item_id
    WHERE kpi.plan_batch_id IN ({placeholders})
    ORDER BY pd.prod_log_id
    """,
    TEST_BATCH_IDS,
).fetchall()

if references:
    print("RESULT: 削除不可。テスト計画明細を参照する生産実績があります。")
    for row in references:
        print(row)
    conn.close()
    raise SystemExit(1)

print("RESULT: production_daily からの参照なし。テストバッチ削除可能です。")
conn.close()
