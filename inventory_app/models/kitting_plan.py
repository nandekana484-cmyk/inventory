import os
import sqlite3
from datetime import datetime

from models.db_common import get_connection
from models.production import get_app_cumulative_qty_bulk, list_daily_production_by_kitting_no
from models.production_side_master import get_second_side_status_by_file_no

# 面1だけの計画（同じ (lot_no, setup_file_no) に面2の計画が無い）の分類（classify_side1_only_plan()）。
SIDE1_ONLY_CLASS_SINGLE_SIDE = "a"       # 片面の製品（生産面マスターで後行面なし）
SIDE1_ONLY_CLASS_WAITING_SIDE2 = "b"     # 面2待ち（生産面マスターで後行面あり）
SIDE1_ONLY_CLASS_UNREGISTERED = "c"      # 生産面マスター未登録

SIDE1_ONLY_CLASS_LABELS = {
    SIDE1_ONLY_CLASS_SINGLE_SIDE: "片面の製品",
    SIDE1_ONLY_CLASS_WAITING_SIDE2: "面2待ち",
    SIDE1_ONLY_CLASS_UNREGISTERED: "生産面マスター未登録",
}


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

        # list_plan_items_by_lot() の WHERE lot_no = ? 用（migration_007 と同じ。新しい DB にも作るため、ここでも作成する）。
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_kitting_plan_items_lot_no
            ON kitting_plan_items(lot_no)
        """)

        # キッティングNo.が未確定のまま取り込んだ計画行の一時保存（import_kitting_plan_csv() が使う）。
        # 識別キー (lot_no, setup_file_no, production_side, order_qty) が後日の取込で一致したら、正式な計画へ移す。
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_kitting_plan_items (
                pending_id INTEGER PRIMARY KEY AUTOINCREMENT,
                lot_no TEXT,
                setup_file_no TEXT,
                production_side TEXT,
                order_qty REAL DEFAULT 0,
                mounting_line TEXT,
                board_name TEXT,
                planned_qty REAL DEFAULT 0,
                cumulative_qty_external REAL DEFAULT 0,
                status TEXT,
                plan_start_datetime TEXT,
                plan_end_datetime TEXT,
                deadline TEXT,
                actual_start_datetime TEXT,
                actual_end_datetime TEXT,
                delete_flag INTEGER DEFAULT 0,
                source_file TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime')),
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)

        # 識別キーの重複防止の保険（本体は SELECT してから UPDATE/INSERT を判定するので、ON CONFLICT には使わない）。
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_pending_kitting_plan_items_identity
            ON pending_kitting_plan_items(
                COALESCE(lot_no, ''), COALESCE(setup_file_no, ''),
                COALESCE(production_side, ''), COALESCE(order_qty, 0)
            )
        """)

        con.commit()


