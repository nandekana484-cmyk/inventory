# ui/parts_ng_report_window.py
import csv
import os
import tempfile

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from ui.daily_report_window import build_daily_report_pdf, ReportPreviewWindow
from models.scrap_records import query_scrap_totals_range

PARTS_NG_HEADERS = ["96コード", "数量"]


def _parts_ng_row_to_values(row):
    return [row["part_no"], f"{row['total_qty']:.0f}"]


class PartsNgReportWindow(tk.Toplevel):
    """
    96NGレポート：期間を指定し、96コード（part_no）単位でNG（仕損）消費数量を
    合計して一覧・出力する画面（月報画面と同じ期間指定パターン）。

    models.scrap_records.query_scrap_totals_range(from_date, to_date) を使用する
    （全期間集計の query_scrap_totals() とは別関数。在庫差異レポート側の挙動には
    影響しない）。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.from_date = None
        self.to_date = None
        self.report_rows = []

        self.title("96NGレポート")
        self.geometry("500x560")

        period_frame = ttk.LabelFrame(self, text="集計期間", padding=10)
        period_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(period_frame, text="開始日：").pack(side=tk.LEFT, padx=5)
        self.from_date_entry = DateEntry(period_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.from_date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Label(period_frame, text="終了日：").pack(side=tk.LEFT, padx=5)
        self.to_date_entry = DateEntry(period_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.to_date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(period_frame, text="集計開始", command=self.on_aggregate).pack(side=tk.LEFT, padx=10)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("part_no", "total_qty")
        headers = dict(zip(cols, PARTS_NG_HEADERS))

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("part_no", text=headers["part_no"])
        self.tree.heading("total_qty", text=headers["total_qty"])
        self.tree.column("part_no", width=260, anchor=tk.W)
        self.tree.column("total_qty", width=140, anchor=tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="印刷", command=self.on_print).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

    def on_aggregate(self):
        from_date = self.from_date_entry.get()
        to_date = self.to_date_entry.get()

        try:
            totals = query_scrap_totals_range(from_date, to_date)
        except Exception as e:
            messagebox.showerror("エラー", f"集計に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        self.report_rows = [
            {"part_no": part_no, "total_qty": total_qty}
            for part_no, total_qty in sorted(totals.items())
        ]
        self.from_date = from_date
        self.to_date = to_date
        self.title(f"96NGレポート（{from_date} ～ {to_date}）")

        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_parts_ng_row_to_values(row))

    def _period_label(self):
        return f"{self.from_date} ～ {self.to_date}"

    def _file_stub(self):
        return f"{self.from_date.replace('-', '')}_{self.to_date.replace('-', '')}"

    def on_preview(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return
        ReportPreviewWindow(
            self, self.report_rows, self._period_label(), title_prefix="96NGレポート",
            headers=PARTS_NG_HEADERS, col_widths=[300, 150], row_to_values=_parts_ng_row_to_values,
        )

    def on_print(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return

        tmp_path = os.path.join(tempfile.gettempdir(), f"parts_ng_report_{self._file_stub()}_print.pdf")
        try:
            build_daily_report_pdf(
                self.report_rows, self._period_label(), tmp_path, title_prefix="96NGレポート",
                headers=PARTS_NG_HEADERS, row_to_values=_parts_ng_row_to_values,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF生成に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        try:
            os.startfile(tmp_path, "print")
        except Exception as e:
            messagebox.showerror("エラー", f"印刷の起動に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("印刷", "OS標準の印刷ダイアログを開きました。", parent=self.winfo_toplevel())

    def on_export_pdf(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return

        default_name = f"parts_ng_report_{self._file_stub()}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            build_daily_report_pdf(
                self.report_rows, self._period_label(), save_path, title_prefix="96NGレポート",
                headers=PARTS_NG_HEADERS, row_to_values=_parts_ng_row_to_values,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return

        default_name = f"parts_ng_report_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(PARTS_NG_HEADERS)
                for row in self.report_rows:
                    writer.writerow(_parts_ng_row_to_values(row))
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())
