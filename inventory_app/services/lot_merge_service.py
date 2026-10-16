# services/lot_merge_service.py
"""
ロットの統合（D-136・D-145・D-150）。末尾「-」のロット（例 123456-）を、「-」の無いロット（123456）へ統合した、という情報を扱う。
集計（services/lot_status_service.py）は load_lot_merge_map() を通して、統合後のロット単位でまとめる（第2段階、D-150）。
実績CSVの照合（候補・自動登録・「似たロットNoあり」）には、まだ反映していない（第3段階）。
既定DB（未選択）では DB に触れない（D-56・D-57）。
"""
import config
from models.kitting_plan import list_plan_items_for_all_lots
from models.lot_merges import MERGE_SUFFIX, delete_lot_merge, insert_lot_merge, is_valid_merge_pair, list_lot_merges

OPERATION_NAME_MERGE = "ロット統合"
OPERATION_NAME_UNMERGE = "ロット統合解除"


class LotMergeError(ValueError):
    """統合・解除ができないとき。メッセージはそのまま画面に出せる。"""


class LotMergeMap:
    """
    ある時点の統合の情報（表を1回だけ読んだもの）。1回の処理（集計1回など）では、これを1つ作って使い回す。
    target_of(): ロットNo → 統合後のロットNo（統合されていなければそのまま）。members_of(): 統合後のロットNo → 統合しているロットの一覧。
    """

    def __init__(self, rows):
        self.rows = list(rows)
        self._target_by_member = {r["member_lot"]: r["target_lot"] for r in self.rows}
        self._members_by_target = {}
        for r in self.rows:
            self._members_by_target.setdefault(r["target_lot"], []).append(r["member_lot"])

    def target_of(self, lot_no):
        return self._target_by_member.get(lot_no, lot_no)

    def members_of(self, target_lot):
        return list(self._members_by_target.get(target_lot, []))

    def is_merged_member(self, lot_no):
        return lot_no in self._target_by_member


def load_lot_merge_map() -> LotMergeMap:
    """今の月次DB の統合の情報を1回だけ読む。既定DB では DB に触れずに空のものを返す。"""
    if config.is_default_db():
        return LotMergeMap([])
    return LotMergeMap(list_lot_merges())


def merged_lot_nos(lot_no: str) -> list:
    """lot_no と同じ統合後のロットに入るロットNoの一覧（統合先・統合されるロットの順）。統合していなければ [lot_no]。"""
    merge_map = load_lot_merge_map()
    target = merge_map.target_of(lot_no)
    members = merge_map.members_of(target)
    return [target] + members if members else [lot_no]


def target_lot_for_member(lot_no):
    """統合されうるロットNo（末尾の「-」1つ）なら統合先のロットNo、そうでなければ None（「-1」「K」「.1」「-」だけ、など）。"""
    if not isinstance(lot_no, str) or not lot_no.endswith(MERGE_SUFFIX):
        return None
    target = lot_no[:-len(MERGE_SUFFIX)]
    return target if is_valid_merge_pair(lot_no, target) else None


def _active_lot_nos():
    return {item["lot_no"] for item in list_plan_items_for_all_lots()}


def list_merge_candidates() -> list:
    """
    統合の画面に並べる組。今の月次DB の有効な計画に X と X- の両方があるロットの組と、統合済みの組（計画が無くなっていても出す）。
    各組の lots には、ロットごとの評価（evaluate_lot_status()。統合済みの組でも、統合しない計算）を入れる。計画が無いロットは None。
    戻り値: [{"target_lot", "member_lot", "merged"（bool）, "merge"（統合の行 or None）, "lots": {ロットNo: 評価 or None}}]（統合先の順）
    """
    # lot_status_service がこのモジュールを import するので、ここで import する（循環 import を避ける）。
    from services.lot_status_service import evaluate_lot_status
    if config.is_default_db():
        return []
    unmerged = LotMergeMap([])
    lots = _active_lot_nos()
    merges = {r["member_lot"]: r for r in list_lot_merges()}
    pairs = {}
    for lot_no in lots:
        target = target_lot_for_member(lot_no)
        if target is not None and target in lots:
            pairs[lot_no] = target
    for member, row in merges.items():
        pairs[member] = row["target_lot"]

    results = []
    for member, target in sorted(pairs.items(), key=lambda kv: (kv[1], kv[0])):
        results.append({
            "target_lot": target,
            "member_lot": member,
            "merged": member in merges,
            "merge": merges.get(member),
            "lots": {lot: (evaluate_lot_status(lot, merge_map=unmerged) if lot in lots else None)
                     for lot in (target, member)},
        })
    return results


