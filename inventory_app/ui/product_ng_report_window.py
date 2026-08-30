# ui/product_ng_report_window.py
import csv
import os
import tempfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from ui.daily_report_window import build_daily_report_pdf, ReportPreviewWindow
from models.scrap_records import list_scrap_summary_by_kitting_no
from models.kitting_plan import find_plan_item_by_kitting_no

PRODUCT_NG_HEADERS = ["キッティングNo", "ファイルNo", "ロットNo", "NG数量"]


def _product_ng_row_to_values(row):
    return [row["kitting_list_no"], row["file_no"], row["lot_no"], f"{row['total_ng_qty']:.0f}"]


def build_product_ng_report_rows():
    """
    製品NGレポート用のデータを、kitting_list_no単位で組み立てる。

    models.scrap_records.list_scrap_summary_by_kitting_no()（ui.ng_input_window の
    NG一覧と同じ集計関数、kitting_list_no・lot_no・production_side単位）を土台に、
    is_unplanned=0（計画あり）の行のみ models.kitting_plan.find_plan_item_by_kitting_no()
    でロットNoを個別補完する（list_active_plan_items()は「1回目除外」ロジックを持つため
    使わない、というNG一覧実装時の方針をそのまま踏襲）。is_unplanned=1（計画外）の行は
    ロットNoを空欄のまま含める。

    lot_noの補完にsummary自身のlot_no（既にkitting_list_noとあわせて集計済み）を渡す
    理由：実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースが
    478件確認されており、kitting_list_noだけの検索では計画を一意に特定できないため
    （list_scrap_summary_by_kitting_no()側で既にlot_no単位に分かれている行に対して、
    別ロットの計画を誤って結びつけてしまう恐れがある）。

    NG数量は total_ng_qty（消費数量＝ng_qty列の合計）をそのまま使う。96コードごとに
    員数が異なるため「申告されたNG枚数」そのものではない点に注意（詳細は
    models.scrap_records のモジュールdocstring参照）。
    """
    rows = []
    for summary in list_scrap_summary_by_kitting_no():
        kitting_list_no = summary["kitting_list_no"]
        is_unplanned = bool(summary["is_unplanned"])
        summary_lot_no = summary["lot_no"]

        lot_no = ""
        if not is_unplanned:
            plan = find_plan_item_by_kitting_no(kitting_list_no, summary_lot_no) if summary_lot_no \
                else find_plan_item_by_kitting_no(kitting_list_no)
            if plan:
                lot_no = plan["lot_no"] or ""

        rows.append({
            "kitting_list_no": kitting_list_no,
            "file_no": summary["file_no"],
            "lot_no": lot_no,
            "total_ng_qty": summary["total_ng_qty"],
            "production_side": summary["production_side"],
            "is_unplanned": is_unplanned,
        })

    return rows


class ProductNgReportWindow(tk.Toplevel):
    """
    製品NGレポート：kitting_list_no単位でNG（仕損）実績をまとめて一覧・出力する画面。

    数量の修正はこの画面では行わない。行をダブルクリックすると
    ui.ng_input_window.NgInputWindow を開き、該当計画（または計画外の
    file_no＋生産面）を検索欄へ反映した上で自動的に再展開する
    （NG一覧からの連携と同じ導線）。実際の数量変更は、そちらの画面での
    再展開・登録（既存のreplace_scrap_records()による洗い替え）に委ねる。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.report_rows = build_product_ng_report_rows()

        self.title("製品NGレポート")
        self.geometry("900x520")

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("kitting_list_no", "file_no", "lot_no", "total_ng_qty")
        headers = dict(zip(cols, PRODUCT_NG_HEADERS))
        widths = {"kitting_list_no": 160, "file_no": 100, "lot_no": 110, "total_ng_qty": 100}
        left_aligned = {"kitting_list_no", "file_no", "lot_no"}

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Double-1>", self.on_row_double_click)

        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_product_ng_row_to_values(row))

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="印刷", command=self.on_print).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

    def on_row_double_click(self, event):
        """
        選択行のkitting_list_no（計画あり）またはfile_no＋生産面（計画外）を
        ui.ng_input_window.NgInputWindow の検索欄へ反映し、on_expand()
        （「展開」ボタンと同じ処理）を自動実行する。NG数量欄は変更しないため、
        未入力の場合はon_expand()側の既存バリデーションがそのまま働く
        （NG一覧からの再展開時と同じ挙動）。循環import回避のため、ここで都度importする。

        計画あり行はrow["lot_no"]（build_product_ng_report_rows()で既にkitting_list_no
        とあわせて特定済み）をon_expand()へ渡す。実DBで同一kitting_list_noが複数の
        異なるlot_noにまたがって存在するケースが478件確認されており、これにより
        曖昧な単体検索を経由せず一意に計画を特定できる。計画外行はlot_noを
        持たないため渡さない。
        """
        sel = self.tree.selection()
        if not sel:
            return
        index = self.tree.index(sel[0])
        if index >= len(self.report_rows):
            return
        row = self.report_rows[index]

        from ui.ng_input_window import NgInputWindow
        ng_window = NgInputWindow(self, {})

        if row["is_unplanned"]:
            side = row["production_side"]
            ng_window.entry_file_no.insert(0, row["file_no"])
            ng_window.combo_side.set(f"面{side}" if side in (1, 2) else "")
            ng_window.on_expand()
        else:
            ng_window.entry_kitting_no.insert(0, row["kitting_list_no"])
            ng_window.on_expand(lot_no=row["lot_no"] or None)

    def _as_of_label(self):
        return datetime.now().strftime("%Y-%m-%d")

    def _file_stub(self):
        return datetime.now().strftime("%Y%m%d")

    def on_preview(self):
        ReportPreviewWindow(
            self, self.report_rows, self._as_of_label(), title_prefix="製品NGレポート",
            headers=PRODUCT_NG_HEADERS, col_widths=[160, 110, 110, 100],
            row_to_values=_product_ng_row_to_values,
        )

    def on_print(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "NG登録済みのデータがありません。", parent=self.winfo_toplevel())
            return

        tmp_path = os.path.join(tempfile.gettempdir(), "product_ng_report_print.pdf")
        try:
            build_daily_report_pdf(
                self.report_rows, self._as_of_label(), tmp_path, title_prefix="製品NGレポート",
                headers=PRODUCT_NG_HEADERS, row_to_values=_product_ng_row_to_values,
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
            messagebox.showwarning("警告", "NG登録済みのデータがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"product_ng_report_{self._file_stub()}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            build_daily_report_pdf(
                self.report_rows, self._as_of_label(), save_path, title_prefix="製品NGレポート",
                headers=PRODUCT_NG_HEADERS, row_to_values=_product_ng_row_to_values,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "NG登録済みのデータがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"product_ng_report_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(PRODUCT_NG_HEADERS)
                for row in self.report_rows:
                    writer.writerow(_product_ng_row_to_values(row))
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())
