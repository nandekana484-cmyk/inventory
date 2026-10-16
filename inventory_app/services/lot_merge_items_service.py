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


def _merge_lot_items(target_lot: str, items: list, member_lots: list):
    """
    統合後のロット（D-150）の評価に使う計画の行と、統合の扱い（merge_ctx）を作る。保存値・計画の行の lot_no は変えない。
    - 統合されるロットの行は、発注数を統合先の代表値に、基板名を統合先の同じファイルNoの基板名（1種類のときだけ）にそろえた写しにする
    - 統合されるロットのファイルNoに面2の計画があれば、そのファイルNoの面1の数量として面2の実績を使う（side1_extra。D-157、
      「面2があるということは、面1もあるとみなす」）。そのファイルNoの統合されるロット自身の面1の実績は使わない（side1_replaced。
      面2の登録で面1へ自動入力された実績 D-113 を二重に足さないため）。面1の計画が統合後のロットのどこにも無いファイルNoには足さない
    統合先に計画が無いときは、そろえる元が無いので、統合されるロットの行の発注数・基板名はそのまま使う。
    """
    target_items = [it for it in items if it["lot_no"] == target_lot]
    member_items = [it for it in items if it["lot_no"] != target_lot]
    ctx = {
        "target_lot": target_lot,
        "member_lots": list(member_lots),
        "display_lot_no": f"{target_lot}（" + "、".join(f"+{m}" for m in member_lots) + "）",
        "aligned_order_qty": None,
        "side1_extra": [],
        "side1_replaced": set(),
    }
    # 面1のキーは統合先の面1の計画を優先し、無ければ統合されるロットの面1の計画から取る（新しい (ファイルNo, 面1) は作らない）
    side1_key_by_file = {}
    for it in member_items + target_items:
        if _side_of(it) == "1":
            side1_key_by_file[it["setup_file_no"]] = (it["setup_file_no"], it["production_side"])
    side2_files = {it["setup_file_no"] for it in member_items if _side_of(it) == "2"}
    for it in member_items:
        key = side1_key_by_file.get(it["setup_file_no"])
        if it["setup_file_no"] not in side2_files or key is None:
            continue
        pair = (it["kitting_list_no"], it["lot_no"])
        if _side_of(it) == "2":
            ctx["side1_extra"].append((pair, key))
        elif _side_of(it) == "1":
            ctx["side1_replaced"].add(pair)
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
    }