def upsert_pending_kitting_plan_item(data: dict, created_by: str = None) -> int:
    """
    キッティングNo.が未確定の計画行を保存する。識別キーが一致する行があれば UPDATE、無ければ INSERT する。戻り値は pending_id。
    テーブルの作成は呼び出し元に任せる（import_kitting_plan_csv() がループの前に create_plan_batch() で行う。
    CSV の行ごとに CREATE TABLE IF NOT EXISTS を実行すると遅いため）。
    """
    lot_no = data.get("lot_no")
    setup_file_no = data.get("setup_file_no")
    production_side = data.get("production_side")
    order_qty = data.get("order_qty") or 0

    values = (
        data.get("mounting_line"),
        data.get("board_name"),
        data.get("planned_qty") or 0,
        data.get("cumulative_qty_external") or 0,
        data.get("status"),
        data.get("plan_start_datetime"),
        data.get("plan_end_datetime"),
        data.get("deadline"),
        data.get("actual_start_datetime"),
        data.get("actual_end_datetime"),
        data.get("delete_flag") or 0,
        data.get("source_file"),
        created_by,
    )

    with get_connection() as con:
        cur = con.cursor()
        existing = cur.execute("""
            SELECT pending_id FROM pending_kitting_plan_items
            WHERE COALESCE(lot_no, '') = COALESCE(?, '')
              AND COALESCE(setup_file_no, '') = COALESCE(?, '')
              AND COALESCE(production_side, '') = COALESCE(?, '')
              AND COALESCE(order_qty, 0) = COALESCE(?, 0)
        """, (lot_no, setup_file_no, production_side, order_qty)).fetchone()

        if existing:
            pending_id = existing["pending_id"]
            cur.execute("""
                UPDATE pending_kitting_plan_items SET
                    mounting_line = ?, board_name = ?, planned_qty = ?,
                    cumulative_qty_external = ?, status = ?, plan_start_datetime = ?,
                    plan_end_datetime = ?, deadline = ?, actual_start_datetime = ?,
                    actual_end_datetime = ?, delete_flag = ?, source_file = ?,
                    created_by = ?, updated_at = datetime('now', 'localtime')
                WHERE pending_id = ?
            """, values + (pending_id,))
        else:
            cur.execute("""
                INSERT INTO pending_kitting_plan_items (
                    lot_no, setup_file_no, production_side, order_qty,
                    mounting_line, board_name, planned_qty, cumulative_qty_external,
                    status, plan_start_datetime, plan_end_datetime, deadline,
                    actual_start_datetime, actual_end_datetime, delete_flag,
                    source_file, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (lot_no, setup_file_no, production_side, order_qty) + values)
            pending_id = cur.lastrowid

        con.commit()
        return pending_id


def find_pending_kitting_plan_item(lot_no, setup_file_no, production_side, order_qty):
    """
    識別キーが一致する保留行を1件返す（無ければ None）。キッティングNo.付きの行が、以前の「未確定」の確定かを判定するのに使う。
    テーブルの作成は呼び出し元に任せる（upsert_pending_kitting_plan_item() と同じ）。
    """
    with get_connection() as con:
        row = con.execute("""
            SELECT * FROM pending_kitting_plan_items
            WHERE COALESCE(lot_no, '') = COALESCE(?, '')
              AND COALESCE(setup_file_no, '') = COALESCE(?, '')
              AND COALESCE(production_side, '') = COALESCE(?, '')
              AND COALESCE(order_qty, 0) = COALESCE(?, 0)
        """, (lot_no, setup_file_no, production_side, order_qty)).fetchone()
        return dict(row) if row else None


def delete_pending_kitting_plan_item(pending_id: int):
    """保留行を1件削除する（確定登録の後に呼ぶ）。テーブルの作成は呼び出し元に任せる。"""
    with get_connection() as con:
        con.execute("DELETE FROM pending_kitting_plan_items WHERE pending_id = ?", (pending_id,))
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
    """
    lot_no の現在アクティブな計画行（is_active=1）を返す。以前は is_active を見ておらず、旧バージョンの行も混ざっていた。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM kitting_plan_items
            WHERE lot_no = ? AND delete_flag = 0 AND COALESCE(is_active, 1) = 1
        """, (lot_no,))
        return [dict(r) for r in cur.fetchall()]


def list_plan_items_for_all_lots():
    """
    list_plan_items_by_lot() と同じ条件で、全ロット分を1回の SELECT で返す（ロットごとに SELECT する N+1 を避けるため）。
    lot_no が NULL・空の行は除く（ロット単位の完成数が成り立たない）。用途に必要な列だけを返す。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT kitting_list_no, lot_no, setup_file_no, production_side, order_qty, board_name
            FROM kitting_plan_items
            WHERE delete_flag = 0 AND COALESCE(is_active, 1) = 1
              AND lot_no IS NOT NULL AND lot_no != ''
        """)
        return [dict(r) for r in cur.fetchall()]


def find_opposite_side_plan(lot_no: str, setup_file_no: str, current_side,
                              current_plan_start_datetime: str = None):
    """
    同じ lot_no・setup_file_no で、反対側の面の現在アクティブな計画を1件返す（無ければ None）。
    日付違いの複数バッチがある場合は、current_plan_start_datetime に最も近いもの（無いか解釈できなければ最も古いもの）。
    """
    opposite_side = "2" if str(current_side).strip() == "1" else "1"

    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM kitting_plan_items
            WHERE COALESCE(lot_no, '') = ?
              AND COALESCE(setup_file_no, '') = ?
              AND COALESCE(production_side, '') = ?
              AND COALESCE(is_active, 1) = 1
        """, (lot_no or "", setup_file_no or "", opposite_side))
        candidates = [dict(row) for row in cur.fetchall()]

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def parse_dt(value):
        try:
            return datetime.strptime(value, "%Y/%m/%d %H:%M:%S")
        except (TypeError, ValueError):
            return None

    reference = parse_dt(current_plan_start_datetime)
    if reference is None:
        candidates.sort(key=lambda item: item.get("plan_start_datetime") or "")
        return candidates[0]

    def diff_seconds(item):
        dt = parse_dt(item.get("plan_start_datetime"))
        if dt is None:
            return float("inf")
        return abs((dt - reference).total_seconds())

    candidates.sort(key=diff_seconds)
    return candidates[0]


