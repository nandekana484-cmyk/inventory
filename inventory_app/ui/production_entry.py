import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from models.production import get_bom_groups, get_daily_production, upsert_production_record

class ProductionEntryWindow(tk.Toplevel):
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.title("日次生産入力（計画・実績）")
        self.geometry("700x500")

        self.create_widgets()
        self.load_data()

    def create_widgets(self):
        # 上部：日付選択エリア
        top_frame = ttk.LabelFrame(self, text="対象日", padding=10)
        top_frame.pack(fill=tk.X, padx=15, pady=5)

        ttk.Label(top_frame, text="生産日:").pack(side=tk.LEFT, padx=5)
        self.entry_date = ttk.Entry(top_frame, width=15)
        self.entry_date.pack(side=tk.LEFT, padx=5)
        self.entry_date.insert(0, datetime.now().strftime("%Y-%m-%d"))

        btn_load = ttk.Button(top_frame, text="表示更新", command=self.load_data)
        btn_load.pack(side=tk.LEFT, padx=15)

        # 中央：入力エリア
        mid_frame = ttk.LabelFrame(self, text="計画・実績の登録", padding=10)
        mid_frame.pack(fill=tk.X, padx=15, pady=5)

        ttk.Label(mid_frame, text="基板グループ(BOM):").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.combo_group = ttk.Combobox(mid_frame, width=20, state="readonly")
        self.combo_group.grid(row=0, column=1, padx=5, pady=5)
        self.combo_group["values"] = get_bom_groups()

        ttk.Label(mid_frame, text="計画数:").grid(row=0, column=2, sticky=tk.W, padx=(15, 0))
        self.entry_plan = ttk.Entry(mid_frame, width=10)
        self.entry_plan.grid(row=0, column=3, padx=5)
        self.entry_plan.insert(0, "0")

        ttk.Label(mid_frame, text="実績数:").grid(row=0, column=4, sticky=tk.W, padx=(15, 0))
        self.entry_actual = ttk.Entry(mid_frame, width=10)
        self.entry_actual.grid(row=0, column=5, padx=5)
        self.entry_actual.insert(0, "0")

        btn_save = ttk.Button(mid_frame, text="保存 / 更新", command=self.save_record)
        btn_save.grid(row=0, column=6, padx=15)

        # 下部：一覧テーブル
        list_frame = ttk.LabelFrame(self, text="当日の生産状況", padding=10)
        list_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=5)

        cols = ("group_id", "plan_qty", "actual_qty", "progress")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings")
        self.tree.heading("group_id", text="基板グループ")
        self.tree.heading("plan_qty", text="計画数")
        self.tree.heading("actual_qty", text="実績数")
        self.tree.heading("progress", text="達成率")

        self.tree.column("group_id", width=200)
        self.tree.column("plan_qty", width=100, anchor=tk.E)
        self.tree.column("actual_qty", width=100, anchor=tk.E)
        self.tree.column("progress", width=100, anchor=tk.E)

        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_row)

    def load_data(self):
        target_date = self.entry_date.get().strip()
        for item in self.tree.get_children():
            self.tree.delete(item)

        records = get_daily_production(target_date)
        for r in records:
            plan = r["plan_qty"]
            actual = r["qty"]
            progress = f"{(actual / plan * 100):.1f}%" if plan > 0 else "-"
            
            self.tree.insert("", tk.END, values=(
                r["board_group_id"],
                f"{plan:.0f}",
                f"{actual:.0f}",
                progress
            ))

    def on_select_row(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        self.combo_group.set(vals[0])
        self.entry_plan.delete(0, tk.END)
        self.entry_plan.insert(0, vals[1])
        self.entry_actual.delete(0, tk.END)
        self.entry_actual.insert(0, vals[2])

    def save_record(self):
        p_date = self.entry_date.get().strip()
        group_id = self.combo_group.get().strip()
        try:
            plan_qty = float(self.entry_plan.get().strip() or 0)
            actual_qty = float(self.entry_actual.get().strip() or 0)
        except ValueError:
            messagebox.showwarning("入力エラー", "数量には数値を入力してください。")
            return

        if not group_id:
            messagebox.showwarning("入力エラー", "基板グループを選択してください。")
            return

        worker_id = self.current_worker.get("worker_id", "SYSTEM")
        upsert_production_record(p_date, group_id, plan_qty, actual_qty, worker_id)
        
        self.load_data()
        
        # 入力欄のクリア
        self.entry_plan.delete(0, tk.END)
        self.entry_plan.insert(0, "0")
        self.entry_actual.delete(0, tk.END)
        self.entry_actual.insert(0, "0")

