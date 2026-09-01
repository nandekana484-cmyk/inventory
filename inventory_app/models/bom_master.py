# models/bom_master.py
"""
BOM基盤用のDBアクセス層。

共有フォルダのTSVから計算したBOM（file_no・面・96コード単位の構成数）を
月（data_ym）単位でキャッシュ保存し、以降は再計算せずDBから取得できるようにする。
"""
import sqlite3
from datetime import datetime

import config


def get_connection():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_bom_master_table():
    """
    bom_master テーブルの初期化（既存があれば何もしない）。

    mounting_line（実装ライン）列は db/migration_011 で、item_type（区分：
    "part"=通常部品／"board"=基板自身の消費枚数）列は db/migration_013 で、
    それぞれ既存テーブルに追加済みの前提（新規環境ではここで最初から両方
    込みで作成される）。
    """
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS bom_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_no TEXT NOT NULL,
                production_side INTEGER NOT NULL,
                mounting_line TEXT NOT NULL DEFAULT '',
                part_no TEXT NOT NULL,
                qty_per_product REAL NOT NULL,
                item_type TEXT NOT NULL DEFAULT 'part',
                data_ym TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                source_file_hash TEXT,
                UNIQUE(file_no, production_side, mounting_line, part_no, data_ym)
            )
        """)
        con.commit()


def get_current_ym() -> str:
    """当月を 'YYYYMM' 形式で返す。"""
    return datetime.now().strftime("%Y%m")


def query_bom_master(file_no: str, side: int, mounting_line: str = "", data_ym: str = None) -> list:
    """
    file_no・side・実装ライン（・data_ym）に対応する bom_master の行を取得する。
    data_ym を省略した場合は当月（get_current_ym()）分を対象とする。

    mounting_line：呼び出し元（services.bom_service.get_parts_for_file_no）で
    未指定（None）だった場合は空文字列("")に正規化して渡すこと
    （SQLiteのUNIQUE制約はNULL同士を別物として扱うため、ON CONFLICTを効かせる
    には具体的な値―空文字列―に揃える必要がある）。

    戻り値：[{"part_no": ..., "qty_per_product": ..., "item_type": ...}, ...]
             （該当なしは空リスト。item_typeは"part"=通常部品／"board"=基板自身の
             消費枚数のいずれか）
    """
    init_bom_master_table()
    if data_ym is None:
        data_ym = get_current_ym()

    with get_connection() as con:
        cur = con.execute("""
            SELECT part_no, qty_per_product, item_type
            FROM bom_master
            WHERE file_no = ? AND production_side = ? AND mounting_line = ? AND data_ym = ?
            ORDER BY part_no
        """, (file_no, side, mounting_line, data_ym))
        return [dict(row) for row in cur.fetchall()]


def invalidate_bom_master_by_part_no(part_no: str) -> int:
    """
    指定 part_no を含む bom_master キャッシュを無効化する。

    bom_master は (file_no, production_side, mounting_line, data_ym) 単位で
    キャッシュされ、query_bom_master() の呼び出し側
    （services.bom_service.get_parts_for_file_no）は「1件でも返ってくれば
    キャッシュヒット」として扱う。そのため、該当 part_no の行だけを削除すると、
    その (file_no, production_side, mounting_line, data_ym) の残りの部品行は
    キャッシュに残ったまま返され続け、変更した part_no だけが結果から欠落した状態が
    再計算されずに固定化されてしまう。

    これを避けるため、該当 part_no を含む (file_no, production_side,
    mounting_line, data_ym) の組み合わせを特定し、その組み合わせに属する行を
    丸ごと削除する（＝次回 query_bom_master() を完全なキャッシュミスにし、
    _calculate_bom() で全部品を再計算させる）。

    戻り値：削除した行数。
    """
    init_bom_master_table()
    with get_connection() as con:
        cur = con.execute("""
            DELETE FROM bom_master
            WHERE (file_no, production_side, mounting_line, data_ym) IN (
                SELECT DISTINCT file_no, production_side, mounting_line, data_ym
                FROM bom_master
                WHERE part_no = ?
            )
        """, (part_no,))
        con.commit()
        return cur.rowcount


def save_bom_master(file_no: str, side: int, parts: list, mounting_line: str = "",
                     data_ym: str = None, source_file_hash: str = None):
    """
    BOM計算結果（parts）を bom_master へ保存する。
    data_ym を省略した場合は当月（get_current_ym()）を使う。

    parts：[{"part_no": ..., "qty_per_product": ..., "item_type": ...}, ...]
    item_typeは省略可（省略時は"part"として保存する。呼び出し元
    services.bom_service._calculate_bom() は常に明示的に付与する）。

    mounting_line：query_bom_master() と同様、呼び出し元で未指定だった場合は
    空文字列("")に正規化して渡すこと。

    同一 (file_no, production_side, mounting_line, part_no, data_ym) は
    UNIQUE制約により上書き更新する（常に上書き。差分検知は行わない）。
    """
    init_bom_master_table()
    if data_ym is None:
        data_ym = get_current_ym()

    with get_connection() as con:
        for part in parts:
            con.execute("""
                INSERT INTO bom_master (
                    file_no, production_side, mounting_line, part_no,
                    qty_per_product, item_type, data_ym, source_file_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_no, production_side, mounting_line, part_no, data_ym) DO UPDATE SET
                    qty_per_product = excluded.qty_per_product,
                    item_type = excluded.item_type,
                    imported_at = datetime('now', 'localtime'),
                    source_file_hash = excluded.source_file_hash
            """, (file_no, side, mounting_line, part["part_no"], part["qty_per_product"],
                  part.get("item_type", "part"), data_ym, source_file_hash))
        con.commit()
