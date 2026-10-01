from datetime import datetime

from models.db_common import get_connection


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

    report_date：明示的に指定された場合（CSVの払い出し日等）はその値をそのまま
    使う。**「明示的に指定された」とはNoneでも空文字列('')でもないことを指す**
    （空文字列は「値が無い」ことを表すNoneと同列に扱う。呼び出し元がCSVの
    空欄を`''`のまま渡してくる可能性を考慮した安全策）。

    Noneまたは空文字列の場合は、削除対象となる既存行（同一kitting_list_no・
    lot_id）のreport_dateを引き継ぐ（2026-09-29の調査で判明した、手動での
    数量修正・再登録のたびにreport_dateが意図せず「今日」に書き換わって
    しまう問題への対応。既存行の取得はDELETEの直前・同一コネクション内で
    行うため、他の処理と競合して読み取った内容と削除対象がずれる心配はない）。
    ただし、**引き継ごうとした既存行のreport_date自体がNULL・空文字列だった
    場合**（本来は本関数のNOT NULL制約により通常は発生しないはずだが、
    「行が存在すること」と「report_dateに有効な値が入っていること」は別の
    事実であるため、念のため区別して判定する。2026-09-29の調査で発見した
    論点）や、そもそも該当する既存行が無い場合（本来はoverwrite_daily_
    result()の契約上、既存行がある前提で呼ばれるため通常は発生しないが、
    念のためのフォールバック）は、register_daily_result()の新規登録時と
    同じく実行日（今日）を使う。

    最終防衛線：上記のいずれの分岐を通っても、DELETE・INSERTの直前に
    report_dateがNoneでも空文字列でもないことを改めて確認する（将来この
    関数にロジックが追加された場合等に備え、NOT NULL制約を持つ列へ空の値を
    書き込んでしまう抜け道を無くすため）。

    1つのコネクション・1つのcommitで（既存行の参照を行う場合はそれも含めて）
    削除・追加を確定させる（with文により、途中で例外が発生した場合は自動的に
    ロールバックされ、削除だけが反映される中途半端な状態にはならない）。
    """
    with get_connection() as con:
        cur = con.cursor()
        if not report_date:
            existing = cur.execute(
                "SELECT report_date FROM production_daily WHERE kitting_list_no = ? "
                "AND COALESCE(lot_id, '') = COALESCE(?, '')",
                (kitting_list_no, lot_id),
            ).fetchone()
            existing_report_date = existing["report_date"] if existing else None
            report_date = existing_report_date if existing_report_date else datetime.now().strftime("%Y-%m-%d")
        # 最終防衛線（docstring参照）：ここまでの分岐にロジックの抜け道があっても、
        # NOT NULL制約の列へNone・空文字列を書き込まないことをその場で保証する。
        if not report_date:
            report_date = datetime.now().strftime("%Y-%m-%d")
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


def get_production_daily_by_id(prod_log_id: int):
    """
    prod_log_id指定で1件取得する（無ければNone）。

    services.production_service.update_daily_result()・delete_daily_result()が、
    UPDATE/DELETE実行前に対象行のkitting_list_no・lot_idを特定するために使う
    （lot_status_history.record_lot_status_snapshot()を呼ぶにはlot_noが必要だが、
    update_daily_result()/delete_daily_result()はprod_log_idしか受け取らないため。
    DELETEは実行後には行が無くなり参照できなくなるので、必ずUPDATE/DELETEの
    「前」に呼ぶこと）。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM production_daily WHERE prod_log_id = ?
        """, (prod_log_id,))
        row = cur.fetchone()
        return dict(row) if row else None


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