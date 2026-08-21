import sqlite3
from config import DB_PATH

def get_connection():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def get_inventory_reconciliation():
    """
    スナップショット、生産消費量、実地カウント結果を結合し、
    理論在庫および差分（理論差分、棚卸差分）を算出する。
    """
    query = """
    WITH latest_snap AS (
        -- 最新のスナップショットデータを部品コードごとに集計
        SELECT code96, SUM(qty) AS snap_qty, MAX(imported_at) AS snap_date
        FROM snapshot_inventory
        GROUP BY code96
    ),
    used_qty AS (
        -- スナップショット以降の生産実績による消費量を集計
        SELECT cb.code96, SUM(pr.qty * cb.usage_qty) AS total_used
        FROM production_records pr
        JOIN component_bom cb ON pr.board_group_id = cb.group_id
        GROUP BY cb.code96
    ),
    actual_count AS (
        -- 最新の実地カウント結果を部品コードごとに集計
        SELECT p.code96, SUM(pc.count_qty) AS counted_qty
        FROM physical_counts pc
        JOIN parts p ON pc.part_id = p.part_id
        GROUP BY p.code96
    )
    SELECT 
        p.code96,
        p.part_type,
        p.shelf_type,
        COALESCE(s.snap_qty, 0) AS snap_qty,
        COALESCE(u.total_used, 0) AS used_qty,
        (COALESCE(s.snap_qty, 0) - COALESCE(u.total_used, 0)) AS theoretical_qty,
        COALESCE(a.counted_qty, 0) AS counted_qty,
        (COALESCE(a.counted_qty, 0) - (COALESCE(s.snap_qty, 0) - COALESCE(u.total_used, 0))) AS diff_qty
    FROM parts p
    LEFT JOIN latest_snap s ON p.code96 = s.code96
    LEFT JOIN used_qty u ON p.code96 = u.code96
    LEFT JOIN actual_count a ON p.code96 = a.code96
    ORDER BY p.code96
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(query)
        return [dict(row) for row in cur.fetchall()]

