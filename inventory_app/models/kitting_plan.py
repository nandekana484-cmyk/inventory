import os
import sqlite3

import config
from models.production import get_app_cumulative_qty


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_kitting_plan_tables():
    """初回起動時に計画バッチ/明細テーブルを作成する（既存があれば何もしない）。"""
    with get_connection() as con:
        cur = con.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS kitting_plan_batches (
                plan_batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file TEXT NOT NULL,
                imported_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                imported_by TEXT,
                row_count INTEGER DEFAULT 0,
                delete_flag INTEGER DEFAULT 0
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS kitting_plan_items (
                plan_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_batch_id INTEGER NOT NULL,
                kitting_list_no TEXT NOT NULL,
                delete_flag INTEGER DEFAULT 0,
                setup_file_no TEXT,
                lot_no TEXT,
                mounting_line TEXT,
                board_name TEXT,
                planned_qty REAL DEFAULT 0,
                cumulative_qty_external REAL DEFAULT 0,
                order_qty REAL DEFAULT 0,
                production_side TEXT,
                status TEXT,
                plan_start_datetime TEXT,
                plan_end_datetime TEXT,
                deadline TEXT,
                actual_start_datetime TEXT,
                actual_end_datetime TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime')),
                version INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                previous_plan_item_id INTEGER,
                created_by TEXT,
                FOREIGN KEY (plan_batch_id) REFERENCES kitting_plan_batches(plan_batch_id)
            )
        """)

        # 部分ユニークインデックス：同一(kitting_list_no, lot_no)でis_active=1は1件のみ
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_kitting_plan_items_active_kitting_lot
            ON kitting_plan_items(kitting_list_no, COALESCE(lot_no, ''))
            WHERE COALESCE(is_active, 1) = 1
        """)

        con.commit()


def create_plan_batch(source_file: str, imported_by: str, row_count: int) -> int:
    init_kitting_plan_tables()
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO kitting_plan_batches (source_file, imported_by, row_count)
            VALUES (?, ?, ?)
        """, (source_file, imported_by, row_count))
        con.commit()
        return cur.lastrowid


def upsert_plan_item(plan_batch_id, data: dict):
    """
    旧API互換用ラッパー。
    バージョン方式に合わせ、既存行を更新せず新しいバージョンを作成する。
    """
    kitting_list_no = data.get("kitting_list_no")
    if not kitting_list_no:
        raise ValueError("data['kitting_list_no'] は必須です。")

    return create_plan_version(
        plan_batch_id=plan_batch_id,
        kitting_list_no=kitting_list_no,
        data=data,
        created_by=data.get("created_by"),
    )


def find_plan_item_by_kitting_no(kitting_list_no: str, lot_no: str = None):
    """互換関数。最新版を取得。lot_no 指定可能。"""
    return get_latest_plan_by_kitting_no(kitting_list_no, lot_no)


def list_plan_items_by_lot(lot_no: str):
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM kitting_plan_items WHERE lot_no = ? AND delete_flag = 0
        """, (lot_no,))
        return [dict(r) for r in cur.fetchall()]


