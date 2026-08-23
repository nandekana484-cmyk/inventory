# ui/kitting_production_entry.py
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from services.production_service import (
    search_plan_by_kitting_no,
    register_daily_result,
    get_daily_history,
    calculate_lot_completion,
    update_daily_result,
    delete_daily_result,
)
from services.production_import_service import import_production_csv
from models.kitting_plan import list_active_plan_items
from models.production import get_app_cumulative_qty
from ui.daily_report_window import DailyReportWindow
from ui.monthly_report_window import MonthlyReportWindow
from ui.unmatched_production_window import UnmatchedProductionWindow


class KittingProductionEntryWindow(tk.Toplevel):
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.current_plan = None
        self.plan_sort_states = {}

        self.title("生産実績入力（キッティングリストNo.）")
        self.geometry("1150x600")

        self.create_widgets()

    def create_widgets(self):
        container = ttk.Frame(self)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Frame(container)
        left_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        right_frame = ttk.Labelframe(container, text="計画一覧", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 15), pady=5)

        # 検索エリア
        search_frame = ttk.LabelFrame(left_frame, text="キッティングリストNo.検索", padding=10)
        search_frame.pack(fill=tk.X, padx=15, pady=5)

        ttk.Label(search_frame, text="キッティングリストNo.:").pack(side=tk.LEFT, padx=5)
        self.entry_kitting_no = ttk.Entry(search_frame, width=20)
        self.entry_kitting_no.pack(side=tk.LEFT, padx=5)
        self.entry_kitting_no.bind("<Return>", lambda e: self.search_plan())

        ttk.Button(search_frame, text="検索", command=self.search_plan).pack(side=tk.LEFT, padx=5)

        # 計画情報表示エリア
        info_frame = ttk.LabelFrame(left_frame, text="計画情報", padding=10)
        info_frame.pack(fill=tk.X, padx=15, pady=5)

        self.lbl_lot = self._add_info_row(info_frame, "ロットNo.：", 0)
        self.lbl_setup = self._add_info_row(info_frame, "セットアップファイルNo.（基板名）：", 1)
        self.lbl_side = self._add_info_row(info_frame, "生産面：", 2)
        self.lbl_plan_qty = self._add_info_row(info_frame, "今回計画数：", 3)
        self.lbl_ext_cum = self._add_info_row(info_frame, "外部システム累計：", 4)
        self.lbl_app_cum = self._add_info_row(info_frame, "アプリ入力累計：", 5)
        self.lbl_lot_completed = self._add_info_row(info_frame, "ロット完成数：", 6)
        self.lbl_lot_remaining = self._add_info_row(info_frame, "ロット未完成数：", 7)
        self.lbl_lot_file_actuals = self._add_info_row(info_frame, "基板別実績（file_no）：", 8)
        self.lbl_lot_surplus = self._add_info_row(info_frame, "余剰基板（file_no）：", 9)

        # 実績入力エリア
        entry_frame = ttk.LabelFrame(left_frame, text="本日の生産実績", padding=10)
        entry_frame.pack(fill=tk.X, padx=15, pady=5)

        ttk.Label(entry_frame, text="本日生産実績：").pack(side=tk.LEFT, padx=5)
        self.entry_daily_qty = ttk.Entry(entry_frame, width=10)
        self.entry_daily_qty.pack(side=tk.LEFT, padx=5)

        self.btn_register = ttk.Button(entry_frame, text="登録", command=self.register_result,
                                        state=tk.DISABLED)
        self.btn_register.pack(side=tk.LEFT, padx=15)

        self.btn_correction = ttk.Button(entry_frame, text="実績修正", command=self.open_correction_window,
                                          state=tk.DISABLED)
        self.btn_correction.pack(side=tk.LEFT, padx=5)

        # 履歴表示エリア
        hist_frame = ttk.LabelFrame(left_frame, text="日次実績履歴", padding=10)
        hist_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=5)

        cols = ("report_date", "daily_qty", "worker_id")
        self.tree = ttk.Treeview(hist_frame, columns=cols, show="headings")
        self.tree.heading("report_date", text="日付")
        self.tree.heading("daily_qty", text="当日実績")
        self.tree.heading("worker_id", text="作業者")
        self.tree.column("report_date", width=150)
        self.tree.column("daily_qty", width=100, anchor=tk.E)
        self.tree.column("worker_id", width=150)
        self.tree.pack(expand=True, fill=tk.BOTH)

        report_btn_frame = ttk.Frame(left_frame)
        report_btn_frame.pack(fill=tk.X, padx=15, pady=(0, 10))

        self.btn_daily_report = ttk.Button(report_btn_frame, text="日報出力", command=self.open_daily_report)
        self.btn_daily_report.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.btn_monthly_report = ttk.Button(report_btn_frame, text="月報出力", command=self.open_monthly_report)
        self.btn_monthly_report.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 5))

        self.btn_production_csv_import = ttk.Button(
            report_btn_frame, text="実績CSV取込", command=self.on_production_csv_import
        )
        self.btn_production_csv_import.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0))

        # 計画一覧エリア（右側）
        cols_plan = ("list_no", "lot_no", "file_no", "board_name", "order_qty", "actual_qty", "diff",
                     "lot_completed", "lot_remaining")
        self.tree_plan_list = ttk.Treeview(right_frame, columns=cols_plan, show="headings")
        self.tree_plan_list.heading("list_no", text="キッティングNo.",
                                     command=lambda c="list_no": self.sort_plan_list(c))
        self.tree_plan_list.heading("lot_no", text="ロットNo.",
                                     command=lambda c="lot_no": self.sort_plan_list(c))
        self.tree_plan_list.heading("file_no", text="file_no",
                                     command=lambda c="file_no": self.sort_plan_list(c))
        self.tree_plan_list.heading("board_name", text="基板名",
                                     command=lambda c="board_name": self.sort_plan_list(c))
        self.tree_plan_list.heading("order_qty", text="発注数",
                                     command=lambda c="order_qty": self.sort_plan_list(c))
        self.tree_plan_list.heading("actual_qty", text="実績累計",
                                     command=lambda c="actual_qty": self.sort_plan_list(c))
        self.tree_plan_list.heading("diff", text="差分",
                                     command=lambda c="diff": self.sort_plan_list(c))
        self.tree_plan_list.heading("lot_completed", text="ロット完成数",
                                     command=lambda c="lot_completed": self.sort_plan_list(c))
        self.tree_plan_list.heading("lot_remaining", text="ロット未完成数",
                                     command=lambda c="lot_remaining": self.sort_plan_list(c))
        self.tree_plan_list.column("list_no", width=110, anchor=tk.W)
        self.tree_plan_list.column("lot_no", width=90, anchor=tk.W)
        self.tree_plan_list.column("file_no", width=90, anchor=tk.W)
        self.tree_plan_list.column("board_name", width=120, anchor=tk.W)
        self.tree_plan_list.column("order_qty", width=80, anchor=tk.E)
        self.tree_plan_list.column("actual_qty", width=80, anchor=tk.E)
        self.tree_plan_list.column("diff", width=80, anchor=tk.E)
        self.tree_plan_list.column("lot_completed", width=100, anchor=tk.E)
        self.tree_plan_list.column("lot_remaining", width=100, anchor=tk.E)

        vsb_plan = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_plan_list.yview)
        self.tree_plan_list.configure(yscrollcommand=vsb_plan.set)
        vsb_plan.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_plan_list.pack(expand=True, fill=tk.BOTH)
        self.tree_plan_list.bind("<<TreeviewSelect>>", self.on_select_plan_list)
        self.tree_plan_list.bind("<Double-1>", self.on_plan_double_click)

        ttk.Button(right_frame, text="更新", command=self.load_plan_list).pack(fill=tk.X, pady=(5, 0))

        self.load_plan_list()

    def load_plan_list(self):
        for item in self.tree_plan_list.get_children():
            self.tree_plan_list.delete(item)

        lot_completion_cache = {}

        for plan_item in list_active_plan_items():
            kitting_list_no = plan_item["kitting_list_no"]
            lot_no = plan_item["lot_no"]
            order_qty = plan_item["order_qty"] or 0
            actual_qty = get_app_cumulative_qty(kitting_list_no)
            diff = order_qty - actual_qty

            if lot_no not in lot_completion_cache:
                lot_completion_cache[lot_no] = calculate_lot_completion(lot_no)
            lot_info = lot_completion_cache[lot_no]
            lot_completed = lot_info["completed_quantity"]
            lot_remaining = lot_info["remaining_quantity"]

            self.tree_plan_list.insert("", tk.END, values=(
                kitting_list_no,
                lot_no,
                plan_item["setup_file_no"],
                plan_item["board_name"],
                f"{order_qty:.0f}",
                f"{actual_qty:.0f}",
                f"{diff:.0f}",
                f"{lot_completed:.0f}",
                f"{lot_remaining:.0f}",
            ))

    def on_select_plan_list(self, event):
        sel = self.tree_plan_list.selection()
        if not sel:
            return
        list_no = self.tree_plan_list.item(sel[0], "values")[0]
        self.entry_kitting_no.delete(0, tk.END)
        self.entry_kitting_no.insert(0, list_no)

    def sort_plan_list(self, col):
        numeric_cols = {"order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining"}

        def sort_key(value):
            if col in numeric_cols:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

        ascending = self.plan_sort_states.get(col, True)

        items = [
            (self.tree_plan_list.set(iid, col), iid)
            for iid in self.tree_plan_list.get_children("")
        ]
        items.sort(key=lambda t: sort_key(t[0]), reverse=not ascending)

        for index, (_, iid) in enumerate(items):
            self.tree_plan_list.move(iid, "", index)

        self.plan_sort_states[col] = not ascending

    def on_plan_double_click(self, event):
        sel = self.tree_plan_list.selection()
        if not sel:
            return
        list_no = self.tree_plan_list.item(sel[0], "values")[0]
        self.entry_kitting_no.delete(0, tk.END)
        self.entry_kitting_no.insert(0, list_no)
        self.search_plan()

    def _add_info_row(self, parent, label_text, row):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky=tk.W, pady=3)
        val_label = ttk.Label(parent, text="-", foreground="blue")
        val_label.grid(row=row, column=1, sticky=tk.W, pady=3)
        return val_label

    def search_plan(self):
        kitting_no = self.entry_kitting_no.get().strip()
        if not kitting_no:
            messagebox.showwarning("入力エラー", "キッティングリストNo.を入力してください。")
            return

        plan = search_plan_by_kitting_no(kitting_no)
        if not plan:
            messagebox.showerror("検索エラー", f"キッティングリストNo. {kitting_no} の計画が見つかりません。")
            self.current_plan = None
            self.btn_register.config(state=tk.DISABLED)
            self.btn_correction.config(state=tk.DISABLED)
            return

        self.current_plan = plan
        self.lbl_lot.config(text=plan["lot_no"])
        self.lbl_setup.config(text=f"{plan['setup_file_no']}（{plan['board_name']}）")
        self.lbl_side.config(text=plan["production_side"])
        self.lbl_plan_qty.config(text=f"{plan['planned_qty']:.0f}")
        self.lbl_ext_cum.config(text=f"{plan['cumulative_qty_external']:.0f}")
        self.lbl_app_cum.config(text=f"{plan['app_cumulative_qty']:.0f}")

        self.lbl_lot_completed.config(text=f"{plan['lot_completed_quantity']:.0f}")
        self.lbl_lot_remaining.config(text=f"{plan['lot_remaining_quantity']:.0f}")

        file_actuals_text = "\n".join(
            f"{file_no}: {qty:.0f}" for file_no, qty in plan["lot_file_actuals"].items()
        )
        self.lbl_lot_file_actuals.config(text=file_actuals_text or "-")

        surplus_text = "\n".join(
            f"{file_no}: {qty:.0f}" for file_no, qty in plan["lot_surplus"].items()
        )
        self.lbl_lot_surplus.config(text=surplus_text or "-")

        self.btn_register.config(state=tk.NORMAL)
        self.btn_correction.config(state=tk.NORMAL)
        self.load_history(kitting_no)

    def load_history(self, kitting_no):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in get_daily_history(kitting_no):
            self.tree.insert("", tk.END, values=(
                rec["report_date"], f"{rec['daily_qty']:.0f}", rec["worker_id"]
            ))

    def open_correction_window(self):
        if not self.current_plan:
            return
        ActualCorrectionWindow(
            self,
            kitting_list_no=self.current_plan["kitting_list_no"],
            lot_no=self.current_plan["lot_no"],
            on_updated=self.load_plan_list,
        )

    def open_daily_report(self):
        DailyReportWindow(self)

    def open_monthly_report(self):
        MonthlyReportWindow(self)

    def on_production_csv_import(self):
        """
        実績CSV（lot_no + 製品名ベース）を取り込み、production_daily へ自動登録する。
        一致しなかった行は UnmatchedProductionWindow で一覧表示して通知する。
        """
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not file_path:
            return

        worker_id = self.current_worker.get("worker_id", "SYSTEM")

        try:
            result = import_production_csv(file_path, default_worker_id=worker_id)
        except Exception as e:
            messagebox.showerror("エラー", f"実績CSV取込中にエラーが発生しました：\n{e}")
            return

        imported = result["imported"]
        unmatched = result["unmatched"]
        warnings = result["warnings"]

        msg = f"取込件数：{len(imported)}件"
        if warnings:
            shown = "\n".join(warnings[:10])
            more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
            msg += f"\n\n警告（{len(warnings)}件）：\n{shown}{more}"
        messagebox.showinfo("実績CSV取込結果", msg)

        if unmatched:
            UnmatchedProductionWindow(self, unmatched)

        if imported:
            self.load_plan_list()
            if self.current_plan:
                self.load_history(self.current_plan["kitting_list_no"])
            self.open_daily_report()

    def register_result(self):
        if not self.current_plan:
            return

        try:
            daily_qty = float(self.entry_daily_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "実績数には数値を入力してください。")
            return

        worker_id = self.current_worker.get("worker_id", "SYSTEM")
        kitting_no = self.current_plan["kitting_list_no"]

        try:
            new_cumulative = register_daily_result(kitting_no, daily_qty, worker_id)
        except ValueError as e:
            messagebox.showerror("登録エラー", str(e))
            return

        self.lbl_app_cum.config(text=f"{new_cumulative:.0f}")
        self.entry_daily_qty.delete(0, tk.END)
        self.load_history(kitting_no)
        messagebox.showinfo("登録完了", f"実績を登録しました。アプリ入力累計：{new_cumulative:.0f}")


