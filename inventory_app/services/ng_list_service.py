"""
NG 一覧（ui/ng_wip/ng_input_window.py の右ペイン）の行を組み立てる。DB を読むだけで、ウィジェットには触れない。
段階4で ui/ng_wip/ng_input_window.py から移した（関数の中身は変えていない）。在庫差異レポート前の未処理件数（services/unprocessed_check_service.py）も使う。
"""
from models.kitting_plan import find_plan_item_by_kitting_no
from models.ng_declarations import list_ng_declarations_latest
from models.ng_exclusion_list import list_ng_exclusions
from models.scrap_records import list_scrap_summary_by_kitting_no

# NG 一覧の列順。_fetch_ng_list_rows() のタプルと一致させること（unprocessed_check_service もこの定義を参照する）。
NG_LIST_COLUMNS = (
    "kitting_list_no", "lot_no", "board_name", "file_no", "side",
    "is_unplanned", "status", "declared_ng_qty", "part_count",
    "record_count", "last_report_date", "excluded",
)


def fetch_ng_list_rows():
    """
    NG 一覧の DB アクセスだけを行う（ウィジェットに触れない）。
    NG 申告と展開済み（scrap_records）を (kitting_list_no, lot_no, production_side) で合わせ、状態を付ける:
    未展開（申告だけ）／展開済み（両方）／展開済み（申告記録なし）（展開済みだけ。機能の導入前のデータなど）。
    キーに lot_no を含めるのは kitting_list_no が重複するため。対象外の行も一覧から消さず、「対象外」列で示す（解除の導線を残す）。
    """
    declarations = {
        (d["kitting_list_no"], d["lot_no"] or "", d["production_side"]): d
        for d in list_ng_declarations_latest()
    }
    expanded = {
        (s["kitting_list_no"], s["lot_no"] or "", s["production_side"]): s
        for s in list_scrap_summary_by_kitting_no()
    }
    excluded_keys = {
        (e["kitting_list_no"], e["lot_no"] or "", e["production_side"])
        for e in list_ng_exclusions()
    }

    rows = []
    for key in sorted(set(declarations) | set(expanded)):
        kitting_list_no, lot_no, side = key
        declaration = declarations.get(key)
        summary = expanded.get(key)

        if declaration and summary:
            status = "展開済み"
        elif declaration:
            status = "未展開"
        else:
            status = "展開済み（申告記録なし）"

        representative = summary or declaration
        is_unplanned = bool(representative["is_unplanned"])
        file_no = representative["file_no"]

        board_name = ""
        if not is_unplanned:
            plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no) if lot_no \
                else find_plan_item_by_kitting_no(kitting_list_no)
            if plan:
                board_name = plan["board_name"] or ""

        declared_ng_qty_text = f"{declaration['ng_qty']:g}" if declaration else ""
        part_count_text = str(summary["part_count"]) if summary else "0"
        record_count_text = str(summary["record_count"]) if summary else "0"
        last_report_date = (summary["last_report_date"] if summary else None) or \
            (declaration["report_date"] if declaration else "")

        rows.append((
            kitting_list_no,
            lot_no,
            board_name,
            file_no,
            f"面{side}" if side in (1, 2) else str(side),
            "計画外" if is_unplanned else "",
            status,
            declared_ng_qty_text,
            part_count_text,
            record_count_text,
            last_report_date,
            "対象外" if key in excluded_keys else "",
        ))

    return rows


# 段階5で公開名を付けた。旧名は同じものを指す別名として残す（呼び出し側は新しい名前を使う）。
_fetch_ng_list_rows = fetch_ng_list_rows