def preview_merge(member_lot: str) -> dict:
    """
    統合の画面の試算（D-153）。member_lot を統合した場合と、統合しない今の値を、同じ計算（evaluate_lot_status()）で出す。DB には書かない。
    戻り値: {"target_lot", "member_lot", "before": {ロットNo: {"order_quantity", "completed", "remaining"} or None},
             "after": {"order_quantity", "completed", "remaining", "status"}}
    """
    from services.lot_status_service import evaluate_lot_status
    if config.is_default_db():
        raise LotMergeError("データベースが選択されていません。")
    target = target_lot_for_member(member_lot)
    if target is None:
        raise LotMergeError(f"{member_lot} は統合できるロットNo.ではありません。")
    current = load_lot_merge_map()
    unmerged = LotMergeMap([r for r in current.rows if r["member_lot"] != member_lot])
    merged = LotMergeMap(unmerged.rows + [{"member_lot": member_lot, "target_lot": target}])

    def figures(lot_eval):
        return {"order_quantity": lot_eval["order_quantity"], "completed": lot_eval["lot_completed"],
                "remaining": lot_eval["order_quantity"] - lot_eval["lot_completed"], "status": lot_eval["status"]}

    before = {}
    for lot in (target, member_lot):
        try:
            before[lot] = figures(evaluate_lot_status(lot, merge_map=unmerged))
        except ValueError:
            before[lot] = None
    return {"target_lot": target, "member_lot": member_lot, "before": before,
            "after": figures(evaluate_lot_status(target, merge_map=merged))}


def check_merge(member_lot) -> list:
    """統合できない理由のリスト（できるなら空）。D-136：末尾「-」だけ、両方に有効な計画があること、まだ統合していないこと。"""
    problems = []
    target = target_lot_for_member(member_lot)
    if target is None:
        return [f"{member_lot} は統合できるロットNo.ではありません（末尾に「-」が1つ付いたロットだけを統合できます）。"]
    lots = _active_lot_nos()
    if target not in lots:
        problems.append(f"統合先の {target} に有効な計画がありません（「-」付きだけのロットは統合しません）。")
    if member_lot not in lots:
        problems.append(f"{member_lot} に有効な計画がありません。")
    if load_lot_merge_map().is_merged_member(member_lot):
        problems.append(f"{member_lot} はすでに統合済みです。")
    return problems


def merge_lots(member_lot: str, worker_name: str, note: str = None) -> dict:
    """member_lot（例 123456-）を統合先（123456）へ統合する。統合の行と操作履歴を1つのトランザクションで書く。できなければ LotMergeError。"""
    if config.is_default_db():
        raise LotMergeError("データベースが選択されていません。")
    problems = check_merge(member_lot)
    if problems:
        raise LotMergeError("\n".join(problems))
    target = target_lot_for_member(member_lot)
    return insert_lot_merge(
        member_lot, target, worker_name, note,
        operation=(worker_name, OPERATION_NAME_MERGE, f"{member_lot} → {target}"),
    )


def unmerge_lot(member_lot: str, worker_name: str) -> dict:
    """統合を解除する（統合の行を消す。統合している間に登録した実績などは変えない）。統合されていなければ LotMergeError。"""
    if config.is_default_db():
        raise LotMergeError("データベースが選択されていません。")
    target = load_lot_merge_map().target_of(member_lot)
    removed = delete_lot_merge(
        member_lot, operation=(worker_name, OPERATION_NAME_UNMERGE, f"{member_lot} → {target} を解除"),
    )
    if removed is None:
        raise LotMergeError(f"{member_lot} は統合されていません。")
    return removed
