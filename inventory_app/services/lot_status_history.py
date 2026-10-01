# services/lot_status_history.py
"""
models.lot_status_history（実績の登録・修正のたびに記録されるロット状態の
履歴）を使った集計機能。

このモジュールが答えられること・答えられないことの区別が重要：
- lot_status_historyは「実績の登録・修正・引き継ぎが実際に行われた瞬間」の
  スナップショットの列であり、ある過去の日付時点で「そのロットがどういう
  状態だったか」を後から正確に再構成できる（recorded_atは実際に書き込まれた
  壁時計時刻であり、production_daily.report_date（ユーザーが指定する任意の
  日付、後から過去日付として登録し直すこともできる）とは異なり、後から
  遡って書き換わることが無い。過去に調査した「report_dateベースの累計は
  後追い登録で事後的に変わりうる」という問題（過去の会話参照）が、この
  recorded_atベースの集計には当てはまらない）。
- 一方、一度も実績の登録・修正が行われていないロット（計画はあるが
  production_dailyが1件も無いロット）は、lot_status_historyに1行も
  記録されないため、このモジュールの集計からは常に漏れる。「現在アクティブな
  全ロットの状態」を網羅的に把握したい場合は、
  services.production_service.check_lot_progress()（毎回全ロットを再計算する
  方式）を使うこと。詳細な判断根拠はget_incomplete_lots_from_history()を
  実装しなかった理由として、本モジュールのdocstring末尾にまとめる。
"""
import json
from datetime import datetime, timedelta

from models.lot_status_history import list_latest_snapshot_per_lot


def _cutoff_for_date(date_str: str) -> str:
    """指定日の23:59:59を、recorded_atとの文字列比較にそのまま使える形式で返す。"""
    return f"{date_str} 23:59:59"


def _previous_date(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


def get_daily_drawdown(target_date: str) -> list:
    """
    指定日（target_date、"YYYY-MM-DD"）に増えた引落を、ロット・ファイルNo単位で
    算出する。

    考え方：
      1. 「指定日以前で最新の記録」（recorded_at <= target_date 23:59:59の
         うち最新）と、「指定日の前日以前で最新の記録」（recorded_at <=
         前日23:59:59のうち最新）を、lot_noごとに取得する
         （models.lot_status_history.list_latest_snapshot_per_lot()）。
      2. 両者のfiles_detail（(setup_file_no, production_side)単位の
         lot_completed）を突き合わせ、差分を計算する。
      3. 差分が0のファイルNoは結果から除外する（「その日に引落が増えた」行の
         みを返す）。

    「指定日以前で最新の記録が無いlot_no」（そのロットについて、指定日までに
    一度も実績の登録・修正が行われていない）は、そもそも比較対象が無いため
    結果に含めない。

    「前日以前で最新の記録が無いlot_no」（指定日にそのロットが初めて登場した
    ケース、例：初回の実績登録がまさに指定日に行われた）は、前日側の
    lot_completedを全ファイルNoについて0として扱う（＝初回登録分は全て
    「その日の引落」としてそのまま計上される）。

    ファイルNoの対応付けは(setup_file_no, production_side)をキーとする
    （board_nameではなく、こちらがファイル自体の識別子のため）。「未確定」
    仮想エントリ（setup_file_no="未確定", production_side=None）は
    lot_completedが常に0のため、差分も常に0になり自動的に除外される
    （特別扱いは不要）。

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

    latest_today = list_latest_snapshot_per_lot(cutoff_today)
    latest_prev = list_latest_snapshot_per_lot(cutoff_prev)

    rows = []
    for lot_no, today_row in latest_today.items():
        prev_row = latest_prev.get(lot_no)
        today_files = json.loads(today_row["files_detail"])
        prev_files = json.loads(prev_row["files_detail"]) if prev_row else []

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


# get_incomplete_lots_from_history()を実装しなかった理由（判断根拠）：
#
# lot_status_historyの最新の記録から「未完了」を判定する方法には、
# 致命的な欠落がある。lot_status_historyは実績の登録・修正・引き継ぎが
# 実際に行われた時にのみ1行記録される「イベントログ」であり、一度も実績が
# 登録されていないロット（計画はあるがまだ生産に着手していない、最も
# 「未完了」らしいロット）は、この履歴に1行も存在しない。
# 「最新の記録でtotal_surplus_qty>0またはtotal_not_produced_qty>0」という
# 条件だけを見る集計では、記録が無いロットはそもそも集計対象に含まれず、
# 静かに一覧から漏れてしまう。これは§16・D-24で修正した「対象期間に実績が
# 無いロットは検知されない」という欠陥（構成基板数チェックの起点の欠陥1）と
# 本質的に同じ種類の見落としであり、繰り返すべきではないと判断した。
#
# また、lot_status_historyの最新の記録は「その時点で計算された結果」を
# 保存したものに過ぎず、その後に構成基板数マスタが訂正された、計画側の
# 内容（board_name・plan_start_datetime等）が変更された等、実績の登録・修正
# 以外の要因で状態が変わった場合には追随しない（最後に実績が動いた時点の
# 状態のまま古くなり得る）。
#
# 一方、既存のcheck_lot_progress()（ui.lot_progress_window.LotProgressWindow
# 「ロット進捗チェック」画面）は、呼び出しのたびに現在アクティブな全ロットを
# 現在の計画・実績・マスタから計算し直す設計のため、上記いずれの問題も
# 生じない。693ロットの処理時間は約1.5秒（過去の計測）で、手動でボタンを
# 押して開く画面の用途としては許容範囲。
#
# 結論：「未完了一覧」は新規実装せず、既存のcheck_lot_progress()
# （ui.lot_progress_window.LotProgressWindow）をそのまま使うことが適切と
# 判断した。lot_status_historyは「ある過去の時点の状態を振り返る」
# （get_daily_drawdown()のような）用途に向いており、「現在の状態を漏れなく
# 把握する」用途にはcheck_lot_progress()の方が適している、という役割分担で
# 整理する。
