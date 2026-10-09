# services/lot_status_history.py
"""
日々の引落一覧の集計機能。

2026-10-09、D-115（登録した時刻ベース）からD-116（払出し日・入力日ベース）へ
変更した（D-29改訂、CANONICAL_DESIGN_DECISIONS.md参照）。

- 払出し日・入力日：production_daily.report_date。CSV由来の実績は払出し日、
  手入力の実績は入力した日（いずれも既に同列にそのまま入っている、D-115の
  調査で確認済み）。
- 引落：ロットの中で揃った数量（ロットの完成数の増分）。
- 引落日：揃った数量が増えた日。個々の実績の日付そのものではなく、各実績の
  日付をもとに「その日までに揃った数量」（services.production_service.
  evaluate_all_lot_status(cutoff_date=...)）を計算して決める。

この変更により、models.lot_status_history（recorded_at、実績の登録・修正の
たびに書き込まれる壁時計時刻ベースのイベントログ）は本機能からは一切
参照しなくなった。lot_status_historyへの記録自体（models.lot_status_history.
record_lot_status_snapshot()の呼び出し）は変更・削除していない（他の用途に
備えて記録は継続する。2026-10-09時点で、本機能以外にlot_status_historyの
記録を読んでいる機能は無いことを確認済み）。

D-29改訂（過去に表示した引落の数字が後から変わらない、という以前の方針）：
対象日・前日時点の完成数を**都度再計算**する設計のため、計算のもとになる
実績（production_daily）・構成基板数マスタ（board_structure_master）・計画
（kitting_plan_items）のいずれかが変われば、過去の日付の引落も事後的に
変わりうる。これは実データの検証で実際に確認済み（構成基板数マスタの
更新だけで、過去にmatchだったロットがshortfallへ変わり、引落が遡って0に
なる例が実在した）。逆に、もとのデータに変更が無ければ、過去の日付の
結果は何度計算しても完全に同じになる（都度の再計算が同じ入力から同じ
計算を行うだけのため）。
"""
from services.production_service import evaluate_all_lot_status


def _cutoff_for_date(date_str: str) -> str:
    """report_dateとの文字列比較にそのまま使える形式（"YYYY-MM-DD"）をそのまま返す。"""
    return date_str


def _previous_date(date_str: str) -> str:
    from datetime import datetime, timedelta
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


def _flatten_files(lot_status: dict) -> list:
    """
    services.production_service._evaluate_lot_status()（evaluate_all_lot_status()
    経由）の戻り値の"boards"（board_name単位）・"missing_file_entries"
    （shortfall時の未確定仮想エントリ）を、board_name単位のネストを解いた
    単一のリストへフラット化する。models.lot_status_history._flatten_files_
    detail()と全く同じ入力形状（"boards"・"missing_file_entries"）を受け取る
    ため、同じロジックをここに持つ（record_lot_status_snapshot()の記録経路とは
    独立に、日々の引落一覧専用の経路としてここで持つ。lot_status_historyの
    記録処理自体には手を加えない）。
    """
    boards = lot_status["boards"]
    entries = []
    for board in boards:
        board_name = board["board_name"]
        for f in board["files"]:
            entries.append({
                "board_name": board_name,
                "setup_file_no": f["setup_file_no"],
                "production_side": f["production_side"],
                "lot_completed": f["lot_completed"],
                "surplus_qty": f["surplus_qty"],
                "not_produced_qty": f["not_produced_qty"],
            })
    representative_board_name = boards[0]["board_name"] if boards else None
    for mf in lot_status["missing_file_entries"]:
        entries.append({
            "board_name": representative_board_name,
            "setup_file_no": mf["setup_file_no"],
            "production_side": mf["production_side"],
            "lot_completed": mf["lot_completed"],
            "surplus_qty": mf["surplus_qty"],
            "not_produced_qty": mf["not_produced_qty"],
        })
    return entries


