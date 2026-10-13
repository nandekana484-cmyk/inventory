"""
仕掛一覧（ui/wip_expansion_window.py の右ペイン）の行を組み立てる。DB を読むだけで、ウィジェットには触れない。
段階4で ui/wip_expansion_window.py から移した。中身で変えたのは、STATUS_CONFIRMED_ORPHAN の参照先（画面クラスの属性 → このモジュールの定数）だけ。
"""
from models.wip_board_snapshot import list_wip_snapshot
from models.wip_exclusion_list import list_wip_exclusions
from models.wip_scrap_records import list_wip_scrap_summary

# 仕掛一覧の列順。_fetch_wip_list_rows() のタプルと一致させること（unprocessed_check_service もこの定義を参照する）。
WIP_LIST_COLUMNS = (
    "kitting_list_no", "board_name", "file_no", "side", "lot_no",
    "mounting_line", "surplus_qty", "status", "created_at", "excluded",
)

# スナップショットの無い確定済み行の状態。「確定済み」「未確定」と別の値にして、一括展開・登録の対象や未確定件数に入らないようにする。
STATUS_CONFIRMED_ORPHAN = "確定済み(スナップショットなし)"


def _fetch_wip_list_rows():
    """
    仕掛一覧の DB アクセスだけを行う（ウィジェットに触れない）。
    スナップショットと確定済みの展開結果を (kitting_list_no, lot_no, production_side) で合わせ、「確定済み」「未確定」を付ける。
    production_side はスナップショットが TEXT、確定済みが INTEGER なので、str() にそろえて比べる。
    対象外の行も一覧から消さず「対象外」列で示す。スナップショットから消えた確定済み行も、実績修正で見つけられるよう末尾に足す。
    """
    summaries = list_wip_scrap_summary()
    confirmed_keys = {
        (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
        for s in summaries
    }
    excluded_keys = {
        (e["kitting_list_no"], e["lot_no"] or "", e["file_no"], str(e["production_side"]))
        for e in list_wip_exclusions()
    }

    rows = []
    snapshot_keys = set()
    for row in list_wip_snapshot():
        key = (row["kitting_list_no"], row["lot_no"] or "", str(row["production_side"]))
        snapshot_keys.add(key)
        status = "確定済み" if key in confirmed_keys else "未確定"
        exclusion_key = (row["kitting_list_no"], row["lot_no"] or "", row["file_no"], str(row["production_side"]))
        rows.append((
            row["kitting_list_no"],
            row["board_name"],
            row["file_no"],
            row["production_side"],
            row["lot_no"] or "",
            row["mounting_line"] or "",
            f"{row['surplus_qty']:g}",
            status,
            row["created_at"] or "",
            "対象外" if exclusion_key in excluded_keys else "",
        ))

    for s in summaries:
        key = (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
        if key in snapshot_keys:
            continue
        exclusion_key = (s["kitting_list_no"], s["lot_no"] or "", s["file_no"], str(s["production_side"]))
        rows.append((
            s["kitting_list_no"],
            "",
            s["file_no"],
            str(s["production_side"]),
            s["lot_no"] or "",
            "",
            "",
            STATUS_CONFIRMED_ORPHAN,
            "",
            "対象外" if exclusion_key in excluded_keys else "",
        ))
    return rows
