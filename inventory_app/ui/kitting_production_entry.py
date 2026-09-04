# ui/kitting_production_entry.py
import threading
import queue
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkcalendar import DateEntry
from services.production_service import (
    search_plan_by_kitting_no,
    register_daily_result,
    overwrite_daily_result,
    register_opposite_side_daily_result,
    get_daily_history,
    calculate_lot_completion,
    update_daily_result,
    delete_daily_result,
)
from services.production_import_service import parse_production_csv_for_staging
from models.kitting_plan import list_active_plan_items, find_opposite_side_plan, find_plan_item_by_kitting_no
from models.production import list_daily_production_today
from models.ng_declarations import save_ng_declaration, get_ng_declaration
from models.board_structure_master import get_board_structure
from ui.daily_report_window import DailyReportWindow
from ui.monthly_report_window import MonthlyReportWindow
from ui.plan_candidate_dialog import select_plan_candidate_by_lot
from ui.production_import_staging_window import ProductionImportStagingWindow
from ui.loading_window import LoadingWindow


def _resolve_csv_report_date(raw_value):
    """
    実績CSVステージング一覧経由の払い出し日（raw_value、COLUMN_MAP_PRODUCTIONの
    report_date列からそのまま渡された未検証の文字列）を、register_daily_result()/
    overwrite_daily_result()のreport_date引数（"YYYY-MM-DD"形式を期待）として
    使える形に検証する。

    値が無い（None・空欄）、または"YYYY-MM-DD"としてパースできない場合は
    Noneを返す（呼び出し元はreport_date=Noneのまま渡すことになり、
    register_daily_result()/overwrite_daily_result()側のデフォルト動作
    （実行日を使う）にフォールバックする）。実際のCSVでの表記が未確認のため、
    現時点では"YYYY-MM-DD"以外の形式（例："YYYY/MM/DD"）への変換は行わない
    （誤った日付を採用するより、安全側でフォールバックする方針）。
    """
    if not raw_value:
        return None
    value = str(raw_value).strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