def list_active_side_plans_for_file(lot_no: str, setup_file_no: str) -> list:
    """
    同じ (lot_no, setup_file_no) の、現在アクティブな面1・面2の計画を全件返す。
    面1と面2の合計を比べる不整合判定用（find_opposite_side_plan() で1件に絞ると、複数バッチで誤った組を選んで誤検出した）。
    """
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("""
            SELECT * FROM kitting_plan_items
            WHERE COALESCE(lot_no, '') = ? AND COALESCE(setup_file_no, '') = ?
              AND production_side IN ('1', '2') AND COALESCE(is_active, 1) = 1
        """, (lot_no or "", setup_file_no or ""))
        return [dict(row) for row in cur.fetchall()]


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
    バッチのソフト削除・復元。一覧は kitting_plan_items.is_active しか見ないので、明細の is_active も同じトランザクションで更新する。
    - 削除: このバッチのアクティブな行を is_active=0 にする
    - 復元: 同じ (kitting_list_no, lot_no) にほかのアクティブな行（新しいバージョン）が無い行だけを戻す（無条件に戻すと、アクティブな行が重複する）
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

        if deleted:
            cur.execute("""
                UPDATE kitting_plan_items
                SET is_active = 0, updated_at = datetime('now', 'localtime')
                WHERE plan_batch_id = ?
                  AND COALESCE(is_active, 1) = 1
            """, (plan_batch_id,))
        else:
            cur.execute("""
                UPDATE kitting_plan_items
                SET is_active = 1, updated_at = datetime('now', 'localtime')
                WHERE plan_batch_id = ?
                  AND COALESCE(is_active, 1) = 0
                  AND NOT EXISTS (
                      SELECT 1 FROM kitting_plan_items AS other
                      WHERE other.kitting_list_no = kitting_plan_items.kitting_list_no
                        AND COALESCE(other.lot_no, '') = COALESCE(kitting_plan_items.lot_no, '')
                        AND COALESCE(other.is_active, 1) = 1
                  )
            """, (plan_batch_id,))

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


def list_active_plan_items_by_kitting_no(kitting_list_no: str) -> list:
    """
    kitting_list_no に一致する現在アクティブな計画を、lot_no で絞らずに全部返す（lot_no の候補が複数あるかを判定するため）。
    lot_no ごとにアクティブな行は最大1件なので、候補一覧として過不足が無い。is_active 列の無い古い DB では、一致する行をすべて返す。
    """
    if not kitting_list_no:
        return []

    with get_connection() as con:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(kitting_plan_items)")
        cols = [c[1] for c in cur.fetchall()]

        if "is_active" in cols:
            cur.execute("""
                SELECT * FROM kitting_plan_items
                WHERE kitting_list_no = ?
                  AND COALESCE(is_active, 1) = 1
                ORDER BY COALESCE(lot_no, '')
            """, (kitting_list_no,))
        else:
            cur.execute("""
                SELECT * FROM kitting_plan_items
                WHERE kitting_list_no = ?
                ORDER BY COALESCE(lot_no, '')
            """, (kitting_list_no,))

        return [dict(row) for row in cur.fetchall()]


