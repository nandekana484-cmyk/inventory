import sqlite3
import config

def get_connection():
    con = sqlite3.connect(config.DB_PATH)
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

# --- CSVインポート用 upsert（services/master_import_service.py から利用） ---
# 注意：既存の upsert_part(part_id, code96, part_type, shelf_type, shape_category) と
# 引数構成・意味が異なるため、同名で上書きしないよう upsert_part_master としている。

def upsert_part_master(part_no: str, name: str, shelf: str):
    """
    部品マスタCSVインポート用のupsert（拡張ポイント）。

    CSVはリールID（part_id）と96コード（code96）を区別しないため、
    part_no を両方の値として使用する。
    parts テーブルには「部品名」に対応する列が存在しないため、
    name は現時点では保存されない（列追加が必要になった場合の拡張ポイント）。
    既存の part_type / shape_category はこの関数では更新しない
    （旧 upsert_part() で設定された値を保持する）。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO parts (part_id, code96, shelf_type, is_active)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(part_id) DO UPDATE SET
                code96=excluded.code96,
                shelf_type=excluded.shelf_type
        """, (part_no, part_no, shelf))
        con.commit()