class ActualCorrectionWindow(tk.Toplevel):
    """
    完了済み計画も含め、production_daily の実績を修正・削除するためのウィンドウ。
    """
    def __init__(self, parent, kitting_list_no, lot_no, on_updated=None):
        super().__init__(parent)
        self.kitting_list_no = kitting_list_no
        self.lot_no = lot_no
        self.on_updated = on_updated

        self.title(f"実績修正（{kitting_list_no}）")
        self.geometry("500x420")

        hist_frame = ttk.LabelFrame(self, text="実績履歴", padding=10)
        hist_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=(15, 5))

        cols = ("report_date", "daily_qty", "worker_id")
        self.tree = ttk.Treeview(hist_frame, columns=cols, show="headings")
        self.tree.heading("report_date", text="日付")
        self.tree.heading("daily_qty", text="当日実績")
        self.tree.heading("worker_id", text="作業者")
        self.tree.column("report_date", width=150)
        self.tree.column("daily_qty", width=100, anchor=tk.E)
        self.tree.column("worker_id", width=150)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_history)

        edit_frame = ttk.LabelFrame(self, text="選択した実績の修正", padding=10)
        edit_frame.pack(fill=tk.X, padx=15, pady=(5, 15))

        ttk.Label(edit_frame, text="実績数：").pack(side=tk.LEFT, padx=5)
        self.entry_edit_qty = ttk.Entry(edit_frame, width=10)
        self.entry_edit_qty.pack(side=tk.LEFT, padx=5)

        self.btn_update = ttk.Button(edit_frame, text="修正", command=self.on_update,
                                      state=tk.DISABLED)
        self.btn_update.pack(side=tk.LEFT, padx=5)

        self.btn_delete = ttk.Button(edit_frame, text="削除", command=self.on_delete,
                                      state=tk.DISABLED)
        self.btn_delete.pack(side=tk.LEFT, padx=5)

        self.load_history()

    def load_history(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in get_daily_history(self.kitting_list_no):
            self.tree.insert("", tk.END, iid=str(rec["prod_log_id"]), values=(
                rec["report_date"], f"{rec['daily_qty']:.0f}", rec["worker_id"]
            ))
        self.entry_edit_qty.delete(0, tk.END)
        self.btn_update.config(state=tk.DISABLED)
        self.btn_delete.config(state=tk.DISABLED)

    def on_select_history(self, event):
        sel = self.tree.selection()
        if not sel:
            self.btn_update.config(state=tk.DISABLED)
            self.btn_delete.config(state=tk.DISABLED)
            return
        values = self.tree.item(sel[0], "values")
        self.entry_edit_qty.delete(0, tk.END)
        self.entry_edit_qty.insert(0, values[1])
        self.btn_update.config(state=tk.NORMAL)
        self.btn_delete.config(state=tk.NORMAL)

    def on_update(self):
        sel = self.tree.selection()
        if not sel:
            return
        prod_log_id = int(sel[0])

        try:
            daily_qty = float(self.entry_edit_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "実績数には数値を入力してください。")
            return

        update_daily_result(prod_log_id, daily_qty)
        self._after_change()
        messagebox.showinfo("修正完了", "実績を修正しました。")

    def on_delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        prod_log_id = int(sel[0])

        if not messagebox.askyesno("確認", "選択した実績を削除します。よろしいですか？"):
            return

        delete_daily_result(prod_log_id)
        self._after_change()
        messagebox.showinfo("削除完了", "実績を削除しました。")

    def _after_change(self):
        calculate_lot_completion(self.lot_no)
        self.load_history()
        if self.on_updated:
            self.on_updated()