# ui/plan_candidate_dialog.py
"""
kitting_list_no検索で複数のlot_no候補にあたった場合に、ユーザーに1件選ばせる
共通モーダルダイアログ。

実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースが
478件確認されており（services.production_service._resolve_plan_item()参照）、
元々はui.kitting_production_entry.KittingProductionEntryWindow.search_plan()と
ui.ng_input_window.NgInputWindow.on_expand()/_expand_from_kitting_no()の
両方から共通のUI部品として使うために切り出した。生産実績入力画面はその後、
キッティングリストNo.検索欄自体を廃止し右ペインの計画一覧からの選択に
一本化した（選択行から常にlot_noも一意に判明するため、本ダイアログを経由する
経路が無くなった）ため、現在使っているのはui.ng_input_window.NgInputWindowの
みである。
"""
import tkinter as tk
from tkinter import ttk, messagebox

_COLS = ("lot_no", "board_name", "setup_file_no", "production_side", "planned_qty", "order_qty")
_HEADERS = {
    "lot_no": "ロットNo.",
    "board_name": "基板名",
    "setup_file_no": "ファイルNo.",
    "production_side": "生産面",
    "planned_qty": "計画数",
    "order_qty": "注文数",
}
_RIGHT_ALIGNED = {"planned_qty", "order_qty"}


def _format_side(side):
    if side in (1, 2):
        return f"面{side}"
    return "" if side is None else str(side)


def _format_qty(value):
    if value is None:
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def select_plan_candidate(parent, kitting_list_no, candidates):
    """
    候補一覧（models.kitting_plan.list_active_plan_items_by_kitting_no()の戻り値、
    kitting_plan_itemsの行の辞書のリスト）をTreeviewで一覧表示し、ユーザーに
    1件選ばせるモーダルダイアログ。

    一覧から1行選択して「選択」ボタン、またはダブルクリックで即確定する
    （ui.checkable_treeview等、既存のTreeview実装パターンに合わせたシンプルな
    一覧選択UI）。

    戻り値：選択されたcandidatesの要素（辞書）。ユーザーがキャンセル
    （キャンセルボタン／ウインドウを閉じる）した場合はNone。
    """
    dialog = tk.Toplevel(parent)
    dialog.title("ロットNo.の選択")
    dialog.geometry("600x320")
    dialog.transient(parent)
    dialog.grab_set()

    result = {"chosen": None}

    ttk.Label(
        dialog,
        text=(
            f"キッティングリストNo. {kitting_list_no} には複数のロットが該当します。\n"
            "対象のロットを選択してください。"
        ),
        padding=10,
    ).pack(anchor=tk.W)

    tree_frame = ttk.Frame(dialog, padding=(10, 0))
    tree_frame.pack(expand=True, fill=tk.BOTH)

    tree = ttk.Treeview(tree_frame, columns=_COLS, show="headings", selectmode="browse")
    for col in _COLS:
        tree.heading(col, text=_HEADERS[col])
        tree.column(col, width=100, anchor=tk.E if col in _RIGHT_ALIGNED else tk.W)
    tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    for candidate in candidates:
        tree.insert("", tk.END, values=(
            candidate.get("lot_no") or "",
            candidate.get("board_name") or "",
            candidate.get("setup_file_no") or "",
            _format_side(candidate.get("production_side")),
            _format_qty(candidate.get("planned_qty")),
            _format_qty(candidate.get("order_qty")),
        ))

    def confirm(event=None):
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("選択エラー", "ロットNo.を選択してください。", parent=dialog)
            return
        index = tree.index(sel[0])
        result["chosen"] = candidates[index]
        dialog.destroy()

    def cancel():
        result["chosen"] = None
        dialog.destroy()

    tree.bind("<Double-1>", confirm)

    btn_frame = ttk.Frame(dialog, padding=10)
    btn_frame.pack(fill=tk.X)
    ttk.Button(btn_frame, text="選択", command=confirm).pack(side=tk.RIGHT, padx=5)
    ttk.Button(btn_frame, text="キャンセル", command=cancel).pack(side=tk.RIGHT)

    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.wait_window()
    return result["chosen"]