def list_plan_batches(include_deleted: bool = False):
    """
    バッチ一覧を取得。include_deleted=False で delete_flag=1 のバッチは除外。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(kitting_plan_batches)")
        cols = [c[1] for c in cur.fetchall()]
        if "delete_flag" in cols:
            if include_deleted:
                cur.execute("""
                    SELECT plan_batch_id, source_file, imported_at, imported_by, row_count,
                           COALESCE(delete_flag,0) AS delete_flag
                    FROM kitting_plan_batches
                    ORDER BY imported_at DESC
                """)
            else:
                cur.execute("""
                    SELECT plan_batch_id, source_file, imported_at, imported_by, row_count,
                           COALESCE(delete_flag,0) AS delete_flag
                    FROM kitting_plan_batches
                    WHERE COALESCE(delete_flag,0) = 0
                    ORDER BY imported_at DESC
                """)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        else:
            cur.execute("""
                SELECT plan_batch_id, source_file, imported_at, imported_by, row_count
                FROM kitting_plan_batches
                ORDER BY imported_at DESC
            """)
            rows = cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["delete_flag"] = 0
                result.append(d)
            return result


def mark_batch_deleted(plan_batch_id: int, deleted: bool = True):
    """
    バッチのソフト削除または復活
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(kitting_plan_batches)")
        cols = [c[1] for c in cur.fetchall()]
        if "delete_flag" not in cols:
            try:
                cur.execute("ALTER TABLE kitting_plan_batches ADD COLUMN delete_flag INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
        cur.execute("UPDATE kitting_plan_batches SET delete_flag = ? WHERE plan_batch_id = ?",
                    (1 if deleted else 0, plan_batch_id))
        con.commit()


def get_latest_plan_by_kitting_no(kitting_list_no: str, lot_no: str = None):
    """
    kitting_list_no (および optional lot_no) に対する最新版（is_active=1）を返す。
    """
    if not kitting_list_no:
        return None

    with get_connection() as con:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(kitting_plan_items)")
        cols = [c[1] for c in cur.fetchall()]

        if "version" in cols and "is_active" in cols:
            if lot_no is not None:
                cur.execute("""
                    SELECT * FROM kitting_plan_items
                    WHERE kitting_list_no = ?
                      AND COALESCE(lot_no, '') = ?
                      AND COALESCE(is_active, 1) = 1
                    ORDER BY version DESC, plan_item_id DESC
                    LIMIT 1
                """, (kitting_list_no, str(lot_no).strip()))
            else:
                cur.execute("""
                    SELECT * FROM kitting_plan_items
                    WHERE kitting_list_no = ?
                      AND COALESCE(is_active, 1) = 1
                    ORDER BY version DESC, plan_item_id DESC
                    LIMIT 1
                """, (kitting_list_no,))
        else:
            if lot_no is not None:
                cur.execute("""
                    SELECT * FROM kitting_plan_items
                    WHERE kitting_list_no = ?
                      AND COALESCE(lot_no, '') = ?
                    ORDER BY updated_at DESC, plan_item_id DESC
                    LIMIT 1
                """, (kitting_list_no, str(lot_no).strip()))
            else:
                cur.execute("""
                    SELECT * FROM kitting_plan_items
                    WHERE kitting_list_no = ?
                    ORDER BY updated_at DESC, plan_item_id DESC
                    LIMIT 1
                """, (kitting_list_no,))

        row = cur.fetchone()
        return dict(row) if row else None


def get_latest_plan(kitting_list_no: str, lot_no: str = None):
    return get_latest_plan_by_kitting_no(kitting_list_no, lot_no)


def list_active_plan_items(kitting_list_no: str = None, lot_no: str = None):
    """
    実績入力用：現在アクティブな計画を一覧で返す（検索は部分一致）
    """
    sql = """
        SELECT *
        FROM kitting_plan_items
        WHERE COALESCE(is_active, 1) = 1
    """
    params = []

    if kitting_list_no:
        sql += " AND kitting_list_no LIKE ?"
        params.append(f"%{kitting_list_no.strip()}%")
    if lot_no:
        sql += " AND COALESCE(lot_no, '') LIKE ?"
        params.append(f"%{lot_no.strip()}%")

    sql += " ORDER BY kitting_list_no, lot_no, version DESC, plan_item_id DESC"

    with get_connection() as con:
        cur = con.cursor()
        cur.execute(sql, params)
        plan_items = [dict(row) for row in cur.fetchall()]

    # (lot_no, setup_file_no) 単位で「2回目」計画が存在するかどうかを事前に把握する
    second_side_keys = set()
    for item in plan_items:
        if str(item.get("production_side")).strip() == "2":
            second_side_keys.add((item.get("lot_no"), item.get("setup_file_no")))

    result = []
    for item in plan_items:
        order_qty = item.get("order_qty") or 0
        actual_qty = get_app_cumulative_qty(item["kitting_list_no"])
        if actual_qty >= order_qty:
            # 実績が発注数に到達済み＝完了扱いのため一覧から除外
            continue

        production_side = str(item.get("production_side")).strip()
        key = (item.get("lot_no"), item.get("setup_file_no"))
        if production_side == "1" and key in second_side_keys:
            # 同一ロット・file_no に2回目計画がある場合、1回目は完成品ではないため除外
            continue

        result.append(item)

    return result


def create_plan_version(
    plan_batch_id: int,
    kitting_list_no: str,
    data: dict,
    created_by: str = None,
) -> int:
    """
    kitting_list_no + lot_no 単位で新しい計画バージョンを作成する。
    - data は 'lot_no' を含むことを想定
    - 実在する列のみで INSERT を動的に組み立てる
    """
    if not kitting_list_no or not str(kitting_list_no).strip():
        raise ValueError("kitting_list_no は必須です。")

    kitting_list_no = str(kitting_list_no).strip()
    raw_lot_no = data.get("lot_no")
    lot_no = "" if raw_lot_no is None else str(raw_lot_no).strip()

    with get_connection() as con:
        cur = con.cursor()

        cur.execute("PRAGMA table_info(kitting_plan_items)")
        db_columns = {r[1] for r in cur.fetchall()}

        required_columns = {
            "plan_batch_id",
            "kitting_list_no",
            "lot_no",
            "version",
            "is_active",
            "previous_plan_item_id",
        }
        missing_columns = required_columns - db_columns
        if missing_columns:
            raise RuntimeError(
                "kitting_plan_items の必須列が不足しています: "
                + ", ".join(sorted(missing_columns))
            )

        previous = cur.execute("""
            SELECT plan_item_id, version
            FROM kitting_plan_items
            WHERE kitting_list_no = ?
              AND COALESCE(lot_no, '') = ?
            ORDER BY COALESCE(version, 0) DESC, plan_item_id DESC
            LIMIT 1
        """, (kitting_list_no, lot_no)).fetchone()

        previous_plan_item_id = previous["plan_item_id"] if previous else None
        previous_version = previous["version"] if previous else 0

        try:
            new_version = int(previous_version or 0) + 1
        except (TypeError, ValueError):
            new_version = 1

        insert_data = {
            "plan_batch_id": plan_batch_id,
            "kitting_list_no": kitting_list_no,
            "delete_flag": data.get("delete_flag", 0),
            "setup_file_no": data.get("setup_file_no"),
            "lot_no": lot_no,
            "mounting_line": data.get("mounting_line"),
            "board_name": data.get("board_name"),
            "planned_qty": data.get("planned_qty", 0),
            "cumulative_qty_external": data.get("cumulative_qty_external", 0),
            "order_qty": data.get("order_qty", 0),
            "production_side": data.get("production_side"),
            "status": data.get("status"),
            "plan_start_datetime": data.get("plan_start_datetime"),
            "plan_end_datetime": data.get("plan_end_datetime"),
            "deadline": data.get("deadline"),
            "actual_start_datetime": data.get("actual_start_datetime"),
            "actual_end_datetime": data.get("actual_end_datetime"),
            "version": new_version,
            "is_active": 1,
            "previous_plan_item_id": previous_plan_item_id,
            "created_by": created_by,
        }

        columns = [name for name in insert_data if name in db_columns]
        values = [insert_data[name] for name in columns]
        placeholders = ", ".join("?" for _ in columns)

        if len(columns) != len(values):
            raise RuntimeError(
                "INSERT列数と値数が不一致です: "
                f"columns={len(columns)}, values={len(values)}"
            )

        sql = f"INSERT INTO kitting_plan_items ({', '.join(columns)}) VALUES ({placeholders})"

        if os.getenv("KITTING_IMPORT_DEBUG") == "1":
            print(
                "[create_plan_version] "
                f"kitting_list_no={kitting_list_no!r}, lot_no={lot_no!r}, "
                f"version={new_version}, columns_count={len(columns)}, values_count={len(values)}"
            )
            print("[create_plan_version] SQL:", sql)

        # 同一 (kitting_list_no, lot_no) の旧アクティブ版を無効化
        cur.execute("""
            UPDATE kitting_plan_items
            SET is_active = 0, updated_at = datetime('now', 'localtime')
            WHERE kitting_list_no = ?
              AND COALESCE(lot_no, '') = ?
              AND COALESCE(is_active, 1) = 1
        """, (kitting_list_no, lot_no))

        # 新バージョンを登録
        cur.execute(sql, values)
        new_plan_item_id = cur.lastrowid
        con.commit()
        return new_plan_item_id


def find_matching_plan_items(lot_no: str, product_name_normalized: str):
    """
    実績CSV自動取込（services.production_import_service）用の内部ヘルパー。
    lot_no + 正規化済み製品名(product_name_normalized) から、候補となる
    現在アクティブな計画（kitting_plan_items）を探す。

    resolve_plan_by_lot_and_name() の一致判定そのものに使われるほか、
    production_import_service 側で未一致の理由（計画なし／製品名ゆらぎ／複数候補あり）
    を判別する際にも同じロジックを使うために公開関数としている。

    戻り値：(lot_no が一致する現在アクティブな計画一覧, その中で製品名も一致する計画一覧)
    """
    # services.production_import_service は本モジュールの resolve_plan_by_lot_and_name /
    # find_matching_plan_items をインポートしているため、モジュールトップレベルで
    # 逆方向にインポートすると循環importになる。関数内インポートで回避する。
    from services.production_import_service import normalize_product_name

    candidates = [
        item for item in list_active_plan_items()
        if str(item.get("lot_no") or "").strip() == lot_no
    ]

    # 一致判定：
    #   1. 完全一致／2. 正規化一致：
    #      本関数の入力（product_name_normalized）は既に正規化済みの文字列のみのため、
    #      board_name 側を normalize_product_name() で正規化した上での比較は
    #      「完全一致」と「正規化一致」が実質的に同一の判定になる。
    #   3. あいまい一致（部分一致など）：
    #      どこまで許容するかは仕様未確定のため決め打ちしない。
    #      TODO：あいまい一致の仕様が確定した場合、ここに追加の候補抽出ロジックを実装すること
    #      （例：matched が空の場合に candidates に対して部分一致・編集距離等でフォールバック）。
    matched = [
        item for item in candidates
        if normalize_product_name(item.get("board_name")) == product_name_normalized
    ]

    return candidates, matched


def resolve_plan_by_lot_and_name(lot_no: str, product_name_normalized: str):
    """
    lot_no + 正規化済み製品名(product_name_normalized) から計画を一意に特定し、
    kitting_list_no を返す（実績CSV自動取込用）。

    一致するアクティブな計画の kitting_list_no が1種類のみに定まれば、その値を返す。
    0件、または複数の異なる kitting_list_no に一致する場合（曖昧）は None を返す。
    未一致の理由を区別したい場合は find_matching_plan_items() を利用すること。
    """
    _, matched = find_matching_plan_items(lot_no, product_name_normalized)

    unique_kitting_nos = {item["kitting_list_no"] for item in matched}
    if len(unique_kitting_nos) == 1:
        return matched[0]["kitting_list_no"]
    return None
