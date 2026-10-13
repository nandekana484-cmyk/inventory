import sys
import sqlite3

sys.path.append(r'C:\work\inventory\inventory_app')
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print('DB_PATH:', DB_PATH)
print()

print('--- CREATE TABLE kitting_plan_items ---')
row = cur.execute(
    'SELECT sql FROM sqlite_master WHERE type = ? AND name = ?',
    ('table', 'kitting_plan_items')
).fetchone()

print(row[0] if row else 'TABLE NOT FOUND')

print()
print('--- INDEX LIST ---')

indexes = cur.execute(
    'PRAGMA index_list(kitting_plan_items)'
).fetchall()

for index_row in indexes:
    print(index_row)

print()
print('--- INDEX DETAILS ---')

for index_row in indexes:
    index_name = index_row[1]

    # PRAGMAにはパラメータバインドを使えないため、SQLite識別子用にエスケープする。
    safe_index_name = index_name.replace('"', '""')
    index_columns = cur.execute(
        f'PRAGMA index_info("{safe_index_name}")'
    ).fetchall()

    print(index_name, index_columns)

conn.close()
