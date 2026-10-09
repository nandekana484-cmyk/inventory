"""
日報・月報を統合した実績レポート画面。期間（今日/今週/今月/カスタム）を選び、build_monthly_report(from, to) で集計する。
警告5種のオン/オフ・行の絞り込み・列の表示/非表示・CSV/PDF 出力・仕掛数量抽出を持つ。
設計と経緯は docs/domain/unified_report.md。
"""
import csv
import os
import tempfile
from datetime import datetime, timedelta

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from services.production_service import build_monthly_report, build_wip_extraction_rows, evaluate_lot_status
from ui.daily_report_window import (
    REPORT_HEADERS,
    _row_to_values,
    build_daily_report_pdf,
    ReportPreviewWindow,
    configure_lot_stripe_tags,
    configure_status_color_tags,
    populate_report_tree,
    PDF_COL_WIDTHS,
    PDF_WRAP_COLUMN_INDICES,
)
from models.wip_board_snapshot import save_wip_snapshot
from models.operation_log import log_operation
from ui.lot_progress_window import STATUS_LABELS
from ui.window_utils import center_window

PERIOD_OPTIONS = ["今日", "今週", "今月", "カスタム範囲"]

PERIOD_LABEL_TO_KEY = {
    "今日": "today",
    "今週": "this_week",
    "今月": "this_month",
    "カスタム範囲": "custom",
}

# ソートで数値として比べる列。board_count には「未登録」が入りうるので、sort_key() で変換の失敗に備える。
_NUMERIC_COLUMNS = {"seq", "board_count", "daily_qty", "order_qty", "lot_completed", "surplus_qty", "lot_remaining"}


def compute_period_dates(period_key: str, today: datetime = None):
    """
    period_key（today/this_week/this_month）から開始日・終了日を返す（custom は None）。
    週は月曜から。終了日は期間の終わりではなく今日にする（まだ来ていない日付をタイトルに出して誤解させないため。実績は今日までしか無い）。
    """
    today = today or datetime.now()
    today_date = today.date()

    if period_key == "today":
        return today_date, today_date
    elif period_key == "this_week":
        start = today_date - timedelta(days=today_date.weekday())  # 月曜起算
        return start, today_date
    elif period_key == "this_month":
        start = today_date.replace(day=1)
        return start, today_date
    else:
        return None, None


