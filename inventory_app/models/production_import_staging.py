# models/production_import_staging.py
"""
実績CSV取込のステージングデータ（登録前の一時保管）の永続化。

以前はui.production_import_staging_window.ProductionImportStagingWindowの
メモリ上（self._row_by_iid）にのみ保持しており、ウィンドウを閉じる・
アプリを終了すると未登録行が失われる問題があった。CSVから読み取った
生の行データ（lot_no・product_name・daily_qty・report_date・worker_id）
のみをここに永続化し、候補計画（candidates/matched）は保存しない。

候補を保存しない理由：候補はkitting_plan_itemsのスナップショットであり、
保存すると計画の変更（新バージョン作成・完了等）に追随できず陳腐化する
リスクがある。表示・再開のたびにmodels.kitting_plan.find_matching_plan_items()
で再照合する方針とした（速度改善（services.production_import_service.
group_active_plan_items_by_lot()によるN+1解消）により、再照合コストは
無視できるほど小さい）。

登録済み・除外済みの行は履歴として残さず、即座に物理削除する
（delete_pending_csv_import_row()）。ステータス列自体を持たない（テーブルに
存在する＝未処理、という単純な設計）。

識別キー（2026-10-06改訂）：以前は(lot_no, 正規化済みproduct_name)が一致する
既存の保留行を無条件に削除してから挿入していたが、これだと**同一CSV内に
同じロットNo・製品名の行が複数（別日の実績）ある場合、最後の1行を除く全てが
消えてしまう**という問題があった（実データで約22%の実績数量が消失する
ことを確認済み、CANONICAL_DESIGN_DECISIONS.md D-7x参照）。業務ルール：
同じロットNo・製品名でも、日付が異なれば別のキッティング計画の実績である
（どの計画の実績かは、払出し日を手がかりに人が判断して割り当てる）。
そのため実績CSVの行は、同一CSV内では**まとめたり捨てたりせず、1行を1件の
保留行として必ず残す**。

一方で「同じCSVをもう一度読み込んだ場合に二重に増えない」という目的
（本来の導入経緯）は維持する必要がある。そこで、重複判定の対象を
**「今回の取込バッチ（import_batch_id）とは別のバッチに属する既存の保留行」
のみに限定**し、かつ一致条件を(lot_no, 正規化済みproduct_name)だけでなく
**report_date・daily_qtyも含めた完全一致**に変更した：
  - 同一バッチ内の行同士は互いに比較しない（＝1回の取込で読み込んだ行は、
    内容が重複していても全て別々の行として残る）。
  - 別バッチ（＝以前の取込）の既存行と、lot_no・正規化product_name・
    report_date・daily_qtyが完全に一致する場合のみ「同じ内容が既に
    保留中」とみなし、新規挿入せず既存行をそのまま使う（再取込しても
    行が増えない）。日付や数量が少しでも異なれば、別の実績として新規に
    追加する。
"""
from models.db_common import get_connection


