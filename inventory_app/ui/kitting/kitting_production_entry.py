import threading
import queue

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from services.production_service import (
    search_plan_by_kitting_no,
    get_daily_history,
    side2_plans_hiding,
    side1_registration_block,
)
from services.lot_status_service import preview_completed_lot_registration
from services.production_import_service import parse_production_csv_for_staging
from services.csv_format_detection import (
    read_csv_header, detect_format_mismatch_warnings, detect_external_actual_csv_warnings,
    PRODUCTION_CSV_SIGNATURE_COLUMNS, PRODUCTION_CSV_FORMAT_LABEL,
    PLAN_CSV_SIGNATURE_COLUMNS, PLAN_CSV_FORMAT_LABEL,
)
from models.production_import_staging import list_pending_csv_import_rows
from models.kitting_plan import (
    find_opposite_side_plan, find_plan_item_by_kitting_no,
    classify_side1_only_plan, SIDE1_ONLY_CLASS_UNREGISTERED,
)
from models.production import list_daily_production_today
from models.ng_declarations import get_ng_declaration
from models.board_structure_master import get_board_structure
from ui.reports.unified_report_window import UnifiedReportWindow
from ui.production.lot_progress_window import LotProgressWindow
from ui.production.daily_drawdown_window import DailyDrawdownWindow
from ui.production.production_import_staging_window import open_or_notify, format_completed_lot_warning
from ui.production.external_actual_window import ExternalActualWindow
from ui.loading_window import LoadingWindow
from ui.common.window_utils import center_window
from ui.common.highlight_colors import SIDE2_WAIT_BLUE, SIDE_MASTER_UNREGISTERED_GRAY
from ui.common.color_legend import build_color_legend
from ui.common.column_filter import make_sort_key
from ui.kitting.plan_list_filters import PlanListFiltersMixin   # 計画一覧の絞り込みと一覧の取得・表示・更新は mixin に移した（D-190）
# 段階2で ui/kitting/actual_correction_window.py へ移した。他の画面はここから import しているので re-export する。
from ui.kitting.actual_correction_window import ActualCorrectionWindow  # noqa: F401 （re-export）


# 段階1で services/production_entry_service.py へ移した。_resolve_csv_report_date はここから re-export する。
from services.production_entry_service import (  # noqa: E402,F401 （re-export）
    _resolve_csv_report_date,
    resolve_csv_report_date,
    compute_ng_save_qty,
    build_planned_qty_mismatch_lines,
    is_plan_row_completed,
    plan_row_tag,
    register_actual_and_ng,
    format_registration_message,
)


# 計画一覧の色の凡例（D-181）。値は行の色付け（tag_configure）と同じ定数を使う。
PLAN_LIST_LEGEND = [
    ("面2待ち（面1には登録できない）", SIDE2_WAIT_BLUE, None),
    ("生産面マスター未登録（確認のうえ登録できる）", SIDE_MASTER_UNREGISTERED_GRAY, None),
]


