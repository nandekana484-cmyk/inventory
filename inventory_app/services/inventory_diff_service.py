# services/inventory_diff_service.py
"""
在庫（PC在庫）＋仕掛（WIP）＋仕損（NG）＋理論在庫を突き合わせ、
96コードごとの差異を算出するサービス。

NG展開・WIP展開はいずれもBOM基盤（services.bom_service.BOMService、TSV →
bom_master）経由で事前に行われ、結果がmodels.scrap_records／
models.wip_scrap_recordsに確定登録済みという前提で、本サービスは
それらの集計結果を読むだけの薄いレイヤーとする（詳細は_collect_wip_totals()
のdocstring参照）。
"""
from models.inventory import list_inventory
from models.theoretical_inventory import list_theoretical_inventory
from models.scrap_records import query_scrap_totals
from models.wip_board_snapshot import list_wip_snapshot
from models.wip_scrap_records import query_wip_totals, list_wip_scrap_summary


def _collect_wip_totals():
    """
    仕掛展開画面（ui.wip_expansion_window.WipExpansionWindow）で確定登録済みの
    仕掛展開結果（models.wip_scrap_records）から、96コード（part_no）単位の
    合計仕掛数量を取得する。

    以前は本日の日報データ（services.production_service.build_daily_report()、
    「本日」のみが対象範囲）が持つsurplus_qtyを、呼び出しのたびに
    BOMService.expand_wip_to_parts()（共有フォルダ上のTSV読み込みを伴う）で
    都度展開していた。この方式には2つの問題があった：
      1. build_daily_report()は「本日」限定であり、月報の「仕掛数量抽出」
         （models.wip_board_snapshot）や仕掛展開画面（models.wip_scrap_records）
         とはそもそも別系統のデータソースだった（設計上の食い違い。調査で判明）。
      2. 在庫差異レポートを開くたびに、仕掛のある行数分だけBOM展開（共有フォルダ
         アクセス）が直列実行され、画面表示前にUIが完全にフリーズする、
         全画面中で最も深刻な遅延要因になっていた。
    仕掛展開画面側で既に確定登録（BOM展開済み・96コード単位で保存済み）された
    データをそのまま集計するだけの方式に変更したことで、本関数はBOM展開・
    共有フォルダアクセスを一切行わなくなり、DBのみで完結する。

    注意：まだ仕掛展開画面で確定登録されていない仕掛基板
    （models.wip_board_snapshotには存在するがmodels.wip_scrap_recordsに
    対応行が無いもの）の分は、ここには反映されない。その件数は
    count_unconfirmed_wip_boards()で別途取得でき、ui.inventory_diff_window側で
    注意表示するために使う。
    """
    return query_wip_totals()


def count_unconfirmed_wip_boards() -> int:
    """
    models.wip_board_snapshot（月報の「仕掛数量抽出」によるスナップショット）の
    うち、models.wip_scrap_records（仕掛展開画面での確定登録）に対応する行が
    まだ無いものの件数を返す。

    ui.wip_expansion_window.WipExpansionWindow._fetch_wip_list_rows() の
    「未確定」判定と同じロジック（(kitting_list_no, lot_no, production_side)
    キーでの突き合わせ）をここでも独立して行う。在庫差異レポート
    （ui.inventory_diff_window）が、_collect_wip_totals()に反映されていない
    未確定分の存在をユーザーに注意表示するために使う。
    """
    confirmed_keys = {
        (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
        for s in list_wip_scrap_summary()
    }

    count = 0
    for row in list_wip_snapshot():
        key = (row["kitting_list_no"], row["lot_no"] or "", str(row["production_side"]))
        if key not in confirmed_keys:
            count += 1
    return count


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
