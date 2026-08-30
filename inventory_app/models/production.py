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

def get_app_cumulative_qty(kitting_list_no: str, lot_no: str) -> float:
    """
    指定kitting_list_no・lot_noの組み合わせに一致するアプリ入力累計を返す。

    実DBで、同一kitting_list_noが複数の異なるlot_noにまたがって存在する
    ケースが478件確認されており（別々の基板・別々の発注数の計画が同じ
    kitting_list_noを共有している）、kitting_list_noだけで集計すると
    別ロットの実績まで巻き込んで合算してしまう（実データで完成数の取り違えを
    確認済み）。そのため、production_daily.lot_id（登録時にplanのlot_noが
    そのまま記録される列）も条件に含める。COALESCE(...,'')で比較するのは、
    NULLとNULLはSQL上「等しい」と判定されないため（他の箇所のlot_no比較
    パターン、list_active_plan_items()等と同様の書き方に揃えている）。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(daily_qty), 0) AS total
            FROM production_daily
            WHERE kitting_list_no = ? AND COALESCE(lot_id, '') = COALESCE(?, '')
        """, (kitting_list_no, lot_no))
        return cur.fetchone()["total"]


def get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs, con=None) -> dict:
    """
    複数の(kitting_list_no, lot_no)の組について、アプリ入力累計（daily_qtyのSUM）を
    1回（〜数回）のクエリでまとめて取得する。

    get_app_cumulative_qty()を件数分ループ呼び出しする（N+1）のを避けるための一括版。
    計算内容はget_app_cumulative_qty()と同一（同一(kitting_list_no, lot_no)に対して
    同じ値を返す）。

    kitting_list_no_lot_pairs：[(kitting_list_no, lot_no), ...]。
    呼び出し元がkitting_list_noごとに異なるlot_noを扱う必要がある場合
    （list_active_plan_items()等、複数lot_noの計画が混在するリストを渡す場合）に
    対応するため、単一のlot_noではなく組のリストを受け取る形にしている。

    実装方針：kitting_list_no側でSQLのIN句による絞り込みを行った上で、
    (kitting_list_no, lot_id)の組をPython側で要求された組のみに絞り込む
    （SQLiteの行値IN句構文に頼らない、より確実な方式）。同一kitting_list_noに
    複数のlot_noが存在するケースでも、GROUP BYにlot_idを含めることで
    正しく組ごとに分離集計される。

    戻り値：{(kitting_list_no, lot_no): 累計値, ...}。production_dailyに1件も無い
    組も0.0でキーを含める（get_app_cumulative_qty()のCOALESCE(...,0)と挙動を
    揃えるため。呼び出し元は必ず全キーが存在する前提で辞書を引ける）。

    con：呼び出し元が既に開いているコネクションを渡すと、それを使い回して
    新規コネクションを張らない（呼び出し元がトランザクション・クローズの責任を持つ）。
    省略時はここで新規コネクションを開いて完結させる。
    """
    unique_pairs = list(dict.fromkeys(kitting_list_no_lot_pairs))  # 重複除去・順序維持
    result = {pair: 0.0 for pair in unique_pairs}
    if not result:
        return result

    # lot_noの有無に関わらず一致判定できるよう、Python側の照合キーは
    # 空文字に正規化する（DB側のCOALESCE(lot_id,'')との対称性を保つため）。
    # 正規化キー -> 元のキー（resultの実キーは元のlot_no表記のまま使いたいため）。
    normalized_to_original = {(kn, lot or ""): (kn, lot) for kn, lot in unique_pairs}
    kitting_list_nos = list(dict.fromkeys(kn for kn, _lot in unique_pairs))

    # SQLiteのホストパラメータ上限（環境によっては999）を考慮し、チャンクに分けて実行する
    CHUNK_SIZE = 500

    def _run(active_con):
        for i in range(0, len(kitting_list_nos), CHUNK_SIZE):
            chunk = kitting_list_nos[i:i + CHUNK_SIZE]
            placeholders = ",".join("?" * len(chunk))
            cur = active_con.execute(f"""
                SELECT kitting_list_no, lot_id, COALESCE(SUM(daily_qty), 0) AS total
                FROM production_daily
                WHERE kitting_list_no IN ({placeholders})
                GROUP BY kitting_list_no, lot_id
            """, chunk)
            for row in cur.fetchall():
                key = (row["kitting_list_no"], row["lot_id"] or "")
                original = normalized_to_original.get(key)
                if original is not None:
                    result[original] = row["total"]

    if con is not None:
        _run(con)
    else:
        with get_connection() as new_con:
            _run(new_con)

    return result


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


