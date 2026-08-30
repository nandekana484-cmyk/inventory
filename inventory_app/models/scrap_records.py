# models/scrap_records.py
"""
NG（仕損）実績のDBアクセス層（フェーズ2・新BOM基盤接続）。

ui.ng_input_window で、新BOM基盤（services.bom_service.BOMService）による
BOM展開結果から操作者が選択した部品を、96コード単位の「消費数量」として
保存する。ng_qty 列は列名こそ「NG数量」だが、実際に保存する値は
BOM展開で計算済みの消費数量（qty_per_product × 入力されたNG枚数）である。
そのため services.inventory_diff_service 側では再計算せず、
そのまま part_no 単位に SUM するだけでよい。

lot_noについて：実DBで同一kitting_list_noが複数の異なるlot_noにまたがって
存在するケースが478件確認されており（production_daily側は既にlot_no
（lot_id列）で一意化する修正を実施済み）、scrap_records側も同様の巻き込み
リスクがあるためlot_no列を追加した（db/migration_010）。計画外
（is_unplanned=1）の登録はlot_noを持たない（NULLのまま）。
"""
import sqlite3

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_scrap_records_table():
    """
    scrap_records テーブルの初期化（既存があれば何もしない）。

    is_unplanned：計画外（kitting_plan_itemsに存在しないfile_no・生産面への登録、
    ui.ng_input_window のファイルNo.検索経由）のレコードかどうかを示すフラグ
    （0=計画あり、1=計画外）。
    lot_no：実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在する
    ケースが478件確認されているため、kitting_list_noだけでは一意に扱えない。
    計画外の場合はNULL。
    新規DB作成時にもこれらの列を含めてテーブルが作られるよう
    ここに反映する（db/migration_008・010で既存DBにも同じ列を追加する。
    schema.sqlへの直接追記は、migration_007運用時の教訓を踏まえ行わない）。
    """
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS scrap_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                part_no TEXT NOT NULL,
                ng_qty REAL NOT NULL,
                report_date TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                is_unplanned INTEGER NOT NULL DEFAULT 0,
                lot_no TEXT
            )
        """)
        con.commit()


def save_scrap_record(kitting_list_no: str, file_no: str, side: int, part_no: str,
                       ng_qty: float, report_date: str, lot_no: str = None,
                       is_unplanned: bool = False):
    """
    NG（仕損）実績を1部品・1レコードとして追加保存する（洗い替えではなく追記）。

    lot_no：計画あり登録の場合は選択中の計画のlot_noを渡す。計画外
    （is_unplanned=True）の場合はNoneのままでよい。
    is_unplanned：計画外登録（kitting_plan_itemsに存在しないfile_no・生産面への
    NG登録、ui.ng_input_window のファイルNo.検索経由）の場合True。デフォルトFalse
    （従来通りのキッティングリストNo.検索・計画ありの登録）。
    """
    init_scrap_records_table()
    with get_connection() as con:
        con.execute("""
            INSERT INTO scrap_records (
                kitting_list_no, file_no, production_side, part_no, ng_qty, report_date,
                is_unplanned, lot_no
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (kitting_list_no, file_no, side, part_no, ng_qty, report_date, int(is_unplanned), lot_no))
        con.commit()


def replace_scrap_records(kitting_list_no: str, file_no: str, side: int, records: list,
                           report_date: str, lot_no: str = None, is_unplanned: bool = False):
    """
    指定kitting_list_no・lot_no・production_sideの既存scrap_recordsを全て削除し、
    新しい部品ごとの消費数量（records）で登録し直す（delete-then-insert）。

    ui.ng_input_window でNG一覧から既存登録済みの計画を選び再展開・再登録する場合に、
    「後からの展開・登録を正として上書きする」ために使う。1つのコネクション・
    1つのcommitで削除・再登録の両方を確定させる（途中で例外が発生した場合、
    withブロックにより自動的にロールバックされ、削除だけが反映される中途半端な
    状態にはならない）。

    既存レコードが無い場合（初回登録）も、削除対象が0件になるだけで同じロジックが
    そのまま使える（特別扱い不要）。

    DELETEをlot_no・production_sideでも絞り込む理由：
    - production_side：計画外（is_unplanned=1）の場合、kitting_list_noにはfile_noが
      そのまま流用されるため、同一file_noの面1・面2が同じkitting_list_no値を共有する。
    - lot_no：実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在する
      ケースが478件確認されている。
      いずれも条件に含めないと、別の面・別のロットのscrap_recordsまで誤って
      削除してしまう（kitting_list_noが元々一意な組み合わせの場合は、この絞り込みを
      加えても挙動は変わらない）。lot_noの比較はCOALESCE(...,'')で行う
      （NULLとNULLはSQL上「等しい」と判定されないため。計画外＝lot_no NULL同士の
      レコードを同一グループとして扱うために必要）。

    records：[{"part_no": ..., "ng_qty": ...}, ...]（ng_qtyは消費数量。NG枚数そのものではない）
    """
    init_scrap_records_table()
    with get_connection() as con:
        con.execute(
            "DELETE FROM scrap_records WHERE kitting_list_no = ? AND production_side = ? "
            "AND COALESCE(lot_no, '') = COALESCE(?, '')",
            (kitting_list_no, side, lot_no),
        )
        for rec in records:
            con.execute("""
                INSERT INTO scrap_records (
                    kitting_list_no, file_no, production_side, part_no, ng_qty, report_date,
                    is_unplanned, lot_no
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (kitting_list_no, file_no, side, rec["part_no"], rec["ng_qty"], report_date,
                  int(is_unplanned), lot_no))
        con.commit()


def list_scrap_records_by_kitting_no(kitting_list_no: str, lot_no: str = None) -> list:
    """
    指定キッティングリストNo.・lot_noのNG実績履歴を取得する。lot_noは常に条件に
    含める（COALESCE(...,'')比較のため、計画外＝lot_no=Noneの場合はlot_no=NULLの
    レコードのみが対象になる。「lot_no未指定で全件」という抜け道は用意しない）。

    ui.ng_input_window.on_register() が、置き換え対象となる既存レコードの有無・件数を
    確認する（上書き確認ダイアログを出すかどうかの判定）ために使う。lot_noを渡さないと
    別ロットの既存レコードまで「既存あり」として拾ってしまい、実際には削除されない
    レコードに基づいて確認ダイアログを出してしまう。
    """
    init_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT * FROM scrap_records
            WHERE kitting_list_no = ? AND COALESCE(lot_no, '') = COALESCE(?, '')
            ORDER BY report_date, id
        """, (kitting_list_no, lot_no))
        return [dict(row) for row in cur.fetchall()]


