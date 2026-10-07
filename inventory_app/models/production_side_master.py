# models/production_side_master.py
"""
生産面マスタのDBアクセス層（2026-10-07新設）。

両面実装の製品では、同じロットに先行面（面1、production_side=1）と
後行面（面2、production_side=2）の計画がある。面1の計画しかkitting_plan_items
に存在しないロットが、「片面の製品」なのか「面2の計画がまだ取り込まれていない
だけ」なのかを、現在のデータ（計画CSV・構成基板数マスター・基板丁数マスター・
BOM）からは区別できない（CANONICAL_DESIGN_DECISIONS.md D-9x参照）。

これを定める参照専用マスタが本モジュール。「(セットアップファイルNo, 実装
ライン) に後行面があるか」を、利用者が別途CSVで取り込んだ一覧から判定する。
後行面の有無はセットアップファイルNoだけでなく実装ラインによっても変わる
（利用者確定事項）ため、キーは両方を組にする。

今回のタスクでは、このマスタの保存・画面・CSVの取込/出力のみを実装する。
実績の取込・計画一覧の判定からの参照は未実装（次段階、D-9x §6参照）。

models.board_structure_master.py・models.parts_attributes.py と同じ
「CSVをマスタとした差分同期」パターン（upsert + 差分同期関数）を踏襲する。
"""
import re
import unicodedata

from models.db_common import get_master_connection

# CSVの「生産面」列の値（和名）と、内部で使う値（kitting_plan_items.
# production_sideと同じ"1"/"2"表記）の対応。既存のproduction_side列の
# 慣習（"1"=面1・"2"=面2）に揃えることで、将来の参照実装（D-9x §6）が
# kitting_plan_items.production_sideとの比較をそのまま行えるようにする。
SIDE_LABEL_TO_VALUE = {"先行面": "1", "後行面": "2"}
SIDE_VALUE_TO_LABEL = {"1": "先行面", "2": "後行面"}


def normalize_setup_file_no(value) -> str:
    """
    セットアップファイルNoの表記ゆれ（全角/半角・前後空白・ゼロ埋め有無）を
    吸収する正規化関数。

    実際のkitting_plan_items.setup_file_noの値を調査した結果（2026-10-07、
    実データ3526行・distinct 494件）、全件が4文字（"0569"のような4桁の
    数字ゼロ埋め、または"T458"のような4文字の英数字コード）であることを
    確認した。これに合わせ、数字のみの値はint変換後に4桁ゼロ埋め
    （"4"→"0004"、"0004"→"0004"で同じ値になる）、数字以外を含む値
    （英数字コード）は前後空白除去・NFKC正規化・大文字化のみを行う
    （桁数が4文字固定で揃っているため、ゼロ埋めの出番がない）。
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if text.isdigit():
        return text.zfill(4)
    return text.upper()


def normalize_mounting_line(value) -> str:
    """
    実装ラインの表記ゆれを吸収する正規化関数。実データ（distinct 10件、
    いずれも単一の半角大文字アルファベット："P","L","S","M","R","D","O",
    "J","K","C"）を確認した上で、NFKC正規化（全角→半角）・前後空白除去・
    大文字化のみを行う（既存データは既にこの形式のため、実運用上は
    ほぼ変換が発生しない想定だが、将来の表記ゆれに備えて正規化自体は行う）。
    """
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip().upper()


def _row_key(setup_file_no_norm: str, mounting_line_norm: str, production_side: str) -> str:
    return f"{setup_file_no_norm}|{mounting_line_norm}|{production_side}"


def init_production_side_master_table():
    """production_side_master テーブルの初期化（既存があれば何もしない）。"""
    with get_master_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS production_side_master (
                row_key TEXT PRIMARY KEY,
                setup_file_no TEXT NOT NULL,
                mounting_line TEXT NOT NULL,
                production_side TEXT NOT NULL,
                bond_flag TEXT,
                common_parts_group TEXT,
                line_priority TEXT,
                takt_time TEXT,
                imported_at TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(setup_file_no, mounting_line, production_side)
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_production_side_master_file_line
            ON production_side_master(setup_file_no, mounting_line)
        """)
        con.commit()


