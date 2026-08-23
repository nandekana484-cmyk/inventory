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
    """
    (group_id, code96) の組み合わせが既存であれば usage_qty を上書き更新し、
    なければ新規登録する。

    component_bom には (group_id, code96) の一意制約を追加していない
    （DBスキーマ変更は行わない方針のため）。重複登録の防止はこの関数内の
    事前SELECTによるコード側チェックのみで行っている。
    差分検知（値が変わった場合のみ更新する等）は行わず、常に上書きする。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT bom_id FROM component_bom WHERE group_id = ? AND code96 = ?
        """, (group_id, code96))
        existing = cur.fetchone()

        if existing:
            cur.execute("""
                UPDATE component_bom SET usage_qty = ? WHERE bom_id = ?
            """, (usage_qty, existing["bom_id"]))
        else:
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


def _resolve_group_id_by_file_no(file_no: str):
    """
    file_no（setup_file_no）から board_definitions → component_groups の経路で
    group_id を解決する内部ヘルパー。

    board_definitions.setup_file_no = file_no を満たす board_definitions から
    component_groups（board_id経由）を辿る。1つの file_no に対し面（side_number）
    違いで複数の component_groups が存在しうるため、group_id が一意に定まらない
    場合（0件、または2件以上）は None を返す。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT cg.group_id
            FROM board_definitions bd
            JOIN component_groups cg ON cg.board_id = bd.board_id
            WHERE bd.setup_file_no = ?
        """, (file_no,))
        rows = cur.fetchall()

    group_ids = {row["group_id"] for row in rows}
    if len(group_ids) == 1:
        return next(iter(group_ids))
    return None


def upsert_bom(file_no: str, part_no: str, usage_qty: int):
    """
    BOMマスタCSVインポート用のupsert。

    file_no（setup_file_no）→ board_definitions → component_groups の経路で
    group_id を解決し、insert_bom() と同じ「既存なら上書き・なければ新規登録」の
    方式で component_bom へ保存する（差分検知は行わず常に上書き）。

    group_id が一意に解決できない場合（該当する board_definitions/component_groups
    が存在しない、または1つの file_no に複数面のcomponent_groupsが存在し
    一意に定まらない場合）は、解決不能として NotImplementedError を送出する。
    呼び出し元の services.master_import_service.import_bom_csv() は
    この例外を捕捉して警告に変換する既存動作をそのまま利用できるため、
    この関数のシグネチャ・戻り値の扱いは変更していない。
    """
    group_id = _resolve_group_id_by_file_no(file_no)
    if group_id is None:
        raise NotImplementedError(
            f"upsert_bom() は file_no={file_no!r} の group_id を一意に解決できませんでした"
            "（該当するboard_definitions/component_groupsがない、"
            "または複数面のcomponent_groupsが存在する可能性があります）。"
        )

    insert_bom(group_id, part_no, usage_qty)
