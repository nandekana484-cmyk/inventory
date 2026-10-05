from models.db_common import get_master_connection


def init_master_tables():
    """parts・final_products テーブルの初期化（既存があれば何もしない）。db/schema.sqlの定義と同一。"""
    with get_master_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS parts (
                part_id TEXT PRIMARY KEY,
                code96 TEXT NOT NULL,
                part_type TEXT,
                shelf_type TEXT,
                shape_category TEXT,
                is_high_value INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS final_products (
                product_id TEXT PRIMARY KEY,
                product_name TEXT NOT NULL
            )
        """)
        con.commit()


# --- 部品マスタ (parts) ---
def upsert_part(part_id: str, code96: str, part_type: str, shelf_type: str, shape_category: str):
    init_master_tables()
    with get_master_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO parts (part_id, code96, part_type, shelf_type, shape_category, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(part_id) DO UPDATE SET
                code96=excluded.code96,
                part_type=excluded.part_type,
                shelf_type=excluded.shelf_type,
                shape_category=excluded.shape_category
        """, (part_id, code96, part_type, shelf_type, shape_category))
        con.commit()

# --- 完成品マスタ (final_products) ---
def upsert_product(product_id: str, product_name: str):
    init_master_tables()
    with get_master_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO final_products (product_id, product_name)
            VALUES (?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                product_name=excluded.product_name
        """, (product_id, product_name))
        con.commit()
