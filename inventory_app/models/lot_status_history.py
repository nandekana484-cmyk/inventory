# models/lot_status_history.py
"""
ロット状態（services.production_service._evaluate_lot_status()の結果）を、
実績の登録・修正のたびに履歴として自動で残すテーブル。

既存の operation_log（models/operation_log.py、「いつ・誰が・どの操作をしたか」を
記録する機能）と同じ位置づけの記録機能だが、記録する対象が「ロットの構成基板数・
引落・仕掛・未生産の状態のスナップショット」である点が異なる。operation_log と
同様、models層・services層に共通の書き込みフックを新設するのではなく、実績の
登録・修正が実際に行われる各箇所（services/production_service.py の
register_daily_result()・overwrite_daily_result()・update_daily_result()・
delete_daily_result()、services/db_migration_carryover.py の月次DB引き継ぎ処理）に
個別に record_lot_status_snapshot() の呼び出しを追加する方針を踏襲する
（一元化しない理由・調査経緯は調査時のやり取り、および operation_log導入時の
UI_WORKFLOW_FIXES_NOTES.md グループAA（AA-4）参照）。

files_detail 列には、_evaluate_lot_status() が返す "boards"（board_name単位の
ファイルNo.内訳）と "missing_file_entries"（構成基板数不足時の「未確定」仮想
エントリ）を、board_name単位で省略せず全てフラット化したJSON配列を保存する
（1ロットに複数board_nameが存在するケースが実データで268件確認されている、
CANONICAL_DESIGN_DECISIONS.md D-25参照）。missing_file_entries自体は
board_nameを持たないため、boardsの先頭（代表1件）のboard_nameを補って使う
（_evaluate_lot_status()の「未確定」仮想行がboard_nameを代表1件で表示している
のと同じ妥協。正確な対応関係を表すものではない）。

total_surplus_qty・total_not_produced_qty は、files_detail中の全エントリの
surplus_qty・not_produced_qtyの合計をそのまま非正規化して保持する列。
「そのロットが仕掛0・未生産0になった最初の記録」をJSON解析無しで検索できる
ようにするための列であり、files_detailを都度パースしなくても
WHERE total_surplus_qty = 0 AND total_not_produced_qty = 0 のような
単純な条件でインデックスを効かせて検索できる。
"""
import json

from models.db_common import get_connection


def init_lot_status_history_table():
    """lot_status_history テーブルの初期化（既存があれば何もしない）。"""
    with get_connection() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS lot_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                lot_no TEXT NOT NULL,
                status TEXT NOT NULL,
                board_count REAL,
                visible_file_no_count INTEGER,
                files_detail TEXT NOT NULL,
                total_surplus_qty REAL,
                total_not_produced_qty REAL,
                trigger_source TEXT
            )
        """)
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_lot_status_history_lot_no
            ON lot_status_history(lot_no, recorded_at)
        """)
        con.commit()


def _flatten_files_detail(lot_status: dict) -> list:
    """
    evaluate_lot_status()の戻り値（"boards"・"missing_file_entries"）を、
    board_name・setup_file_no・production_side・order_qty・file_actual・
    lot_completed・surplus_qty・not_produced_qty を持つオブジェクトの
    単一のリストへフラット化する。

    "boards"は[{"board_name", "board_count", "files": [...]}, ...]という
    board_name単位のネスト構造のため、board_nameを各fileエントリへ複製して
    展開する。"missing_file_entries"（shortfall時のみ非空）はboard_nameを
    持たないため、boardsの先頭（代表1件）のboard_nameを補う
    （boardsが空になるのはregistered_countsが空の場合＝status=="unregistered"の
    ときのみで、missing_file_entriesはstatus=="shortfall"のときにしか作られない
    ため、この2つが同時に起きることは無い。念のためboards空の場合はNoneとする）。
    """
    boards = lot_status["boards"]
    representative_board_name = boards[0]["board_name"] if boards else None

    entries = []
    for board in boards:
        board_name = board["board_name"]
        for f in board["files"]:
            entries.append({
                "board_name": board_name,
                "setup_file_no": f["setup_file_no"],
                "production_side": f["production_side"],
                "order_qty": f["order_qty"],
                "file_actual": f["file_actual"],
                "lot_completed": f["lot_completed"],
                "surplus_qty": f["surplus_qty"],
                "not_produced_qty": f["not_produced_qty"],
            })

    for mf in lot_status["missing_file_entries"]:
        entries.append({
            "board_name": representative_board_name,
            "setup_file_no": mf["setup_file_no"],
            "production_side": mf["production_side"],
            "order_qty": mf["order_qty"],
            "file_actual": mf["file_actual"],
            "lot_completed": mf["lot_completed"],
            "surplus_qty": mf["surplus_qty"],
            "not_produced_qty": mf["not_produced_qty"],
        })

    return entries


