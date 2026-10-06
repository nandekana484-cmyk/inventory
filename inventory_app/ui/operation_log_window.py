# ui/operation_log_window.py
import tkinter as tk
from tkinter import ttk

from models.operation_log import list_operation_log
from models.db_lifecycle_log import list_db_lifecycle_log, OPERATION_TYPE_LABELS
from ui.window_utils import center_window


class OperationLogWindow(tk.Toplevel):
    """
    操作履歴の一覧表示専用ウィンドウ。2026-10-06、タブ構成に変更した：

    - 「月次DBの操作履歴」タブ：以前からのoperation_log（月次DB内、
      config.DB_PATH切り替えの対象）。DBを切り替えるたびに表示内容も
      切り替わる（従来通り、変更なし）。
    - 「データベースの作成・削除履歴」タブ：新設のdb_lifecycle_log
      （master.db、models.db_lifecycle_log参照）。DB新規作成・削除・
      バックアップ呼び出しの履歴で、どの月次DBを開いていても同じ内容が
      見える（削除済みDBの記録も残る）。

    2つのタブを明確に分け、どちらを見ているか混同しないようにする
    （タブの見出し自体に「月次DBの」「データベースの作成・削除」と
    対象の違いを明記する）。

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

    LIFECYCLE_COLUMNS = ("timestamp", "operation_type", "target_db_folder", "source_info", "worker_name")
    LIFECYCLE_HEADERS = {
        "timestamp": "日時",
        "operation_type": "操作の種類",
        "target_db_folder": "対象のDBフォルダ名",
        "source_info": "引き継ぎ元／呼び出し元",
        "worker_name": "操作した作業者",
    }
    LIFECYCLE_WIDTHS = {
        "timestamp": 150,
        "operation_type": 200,
        "target_db_folder": 160,
        "source_info": 320,
        "worker_name": 120,
    }

    def __init__(self, parent):
        super().__init__(parent)
        self.title("操作履歴")
        self.geometry("1050x600")
        center_window(self, parent)

        self._sort_reverse = {}
        self._lifecycle_sort_reverse = {}

        notebook = ttk.Notebook(self)
        notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

        tab_operation_log = ttk.Frame(notebook)
        tab_lifecycle_log = ttk.Frame(notebook)
        notebook.add(tab_operation_log, text="月次DBの操作履歴")
        notebook.add(tab_lifecycle_log, text="データベースの作成・削除履歴")

        self._create_operation_log_tab(tab_operation_log)
        self._create_lifecycle_log_tab(tab_lifecycle_log)

        self.load_log()
        self.load_lifecycle_log()

    def _create_operation_log_tab(self, frame):
        top_frame = ttk.Frame(frame, padding=(0, 0, 0, 8))
        top_frame.pack(fill=tk.X)
        ttk.Button(top_frame, text="更新", command=self.load_log).pack(side=tk.LEFT)
        ttk.Label(
            top_frame,
            text="現在開いている月次DB内の履歴です。DBを切り替えると表示内容も切り替わります。",
            foreground="gray",
        ).pack(side=tk.LEFT, padx=(10, 0))

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

    def _create_lifecycle_log_tab(self, frame):
        top_frame = ttk.Frame(frame, padding=(0, 0, 0, 8))
        top_frame.pack(fill=tk.X)
        ttk.Button(top_frame, text="更新", command=self.load_lifecycle_log).pack(side=tk.LEFT)
        ttk.Label(
            top_frame,
            text="どの月次DBを開いていても同じ内容が見えます（削除済みDBの記録も残ります）。",
            foreground="gray",
        ).pack(side=tk.LEFT, padx=(10, 0))

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.tree_lifecycle = ttk.Treeview(tree_frame, columns=self.LIFECYCLE_COLUMNS, show="headings")
        for col in self.LIFECYCLE_COLUMNS:
            self.tree_lifecycle.heading(
                col, text=self.LIFECYCLE_HEADERS[col],
                command=lambda c=col: self.sort_lifecycle_by_column(c),
            )
            self.tree_lifecycle.column(col, width=self.LIFECYCLE_WIDTHS[col], anchor=tk.W)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_lifecycle.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree_lifecycle.xview)
        self.tree_lifecycle.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree_lifecycle.pack(expand=True, fill=tk.BOTH)

    def load_log(self):
        """DBから履歴を再取得し、list_operation_log()の戻り順（新しい順）のまま表示する。"""
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in list_operation_log():
            self.tree.insert("", tk.END, values=(
                rec["timestamp"], rec["worker_name"], rec["pc_name"],
                rec["operation_name"], rec["detail"] or "",
            ))

    def load_lifecycle_log(self):
        """master.dbからDB作成・削除履歴を再取得し、新しい順のまま表示する。"""
        for item in self.tree_lifecycle.get_children():
            self.tree_lifecycle.delete(item)
        for rec in list_db_lifecycle_log():
            operation_type_label = OPERATION_TYPE_LABELS.get(rec["operation_type"], rec["operation_type"])
            self.tree_lifecycle.insert("", tk.END, values=(
                rec["timestamp"], operation_type_label, rec["target_db_folder"],
                rec["source_info"] or "", rec["worker_name"],
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

    def sort_lifecycle_by_column(self, col):
        """sort_by_column()と同じ、データベース作成・削除履歴タブ向けの簡易ソート。"""
        reverse = self._lifecycle_sort_reverse.get(col, False)
        rows = [(self.tree_lifecycle.set(iid, col), iid) for iid in self.tree_lifecycle.get_children("")]
        rows.sort(key=lambda x: x[0], reverse=reverse)
        for index, (_, iid) in enumerate(rows):
            self.tree_lifecycle.move(iid, "", index)
        self._lifecycle_sort_reverse[col] = not reverse
