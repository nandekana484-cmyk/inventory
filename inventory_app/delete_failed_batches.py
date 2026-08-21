import sqlite3
import sys

sys.path.append(r"C:\work\inventory\inventory_app")
from config import DB_PATH

TARGET_BATCH_IDS = (10, 12, 13)

conn = sqlite3.connect(DB_PATH)

try:
    cur = conn.cursor()
    placeholders = ", ".join("?" for _ in TARGET_BATCH_IDS)

    cur.execute("BEGIN IMMEDIATE")

    print("--- 削除前件数 ---")

    item_count = cur.execute(
        f"""
        SELECT COUNT(*)
        FROM kitting_plan_items
        WHERE plan_batch_id IN ({placeholders})
        """,
        TARGET_BATCH_IDS,
    ).fetchone()[0]

    batch_count = cur.execute(
        f"""
        SELECT COUNT(*)
        FROM kitting_plan_batches
        WHERE plan_batch_id IN ({placeholders})
        """,
        TARGET_BATCH_IDS,
    ).fetchone()[0]

    print("対象 plan_items:", item_count)
    print("対象 plan_batches:", batch_count)

    deleted_items = cur.execute(
        f"""
        DELETE FROM kitting_plan_items
        WHERE plan_batch_id IN ({placeholders})
        """,
        TARGET_BATCH_IDS,
    ).rowcount

    deleted_batches = cur.execute(
        f"""
        DELETE FROM kitting_plan_batches
        WHERE plan_batch_id IN ({placeholders})
        """,
        TARGET_BATCH_IDS,
    ).rowcount

    conn.commit()

    print()
    print("--- 削除結果 ---")
    print("deleted_plan_items:", deleted_items)
    print("deleted_plan_batches:", deleted_batches)

except Exception:
    conn.rollback()
    raise

finally:
    conn.close()
