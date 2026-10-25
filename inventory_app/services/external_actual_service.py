# services/external_actual_service.py
"""
現場実績入力アプリの実績CSV（services/external_actual_csv.py が解析する）を、人が確認したうえで実績（production_daily）と
NG申告（ng_declarations）に登録する。取り込み（登録待ちにためる）、一覧用の計算、登録の3つ。自動では書き込まない。
GUI には依存しない。業務ルールは docs/domain/external_actual.md。
"""
import os

import config
from db.migrations import LATEST_VERSION
from models import external_actual_rows as rows_model
from services.csv_format_detection import (
    read_csv_header, detect_format_mismatch_warnings,
    EXTERNAL_ACTUAL_CSV_FORMAT_LABEL, EXTERNAL_ACTUAL_CSV_SIGNATURE_COLUMNS,
    PRODUCTION_CSV_FORMAT_LABEL, PRODUCTION_CSV_SIGNATURE_COLUMNS,
    PLAN_CSV_FORMAT_LABEL, PLAN_CSV_SIGNATURE_COLUMNS,
)
from services.external_actual_csv import parse_external_actual_csv


class ExternalActualRefused(ValueError):
    """取り込み・登録を受け付けないとき。メッセージはそのまま画面に出せる。"""


def current_db_folder_name() -> str:
    """今の月次DB のフォルダ名（例 2026-10）。バッチの target_db_name と、CSV の「元のDB」の比較に使う。"""
    return os.path.basename(os.path.dirname(config.DB_PATH))


def check_db_ready():
    """
    取り込み・登録を始められない理由（始められるなら None）。取り込み時と登録の直前に呼ぶ。
    既定DB（未選択）では断る。DB の版がアプリの最新と違えば断る（表が無い・形が違う DB に書かないため）。
    """
    if config.is_default_db():
        return "データベースが未選択です。月次データベースを選択してから取り込んでください"
    version = rows_model.get_db_user_version()
    if version != LATEST_VERSION:
        return f"データベースの版（{version}）がこのアプリの版（{LATEST_VERSION}）と違うため、取り込めません"
    return None


def detect_wrong_format_warnings(file_path: str) -> list:
    """現場実績CSVではなく、実績CSV・キッティング計画CSVを選んでいないか（ヘッダーの固有列で調べる）。疑いがなければ空。"""
    try:
        header = read_csv_header(file_path)
    except Exception:
        return []          # 読めないときは、取り込みの解析がエラーとして知らせる
    warnings = detect_format_mismatch_warnings(
        header, EXTERNAL_ACTUAL_CSV_SIGNATURE_COLUMNS, EXTERNAL_ACTUAL_CSV_FORMAT_LABEL,
        PRODUCTION_CSV_SIGNATURE_COLUMNS, PRODUCTION_CSV_FORMAT_LABEL,
    )
    seen = set(warnings)
    for message in detect_format_mismatch_warnings(
        header, EXTERNAL_ACTUAL_CSV_SIGNATURE_COLUMNS, EXTERNAL_ACTUAL_CSV_FORMAT_LABEL,
        PLAN_CSV_SIGNATURE_COLUMNS, PLAN_CSV_FORMAT_LABEL,
    ):
        if message not in seen:
            warnings.append(message)
    return warnings


def import_external_actual_file(file_path: str, imported_by: str) -> dict:
    """
    CSV を読んで検査し、通った行を登録待ちにためる（実績・NG にはまだ書かない）。
    同じ計画の登録待ちがあれば新しい行で置き換える。取り込み済みの書き出しより古い行は、警告して取り込まない。
    今のDB が未選択・版が違うときは ExternalActualRefused。ヘッダーの不備・書式版の不一致は、戻り値の fatal に説明を入れて何も取り込まない。
    戻り値: fatal・file_sha256・total_rows・errors・warnings（external_actual_csv.parse_external_actual_csv() と同じ）に加えて
      imported: 登録待ちにためた行数  replaced: 置き換えた計画 [(キッティングNo., ロットNo.), ...]
      stale: 古い書き出しのため取り込まなかった行  already_imported: 同じ内容のファイルを取り込み済みなら、そのバッチ（なければ None）
    """
    reason = check_db_ready()
    if reason:
        raise ExternalActualRefused(reason)

    result = parse_external_actual_csv(file_path, current_db_folder_name())
    result.update({"imported": 0, "replaced": [], "stale": [], "already_imported": None, "batch_id": None})
    if result["fatal"]:
        return result

    previous = rows_model.find_batch_by_sha256(result["file_sha256"])
    if previous:
        result["already_imported"] = previous
        result["warnings"].append(
            f"同じ内容のファイルを取り込み済みです（{previous['imported_at']}、{previous['source_file']}）")

    latest = rows_model.get_latest_exported_at_by_plan(
        (row["kitting_list_no"], row["lot_no"]) for row in result["rows"])
    accepted = []
    for row in result["rows"]:
        newest = latest.get((row["kitting_list_no"], row["lot_no"]))
        if newest is not None and newest > row["exported_at"]:
            result["stale"].append({
                "csv_row_no": row["csv_row_no"], "kitting_list_no": row["kitting_list_no"], "lot_no": row["lot_no"],
                "messages": [f"取り込み済みの書き出し（{newest}）より古い書き出し（{row['exported_at']}）のため取り込みません"],
            })
        else:
            accepted.append(row)
    if result["stale"]:
        result["warnings"].append(f"古い書き出しの行が {len(result['stale'])} 行あるため、取り込みませんでした")

    if accepted:
        batch = {
            "source_file": os.path.basename(file_path), "file_sha256": result["file_sha256"],
            "exported_at": max(row["exported_at"] for row in accepted),
            "source_db_name": accepted[0]["source_db_name"], "target_db_name": current_db_folder_name(),
        }
        saved = rows_model.import_batch(batch, accepted, imported_by)
        result.update({"imported": saved["inserted"], "replaced": saved["replaced"], "batch_id": saved["batch_id"]})
    return result