class KittingProductionEntryWindow(tk.Toplevel):
    # <<TreeviewSelect>>（矢印キーでも発火）のたびに毎回search_plan()（DBアクセス）を
    # 行うと、キー連打時に無駄な処理が積み重なるため、選択が止まってから
    # このミリ秒だけ待って実行するデバウンスを行う。
    PLAN_SELECT_DEBOUNCE_MS = 200

    def __init__(self, parent, current_worker, preloaded_plan_rows=None):
        """
        preloaded_plan_rows：呼び出し元（ui.main_window.open_kitting_production_entry()）が
        別スレッドで事前に _fetch_plan_list_rows() を実行し取得しておいた計画一覧データ。
        渡された場合はDBへ再アクセスせずそのままTreeviewに反映する（初回表示を
        UIスレッドでブロックしないため）。省略時はこれまで通り__init__内で
        同期的に取得する（後方互換）。
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
        # 実績CSVステージング一覧（ui.production_import_staging_window）で
        # 候補選択→転記した行を、実際の登録成功時に一覧から消すためのコール
        # バック。一度に1件分のみ保持する（確認ダイアログはモーダルのため、
        # 転記から登録完了までの間に別の登録が割り込むことは通常無い想定）。
        self._pending_csv_row_removal = None
        # 上記と同時にセットする、CSV行が持つ払い出し日（"YYYY-MM-DD"形式を期待）。
        # _perform_registration()でregister_daily_result()/overwrite_daily_result()の
        # report_dateとして使う（CSV経由でない通常の手動登録ではNoneのまま＝
        # 従来通り実行日が使われる）。
        self._pending_csv_report_date = None

        # 実績CSV取込（on_production_csv_import()）の非同期パース用。
        # ui.kitting_plan_import.KittingPlanImportWindow.on_start_import()と同じ
        # LoadingWindow + threading.Thread(daemon=True) + queue.Queue +
        # self.after(200, ...)ポーリングのパターンを踏襲する
        # （ui.main_window.MainWindow.open_kitting_production_entry()も同じ
        # パターンで本ウィンドウ自体の計画一覧取得を非同期化している）。
        self._csv_import_queue = queue.Queue()
        self._csv_import_loading_window = None

        # 計画一覧の絞り込み基盤：
        # - _all_plan_rows：_fetch_plan_list_rows() の全件結果（フィルタ前）。
        #   Treeviewに現在表示されている行はこの部分集合に過ぎない。
        # - _plan_filter_vars：列key -> テキスト部分一致フィルタ入力欄のStringVar。
        # - _plan_checkbox_filters：列key -> 選択済み値の集合（チェックボックス式。
        #   キーが存在しない列＝絞り込み無し。全選択状態はOK確定時にキーごと除去する）。
        # - _plan_checkbox_buttons：列key -> ▼ボタンウィジェット（絞り込み中の見た目切替用）。
        # - _plan_col_index：列key -> rowタプル内でのインデックス（cols_plan準拠）。
        # - _hide_completed_var：「入力済みを隠す」チェックボックスの状態。
        #   order_qty・actual_qtyという2列をまたいだ判定のため、他の列単位フィルタ
        #   （_plan_filter_predicates()）とは別立てでapply_plan_filters()内で適用する。
        #   デフォルトはFalse（完了済みも含めて表示）。
        # - _plan_date_from_entry/_plan_date_to_entry：「実装開始予定日」の期間指定用
        #   DateEntry（tkcalendar）。空欄＝その側の境界なし。create_widgets()で生成する
        #   （ウィジェット生成前はNone）。
        # - _plan_row_iid_by_kitting_no：(kitting_list_no, lot_no) -> 現在Treeviewに
        #   挿入されている行のiid。_populate_plan_list_tree()実行のたびに、その時点で
        #   実際にTreeviewへ挿入した行だけで作り直す（全件表示時はフルセット、
        #   絞り込み表示時はその部分集合のみが入る＝フィルタで非表示中の行は
        #   このマップに存在しない）。_refresh_plan_list_for_lot()が、登録直後に
        #   DBを再取得せず該当行だけを直接書き換えるために使う。
        #   キーをkitting_list_no単体ではなく(kitting_list_no, lot_no)のタプルに
        #   しているのは、実DBで同一kitting_list_noが複数の異なるlot_noに
        #   またがって存在するケースが478件確認されているため（他のkitting_list_no
        #   単体キーで同種の事故が起きた既知のバグパターンと同じ理由。
        #   models.kitting_plan.get_app_cumulative_qty_bulk()等を参照）。
        self._all_plan_rows = []
        self._plan_filter_vars = {}
        self._plan_checkbox_filters = {}
        self._plan_checkbox_buttons = {}
        self._plan_col_index = {}
        self._plan_filter_labels = {}
        self._hide_completed_var = tk.BooleanVar(value=False)
        self._plan_date_from_entry = None
        self._plan_date_to_entry = None
        self._plan_row_iid_by_kitting_no = {}

        # NG（仕損）数量入力：面1・面2固定の2スロット。
        # - _ng_side_plans：production_side("1"/"2") -> その面の計画dict（無ければNone）。
        #   選択中の計画はcurrent_plan、反対側はfind_opposite_side_plan()の結果を
        #   production_sideをキーに振り分けて保持する。
        # - _ng_side_entries/_ng_side_labels：production_side -> ウィジェット（create_widgets()で生成）。
        self._ng_side_plans = {"1": None, "2": None}
        self._ng_side_entries = {}
        self._ng_side_labels = {}

        # 日次実績履歴（self.tree）は「選択中計画に閉じた表示」から「本日の全計画分の
        # ログ」に変更した。load_today_log()で取得した全件（models.production.
        # list_daily_production_today()の生レコード）をそのまま保持する
        # （表示側で面1除外フィルタをかけても、元データは全件保持し続ける）。
        self._today_all_rows = []
        # Treeviewのiid→元レコードの対応（表示行のフィルタ有無に関わらず、
        # on_history_row_double_click()が正しい行を逆引きできるようにするため、
        # tree.index()による位置対応ではなくiidで直接引く）。
        self._today_row_by_iid = {}

        # 実績・NG入力のEnterキーによる一直線フロー：
        # 実績記入欄Enter→NG面1欄Enter→NG面2欄Enter→登録確認ダイアログ、の順に
        # フォーカスが進み、最後に登録確認ダイアログで実際の登録を行う
        # （_on_daily_qty_enter()・_on_ng_side1_enter()・_on_ng_side2_enter()・
        # _start_registration()参照）。途中の各EnterではDBへ一切書き込まない。

        # 左右矢印キーでの主要ウィジェット間フォーカス移動の対象一覧。
        # create_widgets()内でウィジェット生成後に実体を格納する。
        self._arrow_nav_widgets = []

        self.title("生産実績入力（キッティングリストNo.）")
        # left_frame内の各フレーム（info_frame/entry_frame/hist_frame）の自然要求
        # 高さの合計に対し十分な余裕を持たせた高さ（1080p等の一般的なディスプレイ
        # でも十分収まる）。拡張可能（expand=True）な唯一の要素であるhist_frameが
        # 不足分を吸収してしまい極端に潰れることのないよう、850px以上を保つこと。
        # 縦850pxだと画面からはみ出す環境があるため700pxに縮小した。
        # 実測（1150x850時点）：info_frame reqheight=289px・entry_frame
        # （実績+NG入力統合）reqheight=147px・hist_frame reqheight=265px
        # （それぞれpack pady=5の上下10pxずつを含む）。info_frame・entry_frameは
        # fill=tk.X（expand無し）のため常に自然サイズが確保され、expand=True・
        # fill=tk.BOTHのhist_frameのみが縮小分を吸収する設計（左側3フレームの
        # pack順序による優先度）。実際に1150x700で検証したところ、
        # info_frame・entry_frameは289px/147pxのまま変化せず、hist_frameのみ
        # 265px→234px（約1行分）に縮み、登録ボタン等は引き続きウィンドウ内に
        # 収まることを確認済み。
        self.geometry("1150x700")

        self.create_widgets()

    def create_widgets(self):
        container = ttk.Frame(self)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Frame(container)
        left_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        right_frame = ttk.Labelframe(container, text="計画一覧", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 15), pady=5)

        # 計画情報表示エリア
        # キッティングリストNo.検索欄は廃止し、右ペインの計画一覧（tree_plan_list、
        # キッティングNo.列のテキスト絞り込み込み）からの行選択に一本化した
        # （on_select_plan_list()参照）。
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

        # 実績・NG入力エリア（1つの枠に統合）。Enterキーで実績記入欄→NG面1欄→
        # NG面2欄→登録確認ダイアログ、と一直線に進める操作フローに対応する
        # （_on_daily_qty_enter()・_on_ng_side1_enter()・_on_ng_side2_enter()参照）。
        entry_frame = ttk.LabelFrame(left_frame, text="本日の生産実績・NG（仕損）入力", padding=10)
        entry_frame.pack(fill=tk.X, padx=15, pady=5)

        daily_row = ttk.Frame(entry_frame)
        daily_row.pack(fill=tk.X, pady=2)
        ttk.Label(daily_row, text="本日生産実績：").pack(side=tk.LEFT, padx=5)
        self.entry_daily_qty = ttk.Entry(daily_row, width=10)
        self.entry_daily_qty.pack(side=tk.LEFT, padx=5)
        self.entry_daily_qty.bind("<Return>", self._on_daily_qty_enter)
        # 記入欄にフォーカスがあっても上下矢印キーで計画一覧の選択行を移動できるように
        # する（フォーカス自体はentry_daily_qtyに留まる。_move_plan_selection()参照）。
        self.entry_daily_qty.bind("<Up>", lambda e: self._move_plan_selection(-1))
        self.entry_daily_qty.bind("<Down>", lambda e: self._move_plan_selection(1))

        # NG（仕損）数量入力：面1・面2固定の2行。
        # 計画選択時（search_plan()）に_setup_ng_side_ui()で有効/無効・ラベルを更新する。
        # 生成直後は両面とも計画未選択のため無効化しておく。
        for side in ("1", "2"):
            row = ttk.Frame(entry_frame)
            row.pack(fill=tk.X, pady=2)
            label = ttk.Label(row, text=f"NG 面{side}：")
            label.pack(side=tk.LEFT, padx=5)
            entry = ttk.Entry(row, width=10, state=tk.DISABLED)
            entry.pack(side=tk.LEFT, padx=5)
            # 実績記入欄と同様、NG記入欄にフォーカスがあっても上下矢印キーで
            # 計画一覧の選択行を移動できるようにする。
            entry.bind("<Up>", lambda e: self._move_plan_selection(-1))
            entry.bind("<Down>", lambda e: self._move_plan_selection(1))
            self._ng_side_labels[side] = label
            self._ng_side_entries[side] = entry

        self._ng_side_entries["1"].bind("<Return>", self._on_ng_side1_enter)
        self._ng_side_entries["2"].bind("<Return>", self._on_ng_side2_enter)

        btn_row = ttk.Frame(entry_frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))

        # 実績登録・NG登録は1つの「登録」ボタン・1つの登録確認ダイアログに統合した
        # （_start_registration()参照）。
        self.btn_register = ttk.Button(btn_row, text="登録", command=self._start_registration,
                                        state=tk.DISABLED)
        self.btn_register.pack(side=tk.LEFT, padx=5)

        self.btn_correction = ttk.Button(btn_row, text="実績修正", command=self.open_correction_window,
                                          state=tk.DISABLED)
        self.btn_correction.pack(side=tk.LEFT, padx=5)

        # 左右矢印キーでの主要ウィジェット間フォーカス移動（Tabキー順序の左右矢印版）。
        # 対象ウィジェットが全て生成された直後に設定する。
        self._setup_arrow_focus_navigation()

        # 履歴表示エリア（left_frame内で残りの縦スペースを使う唯一のexpand=True要素。
        # report_btn_frame（日報/月報/実績CSV取込ボタン）はright_frame側の「更新」ボタンと
        # 横並びに移設したため、left_frame側にはside=tk.BOTTOMで固定高さを先取りする
        # フレームが無くなり、以前必要だった「BOTTOM要素を先にpackして高さを確保する」
        # ワークアラウンドはそもそも不要になっている。）
        hist_frame = ttk.LabelFrame(left_frame, text="日次実績履歴（本日の全計画分）", padding=10)
        hist_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=5)

        # 「選択中計画に閉じた表示」から「本日の全計画分のログ」に変更したため、
        # どの計画の実績かを識別するkitting_list_no（必須）・lot_no・基板名を追加した。
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
        # 履歴行のダブルクリックで対応する計画を呼び出す（項目11）。
        self.tree.bind("<Double-1>", self.on_history_row_double_click)

        # tree_plan_list（計画一覧）のvsb_planと同じパターンで垂直スクロールバーを追加。
        # hist_frameが縮んで全件表示できない場合の唯一の閲覧手段になるため、
        # 【1】のpack順修正とセットで必須。
        vsb_hist = ttk.Scrollbar(hist_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb_hist.set)
        vsb_hist.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)

        # 計画一覧エリア（右側）
        cols_plan = ("list_no", "lot_no", "plan_start_datetime", "file_no", "board_name",
                     "planned_qty", "order_qty", "actual_qty", "diff", "lot_completed", "lot_remaining")
        self._plan_col_index = {key: i for i, key in enumerate(cols_plan)}

        # 絞り込みエリア（Treeviewの上）。既存の「キッティングリストNo.検索」search_frameと
        # 同じスタイル（ラベル＋Entryの横並び）。
        # - list_no/plan_start_datetime/planned_qty/order_qty/actual_qty/diff/lot_completed/
        #   lot_remaining：テキスト部分一致（_plan_filter_vars）。
        # - lot_no/file_no/board_name：エクセルのオートフィルタ風チェックボックス式ポップアップ
        #   （_plan_checkbox_filters）。distinct値が数百件規模のため、テキスト部分一致より
        #   候補を見ながら選べるこちらの方式にしている。
        # いずれも最終的には _plan_filter_predicates() で同じ「col_key -> callable(value)->bool」
        # という述語形式に正規化されるため、apply_plan_filters() 側は列の実装方式を意識しない。
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

        # order_qty・actual_qtyという2列をまたいだ判定のため、他の列単位フィルタ
        # （_plan_filter_predicates()）とは別に、apply_plan_filters()内で追加適用する。
        ttk.Checkbutton(
            filter_row2, text="入力済みを隠す", variable=self._hide_completed_var,
            command=self.apply_plan_filters,
        ).pack(side=tk.LEFT, padx=(15, 5))

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

        vsb_plan = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_plan_list.yview)
        self.tree_plan_list.configure(yscrollcommand=vsb_plan.set)

        # 列幅合計（980px）が実際の表示幅を超過しているため、水平スクロールバーを追加
        # （発注数以降の列が横スクロールなしでは確認できないため）。
        hsb_plan = ttk.Scrollbar(right_frame, orient="horizontal", command=self.tree_plan_list.xview)
        self.tree_plan_list.configure(xscrollcommand=hsb_plan.set)

        # pack順序の注意：Tkのpackはside=BOTTOM/TOPを問わずpackを呼んだ順にcavityを
        # 消費するため、下部ボタン行（bottom_btn_frame）をhsb_planより先にside=tk.BOTTOMで
        # packし、ウィンドウ最下端の帯を先に確保する。その後hsb_planを同じくside=tk.BOTTOMで
        # packすると、その時点の（ボタン確保後の）最下端＝Treeviewの直下にhsb_planが
        # 配置される。逆順（従来の実装）だと、hsb_planが先にウィンドウ最下端を
        # 確保してしまい、後からpackされるボタン行がhsb_planとTreeviewの間に
        # 割り込んでしまい、hsb_planがTreeviewから視覚的に切り離された位置
        # （ボタンのさらに下）に表示されていた。
        #
        # 「更新」ボタンと日報出力・月報出力・実績CSV取込ボタンは、元々left_frame側の
        # 独立したフレーム（report_btn_frame）にあったが、右側の計画一覧の操作と
        # まとめて横並びにする方が導線として自然なため、この1つのフレームへ統合した。
        bottom_btn_frame = ttk.Frame(right_frame)
        bottom_btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

        ttk.Button(bottom_btn_frame, text="更新", command=self.load_plan_list).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5)
        )
        self.btn_daily_report = ttk.Button(bottom_btn_frame, text="日報出力", command=self.open_daily_report)
        self.btn_daily_report.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.btn_monthly_report = ttk.Button(bottom_btn_frame, text="月報出力", command=self.open_monthly_report)
        self.btn_monthly_report.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))

        self.btn_production_csv_import = ttk.Button(
            bottom_btn_frame, text="実績CSV取込", command=self.on_production_csv_import
        )
        self.btn_production_csv_import.pack(side=tk.LEFT, expand=True, fill=tk.X)

        hsb_plan.pack(side=tk.BOTTOM, fill=tk.X)
        vsb_plan.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_plan_list.pack(expand=True, fill=tk.BOTH)
        self.tree_plan_list.bind("<<TreeviewSelect>>", self.on_select_plan_list)
        self.tree_plan_list.bind("<Double-1>", self.on_plan_cell_double_click)

        if self._preloaded_plan_rows is not None:
            # 呼び出し元が別スレッドで事前取得済み（main_window.open_kitting_production_entry()）。
            # ここでは再度DBへアクセスせず、そのままTreeviewへ反映する。
            self._all_plan_rows = self._preloaded_plan_rows
            self._populate_plan_list_tree(self._preloaded_plan_rows)
        else:
            self.load_plan_list()

        # 日次実績履歴は「本日の全計画分のログ」のため、計画選択前（画面を開いた直後）
        # から表示しておく。
        self.load_today_log()

    def _add_plan_filter_entry(self, parent, col_key, label_text, width):
        """絞り込みエリアに列1つ分のラベル+Entryを追加し、StringVarを登録する。"""
        ttk.Label(parent, text=f"{label_text}:").pack(side=tk.LEFT, padx=(5, 2))
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.pack(side=tk.LEFT, padx=(0, 5))
        # 絞り込みは self._all_plan_rows に対するメモリ内の文字列部分一致でしかなく
        # DBアクセスを伴わないため、キー入力のたびに即時反映してもコストは無視できる
        # （生産実績入力画面の計画一覧側で行ったDBアクセスを伴う操作のデバウンスとは
        # 性質が異なる）。
        entry.bind("<KeyRelease>", self.apply_plan_filters)
        self._plan_filter_vars[col_key] = var

    def _add_plan_date_range_filter(self, parent):
        """
        絞り込みエリアに「実装開始予定日」の期間指定用UIを追加する（開始日・終了日の
        DateEntryを2つ）。日報・月報画面（ui/daily_report_window.py等）と同じ形式
        （date_pattern="yyyy-mm-dd", locale="ja_JP"）で統一する。

        plan_start_datetime の実データ形式（"YYYY/MM/DD HH:MM:SS"、スラッシュ区切り＋
        時刻付き）とDateEntryの出力（"YYYY-MM-DD"、ハイフン区切り・日付のみ）には差異が
        あるため、比較は _plan_date_range_predicate() 側で吸収する。ここでは生成と
        イベントバインドのみ行う。

        空欄＝その側は絞り込み無し（未入力状態から開始する。DateEntryは通常
        当日日付が初期選択されているため、生成直後に明示的にクリアする）。
        """
        ttk.Label(parent, text=f"{self._plan_filter_labels['plan_start_datetime']}:").pack(
            side=tk.LEFT, padx=(5, 2)
        )
        self._plan_date_from_entry = DateEntry(parent, date_pattern="yyyy-mm-dd", width=10, locale="ja_JP")
        self._plan_date_from_entry.delete(0, tk.END)
        self._plan_date_from_entry.pack(side=tk.LEFT, padx=(0, 2))

        ttk.Label(parent, text="〜").pack(side=tk.LEFT, padx=(0, 2))

        self._plan_date_to_entry = DateEntry(parent, date_pattern="yyyy-mm-dd", width=10, locale="ja_JP")
        self._plan_date_to_entry.delete(0, tk.END)
        self._plan_date_to_entry.pack(side=tk.LEFT, padx=(0, 5))

        for date_entry in (self._plan_date_from_entry, self._plan_date_to_entry):
            date_entry.bind("<<DateEntrySelected>>", self.apply_plan_filters)
            date_entry.bind("<KeyRelease>", self.apply_plan_filters)

    def _plan_date_range_predicate(self):
        """
        「実装開始予定日」の期間フィルタ（開始日・終了日DateEntry）から、
        plan_start_datetime列用の述語（value: str -> bool）を組み立てる。
        両方空欄なら None を返す（絞り込み無し）。

        DateEntryの出力"YYYY-MM-DD"と、plan_start_datetimeの実データ形式
        "YYYY/MM/DD HH:MM:SS"との差異（区切り文字・時刻の有無）を、日付部分の
        先頭10文字を取り出しスラッシュ区切りに統一した上での文字列比較で吸収する
        （ゼロ埋め済みのため辞書順比較がそのまま時系列順になる）。
        """
        from_text = self._plan_date_from_entry.get().strip() if self._plan_date_from_entry else ""
        to_text = self._plan_date_to_entry.get().strip() if self._plan_date_to_entry else ""
        if not from_text and not to_text:
            return None

        from_date = from_text.replace("-", "/") if from_text else None
        to_date = to_text.replace("-", "/") if to_text else None

        def predicate(value):
            date_part = value[:10].replace("-", "/")
            if from_date and date_part < from_date:
                return False
            if to_date and date_part > to_date:
                return False
            return True

        return predicate

    def _add_plan_checkbox_filter_button(self, parent, col_key):
        """
        絞り込みエリアに、エクセルのオートフィルタ風チェックボックス式ポップアップを開く
        ▼ボタンを1列分追加する。ttk.Buttonではテーマによって背景色を変更できないことが
        あるため、「絞り込み中」の見た目切替（色変更）のため素のtk.Buttonを使う。
        """
        label_text = self._plan_filter_labels[col_key]
        button = tk.Button(
            parent, text=f"{label_text} ▼", relief=tk.RAISED,
            command=lambda c=col_key: self.open_plan_checkbox_filter_popup(c),
        )
        button.pack(side=tk.LEFT, padx=(5, 5))
        self._plan_checkbox_buttons[col_key] = button
        self._plan_checkbox_default_bg = button.cget("background")

    def _update_plan_filter_button_style(self, col_key):
        """指定列のチェックボックス式フィルタが有効かどうかをボタンの見た目に反映する。"""
        button = self._plan_checkbox_buttons.get(col_key)
        if button is None:
            return
        label_text = self._plan_filter_labels[col_key]
        active = col_key in self._plan_checkbox_filters
        button.configure(
            text=f"{label_text} ▼●" if active else f"{label_text} ▼",
            background="#cfe8ff" if active else self._plan_checkbox_default_bg,
        )

    def open_plan_checkbox_filter_popup(self, col_key):
        """
        lot_no/file_no/board_name用の、エクセルのオートフィルタ風チェックボックス式
        絞り込みポップアップを開く。

        distinct値の算出方針：この列自身のフィルタを除いた、現在の他の全フィルタ
        （テキスト・チェックボックス問わず）を適用した結果の中でのdistinct値とする
        （エクセルのオートフィルタの一般的な挙動に合わせた）。これにより、他の条件で
        既に絞り込まれている状況でも「今その条件下で実際に選べる値」だけが候補に出る。
        """
        label_text = self._plan_filter_labels[col_key]
        col_index = self._plan_col_index[col_key]

        other_predicates = self._plan_filter_predicates()
        other_predicates.pop(col_key, None)
        if other_predicates:
            base_rows = [row for row in self._all_plan_rows if self._plan_row_matches(row, other_predicates)]
        else:
            base_rows = self._all_plan_rows

        full_values = sorted({str(row[col_index]) for row in base_rows})

        current_selection = self._plan_checkbox_filters.get(col_key)
        checked_values = set(full_values) if current_selection is None else set(current_selection)

        # selfが最小化状態だと、transient(self)したポップアップがstate()="withdrawn"
        # のまま実際には表示されない（grab_set()は効くため、見えないポップアップが
        # 入力を握ったままになる）。ui.plan_candidate_dialog._show_candidate_list_dialog()
        # と同じ理由・同じ対策（UI_WORKFLOW_FIXES_NOTES.md参照）。
        if self.state() == "iconic":
            self.deiconify()

        popup = tk.Toplevel(self)
        popup.title(f"{label_text} の絞り込み")
        popup.geometry("280x420")
        popup.transient(self)
        popup.grab_set()

        ttk.Label(popup, text="検索：").pack(anchor=tk.W, padx=10, pady=(10, 0))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(popup, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=10, pady=(0, 5))
        search_entry.focus_set()

        list_outer = ttk.Frame(popup)
        list_outer.pack(expand=True, fill=tk.BOTH, padx=10)

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
        row_widgets = {
            value: ttk.Checkbutton(checklist_frame, text=value, variable=check_vars[value])
            for value in full_values
        }

        def rebuild_visible(*_args):
            needle = search_var.get().strip().lower()
            for widget in row_widgets.values():
                widget.pack_forget()
            for value in full_values:
                if needle and needle not in value.lower():
                    continue
                row_widgets[value].pack(anchor=tk.W, fill=tk.X)

        rebuild_visible()
        search_var.trace_add("write", rebuild_visible)

        btn_frame1 = ttk.Frame(popup)
        btn_frame1.pack(fill=tk.X, padx=10, pady=(5, 0))

        def select_all():
            for var in check_vars.values():
                var.set(True)

        def deselect_all():
            for var in check_vars.values():
                var.set(False)

        ttk.Button(btn_frame1, text="全選択", command=select_all).pack(side=tk.LEFT)
        ttk.Button(btn_frame1, text="全解除", command=deselect_all).pack(side=tk.LEFT, padx=(5, 0))

        btn_frame2 = ttk.Frame(popup)
        btn_frame2.pack(fill=tk.X, padx=10, pady=10)

        def on_ok():
            selected = {value for value, var in check_vars.items() if var.get()}
            if selected == set(full_values):
                # 全選択状態は「絞り込みなし」として扱う
                self._plan_checkbox_filters.pop(col_key, None)
            else:
                self._plan_checkbox_filters[col_key] = selected
            self._update_plan_filter_button_style(col_key)
            popup.destroy()
            self.apply_plan_filters()

        def on_cancel():
            popup.destroy()

        ttk.Button(btn_frame2, text="OK", command=on_ok).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        ttk.Button(btn_frame2, text="キャンセル", command=on_cancel).pack(side=tk.LEFT, expand=True, fill=tk.X)

        return popup

    @staticmethod
    def _fetch_plan_list_rows():
        """
        計画一覧のDBアクセス部分のみを行う（Tkinterウィジェットには一切触れない）。
        インスタンス状態に依存しないため、インスタンス生成前・別スレッドからでも
        呼び出せる（main_window.open_kitting_production_entry()の非同期化で利用）。
        sqlite3接続は呼び出し先の各関数がそれぞれ都度 get_connection() で新規に
        張るため、スレッドをまたいで接続オブジェクトを共有することはない。

        戻り値：Treeviewへそのまま渡せる values タプルのリスト。
        """
        rows = []
        lot_completion_cache = {}

        # include_completed=True：完了済み計画も常に取得しておき、表示/非表示は
        # 「入力済みを隠す」チェックボックス（apply_plan_filters()）側で切り替える。
        # find_matching_plan_items()（実績CSV自動取込）は list_active_plan_items() を
        # デフォルト（include_completed=False）のまま呼んでおり、こちらの変更の影響は受けない。
        for plan_item in list_active_plan_items(include_completed=True):
            kitting_list_no = plan_item["kitting_list_no"]
            lot_no = plan_item["lot_no"]
            planned_qty = plan_item["planned_qty"] or 0
            order_qty = plan_item["order_qty"] or 0
            # list_active_plan_items() が完了判定用に計算済みの値をそのまま再利用し、
            # 同じkitting_list_noに対する重複呼び出しを避ける。
            actual_qty = plan_item["app_cumulative_qty"]
            diff = order_qty - actual_qty

            if lot_no not in lot_completion_cache:
                lot_completion_cache[lot_no] = calculate_lot_completion(lot_no)
            lot_info = lot_completion_cache[lot_no]
            lot_completed = lot_info["completed_quantity"]
            lot_remaining = lot_info["remaining_quantity"]

            rows.append((
                kitting_list_no,
                lot_no,
                plan_item["plan_start_datetime"] or "",
                plan_item["setup_file_no"],
                plan_item["board_name"],
                f"{planned_qty:.0f}",
                f"{order_qty:.0f}",
                f"{actual_qty:.0f}",
                f"{diff:.0f}",
                f"{lot_completed:.0f}",
                f"{lot_remaining:.0f}",
            ))

        return rows

    def _populate_plan_list_tree(self, rows):
        """
        渡された行データ（全件、またはフィルタ後の部分集合）でTreeviewを更新する。
        UIスレッド専用。全件データの保持・絞り込みの適用はこのメソッドの責務ではない
        （呼び出し元がどのデータを渡すか決める）。

        併せて self._plan_row_iid_by_kitting_no（(kitting_list_no, lot_no) -> iid）
        を、この呼び出しで実際にTreeviewへ挿入した行だけで作り直す
        （_refresh_plan_list_for_lot()が登録直後の部分更新に使う）。
        """
        self._close_cell_edit_entry()

        for item in self.tree_plan_list.get_children():
            self.tree_plan_list.delete(item)

        self._plan_row_iid_by_kitting_no = {}
        for values in rows:
            iid = self.tree_plan_list.insert("", tk.END, values=values)
            self._plan_row_iid_by_kitting_no[(values[0], values[1])] = iid

    def load_plan_list(self):
        """
        DB取得とTreeview更新をまとめて同期的に行う（「更新」ボタン等から使用）。
        v1として、更新のたびに絞り込み条件はリセットする（ソートもTreeview再構築に
        伴い解除される。sort_plan_list()はTreeviewの現在の表示内容を直接並べ替える
        実装のため、再構築後は元の取得順に戻る）。
        """
        rows = self._fetch_plan_list_rows()
        self._all_plan_rows = rows
        for var in self._plan_filter_vars.values():
            var.set("")
        self._plan_checkbox_filters.clear()
        for col_key in self._plan_checkbox_buttons:
            self._update_plan_filter_button_style(col_key)
        self._hide_completed_var.set(False)
        if self._plan_date_from_entry is not None:
            self._plan_date_from_entry.delete(0, tk.END)
        if self._plan_date_to_entry is not None:
            self._plan_date_to_entry.delete(0, tk.END)
        self._populate_plan_list_tree(rows)

    def _refresh_plan_list_for_lot(self, lot_no):
        """
        実績・NG登録完了直後、計画一覧（tree_plan_list）のうち同一lot_noに属する
        行だけを部分更新する（_perform_registration()から呼ぶ）。

        load_plan_list()のような全件再取得（list_active_plan_items(include_
        completed=True)で全lot_noを走査し、lot_no単位でcalculate_lot_completion()
        を都度呼ぶ）は行わず、models.kitting_plan.list_active_plan_items(lot_no=lot_no,
        include_completed=True)でDB側から絞り込んだ上で取得する。この引数は部分
        一致（LIKE）のため、意図しない他lot_noの誤マッチ（例：lot_no="100075"の
        絞り込みに"1100075"等が混入）を避けるため、取得後にlot_no完全一致で
        再フィルタする。calculate_lot_completion(lot_no)も対象lot_noについて1回
        だけ呼ぶ（_fetch_plan_list_rows()のlot単位キャッシュと異なり、ここでは
        対象lot_noが常に1つのみのためキャッシュ自体が不要）。

        面連動（register_opposite_side_daily_result()による面1への自動登録）で
        更新された行も、同一lot_noに属する限りlist_active_plan_items(lot_no=lot_no)
        の結果に自動的に含まれるため、ここで別途の考慮は不要。

        self._all_plan_rows（フィルタ前の全件データ、_fetch_plan_list_rows()と
        同じtuple形式）を該当行だけ書き換え、self._plan_row_iid_by_kitting_no
        （_populate_plan_list_tree()実行時点でTreeviewに挿入済みの行のみを持つ
        マップ）にiidがある行だけself.tree_plan_list.set()で反映する。絞り込みで
        現在非表示の行はiidが存在しないためTreeview更新をスキップするが、
        _all_plan_rows側は更新しておく（絞り込み解除時に古い値が再表示される
        事故を防ぐ）。

        Treeviewは既存iidへの.set()のみで削除・再挿入を行わないため、
        sort_plan_list()によるTreeview上の並び順（move()で管理、行の挿入順とは
        無関係）にも、選択状態にも影響しない。
        """
        plan_items = list_active_plan_items(lot_no=lot_no, include_completed=True)
        plan_items = [item for item in plan_items if item.get("lot_no") == lot_no]
        if not plan_items:
            return

        lot_info = calculate_lot_completion(lot_no)
        lot_completed = lot_info["completed_quantity"]
        lot_remaining = lot_info["remaining_quantity"]

        row_index_by_key = {
            (row[0], row[1]): i for i, row in enumerate(self._all_plan_rows)
        }
        plan_list_cols = sorted(self._plan_col_index, key=self._plan_col_index.get)

        for plan_item in plan_items:
            kitting_list_no = plan_item["kitting_list_no"]
            planned_qty = plan_item["planned_qty"] or 0
            order_qty = plan_item["order_qty"] or 0
            actual_qty = plan_item["app_cumulative_qty"]
            diff = order_qty - actual_qty

            new_row = (
                kitting_list_no,
                lot_no,
                plan_item["plan_start_datetime"] or "",
                plan_item["setup_file_no"],
                plan_item["board_name"],
                f"{planned_qty:.0f}",
                f"{order_qty:.0f}",
                f"{actual_qty:.0f}",
                f"{diff:.0f}",
                f"{lot_completed:.0f}",
                f"{lot_remaining:.0f}",
            )

            key = (kitting_list_no, lot_no)
            row_index = row_index_by_key.get(key)
            if row_index is not None:
                self._all_plan_rows[row_index] = new_row

            iid = self._plan_row_iid_by_kitting_no.get(key)
            if iid is not None and self.tree_plan_list.exists(iid):
                for col, value in zip(plan_list_cols, new_row):
                    self.tree_plan_list.set(iid, col, value)

    def _plan_filter_predicates(self):
        """
        現在のフィルタ状態（テキスト入力欄＋チェックボックス式ポップアップ）から、
        列ごとの述語関数（value: str -> bool）の辞書を組み立てる。
        絞り込みが指定されていない列は辞書に含めない（絞り込み対象外）。

        テキスト部分一致・チェックボックス選択のいずれも最終的には同じ
        「col_key -> callable(value)->bool」という形に正規化されるため、
        apply_plan_filters() / open_plan_checkbox_filter_popup() 側は
        列の絞り込み方式の違いを意識しない。
        """
        predicates = {}
        for col_key, var in self._plan_filter_vars.items():
            text = var.get().strip()
            if not text:
                continue
            needle = text.lower()
            predicates[col_key] = lambda value, needle=needle: needle in value.lower()

        for col_key, selected_values in self._plan_checkbox_filters.items():
            predicates[col_key] = lambda value, selected=selected_values: value in selected

        date_range_predicate = self._plan_date_range_predicate()
        if date_range_predicate is not None:
            predicates["plan_start_datetime"] = date_range_predicate

        return predicates

    def _plan_row_matches(self, row, predicates):
        for col_key, predicate in predicates.items():
            col_index = self._plan_col_index[col_key]
            if not predicate(str(row[col_index])):
                return False
        return True

    def apply_plan_filters(self, event=None):
        """
        self._all_plan_rows に対して現在の全フィルタ条件をAND条件で適用し、
        結果をTreeviewへ反映する（DBへは一切アクセスしない）。

        「入力済みを隠す」（order_qty・actual_qtyという2列をまたいだ判定）は、
        列単位の述語（_plan_filter_predicates()）の形に馴染まないため、
        列フィルタ適用後の結果に対して別立てで追加適用する。
        """
        predicates = self._plan_filter_predicates()
        if not predicates:
            filtered = self._all_plan_rows
        else:
            filtered = [row for row in self._all_plan_rows if self._plan_row_matches(row, predicates)]

        if self._hide_completed_var.get():
            order_index = self._plan_col_index["order_qty"]
            actual_index = self._plan_col_index["actual_qty"]
            filtered = [
                row for row in filtered
                if float(row[actual_index]) < float(row[order_index])
            ]

        self._populate_plan_list_tree(filtered)

    def clear_plan_filters(self):
        """全フィルタ（テキスト入力欄＋チェックボックス式＋期間指定＋入力済みを隠す）をクリアし、全件表示に戻す。"""
        for var in self._plan_filter_vars.values():
            var.set("")
        self._plan_checkbox_filters.clear()
        for col_key in self._plan_checkbox_buttons:
            self._update_plan_filter_button_style(col_key)
        self._hide_completed_var.set(False)
        if self._plan_date_from_entry is not None:
            self._plan_date_from_entry.delete(0, tk.END)
        if self._plan_date_to_entry is not None:
            self._plan_date_to_entry.delete(0, tk.END)
        self.apply_plan_filters()

    def on_select_plan_list(self, event):
        """
        計画一覧の選択（クリック・矢印キーいずれも<<TreeviewSelect>>で発火）。
        計画情報表示・履歴読み込み（search_plan()、DBアクセスを伴う）はデバウンスし、
        矢印キー連打中は実行しない。

        tree_plan_list の選択行は values[0] に kitting_list_no、values[1] に
        lot_no を持つ（cols_plan参照）。実DBで同一kitting_list_noが複数の異なる
        lot_noにまたがって存在するケースが478件確認されており、この行選択の
        時点で両方とも一意に判明しているため、_pending_plan_select_kitting_no・
        _pending_plan_select_lot_no に保持しておき、デバウンス確定後の
        search_plan() へ直接渡す（曖昧な単体検索を経由しない）。
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
        デバウンス確定後（矢印キー連打中は発火しない）にsearch_plan()を実行し、
        その後で生産実績記入欄（entry_daily_qty）へフォーカスを移す。
        on_select_plan_list()側（デバウンス予約のみ行う生の選択イベントハンドラ）で
        即座にフォーカスを移すと、矢印キーでの計画一覧ナビゲーション中に最初の
        キー入力でTreeviewからフォーカスが逃げてしまい、以降の矢印キーが
        entry_daily_qty側に取られてリストナビゲーションが機能しなくなるため、
        ここ（選択が確定した後）で行う。

        on_select_plan_list()が保持しておいたkitting_list_no・lot_noをそのまま
        search_plan()へ渡す。
        """
        self._plan_select_debounce_id = None
        self.search_plan(self._pending_plan_select_kitting_no, lot_no=self._pending_plan_select_lot_no)
        self.entry_daily_qty.focus_set()

    def _move_plan_selection(self, delta):
        """
        実績記入欄・NG記入欄にフォーカスがある状態でも、上下矢印キーで計画一覧
        （tree_plan_list）の選択行を1つ上（delta=-1）/下（delta=+1）に移動できる
        ようにする。tree_plan_list側の選択・フォーカス行を実際に動かした上で
        <<TreeviewSelect>>を発火させ、既存のon_select_plan_list()→デバウンス→
        search_plan()の流れをそのまま利用する。

        フォーカスはこの関数自体では動かさない。デバウンス確定後の
        _on_plan_select_debounced()が最後にentry_daily_qtyへフォーカスを戻す処理を
        既に行っているため、呼び出し元（entry_daily_qtyやNG記入欄）から見て
        フォーカスは実質的に記入欄側に留まる。
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

        def sort_key(value):
            if col in numeric_cols:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

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
        計画一覧のセルをダブルクリックした際、そのセルの上に一時的なEntryを重ねて
        テキストを全選択状態で表示する（Treeviewは標準でセル内テキストのコピーに
        対応していないための簡易的なコピー手段）。フォーカスが外れる・Escape/Enter
        が押されると元のTreeview表示に戻る。
        計画を開く処理（旧on_plan_double_click）はワンクリック選択に統合したため、
        ここでは行わない。
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
        指定されたkitting_list_no・lot_noから計画を検索し、画面に表示する。

        呼び出し元は右ペインの計画一覧の行選択（on_select_plan_list()→
        _on_plan_select_debounced()）、日次実績履歴のダブルクリック
        （on_history_row_double_click()）、または実績CSVステージング一覧
        （_on_csv_staging_row_confirmed()）のいずれかで、いずれも選択・確定した
        時点で既にkitting_list_no・lot_noの両方を把握した上で本関数を呼ぶ
        （キッティングリストNo.欄への直接手入力による検索は廃止し、計画一覧からの
        選択に一本化したため、lot_noが不明なままこの関数が呼ばれることは無くなった。
        そのため search_plan_by_kitting_no() が複数候補を返す＝候補選択ダイアログ
        （ui.plan_candidate_dialog）が必要になるケースも発生しない）。

        実績CSVステージング一覧経由の登録待ち（self._pending_csv_row_removal・
        self._pending_csv_report_date）があれば、ここでクリアする：CSV行の
        候補選択直後は_on_csv_staging_row_confirmed()がこの呼び出しの直後に
        改めてセットするため影響が無い一方、CSVの選択を経ずに別の計画へ
        切り替えた場合（計画一覧からの通常の行選択等）に、古いCSV行の
        remove_callback・払い出し日が無関係な登録で誤って使われてしまう
        事故を防ぐ。
        """
        self._pending_csv_row_removal = None
        self._pending_csv_report_date = None
        plan, _candidates = search_plan_by_kitting_no(kitting_list_no, lot_no)

        if not plan:
            messagebox.showerror("検索エラー", f"キッティングリストNo. {kitting_list_no} の計画が見つかりません。", parent=self.winfo_toplevel())
            self.current_plan = None
            self.btn_register.config(state=tk.DISABLED)
            self.btn_correction.config(state=tk.DISABLED)
            self._reset_ng_side_ui()
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

        # 構成基板数マスタ（models.board_structure_master、CSVインポートのみで
        # 更新される参照専用マスタ）から、board_nameで検索して表示する。
        # 表記ゆれ（全角/半角・大小文字・空白）は get_board_structure() 側で
        # 正規化して吸収するため、ここでは plan["board_name"] をそのまま渡す。
        board_structure = get_board_structure(plan["board_name"]) if plan.get("board_name") else None
        if board_structure and board_structure.get("board_count") is not None:
            self.lbl_board_structure_count.config(text=f"{board_structure['board_count']:g}")
        else:
            self.lbl_board_structure_count.config(text="未登録")

        # 同一setup_file_noで面2が存在する場合、面1は完成品ではないため表示から
        # 除外する（models.kitting_plan.list_active_plan_items()の
        # 「2回目計画があれば1回目除外」ロジックと同じ考え方）。
        #
        # lot_file_actualsのキーは、services.production_service.
        # calculate_lot_completion()の変更により(setup_file_no, production_side)の
        # 2要素になった（以前は(setup_file_no, production_side, kitting_list_no)の
        # 3要素で、file_no単位で複数バッチが同時アクティブな場合はバッチごとに
        # 個別の行として表示していたが、file_no×面単位で実績を合算する方式に
        # 変更されたことに伴い、特定の1バッチを名指しする意味が無くなったため
        # 表示からkitting_list_noを外した）。
        second_side_setup_files = {
            file_no for (file_no, side) in plan["lot_file_actuals"]
            if str(side).strip() == "2"
        }
        file_actuals_text = "\n".join(
            f"{file_no}（面{side}）: {qty:.0f}"
            for (file_no, side), qty in plan["lot_file_actuals"].items()
            if not (str(side).strip() == "1" and file_no in second_side_setup_files)
        )
        self.lbl_lot_file_actuals.config(text=file_actuals_text or "-")

        self.btn_register.config(state=tk.NORMAL)
        self.btn_correction.config(state=tk.NORMAL)
        self.load_today_log()
        self._setup_ng_side_ui(plan)
        self._load_current_daily_qty(plan["kitting_list_no"], plan["lot_no"])

    def _load_current_daily_qty(self, kitting_no, lot_no):
        """
        選択中の計画に、現在の実績が既に登録されていれば、その数量を生産実績記入欄へ
        表示する（_start_registration()の「既に実績が登録されています」判定と
        整合する値）。未登録なら空欄のままにする。

        以前はreport_date=当日限定で検索していたが、models.production.
        replace_daily_result()により「1計画（kitting_list_no・lot_no）=1レコード、
        常に上書き」となったため、report_dateを問わない全期間検索
        （get_daily_history(kitting_no, lot_no)）に変更した。当日限定のままだと、
        実績が過去日付で登録されたまま当日中に未更新の計画を選択した際、記入欄が
        誤って空欄のまま表示されてしまっていた。

        既存レコードが複数件ある場合（本仕様変更前の過去データ等）は、最も新しい
        report_dateのレコードを表示する（get_daily_history()はreport_date昇順で
        返すため末尾）。

        lot_noを渡すのは、実DBで同一kitting_list_noが複数の異なるlot_noにまたがって
        存在するケースが478件確認されているため。
        """
        self.entry_daily_qty.delete(0, tk.END)
        existing = get_daily_history(kitting_no, lot_no)
        if existing:
            self.entry_daily_qty.insert(0, f"{existing[-1]['daily_qty']:.0f}")

    def load_today_log(self):
        """
        日次実績履歴（画面上部の一覧）を「本日（report_date=今日）に入力された
        全計画分のログ」として表示する。選択中の計画に関わらず、その日に入力された
        全ての実績が並ぶ（計画を切り替えても履歴は消えない）。過去分も含めた
        個別の修正・削除は引き続き「実績修正」ボタン（ActualCorrectionWindow、
        全期間を表示する別画面）から行う。

        models.production.list_daily_production_today()で当日のproduction_daily
        全件を取得する（NG申告はproduction_dailyに含まれないため対象外）。
        取得した生レコードをそのままself._today_all_rowsに保持する（表示側で
        面1除外フィルタをかけても、元データ自体は変更しない）。

        各行のロットNo・基板名は、日報画面（_build_report_rows()）と同じパターンで
        kitting_list_noからfind_plan_item_by_kitting_no()により補完する（計画が
        見つからない場合は、production_daily側に記録済みの値（登録時点のスナップ
        ショット）にフォールバックする）。

        find_plan_item_by_kitting_no()には、rec自身が持つrec["lot_id"]（その実績が
        実際に登録されたlot_no）も一緒に渡す。実DBで同一kitting_list_noが複数の
        異なるlot_noにまたがって存在するケースが478件確認されており、
        kitting_list_noだけの検索ではどちらの計画が返るか不定になるため
        （_build_report_rows()と同じ理由）。

        表示フィルタ：同一(lot_no, setup_file_no)で面2の計画が存在する行がある
        場合、面1の行は完成品ではないため一覧から除外する
        （models.kitting_plan.list_active_plan_items()の「2回目計画があれば
        1回目除外」ロジックと同じ考え方）。判定のため、Treeviewへの挿入前に
        全レコードの計画解決を1回済ませ、面2が存在する(lot_no, setup_file_no)の
        集合を作ってから、挿入するレコードを絞り込む。

        除外により見た目上の行と self._today_all_rows の対応が崩れるため、
        on_history_row_double_click()はTreeview上の位置（tree.index()）ではなく、
        挿入時に記録するiid→レコードの対応（self._today_row_by_iid）で逆引きする。
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

        second_side_keys = {
            (lot_no, plan.get("setup_file_no"))
            for _rec, plan, lot_no, _board_name in resolved_rows
            if plan and str(plan.get("production_side")).strip() == "2"
        }

        for rec, plan, lot_no, board_name in resolved_rows:
            if plan:
                production_side = str(plan.get("production_side")).strip()
                key = (lot_no, plan.get("setup_file_no"))
                if production_side == "1" and key in second_side_keys:
                    continue

            iid = self.tree.insert("", tk.END, values=(
                rec["kitting_list_no"] or "", lot_no, board_name,
                rec["report_date"], f"{rec['daily_qty']:.0f}", rec["worker_id"],
            ))
            self._today_row_by_iid[iid] = rec

    def on_history_row_double_click(self, event):
        """
        日次実績履歴（本日の全計画ログ）の行をダブルクリックすると、対応する計画を
        直接search_plan()へ渡して呼ぶ（既存のUIパターン：
        ui.daily_report_window.DailyReportWindow.on_row_double_click()や
        ui.ng_input_window.NgInputWindow.on_ng_list_double_click()と同じ、
        「行→保持データからkitting_list_noを逆引き→対応する処理を呼ぶ」導線）。
        計画情報表示・NG欄・実績記入欄は、search_plan()内の既存処理
        （_setup_ng_side_ui()・_load_current_daily_qty()呼び出し）でまとめて更新される。

        self._today_row_by_iid[row_id]はproduction_dailyの生レコードであり、
        その実績が実際に登録されたlot_no（lot_id列）を持っている。実DBで同一
        kitting_list_noが複数の異なるlot_noにまたがって存在するケースが478件
        確認されているため、このlot_idをsearch_plan()へ渡し、曖昧な単体検索を
        経由しないようにする。

        面2が存在する場合に面1の行を表示から除外するフィルタ（load_today_log()）
        により、Treeview上の見た目の行順とself._today_all_rowsの並びは一致しない
        ため、tree.index()による位置参照ではなく、挿入時に記録したiid→レコードの
        対応（self._today_row_by_iid）で直接引く。
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
        )

    def open_daily_report(self):
        DailyReportWindow(self)

    def open_monthly_report(self):
        MonthlyReportWindow(self)

    def on_production_csv_import(self):
        """
        実績CSV（lot_no + 製品名ベース）を解析するが、この時点ではDBへ一切
        書き込まない（「確認・選択・転記」方式）。解析結果は
        ProductionImportStagingWindow に一覧表示し、行をダブルクリックした
        際に候補選択ダイアログ（ui.plan_candidate_dialog.select_plan_candidate_by_lot()）
        →計画確定→実績記入欄への転記、という流れで
        _on_csv_staging_row_confirmed() に処理を委ねる。実際の登録は既存の
        「実績記入欄→NG面1→NG面2→登録確認ダイアログ→登録」フロー
        （_start_registration()）にそのまま乗せる。

        以前はimport_production_csv()で即時登録していたが、CSVの内容を
        確認せずに自動登録されることを避けたいという方針変更により、
        パース専用のservices.production_import_service.
        parse_production_csv_for_staging()を使うよう変更した
        （import_production_csv()自体は後方互換のため変更していない）。

        parse_production_csv_for_staging()はファイル読み込み・DBアクセス
        （候補計画の検索）のみを行いTkinterには一切触れないため、UIスレッドで
        同期実行すると行数の多いCSVでは画面がフリーズしたように見える。
        ui.kitting_plan_import.KittingPlanImportWindow.on_start_import()で
        確立済みのパターン（LoadingWindow表示→threading.Thread(daemon=True)で
        重い処理→queue.Queueで結果受け渡し→self.after(200, ...)ポーリング→
        LoadingWindow.destroy()）をそのまま踏襲し、パース中もUIスレッドが
        ブロックされないようにする。
        """
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], parent=self.winfo_toplevel())
        if not file_path:
            return

        worker_id = self.current_worker.get("worker_id", "SYSTEM")

        self.btn_production_csv_import.config(state=tk.DISABLED)
        self._csv_import_loading_window = LoadingWindow(self, message="実績CSVを解析しています…")
        threading.Thread(
            target=self._run_csv_parse_in_thread, args=(file_path, worker_id), daemon=True,
        ).start()
        self.after(200, self._poll_csv_import_queue)

    def _run_csv_parse_in_thread(self, file_path, worker_id):
        """
        別スレッドで実行する部分。Tkinterウィジェットには一切触れず、結果は
        self._csv_import_queueへ put するのみ（UIスレッド側のポーリング
        （_poll_csv_import_queue()）が受け取って画面へ反映する）。
        """
        try:
            result = parse_production_csv_for_staging(file_path, default_worker_id=worker_id)
            self._csv_import_queue.put((True, result))
        except Exception as e:
            self._csv_import_queue.put((False, str(e)))

    def _poll_csv_import_queue(self):
        """
        _run_csv_parse_in_thread()の完了をポーリングで検知し、UIスレッド上で
        ロード画面を閉じてステージング一覧（ProductionImportStagingWindow）を
        表示する。
        """
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

        staged_rows = payload["rows"]
        warnings = payload["warnings"]

        if warnings:
            shown = "\n".join(warnings[:10])
            more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
            messagebox.showwarning(
                "実績CSV取込：警告", f"警告（{len(warnings)}件）：\n{shown}{more}", parent=self.winfo_toplevel()
            )

        if not staged_rows:
            messagebox.showinfo("実績CSV取込", "登録対象の行がありませんでした。", parent=self.winfo_toplevel())
            return

        ProductionImportStagingWindow(self, staged_rows, self._on_csv_staging_row_confirmed)

    def _on_csv_staging_row_confirmed(self, row, remove_callback):
        """
        実績CSVステージング一覧（ProductionImportStagingWindow）の行が
        ダブルクリックされた際に呼ばれる。候補選択ダイアログで計画を確定させ、
        既存のsearch_plan()で計画情報を表示した上で、実績記入欄にCSVの
        daily_qtyを転記する。

        転記後は既存の一直線フロー（実績記入欄→NG面1→NG面2→登録確認
        ダイアログ→登録、_start_registration()）にそのまま委ねる（ここでは
        登録処理を呼ばない）。remove_callbackは_perform_registration()の
        登録成功時に呼び出し、ステージング一覧から該当行を消す
        （self._pending_csv_row_removalに保持しておく）。CSV行の払い出し日
        （row["report_date"]）も同時にself._pending_csv_report_dateへ保持し、
        _perform_registration()でregister_daily_result()/overwrite_daily_result()の
        report_dateとして使う。

        キャンセル時は何もしない（ステージング一覧の行はそのまま残る）。
        """
        chosen = select_plan_candidate_by_lot(
            self, row["lot_no"], row["product_name"], row["candidates"], row["matched"],
            row.get("report_date"),
        )
        if chosen is None:
            return

        self.search_plan(chosen["kitting_list_no"], chosen["lot_no"])
        self.entry_daily_qty.delete(0, tk.END)
        self.entry_daily_qty.insert(0, f"{row['daily_qty']:g}")
        self._pending_csv_row_removal = remove_callback
        self._pending_csv_report_date = row.get("report_date")
        self.entry_daily_qty.focus_set()

    def _on_daily_qty_enter(self, event=None):
        """
        実績記入欄でのEnterキー：一直線フローの1段階目。入力値が数値として妥当か
        検証するだけで、DBへは一切書き込まない。妥当であれば、NG面1欄（無効なら
        NG面2欄）へフォーカスを移す（_focus_first_ng_entry()）。
        """
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
        """
        NG面1欄が有効（対象計画あり）ならそちらへ、無効（片面のみの計画で面1が
        存在しない）ならNG面2欄へフォーカスを移す。両面とも無効な場合（計画未選択
        時のみ想定、通常この関数は計画選択済みの場合にしか呼ばれない）は、
        フォーカス移動をせずそのまま登録確認へ進む。
        """
        for side in ("1", "2"):
            entry = self._ng_side_entries[side]
            if str(entry.cget("state")) != str(tk.DISABLED):
                entry.focus_set()
                return
        self._start_registration()

    def _on_ng_side1_enter(self, event=None):
        """
        NG面1欄でのEnter：一直線フローの2段階目。入力の有無を問わずNG面2欄へ
        フォーカスを移す（面2欄が無効＝片面のみの計画の場合は、そのまま登録確認へ
        進む）。
        """
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
        生産実績記入欄・NG記入欄（面1・面2）・登録ボタン・実績修正ボタンの間を、
        左右矢印キーで順番にフォーカス移動できるようにする（Tabキー順序の
        左右矢印版）。実績・NG登録は1つの「登録」ボタンに統合済みのため、
        対象は実績記入欄→NG面1欄→NG面2欄→登録ボタン→実績修正ボタンの5つ
        （画面上の並び順と一致させている）。

        テキスト入力欄（Entry）では、矢印キーでのテキストカーソル移動と競合しない
        よう、カーソルが欄の先頭にある場合のみ<Left>で前のウィジェットへ、末尾にある
        場合のみ<Right>で次のウィジェットへ移動する（それ以外の位置では通常の
        テキストカーソル移動をそのまま行わせる＝ハンドラ内でbreakを返さない）。
        ボタンにはテキストカーソルの概念が無いため、矢印キーで無条件に移動する。
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
        実績・NG入力の内容を検証し、登録確認ダイアログを表示する（統合登録ボタン、
        またはNG面2欄でのEnter、いずれからも呼ばれる一直線フローの最終段階）。

        ここではDBへの問い合わせ（既存レコードの有無の確認、_build_registration_
        preview()参照）のみを行い、書き込みは一切行わない。実際の書き込みは、
        確認ダイアログで「登録」が選ばれた場合にのみ_perform_registration()で行う。
        キャンセル時・入力エラー時は何もせず実績記入欄へフォーカスを戻す。
        """
        if not self.current_plan:
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
        NG面1・面2の入力欄を検証する。「今回入力された固有分」を空欄は0として
        扱い、過去の保存値への加算は行わない（今回の入力のみが正）。両面とも
        空欄でもエラーにはしない（NGを入力せず実績のみを登録することを許容する。
        NG面1欄・面2欄いずれもEnterでは「入力の有無を問わず」次へ進む仕様のため）。

        戻り値：(own_qty_by_side, error_message) のタプル。error_messageが
        Noneでなければ入力エラーがあったことを示し、その場合own_qty_by_sideは
        空辞書で意味を持たない。
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
        面2欄の値を面1へ連動させたNG保存値を計算する。
          - 面1への保存値 ＝ 面1欄の入力値 ＋ 面2欄の入力値（どちらか一方が空でも
            もう片方の値がそのまま反映される。例：面1欄=空・面2欄=5 → 面1へ5）
          - 面2への保存値 ＝ 面2欄の入力値のみ（面1欄の値は面2に一切影響しない）
        合計が0（＝両面とも未入力）の面は保存対象に含めない
        （save_ng_declaration()を呼ばない＝既存のNG申告に触れない）。
        """
        own_2 = own_qty_by_side.get("2", 0.0)
        save_qty_by_side = {}
        if self._ng_side_plans.get("1") is not None:
            total_1 = own_qty_by_side.get("1", 0.0) + own_2
            if total_1 > 0:
                save_qty_by_side["1"] = total_1
        if self._ng_side_plans.get("2") is not None and own_2 > 0:
            save_qty_by_side["2"] = own_2
        return save_qty_by_side

    def _build_registration_preview(self, daily_qty, save_qty_by_side):
        """
        登録確認ダイアログに表示する内容を、DBへ一切書き込まずに事前計算する。

        既存レコードの有無：self.current_plan（選択中の面）についてのみ
        get_daily_history(kitting_no, lot_no)（report_date不問の全期間検索、
        _load_current_daily_qty()と同じ考え方）で確認する。反対側の面の既存
        レコードは、選択中の面で既にユーザーが確認済みという前提で（従来通り）
        確認なしで自動上書きするため、ここでは見ない。

        不一致判定：models.production.replace_daily_result()の「1計画（kitting_
        list_no・lot_no）=1レコード、常に上書き」ルールにより、登録後の実績値は
        常にdaily_qtyそのものになる（選択中の面・反対側の面いずれも、連動により
        同じdaily_qtyに揃うため）。そのためDBへの書き込みを待たず、daily_qtyを
        そのまま使って判定できる（従来の_warn_ng_quantity_mismatch()が登録後に
        DBへ問い合わせていたのに対し、書き込み前に同じ結果を計算できる）。

        比較対象はplanned_qty（予定生産数、その面の計画数量）であり、order_qty
        （発注数、ロット全体の注文数量）ではない（以前はorder_qtyと比較していたが、
        1面分の実績+NGの合計を比較する対象としてはplanned_qtyの方が実態に
        合っているため変更した）。
        """
        kitting_no = self.current_plan["kitting_list_no"]
        lot_no = self.current_plan["lot_no"]
        existing = get_daily_history(kitting_no, lot_no)
        existing_daily_qty = existing[-1]["daily_qty"] if existing else None

        mismatch_lines = []
        for side in ("1", "2"):
            plan = self._ng_side_plans.get(side)
            if plan is None:
                continue
            ng_qty = save_qty_by_side.get(side, 0.0)
            planned_qty = plan.get("planned_qty") or 0
            total = daily_qty + ng_qty
            if total != planned_qty:
                mismatch_lines.append(
                    f"面{side}（{plan['kitting_list_no']}）：実績{daily_qty:.0f} + "
                    f"NG{ng_qty:.0f} = {total:.0f}（予定生産数{planned_qty:.0f}と不一致）"
                )

        return {
            "daily_qty": daily_qty,
            "existing_daily_qty": existing_daily_qty,
            "save_qty_by_side": save_qty_by_side,
            "mismatch_lines": mismatch_lines,
        }

    def _show_registration_confirm_dialog(self, preview):
        """
        実績・NG登録の内容をまとめて確認するモーダルダイアログを表示する。
        既存レコードの有無・「実績＋NG数量」と計画数の不一致があれば、登録内容と
        一緒に1画面でまとめて表示する。ダイアログ内でEnterキー＝「登録」
        （デフォルトボタンにフォーカスを置く）、Esc＝「キャンセル」。

        戻り値：「登録」が選ばれた場合True、「キャンセル」またはウインドウを
        閉じた場合False。
        """
        plan = self.current_plan
        lines = []
        if preview["existing_daily_qty"] is not None:
            lines.append(f"・既に実績が登録されています（{preview['existing_daily_qty']:.0f}）。上書きします。")
        lines.append(f"・本日の実績（{plan['kitting_list_no']}）：{preview['daily_qty']:g}")
        for side in ("1", "2"):
            if side in preview["save_qty_by_side"]:
                side_plan = self._ng_side_plans[side]
                lines.append(
                    f"・面{side}（{side_plan['kitting_list_no']}）のNG数量："
                    f"{preview['save_qty_by_side'][side]:g}"
                )

        if preview["mismatch_lines"]:
            lines.append("")
            lines.append("以下の面で「実績＋NG数量」が計画数と一致していません：")
            lines.extend(f"　{line}" for line in preview["mismatch_lines"])

        # selfが最小化状態だと、transient(self)したダイアログがstate()="withdrawn"
        # のまま実際には表示されない（ui.plan_candidate_dialog._show_candidate_list_dialog()
        # と同じ理由・同じ対策、UI_WORKFLOW_FIXES_NOTES.md参照）。
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
        dialog.wait_window()
        return result["confirmed"]

    def _perform_registration(self, daily_qty, preview):
        """
        登録確認ダイアログで「登録」が選ばれた後、実績→NGの順に逐次登録する。
        途中でエラーが発生しても、既に成功した分はそのまま残し（完全ロールバックは
        しない、services.production_import_service.import_production_csv()の
        「1行の異常が他行に影響しない」設計と同じ考え方）、エラー内容を明示する。

        実績側は、_build_registration_preview()で既に既存レコードの有無を確認・
        ユーザーの承認も確認ダイアログで得ている（この時点で既にexisting_daily_
        qtyが分かっている）ため、register_daily_result(check_duplicate=True)の
        例外ハンドリングは経由せず、existing_daily_qtyの有無で直接
        register_daily_result()/overwrite_daily_result()を呼び分ける。

        report_date：実績CSVステージング一覧経由の登録（self._pending_csv_
        report_date）であれば、CSV行の払い出し日を_resolve_csv_report_date()で
        検証した上でreport_dateとして渡す（パース不能・未設定ならNoneのまま＝
        register_daily_result()/overwrite_daily_result()側のデフォルト動作である
        実行日にフォールバックする）。CSV経由でない通常の手動登録では
        self._pending_csv_report_dateは常にNoneのため、従来通り実行日になる。

        登録完了後も同じ計画のまま画面が続く（次の計画を選び直すとは限らない）ため、
        最後に_load_current_daily_qty()・_setup_ng_side_ui()を再度呼び、実績記入欄・
        NG面1/面2欄を今回登録した最新の値でプリフィルし直す（呼ばないと、次に
        画面を開き直すまで登録前の入力内容が表示され続けてしまう）。
        """
        worker_id = self.current_worker.get("worker_id", "SYSTEM")
        kitting_no = self.current_plan["kitting_list_no"]
        lot_no = self.current_plan["lot_no"]
        report_date = _resolve_csv_report_date(self._pending_csv_report_date)
        self._pending_csv_report_date = None

        try:
            if preview["existing_daily_qty"] is not None:
                new_cumulative = overwrite_daily_result(kitting_no, lot_no, daily_qty, worker_id, report_date=report_date)
            else:
                new_cumulative = register_daily_result(kitting_no, lot_no, daily_qty, worker_id, report_date=report_date)
        except Exception as e:
            messagebox.showerror("登録エラー", f"実績の登録に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        # 実績CSVステージング一覧（ui.production_import_staging_window）経由の
        # 登録であれば、実績登録が成功した時点でその行を一覧から消す
        # （_on_csv_staging_row_confirmed()で転記時にセットされたコールバック）。
        # NG申告・反対側連動の成否には関係なく、主たる実績登録が成功した
        # 時点で消す（CSV行が表すのは実績数量そのものであり、NG申告は別枠のため）。
        if self._pending_csv_row_removal is not None:
            self._pending_csv_row_removal()
            self._pending_csv_row_removal = None

        errors = []
        opposite_registered = False
        try:
            opposite_registered = self._register_opposite_side_daily_result(daily_qty, worker_id)
        except Exception as e:
            errors.append(f"反対側の面への実績連動登録に失敗しました：{e}")

        report_date = datetime.now().strftime("%Y-%m-%d")
        declared_faces = []
        for side in ("1", "2"):
            if side not in preview["save_qty_by_side"]:
                continue
            plan = self._ng_side_plans[side]
            try:
                save_ng_declaration(
                    plan["kitting_list_no"], plan["setup_file_no"], int(side),
                    preview["save_qty_by_side"][side], report_date,
                    lot_no=plan["lot_no"], is_unplanned=False,
                )
                declared_faces.append(side)
            except Exception as e:
                errors.append(f"面{side}のNG登録に失敗しました：{e}")

        self.lbl_app_cum.config(text=f"{new_cumulative:.0f}")
        self.load_today_log()
        # 登録直後も同じ計画のまま画面が続くため、実績記入欄・NG面1/面2欄を
        # 最新の保存値で再プリフィルする（計画を選び直さない限り自動更新されない
        # ため、ここで明示的に呼ぶ）。
        self._load_current_daily_qty(kitting_no, lot_no)
        self._setup_ng_side_ui(self.current_plan)
        # 計画一覧（tree_plan_list）のうち、今回の登録（実績本体＋面連動＋NG）で
        # 値が変わり得る同一lot_no内の行だけを、全件再取得せずに部分更新する。
        # 全てのDB書き込み（実績・反対側連動・NG申告）が完了した後に呼ぶ。
        self._refresh_plan_list_for_lot(lot_no)

        msg_lines = [f"実績を登録しました。アプリ入力累計：{new_cumulative:.0f}"]
        if opposite_registered:
            msg_lines.append("反対側の面にも同じ数量を連動登録しました。")
        for side in declared_faces:
            msg_lines.append(f"面{side}：NG数量{preview['save_qty_by_side'][side]:g}を申告しました")
        if errors:
            msg_lines.append("")
            msg_lines.append("以下の項目でエラーが発生しました（登録できた分はそのまま残っています）：")
            msg_lines.extend(errors)

        messagebox.showinfo("登録完了", "\n".join(msg_lines), parent=self.winfo_toplevel())
        self.entry_daily_qty.focus_set()

    def _register_opposite_side_daily_result(self, daily_qty, worker_id):
        """
        選択中の計画（self.current_plan）の反対側の面への連動登録。
        実体は services.production_service.register_opposite_side_daily_result()
        （CSV自動取込 services.production_import_service.import_production_csv()
        とも共通で使われる）に委譲する薄いラッパー。self.current_plan は
        register_opposite_side_daily_result() が要求する計画dict形状
        （kitting_list_no・lot_no・setup_file_no・production_side・
        plan_start_datetime）をそのまま満たしているため、変換不要でそのまま渡せる。

        戻り値：反対側への登録を実際に行った場合True、反対側が存在しない場合False。
        """
        return register_opposite_side_daily_result(self.current_plan, daily_qty, worker_id)

    def _setup_ng_side_ui(self, plan):
        """
        計画選択時（search_plan()成功時）に、面1・面2のNG入力欄を更新する。

        選択中の計画自身の面（plan["production_side"]）をそのproduction_side用
        スロットに、find_opposite_side_plan()で見つかった反対側の計画をもう片方の
        スロットに割り当てる。反対側が見つからない（0件＝片面のみの計画）場合、
        そのスロットはNoneのままとなり、対応する入力欄は無効化・空欄になる。

        いずれかの面に、現在のNG申告（models.ng_declarations、report_dateを問わない
        「1計画・面＝1レコード」の現在値）があれば、その数量をNG入力欄へ自動表示
        する（_load_current_daily_qty()と同じ「登録済みの数量を表示する」パターン。
        以前はreport_date=当日限定だったため、過去日付のまま当日中に未更新の
        申告を拾えない不具合があったが、get_ng_declaration()の全期間検索化に
        伴い解消した）。

        面2：保存されているNG申告値をそのまま表示する（面2の保存値＝面2欄の入力値
        のみで、他面からの影響を受けないため）。

        面1：_perform_registration()が毎回「面1欄＋面2欄」を合算して面1へ保存する
        仕様のため、面1の保存値を（区別せず）そのまま表示すると、次に画面を開いて
        何も変えず再登録した際に「保存されている合算値」＋「面2欄の値」でさらに
        加算されてしまう（二重加算）。これを避けるため、面1欄には「面1固有分」＝
        面1の保存値 − 面2の保存値（0未満は表示しない＝空欄）を表示する
        （面2の保存値は常に「面2欄のみ」なので、この引き算で面1固有分を
        正しく復元できる）。
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
            # ttk.Entryはstate=DISABLEDのままinsert/deleteしても無視される（テキストが
            # 残ったままになる）ため、内容を変更する際は必ずNORMALに戻してから行う。
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


