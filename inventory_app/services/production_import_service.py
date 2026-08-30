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
from services.production_service import (
    register_daily_result,
    overwrite_daily_result,
    register_opposite_side_daily_result,
    DailyResultAlreadyExists,
)
from models.kitting_plan import (
    resolve_plan_by_lot_and_name,
    find_matching_plan_items,
    find_plan_item_by_kitting_no,
)

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
    register_daily_result() へは、この行の計画特定に使ったlot_noをそのまま渡す
    （kitting_list_no単体では計画を一意に特定できないケースが実DBに存在するため、
    register_daily_result()はkitting_list_no・lot_noの両方を必須で受け取る仕様）。

    上書き（「1計画=1レコード、常に上書き」ルール）：
    register_daily_result(check_duplicate=True) を呼び、その計画（kitting_list_no・
    lot_no）に既存レコードがあれば（report_dateを問わず）DailyResultAlreadyExists
    が送出されるので、overwrite_daily_result() で上書きする。CSVに同一計画の行が
    複数回（異なるreport_dateを含む）出現した場合も、後の行が前の行を上書きする。

    面1/面2連動：主行の登録（新規追加／上書きいずれも）に成功した後、
    services.production_service.register_opposite_side_daily_result() を使って
    反対側の面（存在する場合）にも同じdaily_qtyを連動登録する
    （ui.kitting_production_entry.KittingProductionEntryWindow の手動入力と
    共通のロジック）。反対側への登録が失敗しても、主行自体はimportedに含めたまま
    とし（主行の処理結果を巻き込まない）、反対側のエラーのみ別途errorsに記録する。

    register_daily_result()/overwrite_daily_result() が例外を送出した行（計画は
    特定できたが登録に失敗した行。例：処理中に計画が削除された等）は、その行だけを
    エラーとして errors に記録し、残りの行の処理は継続する（1行の異常で全行の結果が
    失われないようにするため）。

    戻り値：{
        "imported": [{"lot_no", "product_name", "kitting_list_no", "daily_qty",
                       "worker_id", "report_date", "app_cumulative_qty"}, ...],
        "unmatched": [{"lot_no", "product_name", "report_date", "worker_id",
                        "daily_qty", "reason"}, ...],
        "warnings": [CSV解析時点の警告メッセージ（必須列欠落・数値変換エラー等）],
        "errors": [{"row", "lot_no", "product_name", "kitting_list_no", "daily_qty",
                     "worker_id", "report_date", "error"}, ...]
                    （register_daily_result()/overwrite_daily_result() 呼び出し時、
                     または反対側の面への連動登録時に例外が発生した行。
                     既存呼び出し元との後方互換のため追加したキー。
                     未対応の呼び出し元は単に参照しないだけで動作に影響しない）,
    }
    """
    rows = parse_csv_generic(file_path, COLUMN_MAP_PRODUCTION)

    imported = []
    unmatched = []
    warnings = []
    errors = []

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
                "report_date": report_date,
                "worker_id": worker_id,
                "daily_qty": daily_qty,
                "reason": reason,
            })
            continue

        try:
            # lot_noは、この行の計画特定に使ったのと同じ値をそのまま渡す（実DBで
            # 同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースが
            # 478件確認されているため、register_daily_result()はkitting_list_noに
            # 加えてlot_noも必須で受け取る仕様になった）。
            # check_duplicate=Trueにより、その計画（kitting_list_no・lot_no）に
            # 既存レコードがあれば（report_dateを問わず）DailyResultAlreadyExists
            # が送出されるので、overwrite_daily_result()で上書きする
            # （「1計画=1レコード、常に上書き」ルール。except DailyResultAlready
            # Existsは、Exceptionのサブクラスのためexcept Exceptionより前に置く
            # 必要がある）。
            new_cumulative = register_daily_result(
                kitting_list_no, lot_no, daily_qty, worker_id, report_date, check_duplicate=True,
            )
        except DailyResultAlreadyExists:
            try:
                new_cumulative = overwrite_daily_result(kitting_list_no, lot_no, daily_qty, worker_id, report_date)
            except Exception as e2:
                errors.append({
                    "row": i,
                    "lot_no": lot_no,
                    "product_name": product_name,
                    "kitting_list_no": kitting_list_no,
                    "daily_qty": daily_qty,
                    "worker_id": worker_id,
                    "report_date": report_date,
                    "error": str(e2),
                })
                continue
        except Exception as e:
            errors.append({
                "row": i,
                "lot_no": lot_no,
                "product_name": product_name,
                "kitting_list_no": kitting_list_no,
                "daily_qty": daily_qty,
                "worker_id": worker_id,
                "report_date": report_date,
                "error": str(e),
            })
            continue

        imported.append({
            "lot_no": lot_no,
            "product_name": product_name,
            "kitting_list_no": kitting_list_no,
            "daily_qty": daily_qty,
            "worker_id": worker_id,
            "report_date": report_date,
            "app_cumulative_qty": new_cumulative,
        })

        # 主行の登録（新規／上書きいずれも）に成功した後、反対側の面（存在する場合）
        # にも同じdaily_qtyを連動登録する。失敗しても主行は既にimportedへ追加済み
        # のため、主行の処理結果には影響させず、反対側のエラーのみ別途記録する
        # （元の行の処理を止めない）。
        plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
        if plan is not None:
            try:
                register_opposite_side_daily_result(plan, daily_qty, worker_id, report_date=report_date)
            except Exception as e3:
                errors.append({
                    "row": i,
                    "lot_no": lot_no,
                    "product_name": product_name,
                    "kitting_list_no": kitting_list_no,
                    "daily_qty": daily_qty,
                    "worker_id": worker_id,
                    "report_date": report_date,
                    "error": f"反対側の面への連動登録に失敗しました：{e3}",
                })

    return {"imported": imported, "unmatched": unmatched, "warnings": warnings, "errors": errors}
