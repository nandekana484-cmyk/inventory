import sqlite3

con = sqlite3.connect(r'C:\work\inventory\inventory_app\db\2026-07\inventory.db')
cur = con.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(cur.fetchall())

try:
    cur.execute("SELECT plan_batch_id, source_file, row_count FROM kitting_plan_batches ORDER BY plan_batch_id DESC LIMIT 5")
    print("batches:", cur.fetchall())
except Exception as e:
    print("batches error:", e)

try:
    cur.execute("SELECT plan_batch_id, COUNT(*) FROM kitting_plan_items GROUP BY plan_batch_id ORDER BY plan_batch_id DESC LIMIT 5")
    print("items:", cur.fetchall())
except Exception as e:
    print("items error:", e)