class ActualCorrectionWindow(tk.Toplevel):
    """
    完了済み計画も含め、production_daily の実績を修正・削除するためのウィンドウ。
    """
    def __init__(self, parent, kitting_list_no, lot_no, on_updated=None):
        super().__init__(parent)
        self.kitting_list_no = kitting_list_no
        self.lot_no = lot_no
        self.on_updated = on_updated

        self.title(f"実績修正（{kitting_list_no}）")
        self.geometry("500x420")

        hist_frame = ttk.LabelFrame(self, text="実績履歴", padding=10)
        hist_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=(15, 5))

        cols = ("report_date", "daily_qty", "worker_id")
        self.tree = ttk.Treeview(hist_frame, columns=cols, show="headings")
        self.tree.heading("report_date", text="日付")
        self.tree.heading("daily_qty", text="当日実績")
        self.tree.heading("worker_id", text="作業者")
        self.tree.column("report_date", width=150)
        self.tree.column("daily_qty", width=100, anchor=tk.E)
        self.tree.column("worker_id", width=150)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_history)

        edit_frame = ttk.LabelFrame(self, text="選択した実績の修正", padding=10)
        edit_frame.pack(fill=tk.X, padx=15, pady=(5, 15))

        ttk.Label(edit_frame, text="実績数：").pack(side=tk.LEFT, padx=5)
        self.entry_edit_qty = ttk.Entry(edit_frame, width=10)
        self.entry_edit_qty.pack(side=tk.LEFT, padx=5)

        self.btn_update = ttk.Button(edit_frame, text="修正", command=self.on_update,
                                      state=tk.DISABLED)
        self.btn_update.pack(side=tk.LEFT, padx=5)

        self.btn_delete = ttk.Button(edit_frame, text="削除", command=self.on_delete,
                                      state=tk.DISABLED)
        self.btn_delete.pack(side=tk.LEFT, padx=5)

        self.load_history()

    def load_history(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in get_daily_history(self.kitting_list_no, self.lot_no):
            self.tree.insert("", tk.END, iid=str(rec["prod_log_id"]), values=(
                rec["report_date"], f"{rec['daily_qty']:.0f}", rec["worker_id"]
            ))
        self.entry_edit_qty.delete(0, tk.END)
        self.btn_update.config(state=tk.DISABLED)
        self.btn_delete.config(state=tk.DISABLED)

    def on_select_history(self, event):
        sel = self.tree.selection()
        if not sel:
            self.btn_update.config(state=tk.DISABLED)
            self.btn_delete.config(state=tk.DISABLED)
            return
        values = self.tree.item(sel[0], "values")
        self.entry_edit_qty.delete(0, tk.END)
        self.entry_edit_qty.insert(0, values[1])
        self.btn_update.config(state=tk.NORMAL)
        self.btn_delete.config(state=tk.NORMAL)

    def on_update(self):
        sel = self.tree.selection()
        if not sel:
            return
        prod_log_id = int(sel[0])

        try:
            daily_qty = float(self.entry_edit_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "実績数には数値を入力してください。", parent=self.winfo_toplevel())
            return

        update_daily_result(prod_log_id, daily_qty)
        self._after_change()
        messagebox.showinfo("修正完了", "実績を修正しました。", parent=self.winfo_toplevel())

    def on_delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        prod_log_id = int(sel[0])

        if not messagebox.askyesno("確認", "選択した実績を削除します。よろしいですか？", parent=self.winfo_toplevel()):
            return

        delete_daily_result(prod_log_id)
        self._after_change()
        messagebox.showinfo("削除完了", "実績を削除しました。", parent=self.winfo_toplevel())

    def _after_change(self):
        calculate_lot_completion(self.lot_no)
        self.load_history()
        if self.on_updated:
            self.on_updated()