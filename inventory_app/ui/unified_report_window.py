# ui/unified_report_window.py
"""
日報（旧DailyReportWindow）・月報（旧MonthlyReportWindow）を1つの画面に
統合したもの。

経緯：
- 旧DailyReportWindowは、起動直後（build_daily_report()）を除けば、日付を
  変更するたびにbuild_monthly_report(selected_date, selected_date)を呼んで
  おり、実質的に「1日だけの月報」として動いていた。
- 旧DailyReportWindowはconfigure_status_color_tags()を一度も呼んでおらず、
  「確認事項」欄の赤字・オレンジ字（needs_review/shortfall）による警告表示が
  機能していないバグがあった（調査で発見、隔離コピー上でtag_configure()の
  foregroundが空文字のままであることを実機確認済み）。

期間指定は「今日/今週/今月/カスタム範囲」の4択に統一し、いずれの場合も
build_monthly_report(from_date, to_date)を呼ぶことで上記2点を解消した。

旧ui/monthly_report_window.py::MonthlyReportWindowは、本クラスへの機能移植
完了（仕掛数量抽出・マスタ未登録等3種のCSV出力を含む全機能）を確認した上で、
2026-10-01に削除した。5種の警告ダイアログ（_show_*_warning_if_any()）・
仕掛数量抽出（on_extract_wip()）・3種のCSV出力メソッドは、削除前は
MonthlyReportWindowとロジックが重複していたが、削除により本クラスの
実装のみが残っている（重複は解消済み）。

5種の警告のオン/オフ切り替え（2026-10-01追加）：self.warning_toggle_vars
（5つのtk.BooleanVar、デフォルト全てTrue）で管理する。_build_report_rows()
自体は5種の判定をまとめて1回の処理で行っており、特定の1種だけを計算対象から
外す作りにはなっていない（評価コスト自体も1ロットあたり数ms程度と軽い、
既存のベンチマーク参照）ため、計算は常に全て行い、オフにした警告はダイアログ
表示（_show_*_warning_if_any()の呼び出し）だけを省略する方式を採った
（on_display()・refresh_report()参照）。

CSV出力ボタン（マスタ未登録・構成基板数超過・構成基板数不一致）は、対応する
警告のオン/オフとは独立に常に有効なままとした（ボタンの無効化は行わない）。
理由：警告チェックボックスは「集計のたびにダイアログで知らされたくない」
という表示上の好みを表すものであり、「その種類の異常を今後一切気にしない」
という意味ではないと考えたため。たとえば恒常的に件数が多い既知の問題（マスタ
未登録等）をダイアログでは毎回見たくないが、整備の進捗を確認するために
CSVで時々書き出したい、という利用シーンを妨げないようにした。self.xxx_
warningsが空の場合に「対象がありません」と案内する既存の挙動（旧
MonthlyReportWindowから踏襲）は変更していない。

行の絞り込み（2026-10-01追加）：ui.wip_expansion_window.py・ui.ng_input_window.py
で確立済みの「テキスト部分一致＋チェックボックス式ポップアップ」パターンを
踏襲する。絞り込みの軸はロットNo.（部分一致）・状態（一致/不足/超過/未登録/
構成基板数不一致、チェックボックス式）・NGの有無（あり/なし、チェックボックス
式）の3つ。「状態」「NGの有無」はTreeviewの表示列には含めない（既存の
"確認事項"列も同様にTreeview表示列には含まれておらず、色分けタグとCSV/PDF
出力でのみ表現する既存方針に揃えた）。「状態」はservices.production_service.
_build_report_rows()が2026-10-01に新設した各行の"status"キー（"match"|
"shortfall"|"excess"|"unregistered"|"board_count_inconsistent"|None）を、
ui.lot_progress_window.STATUS_LABELSと同じ日本語ラベルに変換して使う。
「NGの有無」も同時に新設した"has_ng"キー（models.scrap_records.
list_scrap_summary_by_kitting_no()・models.ng_declarations.
list_ng_declarations_latest()を事前に一括取得し、(kitting_list_no, lot_no,
production_side)単位で存在有無を判定したもの）をそのまま使う。

絞り込みはself.report_rows（集計結果の全件）に対してPython側でフィルタし、
populate_report_tree()で絞り込み後の行だけを再描画する方式（wip_expansion_
window.pyのapply_wip_filters()と同じ）。これによりロット単位の縞模様は
絞り込み後の行だけを対象に再計算される（絞り込みで消えた行の分だけ縞模様の
境目が変わるのは意図した挙動）。絞り込み後にソート済みだった順序は保持されない
（populate_report_tree()がself.report_rowsの元の順序で再描画するため）。これは
ui.wip_expansion_window.py・ui.ng_input_window.py・生産実績入力画面の計画一覧
（「フィルタ適用後、ソート状態を自動的に再適用する機能は無い」とv1仕様として
明記済み）と同じ、本アプリ全体で一貫した既存の挙動であり、本画面固有の制約
ではない。

列の表示/非表示（2026-10-01追加）：列ヘッダーを右クリックすると、
tk.Menuのチェックボタン項目で表示する列を選べるようにした（Treeviewの
"displaycolumns"プロパティで実際の表示列を制御する、tkinter標準の仕組み。
データ自体（columns）は変更しないため、CSV出力・PDF出力は常にself.report_rows
から全列を出力し、表示設定の影響を受けない。表示列と出力列を独立に保つ
ことで、「画面では今は要らない列を隠しつつ、出力時は漏れなく記録に残す」
という使い方ができるようにした。全列を非表示にすることはできない
（最低1列は残すよう制御する）。
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

# 列ヘッダーソート（sort_by_column()）で数値として比較する列。
# board_countは"未登録"という非数値文字列が入り得るため、sort_key()側で
# 変換失敗時のフォールバックを用意する（ui.lot_progress_window.py::
# sort_by_column()と同じ考え方）。
_NUMERIC_COLUMNS = {"seq", "board_count", "daily_qty", "order_qty", "lot_completed", "surplus_qty", "lot_remaining"}


def compute_period_dates(period_key: str, today: datetime = None):
    """
    period_key（"today"|"this_week"|"this_month"）から、開始日・終了日
    （date型）を計算する。"custom"はここでは計算できない
    （呼び出し元がDateEntryの値をそのまま使うため、Noneを返す）。

    週の起算日：月曜起算とした（ISO 8601・Python標準のdatetime.weekday()の
    定義（0=月曜）に素直に合わせた。業務上の慣習として日曜起算が使われて
    いないか、または「今週」の終わりを（まだ来ていない）日曜まで含めるべきか
    どうかは、今回のスコープでは判断できておらず、以下の通り「開始日は月曜、
    終了日は今日」という設計を採用した）。

    終了日の扱い：週・月のいずれも、期間の理論上の終わり（週なら日曜、月なら
    月末）ではなく、「今日」を終了日とする。理由：production_dailyのreport_date
    に未来の日付が入ることは無い（実績は過去〜当日分しか登録されない）ため、
    理論上の終わりを終了日にしても表示結果は同じになるが、「今月」を選んだ
    ときにタイトルへ表示される終了日が月末（まだ来ていない日付）になると
    誤解を招く可能性があるため、今日までとした。
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
    実績レポート画面（日報・月報統合）。期間指定（今日/今週/今月/カスタム範囲）に
    応じてbuild_monthly_report()を呼び、一覧表示・印刷・印刷プレビュー・
    PDF出力・CSV出力・仕掛数量抽出・マスタ未登録等3種のCSV出力を行う。

    旧ui/monthly_report_window.py::MonthlyReportWindowが持っていた機能は
    全て本クラスへ移植済みであることを確認した上で、2026-10-01に同ファイルを
    削除した（モジュールdocstring参照）。
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

        # 5種の警告のオン/オフ（2026-10-01追加）。デフォルトは全てTrue
        # （既存の「月報は常に全て表示」という挙動を変えないため）。
        # キーはon_display()・refresh_report()が参照する際の名前。
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

        # 警告表示のオン/オフ（2026-10-01追加）。計算自体（_build_report_rows()・
        # evaluate_lot_status()）は5種まとめて1回で行われ、計算コスト自体は
        # 軽い（既存のベンチマークで1ロットあたり数ms程度）ため、計算は省略せず
        # 常に行い、ダイアログ表示だけをここでオン/オフする（on_display()・
        # refresh_report()参照）。CSV出力ボタンはこのチェックボックスとは独立に
        # 常に有効のまま（CSV出力の扱いについてはモジュールdocstring末尾参照）。
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

        # 行の絞り込み（2026-10-01追加）。ui.wip_expansion_window.py・
        # ui.ng_input_window.pyの「テキスト部分一致＋チェックボックス式
        # ポップアップ」パターンを踏襲（モジュールdocstring参照）。
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

        # "report_date"（登録日、2026-10-01追加）は、REPORT_HEADERS側で
        # "確認事項"の直前（インデックス10）に追加したため、ここでも同じ
        # 位置に追加する必要がある（headers辞書はself._colsとREPORT_HEADERSを
        # zip()で位置対応させているため、順序がずれるとヘッダー文言が
        # 1列ずつずれてしまう。"確認事項"は従来通りTreeview表示列には含めない）。
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
        # 日報側で欠けていたバグ（configure_status_color_tags()の未呼び出し）は
        # ここで必ず両方呼ぶことで解消する。
        configure_lot_stripe_tags(self.tree)
        configure_status_color_tags(self.tree)

        # 列の表示/非表示（2026-10-01追加）。列ヘッダーの右クリックで
        # チェックボタン付きメニューを出し、Treeviewの"displaycolumns"
        # プロパティで表示列を切り替える（データ自体・columns自体は
        # 変更しないため、CSV/PDF出力には影響しない。モジュールdocstring参照）。
        self._column_visible_vars = {c: tk.BooleanVar(value=True) for c in self._cols}
        self.tree.bind("<Button-3>", self._on_tree_header_right_click)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="印刷", command=self.on_print).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)
        # 以下4ボタンは旧MonthlyReportWindow限定だった機能（2026-09-30に
        # 統合画面へ移植、2026-10-01に移植元のMonthlyReportWindow自体を削除。
        # モジュールdocstring参照）。
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

        # 初期表示：「今日」を選択済みの状態で、開始日・終了日欄を読み取り
        # 専用にし、即座に集計する（従来のDailyReportWindow・
        # MonthlyReportWindowがいずれも開いた瞬間に何かしらの一覧を
        # 表示していたのに合わせる）。
        self._apply_period_preset("today")
        self.on_display()

    def _apply_period_preset(self, period_key: str):
        """
        "today"/"this_week"/"this_month"の場合、開始日・終了日欄へ計算結果を
        セットした上で読み取り専用（state="readonly"）にする。"custom"の
        場合は編集可能（state="normal"）に戻すのみで、日付の値には触れない
        （ユーザーが直前まで見ていたカスタム範囲の値をそのまま残す）。
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

        # 期間の長さに関わらず常に5種とも計算する（計算自体は軽いため省略しない。
        # モジュールdocstring参照）が、表示するかどうかは警告表示チェックボックス
        # （self.warning_toggle_vars）に従う。デフォルトは全てTrueのため、
        # 何もオフにしない限り従来通り全て表示される（退行なし）。
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
    # 警告ダイアログ（旧MonthlyReportWindowの同名メソッドと同一ロジック。
    # モジュールdocstring参照）
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
        """
        2026-10-08改訂（D-111）：inconsistency_warningsの形がロットNo・ファイルNo
        単位の合計比較に変更された（_build_report_rows()のdocstring参照）ため、
        表示もこれに合わせた。計画No（面1・面2それぞれの一覧）も表示する。
        """
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
        選択行に対応する実績（kitting_list_no・lot_no）を実績修正ウインドウ
        （ui.kitting_production_entry.ActualCorrectionWindow）で開く。
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
        """
        実績修正後に一覧を再取得して表示を更新する。旧MonthlyReportWindow.
        refresh_report()と同じく、面1/面2不整合警告のみ再表示する
        （発注数不一致・マスタ未登録等は片面修正では変化し得ないため）。
        """
        if not self.from_date or not self.to_date:
            return
        (
            self.report_rows, self.inconsistency_warnings,
            self.order_qty_inconsistency_warnings, self.unregistered_board_warnings,
            self.excess_file_no_warnings, self.board_count_inconsistency_warnings,
        ) = build_monthly_report(self.from_date, self.to_date)
        self._annotate_filter_fields(self.report_rows)
        # refresh_report()は実績修正1件に伴う再取得のため、on_display()と違い
        # 絞り込み条件はクリアせずそのまま維持する（見ていた絞り込み結果の
        # ままデータだけ最新化する）。
        self.apply_filters()
        if self.warning_toggle_vars["inconsistency"].get():
            self._show_inconsistency_warning_if_any()

    def sort_by_column(self, col):
        """
        列ヘッダークリックでのソート（2026-10-01追加）。ui.lot_progress_window.
        LotProgressWindow.sort_by_column()の「ロット単位のブロックにまとめて
        からブロック単位でソートする」方式を踏襲するが、ブロックの検出方法は
        異なる（下記の重要な違い参照）。

        重要な違い（調査で確認した懸念点への対応）：LotProgressWindowの
        build_display_rows()は、各ロットの実データ行の直後にそのロットの
        「未確定」仮想行を続けて追加するため、同一lot_noの行は最初から
        必ず連続している（この前提のもとでLotProgressWindow側は「現在
        連続している行のまとまり」を単純にスキャンして検出している）。

        一方、services.production_service._build_report_rows()は、実データ行を
        production_daily のレコード順（lot_no単位にグルーピングされていない）
        で構築した後、「未確定」仮想行は全ロット分をまとめて末尾に追加する
        設計のため、**同一lot_noの行は最初から連続しているとは限らない**
        （実データ行同士ですら他ロットの行で分断され得る上、「未確定」行は
        常にロットの実データ行から離れた末尾にある）。この状態で
        LotProgressWindowと同じ「現在連続している行のまとまり」方式を使うと、
        同じロットの行が複数の別々のブロックに分かれてしまい、「未確定」行を
        含むロットがブロックとして正しくまとまらない（調査で予想された懸念の
        通り、隔離コピー上の実データで実際に発生することを確認済み。詳細は
        対応時の報告参照）。

        対応：ブロックの検出を「現在の並びで連続しているか」ではなく、
        「lot_no値が一致する行を全て1つのブロックに集める」方式に変更した
        （行の物理的な位置が離れていても、同じlot_noであれば必ず同じ
        ブロックに入る）。ブロックの初期位置は、そのlot_noが現在の並びで
        最初に出現した位置とする（複数回ソートを繰り返しても、常にこの
        グルーピングが正しく機能する）。
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
    # 行の絞り込み（2026-10-01追加。モジュールdocstring参照）
    # ------------------------------------------------------------------

    def _annotate_filter_fields(self, rows):
        """
        report_rowsの各行に、絞り込み専用の表示用キー"status_label"・
        "ng_label"を追加する（CSV/PDF出力には使われないキーのため、
        REPORT_HEADERS・_row_to_values()には影響しない）。
        """
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
        """
        状態・NGの有無用の、エクセルのオートフィルタ風チェックボックス式
        絞り込みポップアップ（ui.wip_expansion_window.py::
        open_wip_checkbox_filter_popup()と同じ設計）。
        """
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
    # 列の表示/非表示（2026-10-01追加。モジュールdocstring参照）
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
    # 旧MonthlyReportWindow限定機能の移植（2026-09-30追加、2026-10-01に
    # 移植元のMonthlyReportWindow自体を削除。モジュールdocstring参照）。
    #
    # 仕掛数量抽出の対象lot_noは、self.report_rows（＝現在選択中の期間で
    # build_monthly_report()が返した行）のdistinctなlot_noから決める。
    # つまり「どのロットを対象にするか」は選択中の期間に依存するが、各ロットの
    # 状態そのもの（evaluate_lot_status()・build_wip_extraction_rows()が
    # 計算する引落・仕掛・未生産、および保存前確認で見るneeds_review判定）は、
    # production_daily・kitting_plan_items・board_structure_masterの「現時点の
    # 内容」から計算され、期間（report_date範囲）には一切依存しない
    # （calculate_lot_completion()・_evaluate_lot_status()はいずれもreport_date
    # を引数に取らない）。この2点の違いは、旧MonthlyReportWindowでも全く同じ
    # 挙動だったため、移植による差異は無い。
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