def upsert_production_side(setup_file_no, mounting_line, production_side,
                             bond_flag=None, common_parts_group=None,
                             line_priority=None, takt_time=None):
    """
    (setup_file_no, mounting_line, production_side)をキーに1行を登録・更新する
    （正規化した値をキー・保存値の両方に使う。既存なら上書き、なければ新規登録）。

    production_sideは"1"（先行面）または"2"（後行面）を渡すこと
    （SIDE_LABEL_TO_VALUEで和名から変換してから呼ぶ想定）。
    """
    init_production_side_master_table()
    norm_file = normalize_setup_file_no(setup_file_no)
    norm_line = normalize_mounting_line(mounting_line)
    production_side = str(production_side).strip()
    key = _row_key(norm_file, norm_line, production_side)

    with get_master_connection() as con:
        con.execute("""
            INSERT INTO production_side_master
                (row_key, setup_file_no, mounting_line, production_side,
                 bond_flag, common_parts_group, line_priority, takt_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_key) DO UPDATE SET
                bond_flag = excluded.bond_flag,
                common_parts_group = excluded.common_parts_group,
                line_priority = excluded.line_priority,
                takt_time = excluded.takt_time,
                imported_at = datetime('now', 'localtime')
        """, (key, norm_file, norm_line, production_side,
              bond_flag, common_parts_group, line_priority, takt_time))
        con.commit()


def delete_production_side(setup_file_no, mounting_line, production_side):
    """(setup_file_no, mounting_line, production_side)の1行を削除する（正規化した値で照合）。"""
    init_production_side_master_table()
    norm_file = normalize_setup_file_no(setup_file_no)
    norm_line = normalize_mounting_line(mounting_line)
    production_side = str(production_side).strip()
    key = _row_key(norm_file, norm_line, production_side)
    with get_master_connection() as con:
        con.execute("DELETE FROM production_side_master WHERE row_key = ?", (key,))
        con.commit()


def delete_production_side_group(setup_file_no, mounting_line):
    """(setup_file_no, mounting_line)の全行（先行面・後行面の両方）を削除する（行削除用）。"""
    init_production_side_master_table()
    norm_file = normalize_setup_file_no(setup_file_no)
    norm_line = normalize_mounting_line(mounting_line)
    with get_master_connection() as con:
        con.execute(
            "DELETE FROM production_side_master WHERE setup_file_no = ? AND mounting_line = ?",
            (norm_file, norm_line),
        )
        con.commit()


def delete_production_side_rows_not_in(keep_row_keys) -> list:
    """
    keep_row_keys（row_keyの集合）に含まれない行を削除する
    （CSVをマスタとした差分同期用）。戻り値：実際に削除されたrow_keyのリスト。
    """
    keep_set = set(keep_row_keys)
    init_production_side_master_table()
    with get_master_connection() as con:
        existing = [row["row_key"] for row in con.execute("SELECT row_key FROM production_side_master")]
        to_delete = [k for k in existing if k not in keep_set]
        for key in to_delete:
            con.execute("DELETE FROM production_side_master WHERE row_key = ?", (key,))
        con.commit()
    return to_delete


def get_second_side_status(setup_file_no, mounting_line):
    """
    (setup_file_no, mounting_line)に後行面（面2）があるかを返す。

    戻り値：
      True  ：後行面の登録がある（2回目まである）
      False ：先行面の登録はあるが後行面の登録が無い（片面で完結と判定済み）
      None  ：マスタに登録が無い（不明。「後行面なし」と区別する）

    正規化した値で照合するため、呼び出し元（kitting_plan_items.
    setup_file_no・mounting_line）の表記ゆれ（ゼロ埋み有無等）を気にせず
    渡せる。

    2026-10-08改訂：実際の自動判定（classify_side1_only_plan()）は、本関数
    （実装ライン単位）ではなく get_second_side_status_by_file_no()（ファイルNo
    単位）を使うよう変更した（同じファイルNoで先行面・後行面が異なる実装
    ラインを流れることがあるため、本関数の「同じ実装ラインに後行面がある
    か」という基準では誤って「未登録」「片面」と判定してしまう、D-9x改訂
    参照）。本関数自体は、実装ラインごとの登録状況を参考情報として個別に
    確認する用途（find_registered_file_nos_missing_line_combinations()）の
    ためにそのまま残す。
    """
    init_production_side_master_table()
    norm_file = normalize_setup_file_no(setup_file_no)
    norm_line = normalize_mounting_line(mounting_line)
    with get_master_connection() as con:
        rows = con.execute(
            "SELECT production_side FROM production_side_master WHERE setup_file_no = ? AND mounting_line = ?",
            (norm_file, norm_line),
        ).fetchall()
    if not rows:
        return None
    sides = {row["production_side"] for row in rows}
    return "2" in sides


