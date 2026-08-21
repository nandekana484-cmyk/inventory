# ui/kitting_production_entry.py
import tkinter as tk
from tkinter import ttk, messagebox
from services.production_service import (
    search_plan_by_kitting_no,
    register_daily_result,
    get_daily_history,
)


class KittingProductionEntryWindow(tk.Toplevel):
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.current_plan = None

        self.title("生産実績入力（キッティングリストNo.）")
        self.geometry("700x550")

        self.create_widgets()

    def create_widgets(self):
        # 検索エリア
        search_frame = ttk.LabelFrame(self, text="キッティングリストNo.検索", padding=10)
        search_frame.pack(fill=tk.X, padx=15, pady=5)

        ttk.Label(search_frame, text="キッティングリストNo.:").pack(side=tk.LEFT, padx=5)
        self.entry_kitting_no = ttk.Entry(search_frame, width=20)
        self.entry_kitting_no.pack(side=tk.LEFT, padx=5)
        self.entry_kitting_no.bind("<Return>", lambda e: self.search_plan())

        ttk.Button(search_frame, text="検索", command=self.search_plan).pack(side=tk.LEFT, padx=5)

        # 計画情報表示エリア
        info_frame = ttk.LabelFrame(self, text="計画情報", padding=10)
        info_frame.pack(fill=tk.X, padx=15, pady=5)

        self.lbl_lot = self._add_info_row(info_frame, "ロットNo.：", 0)
        self.lbl_setup = self._add_info_row(info_frame, "セットアップファイルNo.（基板名）：", 1)
        self.lbl_side = self._add_info_row(info_frame, "生産面：", 2)
        self.lbl_plan_qty = self._add_info_row(info_frame, "今回計画数：", 3)
        self.lbl_ext_cum = self._add_info_row(info_frame, "外部システム累計：", 4)
        self.lbl_app_cum = self._add_info_row(info_frame, "アプリ入力累計：", 5)

        # 実績入力エリア
        entry_frame = ttk.LabelFrame(self, text="本日の生産実績", padding=10)
        entry_frame.pack(fill=tk.X, padx=15, pady=5)

        ttk.Label(entry_frame, text="本日生産実績：").pack(side=tk.LEFT, padx=5)
        self.entry_daily_qty = ttk.Entry(entry_frame, width=10)
        self.entry_daily_qty.pack(side=tk.LEFT, padx=5)

        self.btn_register = ttk.Button(entry_frame, text="登録", command=self.register_result,
                                        state=tk.DISABLED)
        self.btn_register.pack(side=tk.LEFT, padx=15)

        # 履歴表示エリア
        hist_frame = ttk.LabelFrame(self, text="日次実績履歴", padding=10)
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
            return

        self.current_plan = plan
        self.lbl_lot.config(text=plan["lot_no"])
        self.lbl_setup.config(text=f"{plan['setup_file_no']}（{plan['board_name']}）")
        self.lbl_side.config(text=plan["production_side"])
        self.lbl_plan_qty.config(text=f"{plan['planned_qty']:.0f}")
        self.lbl_ext_cum.config(text=f"{plan['cumulative_qty_external']:.0f}")
        self.lbl_app_cum.config(text=f"{plan['app_cumulative_qty']:.0f}")

        self.btn_register.config(state=tk.NORMAL)
        self.load_history(kitting_no)

    def load_history(self, kitting_no):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in get_daily_history(kitting_no):
            self.tree.insert("", tk.END, values=(
                rec["report_date"], f"{rec['daily_qty']:.0f}", rec["worker_id"]
            ))

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