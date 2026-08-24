# ui/monthly_report_window.py
import csv
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from services.production_service import build_monthly_report
from ui.daily_report_window import (
    REPORT_HEADERS,
    _row_to_values,
    build_daily_report_pdf,
    ReportPreviewWindow,
)


class MonthlyReportWindow(tk.Toplevel):
    """
    任意の期間（開始日～終了日）を指定して集計する月報ウィンドウ。
    列構成・印刷プレビュー・PDF/CSV出力ロジックは日報（DailyReportWindow）と共通。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.from_date = None
        self.to_date = None
        self.report_rows = []

        self.title("月報出力")
        self.geometry("1020x560")

        # 期間指定エリア
        period_frame = ttk.LabelFrame(self, text="集計期間", padding=10)
        period_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(period_frame, text="開始日：").pack(side=tk.LEFT, padx=5)
        self.from_date_entry = DateEntry(period_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.from_date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Label(period_frame, text="終了日：").pack(side=tk.LEFT, padx=5)
        self.to_date_entry = DateEntry(period_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.to_date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(period_frame, text="集計開始", command=self.on_aggregate).pack(side=tk.LEFT, padx=10)

        # 一覧表示エリア（日報と同一列構成）
        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("seq", "file_no", "board_name", "lot_no", "daily_qty", "app_cumulative_qty", "order_qty",
                "lot_completed", "surplus_qty", "lot_remaining")
        headers = dict(zip(cols, REPORT_HEADERS))
        widths = {
            "seq": 50, "file_no": 100, "board_name": 160, "lot_no": 110,
            "daily_qty": 80, "app_cumulative_qty": 80, "order_qty": 80,
            "lot_completed": 80, "surplus_qty": 80, "lot_remaining": 80,
        }
        left_aligned = {"file_no", "board_name", "lot_no"}

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

    def on_aggregate(self):
        from_date = self.from_date_entry.get()
        to_date = self.to_date_entry.get()

        try:
            self.report_rows = build_monthly_report(from_date, to_date)
        except Exception as e:
            messagebox.showerror("エラー", f"集計に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        self.from_date = from_date
        self.to_date = to_date
        self.title(f"月報出力（{from_date} ～ {to_date}）")

        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_row_to_values(row))

    def _period_label(self):
        return f"{self.from_date} ～ {self.to_date}"

    def _file_stub(self):
        return f"{self.from_date.replace('-', '')}_{self.to_date.replace('-', '')}"

    def on_preview(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return
        ReportPreviewWindow(self, self.report_rows, self._period_label(), title_prefix="月報")

    def on_export_pdf(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return

        default_name = f"monthly_report_{self._file_stub()}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            build_daily_report_pdf(self.report_rows, self._period_label(), save_path, title_prefix="月報")
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return

        default_name = f"monthly_report_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(REPORT_HEADERS)
                for row in self.report_rows:
                    writer.writerow(_row_to_values(row))
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())