class UnifiedReportWindow(tk.Toplevel):
    """
    実績レポート画面（日報・月報の統合）。期間に応じて build_monthly_report() を呼び、一覧・印刷・PDF/CSV 出力・仕掛数量抽出・警告の CSV 出力を行う。
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
        self.board_count_inconsistency_warnings = []

        # 警告5種のオン/オフ。既定はすべてオン（月報は常に全部出していた挙動を変えないため）。
        self.warning_toggle_vars = {
            "inconsistency": tk.BooleanVar(value=True),
            "order_qty_inconsistency": tk.BooleanVar(value=True),
            "unregistered_board": tk.BooleanVar(value=True),
            "excess_file_no": tk.BooleanVar(value=True),
            "board_count_inconsistency": tk.BooleanVar(value=True),
        }

        self.title("実績レポート")
        self.geometry("1020x560")
        center_window(self, parent)

        period_frame = ttk.LabelFrame(self, text="集計期間", padding=10)
        period_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(period_frame, text="期間：").pack(side=tk.LEFT, padx=5)
        self.period_var = tk.StringVar(value="今日")
        self.period_combo = ttk.Combobox(
            period_frame, textvariable=self.period_var, values=PERIOD_OPTIONS,
            state="readonly", width=12,
        )
        self.period_combo.pack(side=tk.LEFT, padx=5)
        self.period_combo.bind("<<ComboboxSelected>>", self.on_period_type_changed)

        ttk.Label(period_frame, text="開始日：").pack(side=tk.LEFT, padx=(15, 5))
        self.from_date_entry = DateEntry(period_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.from_date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Label(period_frame, text="終了日：").pack(side=tk.LEFT, padx=5)
        self.to_date_entry = DateEntry(period_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.to_date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(period_frame, text="表示", command=self.on_display).pack(side=tk.LEFT, padx=10)

        # 警告のオン/オフはダイアログの表示だけを切り替える（計算は軽いので常に全部行う）。警告の CSV 出力ボタンは、これと関係なく常に使える。
        warning_frame = ttk.LabelFrame(self, text="警告表示（オン/オフ）", padding=10)
        warning_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        ttk.Checkbutton(
            warning_frame, text="面1/面2不整合", variable=self.warning_toggle_vars["inconsistency"],
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            warning_frame, text="発注数不一致", variable=self.warning_toggle_vars["order_qty_inconsistency"],
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            warning_frame, text="マスタ未登録", variable=self.warning_toggle_vars["unregistered_board"],
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            warning_frame, text="構成基板数超過", variable=self.warning_toggle_vars["excess_file_no"],
        ).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(
            warning_frame, text="構成基板数不一致", variable=self.warning_toggle_vars["board_count_inconsistency"],
        ).pack(side=tk.LEFT, padx=5)

        # 行の絞り込み（NG一覧・仕掛一覧と同じ「テキスト部分一致＋チェックボックス式」）。
        filter_frame = ttk.LabelFrame(self, text="絞り込み", padding=8)
        filter_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        ttk.Label(filter_frame, text="ロットNo.:").pack(side=tk.LEFT, padx=(5, 2))
        self.lot_no_filter_var = tk.StringVar()
        lot_no_entry = ttk.Entry(filter_frame, textvariable=self.lot_no_filter_var, width=14)
        lot_no_entry.pack(side=tk.LEFT, padx=(0, 10))
        lot_no_entry.bind("<KeyRelease>", lambda e: self.apply_filters())

        self._filter_labels = {"status_label": "状態", "ng_label": "NGの有無"}
        self._checkbox_filters = {}
        self._checkbox_buttons = {}
        self._add_checkbox_filter_button(filter_frame, "status_label")
        self._add_checkbox_filter_button(filter_frame, "ng_label")

        ttk.Button(filter_frame, text="絞り込みクリア", command=self.clear_filters).pack(side=tk.LEFT, padx=(15, 0))

        # 一覧表示エリア（日報・月報と同一列構成）
        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        # report_date は REPORT_HEADERS と同じ位置（「確認事項」の直前）に置くこと
        # （見出しは _cols と REPORT_HEADERS を zip で対応させるので、ずれると見出しが1列ずつずれる）。
        self._cols = ("seq", "file_no", "board_name", "board_count", "lot_no", "daily_qty", "order_qty",
                      "lot_completed", "surplus_qty", "lot_remaining", "report_date")
        headers = dict(zip(self._cols, REPORT_HEADERS))
        widths = {
            "seq": 50, "file_no": 100, "board_name": 160, "board_count": 80, "lot_no": 110,
            "daily_qty": 80, "order_qty": 80,
            "lot_completed": 80, "surplus_qty": 80, "lot_remaining": 80, "report_date": 90,
        }
        left_aligned = {"file_no", "board_name", "board_count", "lot_no", "report_date"}
        self._col_headers = headers

        self.tree = ttk.Treeview(tree_frame, columns=self._cols, show="headings")
        self._sort_states = {}
        for c in self._cols:
            self.tree.heading(c, text=headers[c], command=lambda c=c: self.sort_by_column(c))
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Double-1>", self.on_row_double_click)
        # 両方必ず呼ぶ（旧日報は configure_status_color_tags() を呼んでおらず、確認事項の色が出なかった）。
        configure_lot_stripe_tags(self.tree)
        configure_status_color_tags(self.tree)

        # 列の表示/非表示（ヘッダーの右クリック）。displaycolumns だけを変えるので、CSV/PDF 出力は常に全列。
        self._column_visible_vars = {c: tk.BooleanVar(value=True) for c in self._cols}
        self.tree.bind("<Button-3>", self._on_tree_header_right_click)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="印刷", command=self.on_print).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)
        # 以下4つのボタンは、旧月報画面にだけあった機能。
        ttk.Button(btn_frame, text="仕掛数量抽出", command=self.on_extract_wip).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            btn_frame, text="マスタ未登録リストをCSV出力", command=self.on_export_unregistered_board_csv,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            btn_frame, text="構成基板数超過リストをCSV出力", command=self.on_export_excess_file_no_csv,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            btn_frame, text="構成基板数不一致リストをCSV出力", command=self.on_export_board_count_inconsistency_csv,
        ).pack(side=tk.LEFT, padx=5)

        # 初期表示は「今日」で集計する（旧日報・月報も、開いた直後に一覧を出していた）。
        self._apply_period_preset("today")
        self.on_display()

    def _apply_period_preset(self, period_key: str):
        """
        today/this_week/this_month なら開始日・終了日を入れて読み取り専用にする。custom なら編集可能に戻すだけで、日付は変えない（直前の範囲を残す）。
        """
        if period_key == "custom":
            self.from_date_entry.config(state="normal")
            self.to_date_entry.config(state="normal")
            return

        start, end = compute_period_dates(period_key)
        self.from_date_entry.config(state="normal")
        self.to_date_entry.config(state="normal")
        self.from_date_entry.set_date(start)
        self.to_date_entry.set_date(end)
        self.from_date_entry.config(state="readonly")
        self.to_date_entry.config(state="readonly")

    def on_period_type_changed(self, event=None):
        period_key = PERIOD_LABEL_TO_KEY[self.period_var.get()]
        self._apply_period_preset(period_key)

    def on_display(self):
        from_date = self.from_date_entry.get()
        to_date = self.to_date_entry.get()

        try:
            (
                self.report_rows, self.inconsistency_warnings,
                self.order_qty_inconsistency_warnings, self.unregistered_board_warnings,
                self.excess_file_no_warnings, self.board_count_inconsistency_warnings,
            ) = build_monthly_report(from_date, to_date)
        except Exception as e:
            messagebox.showerror("エラー", f"集計に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        self.from_date = from_date
        self.to_date = to_date
        self.title(f"実績レポート（{self._period_label()}）")

        self._annotate_filter_fields(self.report_rows)
        self.clear_filters()  # 絞り込み条件をリセットした上で再描画する

        # 警告は常に5種とも計算し、表示するかどうかだけをチェックボックスに従う。
        if self.warning_toggle_vars["inconsistency"].get():
            self._show_inconsistency_warning_if_any()
        if self.warning_toggle_vars["order_qty_inconsistency"].get():
            self._show_order_qty_inconsistency_warning_if_any()
        if self.warning_toggle_vars["unregistered_board"].get():
            self._show_unregistered_board_warning_if_any()
        if self.warning_toggle_vars["excess_file_no"].get():
            self._show_excess_file_no_warning_if_any()
        if self.warning_toggle_vars["board_count_inconsistency"].get():
            self._show_board_count_inconsistency_warning_if_any()

    # ------------------------------------------------------------------
    # 警告ダイアログ
    # ------------------------------------------------------------------

    def _show_order_qty_inconsistency_warning_if_any(self):
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
        """面1・面2の不整合の警告。ロットNo・ファイルNoごとの合計で比べた結果と、面1・面2の計画Noを表示する（D-111）。"""
        if not self.inconsistency_warnings:
            return
        lines = "\n".join(
            f"・lot_no={w['lot_no']}（setup_file_no={w['setup_file_no']}）：\n"
            f"　　面1合計={w['side1_total']:.0f} ＞ 面2合計={w['side2_total']:.0f}"
            f"（差 {w['diff']:+.0f}）\n"
            f"　　面1の計画No：{'、'.join(w['side1_kitting_list_nos'])}\n"
            f"　　面2の計画No：{'、'.join(w['side2_kitting_list_nos'])}"
            for w in self.inconsistency_warnings
        )
        messagebox.showwarning(
            "実績不整合の警告",
            "以下のロット・ファイルNoで面1・面2の実績に不整合があります（面1の実績の"
            "合計が面2の合計を上回っています）。実績修正画面（ActualCorrectionWindow）"
            "での片面のみの修正・削除が原因の可能性があります。\n"
            "該当する面1の行は、通常表示されるはずの面2省略ルールにより一覧からは"
            "除外されていますが、内容のご確認をお願いします。\n\n"
            f"{lines}",
            parent=self.winfo_toplevel(),
        )

    def _show_unregistered_board_warning_if_any(self):
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
            "チェックを行えませんでした。マスタへの登録をご検討ください。\n\n"
            f"{lines}",
            parent=self.winfo_toplevel(),
        )

    def _show_excess_file_no_warning_if_any(self):
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
            "可能性がありますので、ご確認ください。\n\n"
            f"{lines}",
            parent=self.winfo_toplevel(),
        )

    def _show_board_count_inconsistency_warning_if_any(self):
        if not self.board_count_inconsistency_warnings:
            return
        lines = "\n".join(
            f"・lot_no={w['lot_no']}（基板名: {w['board_name']}、"
            f"構成基板数: {w['board_count']:g}、"
            f"ロット内の値一覧: {', '.join(f'{v:g}' for v in w['board_count_values'])}）"
            for w in self.board_count_inconsistency_warnings
        )
        messagebox.showwarning(
            "構成基板数不一致の警告",
            "以下のロットは、同一ロット内の基板名ごとに構成基板数マスタの"
            "登録値が異なっています。どちらの値を基準にすべきか判断できない"
            "ため、構成基板数の比較・自動判定は行っていません。マスタの"
            "登録内容をご確認ください。\n\n"
            f"{lines}",
            parent=self.winfo_toplevel(),
        )

    # ------------------------------------------------------------------

    def on_row_double_click(self, event):
        """
        選択した行の実績を、実績修正画面で開く。循環 import を避けるため、ここで import する。
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
        """
        実績修正の後に一覧を読み直す。再表示する警告は面1・面2の不整合だけ（ほかは片面の修正では変わらない）。
        """
        if not self.from_date or not self.to_date:
            return
        (
            self.report_rows, self.inconsistency_warnings,
            self.order_qty_inconsistency_warnings, self.unregistered_board_warnings,
            self.excess_file_no_warnings, self.board_count_inconsistency_warnings,
        ) = build_monthly_report(self.from_date, self.to_date)
        self._annotate_filter_fields(self.report_rows)
        # 実績の修正に伴う読み直しなので、on_display() と違い絞り込みはそのまま残す。
        self.apply_filters()
        if self.warning_toggle_vars["inconsistency"].get():
            self._show_inconsistency_warning_if_any()

    def sort_by_column(self, col):
        """
        列ヘッダーのクリックで、ロット単位のまとまりを保ったまま並べ替える。
        同じ lot_no の行は連続しているとは限らない（実績の順に並び、「未確定」行は末尾にまとめて足される）ので、
        連続した行ではなく、lot_no が同じ行をすべて1つのまとまりにする（まとまりの位置は、その lot_no が最初に出た位置）。
        """
        ascending = self._sort_states.get(col, True)

        def sort_key(value):
            if col in _NUMERIC_COLUMNS:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

        children = list(self.tree.get_children(""))
        blocks_by_lot = {}
        lot_order = []
        for iid in children:
            lot_no = self.tree.set(iid, "lot_no")
            if lot_no not in blocks_by_lot:
                blocks_by_lot[lot_no] = []
                lot_order.append(lot_no)
            blocks_by_lot[lot_no].append(iid)
        blocks = [blocks_by_lot[lot_no] for lot_no in lot_order]

        def block_sort_key(block):
            return sort_key(self.tree.set(block[0], col))

        blocks.sort(key=block_sort_key, reverse=not ascending)

        index = 0
        for block in blocks:
            for iid in block:
                self.tree.move(iid, "", index)
                index += 1

        self._sort_states[col] = not ascending

    # ------------------------------------------------------------------
    # 行の絞り込み
    # ------------------------------------------------------------------

    def _annotate_filter_fields(self, rows):
        """各行に、絞り込み用の表示キー（status_label・ng_label）を足す（CSV/PDF 出力には使わない）。"""
        for row in rows:
            row["status_label"] = STATUS_LABELS.get(row.get("status"), row.get("status") or "判定不能")
            row["ng_label"] = "あり" if row.get("has_ng") else "なし"

    def _add_checkbox_filter_button(self, parent, col_key):
        label_text = self._filter_labels[col_key]
        button = tk.Button(
            parent, text=f"{label_text} ▼", relief=tk.RAISED,
            command=lambda c=col_key: self.open_checkbox_filter_popup(c),
        )
        button.pack(side=tk.LEFT, padx=(5, 5))
        self._checkbox_buttons[col_key] = button
        self._checkbox_default_bg = button.cget("background")

    def _update_filter_button_style(self, col_key):
        button = self._checkbox_buttons.get(col_key)
        if button is None:
            return
        label_text = self._filter_labels[col_key]
        active = col_key in self._checkbox_filters
        button.configure(
            text=f"{label_text} ▼●" if active else f"{label_text} ▼",
            background="#cfe8ff" if active else self._checkbox_default_bg,
        )

    def open_checkbox_filter_popup(self, col_key):
        """状態・NGの有無 のチェックボックス式絞り込みポップアップを開く（仕掛一覧と同じ設計）。"""
        label_text = self._filter_labels[col_key]

        other_predicates = self._filter_predicates()
        other_predicates.pop(col_key, None)
        if other_predicates:
            base_rows = [r for r in self.report_rows if self._row_matches(r, other_predicates)]
        else:
            base_rows = self.report_rows

        full_values = sorted({str(r[col_key]) for r in base_rows})

        current_selection = self._checkbox_filters.get(col_key)
        checked_values = set(full_values) if current_selection is None else set(current_selection)

        if self.state() == "iconic":
            self.deiconify()

        popup = tk.Toplevel(self)
        popup.title(f"{label_text} の絞り込み")
        popup.geometry("260x380")
        center_window(popup, self)
        popup.transient(self)
        popup.grab_set()

        list_outer = ttk.Frame(popup)
        list_outer.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

        canvas = tk.Canvas(list_outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
        checklist_frame = ttk.Frame(canvas)
        checklist_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=checklist_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        check_vars = {value: tk.BooleanVar(value=(value in checked_values)) for value in full_values}
        for value in full_values:
            ttk.Checkbutton(checklist_frame, text=value, variable=check_vars[value]).pack(anchor=tk.W, fill=tk.X)

        btn_frame1 = ttk.Frame(popup)
        btn_frame1.pack(fill=tk.X, padx=10, pady=(0, 5))

        def select_all():
            for var in check_vars.values():
                var.set(True)

        def deselect_all():
            for var in check_vars.values():
                var.set(False)

        ttk.Button(btn_frame1, text="全選択", command=select_all).pack(side=tk.LEFT)
        ttk.Button(btn_frame1, text="全解除", command=deselect_all).pack(side=tk.LEFT, padx=(5, 0))

        btn_frame2 = ttk.Frame(popup)
        btn_frame2.pack(fill=tk.X, padx=10, pady=(0, 10))

        def on_ok():
            selected = {value for value, var in check_vars.items() if var.get()}
            if selected == set(full_values):
                self._checkbox_filters.pop(col_key, None)
            else:
                self._checkbox_filters[col_key] = selected
            self._update_filter_button_style(col_key)
            popup.destroy()
            self.apply_filters()

        def on_cancel():
            popup.destroy()

        ttk.Button(btn_frame2, text="OK", command=on_ok).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        ttk.Button(btn_frame2, text="キャンセル", command=on_cancel).pack(side=tk.LEFT, expand=True, fill=tk.X)

        return popup

    def _filter_predicates(self):
        predicates = {}
        lot_no_text = self.lot_no_filter_var.get().strip()
        if lot_no_text:
            needle = lot_no_text.lower()
            predicates["lot_no"] = lambda value, needle=needle: needle in str(value).lower()

        for col_key, selected_values in self._checkbox_filters.items():
            predicates[col_key] = lambda value, selected=selected_values: str(value) in selected

        return predicates

    def _row_matches(self, row, predicates):
        for col_key, predicate in predicates.items():
            if not predicate(row.get(col_key)):
                return False
        return True

    def apply_filters(self, event=None):
        predicates = self._filter_predicates()
        if not predicates:
            filtered = self.report_rows
        else:
            filtered = [r for r in self.report_rows if self._row_matches(r, predicates)]

        for item in self.tree.get_children():
            self.tree.delete(item)
        populate_report_tree(self.tree, filtered)

    def clear_filters(self):
        self.lot_no_filter_var.set("")
        self._checkbox_filters.clear()
        for col_key in self._checkbox_buttons:
            self._update_filter_button_style(col_key)
        self.apply_filters()

    # ------------------------------------------------------------------
    # 列の表示/非表示
    # ------------------------------------------------------------------

    def _on_tree_header_right_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "heading":
            return

        menu = tk.Menu(self, tearoff=0)
        for col in self._cols:
            menu.add_checkbutton(
                label=self._col_headers[col],
                variable=self._column_visible_vars[col],
                command=lambda c=col: self._toggle_column_visibility(c),
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _toggle_column_visibility(self, col):
        visible_cols = [c for c in self._cols if self._column_visible_vars[c].get()]
        if not visible_cols:
            # 全列非表示は許可しない（最低1列は残す）。チェックを外した
            # 直後のcolを強制的に戻す。
            self._column_visible_vars[col].set(True)
            visible_cols = [col]
        self.tree["displaycolumns"] = visible_cols

    def _period_label(self):
        if self.from_date == self.to_date:
            return self.from_date
        return f"{self.from_date} ～ {self.to_date}"

    def _file_stub(self):
        if self.from_date == self.to_date:
            return self.from_date.replace("-", "")
        return f"{self.from_date.replace('-', '')}_{self.to_date.replace('-', '')}"

    def on_preview(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "対象期間の実績データがありません。", parent=self.winfo_toplevel())
            return
        ReportPreviewWindow(self, self.report_rows, self._period_label(), title_prefix="実績レポート")

    def on_print(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "対象期間の実績データがありません。", parent=self.winfo_toplevel())
            return

        tmp_path = os.path.join(tempfile.gettempdir(), f"unified_report_{self._file_stub()}_print.pdf")
        try:
            build_daily_report_pdf(
                self.report_rows, self._period_label(), tmp_path, title_prefix="実績レポート",
                col_widths=PDF_COL_WIDTHS, wrap_column_indices=PDF_WRAP_COLUMN_INDICES,
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
            messagebox.showwarning("警告", "対象期間の実績データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"unified_report_{self._file_stub()}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")], parent=self.winfo_toplevel(),
        )
        if not save_path:
            return

        try:
            build_daily_report_pdf(
                self.report_rows, self._period_label(), save_path, title_prefix="実績レポート",
                col_widths=PDF_COL_WIDTHS, wrap_column_indices=PDF_WRAP_COLUMN_INDICES,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "対象期間の実績データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"unified_report_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")], parent=self.winfo_toplevel(),
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
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    # ------------------------------------------------------------------
    # 仕掛数量抽出・警告の CSV 出力（旧月報画面から移した機能）
    # 対象のロットは選択中の期間の行から決めるが、各ロットの引落・仕掛は期間に関係なく、今の実績・計画・マスタから計算する。
    # ------------------------------------------------------------------

    def on_extract_wip(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "先に集計を実行してください。", parent=self.winfo_toplevel())
            return

        lot_nos = sorted({row["lot_no"] for row in self.report_rows if row["lot_no"]})

        needs_review_lot_nos = []
        for lot_no in lot_nos:
            try:
                lot_eval = evaluate_lot_status(lot_no)
            except ValueError:
                continue
            if lot_eval["status_color_category"] == "needs_review":
                needs_review_lot_nos.append(lot_no)

        if needs_review_lot_nos:
            preview = "、".join(needs_review_lot_nos[:10])
            if len(needs_review_lot_nos) > 10:
                preview += f" 他{len(needs_review_lot_nos) - 10}件"
            proceed = messagebox.askyesno(
                "仕掛数量抽出の確認",
                f"抽出対象に、構成基板数の確認・修正が必要な状態のロットが"
                f"{len(needs_review_lot_nos)}件含まれています（{preview}）。\n"
                "これらのロットは引落が未検証のまま仕掛数量を算出しています。\n"
                "このまま保存しますか？",
                parent=self.winfo_toplevel(),
            )
            if not proceed:
                return

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

    def on_export_unregistered_board_csv(self):
        if not self.unregistered_board_warnings:
            messagebox.showinfo(
                "マスタ未登録リスト", "マスタ未登録のロットはありません。", parent=self.winfo_toplevel(),
            )
            return

        default_name = f"unregistered_board_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")], parent=self.winfo_toplevel(),
        )
        if not save_path:
            return

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
        if not self.excess_file_no_warnings:
            messagebox.showinfo(
                "構成基板数超過リスト", "構成基板数が超過しているロットはありません。",
                parent=self.winfo_toplevel(),
            )
            return

        default_name = f"excess_file_no_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")], parent=self.winfo_toplevel(),
        )
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

    def on_export_board_count_inconsistency_csv(self):
        if not self.board_count_inconsistency_warnings:
            messagebox.showinfo(
                "構成基板数不一致リスト", "構成基板数が不一致のロットはありません。",
                parent=self.winfo_toplevel(),
            )
            return

        default_name = f"board_count_inconsistency_{self._file_stub()}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")], parent=self.winfo_toplevel(),
        )
        if not save_path:
            return

        grouped = {}
        for w in self.board_count_inconsistency_warnings:
            entry = grouped.setdefault(
                w["board_name"], {"lot_nos": [], "file_nos": set(), "board_count": w["board_count"], "value_sets": []},
            )
            entry["lot_nos"].append(w["lot_no"])
            entry["file_nos"].update(w["file_nos"])
            values_str = ", ".join(f"{v:g}" for v in w["board_count_values"])
            if values_str not in entry["value_sets"]:
                entry["value_sets"].append(values_str)

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["board_name", "lot_nos", "file_nos", "board_count", "board_count_values"])
                for board_name, entry in grouped.items():
                    writer.writerow([
                        board_name,
                        ", ".join(entry["lot_nos"]),
                        ", ".join(sorted(entry["file_nos"])),
                        f"{entry['board_count']:g}",
                        "; ".join(entry["value_sets"]),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())
