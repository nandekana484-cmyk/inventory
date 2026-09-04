# ui/monthly_report_window.py
import csv
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from services.production_service import build_monthly_report, calculate_lot_completion, build_wip_extraction_rows
from ui.daily_report_window import (
    REPORT_HEADERS,
    _row_to_values,
    build_daily_report_pdf,
    ReportPreviewWindow,
)
from models.wip_board_snapshot import save_wip_snapshot


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
        self.inconsistency_warnings = []
        self.order_qty_inconsistency_warnings = []

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

        cols = ("seq", "file_no", "board_name", "lot_no", "daily_qty", "order_qty",
                "lot_completed", "surplus_qty", "lot_remaining")
        headers = dict(zip(cols, REPORT_HEADERS))
        widths = {
            "seq": 50, "file_no": 100, "board_name": 160, "lot_no": 110,
            "daily_qty": 80, "order_qty": 80,
            "lot_completed": 80, "surplus_qty": 80, "lot_remaining": 80,
        }
        left_aligned = {"file_no", "board_name", "lot_no"}

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Double-1>", self.on_row_double_click)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="仕掛数量抽出", command=self.on_extract_wip).pack(side=tk.LEFT, padx=5)

    def on_aggregate(self):
        from_date = self.from_date_entry.get()
        to_date = self.to_date_entry.get()

        try:
            self.report_rows, self.inconsistency_warnings = build_monthly_report(from_date, to_date)
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

        self.order_qty_inconsistency_warnings = self._collect_order_qty_inconsistencies()

        self._show_inconsistency_warning_if_any()
        self._show_order_qty_inconsistency_warning_if_any()

    def _collect_order_qty_inconsistencies(self):
        """
        self.report_rows（集計済み月報データ）に含まれるdistinctなlot_noについて
        services.production_service.calculate_lot_completion()を呼び、
        order_qty_inconsistent=True（発注数がファイルNo間で一致していない、
        調査により実DBで3件確認済み）と判定されたロットを収集する。

        report_rowsのlot_noはproduction_daily由来（_build_report_rows()の
        rec["lot_id"]）であり、集計対象期間に実績はあるが、その後計画自体が
        削除・無効化された（is_active=0／delete_flag=1）lot_noが理論上あり得る。
        list_plan_items_by_lot()はそのようなlot_noに対して0件を返し
        calculate_lot_completion()がValueErrorを送出するため、その場合は
        判定不能として静かにスキップする（実績はあるのに現存する計画が無い
        ケースへの対応で、今回のスコープではこれ以上の扱いは行わない）。
        """
        lot_nos = sorted({row["lot_no"] for row in self.report_rows if row["lot_no"]})
        warnings = []
        for lot_no in lot_nos:
            try:
                info = calculate_lot_completion(lot_no)
            except ValueError:
                continue
            if info["order_qty_inconsistent"]:
                warnings.append({"lot_no": lot_no, "order_qty_values": info["order_qty_values"]})
        return warnings

    def _show_order_qty_inconsistency_warning_if_any(self):
        """
        _collect_order_qty_inconsistencies()で収集した、発注数（order_qty）が
        ファイルNo間で一致していないロットを警告する。_show_inconsistency_
        warning_if_any()（面1/面2の実績不整合警告）と同じパターン：一覧の表示
        自体は変更せず、集計完了後に別途ダイアログで注意喚起するのみ。
        """
        if not self.order_qty_inconsistency_warnings:
            return

        lines = "\n".join(
            f"・lot_no={w['lot_no']}（値: {', '.join(f'{v:.0f}' for v in w['order_qty_values'])}）"
            for w in self.order_qty_inconsistency_warnings
        )
        messagebox.showwarning(
            "発注数不一致の警告",
            "以下のロットで発注数がファイルNo間で一致していません。"
            "手作業での確認・修正が必要です。\n\n"
            f"{lines}",
            parent=self.winfo_toplevel(),
        )

    def _show_inconsistency_warning_if_any(self):
        """
        services.production_service._build_report_rows()が検知した「面1の実績が
        面2を上回っている」不整合（inconsistency_warnings）があれば、対象lot_noを
        列挙した警告ダイアログを表示する。該当する面1の行は一覧から既に除外済み
        （黙って通常表示・黙って消えるのいずれでもなく、明示的に警告する）。
        """
        if not self.inconsistency_warnings:
            return

        lines = "\n".join(
            f"・lot_no={w['lot_no']}（setup_file_no={w['setup_file_no']}）："
            f"面1（{w['side1_kitting_list_no']}）={w['side1_qty']:.0f} ＞ "
            f"面2（{w['side2_kitting_list_no']}）={w['side2_qty']:.0f}"
            for w in self.inconsistency_warnings
        )
        messagebox.showwarning(
            "実績不整合の警告",
            "以下のロットで面1・面2の実績に不整合があります（面1の実績数が面2を"
            "上回っています）。実績修正画面（ActualCorrectionWindow）での片面のみの"
            "修正が原因の可能性があります。\n"
            "該当する面1の行は、通常表示されるはずの面2省略ルールにより一覧からは"
            "除外されていますが、内容のご確認をお願いします。\n\n"
            f"{lines}",
            parent=self.winfo_toplevel(),
        )

    def on_row_double_click(self, event):
        """
        選択行に対応する実績（kitting_list_no・lot_no）を実績修正ウインドウ
        （ui.kitting_production_entry.ActualCorrectionWindow）で開く。
        完了済み（生産実績入力画面の一覧からは除外済み）の計画でも、
        production_daily に実績が残っている限りここから修正できる。
        循環import回避のため、ここで都度importする。
        """
        sel = self.tree.selection()
        if not sel:
            return
        index = self.tree.index(sel[0])
        if index >= len(self.report_rows):
            return
        row = self.report_rows[index]
        kitting_list_no = row["kitting_list_no"]
        if not kitting_list_no:
            return

        from ui.kitting_production_entry import ActualCorrectionWindow
        ActualCorrectionWindow(
            self,
            kitting_list_no=kitting_list_no,
            lot_no=row["lot_no"],
            on_updated=self.refresh_report,
        )

    def refresh_report(self):
        """実績修正後に月報の一覧を再取得して表示を更新する。"""
        if not self.from_date or not self.to_date:
            return
        self.report_rows, self.inconsistency_warnings = build_monthly_report(self.from_date, self.to_date)
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_row_to_values(row))

        # ActualCorrectionWindowでの片面修正直後はここが呼ばれるため、まさに
        # 不整合が新たに発生し得るタイミング。on_aggregate()と同様に警告する。
        self._show_inconsistency_warning_if_any()

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

    def on_extract_wip(self):
        """
        self.report_rows（既に集計済みの月報データ）に含まれるdistinctなlot_noに
        ついて、services.production_service.build_wip_extraction_rows()で
        setup_file_no×面単位の仕掛数量（calculate_lot_completion()のfile_actuals
        を土台にした合算後の値）を算出し、models.wip_board_snapshot.
        save_wip_snapshot()でスナップショットとして保存する（後続の「仕掛展開」
        機能の入力データ用）。

        以前はself.report_rows自体（kitting_list_no＝バッチ単位）のsurplus_qtyを
        そのまま抽出していたが、複数バッチを持つfile_noの仕掛数量が正しく合算
        されない問題があったため、file_no単位の合算ロジック
        （build_wip_extraction_rows()）に置き換えた。面1省略・代表バッチの選定
        （plan_start_datetimeが最も新しいバッチを採用）もこちらに集約されている
        （詳細はbuild_wip_extraction_rows()のdocstring参照）。

        save_wip_snapshot()はテーブル全体差し替え方式のため、押すたびに
        直前の抽出結果が今回の内容で完全に置き換わる（前回の集計期間で
        仕掛だった基板が、今回の期間の集計結果に含まれなければ残らない）。
        """
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return

        lot_nos = sorted({row["lot_no"] for row in self.report_rows if row["lot_no"]})
        wip_rows = build_wip_extraction_rows(lot_nos)
        save_wip_snapshot(wip_rows)

        messagebox.showinfo(
            "完了", f"{len(wip_rows)}件の仕掛基板をスナップショットに保存しました。",
            parent=self.winfo_toplevel(),
        )
