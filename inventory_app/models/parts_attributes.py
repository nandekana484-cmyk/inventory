# models/parts_attributes.py
"""
部品属性マスタ（丁取り数等）のDBアクセス層（フェーズ3・新BOM基盤統合）。

新BOM計算ロジック（services.bom_service.BOMService._calculate_bom）で、
BOM TSVの係数が0かつRフラグがある行の qty 計算に丁取り数（teitori）を使う
（qty = 部品員数 ÷ 丁取り数）。
"""
from models.bom_master import invalidate_bom_master_by_part_no
from models.db_common import get_master_connection


def init_parts_attributes_table():
    """parts_attributes テーブルの初期化（既存があれば何もしない）。"""
    with get_master_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS parts_attributes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                part_no TEXT NOT NULL,
                teitori INTEGER,
                part_type TEXT,
                supply_type TEXT,
                full_qty INTEGER,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(part_no)
            )
        """)
        con.commit()


def upsert_parts_attributes(part_no: str, teitori, part_type: str = None,
                             supply_type: str = None, full_qty=None):
    """
    96コード（part_no）をキーに部品属性（丁取り数等）を登録・更新する。
    既存なら上書き、なければ新規登録する（差分検知は行わず常に上書き）。

    丁取り数はBOM計算（services.bom_service.BOMService._calculate_bom）の
    キャッシュ（models.bom_master）に影響するため、保存後に該当 part_no を
    含むキャッシュ行を無効化し、次回参照時に再計算させる。
    """
    init_parts_attributes_table()
    with get_master_connection() as con:
        con.execute("""
            INSERT INTO parts_attributes (part_no, teitori, part_type, supply_type, full_qty)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(part_no) DO UPDATE SET
                teitori = excluded.teitori,
                part_type = excluded.part_type,
                supply_type = excluded.supply_type,
                full_qty = excluded.full_qty,
                imported_at = datetime('now', 'localtime')
        """, (part_no, teitori, part_type, supply_type, full_qty))
        con.commit()

    invalidate_bom_master_by_part_no(part_no)


def delete_parts_attributes_not_in(keep_part_no_list) -> list:
    """
    keep_part_no_list に含まれない parts_attributes 行を削除する
    （CSVをマスタとした差分同期用。CSVに存在しない part_no を削除する）。

    削除対象は現在のテーブル全件と keep_part_no_list を比較して特定し、
    実際の削除は1コネクション・1トランザクション内で行う
    （con.commit() 前に例外が発生すれば with ブロックの終了時に自動ロールバックされ、
    一部だけ削除された中途半端な状態にはならない）。

    呼び出し側で「CSV読み込み・全行のupsertが正常に完了した後にのみ呼ぶ」ことを
    想定している（本関数自体はその前提を強制しない）。

    削除した part_no それぞれについて、bom_master キャッシュも
    upsert_parts_attributes() と同様に無効化する。

    戻り値：実際に削除された part_no のリスト。
    """
    keep_set = set(keep_part_no_list)
    init_parts_attributes_table()
    with get_master_connection() as con:
        existing = [row["part_no"] for row in con.execute("SELECT part_no FROM parts_attributes")]
        to_delete = [p for p in existing if p not in keep_set]
        for part_no in to_delete:
            con.execute("DELETE FROM parts_attributes WHERE part_no = ?", (part_no,))
        con.commit()

    for part_no in to_delete:
        invalidate_bom_master_by_part_no(part_no)

    return to_delete


def get_parts_attributes(part_no: str):
    """指定 part_no（96コード）の部品属性を取得する。存在しなければ None を返す。"""
    init_parts_attributes_table()
    with get_master_connection() as con:
        cur = con.execute("""
            SELECT part_no, teitori, part_type, supply_type, full_qty
            FROM parts_attributes WHERE part_no = ?
        """, (part_no,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_parts_attributes() -> list:
    """部品属性の一覧を part_no 順で取得する（インポート画面の一覧表示用）。"""
    init_parts_attributes_table()
    with get_master_connection() as con:
        cur = con.execute("""
            SELECT part_no, teitori, part_type, supply_type, full_qty
            FROM parts_attributes ORDER BY part_no
        """)
        return [dict(row) for row in cur.fetchall()]


def get_parts_attributes_count() -> int:
    """登録件数表示用（ui/parts_attributes_import_window.py）。総件数のみ返す。"""
    init_parts_attributes_table()
    with get_master_connection() as con:
        return con.execute("SELECT COUNT(*) AS c FROM parts_attributes").fetchone()["c"]


def compute_parts_attributes_sync_plan(resolved_rows: list) -> dict:
    """
    resolved_rows（[(part_no, teitori, part_type, supply_type, full_qty), ...]、
    CSV内の重複キーは呼び出し元で既に「最後の行の値」に解決済み・part_no単位で
    1件ずつである前提）を現在のテーブル内容と比較し、差分（追加・更新・変更なし・
    削除）を返す。読み取りのみでDBへの書き込みは行わない（インポート前の確認
    ダイアログ用。apply_parts_attributes_sync()からも再利用する）。

    「変更なし」の判定は5列（teitori・part_type・supply_type・full_qty、
    およびpart_no自体）すべてが一致する場合のみとする。
    """
    init_parts_attributes_table()
    with get_master_connection() as con:
        existing = {
            row["part_no"]: (row["teitori"], row["part_type"], row["supply_type"], row["full_qty"])
            for row in con.execute(
                "SELECT part_no, teitori, part_type, supply_type, full_qty FROM parts_attributes"
            )
        }

    to_add, to_update, unchanged = [], [], []
    keep_part_nos = set()
    for part_no, teitori, part_type, supply_type, full_qty in resolved_rows:
        keep_part_nos.add(part_no)
        new_value = (teitori, part_type, supply_type, full_qty)
        if part_no not in existing:
            to_add.append({"part_no": part_no, "new_value": new_value})
        elif existing[part_no] != new_value:
            to_update.append({"part_no": part_no, "old_value": existing[part_no], "new_value": new_value})
        else:
            unchanged.append({"part_no": part_no, "value": new_value})

    to_delete = [
        {"part_no": part_no, "value": value}
        for part_no, value in existing.items() if part_no not in keep_part_nos
    ]

    return {"to_add": to_add, "to_update": to_update, "unchanged": unchanged, "to_delete": to_delete}


def apply_parts_attributes_sync(resolved_rows: list) -> dict:
    """
    resolved_rows（compute_parts_attributes_sync_plan()と同じ形式）の内容で、
    登録・更新・削除を**1つのトランザクション**にまとめて確定する。

    BOMキャッシュの無効化（invalidate_bom_master_by_part_no()）は、本トランザクションの
    コミットが成功した**後**に、影響を受けたpart_no（追加・更新・削除された分）
    すべてについてまとめて行う（bom_masterは月次DB側のテーブルであり、本関数が
    操作するmaster.db側のトランザクションとは別のDB・別の接続のため、コミット前に
    呼んでも本来のアトミック性には影響しないが、「コミット成功後に行う」という
    指示された設計意図に合わせ、明示的にコミット後へ移動した）。

    既存のupsert_parts_attributes()・delete_parts_attributes_not_in()（1行ごとに
    個別コミット＋個別キャッシュ無効化する設計、services.master_merge_service.py等が
    使用）は変更せず、本関数は取込専用の一括処理として新設した。

    途中で例外が発生した場合、get_master_connection()が返す標準のsqlite3.Connection
    のコンテキストマネージャ仕様により、この関数が呼ばれる前の状態にそのまま戻る。

    戻り値：compute_parts_attributes_sync_plan()と同じ形式の差分（実際に適用した内容）。
    """
    plan = compute_parts_attributes_sync_plan(resolved_rows)

    with get_master_connection() as con:
        for part_no, teitori, part_type, supply_type, full_qty in resolved_rows:
            con.execute("""
                INSERT INTO parts_attributes (part_no, teitori, part_type, supply_type, full_qty)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(part_no) DO UPDATE SET
                    teitori = excluded.teitori,
                    part_type = excluded.part_type,
                    supply_type = excluded.supply_type,
                    full_qty = excluded.full_qty,
                    imported_at = datetime('now', 'localtime')
            """, (part_no, teitori, part_type, supply_type, full_qty))
        for item in plan["to_delete"]:
            con.execute("DELETE FROM parts_attributes WHERE part_no = ?", (item["part_no"],))
        con.commit()

    affected_part_nos = (
        [item["part_no"] for item in plan["to_add"]]
        + [item["part_no"] for item in plan["to_update"]]
        + [item["part_no"] for item in plan["to_delete"]]
    )
    for part_no in affected_part_nos:
        invalidate_bom_master_by_part_no(part_no)

    return plan
