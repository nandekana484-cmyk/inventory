import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.physical_count import get_all_parts_for_count, get_counts_by_date, save_physical_count

class PhysicalCountWindow(tk.Toplevel):
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.title("バーコード実地カウント（棚卸）")
        self.geometry("800x600")

        self.parts_list = []
        self.count_data = {}

        self.create_widgets()
        self.load_data()

    def create_widgets(self):
        # 上部設定・検索エリア
        top_frame = ttk.LabelFrame(self, text="カウント設定・バーコード検索（補助）", padding=10)
        top_frame.pack(fill=tk.X, padx=15, pady=10)

        ttk.Label(top_frame, text="棚卸日:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.entry_date = ttk.Entry(top_frame, width=12)
        self.entry_date.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        self.entry_date.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ttk.Label(top_frame, text="バーコード読み取り (part_id / code96):").grid(row=0, column=2, sticky=tk.W, padx=(20, 5), pady=5)
        self.entry_barcode = ttk.Entry(top_frame, width=25)
        self.entry_barcode.grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)
        self.entry_barcode.bind("<Return>", self.on_barcode_scan)

        btn_search = ttk.Button(top_frame, text="検索・選択", command=self.on_barcode_scan)
        btn_search.grid(row=0, column=4, padx=5, pady=5)

        # 中央メインテーブル
        list_frame = ttk.LabelFrame(self, text="対象部品一覧（リストからの直接手動選択・入力も可能）", padding=10)
        list_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=5)

        columns = ("status", "part_id", "code96", "shelf", "qty", "worker")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", height=12)

        self.tree.heading("status", text="状態")
        self.tree.heading("part_id", text="部品/リールID")
        self.tree.heading("code96", text="部品コード96")
        self.tree.heading("shelf", text="棚番")
        self.tree.heading("qty", text="実地カウント数")
        self.tree.heading("worker", text="作業者")

        self.tree.column("status", width=80, anchor=tk.CENTER)
        self.tree.column("part_id", width=140)
        self.tree.column("code96", width=140)
        self.tree.column("shelf", width=80, anchor=tk.CENTER)
        self.tree.column("qty", width=100, anchor=tk.E)
        self.tree.column("worker", width=100, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        # 下部操作エリア
        bottom_frame = ttk.LabelFrame(self, text="カウント入力・更新", padding=10)
        bottom_frame.pack(fill=tk.X, padx=15, pady=10)

        ttk.Label(bottom_frame, text="選択部品ID:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.lbl_selected_part = ttk.Label(bottom_frame, text="未選択", font=("Helvetica", 10, "bold"))
        self.lbl_selected_part.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)

        ttk.Label(bottom_frame, text="数量:").grid(row=0, column=2, sticky=tk.W, padx=(20, 5), pady=5)
        self.entry_qty = ttk.Entry(bottom_frame, width=12)
        self.entry_qty.grid(row=0, column=3, sticky=tk.W, padx=5, pady=5)

        btn_save = ttk.Button(bottom_frame, text="チェック・保存", command=self.on_save_count)
        btn_save.grid(row=0, column=4, padx=(20, 5), pady=5)

    def load_data(self):
        count_date = self.entry_date.get().strip()
        self.parts_list = get_all_parts_for_count()
        self.count_data = get_counts_by_date(count_date)
        self.refresh_table()

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for p in self.parts_list:
            part_id = p['part_id']
            code96 = p['code96'] or ""
            shelf = p['shelf_type'] or ""
            
            c_info = self.count_data.get(part_id, {})
            is_checked = c_info.get('is_checked', 0)
            status_str = "済" if is_checked == 1 else "未"
            qty_str = f"{c_info.get('counted_qty', 0):.0f}" if is_checked == 1 else "-"
            worker_str = c_info.get('worker_id', "-")

            item_id = self.tree.insert("", tk.END, values=(
                status_str, part_id, code96, shelf, qty_str, worker_str
            ))
            
            # メモリ用タグラベル
            self.tree.item(item_id, tags=(part_id,))

    def on_barcode_scan(self, event=None):
        query = self.entry_barcode.get().strip()
        if not query:
            return

        # リストから一致するものを検索 (part_id または code96)
        matched_item = None
        for child in self.tree.get_children():
            vals = self.tree.item(child, "values")
            # vals[1] = part_id, vals[2] = code96
            if query == vals[1] or query == vals[2]:
                matched_item = child
                break

        if matched_item:
            self.tree.selection_set(matched_item)
            self.tree.see(matched_item)
            self.entry_barcode.delete(0, tk.END)
            self.entry_qty.focus()
        else:
            messagebox.showwarning("未発見", f"該当するバーコード ({query}) がリストに見つかりません。")

    def on_tree_select(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        vals = self.tree.item(selected[0], "values")
        part_id = vals[1]
        self.lbl_selected_part.config(text=part_id)

        c_info = self.count_data.get(part_id, {})
        current_qty = c_info.get('counted_qty', 0)
        self.entry_qty.delete(0, tk.END)
        self.entry_qty.insert(0, str(int(current_qty)))

    def on_save_count(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("警告", "対象の部品をリストまたはバーコードで選択してください。")
            return

        vals = self.tree.item(selected[0], "values")
        part_id = vals[1]
        code96 = vals[2]
        count_date = self.entry_date.get().strip()
        qty_str = self.entry_qty.get().strip()

        try:
            qty = float(qty_str)
            if qty < 0:
                raise ValueError()
        except ValueError:
            messagebox.showwarning("入力エラー", "数量には0以上の数値を入力してください。")
            return

        worker_id = self.current_worker.get('worker_id', 'SYSTEM')
        
        # 1 = 修正・確認済み(済)として保存
        save_physical_count(count_date, part_id, code96, qty, is_checked=1, worker_id=worker_id)
        
        self.load_data()
        self.lbl_selected_part.config(text="未選択")
        self.entry_qty.delete(0, tk.END)
        self.entry_barcode.focus()
