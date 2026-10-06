# services/production_import_service.py
"""
実績CSV（lot_no + 製品名ベース）を取り込み、production_daily へ自動登録するサービス。

CSVフォーマットは未確定のため、services.csv_parsing_common（元は
services.master_import_service内にあったが、マスタインポート画面削除時に
共有ユーティリティとして切り出された）と同様に、COLUMN_MAP_PRODUCTION +
parse_csv_generic による列名ゆらぎ吸収構造を採用する。
CSVには kitting_list_no が存在しないため、lot_no + 製品名（表記ゆらぎ許容）で
kitting_plan_items を特定し（models.kitting_plan.resolve_plan_by_lot_and_name）、
確定できた行のみ既存の services.production_service.register_daily_result() で保存する。
"""
import re
import unicodedata
from datetime import datetime

from services.csv_parsing_common import parse_csv_generic
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
    list_active_plan_items,
)
from models.production import list_daily_production_by_kitting_no
from models.production_import_staging import (
    create_csv_import_batch,
    upsert_pending_csv_import_row,
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


# report_dateの表記ゆらぎ吸収用（is_already_registered()専用、2026-10-06追加）。
# CSV側は"2026/7/14"のようなスラッシュ区切り・ゼロ埋め無し、production_daily側は
# "2026-07-14"のようなハイフン区切り・ゼロ埋め済みで保存されており、書式が
# 異なるため、比較前に同じ形式へ正規化する必要がある。ui.plan_candidate_dialog.
# _parse_flexible_date()と同じ対応形式だが、services層からui層を参照すると
# 依存方向が逆転するため、本モジュール専用に同等のロジックを複製している。
_REPORT_DATE_FORMATS_FOR_MATCH = ("%Y-%m-%d", "%Y/%m/%d")


def _normalize_report_date_for_match(value):
    """report_dateを"%Y-%m-%d"形式へ正規化する。パース不能・値が無い場合はNone。"""
    if not value:
        return None
    text = str(value).strip()
    for fmt in _REPORT_DATE_FORMATS_FOR_MATCH:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
    return None


def group_active_plan_items_by_lot(include_completed=False):
    """
    list_active_plan_items()を1回だけ呼び、lot_noをキーにグルーピングした辞書を
    返す（{lot_no: [item, ...], ...}）。

    include_completed：list_active_plan_items()にそのまま渡す（省略時は
    False＝完了済み除外、通常の候補表示・自動確定判定用）。is_already_
    registered()は、Trueで取得したグルーピング結果を別途渡すこと（完了済み
    計画こそ「既に登録済み」判定の対象になるため。詳細はis_already_
    registered()のdocstring参照）。

    import_production_csv()は、CSVの行数分models.kitting_plan.
    find_matching_plan_items()を呼ぶが、以前はその内部で毎回
    list_active_plan_items()（現在アクティブな全計画のフルスキャン＋累計実績の
    一括集計）が実行されており、CSV行数に比例して同じ全件取得が繰り返される
    N+1状態だった（実測：2000行で約61秒）。services.production_service.
    list_incomplete_lots()で採用済みの「1回だけ全件取得→Python側でlot_noごとに
    グルーピング」と同じアプローチをこちらにも適用し、ループに入る前に本関数で
    1回だけ計画一覧を取得する。

    モジュール外にも公開している理由：ui.production_import_staging_window
    （実績CSVステージング一覧）が、表示・再開のたびに保留行の候補を
    find_matching_plan_items()で再照合する際、同じくN+1を避けるために
    この関数を再利用するため（ステージングデータの永続化に伴い、候補自体は
    DBに保存せず毎回再計算する方針とした。models.production_import_staging
    参照）。

    グルーピングキーはmodels.kitting_plan.find_matching_plan_items()の従来の
    絞り込み条件（str(item.get("lot_no") or "").strip() == lot_no）と同じ正規化を
    行う。呼び出し元がfind_matching_plan_items()へ渡すlot_noも同様に
    str(...).strip()済みのため、キーの表記は一致する。
    """
    grouped = {}
    for item in list_active_plan_items(include_completed=include_completed):
        key = str(item.get("lot_no") or "").strip()
        grouped.setdefault(key, []).append(item)
    return grouped


def is_already_registered(lot_no, product_name, daily_qty, plan_items_by_lot=None, report_date=None):
    """
    このCSV行（lot_no・product_name・daily_qty・report_date）が指す計画に
    ついて、production_dailyへ既に同じ数量かつ同じ日付で登録済みかどうかを
    判定する。
    実績CSVステージング一覧（parse_production_csv_for_staging()）で、
    重複登録の必要が無い行を一覧から除外し、代わりに「登録済みリスト」
    （ui.production_import_staging_window.ProductionImportStagingWindow.
    self._already_registered_rows）へ振り分けるために使う。

    対象計画の特定：find_matching_plan_items(lot_no, 正規化済み
    product_name, plan_items_by_lot)を呼び、製品名まで一致した計画
    （matched）を取得する。0件（該当計画なし）の場合は「判定不可」として
    `already_registered=False`を返す（＝一覧からは除外しない）。

    複数件（lot_no+製品名だけでは一意特定できない、あいまい）の場合
    （2026-10-06改訂）：候補のうち1件でもCSVのdaily_qtyと完全一致する実績が
    既に登録されていれば「登録済み」と判定する。これは、候補が複数ある行を
    人が候補を選んで確定した後に同じCSVを再取込した際、確定済みの計画の
    実績と照合できず保留一覧へ同じ内容の行が復活してしまう不具合
    （CANONICAL_DESIGN_DECISIONS.md D-8x参照）への対応。一致する候補が無い
    場合は「判定不可」として`already_registered=False`を返す（誤って有効な
    行を隠してしまうことを避けるため、判定できない場合は常に「未登録」側に
    倒す、という方針自体は変えていない）。

    判定：matchedの1件について、models.production.
    list_daily_production_by_kitting_no(kitting_list_no, lot_no)（既存の実績
    取得関数）で、その計画に対応するproduction_daily行を取得する。

    **行が1件も無い場合は、CSVのdaily_qtyの値に関わらず「登録済み」とは
    判定しない**（2026-10-06修正、CANONICAL_DESIGN_DECISIONS.md D-7x参照）。
    以前はmodels.production.get_app_cumulative_qty()（COALESCE(SUM(...), 0)）
    を使っていたため、「実績が1件も無い（合計0）」場合と「実績はあるが
    合計がたまたま0」の場合を区別できず、CSVのdaily_qtyが0の行は常に
    「登録済み」と誤判定されていた（本DBに実績が1件も無い新規作成直後の
    月次DBで、daily_qty=0の行が全て誤ってスキップされる不具合として発覚）。

    行が1件以上ある場合、その合計（SUM(daily_qty)）とCSVのdaily_qtyが完全
    一致し、**かつ**、そのうちの少なくとも1行のreport_dateがCSVのreport_date
    と一致する場合のみ「登録済み」（True）と判定する（2026-10-06再改訂、
    CANONICAL_DESIGN_DECISIONS.md D-8x参照）。日付の比較は
    _normalize_report_date_for_match()で"%Y-%m-%d"形式に正規化した上で行う
    （CSV側は"2026/7/14"のようなスラッシュ区切り・ゼロ埋め無し、
    production_daily側は"2026-07-14"のようなハイフン区切り・ゼロ埋め済みで
    保存されており、書式が異なるため正規化しないと一致しない）。

    日付も見る理由：以前は数量のみで判定していたため、同じロットNo・製品名で
    日付が異なる複数の実績（別々の計画の実績、本モジュールのdocstring
    「業務ルール」参照）が同じ数量だった場合（実務上よくある、例：同じ
    ロットを複数日に分けて同数量ずつ生産するケース）、1件を確定しただけで
    残りの未確定行まで全て「登録済み」と誤判定し、黙って一覧から消してしまう
    不具合が見つかった（実際には1件も登録されていないのに消えるため、D-78の
    「1行は1件のまま残す」という方針に反する）。

    数量は一致するが日付が一致しない場合（訂正が必要なケース、または単に
    別日の実績が別途ある場合）はFalse（一覧に表示し、通常通り確認・上書きの
    対象とする）。report_dateが省略された場合（呼び出し元が明示的に渡さない
    場合）は、日付での絞り込みを行わず数量のみで判定する従来動作のまま
    （後方互換のためのデフォルト。実際の呼び出し元
    parse_production_csv_for_staging()は必ずreport_dateを渡す）。

    plan_items_by_lot：group_active_plan_items_by_lot(include_completed=True)の
    戻り値を渡すこと（省略時はlist_active_plan_items(include_completed=True)を
    都度呼ぶ）。CSV行数分呼ばれる想定のため、呼び出し元は事前に1回だけ取得した
    ものを渡し、N+1を避けること。

    include_completed=Trueが必須な理由（重要）：models.kitting_plan.
    list_active_plan_items()のデフォルト（include_completed=False）は、
    実績が発注数に到達済み＝完了扱いの計画を除外する。本関数が検出したい
    「既に登録済み（数量一致）」の計画は、まさにこの「完了済み」に該当する
    ことが多く（実データで確認済み：lot_no=256939、全file_no生産済みの
    ケースでは、デフォルトのままだとcandidatesが0件になり判定不能になって
    しまっていた）、デフォルトのまま呼ぶと本関数の主目的（完了済みCSVの
    再取込を検出する）を果たせない。find_matching_plan_items()の通常の
    呼び出し（候補選択・自動確定判定用）がinclude_completed=Falseを
    要求する理由（models.kitting_plan.list_active_plan_items()のdocstring
    参照）とは目的が異なるため、本関数専用に別のグルーピング結果を使う。

    戻り値：{"already_registered": bool, "matched_kitting_list_no": str|None,
    "existing_qty": float|None, "daily_qty": daily_qty}のdict。
    以前はbool一つだけを返していたが、「登録済みリスト」で判定根拠
    （どの計画のどの実績値と一致したためスキップされたのか）を表示できる
    ようにするため、判定に使った詳細情報も返すよう拡張した。呼び出し元は
    引き続き`result["already_registered"]`だけを見ればbool時代と同じ判定に
    使える。matched_kitting_list_noは判定不可（matchedが1件に定まらない）の
    場合にNoneになる。**existing_qtyは、判定不可の場合に加えて、計画は
    1件に定まったが対応するproduction_daily行が1件も無い場合もNoneになる**
    （2026-10-06改訂。「実績が無い」ことと「実績はあるが合計0」を呼び出し元が
    区別できるようにするため。後者は実績が存在するためNoneではなく0.0が入る）。
    """
    if plan_items_by_lot is None:
        plan_items_by_lot = group_active_plan_items_by_lot(include_completed=True)

    product_name_normalized = normalize_product_name(product_name)
    _, matched = find_matching_plan_items(lot_no, product_name_normalized, plan_items_by_lot)

    normalized_csv_date = _normalize_report_date_for_match(report_date)

    def _qty_and_date_match(existing_rows):
        """existing_rowsの合計がdaily_qtyと一致し、かつ（report_dateが
        指定されている場合）そのうち少なくとも1行の日付がCSVの日付と一致
        するかどうかを判定する（本関数のdocstring参照）。existing_qty
        （合計、表示用）とのタプルを返す。"""
        existing_qty = sum(row["daily_qty"] for row in existing_rows)
        if existing_qty != daily_qty:
            return False, existing_qty
        if normalized_csv_date is None:
            return True, existing_qty
        date_match = any(
            _normalize_report_date_for_match(row["report_date"]) == normalized_csv_date
            for row in existing_rows
        )
        return date_match, existing_qty

    if len(matched) > 1:
        # 候補が複数ある（曖昧）行でも、そのうちどれか1件に既にCSVのdaily_qty・
        # report_dateと完全一致する実績が登録されていれば「登録済み」と判定する
        # （2026-10-06追加）。背景：候補が複数あるため人が候補を選んで確定した後、
        # 同じCSVを再取込すると、is_already_registered()がmatched!=1を理由に
        # 常に「判定不可」を返し、確定済みの計画の実績と照合できず、再取込の
        # たびに保留一覧へ同じ内容の行が復活してしまう不具合が見つかった
        # （別の候補を選んで確定すると二重登録になるリスクがあった）。
        # 該当する候補が無ければ従来通り「判定不可」のまま返す
        # （誤って有効な行を隠さないため）。
        for plan in matched:
            existing_rows = list_daily_production_by_kitting_no(plan["kitting_list_no"], lot_no)
            if not existing_rows:
                continue
            matched_ok, existing_qty = _qty_and_date_match(existing_rows)
            if matched_ok:
                return {
                    "already_registered": True,
                    "matched_kitting_list_no": plan["kitting_list_no"],
                    "existing_qty": existing_qty,
                    "daily_qty": daily_qty,
                }
        return {
            "already_registered": False,
            "matched_kitting_list_no": None,
            "existing_qty": None,
            "daily_qty": daily_qty,
        }

    if len(matched) == 0:
        return {
            "already_registered": False,
            "matched_kitting_list_no": None,
            "existing_qty": None,
            "daily_qty": daily_qty,
        }

    plan = matched[0]
    existing_rows = list_daily_production_by_kitting_no(plan["kitting_list_no"], lot_no)
    if not existing_rows:
        return {
            "already_registered": False,
            "matched_kitting_list_no": plan["kitting_list_no"],
            "existing_qty": None,
            "daily_qty": daily_qty,
        }

    matched_ok, existing_qty = _qty_and_date_match(existing_rows)
    return {
        "already_registered": matched_ok,
        "matched_kitting_list_no": plan["kitting_list_no"],
        "existing_qty": existing_qty,
        "daily_qty": daily_qty,
    }


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

    # list_active_plan_items()（現在アクティブな全計画のフルスキャン＋累計実績の
    # 一括集計）をCSVの行数分繰り返し呼ぶN+1を避けるため、ループに入る前に1回だけ
    # 呼び、lot_noごとにグルーピングしておく（group_active_plan_items_by_lot()参照）。
    plan_items_by_lot = group_active_plan_items_by_lot()

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
        kitting_list_no = resolve_plan_by_lot_and_name(lot_no, product_name_normalized, plan_items_by_lot)

        if not kitting_list_no:
            candidates, matched = find_matching_plan_items(lot_no, product_name_normalized, plan_items_by_lot)
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
    "needs_confirmation_existing": "要確認（既に実績あり）",
    "needs_confirmation_duplicate": "要確認（同一計画への重複候補）",
}

