# ui/unmatched_production_window.py
import tkinter as tk
from tkinter import ttk


class UnmatchedProductionWindow(tk.Toplevel):
    """
    実績CSV自動取込（services.production_import_service.import_production_csv）で
    計画（kitting_plan_items）に一致しなかった行を一覧表示する通知ウインドウ。

    最小構成：Treeview（lot_no / product_name / daily_qty / reason）＋閉じるボタンのみ。
    """
    def __init__(self, parent, unmatched_rows):
        super().__init__(parent)
        self.title("実績CSV取込：未一致行一覧")
        self.geometry("620x400")

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("lot_no", "product_name", "daily_qty", "reason")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("lot_no", text="ロットNo")
        self.tree.heading("product_name", text="製品名")
        self.tree.heading("daily_qty", text="実績数")
        self.tree.heading("reason", text="未一致理由")
        self.tree.column("lot_no", width=110, anchor=tk.W)
        self.tree.column("product_name", width=180, anchor=tk.W)
        self.tree.column("daily_qty", width=80, anchor=tk.E)
        self.tree.column("reason", width=230, anchor=tk.W)
        self.tree.pack(expand=True, fill=tk.BOTH)

        for row in unmatched_rows:
            self.tree.insert("", tk.END, values=(
                row.get("lot_no", ""),
                row.get("product_name", ""),
                row.get("daily_qty", ""),
                row.get("reason", ""),
            ))

        ttk.Button(self, text="閉じる", command=self.destroy).pack(pady=10)
