# ui/inventory_diff_window.py
import csv
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from services.inventory_diff_service import build_inventory_diff_report
from ui.daily_report_window import ReportPreviewWindow, build_daily_report_pdf

REPORT_HEADERS = ["部品番号", "在庫数量", "仕掛数量", "仕損数量", "合計数量", "理論在庫数量", "差異数量"]
COL_WIDTHS = [90, 70, 70, 70, 70, 90, 70]


def _row_to_values(row):
    return [
        row["part_no"], row["stock_qty"], row["wip_qty"], row["scrap_qty"],
        row["total_qty"], row["theoretical_qty"], row["diff_qty"],
    ]


class InventoryDiffWindow(tk.Toplevel):
    """
    在庫（PC在庫）＋仕掛（WIP）＋仕損（NG）＋理論在庫を突き合わせた
    96部品ごとの差異を一覧表示する画面。

    印刷プレビュー・PDF出力・CSV出力は日報（ui.daily_report_window）の
    ReportPreviewWindow / build_daily_report_pdf をそのまま再利用し、
    列構成（REPORT_HEADERS / _row_to_values）だけこの画面専用のものを渡す。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.report_date = datetime.now().strftime("%Y-%m-%d")
        self.report_rows = build_inventory_diff_report()

        self.title("在庫差異レポート")
        self.geometry("900x520")

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("part_no", "stock_qty", "wip_qty", "scrap_qty", "total_qty", "theoretical_qty", "diff_qty")
        headers = dict(zip(cols, REPORT_HEADERS))
        widths = {
            "part_no": 140, "stock_qty": 100, "wip_qty": 100, "scrap_qty": 100,
            "total_qty": 100, "theoretical_qty": 120, "diff_qty": 100,
        }

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c == "part_no" else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)

        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_row_to_values(row))

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

    def on_preview(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "表示するデータがありません。")
            return
        ReportPreviewWindow(
            self, self.report_rows, self.report_date,
            title_prefix="在庫差異レポート",
            headers=REPORT_HEADERS,
            col_widths=COL_WIDTHS,
            row_to_values=_row_to_values,
        )

    def on_export_pdf(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "表示するデータがありません。")
            return

        default_name = f"inventory_diff_report_{self.report_date.replace('-', '')}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        )
        if not save_path:
            return

        try:
            build_daily_report_pdf(
                self.report_rows, self.report_date, save_path,
                title_prefix="在庫差異レポート",
                headers=REPORT_HEADERS,
                row_to_values=_row_to_values,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}")
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}")

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "表示するデータがありません。")
            return

        default_name = f"inventory_diff_report_{self.report_date.replace('-', '')}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(REPORT_HEADERS)
                for row in self.report_rows:
                    writer.writerow(_row_to_values(row))
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}")
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}")
