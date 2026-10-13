import sqlite3
import sys

sys.path.append(r"C:\work\inventory\inventory_app")
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

print("--- PRAGMA table_info(production_daily) ---")
rows = cur.execute("PRAGMA table_info(production_daily)").fetchall()

for row in rows:
    print(row)

print()
print("--- CREATE TABLE production_daily ---")

row = cur.execute(
    "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
    ("table", "production_daily"),
).fetchone()

print(row[0] if row else "TABLE NOT FOUND")

conn.close()
