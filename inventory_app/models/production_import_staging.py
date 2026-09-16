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

識別キー：(lot_no, 正規化済みproduct_name)。異なるタイミングで取り込まれた
CSVに同一キーの行が含まれる場合、古い保留行を削除してから新しい内容を
挿入する（delete-then-insert。models.kitting_plan.
upsert_pending_kitting_plan_item()と同じ「後から取り込んだ方が優先される」
考え方。production_daily自体も同一計画は常に上書きする方針のため、
ステージング段階でもこれに揃えた）。
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


def upsert_pending_csv_import_row(data: dict, import_batch_id: int = None) -> int:
    """
    実績CSVの1行をpending_csv_import_rowsへ保存する。

    識別キー (lot_no, 正規化済みproduct_name) が一致する既存の保留行が
    あれば削除してから新しい内容を挿入する（delete-then-insert。
    本モジュールのdocstring参照）。lot_noは呼び出し元
    （parse_production_csv_for_staging()）で既にstr().strip()済みの値を
    渡す想定。product_nameの正規化はservices.production_import_service.
    normalize_product_name()を使う（本モジュールが逆方向にservicesを
    トップレベルでインポートすると循環importになるため、models.kitting_plan.
    find_matching_plan_items()と同じく関数内インポートで回避する）。

    data：{"csv_row_no", "lot_no", "product_name", "daily_qty",
           "report_date", "worker_id"}。未知のキーは無視する。

    テーブルの存在保証：呼び出し元がループに入る前にcreate_csv_import_batch()
    （内部でinit_csv_import_staging_tables()を呼ぶ）を実行済みのため、
    ここで毎回初期化を呼ぶ必要はない（models.kitting_plan.
    upsert_pending_kitting_plan_item()と同じ理由。CSV行ごとに冗長な
    CREATE TABLE/INDEX IF NOT EXISTS文を再実行するとボトルネックになる
    ため、あえて呼ばない）。

    戻り値：保存した行のpending_row_id。
    """
    from services.production_import_service import normalize_product_name

    lot_no = str(data.get("lot_no") or "").strip()
    product_name = data.get("product_name")
    product_name_normalized = normalize_product_name(product_name)

    with get_connection() as con:
        cur = con.cursor()

        existing_rows = cur.execute(
            "SELECT pending_row_id, product_name FROM pending_csv_import_rows WHERE lot_no = ?",
            (lot_no,),
        ).fetchall()
        for row in existing_rows:
            if normalize_product_name(row["product_name"]) == product_name_normalized:
                cur.execute(
                    "DELETE FROM pending_csv_import_rows WHERE pending_row_id = ?",
                    (row["pending_row_id"],),
                )

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
            data.get("daily_qty"),
            data.get("report_date"),
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
