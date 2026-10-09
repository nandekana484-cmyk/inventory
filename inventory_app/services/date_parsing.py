"""
日付の解釈（払い出し日・生産予定日）。GUI に依存しない純粋関数。

リファクタリング段階1で ui/plan_candidate_dialog.py から移した（関数の中身は変えていない）。
旧位置からは re-export しているので、既存の呼び出し側はそのまま動く。
"""
from datetime import datetime


def _parse_plan_start_datetime(value):
    try:
        return datetime.strptime(str(value).strip(), "%Y/%m/%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


# 払い出し日（report_date）として許容する形式。上から順に試す。
# "%Y-%m-%d"：以前からの標準形式（例："2026-09-05"）。
# "%Y/%m/%d"：実運用のCSVで確認された形式（例："2026/9/5"）。strptimeの
# %m・%dはゼロ埋めの有無を問わず解釈できるため（"9"でも"09"でも可）、
# 区切り文字（"/"か"-"か）さえ合えばこの2つのフォーマット文字列で
# ゼロ埋めあり・なしの両方をカバーできる。
_REPORT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


def _parse_flexible_date(value):
    """
    払い出し日（report_date）のパースを1箇所に集約した共通関数。
    _REPORT_DATE_FORMATSを順に試し、最初に成功した結果を返す。
    どの形式でもパースできない・値が無い場合はNoneを返す。

    以前は"%Y-%m-%d"のみに対応する_parse_report_date()という名前の
    関数だったが、実運用のCSVでは"%Y/%m/%d"形式（かつ月日がゼロ埋め
    されていない、例："2026/3/18"）が使われていることが判明し
    （PRODUCTION_NG_ENHANCEMENTS_NOTES.md等の調査記録参照）、
    "%Y-%m-%d"のみでは実データに対して常にパース不能になっていた。
    そのため複数形式に対応させ、関数名も実態に合わせて改称した。

    本関数はui.kitting_production_entry._resolve_csv_report_date()からも
    importして使う（同じ日付形式の解釈をここに集約し、重複実装を避ける
    ため）。呼び出し元ごとに戻り値の扱いが異なる点に注意：
      - 本モジュール内（_sort_candidates_by_closeness()・
        _compute_date_diff_days()）は、日数差の計算にそのままdatetime
        オブジェクトとして使う。
      - _resolve_csv_report_date()は、DBのreport_date列（"YYYY-MM-DD"
        形式で統一的に保存する必要がある）へ書き込む前提のため、本関数の
        戻り値（datetime）をさらにstrftime("%Y-%m-%d")で正規化してから
        使う（入力が"2026/3/18"のような非ゼロ埋め・スラッシュ区切りで
        あっても、DBには常にゼロ埋め済みのハイフン区切りで保存され、
        文字列としての日付範囲比較（例：models.production.
        list_daily_production_range()のWHERE report_date >= ? AND <= ?）
        が正しく機能するようにするため）。
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in _REPORT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
    return None
