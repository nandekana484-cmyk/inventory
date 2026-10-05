# models/board_structure_master.py
"""
構成基板数マスタのDBアクセス層。

kitting_plan_items.board_name（基板名）単位に「構成基板数」を保持する参照専用
マスタ。CSVインポート（ui/board_structure_import_window.py）でのみ更新され、
生産実績入力画面（ui/kitting_production_entry.py）の計画情報欄で参照表示にのみ
使う（他のBOM計算・実績登録処理からは参照されない）。

models/parts_attributes.py と同じ「CSVをマスタとした差分同期」パターン
（upsert + delete_..._not_in()）を踏襲する。
"""
import re
import unicodedata

from models.db_common import get_master_connection


def normalize_board_name(name):
    """
    基板名の表記ゆれ（全角/半角、大文字/小文字、空白）を吸収する正規化関数。

    services.production_import_service.normalize_product_name() と同一ロジック
    （NFKC正規化＋小文字化＋前後空白除去＋連続空白の圧縮）。models層から
    services層への依存を作らないため、ここに複製している（4行程度の純粋関数の
    ため、共通化による依存関係の複雑化よりも複製の方が実装・保守ともに単純と
    判断した）。
    """
    if name is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(name))
    normalized = normalized.lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def init_board_structure_master_table():
    """board_structure_master テーブルの初期化（既存があれば何もしない）。"""
    with get_master_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS board_structure_master (
                board_name TEXT PRIMARY KEY,
                board_count REAL,
                board_name_normalized TEXT NOT NULL,
                imported_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_board_structure_master_normalized
            ON board_structure_master(board_name_normalized)
        """)
        con.commit()


def upsert_board_structure(board_name: str, board_count):
    """
    基板名（board_name、CSV上の表記そのまま）をキーに構成基板数を登録・更新する。
    既存なら上書き、なければ新規登録する（差分検知は行わず常に上書き）。

    board_name_normalized（表記ゆれ吸収用）も同時に算出・保存し、
    get_board_structure() 側の検索で使う。
    """
    init_board_structure_master_table()
    normalized = normalize_board_name(board_name)
    with get_master_connection() as con:
        con.execute("""
            INSERT INTO board_structure_master (board_name, board_count, board_name_normalized)
            VALUES (?, ?, ?)
            ON CONFLICT(board_name) DO UPDATE SET
                board_count = excluded.board_count,
                board_name_normalized = excluded.board_name_normalized,
                imported_at = datetime('now', 'localtime')
        """, (board_name, board_count, normalized))
        con.commit()


def delete_board_structure_not_in(keep_board_name_list) -> list:
    """
    keep_board_name_list に含まれない board_structure_master 行を削除する
    （CSVをマスタとした差分同期用。CSVに存在しない board_name を削除する）。

    比較は models.parts_attributes.delete_parts_attributes_not_in() と同様、
    CSV上の表記そのまま（board_name、正規化前）で行う（呼び出し元がCSV読み込み時に
    見た表記そのものをそのまま渡す想定のため）。

    削除対象は現在のテーブル全件と keep_board_name_list を比較して特定し、
    実際の削除は1コネクション・1トランザクション内で行う。

    戻り値：実際に削除された board_name のリスト。
    """
    keep_set = set(keep_board_name_list)
    init_board_structure_master_table()
    with get_master_connection() as con:
        existing = [row["board_name"] for row in con.execute("SELECT board_name FROM board_structure_master")]
        to_delete = [b for b in existing if b not in keep_set]
        for board_name in to_delete:
            con.execute("DELETE FROM board_structure_master WHERE board_name = ?", (board_name,))
        con.commit()

    return to_delete


def get_board_structure(board_name: str):
    """
    指定board_nameの構成基板数を取得する。存在しなければ None を返す。

    正規化した値（normalize_board_name()）で検索するため、キッティング計画側
    （kitting_plan_items.board_name）とマスタ側（CSVの「基板名」列）の間で
    全角/半角・大小文字・空白の表記が完全一致していなくても引き当てられる。

    正規化後に複数件が一致する場合（マスタ側に表記ゆれ違いの重複行がある場合）は
    board_name の昇順で先頭の1件を返す（呼び出し元は複数該当の可能性を
    意識する必要はないが、どの行が返るかはマスタの登録内容に依存する）。
    """
    init_board_structure_master_table()
    normalized = normalize_board_name(board_name)
    with get_master_connection() as con:
        cur = con.execute("""
            SELECT board_name, board_count FROM board_structure_master
            WHERE board_name_normalized = ?
            ORDER BY board_name
            LIMIT 1
        """, (normalized,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_board_structure() -> list:
    """構成基板数マスタの一覧を board_name 順で取得する（インポート画面の一覧表示用）。"""
    init_board_structure_master_table()
    with get_master_connection() as con:
        cur = con.execute("""
            SELECT board_name, board_count FROM board_structure_master ORDER BY board_name
        """)
        return [dict(row) for row in cur.fetchall()]


def get_board_structure_count_summary() -> dict:
    """
    登録件数表示用（ui/board_structure_import_window.py）。
    総件数と、構成基板数（board_count）が未設定（NULL）の件数を返す。
    """
    init_board_structure_master_table()
    with get_master_connection() as con:
        total = con.execute("SELECT COUNT(*) AS c FROM board_structure_master").fetchone()["c"]
        missing = con.execute(
            "SELECT COUNT(*) AS c FROM board_structure_master WHERE board_count IS NULL"
        ).fetchone()["c"]
    return {"total": total, "board_count_missing": missing}


def compute_board_structure_sync_plan(resolved_rows: list) -> dict:
    """
    resolved_rows（[(board_name, board_count), ...]、CSV内の重複キーは呼び出し元で
    既に「最後の行の値」に解決済み・board_name単位で1件ずつである前提）を
    現在のテーブル内容と比較し、差分（追加・更新・変更なし・削除）を返す。

    読み取りのみでDBへの書き込みは行わない（インポート前の確認ダイアログ用）。
    apply_board_structure_sync()からも、実際の反映前の差分計算として再利用する。
    """
    init_board_structure_master_table()
    with get_master_connection() as con:
        existing = {
            row["board_name"]: row["board_count"]
            for row in con.execute("SELECT board_name, board_count FROM board_structure_master")
        }

    to_add, to_update, unchanged = [], [], []
    keep_names = set()
    for board_name, board_count in resolved_rows:
        keep_names.add(board_name)
        if board_name not in existing:
            to_add.append({"board_name": board_name, "new_value": board_count})
        elif existing[board_name] != board_count:
            to_update.append({
                "board_name": board_name, "old_value": existing[board_name], "new_value": board_count,
            })
        else:
            unchanged.append({"board_name": board_name, "value": board_count})

    to_delete = [
        {"board_name": name, "value": value}
        for name, value in existing.items() if name not in keep_names
    ]

    return {"to_add": to_add, "to_update": to_update, "unchanged": unchanged, "to_delete": to_delete}


def apply_board_structure_sync(resolved_rows: list) -> dict:
    """
    resolved_rows（compute_board_structure_sync_plan()と同じ形式）の内容で、
    登録・更新・削除を**1つのトランザクション**にまとめて確定する。

    途中で例外が発生した場合、get_master_connection()が返す標準のsqlite3.Connection
    のコンテキストマネージャ仕様（例外発生時はrollback()、正常終了時はcommit()）に
    より、この関数が呼ばれる前の状態にそのまま戻る（一部だけ反映された中途半端な
    状態にはならない）。

    既存のupsert_board_structure()・delete_board_structure_not_in()（1行・1処理
    ごとに個別コミットする設計、services.master_merge_service.py等が使用）は
    変更せず、本関数はそれらとは別の取込専用の一括処理として新設した。

    戻り値：compute_board_structure_sync_plan()と同じ形式の差分（実際に適用した内容）。
    """
    plan = compute_board_structure_sync_plan(resolved_rows)

    with get_master_connection() as con:
        for board_name, board_count in resolved_rows:
            normalized = normalize_board_name(board_name)
            con.execute("""
                INSERT INTO board_structure_master (board_name, board_count, board_name_normalized)
                VALUES (?, ?, ?)
                ON CONFLICT(board_name) DO UPDATE SET
                    board_count = excluded.board_count,
                    board_name_normalized = excluded.board_name_normalized,
                    imported_at = datetime('now', 'localtime')
            """, (board_name, board_count, normalized))
        for item in plan["to_delete"]:
            con.execute("DELETE FROM board_structure_master WHERE board_name = ?", (item["board_name"],))
        con.commit()

    return plan