# 対象外の理由（2026-10-06新設）。ui.production_import_staging_window.
# ProductionImportStagingWindowの「対象外一覧」（旧「登録済みリスト」を
# 理由列付きに拡張したもの）で使う。
EXCLUSION_REASON_ALREADY_REGISTERED = "登録済み"
EXCLUSION_REASON_ZERO_QTY = "数量0のため対象外"
EXCLUSION_REASON_LABELS = {
    EXCLUSION_REASON_ALREADY_REGISTERED: "登録済み（登録済み数量とCSVの数量が一致）",
    EXCLUSION_REASON_ZERO_QTY: "数量0のため対象外（実績が無く、CSVの数量も0）",
}


def parse_production_csv_for_staging(file_path, default_worker_id=None):
    """
    実績CSVを解析し、production_daily へは書き込まず、
    models.production_import_staging.pending_csv_import_rows へ永続化する
    （「確認・選択・転記」方式の実績取込一覧向け）。import_production_csv()
    （即時登録版）とは別のエントリーポイントとして新設した。
    import_production_csv()自体は後方互換のため変更していない（本関数と
    同種の「実績0件の誤判定」は無い。import_production_csv()はis_already_
    registered()を使わず、register_daily_result(check_duplicate=True)の
    例外ハンドリングで無条件に登録・上書きする設計のため。2026-10-06確認）。

    必須列の検証（lot_no・product_name・daily_qtyの空欄チェック、daily_qtyの
    数値変換）・9割スキップ時の注意喚起は import_production_csv() と同じ
    ロジックを踏襲する。

    以前は本関数がここでmodels.kitting_plan.find_matching_plan_items()を
    呼んで候補（candidates/matched）・状態（status）を計算していたが、
    ステージングデータの永続化に伴い廃止した：候補はkitting_plan_itemsの
    スナップショットであり、DBに保存すると計画の変更（新バージョン作成・
    完了等）に追随できず陳腐化する。そのため候補計算は行わず、CSVの生の
    行データ（lot_no・product_name・daily_qty・report_date・worker_id）のみを
    保存し、候補の再照合はui.production_import_staging_window側で表示・
    再開のたびに行う方針とした（models.production_import_staging参照）。

    **1回のCSV取込で読み込んだ行は、同じロットNo・製品名であっても、全て
    別々の保留行として残す（まとめたり捨てたりしない）**。業務ルール：
    同じロットNo・製品名でも、日付が異なれば別のキッティング計画の実績で
    あり、どの計画の実績かは払出し日を手がかりに人が判断して割り当てる。
    同じCSVを再度取り込んだ場合に保留一覧が二重に増えない仕組みは
    models.production_import_staging.upsert_pending_csv_import_row()側に
    ある（本関数のdocstring参照）。

    判定結果は次の3種類に分類する：
      a. **既に登録済み**（EXCLUSION_REASON_ALREADY_REGISTERED）：対象の
         計画にproduction_daily行が1件以上あり、その合計がCSVのdaily_qtyと
         完全一致する。ステージング対象にしない。
      b. **数量0のため対象外**（EXCLUSION_REASON_ZERO_QTY）：対象の計画に
         production_daily行が1件も無く、かつCSVのdaily_qtyが0。登録すべき
         実績が無い（0produced）ため、ステージング対象にしない。
      c. **取込対象**：上記以外（候補が1件に定まらない行・実績はあるが
         数量が食い違う行・実績が無くdaily_qtyが0でない行）。従来通り
         pending_csv_import_rowsへ保存する。
    a・bいずれも、is_already_registered()の拡張された戻り値（existing_qty
    がNone＝実績無し、0以上＝実績あり）から判定する（2026-10-06改訂、
    is_already_registered()のdocstring参照）。

    CSV内の重複キー（同一lot_no・正規化済みproduct_name）の検出：件数のみ
    集計し、戻り値のnoticesに件数を含める（行はまとめない・捨てない。
    どの計画の実績かは候補提示・人の判断に委ねる）。

    戻り値：{
        "imported_count": 保留行として保存した件数（c. 取込対象）,
        "already_registered_count": a. の件数,
        "already_registered_rows": [a. と判定された行の詳細（下記参照）],
        "zero_excluded_count": b. の件数,
        "zero_excluded_rows": [b. と判定された行の詳細（下記参照）],
        "duplicate_key_group_count": CSV内で同一lot_no・製品名が複数回
            出現したキーの組数,
        "duplicate_key_row_count": 上記の組に含まれる行の総数,
        "warnings": [CSV解析時点の警告メッセージ（必須列欠落・数値変換エラー等）],
        "notices": [重複キーの件数等、警告件数には含めない情報メッセージ],
    }
    "report_date"はCSVの「払い出し日」相当の値をそのまま保持するが、
    参考情報としての表示用であり、実際の登録時（呼び出し元がこのモジュールの
    外で行う）にはこの値を使わない方針（登録ボタンを押した日を使うため）。

    "already_registered_rows"・"zero_excluded_rows"の各要素：{"csv_row_no",
    "lot_no", "product_name", "daily_qty", "report_date", "worker_id",
    "matched_kitting_list_no", "existing_qty", "import_batch_id", "reason"}。
    is_already_registered()が返す詳細情報（判定に使った一致計画・既存実績値）
    に、通常の保留行と同じCSV由来のフィールドとreason（上記
    EXCLUSION_REASON_*）を合わせたもの。ui.production_import_staging_window.
    ProductionImportStagingWindow.self._excluded_rows（「対象外一覧」、旧
    「登録済みリスト」）の元データとして使う。pending_csv_import_rowsへは
    保存しない（本関数のdocstring既存部分の通り）ため、この戻り値がこの
    情報の唯一の持ち出し口である。「対象外一覧から取込対象へ戻す」操作の
    ためにcsv_row_no・worker_id・import_batch_idも保持しておき、通常の
    保留行と同じ形でupsert_pending_csv_import_row()へ再投入できるように
    している。
    """
    rows = parse_csv_generic(file_path, COLUMN_MAP_PRODUCTION)

    warnings = []
    notices = []
    required_column_skipped_count = 0
    imported_count = 0
    already_registered_count = 0
    already_registered_rows = []
    zero_excluded_count = 0
    zero_excluded_rows = []

    import_batch_id = create_csv_import_batch(file_path, imported_by=default_worker_id)

    # 今回の取込処理全体で共有する、既に再利用済みの既存保留行idの集合
    # （models.production_import_staging.upsert_pending_csv_import_row()の
    # claimed_pending_row_ids参照）。CSVにリテラルに全く同じ内容の行が複数
    # ある場合でも、別バッチの既存行1件が繰り返し再利用されて2行目以降が
    # 消えてしまわないようにするための保護（2026-10-06追加）。
    claimed_pending_row_ids = set()

    # is_already_registered()もfind_matching_plan_items()経由でlist_active_plan_items()
    # を使うため、CSV行数分のN+1を避けるためループに入る前に1回だけ取得する
    # （group_active_plan_items_by_lot()参照）。is_already_registered()は
    # 完了済み計画こそ検出対象のため、include_completed=Trueで取得する
    # （is_already_registered()のdocstring参照）。
    plan_items_by_lot = group_active_plan_items_by_lot(include_completed=True)

    # CSV内の重複キー（同一lot_no・正規化済みproduct_name）の件数集計用
    # （行はまとめない・捨てない。件数の報告のみに使う）。
    seen_keys = {}

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

        key = (lot_no, normalize_product_name(product_name))
        seen_keys.setdefault(key, []).append(i)

        registration_check = is_already_registered(lot_no, product_name, daily_qty, plan_items_by_lot, report_date=report_date)
        if registration_check["already_registered"]:
            already_registered_count += 1
            already_registered_rows.append({
                "csv_row_no": i,
                "lot_no": lot_no,
                "product_name": product_name,
                "daily_qty": daily_qty,
                "report_date": report_date,
                "worker_id": worker_id,
                "matched_kitting_list_no": registration_check["matched_kitting_list_no"],
                "existing_qty": registration_check["existing_qty"],
                "import_batch_id": import_batch_id,
                "reason": EXCLUSION_REASON_ALREADY_REGISTERED,
            })
            continue

        if registration_check["existing_qty"] is None and daily_qty == 0:
            zero_excluded_count += 1
            zero_excluded_rows.append({
                "csv_row_no": i,
                "lot_no": lot_no,
                "product_name": product_name,
                "daily_qty": daily_qty,
                "report_date": report_date,
                "worker_id": worker_id,
                "matched_kitting_list_no": registration_check["matched_kitting_list_no"],
                "existing_qty": registration_check["existing_qty"],
                "import_batch_id": import_batch_id,
                "reason": EXCLUSION_REASON_ZERO_QTY,
            })
            continue

        upsert_pending_csv_import_row({
            "csv_row_no": i,
            "lot_no": lot_no,
            "product_name": product_name,
            "daily_qty": daily_qty,
            "report_date": report_date,
            "worker_id": worker_id,
        }, import_batch_id=import_batch_id, claimed_pending_row_ids=claimed_pending_row_ids)
        imported_count += 1

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

    duplicate_groups = {k: v for k, v in seen_keys.items() if len(v) > 1}
    duplicate_key_group_count = len(duplicate_groups)
    duplicate_key_row_count = sum(len(v) for v in duplicate_groups.values())
    if duplicate_key_group_count:
        notices.append(
            f"※ 同じロットNo・製品名の行がCSV内に複数ある組が{duplicate_key_group_count}組"
            f"（合計{duplicate_key_row_count}行）あります。まとめずに全て別々の保留行として"
            "残しています。どの計画の実績かは、払出し日を手がかりにご確認のうえ候補を選択してください。"
        )

    return {
        "imported_count": imported_count,
        "already_registered_count": already_registered_count,
        "already_registered_rows": already_registered_rows,
        "zero_excluded_count": zero_excluded_count,
        "zero_excluded_rows": zero_excluded_rows,
        "duplicate_key_group_count": duplicate_key_group_count,
        "duplicate_key_row_count": duplicate_key_row_count,
        "warnings": warnings,
        "notices": notices,
    }
