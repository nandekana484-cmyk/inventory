# models/ng_declarations.py
"""
NG（仕損）枚数の申告のDBアクセス層。

生産実績入力画面（ui/kitting_production_entry.py）でユーザーが入力する「NG枚数」を、
BOM展開前の生値のまま記録する（申告のみ）。実際の96コード単位への展開・登録
（models/scrap_records.py）は、NG入力画面（ui/ng_input_window.py）の「展開」操作で
別途行う。

kitting_list_no・lot_no・production_sideの組み合わせにつき常に最大1件（report_date
を問わない「1計画・面＝1レコード、常に上書き」ルール。models.production.
replace_daily_result()と同じ考え方に統一した）。

lot_noについて：実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在する
ケースが478件確認されているため、lot_no列を追加した（db/migration_010）。計画外
（is_unplanned=1）の申告はlot_noを持たない（NULLのまま）。
"""
import sqlite3

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_ng_declarations_table():
    """ng_declarations テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS ng_declarations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                ng_qty REAL NOT NULL,
                report_date TEXT NOT NULL,
                is_unplanned INTEGER NOT NULL DEFAULT 0,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                lot_no TEXT
            )
        """)
        con.commit()


def save_ng_declaration(kitting_list_no: str, file_no: str, side: int, ng_qty: float,
                          report_date: str, lot_no: str = None, is_unplanned: bool = False):
    """
    NG枚数の申告を1件保存する。同一kitting_list_no・lot_no・production_sideの
    既存申告があれば、report_dateを問わず削除してから登録し直す
    （delete-then-insert、「1計画（kitting_list_no・lot_no）・面＝1レコード、
    常に上書き」ルール）。
    1コネクション・1トランザクションで実行する（with文により、例外発生時は
    自動的にロールバックされる）。

    以前はreport_date（当日）が一致するレコードのみを削除する「同日は1件」の
    仕様だったが、models.production.replace_daily_result()と同じ考え方に統一し、
    report_dateを条件から外した（その計画・面の過去日付分のレコードも含めて
    全て削除してから、新しい1件をINSERTする）。

    lot_no：計画あり申告の場合は選択中の計画のlot_noを渡す。計画外
    （is_unplanned=True）の場合はNoneのままでよい。
    """
    init_ng_declarations_table()
    with get_connection() as con:
        con.execute(
            "DELETE FROM ng_declarations WHERE kitting_list_no = ? AND production_side = ? "
            "AND COALESCE(lot_no, '') = COALESCE(?, '')",
            (kitting_list_no, side, lot_no),
        )
        con.execute("""
            INSERT INTO ng_declarations (
                kitting_list_no, file_no, production_side, ng_qty, report_date, is_unplanned, lot_no
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (kitting_list_no, file_no, side, ng_qty, report_date, int(is_unplanned), lot_no))
        con.commit()


def get_ng_declaration(kitting_list_no: str, side: int, lot_no: str = None):
    """
    指定kitting_list_no・side・lot_noの現在の申告を1件取得する（report_dateを
    問わない全期間検索。save_ng_declaration()の「1計画・面＝1レコード、常に
    上書き」ルールにより、この単位では高々1件しか存在しない）。無ければNone。

    以前はreport_date（省略時は当日）を条件に含めていたが、report_dateを問わない
    全期間検索に変更した（過去日付のまま当日中に未更新の申告も正しく取得できる
    ようにするため。models.production系の同種の修正と同じ考え方）。

    lot_noは常に条件に含める（COALESCE(...,'')比較のため、計画外＝lot_no=Noneの
    場合はlot_no=NULLの申告のみが対象になる）。
    """
    init_ng_declarations_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT * FROM ng_declarations
            WHERE kitting_list_no = ? AND production_side = ?
              AND COALESCE(lot_no, '') = COALESCE(?, '')
        """, (kitting_list_no, side, lot_no))
        row = cur.fetchone()
        return dict(row) if row else None


def list_ng_declarations_latest() -> list:
    """
    kitting_list_no・lot_no・production_side単位の申告を1件ずつ返す
    （ui.ng_input_window のNG一覧、ng_declarations×scrap_recordsのマージ用）。

    save_ng_declaration()が「1計画（kitting_list_no・lot_no）・面＝1レコード、
    常に上書き」ルール（report_dateを問わない）で保存するため、この単位では
    report_dateを問わず全件がそのまま「唯一の（＝最新の）申告」になる。
    以前はreport_date単位で複数件が共存し得たため、MAX(report_date)で絞り込む
    サブクエリ・自己JOINが必要だったが、その必要が無くなったため単純な
    SELECTに変更した（関数名の「latest」は、呼び出し元との後方互換のため
    変更していない）。

    戻り値：[{"kitting_list_no", "file_no", "production_side", "lot_no", "ng_qty",
              "report_date", "is_unplanned"}, ...]
    """
    init_ng_declarations_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT kitting_list_no, file_no, production_side, lot_no, ng_qty,
                   report_date, is_unplanned
            FROM ng_declarations
        """)
        return [dict(row) for row in cur.fetchall()]
