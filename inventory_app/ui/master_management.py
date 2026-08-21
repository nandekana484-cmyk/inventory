import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.master import (
    get_all_parts, upsert_part, delete_part,
    get_all_products, upsert_product, delete_product,
    get_all_boms, insert_bom, delete_bom
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

        # タブ3: BOM定義
        self.tab_bom = ttk.Frame(notebook)
        notebook.add(self.tab_bom, text=" BOM（構成）管理 ")
        self.setup_bom_tab()

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
            messagebox.showwarning("エラー", "部品/リールIDを入力してください。")
            return
        upsert_part(pid, c96, ptype, shelf, "リール")
        self.load_parts()
        messagebox.showinfo("完了", "部品情報を保存しました。")

    def remove_part(self):
        sel = self.tree_parts.selection()
        if not sel:
            return
        pid = self.tree_parts.item(sel[0], "values")[0]
        if messagebox.askyesno("確認", f"部品 {pid} を削除しますか？"):
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
            messagebox.showwarning("エラー", "製品IDを入力してください。")
            return
        upsert_product(pid, pname)
        self.load_products()
        messagebox.showinfo("完了", "製品情報を保存しました。")

    def remove_product(self):
        sel = self.tree_products.selection()
        if not sel:
            return
        pid = self.tree_products.item(sel[0], "values")[0]
        if messagebox.askyesno("確認", f"製品 {pid} を削除しますか？"):
            delete_product(pid)
            self.load_products()

    # --- タブ3: BOM管理 ---
    def setup_bom_tab(self):
        frame_input = ttk.LabelFrame(self.tab_bom, text="BOM（基板グループ使用部品）追加", padding=10)
        frame_input.pack(fill=tk.X, padx=10, pady=5)

        ttk.Label(frame_input, text="グループID:").grid(row=0, column=0, sticky=tk.W)
        self.entry_bom_grp = ttk.Entry(frame_input, width=15)
        self.entry_bom_grp.grid(row=0, column=1, padx=5)

        ttk.Label(frame_input, text="部品コード96:").grid(row=0, column=2, sticky=tk.W)
        self.entry_bom_c96 = ttk.Entry(frame_input, width=15)
        self.entry_bom_c96.grid(row=0, column=3, padx=5)

        ttk.Label(frame_input, text="使用数量:").grid(row=0, column=4, sticky=tk.W)
        self.entry_bom_qty = ttk.Entry(frame_input, width=10)
        self.entry_bom_qty.grid(row=0, column=5, padx=5)

        btn_save = ttk.Button(frame_input, text="追加", command=self.save_bom)
        btn_save.grid(row=0, column=6, padx=10)

        frame_list = ttk.Frame(self.tab_bom, padding=5)
        frame_list.pack(expand=True, fill=tk.BOTH)

        cols = ("bom_id", "group_id", "code96", "usage_qty")
        self.tree_bom = ttk.Treeview(frame_list, columns=cols, show="headings")
        self.tree_bom.heading("bom_id", text="BOM ID")
        self.tree_bom.heading("group_id", text="基板グループID")
        self.tree_bom.heading("code96", text="部品コード96")
        self.tree_bom.heading("usage_qty", text="使用数量")
        self.tree_bom.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        btn_del = ttk.Button(self.tab_bom, text="選択行を削除", command=self.remove_bom)
        btn_del.pack(anchor=tk.E, padx=10, pady=5)

        self.load_boms()

    def load_boms(self):
        for item in self.tree_bom.get_children():
            self.tree_bom.delete(item)
        for b in get_all_boms():
            self.tree_bom.insert("", tk.END, values=(b['bom_id'], b['group_id'], b['code96'], b['usage_qty']))

    def save_bom(self):
        grp = self.entry_bom_grp.get().strip()
        c96 = self.entry_bom_c96.get().strip()
        qty_str = self.entry_bom_qty.get().strip()

        if not grp or not c96 or not qty_str:
            messagebox.showwarning("エラー", "全ての項目を入力してください。")
            return
        try:
            qty = float(qty_str)
        except ValueError:
            messagebox.showwarning("エラー", "使用数量には数値を入力してください。")
            return

        insert_bom(grp, c96, qty)
        self.load_boms()
        messagebox.showinfo("完了", "BOMを追加しました。")

    def remove_bom(self):
        sel = self.tree_bom.selection()
        if not sel:
            return
        bom_id = self.tree_bom.item(sel[0], "values")[0]
        if messagebox.askyesno("確認", f"BOM ID: {bom_id} を削除しますか？"):
            delete_bom(int(bom_id))
            self.load_boms()
