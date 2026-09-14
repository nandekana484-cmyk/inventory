# models/wip_scrap_records.py
"""
仕掛（WIP）展開結果のDBアクセス層。

ui.wip_expansion_window で、services.bom_service.BOMService.expand_wip_to_parts()に
よるBOM展開結果から操作者が選択した部品を、96コード単位の「消費数量」として
保存する。models.scrap_records（NG／仕損実績）と同じ設計方針を踏襲する
（列構成・delete-then-insertパターン・集計関数の形）。

qty列は消費数量（qty_per_product × 対象仕掛数量）であり、部品ごとに員数が
異なるため仕掛数量そのものではない点はscrap_records.ng_qtyと同じ注意が必要。
"""
from models.db_common import get_connection


def init_wip_scrap_records_table():
    """wip_scrap_records テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS wip_scrap_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kitting_list_no TEXT NOT NULL,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                part_no TEXT NOT NULL,
                qty REAL NOT NULL,
                lot_no TEXT,
                mounting_line TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.commit()


def save_wip_scrap_records(kitting_list_no: str, file_no: str, side: int, records: list,
                            lot_no: str = None, mounting_line: str = None):
    """
    指定kitting_list_no・lot_no・production_sideの既存wip_scrap_recordsを全て削除し、
    新しい部品ごとの消費数量（records）で登録し直す（delete-then-insert）。
    models.scrap_records.replace_scrap_records() と同じパターン。

    ui.wip_expansion_window で仕掛一覧から同じ基板を再展開・再確定登録する場合に、
    「後からの展開・登録を正として上書きする」ために使う。1つのコネクション・
    1つのcommitで削除・再登録の両方を確定させる（途中で例外が発生した場合、
    withブロックにより自動的にロールバックされ、削除だけが反映される中途半端な
    状態にはならない）。

    DELETEをlot_noでも絞り込む理由：replace_scrap_records()と同じく、実DBで
    同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースがあるため
    （lot_noの比較はCOALESCE(...,'')で行う。NULLとNULLはSQL上「等しい」と
    判定されないため）。

    records：[{"part_no": ..., "qty": ...}, ...]
    """
    init_wip_scrap_records_table()
    with get_connection() as con:
        con.execute(
            "DELETE FROM wip_scrap_records WHERE kitting_list_no = ? AND production_side = ? "
            "AND COALESCE(lot_no, '') = COALESCE(?, '')",
            (kitting_list_no, side, lot_no),
        )
        for rec in records:
            con.execute("""
                INSERT INTO wip_scrap_records (
                    kitting_list_no, file_no, production_side, part_no, qty, lot_no, mounting_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (kitting_list_no, file_no, side, rec["part_no"], rec["qty"], lot_no, mounting_line))
        con.commit()


def list_wip_scrap_records_by_kitting_no(kitting_list_no: str, lot_no: str = None,
                                           production_side: int = None) -> list:
    """
    指定kitting_list_no・lot_no（・省略可のproduction_side）の仕掛展開結果を
    96コード単位の明細行（idを含む、個別行の修正・削除の対象特定用）として取得する。
    models.scrap_records.list_scrap_records_by_kitting_no() と同じ設計
    （以前の実装時、当時はscrap_records側にのみ必要だったため見送られていた
    WIP版を、個別行の修正・削除機能追加にあわせて新設した）。

    lot_noは常に条件に含める（COALESCE(...,'')比較のため、計画外＝lot_no=Noneの
    場合はlot_no=NULLのレコードのみが対象になる）。production_sideは省略時（None）は
    条件に含めない（両面分をまとめて返す）。

    wip_scrap_recordsはscrap_recordsのreport_dateに相当する列を持たないため
    （query_wip_totals_range()と同じ理由）、created_atで並び替える。
    """
    init_wip_scrap_records_table()
    with get_connection() as con:
        if production_side is None:
            cur = con.execute("""
                SELECT * FROM wip_scrap_records
                WHERE kitting_list_no = ? AND COALESCE(lot_no, '') = COALESCE(?, '')
                ORDER BY created_at, id
            """, (kitting_list_no, lot_no))
        else:
            cur = con.execute("""
                SELECT * FROM wip_scrap_records
                WHERE kitting_list_no = ? AND COALESCE(lot_no, '') = COALESCE(?, '')
                  AND production_side = ?
                ORDER BY created_at, id
            """, (kitting_list_no, lot_no, production_side))
        return [dict(row) for row in cur.fetchall()]


