# ui/monthly_report_window.py
import csv
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from services.production_service import build_monthly_report, build_wip_extraction_rows
from ui.daily_report_window import (
    REPORT_HEADERS,
    _row_to_values,
    build_daily_report_pdf,
    ReportPreviewWindow,
    configure_lot_stripe_tags,
    populate_report_tree,
)
from models.wip_board_snapshot import save_wip_snapshot
from models.operation_log import log_operation


class MonthlyReportWindow(tk.Toplevel):
    """
    任意の期間（開始日～終了日）を指定して集計する月報ウィンドウ。
    列構成・印刷プレビュー・PDF/CSV出力ロジックは日報（DailyReportWindow）と共通。
    """
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker or {}
        self.from_date = None
        self.to_date = None
        self.report_rows = []
        self.inconsistency_warnings = []
        self.order_qty_inconsistency_warnings = []
        self.unregistered_board_warnings = []
        self.excess_file_no_warnings = []

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

        cols = ("seq", "file_no", "board_name", "board_count", "lot_no", "daily_qty", "order_qty",
                "lot_completed", "surplus_qty", "lot_remaining")
        headers = dict(zip(cols, REPORT_HEADERS))
        widths = {
            "seq": 50, "file_no": 100, "board_name": 160, "board_count": 80, "lot_no": 110,
            "daily_qty": 80, "order_qty": 80,
            "lot_completed": 80, "surplus_qty": 80, "lot_remaining": 80,
        }
        left_aligned = {"file_no", "board_name", "board_count", "lot_no"}

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)
        configure_lot_stripe_tags(self.tree)
        self.tree.bind("<Double-1>", self.on_row_double_click)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="仕掛数量抽出", command=self.on_extract_wip).pack(side=tk.LEFT, padx=5)
        # マスタ未登録リストのCSV出力（2026-09-26追加）。集計を実行していない
        # （self.unregistered_board_warningsが空の）状態でも押せるが、その場合は
        # on_export_unregistered_board_csv()内で「対象がありません」を案内する
        # （ui.production_import_staging_window.on_export_mismatched_csv()と
        # 同じ、常時有効＋空なら案内メッセージという方針を踏襲）。
        ttk.Button(
            btn_frame, text="マスタ未登録リストをCSV出力", command=self.on_export_unregistered_board_csv,
        ).pack(side=tk.LEFT, padx=5)
        # 構成基板数超過リストのCSV出力（2026-09-26追加、逆方向の不整合：実際の
        # file_no数がマスタの構成基板数を上回るケース）。上記と同じ「常時有効＋
        # 空なら案内メッセージ」の方針を踏襲する。
        ttk.Button(
            btn_frame, text="構成基板数超過リストをCSV出力", command=self.on_export_excess_file_no_csv,
        ).pack(side=tk.LEFT, padx=5)

    def on_aggregate(self):
        from_date = self.from_date_entry.get()
        to_date = self.to_date_entry.get()

        try:
            (
                self.report_rows, self.inconsistency_warnings,
                self.order_qty_inconsistency_warnings, self.unregistered_board_warnings,
                self.excess_file_no_warnings,
            ) = build_monthly_report(from_date, to_date)
        except Exception as e:
            messagebox.showerror("エラー", f"集計に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        self.from_date = from_date
        self.to_date = to_date
        self.title(f"月報出力（{from_date} ～ {to_date}）")

        for item in self.tree.get_children():
            self.tree.delete(item)
        populate_report_tree(self.tree, self.report_rows)

        self._show_inconsistency_warning_if_any()
        self._show_order_qty_inconsistency_warning_if_any()
        self._show_unregistered_board_warning_if_any()
        self._show_excess_file_no_warning_if_any()

    def _show_order_qty_inconsistency_warning_if_any(self):
        """
        self.order_qty_inconsistency_warnings（発注数(order_qty)がファイルNo間で
        一致していないロット）を警告する。_show_inconsistency_warning_if_any()
        （面1/面2の実績不整合警告）と同じパターン：一覧の表示自体は変更せず、
        集計完了後に別途ダイアログで注意喚起するのみ。

        2026-09-26変更：以前はこのウィンドウ独自の_collect_order_qty_
        inconsistencies()が、report_rowsのdistinctなlot_noに対しcalculate_lot_
        completion()を再度呼び出して同じ情報を求めていたが、services.
        production_service._build_report_rows()が既にlot_completion_cacheを
        使って同じ判定を行っている（order_qty_inconsistent、_compute_lot_
        completion()参照）ため、build_monthly_report()の戻り値をそのまま
        self.order_qty_inconsistency_warningsとして使うよう変更し、二重の
        DB問い合わせを解消した。
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

    def _show_unregistered_board_warning_if_any(self):
        """
        self.unregistered_board_warnings（構成基板数マスタ（models.
        board_structure_master）にboard_nameが未登録のため、構成基板数チェック
        自体を行えなかったロット）を警告する（2026-09-26追加）。他の2種類の
        警告と同じパターンだが、こちらは「異常」ではなく「マスタ整備待ち」の
        可能性が高いため、文言は指摘ではなく登録を促す形にしている。

        ダイアログ本文末尾に、CSV出力ボタン（on_export_unregistered_board_csv()、
        「マスタ未登録リストをCSV出力」）への導線を明記する（2026-09-26追加）。
        ダイアログはボタン操作可能なノンモーダル…ではなくmessagebox.showwarning
        （モーダル）のため、ダイアログを閉じた後にボタンを押す流れになる旨を
        文言で案内する。
        """
        if not self.unregistered_board_warnings:
            return

        lines = "\n".join(
            f"・lot_no={w['lot_no']}（基板名: {w['board_name']}、"
            f"file_no: {', '.join(w['file_nos']) if w['file_nos'] else '-'}）"
            for w in self.unregistered_board_warnings
        )
        messagebox.showwarning(
            "構成基板数マスタ未登録の警告",
            "以下のロットは基板名が構成基板数マスタに未登録のため、構成基板数の"
            "チェックを行えませんでした。マスタへの登録をご検討ください。\n"
            "このダイアログを閉じた後、画面下部の「マスタ未登録リストをCSV出力」"
            "ボタンから一覧をCSVで保存できます。\n\n"
            f"{lines}",
            parent=self.winfo_toplevel(),
        )

    def _show_excess_file_no_warning_if_any(self):
        """
        self.excess_file_no_warnings（構成基板数チェックの逆方向の不整合：
        実際のfile_no数がマスタの構成基板数を上回るロット）を警告する
        （2026-09-26追加）。self.unregistered_board_warnings（マスタ未登録＝
        チェック自体を行えなかった）とは意味が異なる別カテゴリの警告であり、
        混同しないよう別メソッド・別ダイアログ・別CSV出力ボタンとして扱う
        （マスタ未登録は「情報不足で判定不能」、こちらは「判定した結果、実際に
        矛盾がある」という違いがある）。

        矛盾の原因は、マスタ側の登録漏れ（本来の構成基板数より少ない値が
        登録されている）・計画側の重複登録等が考えられるが、本関数では
        原因の特定は行わず、事実（マスタの構成基板数・実際のfile_no一覧）の
        提示に留める。
        """
        if not self.excess_file_no_warnings:
            return

        lines = "\n".join(
            f"・lot_no={w['lot_no']}（基板名: {w['board_name']}、"
            f"マスタの構成基板数: {w['board_count']:g}、"
            f"実際のfile_no: {', '.join(w['file_nos']) if w['file_nos'] else '-'}）"
            for w in self.excess_file_no_warnings
        )
        messagebox.showwarning(
            "構成基板数超過の警告",
            "以下のロットは、実際のファイルNo数が構成基板数マスタの登録値を"
            "上回っています。マスタ側の登録漏れ、または計画データ側の重複等の"
            "可能性がありますので、ご確認ください。\n"
            "このダイアログを閉じた後、画面下部の「構成基板数超過リストをCSV出力」"
            "ボタンから一覧をCSVで保存できます。\n\n"
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
        (
            self.report_rows, self.inconsistency_warnings,
            self.order_qty_inconsistency_warnings, self.unregistered_board_warnings,
            self.excess_file_no_warnings,
        ) = build_monthly_report(self.from_date, self.to_date)
        for item in self.tree.get_children():
            self.tree.delete(item)
        populate_report_tree(self.tree, self.report_rows)

        # ActualCorrectionWindowでの片面修正直後はここが呼ばれるため、まさに
        # 不整合が新たに発生し得るタイミング。on_aggregate()と同様に警告する
        # （発注数不一致・マスタ未登録は片面修正では変化し得ないため、従来通り
        # 面1/面2不整合のみ再表示する）。
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

    def on_export_unregistered_board_csv(self):
        """
        self.unregistered_board_warnings（構成基板数マスタ未登録のロット一覧、
        1ロット1件）を、board_name単位に集約してCSV出力する（2026-09-26追加、
        後日board_name単位への集約に変更）。ui.production_import_staging_window.
        on_export_mismatched_csv()と同じ形式（utf-8-sig、ファイル選択ダイアログ、
        成功・失敗のmessagebox）を踏襲する。

        集約方法：同じboard_nameを持つ全warningのlot_noを収集順（出現順）で、
        file_nosは全warning分を合わせた重複無しの集合をソートして1セルに
        カンマ区切りでまとめる。1つのboard_nameが複数ロットにまたがる
        （マスタ未登録の原因はboard_name自体にあり、ロットごとに繰り返し
        警告されても実質同じ対応（マスタ登録）で解消するため、対応単位で
        あるboard_name単位の一覧の方が実務上扱いやすいとの判断）。

        列構成：board_name, lot_nos（該当する全ロットNo.、カンマ区切り）,
        file_nos（該当する全ファイルNo.の重複無しリスト、カンマ区切り）。
        """
        if not self.unregistered_board_warnings:
            messagebox.showinfo(
                "マスタ未登録リスト", "マスタ未登録のロットはありません。", parent=self.winfo_toplevel(),
            )
            return

        default_name = f"unregistered_board_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        # board_name単位への集約。dictのキー挿入順を利用して、CSV出力順が
        # self.unregistered_board_warningsの出現順（board_nameの初出順）に
        # なるようにする。
        grouped = {}
        for w in self.unregistered_board_warnings:
            entry = grouped.setdefault(w["board_name"], {"lot_nos": [], "file_nos": set()})
            entry["lot_nos"].append(w["lot_no"])
            entry["file_nos"].update(w["file_nos"])

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["board_name", "lot_nos", "file_nos"])
                for board_name, entry in grouped.items():
                    writer.writerow([
                        board_name,
                        ", ".join(entry["lot_nos"]),
                        ", ".join(sorted(entry["file_nos"])),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_excess_file_no_csv(self):
        """
        self.excess_file_no_warnings（構成基板数チェックの逆方向の不整合：
        実際のfile_no数がマスタの構成基板数を上回るロット一覧、1ロット1件）を、
        on_export_unregistered_board_csv()と一貫性を持たせた形（board_name単位
        への集約、utf-8-sig、ファイル選択ダイアログ、成功・失敗のmessagebox）で
        CSV出力する（2026-09-26追加）。

        列構成：board_name, lot_nos（該当する全ロットNo.、カンマ区切り）,
        file_nos（該当する全ファイルNo.の重複無しリスト、カンマ区切り）,
        board_count（マスタの構成基板数）。board_countはboard_name単位で
        マスタから引いた値であり、同一board_nameの複数warning間で値が
        変わることは無いため、最初に出現した値をそのまま採用する。
        """
        if not self.excess_file_no_warnings:
            messagebox.showinfo(
                "構成基板数超過リスト", "構成基板数が超過しているロットはありません。",
                parent=self.winfo_toplevel(),
            )
            return

        default_name = f"excess_file_no_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        grouped = {}
        for w in self.excess_file_no_warnings:
            entry = grouped.setdefault(
                w["board_name"], {"lot_nos": [], "file_nos": set(), "board_count": w["board_count"]},
            )
            entry["lot_nos"].append(w["lot_no"])
            entry["file_nos"].update(w["file_nos"])

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["board_name", "lot_nos", "file_nos", "board_count"])
                for board_name, entry in grouped.items():
                    writer.writerow([
                        board_name,
                        ", ".join(entry["lot_nos"]),
                        ", ".join(sorted(entry["file_nos"])),
                        f"{entry['board_count']:g}",
                    ])
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
        log_operation(
            self.current_worker.get("name", "unknown"),
            "仕掛数量抽出",
            detail=f"{len(wip_rows)}件",
        )

        messagebox.showinfo(
            "完了", f"{len(wip_rows)}件の仕掛基板をスナップショットに保存しました。",
            parent=self.winfo_toplevel(),
        )
