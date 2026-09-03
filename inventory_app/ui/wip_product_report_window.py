# ui/wip_product_report_window.py
import csv
import os
import tempfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from ui.daily_report_window import build_daily_report_pdf, ReportPreviewWindow
from models.wip_scrap_records import list_wip_scrap_summary

WIP_PRODUCT_HEADERS = ["キッティングNo", "ファイルNo", "ロットNo", "仕掛数量"]


def _wip_product_row_to_values(row):
    return [row["kitting_list_no"], row["file_no"], row["lot_no"], f"{row['total_wip_qty']:.0f}"]


def build_wip_product_report_rows():
    """
    仕掛製品レポート用のデータを、kitting_list_no単位で組み立てる。

    models.wip_scrap_records.list_wip_scrap_summary()（ui.wip_expansion_window の
    仕掛一覧の状態表示と同じ集計関数、kitting_list_no・lot_no・production_side単位）を
    そのままレポート行として使う。

    ui.product_ng_report_window.build_product_ng_report_rows() と異なり、
    lot_noを個別に計画（kitting_plan_items）から補完する処理は不要：
    wip_scrap_recordsの元になるwip_board_snapshotの各行は、月報の「仕掛数量抽出」
    （実際の生産実績・計画から抽出）由来であり、NG入力画面の「計画外登録」の
    ような「計画が存在しない」ケースを持たないため、list_wip_scrap_summary()が
    返すlot_noをそのまま使えばよい。

    仕掛数量は total_wip_qty（消費数量＝qty列の合計）をそのまま使う。96コードごとに
    員数が異なるため「仕掛の枚数」そのものではない点はNG版と同じ注意が必要
    （詳細は models.wip_scrap_records のモジュールdocstring参照）。
    """
    rows = []
    for summary in list_wip_scrap_summary():
        rows.append({
            "kitting_list_no": summary["kitting_list_no"],
            "file_no": summary["file_no"],
            "lot_no": summary["lot_no"] or "",
            "total_wip_qty": summary["total_qty"],
            "production_side": summary["production_side"],
        })

    return rows


class WipProductReportWindow(tk.Toplevel):
    """
    仕掛製品レポート：kitting_list_no単位で確定登録済みの仕掛展開結果を
    まとめて一覧・出力する画面（ui.product_ng_report_window.ProductNgReportWindow
    を仕掛版に複製・適応したもの）。

    数量の修正はこの画面では行わない。行をダブルクリックすると
    ui.wip_expansion_window.WipExpansionWindow を新規に開き、該当基板を
    自動的に再展開する（ui.product_ng_report_window と同じ導線パターン）。
    実際の数量変更は、そちらの画面での再展開・再確定登録（既存の
    save_wip_scrap_records()による洗い替え）に委ねる。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.report_rows = build_wip_product_report_rows()

        self.title("仕掛製品レポート")
        self.geometry("900x520")

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("kitting_list_no", "file_no", "lot_no", "total_wip_qty")
        headers = dict(zip(cols, WIP_PRODUCT_HEADERS))
        widths = {"kitting_list_no": 160, "file_no": 100, "lot_no": 110, "total_wip_qty": 100}
        left_aligned = {"kitting_list_no", "file_no", "lot_no"}

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Double-1>", self.on_row_double_click)

        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_wip_product_row_to_values(row))

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="印刷", command=self.on_print).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

    def on_row_double_click(self, event):
        """
        選択行のkitting_list_no・lot_no・production_sideを
        ui.wip_expansion_window.WipExpansionWindow.expand_by_identity() へ渡し、
        該当基板を自動的に再展開する（ui.product_ng_report_window.
        ProductNgReportWindow.on_row_double_click() と同じ導線パターン。
        循環import回避のため、ここで都度importする）。
        """
        sel = self.tree.selection()
        if not sel:
            return
        index = self.tree.index(sel[0])
        if index >= len(self.report_rows):
            return
        row = self.report_rows[index]

        from ui.wip_expansion_window import WipExpansionWindow
        wip_window = WipExpansionWindow(self, {})
        wip_window.expand_by_identity(
            row["kitting_list_no"], lot_no=row["lot_no"] or None, production_side=row["production_side"],
        )

    def _as_of_label(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _file_stub(self):
        return datetime.now().strftime("%Y%m%d")

    def on_preview(self):
        ReportPreviewWindow(
            self, self.report_rows, self._as_of_label(), title_prefix="仕掛製品レポート",
            headers=WIP_PRODUCT_HEADERS, col_widths=[160, 110, 110, 100],
            row_to_values=_wip_product_row_to_values,
        )

    def on_print(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "確定登録済みの仕掛データがありません。", parent=self.winfo_toplevel())
            return

        tmp_path = os.path.join(tempfile.gettempdir(), "wip_product_report_print.pdf")
        try:
            build_daily_report_pdf(
                self.report_rows, self._as_of_label(), tmp_path, title_prefix="仕掛製品レポート",
                headers=WIP_PRODUCT_HEADERS, row_to_values=_wip_product_row_to_values,
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
            messagebox.showwarning("警告", "確定登録済みの仕掛データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"wip_product_report_{self._file_stub()}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            build_daily_report_pdf(
                self.report_rows, self._as_of_label(), save_path, title_prefix="仕掛製品レポート",
                headers=WIP_PRODUCT_HEADERS, row_to_values=_wip_product_row_to_values,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "確定登録済みの仕掛データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"wip_product_report_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(WIP_PRODUCT_HEADERS)
                for row in self.report_rows:
                    writer.writerow(_wip_product_row_to_values(row))
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())
