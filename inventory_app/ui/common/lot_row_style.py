"""
ロット単位の一覧（実績レポート・ロット進捗チェック・日々の引落一覧）の行の色。背景色＝ロットごとの縞、文字色＝状態の区分。
文字色の区分（"needs_review"/"shortfall"/None）は services/lot_status_service._lot_status_color_category() が決める。
3画面で同じ色・同じタグ名にするため、定義はここにだけ置く（D-125）。
"""

LOT_STRIPE_TAG_A = "lot_stripe_a"
LOT_STRIPE_TAG_B = "lot_stripe_b"
# 既存の警告色（赤・オレンジ・黄・緑など）と衝突しないよう、彩度の無い2色だけにする。
LOT_STRIPE_COLOR_A = "#ffffff"
LOT_STRIPE_COLOR_B = "#e6e6e6"

# 縞（background）とは別のタグで foreground だけを設定するので、1行に両方付けても競合しない。
STATUS_COLOR_TAG_NEEDS_REVIEW = "status_needs_review"
STATUS_COLOR_TAG_SHORTFALL = "status_shortfall_note"
STATUS_TEXT_COLOR_NEEDS_REVIEW = "#cc0000"  # 赤（確認・修正が必要）
STATUS_TEXT_COLOR_SHORTFALL = "#cc6600"  # オレンジ（不足のため引落0）

STATUS_COLOR_CATEGORY_TO_TAG = {
    "needs_review": STATUS_COLOR_TAG_NEEDS_REVIEW,
    "shortfall": STATUS_COLOR_TAG_SHORTFALL,
}


def configure_lot_stripe_tags(tree):
    """Treeview 生成直後に1回だけ呼ぶ（tag_configure() はタグの見た目を定義するだけ）。"""
    tree.tag_configure(LOT_STRIPE_TAG_A, background=LOT_STRIPE_COLOR_A)
    tree.tag_configure(LOT_STRIPE_TAG_B, background=LOT_STRIPE_COLOR_B)


def configure_status_color_tags(tree):
    """Treeview 生成直後に1回だけ呼ぶ。configure_lot_stripe_tags() と両方呼ぶこと。"""
    tree.tag_configure(STATUS_COLOR_TAG_NEEDS_REVIEW, foreground=STATUS_TEXT_COLOR_NEEDS_REVIEW)
    tree.tag_configure(STATUS_COLOR_TAG_SHORTFALL, foreground=STATUS_TEXT_COLOR_SHORTFALL)


class LotRowTagger:
    """
    行のタグ（縞＋文字色）を返す。縞の色は lot_no ごとに初めて出てきたときに1回だけ決めて使い回す。
    「直前の行とロットが変わったら切り替える」方式だと、同じロットの行が離れて並ぶ一覧（実績レポート）で色が分かれるため。
    1回の描画ごとに作り直すこと（色の割り当て順は、その描画で初めて出てきた順）。
    """

    def __init__(self):
        self._tag_by_lot = {}
        self._use_tag_b = False

    def tags_for(self, lot_no, status_color_category):
        if lot_no not in self._tag_by_lot:
            self._use_tag_b = not self._use_tag_b
            self._tag_by_lot[lot_no] = LOT_STRIPE_TAG_B if self._use_tag_b else LOT_STRIPE_TAG_A
        tags = [self._tag_by_lot[lot_no]]
        color_tag = STATUS_COLOR_CATEGORY_TO_TAG.get(status_color_category)
        if color_tag:
            tags.append(color_tag)
        return tuple(tags)

    def lot_count(self):
        return len(self._tag_by_lot)
