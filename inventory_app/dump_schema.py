import sqlite3
from config import DB_PATH

print("使用DB:", DB_PATH)

con = sqlite3.connect(DB_PATH)

print("\n=== TABLES ===")

tables = con.execute("""
    SELECT name
    FROM sqlite_master
    WHERE type = 'table'
    ORDER BY name
""").fetchall()

for (table_name,) in tables:
    print(f"\n--- {table_name} ---")

    columns = con.execute(
        f"PRAGMA table_info([{table_name}])"
    ).fetchall()

    for col in columns:
        # cid, name, type, notnull, default_value, pk
        print(
            f"  {col[1]} | type={col[2]} | "
            f"notnull={col[3]} | default={col[4]} | pk={col[5]}"
        )

    count = con.execute(
        f"SELECT COUNT(*) FROM [{table_name}]"
    ).fetchone()[0]

    print(f"  行数: {count}")

con.close()