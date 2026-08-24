# ui/ng_input_window.py
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

from services.production_service import search_plan_by_kitting_no
from services.bom_service import BOMService
from models.scrap_records import save_scrap_record

_bom_service = BOMService()


class NgInputWindow(tk.Toplevel):
    """
    NG（仕損）実績入力画面。

    入力フロー：
      1. キッティングリストNo.を入力
      2. NG数量（枚数）を入力
      3. 「展開」ボタン → 計画からfile_no・生産面を特定し、
         BOMService.expand_scrap_to_parts() で新BOM（TSV由来）を展開
      4. 使用部品一覧（96コードごとの消費数量）をTreeviewに表示
      5. 実際に仕損とする部品を選択（複数選択可）
      6. 「仕損登録」ボタン → 選択行のみ models.scrap_records.save_scrap_record() で保存
      7. 保存済みのscrap_recordsは services.inventory_diff_service 側で
         96コード単位に集計され、在庫差異レポートへ反映される
    """
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.current_plan = None  # {"kitting_list_no", "file_no", "side", "ng_qty"}

        self.title("NG（仕損）入力")
        self.geometry("720x600")

        self.create_widgets()

    def create_widgets(self):
        search_frame = ttk.LabelFrame(self, text="対象計画・NG数量", padding=10)
        search_frame.pack(fill=tk.X, padx=15, pady=10)

        ttk.Label(search_frame, text="キッティングリストNo.：").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.entry_kitting_no = ttk.Entry(search_frame, width=20)
        self.entry_kitting_no.grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)

        ttk.Label(search_frame, text="NG数量（枚数）：").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.entry_ng_qty = ttk.Entry(search_frame, width=20)
        self.entry_ng_qty.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)

        self.btn_expand = ttk.Button(search_frame, text="展開", command=self.on_expand)
        self.btn_expand.grid(row=0, column=2, rowspan=2, padx=15)

        self.lbl_plan_info = ttk.Label(search_frame, text="-", foreground="blue")
        self.lbl_plan_info.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))

        parts_frame = ttk.LabelFrame(self, text="使用部品一覧（仕損とする部品を選択・複数選択可）", padding=10)
        parts_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=(0, 10))

        cols = ("part_no", "qty_per_product", "consumed_qty")
        self.tree = ttk.Treeview(parts_frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("part_no", text="96コード")
        self.tree.heading("qty_per_product", text="1台あたり数量")
        self.tree.heading("consumed_qty", text="消費数量（NG数×員数）")
        self.tree.column("part_no", width=220, anchor=tk.W)
        self.tree.column("qty_per_product", width=140, anchor=tk.E)
        self.tree.column("consumed_qty", width=180, anchor=tk.E)

        vsb = ttk.Scrollbar(parts_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        self.btn_register = ttk.Button(btn_frame, text="仕損登録", command=self.on_register, state=tk.DISABLED)
        self.btn_register.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

    def on_expand(self):
        kitting_no = self.entry_kitting_no.get().strip()
        if not kitting_no:
            messagebox.showwarning("入力エラー", "キッティングリストNo.を入力してください。")
            return

        try:
            ng_qty = float(self.entry_ng_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "NG数量には数値を入力してください。")
            return

        if ng_qty <= 0:
            messagebox.showwarning("入力エラー", "NG数量には0より大きい数値を入力してください。")
            return

        plan = search_plan_by_kitting_no(kitting_no)
        if not plan:
            messagebox.showerror("検索エラー", f"キッティングリストNo. {kitting_no} の計画が見つかりません。")
            self.current_plan = None
            self.btn_register.config(state=tk.DISABLED)
            return

        file_no = plan["setup_file_no"]
        try:
            side = int(plan["production_side"])
        except (TypeError, ValueError):
            messagebox.showerror(
                "エラー",
                f"生産面（production_side）を数値として解釈できません: {plan['production_side']!r}",
            )
            return
        if side not in (1, 2):
            messagebox.showerror("エラー", f"生産面（production_side）は1または2である必要があります（値: {side}）。")
            return

        try:
            parts = _bom_service.expand_scrap_to_parts({
                "setup_file_no": file_no,
                "production_side": side,
                "ng_qty": ng_qty,
                "lot_no": plan.get("lot_no"),
            })
        except FileNotFoundError as e:
            messagebox.showerror("BOMエラー", f"BOM TSVが見つかりません：\n{e}")
            return
        except ValueError as e:
            messagebox.showerror("BOMエラー", f"BOM展開に失敗しました：\n{e}")
            return

        self.current_plan = {
            "kitting_list_no": kitting_no,
            "file_no": file_no,
            "side": side,
            "ng_qty": ng_qty,
        }
        self.lbl_plan_info.config(
            text=f"file_no: {file_no} / 生産面: {side} / ロットNo: {plan.get('lot_no', '-')}"
        )

        self.load_parts_tree(parts, ng_qty)

        if not parts:
            messagebox.showwarning(
                "警告",
                f"file_no「{file_no}」・生産面{side}のBOMが登録されていない、または対象部品がありません。",
            )
        self.btn_register.config(state=tk.NORMAL if parts else tk.DISABLED)

    def load_parts_tree(self, parts, ng_qty):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for part in parts:
            qty_per_product = (part["qty"] / ng_qty) if ng_qty else 0
            self.tree.insert("", tk.END, values=(
                part["part_no"], f"{qty_per_product:g}", f"{part['qty']:g}",
            ))

    def on_register(self):
        if not self.current_plan:
            return

        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("入力エラー", "仕損として登録する部品を選択してください。")
            return

        report_date = datetime.now().strftime("%Y-%m-%d")
        kitting_list_no = self.current_plan["kitting_list_no"]
        file_no = self.current_plan["file_no"]
        side = self.current_plan["side"]

        registered = 0
        for iid in sel:
            values = self.tree.item(iid, "values")
            part_no = values[0]
            try:
                consumed_qty = float(values[2])
            except ValueError:
                continue
            save_scrap_record(kitting_list_no, file_no, side, part_no, consumed_qty, report_date)
            registered += 1

        messagebox.showinfo("登録完了", f"{registered}件の仕損実績を登録しました（{report_date}）。")