def record_lot_status_snapshot(lot_no: str, trigger_source: str) -> int:
    """
    services.production_service.evaluate_lot_status(lot_no) を呼び、その結果を
    lot_status_history に1行INSERTする。戻り値：INSERTした行のid。

    services.production_service を関数内で遅延importする（models層のモジュールが
    services層に依存する、通常とは逆方向の依存になるが、record_lot_status_
    snapshot()の実装をこのモジュールに置くという要件のためにあえて許容する。
    production_service.py側もこのモジュールをトップレベルでimportするため、
    双方がトップレベルimportし合うと循環importになる。呼ばれる時点まで解決を
    遅延させることでこれを回避する、ui層で確立済みの「循環import回避のため
    ここで都度importする」パターンと同じ対処）。

    本関数は失敗時（対象lot_noの計画が見つからない場合のValueError、DB書き込み
    エラー等）に例外をそのまま送出する。実績登録・修正処理自体を失敗させたくない
    呼び出し元は、本関数の呼び出し側でtry/exceptにより捕捉すること
    （services/production_service.py::register_daily_result()等の実装を参照。
    「記録自体の失敗が、実績登録・修正という本来の処理を失敗させてはならない」
    という要件があるため、本関数自体はその判断を行わず、素直に例外を伝播する
    のみに留める）。
    """
    from services.production_service import evaluate_lot_status

    lot_status = evaluate_lot_status(lot_no)
    files_detail = _flatten_files_detail(lot_status)
    total_surplus_qty = sum(f["surplus_qty"] for f in files_detail)
    total_not_produced_qty = sum(f["not_produced_qty"] for f in files_detail)

    init_lot_status_history_table()
    with get_connection() as con:
        cur = con.execute("""
            INSERT INTO lot_status_history (
                recorded_at, lot_no, status, board_count, visible_file_no_count,
                files_detail, total_surplus_qty, total_not_produced_qty, trigger_source
            ) VALUES (datetime('now','localtime'), ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lot_no,
            lot_status["status"],
            lot_status["board_count"],
            len(lot_status["visible_file_nos"]),
            json.dumps(files_detail, ensure_ascii=False),
            total_surplus_qty,
            total_not_produced_qty,
            trigger_source,
        ))
        con.commit()
        return cur.lastrowid


def list_lot_status_history(lot_no: str) -> list:
    """指定lot_noの履歴を記録日時の昇順で取得する（動作確認・将来のUI表示用）。"""
    init_lot_status_history_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT * FROM lot_status_history WHERE lot_no = ? ORDER BY recorded_at, id
        """, (lot_no,))
        return [dict(row) for row in cur.fetchall()]


def list_latest_snapshot_per_lot(cutoff: str) -> dict:
    """
    recorded_at <= cutoff （"YYYY-MM-DD HH:MM:SS"形式の文字列。recorded_atは
    datetime('now','localtime')で常にこの形式・ゼロ埋めで記録されるため、
    文字列比較でそのまま日時の大小判定に使える）の範囲で、lot_noごとに
    最新（recorded_atが最大、同時刻の場合はid最大）の1行を返す
    （services.lot_status_history.get_daily_drawdown()が「指定日以前で
    最新の記録」を求めるために使う）。

    実装方針：SQLの相関サブクエリ・ウィンドウ関数を使わず、対象行を
    recorded_at昇順・id昇順で1回だけ取得し、Python側でlot_noごとに
    「後勝ち」で辞書に格納する（同一lot_noの行を後から処理するたびに
    上書きされるため、最終的に各lot_noには最も新しい行だけが残る）。
    古いSQLite（ウィンドウ関数非対応）でも確実に動く単純な方法を優先した。

    戻り値：{lot_no: 行dict（列は全てそのまま。files_detailは文字列の
    ままでJSON化していない、呼び出し元でjson.loads()すること）, ...}。
    該当する記録が1件も無いlot_noはキーとして含まれない。
    """
    init_lot_status_history_table()
    with get_connection() as con:
        cur = con.execute("""
            SELECT * FROM lot_status_history
            WHERE recorded_at <= ?
            ORDER BY recorded_at ASC, id ASC
        """, (cutoff,))
        rows = [dict(row) for row in cur.fetchall()]

    latest = {}
    for row in rows:
        latest[row["lot_no"]] = row
    return latest
