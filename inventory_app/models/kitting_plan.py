import os
import sqlite3
from datetime import datetime

from models.db_common import get_connection
from models.production import get_app_cumulative_qty_bulk, list_daily_production_by_kitting_no
from models.production_side_master import get_second_side_status_by_file_no

# 面1のみの計画（同一(lot_no, setup_file_no)に面2の計画が無い計画）の分類
# （2026-10-07新設、classify_side1_only_plan()参照）。
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

        # list_plan_items_by_lot() の WHERE lot_no = ? 用（db/migration_007と同内容。
        # 新規DB作成時にも反映されるようここでも作成する）
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_kitting_plan_items_lot_no
            ON kitting_plan_items(lot_no)
        """)

        # キッティングNo.未確定のまま取り込まれた計画行の一時保存テーブル
        # （services.kitting_import_service.import_kitting_plan_csv()から使う）。
        # 識別キーは (lot_no, setup_file_no, production_side, order_qty)。
        # 後日キッティングNo.が付与されて再取込された際、この識別キーが一致すれば
        # 「未確定期間からの確定」として扱い、正式なkitting_plan_itemsへ移行する
        # （upsert_pending_kitting_plan_item()・find_pending_kitting_plan_item()・
        # delete_pending_kitting_plan_item()参照）。
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

        # 識別キーの重複防止（本体のupsert_pending_kitting_plan_item()はSELECT→
        # UPDATE/INSERTで明示的に上書き判定するため、このインデックス自体は
        # 直接のON CONFLICT対象としては使わないが、想定外の経路からの重複挿入を
        # DBレベルでも防ぐための保険として作成する）。
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
    キッティングNo.未確定の計画行をpending_kitting_plan_itemsへ保存する。

    識別キー (lot_no, setup_file_no, production_side, order_qty) が一致する行が
    既にあれば内容を上書き（delete-then-insertではなく、SELECTで既存行を確認した
    上でUPDATE、無ければINSERT）、無ければ新規追加する。

    data：services.kitting_import_service.import_kitting_plan_csv()が組み立てる
    item辞書（kitting_list_noを除く）をそのまま渡せる想定。未知のキーは無視する。

    戻り値：対象行のpending_id。

    テーブルの存在保証について：この関数はservices.kitting_import_service.
    import_kitting_plan_csv()のCSV行ループ内から呼ばれる想定で、同関数は
    ループに入る前に必ずcreate_plan_batch()（内部でinit_kitting_plan_tables()を
    呼ぶ）を実行済みのため、ここで毎回init_kitting_plan_tables()を呼ぶ必要は
    ない（以前は呼んでいたが、CSV行ごとに冗長なCREATE TABLE/INDEX
    IF NOT EXISTS文が再実行され性能上のボトルネックになっていたため削除した）。
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
    識別キー (lot_no, setup_file_no, production_side, order_qty) に一致する
    保留行（pending_kitting_plan_items）を1件検索する。

    キッティングNo.が付与された行を取り込む際、その行が以前「未確定」のまま
    保留登録されていたものの確定なのかを判定するために使う
    （services.kitting_import_service.import_kitting_plan_csv()から呼ぶ）。

    戻り値：一致する行（辞書）。無ければNone。

    テーブルの存在保証はupsert_pending_kitting_plan_item()と同じ理由により
    呼び出し元（import_kitting_plan_csv()）に委ねる（init_kitting_plan_tables()の
    毎回呼び出しは削除済み）。
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
    """
    保留行（pending_kitting_plan_items）を1件削除する（確定登録が完了した後に呼ぶ）。

    テーブルの存在保証はupsert_pending_kitting_plan_item()と同じ理由により
    呼び出し元（import_kitting_plan_csv()）に委ねる（init_kitting_plan_tables()の
    毎回呼び出しは削除済み）。
    """
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
    唯一の呼び出し元はservices.production_service.calculate_lot_completion()
    （調査により確認済み、他に呼び出し箇所なし）。従来is_activeを条件に含んで
    おらず、旧バージョンの行も混ざって返っていたため、is_active=1を追加した
    （calculate_lot_completion()がkitting_list_no/lot_noの取り違え問題と合わせて
    修正されるタイミングで、影響範囲が1箇所のみと確認できたため合わせて対応）。
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
    list_plan_items_by_lot()と同じWHERE条件（delete_flag=0・COALESCE(is_active,1)=1）
    で、lot_noによる絞り込み無しに全件を返す。

    呼び出し元はservices.production_service.list_incomplete_lots()・
    check_lot_progress()（2026-09-26追加、構成基板数チェック・ロット進捗を
    日報・月報の実績データに依存せず全アクティブロット横断で算出する関数）。
    いずれもlot_no全件についてcalculate_lot_completion()相当の計算をN+1
    （lot_no件数分のSELECT）にせず、1回のSELECTで全lot_no分の計画行を
    まとめて取得した上で、呼び出し側でlot_noごとにグルーピングして使うための
    もの。

    lot_noがNULL・空文字の行（万一存在した場合）は対象外とする（lot単位の
    完成数計算という概念自体が成立しないため。calculate_lot_completion(None)や
    calculate_lot_completion("")は、list_plan_items_by_lot()側で該当0件となり
    ValueErrorになる）。

    戻り値：[{"kitting_list_no", "lot_no", "setup_file_no", "production_side",
              "order_qty", "board_name"}, ...]（list_plan_items_by_lot()と
              異なり、呼び出し側の用途（lot単位の集計、構成基板数チェック）に
              必要な列のみに絞っている。board_nameはcheck_lot_progress()の
              構成基板数チェックで使うため2026-09-26に追加した）。
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
    同一lot_no・同一setup_file_noで、current_sideの反対のproduction_sideを持つ
    現在アクティブな計画（COALESCE(is_active,1)=1）を1件検索する。

    0件（片面のみの計画）：Noneを返す。
    1件：そのまま返す。
    複数件（同一lot_no・setup_file_no・反対sideに対して、日付違いの複数バッチが
    アクティブな場合。実データで確認済みのケース）：current_plan_start_datetime
    （選択中の計画のplan_start_datetime、"YYYY/MM/DD HH:MM:SS"形式）に最も近い
    plan_start_datetimeを持つ行を返す。current_plan_start_datetime が省略された、
    またはパース失敗した場合は、plan_start_datetime昇順で最初の行を返す
    （近さの基準がないため）。
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
    同一(lot_no, setup_file_no)に属する、現在アクティブな計画（面1・面2の
    両方、production_sideが"1"または"2"のもの）を全件返す（2026-10-08新設）。

    services.production_service._build_report_rows()の「面1の実績が面2を
    上回る」不整合判定を、計画どうしの1対1の組（find_opposite_side_plan()が
    複数バッチから1件を選ぶ方式、曖昧な場合に誤った組を選び誤検出する問題が
    実データで確認された）ではなく、ロットNo・ファイルNoごとの合計の比較に
    変更するために使う。同一(lot_no, setup_file_no)に面・日付の異なる複数の
    バッチが存在する場合でも、本関数は該当する全件を返すため、呼び出し側で
    面1側・面2側それぞれの合計（get_app_cumulative_qty()のSUM）を計算できる
    （_compute_lot_completion()のfile_actuals、(setup_file_no, production_side)
    単位の合算と同じ考え方）。

    find_opposite_side_plan()（1件に絞り込む、登録時の自動入力・NG入力欄の
    相手探しで使う）とは用途が異なるため、既存の関数は変更せず新設した。
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
    バッチのソフト削除または復活。

    kitting_plan_batches.delete_flag の更新だけでは、list_active_plan_items() 等
    kitting_plan_items.is_active しか見ない一覧取得関数に削除が一切反映されないため、
    該当バッチに属する kitting_plan_items 行の is_active も同一トランザクションで
    連動して更新する（片方だけ成功する中途半端な状態を避けるため、1つの
    コネクション・1つのcommitで両方を確定させる）。

    - deleted=True（削除）：このバッチに属し、現在アクティブな行（is_active=1）を
      is_active=0 にする。
    - deleted=False（復元）：単純に is_active=1 へ戻すことはしない。
      同一 (kitting_list_no, lot_no) に対して、他の理由（create_plan_version() に
      よる新バージョンの作成）で既に別の行が is_active=1 になっている場合、
      無条件に戻すとその新しい行と共存して重複したアクティブ行が生まれてしまう
      （UNIQUE INDEX uq_kitting_plan_items_active_kitting_lot が防ぐのは
      (kitting_list_no, lot_no) が完全一致する場合のみ）。
      そのため、このバッチに属し・現在is_active=0で・かつ同一(kitting_list_no, lot_no)
      に他のアクティブ行が存在しない行のみを復元対象とする。
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
    指定kitting_list_noにヒットする、現在activeな計画行を全て返す（lot_noによる
    絞り込みなし）。実DBで同一kitting_list_noが複数の異なるlot_noにまたがって
    存在するケースが478件確認されており、services.production_service.
    _resolve_plan_item()がlot_no省略で呼ばれた際に「候補が複数あるかどうか」
    （＝ユーザーへの選択ダイアログが必要かどうか）を判定するために使う。

    create_plan_version()は新バージョンを作る際に同一(kitting_list_no, lot_no)の
    旧アクティブ版を必ずis_active=0にしてから登録するため、lot_no単位で
    is_active=1の行は高々1件しか存在しない。そのため本関数は
    「WHERE kitting_list_no=? AND is_active=1」だけで、lot_noごとに1行ずつ
    （＝候補一覧として過不足のない状態）を返せる。

    is_active列が存在しない旧DB環境（get_latest_plan_by_kitting_no()と同様の
    分岐）では、is_active判定を行わずkitting_list_no一致行を全て返す
    （バージョン管理が無い環境なので、そのまま「現在の状態」とみなす）。
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
    実績入力用：現在アクティブな計画を一覧で返す（検索は部分一致）

    戻り値の各要素（kitting_plan_itemsの列 + "app_cumulative_qty"）には、
    完了判定に使ったアプリ内累計値をそのまま含める。呼び出し元（表示用に同じ値を
    再度計算しがちな箇所）はこの値を再利用し、get_app_cumulative_qty()を
    重複して呼ばないこと。

    include_completed：Trueの場合、完了済み（実績が発注数に到達済み）判定による
    除外（actual_qty >= order_qty でのcontinue）をスキップし、完了済み計画も
    含めて返す。デフォルトはFalse（現状維持）。
    find_matching_plan_items()（実績CSV自動取込用）はこの引数を指定せず、常に
    デフォルト（完了済み除外）のまま呼び出すこと（完了済み・未完了が同一lot_no/
    製品名で複数存在する場合に一意特定できなくなり、自動取込のマッチングが
    壊れるため）。
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

        # get_app_cumulative_qty()を件数分ループ呼び出しする代わりに、対象の
        # (kitting_list_no, lot_no)の組を先に集めて1回（〜数回）のクエリでまとめて
        # 取得する。同じコネクションを使い回し、追加のconnect()を発生させない。
        #
        # kitting_list_noだけでなくlot_noも組にして渡す理由：実DBで同一
        # kitting_list_noが複数の異なるlot_noにまたがって存在するケースが478件
        # 確認されており（別々の基板・別々の発注数の計画が同じkitting_list_noを
        # 共有している）、kitting_list_noだけで集計すると別ロットの実績まで
        # 巻き込んで合算してしまう（実データで完成数の取り違えを確認済み）。
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
    面1のみの計画（同一(lot_no, setup_file_no)に面2の計画が無い計画）を、
    生産面マスター（models.production_side_master）で分類する
    （2026-10-07新設、D-9x参照）。

    a（SIDE1_ONLY_CLASS_SINGLE_SIDE）：生産面マスターに後行面なしの登録がある
       → 片面の製品。
    b（SIDE1_ONLY_CLASS_WAITING_SIDE2）：生産面マスターに後行面ありの登録がある
       → 面2待ち（まだ計画データに面2が取り込まれていないだけ）。
    c（SIDE1_ONLY_CLASS_UNREGISTERED）：生産面マスターに登録が無い
       → 生産面マスター未登録。

    plan_itemはkitting_plan_items 1行分の辞書（list_active_plan_items()・
    find_matching_plan_items()が返す形式）を想定する。

    plan_item["production_side"]が"1"でない場合（面2の計画、またはそれ以外の
    値）はNoneを返す（分類対象外）。production_sideが"1"の計画は、
    list_active_plan_items()の既存の「2回目計画があれば1回目除外」ロジック
    （D-8）を経由して得られたものである限り、呼び出し時点で同一
    (lot_no, setup_file_no)に面2の計画が存在しないことが保証されている
    （面2が存在すれば、その時点でこの面1の行自体がlist_active_plan_items()
    の結果から既に除外されているため）。そのため、本関数は計画データ側で
    「面2が無いこと」を改めて確認する必要が無く、生産面マスターの参照のみで
    分類できる。

    判定の単位（2026-10-08改訂、D-9x改訂）：以前は(setup_file_no, mounting_line)
    単位（get_second_side_status()）で判定していたが、「同じファイルNoで、
    先行面と後行面が異なる実装ラインを流れることがある」ことが判明したため、
    setup_file_no単位（実装ラインを問わない、get_second_side_status_by_file_no()）
    に変更した。実装ラインはkitting_list_noの文字列分解ではなく
    plan_item["mounting_line"]列の値を使うという既存の方針（kitting_list_noの
    3番目の区切りとmounting_line列が一致しない行が3526件中20件あることを
    確認済み、CANONICAL_DESIGN_DECISIONS.md D-93参照）自体は変わらないが、
    本関数の判定自体はmounting_lineをもはや使わない。
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
    生産面マスターと計画データ（kitting_plan_items）の食い違いを検出する
    （2026-10-07新設、2026-10-08ファイルNo単位に改訂、D-9x §4・改訂参照。
    画面・判定のどちらからも参照されない、利用者が内容を確認するための
    一覧専用）。

    「マスターではこのファイルNoは後行面なし（1回目のみ）と判定されている
    が、計画データには（いずれかの実装ラインに）このファイルNoの面2の計画が
    存在する」組を全件返す（マスターが古い・未更新である可能性が高いケース。
    計画データを優先する既存方針（D-8）自体は変更しない。あくまで食い違いの
    事実を利用者が確認できるようにするための一覧）。

    判定の単位（2026-10-08改訂）：以前は(setup_file_no, mounting_line)単位
    だったが、classify_side1_only_plan()と同じファイルNo単位（実装ラインを
    問わない）の判定に揃えた。計画データ側の面2がどの実装ラインにあるかは、
    "mounting_lines"（複数あり得る）として参考情報に残す。

    戻り値：[{"setup_file_no", "mounting_lines"（面2の計画がある実装ライン
    のリスト）, "lot_nos"（面2の計画があるロットNoのリスト）}, ...]
    （setup_file_no の昇順）。
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
    計画データ（kitting_plan_items）に存在するが、生産面マスターに全く登録が
    無い（どの実装ラインにも1件も登録が無い）setup_file_noを検出する
    （2026-10-07新設、2026-10-08ファイルNo単位に改訂、D-9x §4・改訂参照）。
    classify_side1_only_plan()の「生産面マスター未登録」は、本関数と同じ
    ファイルNo単位の判定（get_second_side_status_by_file_no()がNone）を使う。
    生産面マスターへの登録に使えるよう、CSV出力の元データとしても使う
    （ui.production_side_master_window参照）。

    戻り値：[{"setup_file_no"}, ...]（昇順）。
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
    参考情報専用（判定には使わない、2026-10-08新設、D-9x改訂）：計画データ
    （kitting_plan_items）に存在する(setup_file_no, mounting_line)の組のうち、
    そのsetup_file_no自体は生産面マスターに登録がある（get_second_side_
    status_by_file_no()がNoneではない）が、この特定の実装ラインについては
    生産面マスターに1件も登録が無い組。

    classify_side1_only_plan()・find_unregistered_production_side_file_nos()
    のファイルNo単位の判定には一切使わない。利用者が「ファイルNoは登録済み
    だが、計画の実装ラインがマスターに無い」ケースを個別に確認するための
    一覧（例：ファイルNoの先行面がAライン・Bラインで実施されるが、マスターに
    はAラインの登録しか無い場合、Bラインがここに現れる）。

    戻り値：[{"setup_file_no", "mounting_line"}, ...]（昇順）。
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
    指定したsetup_file_no（複数）それぞれについて、計画データ
    （kitting_plan_items）に実在する実装ラインの一覧を返す（2026-10-08新設、
    D-9x改訂。ui.production_side_master_window.on_show_unregistered()の
    CSV出力ガイド用：ファイルNo単位で「未登録」と判定された場合、利用者が
    実際にどの実装ラインへ生産面を登録すればよいかを示すために使う）。

    戻り値：{setup_file_no: [mounting_line, ...]}（各リストは昇順。
    file_nosに該当する計画データが無いsetup_file_noはキー自体が現れない）。
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
    面2の計画が存在するために一覧（list_active_plan_items()、D-8のロジック）
    から隠れている面1の計画のうち、その面1の計画に実績（production_daily）が
    既に登録されているものを全件検出する（2026-10-07新設、D-9x §5参照）。

    面1に実績を登録した後で面2が追加された場合、実績の付け替えは自動では
    行わない（利用者の方針）。本関数は、現在そのような状態になっている計画を
    いつでも確認できる手段として提供する（計画CSV取込時の通知（本ファイルの
    find_newly_hidden_side1_plans()）とは独立に、既存の状態を棒卸し的に確認
    する用途）。

    戻り値：[{"lot_no", "setup_file_no", "mounting_line",
              "side1_kitting_list_no", "side2_kitting_list_no",
              "production_rows": [{"report_date", "daily_qty"}, ...]}, ...]
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
    plan_batch_id（今回のキッティング計画CSV取込1回分）で新規に追加された
    面2の計画によって、既存の面1の計画（実績が登録済み）が一覧から隠れる
    ことになった組を検出する（2026-10-07新設、D-9x §5参照、取込完了時の
    通知用）。実績の付け替えは自動では行わない。

    戻り値はfind_all_hidden_side1_plans_with_production()と同じ形式だが、
    「今回の取込で追加された面2の計画」に限定する。
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
    実績CSV自動取込（services.production_import_service）用の内部ヘルパー。
    lot_no + 正規化済み製品名(product_name_normalized) から、候補となる
    現在アクティブな計画（kitting_plan_items）を探す。

    resolve_plan_by_lot_and_name() の一致判定そのものに使われるほか、
    production_import_service 側で未一致の理由（計画なし／製品名ゆらぎ／複数候補あり）
    を判別する際にも同じロジックを使うために公開関数としている。

    plan_items_by_lot：呼び出し元がlist_active_plan_items()を1回だけ呼び、
    lot_noをキーにグルーピングした辞書（{lot_no: [item, ...], ...}、キーは
    str(item.get("lot_no") or "").strip()で正規化したもの）を事前に用意できる
    場合に渡す。渡された場合、本関数は内部でlist_active_plan_items()を呼ばず、
    この辞書から該当lot_noの候補をそのまま取得する（CSV取込のように行数分
    繰り返し呼ばれる場面でのN+1解消。services.production_service.
    list_incomplete_lots()で行った「1回だけ全件取得→lot_noごとにグルーピング」
    と同じアプローチ。実測で2000行のCSV取込が約61秒→改善後は1秒未満まで
    短縮：list_active_plan_items()が全計画のフルスキャン＋累計実績の一括集計を
    行毎回呼ばれていたことが原因だった）。省略時（None）は従来通り
    list_active_plan_items()を都度呼ぶ（後方互換。呼び出し元を変更したくない
    既存・将来の利用箇所への影響を避けるため、デフォルト引数として残す）。

    戻り値：(lot_no が一致する現在アクティブな計画一覧, その中で製品名も一致する計画一覧)
    """
    # services.production_import_service は本モジュールの resolve_plan_by_lot_and_name /
    # find_matching_plan_items をインポートしているため、モジュールトップレベルで
    # 逆方向にインポートすると循環importになる。関数内インポートで回避する。
    from services.production_import_service import normalize_product_name

    if plan_items_by_lot is not None:
        candidates = plan_items_by_lot.get(lot_no, [])
    else:
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
    #      仕様として実装しないことが決定している（完全一致のみで運用する）。
    matched = [
        item for item in candidates
        if normalize_product_name(item.get("board_name")) == product_name_normalized
    ]

    return candidates, matched


def resolve_plan_by_lot_and_name(lot_no: str, product_name_normalized: str, plan_items_by_lot: dict = None):
    """
    lot_no + 正規化済み製品名(product_name_normalized) から計画を一意に特定し、
    kitting_list_no を返す（実績CSV自動取込用）。

    一致するアクティブな計画の kitting_list_no が1種類のみに定まれば、その値を返す。
    0件、または複数の異なる kitting_list_no に一致する場合（曖昧）は None を返す。
    未一致の理由を区別したい場合は find_matching_plan_items() を利用すること。

    plan_items_by_lot：find_matching_plan_items()と同じ（事前グルーピング辞書）。
    そのままfind_matching_plan_items()へ引き継ぐのみで、本関数自体のロジックは
    変更しない。省略時（None）は従来通り。
    """
    _, matched = find_matching_plan_items(lot_no, product_name_normalized, plan_items_by_lot)

    unique_kitting_nos = {item["kitting_list_no"] for item in matched}
    if len(unique_kitting_nos) == 1:
        return matched[0]["kitting_list_no"]
    return None
