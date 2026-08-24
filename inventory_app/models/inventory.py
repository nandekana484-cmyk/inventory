import sqlite3
import config

def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con

# =====================================================
# 96コードごとの在庫数量（inventory_stock）
#
# フェーズ2：在庫CSVインポート（ui.inventory_input_window）に対応するため、
# 棚種別・部品支給区分・マスタCHK使用数の3列を追加した。
# 既存の part_no・stock_qty のみを扱う upsert_inventory() はそのまま残し、
# 新列は upsert_inventory_stock() 経由でのみ設定する
# （upsert_inventory() は新列を変更せず、既存値を保持したまま stock_qty のみ更新する）。
# =====================================================

_EXTRA_COLUMNS = (
    ("shelf_type", "TEXT"),
    ("supply_type", "TEXT"),
    ("master_chk_qty", "REAL"),
)


def init_inventory_stock_table():
    """在庫数量テーブルの初期化（既存があれば何もしない。列追加も既存があればスキップ）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS inventory_stock (
                part_no TEXT PRIMARY KEY,
                stock_qty INTEGER
            )
        """)

        cur = con.execute("PRAGMA table_info(inventory_stock)")
        existing_cols = {row[1] for row in cur.fetchall()}
        for col_name, col_type in _EXTRA_COLUMNS:
            if col_name not in existing_cols:
                con.execute(f"ALTER TABLE inventory_stock ADD COLUMN {col_name} {col_type}")

        con.commit()


def list_inventory():
    """在庫数量の一覧を part_no 順で取得する。"""
    init_inventory_stock_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, stock_qty, shelf_type, supply_type, master_chk_qty
            FROM inventory_stock
            ORDER BY part_no
        """)
        return [dict(row) for row in cur.fetchall()]


def get_inventory(part_no: str):
    """指定 part_no の在庫情報を取得する。存在しなければ None を返す。"""
    init_inventory_stock_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, stock_qty, shelf_type, supply_type, master_chk_qty
            FROM inventory_stock WHERE part_no = ?
        """, (part_no,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_inventory(part_no: str, qty: int):
    """在庫数量のみを登録または更新する（手動入力UI用）。"""
    init_inventory_stock_table()
    with get_connection() as con:
        con.execute("""
            INSERT INTO inventory_stock (part_no, stock_qty)
            VALUES (?, ?)
            ON CONFLICT(part_no) DO UPDATE SET stock_qty = excluded.stock_qty
        """, (part_no, qty))
        con.commit()


def upsert_inventory_stock(part_no: str, qty, shelf_type: str = None,
                            supply_type: str = None, master_chk_qty=None):
    """
    在庫CSVインポート用のupsert（models.inventory_stock.upsert_inventory_stock 相当）。

    96コード（part_no）をキーに、在庫数・棚種別・部品支給区分・マスタCHK使用数を
    まとめて登録・更新する。既存なら上書き、なければ新規登録する
    （差分検知は行わず常に上書き）。
    """
    init_inventory_stock_table()
    with get_connection() as con:
        con.execute("""
            INSERT INTO inventory_stock (part_no, stock_qty, shelf_type, supply_type, master_chk_qty)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(part_no) DO UPDATE SET
                stock_qty = excluded.stock_qty,
                shelf_type = excluded.shelf_type,
                supply_type = excluded.supply_type,
                master_chk_qty = excluded.master_chk_qty
        """, (part_no, qty, shelf_type, supply_type, master_chk_qty))
        con.commit()


def delete_inventory(part_no: str):
    """在庫データを削除する。"""
    init_inventory_stock_table()
    with get_connection() as con:
        con.execute("DELETE FROM inventory_stock WHERE part_no = ?", (part_no,))
        con.commit()