def compute_file_no_status_map(entries) -> dict:
    """
    生産面マスタの判定ルール（ファイルNo単位）を1か所にまとめた純粋関数
    （2026-10-08新設、D-9x改訂）。DBアクセスを行わないため、保存前（画面編集中、
    未保存の変更を含む）の状態にも、DBから読み込んだ確定済みの状態にも、
    どちらにも使える（実績の取込・計画一覧・生産面マスター画面のいずれも、
    本関数を経由した判定結果を使うこと）。

    entries：{"setup_file_no", "has_side1", "has_side2"}を持つ辞書のイテラブル。
    1件が(setup_file_no, mounting_line)1組の「行」に対応する想定だが、本関数
    自体はmounting_lineを一切見ない（ファイルNo単位に統合するため）。
    has_side1・has_side2が両方Falseの行（画面で両方のチェックを外した行・
    追加直後で未設定の行）は「その実装ラインには登録が無い」行として、
    ファイルNo単位の判定からも除外する（1件も登録が無いことと同じ扱い）。

    戻り値：{setup_file_no: True（いずれかの実装ラインに後行面の登録がある
    ＝2回目あり）/ False（登録はあるがどの実装ラインにも後行面の登録が無い
    ＝1回目のみ）}。該当するentryが1件も無いsetup_file_noはこの辞書に
    現れない（＝未登録、呼び出し側は.get(file_no)がNoneになることで判別する）。
    """
    statuses = {}
    for entry in entries:
        if not entry.get("has_side1") and not entry.get("has_side2"):
            continue
        fn = entry["setup_file_no"]
        if entry.get("has_side2"):
            statuses[fn] = True
        elif fn not in statuses:
            statuses[fn] = False
    return statuses


def get_second_side_status_by_file_no(setup_file_no):
    """
    setup_file_no単位（実装ラインを問わない）で後行面（面2）の有無を返す
    （2026-10-08新設、D-9x改訂）。

    戻り値：
      True  ：いずれかの実装ラインに後行面の登録がある（2回目あり）
      False ：登録はあるが、どの実装ラインにも後行面の登録が無い（1回目のみ）
      None  ：マスタにこのファイルNoの登録が1件も無い（未登録）

    利用者の説明：同じファイルNoで、先行面と後行面が異なる実装ラインを流れる
    ことがある。そのため「(setup_file_no, mounting_line)単位で後行面が
    あるか」（get_second_side_status()）ではなく、ファイルNo単位（実装ライン
    を問わず）で判定する必要がある。classify_side1_only_plan()はこちらを使う。
    """
    init_production_side_master_table()
    norm_file = normalize_setup_file_no(setup_file_no)
    with get_master_connection() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT production_side FROM production_side_master WHERE setup_file_no = ?",
            (norm_file,),
        )]
    entries = [
        {"setup_file_no": norm_file, "has_side1": r["production_side"] == "1", "has_side2": r["production_side"] == "2"}
        for r in rows
    ]
    return compute_file_no_status_map(entries).get(norm_file)


def list_production_side_file_statuses() -> dict:
    """
    生産面マスタの全登録を、setup_file_no単位（実装ラインを問わない）の
    判定結果にまとめて返す（2026-10-08新設、D-9x改訂）。
    find_master_plan_discrepancies()で使う（全件を1回のクエリでまとめて
    取得し、setup_file_noごとにget_second_side_status_by_file_no()を
    個別に呼ぶN+1を避ける）。

    戻り値：{setup_file_no: True（2回目あり）/ False（1回目のみ）}
    （登録が無いファイルNoはこの辞書に現れない）。
    """
    init_production_side_master_table()
    with get_master_connection() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT setup_file_no, production_side FROM production_side_master"
        )]
    entries = [
        {"setup_file_no": r["setup_file_no"], "has_side1": r["production_side"] == "1", "has_side2": r["production_side"] == "2"}
        for r in rows
    ]
    return compute_file_no_status_map(entries)


def list_production_side_groups() -> list:
    """
    一覧表示用：(setup_file_no, mounting_line)単位にグルーピングし、
    先行面・後行面それぞれの有無を持つ行のリストを返す
    （setup_file_no, mounting_line の昇順）。

    各要素：{"setup_file_no", "mounting_line", "has_side1", "has_side2"}
    """
    init_production_side_master_table()
    with get_master_connection() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT setup_file_no, mounting_line, production_side FROM production_side_master "
            "ORDER BY setup_file_no, mounting_line"
        )]
    groups = {}
    order = []
    for row in rows:
        key = (row["setup_file_no"], row["mounting_line"])
        if key not in groups:
            groups[key] = {"setup_file_no": key[0], "mounting_line": key[1], "has_side1": False, "has_side2": False}
            order.append(key)
        if row["production_side"] == "1":
            groups[key]["has_side1"] = True
        elif row["production_side"] == "2":
            groups[key]["has_side2"] = True
    return [groups[k] for k in order]


