# services/production_import_service.py
"""
実績CSV（lot_no + 製品名ベース）を取り込み、production_daily へ自動登録するサービス。

CSVフォーマットは未確定のため、services.master_import_service と同様に
COLUMN_MAP_PRODUCTION + parse_csv_generic による列名ゆらぎ吸収構造を採用する。
CSVには kitting_list_no が存在しないため、lot_no + 製品名（表記ゆらぎ許容）で
kitting_plan_items を特定し（models.kitting_plan.resolve_plan_by_lot_and_name）、
確定できた行のみ既存の services.production_service.register_daily_result() で保存する。
"""
import re
import unicodedata

from services.master_import_service import parse_csv_generic
from services.production_service import register_daily_result
from models.kitting_plan import resolve_plan_by_lot_and_name, find_matching_plan_items

# 列名マッピング辞書（拡張ポイント）：canonical key -> 候補列名リスト
COLUMN_MAP_PRODUCTION = {
    "lot_no": ["lot_no", "ロットNo", "ロット番号"],
    "product_name": ["product_name", "製品名", "基板名", "品名"],
    "daily_qty": ["daily_qty", "qty", "実績数", "生産数"],
    "report_date": ["report_date", "日付", "実績日"],
    "worker_id": ["worker_id", "担当者", "作業者"],
}


def normalize_product_name(name):
    """
    製品名の表記ゆらぎ（全角/半角、大文字/小文字、空白）を吸収する正規化関数。

    - unicodedata.normalize("NFKC", ...) で全角/半角を統一
      （全角英数・カナ→半角、一部記号の統一もNFKCの範囲でカバーされる）
    - 小文字化（大文字/小文字ゆらぎの吸収）
    - 前後の空白除去、中間の連続空白を1つに圧縮

    NFKCでカバーされない記号ゆらぎ（例：長音記号の統一など）まで踏み込んだ
    正規化は仕様未確定のため決め打ちしない。
    """
    if name is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(name))
    normalized = normalized.lower().strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def import_production_csv(file_path, default_worker_id=None):
    """
    実績CSVを解析し、lot_no + 製品名で計画（kitting_plan_items）を特定して
    production_daily へ登録する（拡張ポイント：CSVフォーマット確定後も
    COLUMN_MAP_PRODUCTION の調整のみで対応できる構造）。

    必須列：lot_no, product_name, daily_qty（欠けている・空の行は警告してスキップ）
    任意列：
      - report_date（省略時は register_daily_result() のデフォルト＝当日）
      - worker_id（省略時は default_worker_id を使用。呼び出し側でログイン作業者等を渡す想定）

    lot_no + 製品名で計画を一意に特定できなかった行は保存せず、unmatched に集める。
    register_daily_result() の呼び出し方法・仕様は変更しない。

    戻り値：{
        "imported": [{"lot_no", "product_name", "kitting_list_no", "daily_qty",
                       "worker_id", "report_date", "app_cumulative_qty"}, ...],
        "unmatched": [{"lot_no", "product_name", "daily_qty", "reason"}, ...],
        "warnings": [CSV解析時点の警告メッセージ（必須列欠落・数値変換エラー等）],
    }
    """
    rows = parse_csv_generic(file_path, COLUMN_MAP_PRODUCTION)

    imported = []
    unmatched = []
    warnings = []

    for i, row in enumerate(rows, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
        lot_no = row.get("lot_no")
        product_name = row.get("product_name")
        daily_qty_raw = row.get("daily_qty")

        if not lot_no or not product_name:
            warnings.append(f"{i}行目: lot_no または product_name が空のためスキップしました。")
            continue

        if daily_qty_raw in (None, ""):
            warnings.append(f"{i}行目: daily_qty が空のためスキップしました。")
            continue

        try:
            daily_qty = float(daily_qty_raw)
        except (TypeError, ValueError):
            warnings.append(f"{i}行目: daily_qty「{daily_qty_raw}」を数値に変換できないためスキップしました。")
            continue

        lot_no = str(lot_no).strip()
        report_date = row.get("report_date") or None
        worker_id = row.get("worker_id") or default_worker_id or "CSV_IMPORT"

        product_name_normalized = normalize_product_name(product_name)
        kitting_list_no = resolve_plan_by_lot_and_name(lot_no, product_name_normalized)

        if not kitting_list_no:
            candidates, matched = find_matching_plan_items(lot_no, product_name_normalized)
            if not candidates:
                reason = "計画が見つからない（該当lot_noの計画なし）"
            elif not matched:
                reason = "製品名ゆらぎ（一致する基板名が見つからない）"
            else:
                reason = "複数候補あり（lot_no+製品名で一意に特定できない）"

            unmatched.append({
                "lot_no": lot_no,
                "product_name": product_name,
                "daily_qty": daily_qty,
                "reason": reason,
            })
            continue

        new_cumulative = register_daily_result(kitting_list_no, daily_qty, worker_id, report_date)

        imported.append({
            "lot_no": lot_no,
            "product_name": product_name,
            "kitting_list_no": kitting_list_no,
            "daily_qty": daily_qty,
            "worker_id": worker_id,
            "report_date": report_date,
            "app_cumulative_qty": new_cumulative,
        })

    return {"imported": imported, "unmatched": unmatched, "warnings": warnings}
