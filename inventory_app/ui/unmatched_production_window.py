# ui/unmatched_production_window.py
import tkinter as tk
from tkinter import ttk


class UnmatchedProductionWindow(tk.Toplevel):
    """
    実績CSV自動取込（services.production_import_service.import_production_csv）で
    計画（kitting_plan_items）に一致しなかった行、または登録処理でエラーになった行を
    一覧表示する通知ウインドウ。

    最小構成：Treeview（lot_no / product_name / report_date / worker_id / daily_qty / reason）
    ＋閉じるボタンのみ。
    title / reason_key / reason_label を指定すると、未一致行一覧（unmatched）と
    同じ表示形式のまま、エラー行一覧（errors。理由キーは "error"）にも流用できる。
    report_date・worker_id は、渡された行データに存在しない場合は空欄表示となる。
    """
    def __init__(self, parent, unmatched_rows, title="実績CSV取込：未一致行一覧",
                 reason_key="reason", reason_label="未一致理由"):
        super().__init__(parent)
        self.title(title)
        self.geometry("760x400")

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("lot_no", "product_name", "report_date", "worker_id", "daily_qty", "reason")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("lot_no", text="ロットNo")
        self.tree.heading("product_name", text="製品名")
        self.tree.heading("report_date", text="実績日")
        self.tree.heading("worker_id", text="作業者")
        self.tree.heading("daily_qty", text="実績数")
        self.tree.heading("reason", text=reason_label)
        self.tree.column("lot_no", width=100, anchor=tk.W)
        self.tree.column("product_name", width=160, anchor=tk.W)
        self.tree.column("report_date", width=90, anchor=tk.CENTER)
        self.tree.column("worker_id", width=90, anchor=tk.W)
        self.tree.column("daily_qty", width=70, anchor=tk.E)
        self.tree.column("reason", width=200, anchor=tk.W)
        self.tree.pack(expand=True, fill=tk.BOTH)

        for row in unmatched_rows:
            self.tree.insert("", tk.END, values=(
                row.get("lot_no", ""),
                row.get("product_name", ""),
                row.get("report_date", "") or "",
                row.get("worker_id", ""),
                row.get("daily_qty", ""),
                row.get(reason_key, ""),
            ))

        ttk.Button(self, text="閉じる", command=self.destroy).pack(pady=10)
