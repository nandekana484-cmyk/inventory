import sqlite3
from datetime import datetime

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# =====================================================
# 旧：基板グループ単位の簡易生産実績（production_records）
# ※廃止予定。新規実装では使用しないこと。
# =====================================================

def init_production_table():
    """生産実績テーブルの初期化（計画数と実績数を持つ）"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS production_records (
                record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_date TEXT NOT NULL,
                board_group_id TEXT NOT NULL,
                plan_qty REAL DEFAULT 0,
                qty REAL DEFAULT 0,
                worker_id TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(production_date, board_group_id)
            )
        """)
        con.commit()


def get_bom_groups():
    """BOMマスタに登録されている基板グループIDのリストを取得"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT DISTINCT group_id FROM component_bom ORDER BY group_id")
        return [row["group_id"] for row in cur.fetchall()]


def get_daily_production(target_date: str):
    """指定日の生産計画・実績一覧を取得"""
    init_production_table()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT record_id, board_group_id, plan_qty, qty, worker_id
            FROM production_records
            WHERE production_date = ?
            ORDER BY board_group_id
        """, (target_date,))
        return [dict(row) for row in cur.fetchall()]


def upsert_production_record(p_date: str, group_id: str, plan_qty: float, actual_qty: float, worker_id: str):
    """生産計画・実績の保存または更新"""
    init_production_table()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO production_records (production_date, board_group_id, plan_qty, qty, worker_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(production_date, board_group_id) DO UPDATE SET
                plan_qty = excluded.plan_qty,
                qty = excluded.qty,
                worker_id = excluded.worker_id,
                updated_at = CURRENT_TIMESTAMP
        """, (p_date, group_id, plan_qty, actual_qty, worker_id))
        con.commit()


# =====================================================
# 新：キッティングリストNo.紐付き日次生産実績（production_daily）
# ※production_dailyテーブル本体はdb/schema.sqlで作成済み。
#   plan_item_id / kitting_list_no 列は db/migration_002.py で追加すること。
#   このファイルではCREATE TABLEを行わない。
# =====================================================

def get_app_cumulative_qty(kitting_list_no: str) -> float:
    """指定キッティングリストNo.のアプリ入力累計を返す"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(daily_qty), 0) AS total
            FROM production_daily
            WHERE kitting_list_no = ?
        """, (kitting_list_no,))
        return cur.fetchone()["total"]


def insert_daily_production(plan_item_id, kitting_list_no, lot_id, group_id,
                              report_date, daily_qty, worker_id):
    """日次実績を1レコードとして追加保存する（洗い替えではなく追記）"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO production_daily (
                plan_item_id, kitting_list_no, lot_id, group_id,
                report_date, daily_qty, worker_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (plan_item_id, kitting_list_no, lot_id, group_id,
              report_date, daily_qty, worker_id))
        con.commit()


def list_daily_production_by_kitting_no(kitting_list_no: str):
    """指定キッティングリストNo.の日次実績履歴を取得"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM production_daily
            WHERE kitting_list_no = ?
            ORDER BY report_date
        """, (kitting_list_no,))
        return [dict(r) for r in cur.fetchall()]


def list_daily_production_today():
    """本日（report_date = 今日の日付）に登録された日次実績を取得する"""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM production_daily
            WHERE report_date = ?
            ORDER BY prod_log_id
        """, (today,))
        return [dict(r) for r in cur.fetchall()]


def list_daily_production_range(from_date: str, to_date: str):
    """report_date が from_date～to_date（両端含む、"YYYY-MM-DD"文字列）の日次実績を取得する"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM production_daily
            WHERE report_date >= ? AND report_date <= ?
            ORDER BY report_date, prod_log_id
        """, (from_date, to_date))
        return [dict(r) for r in cur.fetchall()]


def update_daily_production(prod_log_id: int, daily_qty: float):
    """日次実績1件（prod_log_id指定）のdaily_qtyを修正する"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            UPDATE production_daily
            SET daily_qty = ?
            WHERE prod_log_id = ?
        """, (daily_qty, prod_log_id))
        con.commit()


def delete_daily_production(prod_log_id: int):
    """日次実績1件（prod_log_id指定）を削除する"""
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            DELETE FROM production_daily
            WHERE prod_log_id = ?
        """, (prod_log_id,))
        con.commit()