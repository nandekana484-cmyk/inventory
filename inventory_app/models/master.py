import sqlite3
from config import DB_PATH

def get_connection():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# --- 部品マスタ (parts) ---
def get_all_parts():
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT part_id, code96, part_type, shelf_type, shape_category, is_active FROM parts")
        return [dict(row) for row in cur.fetchall()]

def upsert_part(part_id: str, code96: str, part_type: str, shelf_type: str, shape_category: str):
    with get_connection() as con:
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

def delete_part(part_id: str):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM parts WHERE part_id = ?", (part_id,))
        con.commit()

# --- 完成品マスタ (final_products) ---
def get_all_products():
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT product_id, product_name FROM final_products")
        return [dict(row) for row in cur.fetchall()]

def upsert_product(product_id: str, product_name: str):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO final_products (product_id, product_name)
            VALUES (?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                product_name=excluded.product_name
        """, (product_id, product_name))
        con.commit()

def delete_product(product_id: str):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM final_products WHERE product_id = ?", (product_id,))
        con.commit()

# --- BOM定義 (component_bom) ---
def get_all_boms():
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT bom_id, group_id, code96, usage_qty 
            FROM component_bom
            ORDER BY group_id, code96
        """)
        return [dict(row) for row in cur.fetchall()]

def insert_bom(group_id: str, code96: str, usage_qty: float):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO component_bom (group_id, code96, usage_qty)
            VALUES (?, ?, ?)
        """, (group_id, code96, usage_qty))
        con.commit()

def delete_bom(bom_id: int):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM component_bom WHERE bom_id = ?", (bom_id,))
        con.commit()
