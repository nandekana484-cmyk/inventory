# models/external_actual_rows.py
"""
月次DB の external_actual_batches・external_actual_rows（現場実績入力アプリの実績CSV。登録待ちと登録済み）。
表は db/schema.sql（新しく作る DB）とマイグレーション 015（既存の DB）で作る。ここでは作らない。
同じ計画（キッティングNo.＋ロットNo.）の登録待ちは1行だけ。再送は、古い登録待ちを消して新しい行を入れる。登録済みの行は消さない。
"""
from models.db_common import get_connection, table_exists

TABLE_NAMES = ("external_actual_batches", "external_actual_rows")

_ROW_COLUMNS = (
    "csv_row_no", "kitting_list_no", "lot_no", "file_no", "side", "report_date", "daily_qty",
    "ng_side1", "ng_side2", "worker_id", "face1_kitting_list_no", "exported_at",
)


def get_db_user_version() -> int:
    """今の月次DB の PRAGMA user_version。"""
    with get_connection() as con:
        return con.execute("PRAGMA user_version").fetchone()[0]


def tables_exist() -> bool:
    with get_connection() as con:
        return all(table_exists(con, name) for name in TABLE_NAMES)


def find_batch_by_sha256(file_sha256: str):
    """同じ内容のファイルを取り込み済みなら、そのバッチ（新しいものから1件）。無ければ None。"""
    with get_connection() as con:
        row = con.execute(
            "SELECT * FROM external_actual_batches WHERE file_sha256 = ? ORDER BY batch_id DESC LIMIT 1",
            (file_sha256,),
        ).fetchone()
        return dict(row) if row else None


def get_latest_exported_at_by_plan(plan_keys) -> dict:
    """{(キッティングNo., ロットNo.): 取り込み済みの行（登録待ち・登録済みとも）の最も新しい書き出し日時}。行が無い計画は含めない。"""
    plan_keys = list(plan_keys)
    result = {}
    with get_connection() as con:
        for start in range(0, len(plan_keys), 400):
            chunk = plan_keys[start:start + 400]
            clause = " OR ".join("(kitting_list_no = ? AND lot_no = ?)" for _ in chunk)
            params = [value for key in chunk for value in key]
            for row in con.execute(
                f"SELECT kitting_list_no, lot_no, MAX(exported_at) AS latest FROM external_actual_rows "
                f"WHERE {clause} GROUP BY kitting_list_no, lot_no", params,
            ):
                result[(row["kitting_list_no"], row["lot_no"])] = row["latest"]
    return result


def import_batch(batch: dict, rows: list, imported_by: str) -> dict:
    """
    バッチ1件と、その行を1つのトランザクションで保存する（途中で例外が出れば、どれも反映されない）。
    各行について、同じ計画の登録待ちがあれば消してから新しい行を入れる。
    batch のキー: source_file・file_sha256・exported_at・source_db_name・target_db_name。rows の各行は _ROW_COLUMNS のキーを持つ。
    戻り値: {"batch_id", "inserted", "replaced": [(キッティングNo., ロットNo.), ...]}
    """
    with get_connection() as con:
        cur = con.execute(
            "INSERT INTO external_actual_batches (source_file, file_sha256, exported_at, source_db_name, target_db_name, imported_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (batch["source_file"], batch["file_sha256"], batch["exported_at"], batch["source_db_name"],
             batch["target_db_name"], imported_by),
        )
        batch_id = cur.lastrowid
        replaced = []
        for row in rows:
            deleted = con.execute(
                "DELETE FROM external_actual_rows WHERE kitting_list_no = ? AND lot_no = ? AND status = 'pending'",
                (row["kitting_list_no"], row["lot_no"]),
            ).rowcount
            if deleted:
                replaced.append((row["kitting_list_no"], row["lot_no"]))
            con.execute(
                f"INSERT INTO external_actual_rows (batch_id, {', '.join(_ROW_COLUMNS)}) "
                f"VALUES (?, {', '.join('?' for _ in _ROW_COLUMNS)})",
                (batch_id, *(row[name] for name in _ROW_COLUMNS)),
            )
        con.commit()
        return {"batch_id": batch_id, "inserted": len(rows), "replaced": replaced}


def list_pending_rows() -> list:
    """登録待ちの行を、バッチの情報（target_db_name・source_file・imported_at）を付けて、取り込み順に返す。"""
    with get_connection() as con:
        rows = con.execute("""
            SELECT r.*, b.target_db_name, b.source_file, b.imported_at
            FROM external_actual_rows AS r JOIN external_actual_batches AS b ON b.batch_id = r.batch_id
            WHERE r.status = 'pending'
            ORDER BY r.row_id
        """).fetchall()
        return [dict(r) for r in rows]


def get_pending_row(row_id: int):
    """row_id の行を、バッチの情報を付けて返す（登録待ちでなくても返す。無ければ None）。"""
    with get_connection() as con:
        row = con.execute("""
            SELECT r.*, b.target_db_name, b.source_file, b.imported_at
            FROM external_actual_rows AS r JOIN external_actual_batches AS b ON b.batch_id = r.batch_id
            WHERE r.row_id = ?
        """, (row_id,)).fetchone()
        return dict(row) if row else None


def mark_row_registered(row_id: int, registered_by: str) -> bool:
    """登録待ちの行を登録済みにする。すでに登録済み（または無い）ときは何も変えず False。"""
    with get_connection() as con:
        changed = con.execute(
            "UPDATE external_actual_rows SET status = 'registered', registered_at = datetime('now','localtime'), "
            "registered_by = ? WHERE row_id = ? AND status = 'pending'",
            (registered_by, row_id),
        ).rowcount
        con.commit()
        return changed == 1


def count_pending_rows() -> int:
    """登録待ちの行の数。表が無い DB（版 15 より前）では 0。"""
    with get_connection() as con:
        if not table_exists(con, "external_actual_rows"):
            return 0
        return con.execute("SELECT COUNT(*) FROM external_actual_rows WHERE status = 'pending'").fetchone()[0]
