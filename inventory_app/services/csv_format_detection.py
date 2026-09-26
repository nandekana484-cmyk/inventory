# services/csv_format_detection.py
"""
実績CSV・キッティング計画CSVという目的の異なる2種類のCSVを取り違えて
インポートしてしまう事故（2026-09-24発生、実績CSV取込にキッティング計画CSVを
誤って読み込ませ、457件の不正データ（report_date等が空欄）が混入した実例）を
検知するための共通ヘルパー。

services.kitting_import_service（キッティング計画CSV取込）・
services.production_import_service（実績CSV取込）の両方から使う（同じ
services層同士のため、依存関係上の問題は無い）。

固有列（ヘッダーの列名）による判定のみを行う（案C：自フォーマット固有列の
欠如検知＋他フォーマット固有列の混入検知、両方）。実際の列の中身が正しいかまで
は検証しない（表記ゆれ等でどうしても誤検知・見逃しの余地は残るが、今回の実例
（列名が全く別物のCSVを読み込んだ）を確実に検知できることを優先する）。
"""
import csv

# 各取込サービスの既存のエンコーディング自動判定（utf-8-sig→utf-8→
# shift_jis→cp932）と同じ順序。ヘッダー1行だけを読むための軽量版。
_ENCODINGS_TO_TRY = ("utf-8-sig", "utf-8", "shift_jis", "cp932")

PRODUCTION_CSV_FORMAT_LABEL = "実績CSV"
PLAN_CSV_FORMAT_LABEL = "キッティング計画CSV"

# 実績CSVにのみ存在し、キッティング計画CSVには存在しない列（実データで確認済み）。
PRODUCTION_CSV_SIGNATURE_COLUMNS = ["払い出し日", "基板構成数"]

# キッティング計画CSVにのみ存在し、実績CSVには存在しない列（実データで確認済み）。
PLAN_CSV_SIGNATURE_COLUMNS = ["キッティングリストNo", "実装開始日時", "セットアップファイルNo."]


def read_csv_header(file_path):
    """
    CSVファイルの1行目（ヘッダー）だけを読み込んで返す（list[str]）。
    各取込サービスの本読み込みと同じエンコーディング自動判定を使う。
    ファイルが空の場合は空リストを返す。読み込み自体に失敗した場合
    （文字コード判定不能・ファイル未存在等）は例外をそのまま送出する
    （呼び出し元がtry/exceptで「フォーマットチェック自体をスキップし、
    本来の取込処理に判定を委ねる」といったフォールバックを選べるように
    するため、ここでは握りつぶさない）。
    """
    last_error = None
    for encoding in _ENCODINGS_TO_TRY:
        try:
            with open(file_path, mode="r", encoding=encoding, newline="") as f:
                header = next(csv.reader(f), [])
            return header
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError(f"CSVの文字コードを判定できませんでした: {last_error}")


def detect_format_mismatch_warnings(
    header, own_signature_columns, own_format_label,
    other_signature_columns, other_format_label,
):
    """
    ヘッダー行（list[str]）を検査し、フォーマット取り違えの疑いがあれば
    警告メッセージのリストを返す（問題なければ空リスト）。

    - 自フォーマットの固有列が1つも見つからない場合に警告。
    - 他フォーマットの固有列が1つ以上見つかった場合、明確に「別フォーマット
      のようだ」と警告（案C：両方の検知を独立して行う）。

    両方に該当する場合（自フォーマット固有列が無く、かつ他フォーマット固有列
    が見つかる、今回の実例そのもの）は、2件の警告メッセージが返る。
    """
    header = header or []
    warnings = []

    if not any(col in header for col in own_signature_columns):
        warnings.append(
            f"{own_format_label}に本来含まれるはずの列"
            f"（例：「{own_signature_columns[0]}」）が見つかりませんでした。"
        )

    found_other = [col for col in other_signature_columns if col in header]
    if found_other:
        warnings.append(
            f"{other_format_label}に特有の列（「{'」「'.join(found_other)}」）が見つかりました。"
            f"このファイルは{other_format_label}のようです。"
            f"{own_format_label}取込ではなく{other_format_label}取込をご利用ください。"
        )

    return warnings