def get_daily_drawdown(target_date: str) -> list:
    """
    指定日（target_date、"YYYY-MM-DD"）に増えた引落を、ロット・ファイルNo単位で
    算出する（2026-10-09、払出し日・入力日ベースへ変更。D-115/D-116参照）。

    考え方：
      1. 「対象日までの実績（report_date <= target_date）だけを使った完成数」と
         「前日までの実績（report_date <= 前日）だけを使った完成数」を、
         services.production_service.evaluate_all_lot_status(cutoff_date=...)で
         全アクティブロット分まとめて計算する（1回の表示につき構成基板数
         マスタ検索は2回分、全ロット共通でキャッシュ・使い回す。
         get_board_structure()自体の呼び出し回数は実データで1057ロット×2回
         程度だが、同一board_nameの再検索はPython内で重複排除されるため
         実測で1秒程度に収まることを確認済み、D-116検証参照）。
      2. 両者のfiles（(setup_file_no, production_side)単位のlot_completed）を
         突き合わせ、差分を計算する。
      3. 差分が0のファイルNoは結果から除外する（「その日に引落が増えた」行の
         みを返す。従来通り、差分が負になった場合も除外しない＝そのまま表示
         する。実データでの検証では負の値は1件も発生しなかったが、構造上は
         実績の修正で起こりうる）。

    「対象日以前の実績が1件も無いlot_no」は、そもそも比較対象が無いため
    結果に含めない（完成数が常に0のロットは、対象日・前日いずれでも0のまま
    のため、diff==0で自動的に除外される。わざわざ除外処理を書く必要はない）。

    一覧の行の単位（ロットNo×ファイルNo×面）・列の構成（surplus_qty・
    not_produced_qty等）は、以前のrecorded_atベースの実装と完全に同じ。

    戻り値：[{"lot_no", "board_name", "setup_file_no", "production_side",
              "daily_drawdown"（その日の引落の増分）,
              "cumulative_lot_completed"（指定日時点の累計引落、
              ロット内の全ファイルNoで共通の値）,
              "surplus_qty"（指定日時点の仕掛）,
              "not_produced_qty"（指定日時点の未生産）}, ...]
    lot_no・setup_file_noの昇順でソート済み。
    """
    cutoff_today = _cutoff_for_date(target_date)
    cutoff_prev = _cutoff_for_date(_previous_date(target_date))

    # 構成基板数マスタの検索結果を、対象日・前日の2回の評価をまたいで
    # 使い回す（1回の表示の中でのみ有効な辞書。呼び出しが終われば破棄される）。
    board_structure_cache = {}
    results_today = evaluate_all_lot_status(cutoff_date=cutoff_today, board_structure_cache=board_structure_cache)
    results_prev = evaluate_all_lot_status(cutoff_date=cutoff_prev, board_structure_cache=board_structure_cache)
    prev_by_lot = {r["lot_no"]: r for r in results_prev}

    rows = []
    for today_status in results_today:
        lot_no = today_status["lot_no"]
        prev_status = prev_by_lot.get(lot_no)

        today_files = _flatten_files(today_status)
        prev_files = _flatten_files(prev_status) if prev_status is not None else []

        today_by_key = {(f["setup_file_no"], f["production_side"]): f for f in today_files}
        prev_by_key = {(f["setup_file_no"], f["production_side"]): f for f in prev_files}

        all_keys = set(today_by_key) | set(prev_by_key)
        for key in all_keys:
            today_f = today_by_key.get(key)
            prev_f = prev_by_key.get(key)
            today_completed = (today_f["lot_completed"] if today_f else 0) or 0
            prev_completed = (prev_f["lot_completed"] if prev_f else 0) or 0
            diff = today_completed - prev_completed
            if diff == 0:
                continue

            representative = today_f or prev_f
            setup_file_no, production_side = key
            rows.append({
                "lot_no": lot_no,
                "board_name": representative.get("board_name"),
                "setup_file_no": setup_file_no,
                "production_side": production_side,
                "daily_drawdown": diff,
                "cumulative_lot_completed": today_completed,
                "surplus_qty": (today_f["surplus_qty"] if today_f else 0) or 0,
                "not_produced_qty": (today_f["not_produced_qty"] if today_f else 0) or 0,
            })

    rows.sort(key=lambda r: (r["lot_no"], str(r["setup_file_no"])))
    return rows


def get_drawdown_date_range():
    """
    引落のあるデータの日付の範囲（production_daily.report_dateの最小値・
    最大値）を返す。日々の引落一覧の対象日選択の手がかり表示・初期値決定用
    （2026-10-09追加、D-116）。

    戻り値："YYYY-MM-DD"形式の(最初の日, 最後の日)のタプル。production_daily
    に実績が1件も無い場合は(None, None)。
    """
    from models.db_common import get_connection
    with get_connection() as con:
        row = con.execute(
            "SELECT MIN(report_date) AS min_date, MAX(report_date) AS max_date FROM production_daily"
        ).fetchone()
        return row["min_date"], row["max_date"]


# get_incomplete_lots_from_history()を実装しなかった理由（判断根拠、D-115以前の
# 記述をそのまま残す。2026-10-09のD-116変更後も、本モジュールがlot_status_history
# を見なくなった経緯とは無関係に、別の既存機能（check_lot_progress()）との役割
# 分担の判断としてそのまま有効）：
#
# 「未完了」を判定する方法として、全アクティブロットを都度再計算する
# evaluate_all_lot_status()・check_lot_progress()（ui.lot_progress_window.
# LotProgressWindow「ロット進捗チェック」画面）が既に存在し、呼び出しの
# たびに現在アクティブな全ロットを現在の計画・実績・マスタから計算し直す
# 設計のため、計画はあるが実績が1件も無いロットも含めて網羅的に把握できる。
# 「未完了一覧」は新規実装せず、既存のcheck_lot_progress()をそのまま使うことが
# 適切と判断した（2026-09-30時点の判断、変更なし）。