def update_wip_scrap_record(id: int, qty: float):
    """
    wip_scrap_records 1件（id指定）のqty（消費数量）のみを修正する。
    models.scrap_records.update_scrap_record()と同じ設計・同じ単純さ（対象行の
    存在確認は行わず、該当idが無ければ0件更新のまま何も起きない）。

    グループ単位の洗い替えであるsave_wip_scrap_records()とは独立した経路。
    save_wip_scrap_records()は「kitting_list_no・lot_no・production_side」単位で
    対象行を全削除してから再登録するため、そのグループに対してsave_wip_scrap_records()
    が後から呼ばれると、本関数でのid単位の修正は（同じ行のidごと）消えて上書きされる
    点に注意（仕掛展開画面から同じ基板を再展開・再確定した場合等）。
    """
    init_wip_scrap_records_table()
    with get_connection() as con:
        con.execute("UPDATE wip_scrap_records SET qty = ? WHERE id = ?", (qty, id))
        con.commit()


def delete_wip_scrap_record(id: int):
    """
    wip_scrap_records 1件（id指定）を削除する。update_wip_scrap_record()と同じく
    models.scrap_records.delete_scrap_record()と同じ設計・同じ単純さ。
    """
    init_wip_scrap_records_table()
    with get_connection() as con:
        con.execute("DELETE FROM wip_scrap_records WHERE id = ?", (id,))
        con.commit()


def list_wip_scrap_summary() -> list:
    """
    kitting_list_no・lot_no・production_side単位で仕掛展開結果（確定登録済み分）を
    集計する。models.scrap_records.list_scrap_summary_by_kitting_no() と同様の設計
    （ui.wip_expansion_window の仕掛一覧の状態表示、Step2の仕掛版レポート用）。

    戻り値：[{"kitting_list_no", "file_no", "production_side", "lot_no",
              "mounting_line", "part_count", "record_count",
              "last_created_at", "total_qty"}, ...]
    """
    init_wip_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT
                kitting_list_no,
                production_side,
                lot_no,
                MAX(file_no) AS file_no,
                MAX(mounting_line) AS mounting_line,
                COUNT(DISTINCT part_no) AS part_count,
                COUNT(*) AS record_count,
                MAX(created_at) AS last_created_at,
                COALESCE(SUM(qty), 0) AS total_qty
            FROM wip_scrap_records
            GROUP BY kitting_list_no, production_side, lot_no
            ORDER BY kitting_list_no, production_side, lot_no
        """)
        return [dict(row) for row in cur.fetchall()]


def query_wip_totals() -> dict:
    """
    96コード（part_no）単位で仕掛展開結果（確定登録済み分）の消費数量を、
    全期間・全kitting_list_no・全lot_noを通算して集計する。
    services.inventory_diff_service（在庫差異レポート）専用。
    models.scrap_records.query_scrap_totals() と同じパターン。

    list_wip_scrap_summary()は(kitting_list_no, production_side, lot_no)単位の
    集計（96コード別の内訳は持たない）のため、在庫差異レポートが必要とする
    「96コードごとの合計」を得るには、本関数のようにpart_no単位でGROUP BYし
    直す必要がある。

    期間指定が必要な用途（仕掛96レポート）は query_wip_totals_range() を
    使うこと（本関数の挙動・呼び出し元への影響を避けるため、期間引数は
    ここには追加しない）。

    戻り値：{part_no: 合計qty, ...}
    """
    init_wip_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, COALESCE(SUM(qty), 0) AS total_qty
            FROM wip_scrap_records
            GROUP BY part_no
        """)
        return {row["part_no"]: row["total_qty"] for row in cur.fetchall()}


def query_wip_totals_range(from_date: str, to_date: str) -> dict:
    """
    96コード（part_no）単位で仕掛展開結果（確定登録済み分）の消費数量を、
    created_at（確定登録日時）の日付部分がfrom_date～to_date（両端含む、
    "YYYY-MM-DD"文字列）の範囲に絞って集計する（ui.wip_parts_report_window の
    仕掛96レポート用）。models.scrap_records.query_scrap_totals_range() と
    同じパターン。

    wip_scrap_recordsはscrap_recordsのreport_dateのような明示的な日付列を
    持たず、created_at（datetime('now','localtime')による日時文字列）のみを
    持つため、substr()で日付部分（先頭10文字）だけを取り出して比較する。

    query_scrap_totals_range()と同じ理由により、lot_noは条件・グループ化に
    含めない（96コードの消費実数を見る集計のため、ロットへの帰属を問わず合算する）。

    戻り値：{part_no: 合計qty, ...}
    """
    init_wip_scrap_records_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, COALESCE(SUM(qty), 0) AS total_qty
            FROM wip_scrap_records
            WHERE substr(created_at, 1, 10) >= ? AND substr(created_at, 1, 10) <= ?
            GROUP BY part_no
        """, (from_date, to_date))
        return {row["part_no"]: row["total_qty"] for row in cur.fetchall()}