def list_active_plan_items(kitting_list_no: str = None, lot_no: str = None,
                             include_completed: bool = False):
    """
    実績入力用に、現在アクティブな計画を返す（検索は部分一致）。各要素には完了判定に使った app_cumulative_qty を含めるので、再計算しないこと。
    include_completed=True なら完了済みも返す。find_matching_plan_items()（実績CSV自動取込）は必ず既定（完了済みを除く）で呼ぶこと
    （完了済みと未完了が同じロット・製品名で複数あると、一意に特定できなくなる）。
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

        # 実績はまとめて取得する（N+1 回避）。組に lot_no を含めるのは、kitting_list_no が lot_no をまたいで重複し、
        # 別ロットの実績まで合算してしまうため（実データで完成数の取り違えを確認）。
        kitting_list_no_lot_pairs = [(item["kitting_list_no"], item["lot_no"]) for item in plan_items]
        cumulative_by_pair = get_app_cumulative_qty_bulk(kitting_list_no_lot_pairs, con=con)

    # (lot_no, setup_file_no) 単位で「2回目」計画が存在するかどうかを事前に把握する
    second_side_keys = set()
    for item in plan_items:
        if str(item.get("production_side")).strip() == "2":
            second_side_keys.add((item.get("lot_no"), item.get("setup_file_no")))

    result = []
    for item in plan_items:
        order_qty = item.get("order_qty") or 0
        actual_qty = cumulative_by_pair[(item["kitting_list_no"], item["lot_no"])]
        if not include_completed and actual_qty >= order_qty:
            # 実績が発注数に到達済み＝完了扱いのため一覧から除外
            continue

        production_side = str(item.get("production_side")).strip()
        key = (item.get("lot_no"), item.get("setup_file_no"))
        if production_side == "1" and key in second_side_keys:
            # 同一ロット・file_no に2回目計画がある場合、1回目は完成品ではないため除外
            continue

        item["app_cumulative_qty"] = actual_qty
        result.append(item)

    return result


def classify_side1_only_plan(plan_item: dict):
    """
    面1だけの計画（同じ (lot_no, setup_file_no) に面2の計画が無い）を、生産面マスターで分類する（D-9x）。
    a: 後行面なしの登録がある（片面の製品）  b: 後行面ありの登録がある（面2待ち）  c: 登録が無い（生産面マスター未登録）
    面1でなければ None。list_active_plan_items() を通った面1には面2が無いことが保証されている（D-8）ので、マスターだけで分類できる。
    判定はファイルNo単位（実装ラインを問わない）。同じファイルNoで、先行面と後行面が別の実装ラインを流れることがあるため。
    """
    production_side = str(plan_item.get("production_side") or "").strip()
    if production_side != "1":
        return None

    status = get_second_side_status_by_file_no(plan_item.get("setup_file_no"))
    if status is True:
        return SIDE1_ONLY_CLASS_WAITING_SIDE2
    elif status is False:
        return SIDE1_ONLY_CLASS_SINGLE_SIDE
    else:
        return SIDE1_ONLY_CLASS_UNREGISTERED


def find_master_plan_discrepancies() -> list:
    """
    マスターでは後行面なし（1回目のみ）だが、計画データには面2の計画があるファイルNoを返す（ファイルNo単位。D-9x）。
    利用者が確認するための一覧で、判定には使わない（計画データを優先する方針 D-8 は変えない）。
    戻り値は [{"setup_file_no", "mounting_lines", "lot_nos"}, ...]（昇順）。
    """
    from models.production_side_master import list_production_side_file_statuses

    with get_connection() as con:
        plan_rows = [dict(r) for r in con.execute(
            "SELECT lot_no, setup_file_no, mounting_line FROM kitting_plan_items "
            "WHERE COALESCE(is_active,1)=1 AND production_side = '2'"
        )]

    side2_by_file = {}
    for row in plan_rows:
        info = side2_by_file.setdefault(row["setup_file_no"], {"mounting_lines": set(), "lot_nos": set()})
        info["mounting_lines"].add(row["mounting_line"])
        info["lot_nos"].add(row["lot_no"])

    file_statuses = list_production_side_file_statuses()

    discrepancies = []
    for setup_file_no, info in side2_by_file.items():
        if file_statuses.get(setup_file_no) is False:
            discrepancies.append({
                "setup_file_no": setup_file_no,
                "mounting_lines": sorted(info["mounting_lines"]),
                "lot_nos": sorted(info["lot_nos"]),
            })

    discrepancies.sort(key=lambda d: d["setup_file_no"])
    return discrepancies


def find_unregistered_production_side_file_nos() -> list:
    """
    計画データにあるが、生産面マスターにどの実装ラインも登録が無い setup_file_no を返す（D-9x）。
    classify_side1_only_plan() の「未登録」と同じ基準。CSV 出力の元データにも使う。戻り値は [{"setup_file_no"}, ...]（昇順）。
    """
    from models.production_side_master import normalize_setup_file_no, get_second_side_status_by_file_no

    with get_connection() as con:
        file_nos = {
            normalize_setup_file_no(r[0])
            for r in con.execute(
                "SELECT DISTINCT setup_file_no FROM kitting_plan_items WHERE COALESCE(is_active,1)=1"
            )
        }

    result = [
        {"setup_file_no": fn} for fn in file_nos
        if get_second_side_status_by_file_no(fn) is None
    ]
    result.sort(key=lambda r: r["setup_file_no"])
    return result


def find_registered_file_nos_missing_line_combinations() -> list:
    """
    参考情報（判定には使わない。D-9x）: ファイルNoはマスターに登録があるが、計画データ上のこの実装ラインには登録が無い組を返す。
    戻り値は [{"setup_file_no", "mounting_line"}, ...]（昇順）。
    """
    from models.production_side_master import (
        normalize_setup_file_no, normalize_mounting_line,
        get_second_side_status, get_second_side_status_by_file_no,
    )

    with get_connection() as con:
        plan_rows = [dict(r) for r in con.execute(
            "SELECT DISTINCT setup_file_no, mounting_line FROM kitting_plan_items WHERE COALESCE(is_active,1)=1"
        )]

    seen = set()
    result = []
    for row in plan_rows:
        norm_file = normalize_setup_file_no(row["setup_file_no"])
        norm_line = normalize_mounting_line(row["mounting_line"])
        key = (norm_file, norm_line)
        if key in seen:
            continue
        seen.add(key)
        if get_second_side_status_by_file_no(norm_file) is None:
            continue
        if get_second_side_status(norm_file, norm_line) is None:
            result.append({"setup_file_no": norm_file, "mounting_line": norm_line})

    result.sort(key=lambda r: (r["setup_file_no"], r["mounting_line"]))
    return result


def list_mounting_lines_by_file_no(file_nos) -> dict:
    """
    setup_file_no ごとに、計画データにある実装ラインの一覧を返す（D-9x。未登録のファイルNoの CSV 出力で、登録すべきラインを示すため）。
    """
    from models.production_side_master import normalize_setup_file_no, normalize_mounting_line

    norm_targets = {normalize_setup_file_no(fn) for fn in file_nos}
    if not norm_targets:
        return {}

    with get_connection() as con:
        plan_rows = [dict(r) for r in con.execute(
            "SELECT DISTINCT setup_file_no, mounting_line FROM kitting_plan_items WHERE COALESCE(is_active,1)=1"
        )]

    result = {}
    for row in plan_rows:
        norm_file = normalize_setup_file_no(row["setup_file_no"])
        if norm_file not in norm_targets:
            continue
        norm_line = normalize_mounting_line(row["mounting_line"])
        result.setdefault(norm_file, set()).add(norm_line)

    return {fn: sorted(lines) for fn, lines in result.items()}


def find_all_hidden_side1_plans_with_production() -> list:
    """
    面2の計画があるため一覧から隠れている面1の計画のうち、実績が登録済みのものを全件返す（D-9x）。
    実績の付け替えは自動では行わない（利用者の方針）。今の状態をいつでも確認するための一覧。
    """
    with get_connection() as con:
        all_items = [dict(r) for r in con.execute(
            "SELECT lot_no, setup_file_no, mounting_line, production_side, kitting_list_no "
            "FROM kitting_plan_items WHERE COALESCE(is_active,1)=1"
        )]

    groups = {}
    for item in all_items:
        key = (item["lot_no"], item["setup_file_no"])
        groups.setdefault(key, []).append(item)

    results = []
    for (lot_no, setup_file_no), items in groups.items():
        side1_items = [i for i in items if str(i["production_side"]).strip() == "1"]
        side2_items = [i for i in items if str(i["production_side"]).strip() == "2"]
        if not side1_items or not side2_items:
            continue
        for side1 in side1_items:
            production = list_daily_production_by_kitting_no(side1["kitting_list_no"], lot_no)
            if not production:
                continue
            for side2 in side2_items:
                results.append({
                    "lot_no": lot_no, "setup_file_no": setup_file_no,
                    "mounting_line": side1.get("mounting_line"),
                    "side1_kitting_list_no": side1["kitting_list_no"],
                    "side2_kitting_list_no": side2["kitting_list_no"],
                    "production_rows": [
                        {"report_date": p["report_date"], "daily_qty": p["daily_qty"]} for p in production
                    ],
                })

    results.sort(key=lambda r: (r["lot_no"], r["setup_file_no"]))
    return results


def find_newly_hidden_side1_plans(plan_batch_id: int) -> list:
    """
    今回の計画CSV取込で追加した面2の計画によって、実績のある面1の計画が隠れることになった組を返す（取込完了時の通知用。D-9x）。
    戻り値の形は find_all_hidden_side1_plans_with_production() と同じ。
    """
    with get_connection() as con:
        new_side2_items = [dict(r) for r in con.execute(
            "SELECT lot_no, setup_file_no, mounting_line, kitting_list_no FROM kitting_plan_items "
            "WHERE plan_batch_id = ? AND production_side = '2' AND COALESCE(is_active,1)=1",
            (plan_batch_id,),
        )]

    results = []
    for side2 in new_side2_items:
        with get_connection() as con:
            side1_items = [dict(r) for r in con.execute(
                "SELECT kitting_list_no FROM kitting_plan_items "
                "WHERE lot_no = ? AND setup_file_no = ? AND production_side = '1' AND COALESCE(is_active,1)=1",
                (side2["lot_no"], side2["setup_file_no"]),
            )]
        for side1 in side1_items:
            production = list_daily_production_by_kitting_no(side1["kitting_list_no"], side2["lot_no"])
            if not production:
                continue
            results.append({
                "lot_no": side2["lot_no"], "setup_file_no": side2["setup_file_no"],
                "mounting_line": side2.get("mounting_line"),
                "side1_kitting_list_no": side1["kitting_list_no"],
                "side2_kitting_list_no": side2["kitting_list_no"],
                "production_rows": [
                    {"report_date": p["report_date"], "daily_qty": p["daily_qty"]} for p in production
                ],
            })

    results.sort(key=lambda r: (r["lot_no"], r["setup_file_no"]))
    return results


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


def find_matching_plan_items(lot_no: str, product_name_normalized: str, plan_items_by_lot: dict = None):
    """
    実績CSV自動取込用に、lot_no と正規化済みの製品名から候補の計画を探す。戻り値は (lot_no が一致する計画, そのうち製品名も一致する計画)。
    plan_items_by_lot（{lot_no: [item, ...]}）を渡すと、list_active_plan_items() を呼ばずにそこから引く
    （CSV の行ごとに全件を取得していたため、2000行で約61秒かかっていた。渡すと1秒未満）。
    """
    # production_import_service がこのモジュールを import しているので、関数内で import する（循環 import の回避）。
    from services.production_import_service import normalize_product_name

    if plan_items_by_lot is not None:
        candidates = plan_items_by_lot.get(lot_no, [])
    else:
        candidates = [
            item for item in list_active_plan_items()
            if str(item.get("lot_no") or "").strip() == lot_no
        ]

    # 一致判定は完全一致だけ（入力が正規化済みなので、正規化一致も同じ判定になる）。あいまい一致は実装しないと決まっている。
    matched = [
        item for item in candidates
        if normalize_product_name(item.get("board_name")) == product_name_normalized
    ]

    return candidates, matched


def resolve_plan_by_lot_and_name(lot_no: str, product_name_normalized: str, plan_items_by_lot: dict = None):
    """
    lot_no と正規化済みの製品名から計画を特定し、kitting_list_no を返す（実績CSV自動取込用）。
    kitting_list_no が1種類に決まらなければ None。未一致の理由を区別したいときは find_matching_plan_items() を使うこと。
    """
    _, matched = find_matching_plan_items(lot_no, product_name_normalized, plan_items_by_lot)

    unique_kitting_nos = {item["kitting_list_no"] for item in matched}
    if len(unique_kitting_nos) == 1:
        return matched[0]["kitting_list_no"]
    return None