class KittingProductionEntryWindow(PlanListFiltersMixin, tk.Toplevel):
    # 選択のたびに DB 検索すると矢印キー連打で処理が積み重なるため、選択が止まってから実行する。
    PLAN_SELECT_DEBOUNCE_MS = 200

    def __init__(self, parent, current_worker, preloaded_plan_rows=None):
        """
        preloaded_plan_rows: 呼び出し元が別スレッドで取得済みの計画一覧（初回表示で UI を止めないため）。
        None なら __init__ 内で同期的に取得する。
        """
        super().__init__(parent)
        self.current_worker = current_worker
        self.current_plan = None
        self.plan_sort_states = {}
        self._plan_select_debounce_id = None
        self._pending_plan_select_kitting_no = None
        self._pending_plan_select_lot_no = None
        self._cell_edit_entry = None
        self._preloaded_plan_rows = preloaded_plan_rows
        # 実績CSVステージング一覧から転記した行を、登録成功時に一覧から消すコールバック（1件分だけ保持）。
        self._pending_csv_row_removal = None
        # 転記した CSV 行の払い出し日。手動登録では None（実行日を使う）。
        self._pending_csv_report_date = None
        # 開いている実績CSVステージング一覧。登録成功時に lift() で手前に戻すために使う。
        self._csv_staging_window = None

        # 現場CSV取込画面。開いている間は、この画面の登録・実績修正のボタンを押せなくする（取り込んだ行の確認中に、同じ計画を書き換えないため）。
        self._external_actual_window = None
        self._external_actual_locked = False

        # 実績CSV取込の非同期パース用（LoadingWindow＋スレッド＋queue をポーリングする）。
        self._csv_import_queue = queue.Queue()
        self._csv_import_loading_window = None

        # ロット進捗チェック画面。開いていれば lift() するだけで、多重に開かない。
        self._lot_progress_window = None

        # 日々の引落一覧画面。多重表示の防止は _lot_progress_window と同じ。
        self._daily_drawdown_window = None

        # 計画一覧の絞り込み:
        # - _all_plan_rows: 絞り込み前の全件。_plan_checkbox_filters にキーが無い列＝絞り込み無し
        # - _hide_completed_var: ファイルNo・面単位の合算で判定するため、列フィルタとは別に apply_plan_filters() で適用する
        # - _plan_row_iid_by_kitting_no: (kitting_list_no, lot_no) -> 表示中の行の iid。登録直後に該当行だけ書き換えるのに使う。
        #   kitting_list_no は lot_no をまたいで重複する（実データで478件）ため、タプルをキーにする
        self._all_plan_rows = []
        self._plan_filter_vars = {}
        self._plan_checkbox_filters = {}
        self._plan_checkbox_buttons = {}
        self._plan_col_index = {}
        self._plan_filter_labels = {}
        self._hide_completed_var = tk.BooleanVar(value=False)
        # 古い計画を隠す・計画終了日が来たものだけ（D-192）。初期はどちらもオフ。判定は plan_list_filters.py。
        self._hide_stale_var = tk.BooleanVar(value=False)
        self._only_ended_var = tk.BooleanVar(value=False)
        self._plan_date_from_entry = None
        self._plan_date_to_entry = None
        self._plan_row_iid_by_kitting_no = {}

        # NG 入力は面1・面2固定の2スロット。_ng_side_plans は面 -> その面の計画（無ければ None）。
        self._ng_side_plans = {"1": None, "2": None}
        self._ng_side_entries = {}
        self._ng_side_labels = {}

        # 本日の全計画分の実績ログ。表示側で絞り込んでも全件を保持する。
        self._today_all_rows = []
        # iid -> 元レコード。表示を絞り込んでもダブルクリックで正しい行を引けるよう、位置ではなく iid で引く。
        self._today_row_by_iid = {}

        # Enter で 実績→NG面1→NG面2→登録確認 と進む。途中の Enter では DB に書き込まない。

        self._arrow_nav_widgets = []

        self.title("生産実績入力（キッティングリストNo.）")
        # 高さ: 850px では画面からはみ出す環境があるため 700px。幅: 右ペインの必要幅（985px）を確保するため 1350px。
        # 実測値と経緯は docs/domain/production_entry.md「画面の設計メモ」。
        self.geometry("1350x700")
        center_window(self, parent)
        # 計画一覧の読み込み後に生成されるので、ここで最大化しても「小さく出てから広がる」動きにならない。
        self.state("zoomed")

        self.create_widgets()

    # 右ペイン（計画一覧・絞り込み）の必要幅。実測 965px に余裕を加えた値。
    _RIGHT_PANE_MIN_WIDTH_PX = 985
    # 左ペインの初期幅。入力欄が切れない範囲で最小にした値。
    _LEFT_PANE_INITIAL_WIDTH_PX = 300

    def create_widgets(self):
        # pack(side=LEFT/RIGHT) では最大化しても右ペインが広がらないため、PanedWindow で初期サッシュ位置を明示する。
        container = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Frame(container)
        right_frame = ttk.Labelframe(container, text="計画一覧", padding=5)
        # 余った幅は右ペイン（計画一覧）を優先して広げる。
        container.add(left_frame, weight=1)
        container.add(right_frame, weight=4)

        # 生成直後はウインドウ幅が未確定なので update_idletasks() で確定させてから計算する。右ペインが潰れないよう下限を設ける。
        self.update_idletasks()
        total_width = max(self.winfo_width(), 1)
        left_width = min(
            self._LEFT_PANE_INITIAL_WIDTH_PX,
            max(100, total_width - self._RIGHT_PANE_MIN_WIDTH_PX),
        )
        container.sashpos(0, left_width)

        # 計画情報表示エリア（計画は右の計画一覧から選ぶ）
        info_frame = ttk.LabelFrame(left_frame, text="計画情報", padding=10)
        info_frame.pack(fill=tk.X, padx=15, pady=5)

        self.lbl_lot = self._add_info_row(info_frame, "ロットNo.：", 0)
        self.lbl_setup = self._add_info_row(info_frame, "セットアップファイルNo.（基板名）：", 1)
        self.lbl_side = self._add_info_row(info_frame, "生産面：", 2)
        self.lbl_plan_qty = self._add_info_row(info_frame, "今回計画数：", 3)
        self.lbl_ext_cum = self._add_info_row(info_frame, "外部システム累計：", 4)
        self.lbl_app_cum = self._add_info_row(info_frame, "アプリ入力累計：", 5)
        self.lbl_lot_completed = self._add_info_row(info_frame, "ロット完成数：", 6)
        self.lbl_lot_remaining = self._add_info_row(info_frame, "ロット未完成数：", 7)
        self.lbl_board_structure_count = self._add_info_row(info_frame, "構成基板数：", 8)
        self.lbl_lot_file_actuals = self._add_info_row(info_frame, "基板別実績（file_no）：", 9)

        # 実績・NG入力エリア
        entry_frame = ttk.LabelFrame(left_frame, text="本日の生産実績・NG（仕損）入力", padding=10)
        entry_frame.pack(fill=tk.X, padx=15, pady=5)

        daily_row = ttk.Frame(entry_frame)
        daily_row.pack(fill=tk.X, pady=2)
        ttk.Label(daily_row, text="本日生産実績：").pack(side=tk.LEFT, padx=5)
        self.entry_daily_qty = ttk.Entry(daily_row, width=10)
        self.entry_daily_qty.pack(side=tk.LEFT, padx=5)
        self.entry_daily_qty.bind("<Return>", self._on_daily_qty_enter)
        # 記入欄にフォーカスがあっても、上下矢印で計画一覧の選択を移動する（フォーカスは記入欄に留まる）。
        self.entry_daily_qty.bind("<Up>", lambda e: self._move_plan_selection(-1))
        self.entry_daily_qty.bind("<Down>", lambda e: self._move_plan_selection(1))

        # NG 入力（面1・面2の2行）。計画を選ぶまでは無効にしておく。
        for side in ("1", "2"):
            row = ttk.Frame(entry_frame)
            row.pack(fill=tk.X, pady=2)
            label = ttk.Label(row, text=f"NG 面{side}：")
            label.pack(side=tk.LEFT, padx=5)
            entry = ttk.Entry(row, width=10, state=tk.DISABLED)
            entry.pack(side=tk.LEFT, padx=5)
            entry.bind("<Up>", lambda e: self._move_plan_selection(-1))
            entry.bind("<Down>", lambda e: self._move_plan_selection(1))
            self._ng_side_labels[side] = label
            self._ng_side_entries[side] = entry

        self._ng_side_entries["1"].bind("<Return>", self._on_ng_side1_enter)
        self._ng_side_entries["2"].bind("<Return>", self._on_ng_side2_enter)

        btn_row = ttk.Frame(entry_frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))

        # 実績と NG は、1つの登録ボタン・1つの確認ダイアログで登録する（_start_registration()）。
        self.btn_register = ttk.Button(btn_row, text="登録", command=self._start_registration,
                                        state=tk.DISABLED)
        self.btn_register.pack(side=tk.LEFT, padx=5)

        self.btn_correction = ttk.Button(btn_row, text="実績修正", command=self.open_correction_window,
                                          state=tk.DISABLED)
        self.btn_correction.pack(side=tk.LEFT, padx=5)

        # 対象のウィジェットがすべて生成された後に呼ぶこと。
        self._setup_arrow_focus_navigation()

        # 日次実績履歴。left_frame の中で残りの高さを使う唯一の expand=True 要素。
        hist_frame = ttk.LabelFrame(left_frame, text="日次実績履歴（本日の全計画分）", padding=10)
        hist_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=5)

        cols = ("kitting_list_no", "lot_no", "board_name", "report_date", "daily_qty", "worker_id")
        self.tree = ttk.Treeview(hist_frame, columns=cols, show="headings")
        self.tree.heading("kitting_list_no", text="キッティングNo.")
        self.tree.heading("lot_no", text="ロットNo.")
        self.tree.heading("board_name", text="基板名")
        self.tree.heading("report_date", text="日付")
        self.tree.heading("daily_qty", text="当日実績")
        self.tree.heading("worker_id", text="作業者")
        self.tree.column("kitting_list_no", width=140, anchor=tk.W)
        self.tree.column("lot_no", width=90, anchor=tk.W)
        self.tree.column("board_name", width=120, anchor=tk.W)
        self.tree.column("report_date", width=100)
        self.tree.column("daily_qty", width=90, anchor=tk.E)
        self.tree.column("worker_id", width=100)
        self.tree.bind("<Double-1>", self.on_history_row_double_click)

        # hist_frame が縮んだときの唯一の閲覧手段。Treeview より先に pack する。
        vsb_hist = ttk.Scrollbar(hist_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb_hist.set)
        vsb_hist.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)

        # 計画一覧エリア（右側）
        cols_plan = ("list_no", "lot_no", "plan_start_datetime", "file_no", "board_name",
                     "planned_qty", "order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining")
        self._plan_col_index = {key: i for i, key in enumerate(cols_plan)}

        # 絞り込み: lot_no/file_no/board_name は候補が数百件あるためチェックボックス式、他はテキスト部分一致。
        # どちらも _plan_filter_predicates() で同じ述語の形にそろえる。
        self._plan_filter_labels = {
            "list_no": "キッティングNo.",
            "lot_no": "ロットNo.",
            "plan_start_datetime": "実装開始予定日",
            "file_no": "file_no",
            "board_name": "基板名",
            "planned_qty": "予定生産数",
            "order_qty": "発注数",
            "actual_qty": "実績累計",
            "diff": "差分",
            "lot_completed": "ロット完成数",
            "lot_remaining": "ロット未完成数",
        }
        plan_filter_frame = ttk.LabelFrame(right_frame, text="絞り込み", padding=8)
        plan_filter_frame.pack(fill=tk.X, pady=(0, 5))

        filter_row1 = ttk.Frame(plan_filter_frame)
        filter_row1.pack(fill=tk.X, pady=(0, 4))
        filter_row2 = ttk.Frame(plan_filter_frame)
        filter_row2.pack(fill=tk.X)

        self._add_plan_filter_entry(filter_row1, "list_no", self._plan_filter_labels["list_no"], width=12)
        self._add_plan_checkbox_filter_button(filter_row1, "lot_no")
        self._add_plan_date_range_filter(filter_row1)
        self._add_plan_checkbox_filter_button(filter_row1, "file_no")
        self._add_plan_checkbox_filter_button(filter_row1, "board_name")

        row2_cols = ("planned_qty", "order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining")
        for col_key in row2_cols:
            self._add_plan_filter_entry(filter_row2, col_key, self._plan_filter_labels[col_key], width=8)

        # ファイルNo・面単位の合算で判定するため、列フィルタとは別に apply_plan_filters() で適用する。
        ttk.Checkbutton(
            filter_row2, text="入力済みを隠す", variable=self._hide_completed_var,
            command=self.apply_plan_filters,
        ).pack(side=tk.LEFT, padx=(15, 5))

        ttk.Checkbutton(
            filter_row2, text="古い計画を隠す", variable=self._hide_stale_var,
            command=self.apply_plan_filters,
        ).pack(side=tk.LEFT, padx=(5, 2))
        self._stale_hidden_label = ttk.Label(filter_row2, text="")
        self._stale_hidden_label.pack(side=tk.LEFT, padx=(0, 5))
        ttk.Checkbutton(
            filter_row2, text="計画終了日が来たものだけ", variable=self._only_ended_var,
            command=self.apply_plan_filters,
        ).pack(side=tk.LEFT, padx=(5, 5))

        ttk.Button(
            filter_row2, text="絞り込みクリア", command=self.clear_plan_filters
        ).pack(side=tk.LEFT, padx=(5, 0))

        self.tree_plan_list = ttk.Treeview(right_frame, columns=cols_plan, show="headings")
        self.tree_plan_list.heading("list_no", text="キッティングNo.",
                                     command=lambda c="list_no": self.sort_plan_list(c))
        self.tree_plan_list.heading("lot_no", text="ロットNo.",
                                     command=lambda c="lot_no": self.sort_plan_list(c))
        self.tree_plan_list.heading("plan_start_datetime", text="実装開始予定日",
                                     command=lambda c="plan_start_datetime": self.sort_plan_list(c))
        self.tree_plan_list.heading("file_no", text="file_no",
                                     command=lambda c="file_no": self.sort_plan_list(c))
        self.tree_plan_list.heading("board_name", text="基板名",
                                     command=lambda c="board_name": self.sort_plan_list(c))
        self.tree_plan_list.heading("planned_qty", text="予定生産数",
                                     command=lambda c="planned_qty": self.sort_plan_list(c))
        self.tree_plan_list.heading("order_qty", text="発注数",
                                     command=lambda c="order_qty": self.sort_plan_list(c))
        self.tree_plan_list.heading("actual_qty", text="実績累計",
                                     command=lambda c="actual_qty": self.sort_plan_list(c))
        self.tree_plan_list.heading("diff", text="差分",
                                     command=lambda c="diff": self.sort_plan_list(c))
        self.tree_plan_list.heading("lot_completed", text="ロット完成数",
                                     command=lambda c="lot_completed": self.sort_plan_list(c))
        self.tree_plan_list.heading("lot_remaining", text="ロット未完成数",
                                     command=lambda c="lot_remaining": self.sort_plan_list(c))
        self.tree_plan_list.column("list_no", width=110, anchor=tk.W)
        self.tree_plan_list.column("lot_no", width=90, anchor=tk.W)
        self.tree_plan_list.column("plan_start_datetime", width=130, anchor=tk.W)
        self.tree_plan_list.column("file_no", width=90, anchor=tk.W)
        self.tree_plan_list.column("board_name", width=120, anchor=tk.W)
        self.tree_plan_list.column("planned_qty", width=90, anchor=tk.E)
        self.tree_plan_list.column("order_qty", width=80, anchor=tk.E)
        self.tree_plan_list.column("actual_qty", width=80, anchor=tk.E)
        self.tree_plan_list.column("diff", width=80, anchor=tk.E)
        self.tree_plan_list.column("lot_completed", width=100, anchor=tk.E)
        self.tree_plan_list.column("lot_remaining", width=100, anchor=tk.E)

        # 面1だけの計画の分類（D-9x）。b（面2待ち）・c（生産面マスター未登録）にだけ背景色を付ける。
        # 配色は実績CSVステージング一覧の候補と同じ。
        self.tree_plan_list.tag_configure("needs_side2_wait", background=SIDE2_WAIT_BLUE)
        self.tree_plan_list.tag_configure("side_master_unregistered", background=SIDE_MASTER_UNREGISTERED_GRAY)

        vsb_plan = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_plan_list.yview)
        self.tree_plan_list.configure(yscrollcommand=vsb_plan.set)

        # 列幅の合計が表示幅を超えるため、水平スクロールバーを付ける。
        hsb_plan = ttk.Scrollbar(right_frame, orient="horizontal", command=self.tree_plan_list.xview)
        self.tree_plan_list.configure(xscrollcommand=hsb_plan.set)

        # pack 順の罠: pack は呼んだ順に領域を取るため、ボタン行→hsb_plan→vsb_plan→Treeview の順に pack する。
        # 逆にすると hsb_plan がボタン行の下に離れて表示される。
        bottom_btn_frame = ttk.Frame(right_frame)
        bottom_btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

        ttk.Button(bottom_btn_frame, text="更新", command=self.load_plan_list).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5)
        )
        self.btn_unified_report = ttk.Button(
            bottom_btn_frame, text="実績レポート", command=self.open_unified_report
        )
        self.btn_unified_report.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.btn_lot_progress = ttk.Button(
            bottom_btn_frame, text="ロット進捗チェック", command=self.open_lot_progress
        )
        self.btn_lot_progress.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.btn_daily_drawdown = ttk.Button(
            bottom_btn_frame, text="日々の引落一覧", command=self.open_daily_drawdown
        )
        self.btn_daily_drawdown.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.btn_production_csv_import = ttk.Button(
            bottom_btn_frame, text="実績CSV取込", command=self.on_production_csv_import
        )
        self.btn_production_csv_import.pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.btn_external_actual = ttk.Button(
            bottom_btn_frame, text="現場CSV取込", command=self.open_external_actual_window
        )
        self.btn_external_actual.pack(side=tk.LEFT, expand=True, fill=tk.X)

        # ステージング一覧は本画面のメソッドを直接呼ぶので、本画面が開いているときだけ使えるようここに置く。
        self.btn_csv_staging_status = ttk.Button(
            bottom_btn_frame, text="実績CSV取込状況", command=self.open_pending_csv_staging_window
        )
        self.btn_csv_staging_status.pack(side=tk.LEFT, expand=True, fill=tk.X)

        # 色の凡例（D-181）。ボタン行の上、水平スクロールバーの下。Treeview より先に side=BOTTOM で pack する。
        self.plan_list_legend = build_color_legend(right_frame, PLAN_LIST_LEGEND)
        self.plan_list_legend.pack(side=tk.BOTTOM, anchor=tk.W, pady=(3, 0))
        hsb_plan.pack(side=tk.BOTTOM, fill=tk.X)
        vsb_plan.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_plan_list.pack(expand=True, fill=tk.BOTH)
        self.tree_plan_list.bind("<<TreeviewSelect>>", self.on_select_plan_list)
        self.tree_plan_list.bind("<Double-1>", self.on_plan_cell_double_click)

        if self._preloaded_plan_rows is not None:
            # 呼び出し元が別スレッドで取得済み。DB に再アクセスしない。
            self._all_plan_rows = self._preloaded_plan_rows
            self._populate_plan_list_tree(self._preloaded_plan_rows)
        else:
            self.load_plan_list()

        # 本日の全計画分のログなので、計画を選ぶ前から表示しておく。
        self.load_today_log()


    def on_select_plan_list(self, event):
        """
        計画一覧の選択（クリック・矢印キー）。DB 検索を伴う search_plan() はデバウンスする。
        kitting_list_no は lot_no をまたいで重複するため、行の lot_no も一緒に渡す。
        """
        sel = self.tree_plan_list.selection()
        if not sel:
            return
        values = self.tree_plan_list.item(sel[0], "values")
        self._pending_plan_select_kitting_no = values[0]
        self._pending_plan_select_lot_no = values[1] or None

        if self._plan_select_debounce_id is not None:
            self.after_cancel(self._plan_select_debounce_id)
        self._plan_select_debounce_id = self.after(
            self.PLAN_SELECT_DEBOUNCE_MS, self._on_plan_select_debounced
        )

    def _on_plan_select_debounced(self):
        """
        デバウンス確定後に search_plan() を実行し、実績記入欄へフォーカスを移す。
        選択イベントの時点で移すと、矢印キーでの一覧移動中にフォーカスが記入欄へ逃げてしまう。
        """
        self._plan_select_debounce_id = None
        self.search_plan(self._pending_plan_select_kitting_no, lot_no=self._pending_plan_select_lot_no)
        self.entry_daily_qty.focus_set()

    def _move_plan_selection(self, delta):
        """
        記入欄にフォーカスがあっても、上下矢印で計画一覧の選択を1行動かす。
        <<TreeviewSelect>> を発火させて通常の選択処理に乗せる。フォーカスはデバウンス後に記入欄へ戻る。
        """
        children = self.tree_plan_list.get_children("")
        if not children:
            return "break"

        sel = self.tree_plan_list.selection()
        if sel and sel[0] in children:
            current_index = children.index(sel[0])
        else:
            current_index = -1 if delta > 0 else len(children)

        new_index = max(0, min(len(children) - 1, current_index + delta))
        new_iid = children[new_index]

        self.tree_plan_list.selection_set(new_iid)
        self.tree_plan_list.focus(new_iid)
        self.tree_plan_list.see(new_iid)
        self.tree_plan_list.event_generate("<<TreeviewSelect>>")
        return "break"

    def sort_plan_list(self, col):
        numeric_cols = {"planned_qty", "order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining"}

        sort_key = make_sort_key(col, numeric_cols)

        ascending = self.plan_sort_states.get(col, True)

        items = [
            (self.tree_plan_list.set(iid, col), iid)
            for iid in self.tree_plan_list.get_children("")
        ]
        items.sort(key=lambda t: sort_key(t[0]), reverse=not ascending)

        for index, (_, iid) in enumerate(items):
            self.tree_plan_list.move(iid, "", index)

        self.plan_sort_states[col] = not ascending

    def on_plan_cell_double_click(self, event):
        """
        セルの上に一時的な Entry を重ね、テキストを全選択で表示する（Treeview はセルをコピーできないため）。
        Escape・Enter・フォーカス移動で閉じる。
        """
        region = self.tree_plan_list.identify_region(event.x, event.y)
        if region != "cell":
            return

        row_id = self.tree_plan_list.identify_row(event.y)
        column_id = self.tree_plan_list.identify_column(event.x)
        if not row_id or not column_id:
            return

        try:
            col_index = int(column_id.replace("#", "")) - 1
        except ValueError:
            return
        values = self.tree_plan_list.item(row_id, "values")
        if col_index < 0 or col_index >= len(values):
            return
        cell_text = str(values[col_index])

        bbox = self.tree_plan_list.bbox(row_id, column_id)
        if not bbox:
            return
        x, y, width, height = bbox

        self._close_cell_edit_entry()

        entry = tk.Entry(self.tree_plan_list)
        entry.insert(0, cell_text)
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()

        entry.bind("<FocusOut>", lambda e: self._close_cell_edit_entry())
        entry.bind("<Escape>", lambda e: self._close_cell_edit_entry())
        entry.bind("<Return>", lambda e: self._close_cell_edit_entry())

        self._cell_edit_entry = entry

    def _close_cell_edit_entry(self):
        if self._cell_edit_entry is not None:
            entry = self._cell_edit_entry
            self._cell_edit_entry = None
            try:
                entry.destroy()
            except tk.TclError:
                pass

    def _add_info_row(self, parent, label_text, row):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky=tk.W, pady=3)
        val_label = ttk.Label(parent, text="-", foreground="blue")
        val_label.grid(row=row, column=1, sticky=tk.W, pady=3)
        return val_label

    def search_plan(self, kitting_list_no, lot_no=None):
        """
        kitting_list_no・lot_no で計画を検索して表示する。呼び出し元は必ず lot_no も渡す。
        CSV 由来の登録待ち（_pending_csv_row_removal・_pending_csv_report_date）はここでクリアする。
        CSV を経ずに別の計画へ切り替えたとき、古い CSV 行の情報が無関係な登録に使われないようにするため
        （CSV 経由のときは、ステージング一覧がこの呼び出しの直後に改めてセットする）。
        """
        self._pending_csv_row_removal = None
        self._pending_csv_report_date = None
        plan, _candidates = search_plan_by_kitting_no(kitting_list_no, lot_no)

        if not plan:
            messagebox.showerror("検索エラー", f"キッティングリストNo. {kitting_list_no} の計画が見つかりません。", parent=self.winfo_toplevel())
            self._clear_current_plan()
            return

        # 面2がある面1の計画は無いものとして扱い、面2の計画に切り替える（D-170）。面2が複数あるときは、どれかを決められないので選んでもらう。
        hiding = side2_plans_hiding(plan)
        if len(hiding) == 1:
            messagebox.showinfo(
                "面2の計画に切り替えました",
                f"計画 {plan['kitting_list_no']} は面1です。同じロット・ファイルNoに面2の計画があるため、"
                f"面2の計画 {hiding[0]['kitting_list_no']} を表示します。",
                parent=self.winfo_toplevel(),
            )
            self.search_plan(hiding[0]["kitting_list_no"], lot_no=hiding[0]["lot_no"])
            return
        if hiding:
            messagebox.showwarning(
                "面2の計画を選んでください",
                f"計画 {plan['kitting_list_no']} は面1です。同じロット・ファイルNoに面2の計画が複数あります"
                f"（{'、'.join(p['kitting_list_no'] for p in hiding)}）。計画一覧から面2の計画を選んでください。",
                parent=self.winfo_toplevel(),
            )
            self._clear_current_plan()
            return

        self.current_plan = plan
        self.lbl_lot.config(text=plan["lot_no"])
        self.lbl_setup.config(text=f"{plan['setup_file_no']}（{plan['board_name']}）")
        self.lbl_side.config(text=plan["production_side"])
        self.lbl_plan_qty.config(text=f"{plan['planned_qty']:.0f}")
        self.lbl_ext_cum.config(text=f"{plan['cumulative_qty_external']:.0f}")
        self.lbl_app_cum.config(text=f"{plan['app_cumulative_qty']:.0f}")

        self.lbl_lot_completed.config(text=f"{plan['lot_completed_quantity']:.0f}")
        self.lbl_lot_remaining.config(text=f"{plan['lot_remaining_quantity']:.0f}")

        # 同じ setup_file_no に面2がある場合、面1は完成品ではないので表示しない（list_active_plan_items() と同じ、D-8）。
        second_side_setup_files = {
            file_no for (file_no, side) in plan["lot_file_actuals"]
            if str(side).strip() == "2"
        }
        visible_file_nos = {
            file_no
            for (file_no, side) in plan["lot_file_actuals"]
            if not (str(side).strip() == "1" and file_no in second_side_setup_files)
        }
        file_actuals_text = "\n".join(
            f"{file_no}（面{side}）: {qty:.0f}"
            for (file_no, side), qty in plan["lot_file_actuals"].items()
            if not (str(side).strip() == "1" and file_no in second_side_setup_files)
        )
        self.lbl_lot_file_actuals.config(text=file_actuals_text or "-")

        # 構成基板数マスタの値と、表示中の file_no の数（面2があれば面1を除く）が違えば赤字にする
        # （登録漏れや計画データの異常の可能性）。基板名の表記ゆれは get_board_structure() 側で吸収する。
        distinct_file_no_count = len(visible_file_nos)
        board_structure = get_board_structure(plan["board_name"]) if plan.get("board_name") else None
        default_color = "blue"
        if board_structure and board_structure.get("board_count") is not None:
            board_count = board_structure["board_count"]
            if float(board_count) == distinct_file_no_count:
                self.lbl_board_structure_count.config(text=f"{board_count:g}", foreground=default_color)
            else:
                self.lbl_board_structure_count.config(
                    text=f"{board_count:g}（実際: {distinct_file_no_count}）", foreground="red",
                )
        else:
            self.lbl_board_structure_count.config(text="未登録", foreground="red")

        self._set_plan_buttons(True)
        self.load_today_log()
        self._setup_ng_side_ui(plan)
        self._load_current_daily_qty(plan["kitting_list_no"], plan["lot_no"])

    def _set_plan_buttons(self, enabled):
        """登録・実績修正のボタンの状態。現場CSV取込画面を開いている間は、計画を選んでいても押せない。"""
        # getattr の既定値は、この属性を持たない偽の画面（tests/test_staging_registration.py）が呼ぶときのため。
        state = tk.NORMAL if enabled and not getattr(self, "_external_actual_locked", False) else tk.DISABLED
        self.btn_register.config(state=state)
        self.btn_correction.config(state=state)

    def open_external_actual_window(self):
        """現場CSV取込画面を開く。開いていれば手前に出すだけ。"""
        if self._external_actual_window is not None and self._external_actual_window.winfo_exists():
            self._external_actual_window.lift()
            return
        self._external_actual_locked = True
        self._set_plan_buttons(False)
        self._external_actual_window = ExternalActualWindow(
            self, current_worker=self.current_worker, on_closed=self._on_external_actual_closed)

    def _on_external_actual_closed(self):
        self._external_actual_locked = False
        self._set_plan_buttons(self.current_plan is not None)

    def _clear_current_plan(self):
        """計画を未選択の状態に戻す（登録・実績修正を押せなくし、NG の欄を空にする）。"""
        self.current_plan = None
        self._set_plan_buttons(False)
        self._reset_ng_side_ui()

    def _load_current_daily_qty(self, kitting_no, lot_no):
        """
        選択中の計画に登録済みの実績があれば、記入欄に表示する。
        1計画＝1レコードで常に上書きするので、日付を問わず検索する。複数件あれば最も新しい日付の値を使う。
        """
        self.entry_daily_qty.delete(0, tk.END)
        existing = get_daily_history(kitting_no, lot_no)
        if existing:
            self.entry_daily_qty.insert(0, f"{existing[-1]['daily_qty']:.0f}")

    def load_today_log(self):
        """
        本日（report_date＝今日）に入力された全計画分の実績を表示する。計画を切り替えても消えない。
        計画の検索には実績の lot_id も渡す（kitting_list_no は lot_no をまたいで重複するため）。
        元のロット・ファイルNoに面2の有効な計画がある面1の行（古い版で入った写しなど）は表示しない（D-170）。表示と _today_all_rows の並びがずれるので、
        ダブルクリックでは位置ではなく iid -> レコードの対応（_today_row_by_iid）で引く。
        """
        for item in self.tree.get_children():
            self.tree.delete(item)

        self._today_all_rows = list_daily_production_today()
        self._today_row_by_iid = {}

        resolved_rows = []
        for rec in self._today_all_rows:
            kitting_list_no = rec["kitting_list_no"] or ""
            rec_lot_no = rec["lot_id"] or ""
            plan = None
            if kitting_list_no:
                plan = find_plan_item_by_kitting_no(kitting_list_no, rec_lot_no) if rec_lot_no \
                    else find_plan_item_by_kitting_no(kitting_list_no)
            if plan:
                lot_no = plan["lot_no"] or ""
                board_name = plan["board_name"] or ""
            else:
                lot_no = rec_lot_no
                board_name = rec["group_id"] or ""
            resolved_rows.append((rec, plan, lot_no, board_name))

        hidden_by_key = {}
        for rec, plan, lot_no, board_name in resolved_rows:
            if plan:
                key = (plan.get("lot_no"), plan.get("setup_file_no"))
                if key not in hidden_by_key:
                    hidden_by_key[key] = bool(side2_plans_hiding(plan))
                if str(plan.get("production_side")).strip() == "1" and hidden_by_key[key]:
                    continue

            iid = self.tree.insert("", tk.END, values=(
                rec["kitting_list_no"] or "", lot_no, board_name,
                rec["report_date"], f"{rec['daily_qty']:.0f}", rec["worker_id"],
            ))
            self._today_row_by_iid[iid] = rec

    def on_history_row_double_click(self, event):
        """
        日次実績履歴の行をダブルクリックすると、対応する計画を search_plan() で呼び出す。
        実績の lot_id も渡す。行は位置ではなく _today_row_by_iid で引く（load_today_log() 参照）。
        """
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        rec = self._today_row_by_iid.get(row_id)
        if rec is None:
            return
        kitting_list_no = rec["kitting_list_no"]
        if not kitting_list_no:
            return

        self.search_plan(kitting_list_no, lot_no=rec["lot_id"] or None)

    def open_correction_window(self):
        if not self.current_plan:
            return
        ActualCorrectionWindow(
            self,
            kitting_list_no=self.current_plan["kitting_list_no"],
            lot_no=self.current_plan["lot_no"],
            on_updated=self.load_plan_list,
            current_worker=self.current_worker,
            on_cancelled=self._on_registration_cancelled,
        )

    def _on_registration_cancelled(self, pending_row_id):
        """実績の取り消しで登録待ちに戻した行を、開いている登録待ち一覧にも足す（D-129）。"""
        if self._csv_staging_window is not None and self._csv_staging_window.winfo_exists():
            self._csv_staging_window.add_restored_row(pending_row_id)

    def open_unified_report(self):
        """実績レポート画面（日報・月報の統合）を開く。"""
        UnifiedReportWindow(self, current_worker=self.current_worker)

    def open_lot_progress(self):
        """ロット進捗チェック画面を開く。開いていれば手前に出すだけ。"""
        if self._lot_progress_window is not None and self._lot_progress_window.winfo_exists():
            self._lot_progress_window.lift()
            self._lot_progress_window.focus_force()
            return
        self._lot_progress_window = LotProgressWindow(self, current_worker=self.current_worker)

    def open_daily_drawdown(self):
        """日々の引落一覧を開く。開いていれば手前に出すだけ。"""
        if self._daily_drawdown_window is not None and self._daily_drawdown_window.winfo_exists():
            self._daily_drawdown_window.lift()
            self._daily_drawdown_window.focus_force()
            return
        self._daily_drawdown_window = DailyDrawdownWindow(self)

    def on_production_csv_import(self):
        """
        実績CSVを解析して保留行として保存し、ステージング一覧を開く。production_daily には書き込まない（「確認・選択・転記」方式）。
        実際の登録は、候補を選んで記入欄に転記した後の通常の登録フロー（_start_registration()）で行う。
        解析は別スレッドで行い、UI を止めない。
        """
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], parent=self.winfo_toplevel())
        if not file_path:
            return

        if not self._confirm_csv_format_for_production_import(file_path):
            return

        # 未処理の保留行が残っていれば、気づかずに続けて取り込まないよう確認する（取込時の上書きルールは変えない）。
        pending_count = len(list_pending_csv_import_rows())
        if pending_count > 0:
            if not messagebox.askyesno(
                "実績CSV取込",
                f"前回の取込データが{pending_count}件残っています。続けて取り込みますか？",
                parent=self.winfo_toplevel(),
            ):
                return

        worker_id = self.current_worker.get("worker_id", "SYSTEM")

        self.btn_production_csv_import.config(state=tk.DISABLED)
        self._csv_import_loading_window = LoadingWindow(self, message="実績CSVを解析しています…")
        threading.Thread(
            target=self._run_csv_parse_in_thread, args=(file_path, worker_id), daemon=True,
        ).start()
        self.after(200, self._poll_csv_import_queue)

    def _confirm_csv_format_for_production_import(self, file_path):
        """
        キッティング計画CSVを誤って読み込ませていないか、ヘッダーの固有列で簡易チェックする
        （計画CSVを実績CSVとして取り込み、払い出し日が空欄の行が457件混入した事故への対策）。
        ヘッダーを読めないときは後続の解析でエラーになるので、ここでは続行する。False なら取込を中止する。
        """
        try:
            header = read_csv_header(file_path)
        except Exception:
            return True

        warnings = detect_format_mismatch_warnings(
            header, PRODUCTION_CSV_SIGNATURE_COLUMNS, PRODUCTION_CSV_FORMAT_LABEL,
            PLAN_CSV_SIGNATURE_COLUMNS, PLAN_CSV_FORMAT_LABEL,
        ) + detect_external_actual_csv_warnings(header, PRODUCTION_CSV_FORMAT_LABEL)
        if not warnings:
            return True

        message = "\n".join(warnings) + "\n\nこのまま取り込みを続けますか？"
        return messagebox.askyesno("CSVフォーマットの確認", message, parent=self.winfo_toplevel())

    def _run_csv_parse_in_thread(self, file_path, worker_id):
        """別スレッドで実行する。ウィジェットには触れず、結果は _csv_import_queue に入れるだけ。"""
        try:
            result = parse_production_csv_for_staging(file_path, default_worker_id=worker_id)
            self._csv_import_queue.put((True, result))
        except Exception as e:
            self._csv_import_queue.put((False, str(e)))

    def _poll_csv_import_queue(self):
        """解析の完了をポーリングで検知し、ロード画面を閉じてステージング一覧を開く。"""
        try:
            success, payload = self._csv_import_queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_csv_import_queue)
            return

        if self._csv_import_loading_window is not None:
            self._csv_import_loading_window.destroy()
            self._csv_import_loading_window = None
        self.btn_production_csv_import.config(state=tk.NORMAL)

        if not success:
            messagebox.showerror("エラー", f"実績CSV取込中にエラーが発生しました：\n{payload}", parent=self.winfo_toplevel())
            return

        imported_count = payload["imported_count"]
        already_registered_count = payload["already_registered_count"]
        warnings = payload["warnings"]

        if warnings:
            shown = "\n".join(warnings[:10])
            more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
            messagebox.showwarning(
                "実績CSV取込：警告", f"警告（{len(warnings)}件）：\n{shown}{more}", parent=self.winfo_toplevel()
            )

        if already_registered_count > 0:
            # 同じ数量で登録済みと判定された行の件数（ステージング一覧には出さない）。
            messagebox.showinfo(
                "実績CSV取込",
                f"{already_registered_count}件は登録済みのためスキップしました。",
                parent=self.winfo_toplevel(),
            )

        already_registered_rows = payload.get("already_registered_rows") or []

        if imported_count == 0 and not already_registered_rows:
            # この CSV から追加した行が無いという意味（以前からの未処理行は「実績CSV取込状況」から開く）。
            messagebox.showinfo("実績CSV取込", "登録対象の行がありませんでした。", parent=self.winfo_toplevel())
            return

        # 追加が0件でも登録済みの行があれば、それを見せるために開く（open_or_notify() も同じ条件で判定する）。
        self.open_pending_csv_staging_window(already_registered_rows=already_registered_rows)

    def open_pending_csv_staging_window(self, already_registered_rows=None):
        """
        実績CSV取込状況（未処理の保留行）を開く。新規取込の直後と「実績CSV取込状況」ボタンの共通の入口。
        未処理行が無ければ open_or_notify() が案内を出すだけで、ウインドウは開かない。
        既に開いていれば多重に開かない（_perform_registration() が lift() する参照と同じインスタンスに保つため）。
        already_registered_rows: 直前の取込で「登録済み」と判定された行。DB に保存されないので、ボタン経由では None。
        開いているウインドウには add_already_registered_rows() で追記する（lift() だけでは結果が失われる）。
        """
        if self._csv_staging_window is not None and self._csv_staging_window.winfo_exists():
            if already_registered_rows:
                self._csv_staging_window.add_already_registered_rows(already_registered_rows)
            self._csv_staging_window.lift()
            return
        self._csv_staging_window = open_or_notify(self, already_registered_rows=already_registered_rows)

    def _on_daily_qty_enter(self, event=None):
        """実績記入欄の Enter。数値かを確かめるだけで DB には書かず、NG 入力欄へ進む。"""
        if not self.current_plan:
            return "break"

        text = self.entry_daily_qty.get().strip()
        try:
            float(text)
        except ValueError:
            messagebox.showwarning("入力エラー", "実績数には数値を入力してください。", parent=self.winfo_toplevel())
            return "break"

        self._focus_first_ng_entry()
        return "break"

    def _focus_first_ng_entry(self):
        """有効な NG 欄（面1、無ければ面2）へフォーカスを移す。両方無効なら登録確認へ進む。"""
        for side in ("1", "2"):
            entry = self._ng_side_entries[side]
            if str(entry.cget("state")) != str(tk.DISABLED):
                entry.focus_set()
                return
        self._start_registration()

    def _on_ng_side1_enter(self, event=None):
        """NG面1欄の Enter。入力の有無を問わず面2欄へ進む（面2が無効なら登録確認へ）。"""
        if not self.current_plan:
            return "break"
        entry2 = self._ng_side_entries["2"]
        if str(entry2.cget("state")) != str(tk.DISABLED):
            entry2.focus_set()
        else:
            self._start_registration()
        return "break"

    def _on_ng_side2_enter(self, event=None):
        """NG面2欄でのEnter：一直線フローの最終段階。登録確認ダイアログを表示する。"""
        if not self.current_plan:
            return "break"
        self._start_registration()
        return "break"

    def _setup_arrow_focus_navigation(self):
        """
        実績記入欄→NG面1→NG面2→登録→実績修正 の間を、左右矢印でフォーカス移動できるようにする。
        Entry ではテキストカーソルの移動と競合しないよう、カーソルが先頭（左）・末尾（右）にあるときだけ移動する。
        """
        self._arrow_nav_widgets = [
            self.entry_daily_qty,
            self._ng_side_entries["1"],
            self._ng_side_entries["2"],
            self.btn_register,
            self.btn_correction,
        ]

        for widget in self._arrow_nav_widgets:
            if isinstance(widget, ttk.Entry):
                widget.bind("<Left>", self._on_arrow_nav_left)
                widget.bind("<Right>", self._on_arrow_nav_right)
            else:
                widget.bind("<Left>", lambda e: self._move_arrow_focus(-1))
                widget.bind("<Right>", lambda e: self._move_arrow_focus(1))

    def _on_arrow_nav_left(self, event):
        entry = event.widget
        if entry.index(tk.INSERT) == 0:
            return self._move_arrow_focus(-1)
        return None

    def _on_arrow_nav_right(self, event):
        entry = event.widget
        if entry.index(tk.INSERT) == entry.index(tk.END):
            return self._move_arrow_focus(1)
        return None

    def _move_arrow_focus(self, delta):
        """フォーカス中のウィジェットを_arrow_nav_widgets内でdelta分（±1）移動する。"""
        widgets = self._arrow_nav_widgets
        current = self.focus_get()
        try:
            current_index = widgets.index(current)
        except ValueError:
            return None
        new_index = (current_index + delta) % len(widgets)
        widgets[new_index].focus_set()
        return "break"

    def _start_registration(self):
        """
        入力を検証し、登録確認ダイアログを出す。ここでは DB を読むだけで、書き込まない。
        書き込みは、確認ダイアログで「登録」を選んだときの _perform_registration() だけ。
        """
        if not self.current_plan:
            return

        # 面2がある面1・「面2待ち」の面1には登録させない（D-170。サービスの入口でも断る）。
        block_reason = side1_registration_block(self.current_plan)
        if block_reason is not None:
            messagebox.showwarning("登録できません", block_reason + "。", parent=self.winfo_toplevel())
            return

        try:
            daily_qty = float(self.entry_daily_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "実績数には数値を入力してください。", parent=self.winfo_toplevel())
            return

        own_qty_by_side, error = self._validate_ng_inputs()
        if error:
            messagebox.showwarning("入力エラー", error, parent=self.winfo_toplevel())
            return

        save_qty_by_side = self._compute_ng_save_qty(own_qty_by_side)
        preview = self._build_registration_preview(daily_qty, save_qty_by_side)

        if not self._show_registration_confirm_dialog(preview):
            self.entry_daily_qty.focus_set()
            return

        self._perform_registration(daily_qty, preview)

    def _validate_ng_inputs(self):
        """
        NG面1・面2の入力を検証する。空欄は0とし、過去の保存値には加算しない（今回の入力だけが正）。
        両面とも空欄でもよい（実績だけの登録を許す）。戻り値は (own_qty_by_side, error_message)。
        """
        own_qty_by_side = {}
        for side in ("1", "2"):
            plan = self._ng_side_plans.get(side)
            if plan is None:
                continue
            text = self._ng_side_entries[side].get().strip()
            if not text:
                own_qty_by_side[side] = 0.0
                continue
            try:
                ng_qty = float(text)
            except ValueError:
                return {}, f"面{side}のNG数量には数値を入力してください。"
            if ng_qty <= 0:
                return {}, f"面{side}のNG数量には0より大きい数値を入力してください。"
            own_qty_by_side[side] = ng_qty
        return own_qty_by_side, None

    def _compute_ng_save_qty(self, own_qty_by_side):
        """
        NG の保存値を計算する（面2の NG は面1にも連動させる）。
          - 面1の保存値 ＝ 面1欄の入力値 ＋ 面2欄の入力値
          - 面2の保存値 ＝ 面2欄の入力値のみ
        合計が0の面は保存しない（既存の NG 申告に触れない）。docs/domain/production_entry.md 参照。
        段階1で services/production_entry_service.compute_ng_save_qty() へ移した。
        """
        return compute_ng_save_qty(own_qty_by_side, self._ng_side_plans)

    def _build_registration_preview(self, daily_qty, save_qty_by_side):
        """
        登録確認ダイアログの内容を、DB に書き込まずに計算する。
        既存の実績は選択中の面だけ確認する（反対側の面には書かない。D-169）。
        登録後の実績は必ず daily_qty になる（1計画＝1レコードで上書き）ので、書き込み前に不一致を判定できる。
        不一致は予定生産数（planned_qty、その面の計画数）と比べる。発注数（order_qty）ではない。
        """
        kitting_no = self.current_plan["kitting_list_no"]
        lot_no = self.current_plan["lot_no"]
        existing = get_daily_history(kitting_no, lot_no)
        existing_daily_qty = existing[-1]["daily_qty"] if existing else None
        existing_report_date = existing[-1]["report_date"] if existing else None
        # これから登録する実績の日付（表示用）。手入力では None（実行日を使う）。
        new_report_date = resolve_csv_report_date(self._pending_csv_report_date)

        mismatch_lines = build_planned_qty_mismatch_lines(self._ng_side_plans, daily_qty, save_qty_by_side)

        # 面1だけの計画で c（生産面マスター未登録）なら、確認ダイアログで理由を示す（D-9x。登録は禁止しない）。b（面2待ち）は登録の前に断る（D-170）。
        side1_only_classification = classify_side1_only_plan(self.current_plan)

        # ロットが完成済みなら確認ダイアログで示す（D-130。手入力も対象、D-133）。
        completed_lot = preview_completed_lot_registration(self.current_plan, daily_qty)

        return {
            "daily_qty": daily_qty,
            "existing_daily_qty": existing_daily_qty,
            "existing_report_date": existing_report_date,
            "new_report_date": new_report_date,
            "save_qty_by_side": save_qty_by_side,
            "mismatch_lines": mismatch_lines,
            "side1_only_classification": side1_only_classification,
            "completed_lot": completed_lot,
        }

    def _show_registration_confirm_dialog(self, preview):
        """
        実績・NG の登録内容をまとめて確認するモーダルダイアログ。Enter＝登録、Esc＝キャンセル。
        「登録」なら True、キャンセルか閉じたら False。
        """
        plan = self.current_plan
        lines = []
        if preview["existing_daily_qty"] is not None:
            existing_date_text = preview.get("existing_report_date") or "(日付不明)"
            new_date_text = preview.get("new_report_date") or "(本日)"
            lines.append(
                f"・登録済みの実績：{existing_date_text}　数量：{preview['existing_daily_qty']:g}"
            )
            lines.append(
                f"・これから登録する実績：{new_date_text}　数量：{preview['daily_qty']:g}"
            )
            lines.append("・続けると、登録済みの実績は置き換えられます。")
        lines.append(f"・本日の実績（{plan['kitting_list_no']}）：{preview['daily_qty']:g}")
        for side in ("1", "2"):
            if side in preview["save_qty_by_side"]:
                side_plan = self._ng_side_plans[side]
                lines.append(
                    f"・面{side}（{side_plan['kitting_list_no']}）のNG数量："
                    f"{preview['save_qty_by_side'][side]:g}"
                )

        completed_lot = preview.get("completed_lot")
        if completed_lot is not None:
            lines.append("")
            lines.append(f"・{format_completed_lot_warning(completed_lot)}。")

        if preview["mismatch_lines"]:
            lines.append("")
            lines.append("以下の面で「実績＋NG数量」が計画数と一致していません：")
            lines.extend(f"　{line}" for line in preview["mismatch_lines"])

        side1_only_classification = preview.get("side1_only_classification")
        if side1_only_classification == SIDE1_ONLY_CLASS_UNREGISTERED:
            lines.append("")
            lines.append(
                "・この計画のセットアップファイルNo・実装ラインの組み合わせは、"
                "生産面マスターに登録がありません（後行面があるかどうか不明です）。"
            )

        # 最小化中だと transient のダイアログが表示されないため、先に元に戻す（UI_WORKFLOW_FIXES_NOTES.md）。
        if self.state() == "iconic":
            self.deiconify()

        dialog = tk.Toplevel(self)
        dialog.title("登録内容の確認")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        result = {"confirmed": False}

        ttk.Label(dialog, text="\n".join(lines), justify=tk.LEFT, padding=15).pack()

        btn_frame = ttk.Frame(dialog, padding=(15, 0, 15, 15))
        btn_frame.pack(fill=tk.X)

        def confirm(event=None):
            result["confirmed"] = True
            dialog.destroy()

        def cancel(event=None):
            result["confirmed"] = False
            dialog.destroy()

        btn_ok = ttk.Button(btn_frame, text="登録", command=confirm)
        btn_ok.pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="キャンセル", command=cancel).pack(side=tk.RIGHT)

        dialog.bind("<Return>", confirm)
        dialog.bind("<Escape>", cancel)
        dialog.protocol("WM_DELETE_WINDOW", cancel)

        btn_ok.focus_set()
        center_window(dialog, self)
        dialog.wait_window()
        return result["confirmed"]

    def confirm_overwrite_if_existing(self, kitting_list_no, lot_no, new_daily_qty, new_report_date_raw):
        """
        ステージング一覧の右クリック即時登録の前に呼ぶ。既に実績があるときだけ、置き換えの確認ダイアログを出す。
        実績が無ければ確認なしで True（即時性を保つ）。False のとき、呼び出し元は登録も保留行の削除もしないこと。
        """
        existing = get_daily_history(kitting_list_no, lot_no)
        if not existing:
            return True

        last = existing[-1]
        new_report_date = resolve_csv_report_date(new_report_date_raw)
        existing_date_text = last.get("report_date") or "(日付不明)"
        new_date_text = new_report_date or "(本日)"
        message = (
            f"この計画（{kitting_list_no}）には既に実績が登録されています。\n\n"
            f"・登録済みの実績：{existing_date_text}　数量：{last['daily_qty']:g}\n"
            f"・これから登録する実績：{new_date_text}　数量：{new_daily_qty:g}\n\n"
            "続けると、登録済みの実績は置き換えられます。続けますか？"
        )
        return messagebox.askyesno("既存の実績を置き換えます", message, parent=self.winfo_toplevel())

    def load_staged_row(self, kitting_list_no, lot_no, daily_qty=None, report_date=None, on_registered=None,
                        focus_entry=False):
        """
        ステージング画面から呼ばれる。CSV の行の計画を表示し、実績数を記入欄に転記する（登録はしない）。
        report_date・on_registered は、この画面で登録するときに使う払い出し日と、実績の登録が成功したら保留行を消すコールバック。
        daily_qty が None なら、記入欄は search_plan() が入れた登録済みの値のままにする。
        """
        self.search_plan(kitting_list_no, lot_no=lot_no)
        if daily_qty is not None:
            self.entry_daily_qty.delete(0, tk.END)
            self.entry_daily_qty.insert(0, f"{daily_qty:g}")
        self._pending_csv_row_removal = on_registered
        self._pending_csv_report_date = report_date
        if focus_entry:
            self.entry_daily_qty.focus_set()

    def refresh_after_external_registration(self, lot_nos, new_cumulative=None):
        """
        ステージング画面から呼ばれる。この画面を通さずに登録した後、表示中の計画と、lot_nos の計画一覧の行を更新する。
        CSV 由来の登録待ちはクリアする。new_cumulative は表示中の計画の登録後の累計（None ならその表示は変えない）。
        更新の順序は _perform_registration() と同じ。
        """
        self._pending_csv_row_removal = None
        self._pending_csv_report_date = None
        if new_cumulative is not None:
            self.lbl_app_cum.config(text=f"{new_cumulative:.0f}")
        self.load_today_log()
        if self.current_plan:
            self._load_current_daily_qty(self.current_plan["kitting_list_no"], self.current_plan["lot_no"])
            self._setup_ng_side_ui(self.current_plan)
        for lot_no in lot_nos:
            self._refresh_plan_list_for_lot(lot_no)

    def _perform_registration(self, daily_qty, preview, record_history=True):
        """
        確認ダイアログで「登録」が選ばれた後、実績→NG の順に登録する（書き込みは register_actual_and_ng()）。
        途中でエラーが出ても、成功した分は残してエラーを表示する（ロールバックしない）。
        record_history=False は Shift+S 一括登録用（呼び出し元が最後にロット単位でまとめて記録する）。
        実績の日付は CSV の払い出し日（手入力なら実行日）、NG の日付は当日。
        登録後も同じ計画のまま続くので、最後に記入欄を最新の保存値で入れ直す。
        """
        worker_id = self.current_worker.get("worker_id", "SYSTEM")
        kitting_no = self.current_plan["kitting_list_no"]
        lot_no = self.current_plan["lot_no"]
        report_date = resolve_csv_report_date(self._pending_csv_report_date)
        self._pending_csv_report_date = None
        # _pending_csv_row_removal は直後にクリアされるので、最後の lift() の要否判定用に先に控えておく。
        from_csv_staging = self._pending_csv_row_removal is not None

        result = register_actual_and_ng(
            self.current_plan, self._ng_side_plans, daily_qty, preview["save_qty_by_side"],
            preview["existing_daily_qty"], worker_id, self.current_worker.get("name", "unknown"),
            report_date=report_date, record_history=record_history,
        )
        if not result["actual_registered"]:
            messagebox.showerror("登録エラー", f"実績の登録に失敗しました：{result['actual_error']}", parent=self.winfo_toplevel())
            return

        # CSV 経由なら、実績の登録が成功したらその行を一覧から消す。
        # NG の保存の成否は問わない（CSV 行が表すのは実績数量だけで、NG は別枠のため）。
        if self._pending_csv_row_removal is not None:
            self._pending_csv_row_removal()
            self._pending_csv_row_removal = None

        new_cumulative = result["new_cumulative"]

        self.lbl_app_cum.config(text=f"{new_cumulative:.0f}")
        self.load_today_log()
        self._load_current_daily_qty(kitting_no, lot_no)
        self._setup_ng_side_ui(self.current_plan)
        # 同じ lot_no の行だけ部分更新する。すべての書き込み（実績・NG）の後に呼ぶこと。
        self._refresh_plan_list_for_lot(lot_no)

        messagebox.showinfo(
            "登録完了", format_registration_message(result, preview["save_qty_by_side"]), parent=self.winfo_toplevel(),
        )

        # CSV 経由なら一覧を手前に戻し、キーボードフォーカスも一覧へ渡す。lift() だけではフォーカスが記入欄に残り、
        # 一覧で押した Shift+S が文字入力になる（D-84）。topmost は後に開くダイアログを隠すので使わない。
        if from_csv_staging and self._csv_staging_window is not None \
                and self._csv_staging_window.winfo_exists():
            if self._csv_staging_window.state() == "iconic":
                self._csv_staging_window.deiconify()
            self._csv_staging_window.lift()
            self._csv_staging_window.focus_force()
            self._csv_staging_window.tree.focus_set()
        else:
            # 手動登録では、続けて入力できるよう実績記入欄へ戻す。
            self.entry_daily_qty.focus_set()

    def _setup_ng_side_ui(self, plan):
        """
        計画選択時に、面1・面2の NG 入力欄を更新する。反対側の計画が無い面の欄は無効にする。
        現在の NG 申告（日付を問わない「1計画・面＝1レコード」）があれば、その値を入れておく。
          - 面2: 保存値をそのまま表示する
          - 面1: 「面1固有分」＝面1保存値−面2保存値 を表示する（0以下なら空欄）。
            面1には毎回「面1欄＋面2欄」を保存するので、保存値をそのまま出すと、何も変えずに再登録したとき面2分が二重に加算される
        """
        side = str(plan.get("production_side") or "").strip()

        if side in ("1", "2"):
            other_side = "2" if side == "1" else "1"
            opposite_plan = find_opposite_side_plan(
                plan.get("lot_no"), plan.get("setup_file_no"), side,
                current_plan_start_datetime=plan.get("plan_start_datetime"),
            )
            self._ng_side_plans = {side: plan, other_side: opposite_plan}
            selected_side = side
        else:
            # production_sideが1/2以外（想定外データ）の場合は両面とも対象外として扱う
            self._ng_side_plans = {"1": None, "2": None}
            selected_side = None

        plan_2 = self._ng_side_plans.get("2")
        declared_2 = 0.0
        if plan_2 is not None:
            declaration_2 = get_ng_declaration(plan_2["kitting_list_no"], 2, lot_no=plan_2["lot_no"])
            if declaration_2:
                declared_2 = declaration_2["ng_qty"]

        for s in ("1", "2"):
            side_plan = self._ng_side_plans.get(s)
            entry = self._ng_side_entries[s]
            # ttk.Entry は DISABLED のままだと insert/delete が無視されるので、先に NORMAL に戻す。
            entry.config(state=tk.NORMAL)
            entry.delete(0, tk.END)
            if side_plan is None:
                entry.config(state=tk.DISABLED)
            else:
                declaration = get_ng_declaration(
                    side_plan["kitting_list_no"], int(s), lot_no=side_plan["lot_no"],
                )
                declared_qty = declaration["ng_qty"] if declaration else None
                if declared_qty is not None:
                    if s == "1":
                        own_only = declared_qty - declared_2
                        if own_only > 0:
                            entry.insert(0, f"{own_only:g}")
                    else:
                        entry.insert(0, f"{declared_qty:g}")
            suffix = "（選択中）" if s == selected_side else ""
            self._ng_side_labels[s].config(text=f"NG 面{s}{suffix}：")

    def _reset_ng_side_ui(self):
        """計画未選択・検索失敗時に、NG入力欄を初期状態（両面無効・空欄）に戻す。"""
        self._ng_side_plans = {"1": None, "2": None}
        for s in ("1", "2"):
            entry = self._ng_side_entries[s]
            entry.config(state=tk.NORMAL)
            entry.delete(0, tk.END)
            entry.config(state=tk.DISABLED)
            self._ng_side_labels[s].config(text=f"NG 面{s}：")
