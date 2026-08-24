import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.master import (
    get_all_parts, upsert_part, delete_part,
    get_all_products, upsert_product, delete_product,
)

class MasterManagementWindow(tk.Toplevel):
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.title("マスターデータ管理")
        self.geometry("750x550")

        notebook = ttk.Notebook(self)
        notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

        # タブ1: 部品マスタ
        self.tab_parts = ttk.Frame(notebook)
        notebook.add(self.tab_parts, text=" 部品マスタ ")
        self.setup_parts_tab()

        # タブ2: 完成品マスタ
        self.tab_products = ttk.Frame(notebook)
        notebook.add(self.tab_products, text=" 完成品マスタ ")
        self.setup_products_tab()

    # --- タブ1: 部品マスタ ---
    def setup_parts_tab(self):
        frame_input = ttk.LabelFrame(self.tab_parts, text="部品情報の登録・更新", padding=10)
        frame_input.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(frame_input, text="部品/リールID:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.entry_p_id = ttk.Entry(frame_input, width=15)
        self.entry_p_id.grid(row=0, column=1, sticky=tk.W, pady=2, padx=5)

        ttk.Label(frame_input, text="部品コード96:").grid(row=0, column=2, sticky=tk.W, pady=2)
        self.entry_p_c96 = ttk.Entry(frame_input, width=15)
        self.entry_p_c96.grid(row=0, column=3, sticky=tk.W, pady=2, padx=5)

        ttk.Label(frame_input, text="種別 (RESISTOR等):").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.entry_p_type = ttk.Entry(frame_input, width=15)
        self.entry_p_type.grid(row=1, column=1, sticky=tk.W, pady=2, padx=5)

        ttk.Label(frame_input, text="棚番:").grid(row=1, column=2, sticky=tk.W, pady=2)
        self.entry_p_shelf = ttk.Entry(frame_input, width=15)
        self.entry_p_shelf.grid(row=1, column=3, sticky=tk.W, pady=2, padx=5)

        btn_save = ttk.Button(frame_input, text="保存 / 更新", command=self.save_part)
        btn_save.grid(row=1, column=4, padx=10)

        # テーブル
        frame_list = ttk.Frame(self.tab_parts, padding=5)
        frame_list.pack(expand=True, fill=tk.BOTH)

        cols = ("part_id", "code96", "part_type", "shelf")
        self.tree_parts = ttk.Treeview(frame_list, columns=cols, show="headings")
        self.tree_parts.heading("part_id", text="部品/リールID")
        self.tree_parts.heading("code96", text="部品コード96")
        self.tree_parts.heading("part_type", text="種別")
        self.tree_parts.heading("shelf", text="棚番")
        self.tree_parts.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        btn_del = ttk.Button(self.tab_parts, text="選択行を削除", command=self.remove_part)
        btn_del.pack(anchor=tk.E, padx=10, pady=5)

        self.load_parts()

    def load_parts(self):
        for item in self.tree_parts.get_children():
            self.tree_parts.delete(item)
        for p in get_all_parts():
            self.tree_parts.insert("", tk.END, values=(p['part_id'], p['code96'], p['part_type'], p['shelf_type']))

    def save_part(self):
        pid = self.entry_p_id.get().strip()
        c96 = self.entry_p_c96.get().strip()
        ptype = self.entry_p_type.get().strip()
        shelf = self.entry_p_shelf.get().strip()
        if not pid:
            messagebox.showwarning("エラー", "部品/リールIDを入力してください。", parent=self.winfo_toplevel())
            return
        upsert_part(pid, c96, ptype, shelf, "リール")
        self.load_parts()
        messagebox.showinfo("完了", "部品情報を保存しました。", parent=self.winfo_toplevel())

    def remove_part(self):
        sel = self.tree_parts.selection()
        if not sel:
            return
        pid = self.tree_parts.item(sel[0], "values")[0]
        if messagebox.askyesno("確認", f"部品 {pid} を削除しますか？", parent=self.winfo_toplevel()):
            delete_part(pid)
            self.load_parts()

    # --- タブ2: 完成品マスタ ---
    def setup_products_tab(self):
        frame_input = ttk.LabelFrame(self.tab_products, text="完成品モデルの登録・更新", padding=10)
        frame_input.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(frame_input, text="製品ID:").grid(row=0, column=0, sticky=tk.W)
        self.entry_prod_id = ttk.Entry(frame_input, width=15)
        self.entry_prod_id.grid(row=0, column=1, padx=5)

        ttk.Label(frame_input, text="製品名称:").grid(row=0, column=2, sticky=tk.W)
        self.entry_prod_name = ttk.Entry(frame_input, width=25)
        self.entry_prod_name.grid(row=0, column=3, padx=5)

        btn_save = ttk.Button(frame_input, text="保存 / 更新", command=self.save_product)
        btn_save.grid(row=0, column=4, padx=10)

        frame_list = ttk.Frame(self.tab_products, padding=5)
        frame_list.pack(expand=True, fill=tk.BOTH)

        cols = ("product_id", "product_name")
        self.tree_products = ttk.Treeview(frame_list, columns=cols, show="headings")
        self.tree_products.heading("product_id", text="製品ID")
        self.tree_products.heading("product_name", text="製品名称")
        self.tree_products.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        btn_del = ttk.Button(self.tab_products, text="選択行を削除", command=self.remove_product)
        btn_del.pack(anchor=tk.E, padx=10, pady=5)

        self.load_products()

    def load_products(self):
        for item in self.tree_products.get_children():
            self.tree_products.delete(item)
        for p in get_all_products():
            self.tree_products.insert("", tk.END, values=(p['product_id'], p['product_name']))

    def save_product(self):
        pid = self.entry_prod_id.get().strip()
        pname = self.entry_prod_name.get().strip()
        if not pid:
            messagebox.showwarning("エラー", "製品IDを入力してください。", parent=self.winfo_toplevel())
            return
        upsert_product(pid, pname)
        self.load_products()
        messagebox.showinfo("完了", "製品情報を保存しました。", parent=self.winfo_toplevel())

    def remove_product(self):
        sel = self.tree_products.selection()
        if not sel:
            return
        pid = self.tree_products.item(sel[0], "values")[0]
        if messagebox.askyesno("確認", f"製品 {pid} を削除しますか？", parent=self.winfo_toplevel()):
            delete_product(pid)
            self.load_products()
