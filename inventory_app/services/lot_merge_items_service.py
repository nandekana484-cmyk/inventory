"""
ロットの統合（D-136・D-150）で、統合後のロットを評価するための計画の行と、統合の扱い（merge_ctx）を作る。
集計の本体（services/lot_status_service.py）が使う。lot_status_service.py が500行を超えたので、そこから移した（中身は変えていない）。
業務ルールは docs/domain/lot_merge.md。
"""
from models.kitting_plan import list_plan_items_by_lot


def _representative_order_qty(plan_items: list):
    """ロットの発注数の代表値（食い違うときは先頭行の値。_compute_lot_completion() と同じ決め方）。"""
    values = sorted({item["order_qty"] for item in plan_items})
    return plan_items[0]["order_qty"] if len(values) > 1 else values[0]


def _side_of(item) -> str:
    return str(item.get("production_side") or "").strip()


def side2_file_keys(items: list) -> set:
    """面2の計画がある (元のロットNo, ファイルNo) の集合。この組の面1の計画は、完成数・表示に使わない（D-167）。"""
    return {(it["lot_no"], it["setup_file_no"]) for it in items if _side_of(it) == "2"}


def find_side_composition_conflicts(target_lot: str, member_lots: list, items: list) -> list:
    """
    統合先と統合されるロットの両方にあるファイルNoで、面の構成（面2あり／面1のみ）が食い違うものを返す（D-168。統合させない・エラーにする）。
    items は統合先と統合されるロットの計画の行（lot_no は元のまま）。
    戻り値: [{"setup_file_no", "target_lot", "target_label", "member_lot", "member_label"}, ...]（統合されるロット・ファイルNoの順）。
    """
    files_by_lot = {}
    for it in items:
        if _side_of(it) in ("1", "2"):
            files_by_lot.setdefault(it["lot_no"], set()).add(it["setup_file_no"])
    side2 = side2_file_keys(items)

    def label(lot, file_no):
        return "面2あり" if (lot, file_no) in side2 else "面1のみ"

    conflicts = []
    for member in member_lots:
        for file_no in sorted(files_by_lot.get(target_lot, set()) & files_by_lot.get(member, set())):
            if label(target_lot, file_no) != label(member, file_no):
                conflicts.append({"setup_file_no": file_no, "target_lot": target_lot, "target_label": label(target_lot, file_no),
                                  "member_lot": member, "member_label": label(member, file_no)})
    return conflicts


def format_side_conflict(conflict: dict) -> str:
    """例「ファイル0553：面の構成が食い違っています（123456 は面1のみ、123456- は面2あり）。手入力で対応してください」（D-168）。"""
    return (f"ファイル{conflict['setup_file_no']}：面の構成が食い違っています（{conflict['target_lot']} は{conflict['target_label']}、"
            f"{conflict['member_lot']} は{conflict['member_label']}）。手入力で対応してください")


def _merge_lot_items(target_lot: str, items: list, member_lots: list):
    """
    統合後のロット（D-150）の評価に使う計画の行と、統合の扱い（merge_ctx）を作る。保存値・計画の行の lot_no は変えない。
    - 統合されるロットの行は、発注数を統合先の代表値に、基板名を統合先の同じファイルNoの基板名（1種類のときだけ）にそろえた写しにする
    - 面2を面1に数える扱いは無い。完成数は元のロットごとに「面2があれば面2」の規則で求めて足す（D-167。D-151・D-157 は廃止）
    - 同じファイルNoの面の構成が統合先と食い違えば side_conflicts に入れる（ロット進捗チェックでエラーにする。D-168）
    統合先に計画が無いときは、そろえる元が無いので、統合されるロットの行の発注数・基板名はそのまま使う。
    """
    target_items = [it for it in items if it["lot_no"] == target_lot]
    member_items = [it for it in items if it["lot_no"] != target_lot]
    ctx = {
        "target_lot": target_lot,
        "member_lots": list(member_lots),
        "display_lot_no": f"{target_lot}（" + "、".join(f"+{m}" for m in member_lots) + "）",
        "aligned_order_qty": None,
        "side_conflicts": find_side_composition_conflicts(target_lot, member_lots, items),
    }
    if not target_items:
        return items, ctx

    aligned_order_qty = _representative_order_qty(target_items)
    ctx["aligned_order_qty"] = aligned_order_qty
    names_by_file = {}
    for it in target_items:
        names_by_file.setdefault(it["setup_file_no"], set()).add(it["board_name"])

    aligned = []
    for it in member_items:
        names = names_by_file.get(it["setup_file_no"])
        board_name = next(iter(names)) if names and len(names) == 1 else it["board_name"]
        aligned.append(dict(it, order_qty=aligned_order_qty, board_name=board_name))
    return target_items + aligned, ctx


def _group_items_by_merged_lot(plan_items: list, merge_map) -> dict:
    """計画の行を統合後のロットごとにまとめる。戻り値: {統合後のロットNo: (評価に使う行, merge_ctx or None)}。"""
    grouped = {}
    for item in plan_items:
        grouped.setdefault(merge_map.target_of(item["lot_no"]), []).append(item)
    result = {}
    for lot_no, items in grouped.items():
        members = merge_map.members_of(lot_no)
        result[lot_no] = _merge_lot_items(lot_no, items, members) if members else (items, None)
    return result


def _load_lot_items(lot_no: str, merge_map):
    """lot_no（統合されるロットでもよい）の統合後のロットNo・評価に使う行・merge_ctx を返す。計画が無ければ ValueError。"""
    target = merge_map.target_of(lot_no)
    members = merge_map.members_of(target)
    if not members:
        plan_items = list_plan_items_by_lot(lot_no)
        if not plan_items:
            raise ValueError(f"ロットNo. {lot_no} の計画が見つかりません。")
        return lot_no, plan_items, None
    plan_items = []
    for lot in [target] + members:
        plan_items += list_plan_items_by_lot(lot)
    if not plan_items:
        raise ValueError(f"ロットNo. {lot_no} の計画が見つかりません。")
    items, ctx = _merge_lot_items(target, plan_items, members)
    return target, items, ctx


def _pairs_of(lot_no: str, plan_items: list, merge_ctx) -> list:
    """実績を引く (計画No, ロットNo) の組。統合したロットでは計画ごとに元のロットNoで引く（計画Noを共有する組で二重に足さない）。"""
    if merge_ctx is None:
        return [(item["kitting_list_no"], lot_no) for item in plan_items]
    return [(item["kitting_list_no"], item["lot_no"]) for item in plan_items]


def _merge_result_fields(merge_ctx: dict) -> dict:
    """統合したロットの評価結果にだけ足すキー（統合の無いロットの結果の形は変えない）。"""
    return {
        "merged_lot_nos": list(merge_ctx["member_lots"]),
        "display_lot_no": merge_ctx["display_lot_no"],
        "aligned_order_qty": merge_ctx["aligned_order_qty"],
        "merge_side_conflicts": merge_ctx["side_conflicts"],
    }