def get_production_side_count_summary() -> dict:
    """登録件数表示用（ui/production_side_master_window.py）。(setup_file_no, mounting_line)組の件数を返す。"""
    groups = list_production_side_groups()
    return {"total": len(groups)}


def compute_production_side_sync_plan(resolved_rows: list) -> dict:
    """
    resolved_rows（[{"setup_file_no", "mounting_line", "production_side",
    "bond_flag", "common_parts_group", "line_priority", "takt_time"}, ...]、
    CSV内の重複キーは呼び出し元で既に解決済み・1キー1件である前提。
    setup_file_no/mounting_lineは正規化済みの値を渡すこと）を、現在のテーブル
    内容と比較し、差分（追加・更新・変更なし・削除）を返す。

    「更新」の判定対象は補助列（bond_flag・common_parts_group・line_priority・
    takt_time）の値のみ（キー自体が変わる「更新」は無い、キーが変われば
    別行の追加・削除になる）。

    読み取りのみでDBへの書き込みは行わない（取込前の確認ダイアログ用）。
    apply_production_side_sync()からも、実際の反映前の差分計算として再利用する。
    """
    init_production_side_master_table()
    with get_master_connection() as con:
        existing = {
            row["row_key"]: dict(row)
            for row in con.execute("SELECT * FROM production_side_master")
        }

    to_add, to_update, unchanged = [], [], []
    keep_keys = set()
    for row in resolved_rows:
        key = _row_key(row["setup_file_no"], row["mounting_line"], row["production_side"])
        keep_keys.add(key)
        aux_new = (row.get("bond_flag"), row.get("common_parts_group"), row.get("line_priority"), row.get("takt_time"))
        if key not in existing:
            to_add.append({**row, "row_key": key})
        else:
            old = existing[key]
            aux_old = (old.get("bond_flag"), old.get("common_parts_group"), old.get("line_priority"), old.get("takt_time"))
            if aux_old != aux_new:
                to_update.append({**row, "row_key": key, "old": aux_old, "new": aux_new})
            else:
                unchanged.append({**row, "row_key": key})

    to_delete = [
        {"row_key": key, "setup_file_no": row["setup_file_no"], "mounting_line": row["mounting_line"],
         "production_side": row["production_side"]}
        for key, row in existing.items() if key not in keep_keys
    ]

    return {"to_add": to_add, "to_update": to_update, "unchanged": unchanged, "to_delete": to_delete}


def apply_production_side_sync(resolved_rows: list) -> dict:
    """
    resolved_rows（compute_production_side_sync_plan()と同じ形式）の内容で、
    登録・更新・削除を1つのトランザクションにまとめて確定する。

    途中で例外が発生した場合、get_master_connection()が返す標準のsqlite3.
    Connectionのコンテキストマネージャ仕様（例外時rollback・正常終了時commit）
    により、呼ばれる前の状態にそのまま戻る。

    戻り値：compute_production_side_sync_plan()と同じ形式の差分（実際に適用した内容）。
    """
    plan = compute_production_side_sync_plan(resolved_rows)

    with get_master_connection() as con:
        for row in resolved_rows:
            key = _row_key(row["setup_file_no"], row["mounting_line"], row["production_side"])
            con.execute("""
                INSERT INTO production_side_master
                    (row_key, setup_file_no, mounting_line, production_side,
                     bond_flag, common_parts_group, line_priority, takt_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(row_key) DO UPDATE SET
                    bond_flag = excluded.bond_flag,
                    common_parts_group = excluded.common_parts_group,
                    line_priority = excluded.line_priority,
                    takt_time = excluded.takt_time,
                    imported_at = datetime('now', 'localtime')
            """, (key, row["setup_file_no"], row["mounting_line"], row["production_side"],
                  row.get("bond_flag"), row.get("common_parts_group"), row.get("line_priority"), row.get("takt_time")))
        for item in plan["to_delete"]:
            con.execute("DELETE FROM production_side_master WHERE row_key = ?", (item["row_key"],))
        con.commit()

    return plan


def list_production_side_rows_raw() -> list:
    """CSV出力用：登録済みの全行を生の形（1行=1(setup_file_no, mounting_line, production_side)）で返す。"""
    init_production_side_master_table()
    with get_master_connection() as con:
        return [dict(r) for r in con.execute(
            "SELECT setup_file_no, mounting_line, production_side, bond_flag, common_parts_group, "
            "line_priority, takt_time FROM production_side_master ORDER BY setup_file_no, mounting_line, production_side"
        )]
