import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.inventory import get_inventory_reconciliation

class ReconciliationWindow(tk.Toplevel):
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.title("在庫照合・棚卸差分集計")
        self.geometry("900x550")

        # ツールバー領域
        toolbar = ttk.Frame(self, padding=10)
        toolbar.pack(fill=tk.X)

        btn_refresh = ttk.Button(toolbar, text="最新情報に更新", command=self.load_data)
        btn_refresh.pack(side=tk.LEFT, padx=5)

        self.lbl_summary = ttk.Label(toolbar, text="", font=("Helvetica", 10, "bold"))
        self.lbl_summary.pack(side=tk.RIGHT, padx=10)

        # テーブル表示領域
        frame_list = ttk.Frame(self, padding=10)
        frame_list.pack(expand=True, fill=tk.BOTH)

        cols = ("code96", "part_type", "shelf", "snap_qty", "used_qty", "theoretical_qty", "counted_qty", "diff_qty")
        self.tree = ttk.Treeview(frame_list, columns=cols, show="headings")

        self.tree.heading("code96", text="部品コード96")
        self.tree.heading("part_type", text="種別")
        self.tree.heading("shelf", text="棚番")
        self.tree.heading("snap_qty", text="基準在庫(Snap)")
        self.tree.heading("used_qty", text="消費数量")
        self.tree.heading("theoretical_qty", text="理論在庫")
        self.tree.heading("counted_qty", text="実地カウント")
        self.tree.heading("diff_qty", text="棚卸差分")

        self.tree.column("code96", width=120, anchor=tk.CENTER)
        self.tree.column("part_type", width=90, anchor=tk.CENTER)
        self.tree.column("shelf", width=80, anchor=tk.CENTER)
        self.tree.column("snap_qty", width=100, anchor=tk.E)
        self.tree.column("used_qty", width=90, anchor=tk.E)
        self.tree.column("theoretical_qty", width=100, anchor=tk.E)
        self.tree.column("counted_qty", width=100, anchor=tk.E)
        self.tree.column("diff_qty", width=100, anchor=tk.E)

        # スクロールバー設定
        scrollbar = ttk.Scrollbar(frame_list, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        # タグによる色分け設定（差異がある行を目立たせる）
        self.tree.tag_configure("diff_negative", background="#FFD2D2")
        self.tree.tag_configure("diff_positive", background="#E2F0D9")

        self.load_data()

    def load_data(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        rows = get_inventory_reconciliation()
        diff_count = 0

        for r in rows:
            diff = r['diff_qty']
            tag = ""
            if diff < 0:
                tag = "diff_negative"
                diff_count += 1
            elif diff > 0:
                tag = "diff_positive"
                diff_count += 1

            self.tree.insert("", tk.END, values=(
                r['code96'],
                r['part_type'],
                r['shelf_type'],
                f"{r['snap_qty']:,}",
                f"{r['used_qty']:,}",
                f"{r['theoretical_qty']:,}",
                f"{r['counted_qty']:,}",
                f"{r['diff_qty']:,}"
            ), tags=(tag,))

        self.lbl_summary.config(text=f"総品目数: {len(rows)} 件 | 差分検知品目: {diff_count} 件")