def list_scrap_summary_by_kitting_no() -> list:
    """
    kitting_list_no・lot_no・production_side単位でNG（仕損）実績を集計する
    （ui.ng_input_windowのNG一覧用）。

    is_unplanned=0（計画あり）・is_unplanned=1（計画外）の両方を含めて返す。
    計画詳細（基板名等）はkitting_plan_itemsとの結合を含まないため、
    呼び出し側で is_unplanned=0 の行についてのみ
    models.kitting_plan.find_plan_item_by_kitting_no() 等で個別に補完すること
    （list_active_plan_items()は「2回目計画がある場合の1回目除外」ロジックを
    持つため、NG一覧の用途では使わない）。

    GROUP BYにlot_no・production_sideを含める理由：
    - production_side：計画外（is_unplanned=1）の場合、kitting_list_noにはfile_noが
      そのまま流用されるため、同一file_noの面1・面2が同じkitting_list_no値を共有する。
    - lot_no：実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在する
      ケースが478件確認されている。
      いずれも含めないと、別の面・別のロットの実績が1行に誤って合算されてしまう。

    total_ng_qty は消費数量（ng_qty列）の単純合計であり、部品ごとに員数（qty_per_product）
    が異なるため「申告されたNG枚数」そのものを表すわけではない点に注意
    （詳細はモジュールdocstring参照）。

    戻り値：[{"kitting_list_no", "file_no", "production_side", "lot_no", "is_unplanned",
              "part_count", "record_count", "last_report_date", "total_ng_qty"}, ...]
    """
    init_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT
                kitting_list_no,
                production_side,
                lot_no,
                MAX(file_no) AS file_no,
                MAX(is_unplanned) AS is_unplanned,
                COUNT(DISTINCT part_no) AS part_count,
                COUNT(*) AS record_count,
                MAX(report_date) AS last_report_date,
                COALESCE(SUM(ng_qty), 0) AS total_ng_qty
            FROM scrap_records
            GROUP BY kitting_list_no, production_side, lot_no
            ORDER BY kitting_list_no, production_side, lot_no
        """)
        return [dict(row) for row in cur.fetchall()]


def query_scrap_totals() -> dict:
    """
    96コード（part_no）単位でNG（仕損）消費数量を、全期間・全lot_noを通算して集計する。
    services.inventory_diff_service（在庫差異レポート）専用。

    lot_noは意図的に条件・グループ化に含めない：本関数は「どのロットの消費か」ではなく
    「96コードが実際にどれだけ消費されたか」という在庫上の実数を知るための集計であり、
    kitting_list_no/lot_noへの帰属を問わず全件を合算するのが正しい（そもそも
    kitting_list_noを一切参照しないため、同一kitting_list_noが複数のlot_noにまたがる
    問題の影響を受けない）。期間指定が必要な用途（96NGレポート等）は
    query_scrap_totals_range() を使うこと（本関数の挙動・呼び出し元への影響を
    避けるため、期間引数はここには追加しない）。

    戻り値：{part_no: 合計ng_qty, ...}
    """
    init_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, COALESCE(SUM(ng_qty), 0) AS total_qty
            FROM scrap_records
            GROUP BY part_no
        """)
        return {row["part_no"]: row["total_qty"] for row in cur.fetchall()}


def query_scrap_totals_range(from_date: str, to_date: str) -> dict:
    """
    96コード（part_no）単位でNG（仕損）消費数量を、report_dateが
    from_date～to_date（両端含む、"YYYY-MM-DD"文字列）の範囲に絞って集計する
    （ui.parts_ng_report_window の96NGレポート用）。

    query_scrap_totals()と同じ理由により、lot_noは条件・グループ化に含めない
    （96コードの消費実数を見る集計のため、ロットへの帰属を問わず合算する）。

    query_scrap_totals()（全期間、在庫差異レポート用）とは別関数として用意し、
    既存の呼び出し元の挙動には一切影響しない。

    戻り値：{part_no: 合計ng_qty, ...}
    """
    init_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, COALESCE(SUM(ng_qty), 0) AS total_qty
            FROM scrap_records
            WHERE report_date >= ? AND report_date <= ?
            GROUP BY part_no
        """, (from_date, to_date))
        return {row["part_no"]: row["total_qty"] for row in cur.fetchall()}
