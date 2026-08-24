# ui/daily_report_window.py
import csv
import os
import tempfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import registerFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from services.production_service import build_daily_report, build_monthly_report

JP_FONT = "HeiseiKakuGo-W5"
registerFont(UnicodeCIDFont(JP_FONT))

REPORT_HEADERS = ["No", "ファイルNo", "基板名", "ロットNo", "生産数", "累計数", "注文数",
                   "引落数量", "仕掛数量", "未完了数"]


def _row_to_values(row):
    return [
        row["seq"], row["file_no"], row["board_name"], row["lot_no"],
        row["daily_qty"], row["app_cumulative_qty"], row["order_qty"],
        row["lot_completed"], row["surplus_qty"], row["lot_remaining"],
    ]


def build_daily_report_pdf(report_rows, report_date, output_path, title_prefix="日報",
                             headers=None, row_to_values=None):
    """
    日報データを A4縦PDFとして出力する。
    行数が1ページに収まらない場合は reportlab の Table により自動改ページされる。

    title_prefix はタイトル先頭の帳票名（日報／月報など）。省略時は日報用の表記になる。
    headers / row_to_values を指定すると、日報以外の列構成のレポート
    （在庫差異レポート等）でもこの関数をそのまま再利用できる。
    省略時は日報・月報用の REPORT_HEADERS / _row_to_values を使う。
    """
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = JP_FONT

    elements = [Paragraph(f"{title_prefix} {report_date}", title_style), Spacer(1, 10)]

    headers = headers if headers is not None else REPORT_HEADERS
    row_to_values = row_to_values if row_to_values is not None else _row_to_values

    data = [headers] + [row_to_values(row) for row in report_rows]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), JP_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
    ]))
    elements.append(table)

    def draw_page_number(canvas_obj, doc_obj):
        canvas_obj.setFont(JP_FONT, 8)
        canvas_obj.drawCentredString(A4[0] / 2, 10 * mm, f"ページ {doc_obj.page}")

    doc.build(elements, onFirstPage=draw_page_number, onLaterPages=draw_page_number)


class ReportPreviewWindow(tk.Toplevel):
    """
    日報データを A4縦レイアウトのページとしてプレビュー表示するウィンドウ。
    1ページに収まらない行は自動で2ページ目以降に送られる。
    """
    A4_WIDTH = 595
    A4_HEIGHT = 842
    ROWS_PER_PAGE = 35

    COL_HEADERS = REPORT_HEADERS
    COL_WIDTHS = [25, 55, 80, 55, 45, 45, 45, 50, 50, 50]

    def __init__(self, parent, report_rows, report_date, title_prefix="日報",
                 headers=None, col_widths=None, row_to_values=None):
        super().__init__(parent)
        self.report_rows = report_rows
        self.report_date = report_date
        self.title_prefix = title_prefix
        self.col_headers = headers if headers is not None else self.COL_HEADERS
        self.col_widths = col_widths if col_widths is not None else self.COL_WIDTHS
        self.row_to_values = row_to_values if row_to_values is not None else _row_to_values

        self.title("印刷プレビュー")
        self.geometry("660x760")

        outer = ttk.Frame(self)
        outer.pack(expand=True, fill=tk.BOTH)

        self.canvas_scroll = tk.Canvas(outer, bg="#808080")
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas_scroll.yview)
        self.canvas_scroll.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas_scroll.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self.pages_frame = ttk.Frame(self.canvas_scroll)
        self.canvas_scroll.create_window((0, 0), window=self.pages_frame, anchor="nw")
        self.pages_frame.bind(
            "<Configure>",
            lambda e: self.canvas_scroll.configure(scrollregion=self.canvas_scroll.bbox("all")),
        )

        self._render_pages()

    def _paginate(self):
        rows = self.report_rows
        if not rows:
            return [[]]
        return [rows[i:i + self.ROWS_PER_PAGE] for i in range(0, len(rows), self.ROWS_PER_PAGE)]

    def _render_pages(self):
        pages = self._paginate()
        total_pages = len(pages)
        for page_index, page_rows in enumerate(pages, start=1):
            page_canvas = tk.Canvas(
                self.pages_frame, width=self.A4_WIDTH, height=self.A4_HEIGHT,
                bg="white", highlightthickness=1, highlightbackground="black",
            )
            page_canvas.pack(pady=15)
            self._draw_page(page_canvas, page_rows, page_index, total_pages)

    def _draw_page(self, canvas, rows, page_no, total_pages):
        margin = 30

        canvas.create_text(
            self.A4_WIDTH / 2, margin, text=f"{self.title_prefix} {self.report_date}",
            font=("Helvetica", 14, "bold"),
        )

        col_x = [margin]
        for w in self.col_widths:
            col_x.append(col_x[-1] + w)

        header_y = margin + 30
        for i, h in enumerate(self.col_headers):
            canvas.create_text(col_x[i] + 3, header_y, text=h, anchor="nw", font=("Helvetica", 9, "bold"))
        canvas.create_line(margin, header_y + 16, col_x[-1], header_y + 16)

        row_height = 18
        y = header_y + 20
        for row in rows:
            for i, v in enumerate(self.row_to_values(row)):
                canvas.create_text(col_x[i] + 3, y, text=str(v), anchor="nw", font=("Helvetica", 8))
            y += row_height

        canvas.create_text(
            self.A4_WIDTH / 2, self.A4_HEIGHT - margin,
            text=f"ページ {page_no} / {total_pages}", font=("Helvetica", 9),
        )


class DailyReportWindow(tk.Toplevel):
    """
    本日入力された生産実績を一覧表示し、印刷プレビュー・印刷・PDF出力・CSV出力を行うウィンドウ。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.report_date = datetime.now().strftime("%Y-%m-%d")
        self.report_rows = build_daily_report()

        self.title(f"日報出力（{self.report_date}）")
        self.geometry("1020x500")

        date_frame = ttk.Frame(self, padding=10)
        date_frame.pack(fill=tk.X)

        ttk.Label(date_frame, text="対象日：").pack(side=tk.LEFT, padx=5)
        self.date_entry = DateEntry(date_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(date_frame, text="表示", command=self.on_display).pack(side=tk.LEFT, padx=10)

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

        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_row_to_values(row))

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="印刷", command=self.on_print).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

    def on_display(self):
        selected_date = self.date_entry.get()

        try:
            self.report_rows = build_monthly_report(selected_date, selected_date)
        except Exception as e:
            messagebox.showerror("エラー", f"集計に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        self.report_date = selected_date
        self.title(f"日報出力（{self.report_date}）")

        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_row_to_values(row))

    def on_preview(self):
        ReportPreviewWindow(self, self.report_rows, self.report_date)

    def on_print(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "本日の実績データがありません。", parent=self.winfo_toplevel())
            return

        tmp_path = os.path.join(
            tempfile.gettempdir(), f"daily_report_{self.report_date.replace('-', '')}_print.pdf"
        )
        try:
            build_daily_report_pdf(self.report_rows, self.report_date, tmp_path)
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
            messagebox.showwarning("警告", "本日の実績データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"daily_report_{self.report_date.replace('-', '')}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            build_daily_report_pdf(self.report_rows, self.report_date, save_path)
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "本日の実績データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"daily_report_{self.report_date.replace('-', '')}.csv"
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
