# ui/operation_log_window.py
import tkinter as tk
from tkinter import ttk

from models.operation_log import list_operation_log


class OperationLogWindow(tk.Toplevel):
    """
    operation_log（月次DB内の大まかな操作履歴）の一覧表示専用ウィンドウ。

    設計判断：ui.kitting_production_entry.py の計画一覧・ui.ng_input_window.py の
    NG一覧のような、チェックボックス式ポップアップ絞り込み＋テキスト絞り込みの
    機構は導入せず、シンプルな一覧表示（列ヘッダークリックでの簡易ソートのみ）に
    留めた。理由：本画面は「大まかな操作履歴を後から見返す」ための閲覧専用画面で
    あり、データ入力作業のための一覧（絞り込みながら特定の行を探して編集する）とは
    用途が異なるため、まずはシンプルな実装から始め、実運用で件数が増え絞り込みが
    必要になった段階で拡張する方針とした。
    """
    COLUMNS = ("timestamp", "worker_name", "pc_name", "operation_name", "detail")
    HEADERS = {
        "timestamp": "日時",
        "worker_name": "作業者",
        "pc_name": "PC名",
        "operation_name": "操作内容",
        "detail": "補足",
    }
    WIDTHS = {
        "timestamp": 150,
        "worker_name": 120,
        "pc_name": 150,
        "operation_name": 220,
        "detail": 320,
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.title("操作履歴")
        self.geometry("1050x600")

        self._sort_reverse = {}

        frame = ttk.Frame(self, padding=10)
        frame.pack(expand=True, fill=tk.BOTH)

        top_frame = ttk.Frame(frame)
        top_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(top_frame, text="更新", command=self.load_log).pack(side=tk.LEFT)

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.tree = ttk.Treeview(tree_frame, columns=self.COLUMNS, show="headings")
        for col in self.COLUMNS:
            self.tree.heading(
                col, text=self.HEADERS[col],
                command=lambda c=col: self.sort_by_column(c),
            )
            self.tree.column(col, width=self.WIDTHS[col], anchor=tk.W)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(expand=True, fill=tk.BOTH)

        self.load_log()

    def load_log(self):
        """DBから履歴を再取得し、list_operation_log()の戻り順（新しい順）のまま表示する。"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in list_operation_log():
            self.tree.insert("", tk.END, values=(
                rec["timestamp"], rec["worker_name"], rec["pc_name"],
                rec["operation_name"], rec["detail"] or "",
            ))

    def sort_by_column(self, col):
        """
        列ヘッダークリックでの簡易ソート（ui.kitting_plan_import.KittingPlanImportWindow.
        _sort_treeview_column()と同じ、文字列比較による素朴なソート。押すたびに
        昇順・降順をトグルする）。
        """
        reverse = self._sort_reverse.get(col, False)
        rows = [(self.tree.set(iid, col), iid) for iid in self.tree.get_children("")]
        rows.sort(key=lambda x: x[0], reverse=reverse)
        for index, (_, iid) in enumerate(rows):
            self.tree.move(iid, "", index)
        self._sort_reverse[col] = not reverse
