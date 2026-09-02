# services/inventory_diff_service.py
"""
在庫（PC在庫）＋仕掛（WIP）＋仕損（NG）＋理論在庫を突き合わせ、
96コードごとの差異を算出するサービス。

WIP・NG展開は BOM基盤（services.bom_service.BOMService、TSV → bom_master）
経由で行う。
"""
import logging

from models.inventory import list_inventory
from models.theoretical_inventory import list_theoretical_inventory
from models.scrap_records import query_scrap_totals
from services.bom_service import BOMService
from services.production_service import build_daily_report

logger = logging.getLogger(__name__)

_bom_service = BOMService()


def _collect_wip_totals():
    """
    本日の日報データ（build_daily_report）が持つ仕掛数量（surplus_qty）を、
    setup_file_no・production_side（・mounting_line）ごとに
    BOMService.expand_wip_to_parts() で96コードへ展開し、part_no（96コード）
    ごとの合計に集計する。

    mounting_line（実装ライン）はrow（_build_report_rows()が計画から
    補完済み）からそのまま渡す。同一file_no・sideに複数の実装ラインが
    存在するTSVでは、ラインを指定しないと部品数量が過大計算されるため
    （services.bom_service._calculate_bom()参照）、計画が見つからず
    row["mounting_line"]がNoneの場合は、BOMService側のデフォルト方針
    （最初に見つかったライン1本分のみを使う）に委ねる。

    以下の行は0扱いとしてスキップし、レポート自体は表示できるようにする：
    - setup_file_no が空、または surplus_qty が0（仕掛なし）
    - production_side が1/2として解釈できない
    - 該当 setup_file_no のBOM TSVが共有フォルダに存在しない（FileNotFoundError）
    - BOM展開に失敗した（ValueError：TSVパース失敗・入力不正等）
    """
    totals = {}

    # build_daily_report()は(report_rows, inconsistency_warnings)のタプルを返す
    # （services.production_service._build_report_rows()の面1省略・不整合検知に
    # 伴う変更）。inconsistency_warningsはここでは使わない（在庫差異レポートは
    # 表示専用の集計処理のため、面1・面2の実績不整合そのものへの警告は
    # ui.monthly_report_window.py側の責務とする）。
    report_rows, _inconsistency_warnings = build_daily_report()
    for row in report_rows:
        file_no = row.get("file_no")
        wip_qty = row.get("surplus_qty")

        if not file_no or not wip_qty:
            continue

        try:
            side = int(row.get("production_side"))
        except (TypeError, ValueError):
            logger.warning(
                "WIP展開スキップ: file_no=%s の production_side を解釈できません（値: %r）。",
                file_no, row.get("production_side"),
            )
            continue

        if side not in (1, 2):
            logger.warning(
                "WIP展開スキップ: file_no=%s の production_side が1/2以外です（値: %s）。",
                file_no, side,
            )
            continue

        try:
            parts = _bom_service.expand_wip_to_parts({
                "setup_file_no": file_no,
                "production_side": side,
                "wip_qty": wip_qty,
                "lot_no": row.get("lot_no"),
                "mounting_line": row.get("mounting_line"),
            })
        except FileNotFoundError:
            logger.warning("WIP展開スキップ: file_no=%s のBOM TSVが見つかりません。", file_no)
            continue
        except ValueError as e:
            logger.warning("WIP展開スキップ: file_no=%s のBOM展開に失敗しました（%s）。", file_no, e)
            continue

        for part in parts:
            totals[part["part_no"]] = totals.get(part["part_no"], 0) + part["qty"]

    return totals


def _collect_scrap_totals():
    """
    NG（仕損）実績（models.scrap_records）を、96コード単位で集計して返す。

    scrap_records は ui.ng_input_window で、BOMService による展開結果から
    操作者が選択・登録した「96コードごとの消費数量」を既に保存済みのため、
    ここでは再展開せず単純に集計するだけでよい。
    """
    return query_scrap_totals()


def build_inventory_diff_report():
    """
    在庫（PC在庫）＋仕掛（WIP）＋仕損（NG）＋理論在庫を突き合わせ、
    96コードごとの差異一覧を構築する。

    合計 = 在庫 + 仕掛 + 仕損
    差異 = 理論在庫 − 合計
    """
    stock_by_part = {row["part_no"]: row["stock_qty"] for row in list_inventory()}
    theoretical_by_part = {row["part_no"]: row["qty"] for row in list_theoretical_inventory()}
    wip_totals = _collect_wip_totals()
    scrap_totals = _collect_scrap_totals()

    part_nos = set(stock_by_part) | set(theoretical_by_part) | set(wip_totals) | set(scrap_totals)

    report_rows = []
    for part_no in sorted(part_nos):
        stock_qty = stock_by_part.get(part_no, 0)
        wip_qty = wip_totals.get(part_no, 0)
        scrap_qty = scrap_totals.get(part_no, 0)
        theoretical_qty = theoretical_by_part.get(part_no, 0)

        total_qty = stock_qty + wip_qty + scrap_qty
        diff_qty = theoretical_qty - total_qty

        report_rows.append({
            "part_no": part_no,
            "stock_qty": stock_qty,
            "wip_qty": wip_qty,
            "scrap_qty": scrap_qty,
            "total_qty": total_qty,
            "theoretical_qty": theoretical_qty,
            "diff_qty": diff_qty,
        })

    return report_rows
