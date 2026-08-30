# models/parts_attributes.py
"""
部品属性マスタ（丁取り数等）のDBアクセス層（フェーズ3・新BOM基盤統合）。

新BOM計算ロジック（services.bom_service.BOMService._calculate_bom）で、
BOM TSVの係数が0かつRフラグがある行の qty 計算に丁取り数（teitori）を使う
（qty = 部品員数 ÷ 丁取り数）。
"""
import sqlite3

import config
from models.bom_master import invalidate_bom_master_by_part_no


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_parts_attributes_table():
    """parts_attributes テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
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
    with get_connection() as con:
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
    with get_connection() as con:
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
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, teitori, part_type, supply_type, full_qty
            FROM parts_attributes WHERE part_no = ?
        """, (part_no,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_parts_attributes() -> list:
    """部品属性の一覧を part_no 順で取得する（インポート画面の一覧表示用）。"""
    init_parts_attributes_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, teitori, part_type, supply_type, full_qty
            FROM parts_attributes ORDER BY part_no
        """)
        return [dict(row) for row in cur.fetchall()]