def init_csv_import_staging_tables():
    """csv_import_batches・pending_csv_import_rowsテーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        cur = con.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS csv_import_batches (
                import_batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                imported_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                imported_by TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_csv_import_rows (
                pending_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_batch_id INTEGER,
                csv_row_no INTEGER,
                lot_no TEXT NOT NULL,
                product_name TEXT NOT NULL,
                daily_qty REAL NOT NULL,
                report_date TEXT,
                worker_id TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (import_batch_id) REFERENCES csv_import_batches(import_batch_id)
            )
        """)

        # find_matching_plan_items()と同じ絞り込み軸（lot_no）でのSELECT・
        # upsert_pending_csv_import_row()の識別キー検索を高速化する。
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_csv_import_rows_lot_no
            ON pending_csv_import_rows(lot_no)
        """)

        con.commit()


def create_csv_import_batch(source_file: str, imported_by: str = None) -> int:
    """
    CSV取込1回分のバッチ行を作成する（監査・トレーサビリティ用。どのCSVから
    保留行が生まれたかを後から追える）。services.production_import_service.
    parse_production_csv_for_staging()が行ループに入る前に1回だけ呼ぶ想定
    （models.kitting_plan.create_plan_batch()と同じ位置付け）。
    """
    init_csv_import_staging_tables()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO csv_import_batches (source_file, imported_by) VALUES (?, ?)",
            (source_file, imported_by),
        )
        con.commit()
        return cur.lastrowid


def upsert_pending_csv_import_row(data: dict, import_batch_id: int = None, claimed_pending_row_ids: set = None) -> int:
    """
    実績CSVの1行をpending_csv_import_rowsへ保存する（2026-10-06改訂、
    本モジュールのdocstring「識別キー」参照）。

    同一import_batch_id内の既存行とは比較しない（1回の取込で読み込んだ行は
    内容が重複していても全て別々の行として残る）。import_batch_idが異なる
    （＝以前の取込由来の）既存行のうち、lot_no・正規化済みproduct_name・
    report_date・daily_qtyが完全に一致するものが見つかった場合のみ、
    「既に同じ内容の保留行がある」とみなして新規挿入せず、その既存行の
    pending_row_idをそのまま返す（再取込しても行が増えない）。

    claimed_pending_row_ids：同一のparse_production_csv_for_staging()呼び出し
    （＝1回の取込処理）内で、既に再利用済みの既存行のpending_row_idを集める
    集合。呼び出し元が1回の取込の開始時に空集合を作り、ループ全体で同じ
    集合を使い続けて渡す想定（2026-10-06追加）。これが無いと、今回のCSVに
    リテラルに全く同じ内容の行が複数ある場合（例：同じ行が誤って2回入力
    された等）、別バッチの既存行1件が、今回の複数の新規行すべてから
    繰り返し「再利用」されてしまい、2行目以降が新規挿入されず消えてしまう
    不具合があった（1行につき1回しか再利用されないよう、再利用した既存行の
    pending_row_idをこの集合に記録し、以降の同一取込内の比較では対象外にする）。
    省略時（None）は従来通り保護なしで動作する（テスト等、保護が不要な
    単発呼び出し向け）。

    lot_noは呼び出し元（parse_production_csv_for_staging()）で既に
    str().strip()済みの値を渡す想定。product_nameの正規化はservices.
    production_import_service.normalize_product_name()を使う（本モジュールが
    逆方向にservicesをトップレベルでインポートすると循環importになるため、
    models.kitting_plan.find_matching_plan_items()と同じく関数内インポートで
    回避する）。

    data：{"csv_row_no", "lot_no", "product_name", "daily_qty",
           "report_date", "worker_id"}。未知のキーは無視する。

    テーブルの存在保証：呼び出し元がループに入る前にcreate_csv_import_batch()
    （内部でinit_csv_import_staging_tables()を呼ぶ）を実行済みのため、
    ここで毎回初期化を呼ぶ必要はない（models.kitting_plan.
    upsert_pending_kitting_plan_item()と同じ理由。CSV行ごとに冗長な
    CREATE TABLE/INDEX IF NOT EXISTS文を再実行するとボトルネックになる
    ため、あえて呼ばない）。

    戻り値：保存した行（または再利用した既存行）のpending_row_id。
    """
    from services.production_import_service import normalize_product_name

    lot_no = str(data.get("lot_no") or "").strip()
    product_name = data.get("product_name")
    product_name_normalized = normalize_product_name(product_name)
    report_date = data.get("report_date")
    daily_qty = data.get("daily_qty")

    with get_connection() as con:
        cur = con.cursor()

        existing_rows = cur.execute(
            "SELECT pending_row_id, product_name, report_date, daily_qty, import_batch_id "
            "FROM pending_csv_import_rows WHERE lot_no = ?",
            (lot_no,),
        ).fetchall()
        for row in existing_rows:
            # 同一バッチ内の行は比較対象にしない（1回の取込内の行は常に
            # 全て新規追加する。内容が重複していても消さない）。
            if import_batch_id is not None and row["import_batch_id"] == import_batch_id:
                continue
            # 既に今回の取込内で別の行から再利用済みの既存行は、二重に
            # 再利用しない（claimed_pending_row_ids参照、本関数のdocstring）。
            if claimed_pending_row_ids is not None and row["pending_row_id"] in claimed_pending_row_ids:
                continue
            if (
                normalize_product_name(row["product_name"]) == product_name_normalized
                and row["report_date"] == report_date
                and row["daily_qty"] == daily_qty
            ):
                if claimed_pending_row_ids is not None:
                    claimed_pending_row_ids.add(row["pending_row_id"])
                return row["pending_row_id"]

        cur.execute("""
            INSERT INTO pending_csv_import_rows (
                import_batch_id, csv_row_no, lot_no, product_name, daily_qty,
                report_date, worker_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            import_batch_id,
            data.get("csv_row_no"),
            lot_no,
            product_name,
            daily_qty,
            report_date,
            data.get("worker_id"),
        ))
        pending_row_id = cur.lastrowid
        con.commit()
        return pending_row_id


def list_pending_csv_import_rows() -> list:
    """
    未処理の実績CSVステージング行を全件、pending_row_id昇順（取込・登録順）で返す。

    ui.production_import_staging_window（ステージング一覧の表示・再開）から
    呼ばれる想定で、テーブルが未作成のDB（一度もCSV取込を行っていない、
    または新規作成直後のDB）でも安全に呼べるよう、ここでは呼び出し頻度が
    低い（一覧を開くたびに1回）ことを踏まえてinit_csv_import_staging_tables()
    を呼ぶ（CSV行数分呼ばれるupsert_pending_csv_import_row()とは異なり、
    性能上の問題にはならない）。
    """
    init_csv_import_staging_tables()
    with get_connection() as con:
        cur = con.execute(
            "SELECT * FROM pending_csv_import_rows ORDER BY pending_row_id"
        )
        return [dict(row) for row in cur.fetchall()]


def delete_pending_csv_import_row(pending_row_id: int):
    """
    登録・除外が確定した保留行を物理削除する（履歴として残さない方針。
    本モジュールのdocstring参照）。呼び出し元はlist_pending_csv_import_rows()
    で取得済みのpending_row_idを渡す想定のため、テーブルの存在は保証済みで
    あり、ここでは初期化を呼ばない（models.kitting_plan.
    delete_pending_kitting_plan_item()と同じ考え方）。
    """
    with get_connection() as con:
        con.execute(
            "DELETE FROM pending_csv_import_rows WHERE pending_row_id = ?",
            (pending_row_id,),
        )
        con.commit()
