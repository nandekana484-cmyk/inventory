import sqlite3
import sys

sys.path.append(r"C:\work\inventory\inventory_app")
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

rows = cur.execute("""
    SELECT
        plan_item_id,
        plan_batch_id,
        kitting_list_no,
        version,
        is_active,
        previous_plan_item_id,
        created_by
    FROM kitting_plan_items
    WHERE kitting_list_no LIKE 'TEST-CSV-%'
    ORDER BY kitting_list_no, version
""").fetchall()

for row in rows:
    print(row)

conn.close()
