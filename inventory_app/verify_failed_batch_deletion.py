import sqlite3
import sys

sys.path.append(r"C:\work\inventory\inventory_app")
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print('--- バッチ10・12・13の残存確認 ---')

for batch_id in (10, 12, 13):
    batch_count = cur.execute(
        'SELECT COUNT(*) FROM kitting_plan_batches WHERE plan_batch_id = ?',
        (batch_id,),
    ).fetchone()[0]

    item_count = cur.execute(
        'SELECT COUNT(*) FROM kitting_plan_items WHERE plan_batch_id = ?',
        (batch_id,),
    ).fetchone()[0]

    print(
        f'batch_id={batch_id}: '
        f'batch_rows={batch_count}, plan_item_rows={item_count}'
    )

print()
print('--- 計画明細総数 ---')
total = cur.execute(
    'SELECT COUNT(*) FROM kitting_plan_items'
).fetchone()[0]
print('total_plan_items:', total)

conn.close()
