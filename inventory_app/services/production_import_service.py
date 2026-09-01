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
#
# 実データで確認された列構成（払い出し日・機種基板名・ロットNo・数量・累計・
# 発注数・基板構成数）のうち、"累計"・"発注数"・"基板構成数" はここに
# canonical keyを追加していない。これは対応漏れではなく、意図的に無視している：
#   - "累計"：DB側で get_app_cumulative_qty() が production_daily の実績を
#     都度合計して算出する値であり、CSVの値は使わない（照合・上書きもしない）。
#   - "発注数"：kitting_plan_items.order_qty（別途キッティング計画CSVから
#     取り込み済みの値）をそのまま使い続ける。このCSVの値では上書きしない。
#   - "基板構成数"：現時点では既存の概念（丁取り数・部品構成数等）との
#     対応関係が業務側で未確認のため、参考情報として_extraに残すのみで
#     取り込み処理では使用しない。
# これらの列はマッピング対象外のため parse_csv_generic() の "_extra" に
# そのまま格納される（取り込み処理では未使用）。
COLUMN_MAP_PRODUCTION = {
    "lot_no": ["lot_no", "ロットNo", "ロット番号"],
    "product_name": ["product_name", "製品名", "基板名", "品名", "機種基板名"],
    "daily_qty": ["daily_qty", "qty", "実績数", "生産数", "数量"],
    "report_date": ["report_date", "日付", "実績日", "払い出し日"],
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
    required_column_skipped_count = 0

    for i, row in enumerate(rows, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
        lot_no = row.get("lot_no")
        product_name = row.get("product_name")
        daily_qty_raw = row.get("daily_qty")

        if not lot_no or not product_name:
            warnings.append(f"{i}行目: lot_no または product_name が空のためスキップしました。")
            required_column_skipped_count += 1
            continue

        if daily_qty_raw in (None, ""):
            warnings.append(f"{i}行目: daily_qty が空のためスキップしました。")
            required_column_skipped_count += 1
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

    # 区切り文字・列名の不一致でヘッダーが正しく認識されないと、全行が
    # 「lot_noまたはproduct_nameが空」「daily_qtyが空」として一律にスキップ
    # されてしまう（実際に発生した事例：ui.parts_attributes_import_window.py
    # と同種のパターン）。これに気づきやすくするため、必須列の空欄による
    # スキップが読み込み行数の9割以上を占める場合は、通常の行単位警告とは別に、
    # 列名の確認を促す注意喚起メッセージを warnings の先頭に追加する。
    total_rows = len(rows)
    if total_rows > 0 and required_column_skipped_count / total_rows >= 0.9:
        warnings.insert(
            0,
            f"※ 読み込んだ{total_rows}行中{required_column_skipped_count}行"
            f"（{required_column_skipped_count / total_rows * 100:.0f}%）が"
            "lot_no・product_name・daily_qtyのいずれかの空欄でスキップされました。"
            "CSVの列名がCOLUMN_MAP_PRODUCTIONの候補列名と一致していない可能性が"
            "あります。列名をご確認ください。",
        )

    return {"imported": imported, "unmatched": unmatched, "warnings": warnings, "errors": errors}


# ステージング一覧（services.production_import_service.parse_production_csv_for_staging()の
# 戻り値の"status"）の表示ラベル。UI側（ui/production_import_staging_window.py）で使う。
STAGING_STATUS_LABELS = {
    "no_candidates": "候補なし",
    "needs_selection": "候補あり（要選択）",
    "auto_resolvable": "自動確定可能（要確認）",
}


def parse_production_csv_for_staging(file_path, default_worker_id=None):
    """
    実績CSVを解析するが、DBへは一切書き込まない（「確認・選択・転記」方式の
    実績取込一覧向け）。import_production_csv()（即時登録版）とは別の
    エントリーポイントとして新設した。import_production_csv()自体は後方互換の
    ため変更していない。

    必須列の検証（lot_no・product_name・daily_qtyの空欄チェック、daily_qtyの
    数値変換）・9割スキップ時の注意喚起は import_production_csv() と同じ
    ロジックを踏襲する。

    各行について、models.kitting_plan.find_matching_plan_items(lot_no,
    正規化済み製品名) を呼び、そのlot_noに属する現在アクティブな計画
    （candidates）と、製品名も一致するもの（matched）を取得した上で、
    以下の3状態のいずれかを"status"として付与する：
      - "no_candidates"：candidatesが0件（該当lot_noの計画が無い）
      - "needs_selection"：candidatesは1件以上あるが、matchedの
        kitting_list_noが0種類または複数種類で自動確定できない
      - "auto_resolvable"：matchedのkitting_list_noがちょうど1種類
        （import_production_csv()ならそのまま自動登録される状態）

    "auto_resolvable"であっても、実際にその計画を確定させるかどうかの判断は
    呼び出し元のUI（必ず候補選択ダイアログを経由させる方針）に委ねる。
    本関数はcandidatesが1件のみの場合でも自動的に確定させたりはしない。

    戻り値：{
        "rows": [{"row", "lot_no", "product_name", "daily_qty", "report_date",
                   "worker_id", "candidates", "matched", "status"}, ...],
        "warnings": [CSV解析時点の警告メッセージ（必須列欠落・数値変換エラー等）],
    }
    "report_date"はCSVの「払い出し日」相当の値をそのまま保持するが、
    参考情報としての表示用であり、実際の登録時（呼び出し元がこのモジュールの
    外で行う）にはこの値を使わない方針（登録ボタンを押した日を使うため）。
    """
    rows = parse_csv_generic(file_path, COLUMN_MAP_PRODUCTION)

    staged_rows = []
    warnings = []
    required_column_skipped_count = 0

    for i, row in enumerate(rows, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
        lot_no = row.get("lot_no")
        product_name = row.get("product_name")
        daily_qty_raw = row.get("daily_qty")

        if not lot_no or not product_name:
            warnings.append(f"{i}行目: lot_no または product_name が空のためスキップしました。")
            required_column_skipped_count += 1
            continue

        if daily_qty_raw in (None, ""):
            warnings.append(f"{i}行目: daily_qty が空のためスキップしました。")
            required_column_skipped_count += 1
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
        candidates, matched = find_matching_plan_items(lot_no, product_name_normalized)

        if not candidates:
            status = "no_candidates"
        else:
            unique_kitting_nos = {c["kitting_list_no"] for c in matched}
            status = "auto_resolvable" if len(unique_kitting_nos) == 1 else "needs_selection"

        staged_rows.append({
            "row": i,
            "lot_no": lot_no,
            "product_name": product_name,
            "daily_qty": daily_qty,
            "report_date": report_date,
            "worker_id": worker_id,
            "candidates": candidates,
            "matched": matched,
            "status": status,
        })

    total_rows = len(rows)
    if total_rows > 0 and required_column_skipped_count / total_rows >= 0.9:
        warnings.insert(
            0,
            f"※ 読み込んだ{total_rows}行中{required_column_skipped_count}行"
            f"（{required_column_skipped_count / total_rows * 100:.0f}%）が"
            "lot_no・product_name・daily_qtyのいずれかの空欄でスキップされました。"
            "CSVの列名がCOLUMN_MAP_PRODUCTIONの候補列名と一致していない可能性が"
            "あります。列名をご確認ください。",
        )

    return {"rows": staged_rows, "warnings": warnings}