def replace_daily_result(plan_item_id, kitting_list_no, lot_id, group_id,
                           report_date, daily_qty, worker_id):
    """
    指定kitting_list_no・lot_id（lot_no）に一致する既存のproduction_dailyレコードを
    日付を問わず全て削除してから、新しい1件を追加する（delete-then-insert、
    「1計画（kitting_list_no・lot_no）=1レコード、常に上書き」ルール）。

    以前は report_date（当日）が一致するレコードのみを削除する「当日限定」の
    仕様だったが、計画ごとの実績は常に最新の1件のみを保持する運用に変更したため、
    report_dateは削除条件から外した（その計画の過去日付分のレコードも含めて
    全て削除してから、新しい1件をINSERTする）。

    DELETEの条件にlot_id（挿入用にもともと受け取っているlot_no）を含めるのは、
    同一kitting_list_noが複数の異なるlot_noにまたがって存在する実データが
    478件確認されているため（kitting_list_noだけで削除すると、別ロットの
    計画の実績まで誤って削除してしまう）。新たな引数は追加せず、
    既にINSERT用に受け取っているlot_idをDELETEの条件にも流用している。

    1つのコネクション・1つのcommitで削除・追加の両方を確定させる（with文により、
    途中で例外が発生した場合は自動的にロールバックされ、削除だけが反映される
    中途半端な状態にはならない）。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "DELETE FROM production_daily WHERE kitting_list_no = ? "
            "AND COALESCE(lot_id, '') = COALESCE(?, '')",
            (kitting_list_no, lot_id),
        )
        cur.execute("""
            INSERT INTO production_daily (
                plan_item_id, kitting_list_no, lot_id, group_id,
                report_date, daily_qty, worker_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (plan_item_id, kitting_list_no, lot_id, group_id,
              report_date, daily_qty, worker_id))
        con.commit()


def list_daily_production_by_kitting_no(kitting_list_no: str, lot_no: str, report_date: str = None):
    """
    指定キッティングリストNo.・ロットNo.の日次実績履歴を取得。

    同一kitting_list_noが複数の異なるlot_noにまたがって存在する実データが
    478件確認されているため、lot_no（production_daily.lot_id列）も必須の
    条件として受け取る（kitting_list_noだけでは別ロットの実績まで混入する）。

    report_date（"YYYY-MM-DD"、report_date列と同じ形式）を指定すると、
    その日付の実績のみに絞り込む。省略時（None）は従来通り全期間を返す
    （ActualCorrectionWindow等、完了済み計画も含め過去の実績を修正・削除する
    画面はこちらの全期間版が必要なため、デフォルトは変更しない）。
    """
    with get_connection() as con:
        cur = con.cursor()
        if report_date is None:
            cur.execute("""
                SELECT * FROM production_daily
                WHERE kitting_list_no = ? AND COALESCE(lot_id, '') = COALESCE(?, '')
                ORDER BY report_date
            """, (kitting_list_no, lot_no))
        else:
            cur.execute("""
                SELECT * FROM production_daily
                WHERE kitting_list_no = ? AND COALESCE(lot_id, '') = COALESCE(?, '')
                  AND report_date = ?
                ORDER BY report_date
            """, (kitting_list_no, lot_no, report_date))
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