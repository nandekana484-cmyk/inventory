# ui/production_import_staging_window.py
"""
実績CSV取込のステージング一覧（「確認・選択・転記」方式）。

左右2ペイン構成（ui.ng_input_window.NgInputWindow・ui.wip_expansion_window.
WipExpansionWindowと同じ配置規則：左＝操作・詳細、右＝一覧）：
  - 右ペイン：models.production_import_staging.pending_csv_import_rows
    （DBへは未登録のCSV行一覧、_load_staged_rows_from_db()参照）を表示する。
    行を選択（<<TreeviewSelect>>）すると、その行の候補を左ペインへ表示する。
  - 左ペイン：models.kitting_plan.find_matching_plan_items()で再照合した候補
    一覧を表示する（ソート・ハイライトはui.plan_candidate_dialogの既存ロジック
    をそのまま再利用、後述）。候補をダブルクリックすると、親（parent、通常は
    ui.kitting_production_entry.KittingProductionEntryWindowのインスタンス）の
    search_plan()・entry_daily_qty・_pending_csv_row_removal・
    _pending_csv_report_dateへ直接転記・登録準備を行う。

以前は候補選択にui.plan_candidate_dialog.select_plan_candidate_by_lot()
（モーダルダイアログ、on_row_confirmedコールバック経由でparentに処理を
委譲）を使っていたが、本ウインドウが独立したTkinterのToplevelのまま
parentへの参照（self._parent、__init__()のparent引数）を既に保持していた
ため、モーダルダイアログを経由せず「親の同じメソッド・属性を直接呼ぶ」形に
書き換えることができた（parent.search_plan()等はウインドウの前面・
フォーカス状態に依存しないため、この直接呼び出しに支障は無い）。
select_plan_candidate_by_lot()自体・ui.plan_candidate_dialogモジュール自体は
変更していない（同モジュールの_show_candidate_list_dialog()・
select_plan_candidate()はui.ng_input_window.NgInputWindowが引き続き使用する
ため）。ソート・ハイライトロジック（_sort_candidates_by_closeness()・
_is_large_qty_diff()等）は、ロジックを重複させないためui.plan_candidate_dialog
から直接importして再利用する（下線始まりの非公開名だが、意図的な再利用）。

登録完了の検知：以前は呼び出し元が渡すremove_callbackコールバック経由
だったが、候補確定処理自体が本ウインドウ内で完結するようになったため、
_confirm_candidate()内で定義したremove_callbackをparent._pending_csv_
row_removalへ直接セットし、parent._perform_registration()の登録成功時に
それが直接呼ばれる形になった（本ウインドウ側からDBの状態を能動的に
ポーリングしない、という設計自体は変わらない）。

ステージングデータの永続化（models.production_import_staging）：CSVの生の
行データ（lot_no・product_name・daily_qty・report_date・worker_id）をDB
（pending_csv_import_rows）へ永続化する。候補（candidates/matched）自体は
DBに保存せず、_load_staged_rows_from_db()・_populate_candidates()が表示の
たびにmodels.kitting_plan.find_matching_plan_items()で再照合する（計画の
変更に追随できるよう、常に最新のDB状態を反映するため）。登録・除外が確定した
行はdelete_pending_csv_import_row()で即座に物理削除し、履歴としては残さない。
"""
import csv

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from services.production_import_service import (
    STAGING_STATUS_LABELS,
    normalize_product_name,
    group_active_plan_items_by_lot,
)
from models.kitting_plan import find_matching_plan_items
from models.production_import_staging import (
    list_pending_csv_import_rows,
    delete_pending_csv_import_row,
)
# ui.plan_candidate_dialog（モーダルダイアログ用の実装）から、候補の並べ替え・
# ハイライト判定・表示フォーマットのロジックのみをそのまま再利用する。
# ui.plan_candidate_dialog自体はui.ng_input_window.NgInputWindowが引き続き
# 使うモーダルダイアログ（select_plan_candidate()・_show_candidate_list_
# dialog()）のため変更しない。下線始まりの非公開名だが、ロジックを重複させて
# 二重管理になることを避けるため、あえて直接importする。
from ui.plan_candidate_dialog import (
    _sort_candidates_by_closeness,
    _is_large_qty_diff,
    _is_large_date_diff,
    _compute_date_diff_days,
    _format_qty,
    _format_planned_qty_cell,
    _format_side,
    _COLS as _CANDIDATE_COLS,
    _HEADERS as _CANDIDATE_HEADERS,
    _RIGHT_ALIGNED as _CANDIDATE_RIGHT_ALIGNED,
)

# "候補なし"（find_matching_plan_items()の候補が0件）行をCSV出力する際の理由欄。
# services.production_import_service.import_production_csv()のunmatched理由
# 文言と揃えている。
REASON_NO_CANDIDATES = "計画が見つからない（該当lot_noの計画なし）"

# 「不一致として除外」時、除外理由の入力を空欄のまま・キャンセルした場合の
# デフォルト文言。REASON_NO_CANDIDATES（機械判定による固定理由1種類のみ）とは
# 異なり、不一致は人間が個別に判断するため理由は行ごとに様々であり得る
# （例：ロットNoの入力ミス、対象外の製品、二重入力等）。そのため理由入力欄は
# 任意入力（自由記述）とし、何も入力されなかった場合のみこのデフォルト文言を
# 使う方針とした。
REASON_MISMATCH_DEFAULT = "不一致と判断（詳細理由未入力）"


def is_auto_confirmable(lot_no, product_name, planned_qty, daily_qty, plan_start_datetime, report_date):
    """
    調査（自動確定可能な実績の要件検証）で仮実装した判定を正式に実装したもの。

    要件：
      - 生産予定数（planned_qty）とCSVの実績数（daily_qty）が完全一致
      - 生産予定日（plan_start_datetime）とCSVの払い出し日（report_date）の
        差が24時間（1日）以内

    lot_no・product_nameは本関数自体の判定には使わない（呼び出し元
    （_populate_candidates()）が既にfind_matching_plan_items()でlot_no・
    製品名の一致を確認した上で得た個々の候補について、追加の数量・日付条件を
    判定する用途のため）。is_already_registered(lot_no, product_name,
    daily_qty)と引数の並びを揃え、関連する判定関数群として一貫させるために
    残している。

    配置場所について：調査時の指示は「services/production_import_service.py
    （または適切な場所）」だったが、日数差の計算にui.plan_candidate_dialog.
    _compute_date_diff_days()（_sort_candidates_by_closeness()内のパース
    ロジックをそのまま流用したもの）を使う必要があり、services層がui層の
    モジュールに依存する形になってしまう（このコードベースの他の箇所は
    すべてui→services→modelsの一方向依存）。呼び出し元がui.production_
    import_staging_window（本ファイル）のみであることも踏まえ、あえて
    services/production_import_service.pyには置かず、本ファイルに配置した。

    以前の制約（修正済み）：調査時点の実データ（pending_csv_import_rows
    95件）では、report_dateの実運用値が"YYYY/M/D"形式（例："2026/3/18"、
    月日がゼロ埋めされていない）だったが、_compute_date_diff_days()が
    内部で使っていた_parse_report_date()が"%Y-%m-%d"のみにしか対応しておらず、
    実データでは本関数が常にFalse（日付比較不能）を返していた。
    ui.plan_candidate_dialog._parse_flexible_date()（旧_parse_report_date()を
    複数形式対応に拡張・改称したもの）により、"%Y-%m-%d"・"%Y/%m/%d"
    （ゼロ埋めの有無を問わない）の両方に対応済みのため、現在は実データでも
    正しく判定できる。日付・数量いずれも比較不能な場合はFalse（安全側）に
    倒す設計はis_already_registered()と同じ考え方のまま変えていない。
    """
    try:
        if float(planned_qty) != float(daily_qty):
            return False
    except (TypeError, ValueError):
        return False

    date_diff = _compute_date_diff_days(report_date, plan_start_datetime)
    if date_diff is None or date_diff > 1:
        return False

    return True


def _load_staged_rows_from_db():
    """
    models.production_import_staging.list_pending_csv_import_rows()で未処理行を
    DBから取得し、各行についてmodels.kitting_plan.find_matching_plan_items()で
    候補（candidates/matched）・状態（status）を再計算した上で、従来と同じ形の
    辞書リスト（{"pending_row_id", "row", "lot_no", "product_name", "daily_qty",
    "report_date", "worker_id", "candidates", "matched", "status"}）を組み立てる。

    候補をDBに保存せず必ず再照合する理由はmodels.production_import_stagingの
    docstring参照。plan_items_by_lot（services.production_import_service.
    group_active_plan_items_by_lot()）は本関数内で1回だけ取得し、未処理行数分の
    find_matching_plan_items()呼び出しで使い回す（CSV取込時と同じN+1回避）。
    """
    pending_rows = list_pending_csv_import_rows()
    if not pending_rows:
        return []

    plan_items_by_lot = group_active_plan_items_by_lot()

    staged_rows = []
    for row in pending_rows:
        product_name_normalized = normalize_product_name(row["product_name"])
        candidates, matched = find_matching_plan_items(
            row["lot_no"], product_name_normalized, plan_items_by_lot,
        )

        if not candidates:
            status = "no_candidates"
        else:
            unique_kitting_nos = {c["kitting_list_no"] for c in matched}
            status = "auto_resolvable" if len(unique_kitting_nos) == 1 else "needs_selection"

        staged_rows.append({
            "pending_row_id": row["pending_row_id"],
            "row": row.get("csv_row_no"),
            "lot_no": row["lot_no"],
            "product_name": row["product_name"],
            "daily_qty": row["daily_qty"],
            "report_date": row["report_date"],
            "worker_id": row["worker_id"],
            "candidates": candidates,
            "matched": matched,
            "status": status,
        })
    return staged_rows


def open_or_notify(parent):
    """
    未処理の保留行（pending_csv_import_rows）が1件でもあればProductionImport
    StagingWindowを開き、無ければ案内メッセージのみ表示する（新規CSV取込直後
    ・メインメニューの「実績CSV取込状況」からの再開、両方の入口から使う
    共通のエントリーポイント）。

    parent：ui.kitting_production_entry.KittingProductionEntryWindowの
    インスタンスを想定（search_plan()・entry_daily_qty・_pending_csv_row_
    removal・_pending_csv_report_dateを直接呼び出す/参照するため）。

    戻り値：開いたProductionImportStagingWindow、または未処理行が無く
    開かなかった場合はNone。
    """
    staged_rows = _load_staged_rows_from_db()
    if not staged_rows:
        messagebox.showinfo("実績CSV取込状況", "未処理の取込データはありません。", parent=parent)
        return None
    return ProductionImportStagingWindow(parent, staged_rows)


class ProductionImportStagingWindow(tk.Toplevel):
    # 右ペイン（登録待ち一覧）の選択（<<TreeviewSelect>>、矢印キーでも発火）の
    # たびに毎回find_matching_plan_items()（DBアクセスを伴う）を実行しないよう
    # デバウンスする。ui.kitting_production_entry.KittingProductionEntryWindow.
    # PLAN_SELECT_DEBOUNCE_MSと同じ値・同じ考え方。
    CANDIDATE_SELECT_DEBOUNCE_MS = 200

    def __init__(self, parent, staged_rows):
        """
        parent：ui.kitting_production_entry.KittingProductionEntryWindowの
        インスタンス。左ペインの候補をダブルクリックした際、parent.
        search_plan()・parent.entry_daily_qty・parent._pending_csv_row_removal・
        parent._pending_csv_report_dateへ直接アクセスする（_confirm_candidate()
        参照）。

        staged_rows：_load_staged_rows_from_db()が組み立てる辞書のリスト
        （各要素は"pending_row_id"/"row"/"lot_no"/"product_name"/"daily_qty"/
        "report_date"/"worker_id"/"candidates"/"matched"/"status"を持つ）。
        本クラス自体はopen_or_notify()経由で呼ばれる想定で、直接staged_rowsを
        組み立てて渡すのは主にテスト用途。
        """
        super().__init__(parent)
        self._parent = parent
        self.title("実績CSV取込：登録待ち一覧")
        self.geometry("1150x520")

        self._row_by_iid = {}
        # "no_candidates"（候補なし＝登録不可）と判定された行を、一覧から消えても
        # 参照できるよう別途保持しておく（CSV出力ボタン用）。一覧本体
        # （self._row_by_iid）は登録完了のたびに行が消えていくが、こちらは
        # ウインドウを閉じるまで消さない。
        self._unregistrable_rows = [
            row for row in staged_rows if row.get("status") == "no_candidates"
        ]
        # 「不一致として除外」（右クリックメニュー）で個別に人間が判断した行を
        # 保持する別リスト。self._unregistrable_rows（machine判定の"no_candidates"、
        # CSV出力時にまとめて削除）とは異なり、こちらは除外を選んだ時点で即座に
        # pending_csv_import_rowsから削除する（_mark_as_mismatched()参照）。
        self._mismatched_rows = []

        # 左ペイン（候補一覧）の状態：右ペインで現在選択中の保留行（iid・row）と、
        # 候補Treeviewのiidからcandidateへのマッピング。
        self._current_staging_iid = None
        self._current_staging_row = None
        self._candidates_by_iid = {}
        self._candidate_select_debounce_id = None
        self._pending_candidate_select_iid = None

        self._create_widgets(staged_rows)
        self._update_status_label()
        self._clear_candidate_pane()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _create_widgets(self, staged_rows):
        """
        左右2ペイン構成（container→left_frame（候補一覧）/right_frame
        （登録待ち一覧））。ui.ng_input_window.NgInputWindow・ui.wip_expansion_
        window.WipExpansionWindowと同じ配置規則（左：操作・詳細、右：一覧）に
        倣う：右ペインの登録待ち一覧が主たる一覧（選択の起点）、左ペインは
        その選択に反応して候補を表示する詳細ペイン、という位置付けのため。
        """
        container = ttk.Frame(self, padding=10)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Labelframe(container, text="候補一覧（右の登録待ち一覧から行を選択）", padding=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_frame = ttk.Labelframe(container, text="実績CSV：登録待ち一覧", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self._create_candidate_widgets(left_frame)
        self._create_staging_widgets(right_frame, staged_rows)

        btn_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="閉じる", command=self._on_close).pack(side=tk.RIGHT)

    def _create_candidate_widgets(self, left_frame):
        """
        左ペイン（候補一覧）。列構成・書式（_format_side()・_format_qty()）・
        列幅の考え方は、以前ui.plan_candidate_dialog.select_plan_candidate_
        by_lot()がモーダルダイアログとして表示していたものと同一
        （_CANDIDATE_COLS・_CANDIDATE_HEADERS・_CANDIDATE_RIGHT_ALIGNEDを
        そのままimportして使う）。
        """
        self.lbl_candidate_hint = ttk.Label(
            left_frame, foreground="gray", wraplength=420, justify=tk.LEFT,
        )
        self.lbl_candidate_hint.pack(anchor=tk.W, pady=(0, 5))

        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.tree_candidates = ttk.Treeview(
            tree_frame, columns=_CANDIDATE_COLS, show="headings", selectmode="browse",
        )
        for col in _CANDIDATE_COLS:
            self.tree_candidates.heading(col, text=_CANDIDATE_HEADERS[col])
            # 計画数（planned_qty）は_format_planned_qty_cell()で差分表記
            # （例："450（差+50）"）が付くことがあるため、他列より幅を広めに取る
            # （ui.plan_candidate_dialog._show_candidate_list_dialog()と同じ幅）。
            width = 160 if col == "planned_qty" else 100
            self.tree_candidates.column(
                col, width=width, anchor=tk.E if col in _CANDIDATE_RIGHT_ALIGNED else tk.W,
            )
        # 数量差・日付差が大きい候補の背景色を変える
        # （ui.plan_candidate_dialog._is_large_qty_diff()・_is_large_date_diff()
        # と同じ閾値・同じ配色。両方に該当する場合は"large_diff_both"を単独で
        # 割り当てる。3タグ併用によるTkinterの優先順位の曖昧さを避けるため。
        # 詳細はui.plan_candidate_dialog._show_candidate_list_dialog()の
        # docstring参照）。
        #
        # "auto_confirmable"（is_auto_confirmable()、緑系）は、上記の
        # large_diff系（黄・オレンジ・赤）とは数学的に排他（同時に該当し得ない）：
        # auto_confirmableは「数量差=0（完全一致）かつ日付差<=1日」を要求するが、
        # large_diffは「数量差が実績数の20%超」、large_date_diffは「日付差が
        # 3日以上」を要求するため、閾値の範囲が重ならない
        # （0は20%超になり得ず、1日以下は3日以上になり得ない）。そのため
        # 実際には両方に該当するケースは発生しないが、念のためauto_confirmable
        # を最優先で判定し、単独タグとして割り当てる（3タグ併用はしない）。
        self.tree_candidates.tag_configure("large_diff", background="#fff3cd")
        self.tree_candidates.tag_configure("large_date_diff", background="#ffd9a0")
        self.tree_candidates.tag_configure("large_diff_both", background="#ffb3b3")
        self.tree_candidates.tag_configure("auto_confirmable", background="#c8f7c5")
        self.tree_candidates.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree_candidates.yview)
        self.tree_candidates.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_candidates.bind("<Double-1>", self._on_candidate_double_click)
        self.tree_candidates.bind("<Button-3>", self._on_candidate_right_click)

        ttk.Label(
            left_frame,
            text=(
                "候補をダブルクリックすると、生産実績入力画面（親ウインドウ）に転記されます。\n"
                "右クリックすると、確認ダイアログ無しで即座に登録します。"
            ),
            foreground="gray",
        ).pack(anchor=tk.W, pady=(5, 0))

    def _create_staging_widgets(self, right_frame, staged_rows):
        """右ペイン（登録待ち一覧）。以前の単一ペイン版のTreeview・件数表示・CSV出力ボタンをそのまま移設。"""
        tree_frame = ttk.Frame(right_frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("lot_no", "product_name", "report_date", "worker_id", "daily_qty", "status")
        # selectmode="extended"：Ctrl+クリック・Shift+クリックでの複数選択に対応する
        # （ttk.Treeviewのデフォルトも"extended"だが、複数選択対応であることを
        # 明示するため明記する）。一括登録（Shift+S）・一括不一致マーク
        # （複数選択時の右クリック）で使う。
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("lot_no", text="ロットNo")
        self.tree.heading("product_name", text="製品名")
        self.tree.heading("report_date", text="払い出し日（参考）")
        self.tree.heading("worker_id", text="作業者")
        self.tree.heading("daily_qty", text="実績数")
        self.tree.heading("status", text="状態")
        self.tree.column("lot_no", width=100, anchor=tk.W)
        self.tree.column("product_name", width=180, anchor=tk.W)
        self.tree.column("report_date", width=120, anchor=tk.CENTER)
        self.tree.column("worker_id", width=90, anchor=tk.W)
        self.tree.column("daily_qty", width=70, anchor=tk.E)
        self.tree.column("status", width=160, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        for row in staged_rows:
            iid = self.tree.insert("", tk.END, values=(
                row.get("lot_no", ""),
                row.get("product_name", ""),
                row.get("report_date") or "",
                row.get("worker_id", ""),
                row.get("daily_qty", ""),
                STAGING_STATUS_LABELS.get(row.get("status"), row.get("status", "")),
            ))
            self._row_by_iid[iid] = row

        self.tree.bind("<<TreeviewSelect>>", self._on_staging_select)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Shift-S>", self._on_bulk_register)

        ttk.Label(
            right_frame,
            text=(
                "行を選択すると左ペインに候補が表示されます（Ctrl+クリック・Shift+クリックで複数選択可）。\n"
                "右クリックで「不一致として除外」できます（複数選択中は選択中の全行が対象）。\n"
                "複数選択してShift+Sを押すと、要件を満たす行のみ一括で即時登録します。"
            ),
            foreground="gray",
        ).pack(anchor=tk.W, pady=(5, 0))

        # 登録不可（machine判定）・不一致（人間の判断で除外）、それぞれの件数を
        # 分かりやすく常時表示する。件数が変わるたび_update_status_label()で
        # 更新する。
        self.lbl_status = ttk.Label(right_frame, foreground="gray")
        self.lbl_status.pack(anchor=tk.W, pady=(2, 5))

        action_frame = ttk.Frame(right_frame)
        action_frame.pack(fill=tk.X)
        ttk.Button(action_frame, text="登録不可リストをCSV出力", command=self.on_export_unregistrable_csv).pack(
            side=tk.LEFT, padx=(0, 5),
        )
        ttk.Button(action_frame, text="不一致リストをCSV出力", command=self.on_export_mismatched_csv).pack(
            side=tk.LEFT,
        )

    def _update_status_label(self):
        """登録不可・不一致、それぞれの件数表示を最新化する。"""
        self.lbl_status.config(
            text=(
                f"登録不可：{len(self._unregistrable_rows)}件　"
                f"不一致として除外：{len(self._mismatched_rows)}件"
            )
        )

    # ------------------------------------------------------------------
    # 左ペイン（候補一覧）
    # ------------------------------------------------------------------

    def _on_staging_select(self, event=None):
        """
        右ペイン（登録待ち一覧）の選択イベント。DBアクセスを伴う候補再照合
        （_populate_candidates()）は矢印キー連打中に毎回実行しないよう
        デバウンスする（ui.kitting_production_entry.KittingProductionEntryWindow.
        on_select_plan_list()と同じパターン）。
        """
        sel = self.tree.selection()
        if not sel:
            self._clear_candidate_pane()
            return

        self._pending_candidate_select_iid = sel[0]
        if self._candidate_select_debounce_id is not None:
            self.after_cancel(self._candidate_select_debounce_id)
        self._candidate_select_debounce_id = self.after(
            self.CANDIDATE_SELECT_DEBOUNCE_MS, self._on_staging_select_debounced,
        )

    def _on_staging_select_debounced(self):
        self._candidate_select_debounce_id = None
        iid = self._pending_candidate_select_iid
        row = self._row_by_iid.get(iid)
        if row is None:
            self._clear_candidate_pane()
            return

        self._current_staging_iid = iid
        self._current_staging_row = row
        self._populate_candidates(row)

    def _populate_candidates(self, row):
        """
        右ペインで選択された保留行(row)の候補を左ペインへ表示する。以前
        ui.plan_candidate_dialog.select_plan_candidate_by_lot()が担っていた
        判定・整形ロジックをそのまま踏襲する：
          - matched（製品名まで一致した候補）を優先表示し、0件の場合のみ
            candidates（lot_no一致の全件）へフォールバックする。
          - _sort_candidates_by_closeness()で、日数差・数量差の複合スコアで
            並べ替える。
          - _is_large_qty_diff()で数量差の大きい候補、_is_large_date_diff()で
            払い出し日と生産予定日の差が大きい候補をハイライトする（一覧からは
            除外しない）。両方に該当する場合の色の組み合わせ方はui.plan_
            candidate_dialog._show_candidate_list_dialog()のdocstring参照。
          - is_auto_confirmable()で、計画数と実績数が完全一致し、かつ生産予定日
            と払い出し日の差が24時間以内の候補を緑系でハイライトする
            （"auto_confirmable"、_create_candidate_widgets()のtag_configure
            コメント参照：数量差・日付差ハイライトとは数学的に排他のため優先順位の
            衝突は実質発生しないが、念のため最優先で判定する）。
          - _format_planned_qty_cell()で、計画数（planned_qty）セルに実績数との
            差分を併記する（例："450（差+50）"）。行全体のlarge_diffタグ
            （20%超のみ発火）とは独立に、差があれば常に表示する、より細かい
            粒度の指標。Tkinterの標準Treeviewはセル単位の背景色指定に対応して
            いないため、色ではなくテキストへの差分埋め込みで実現している
            （詳細はui.plan_candidate_dialog._format_planned_qty_cell()参照）。
        """
        for item in self.tree_candidates.get_children():
            self.tree_candidates.delete(item)
        self._candidates_by_iid = {}

        candidates = row.get("candidates") or []
        matched = row.get("matched") or []
        daily_qty = row.get("daily_qty")
        report_date = row.get("report_date")

        using_fallback = not matched
        effective_candidates = candidates if using_fallback else matched
        effective_candidates = _sort_candidates_by_closeness(effective_candidates, report_date, daily_qty)

        if not effective_candidates:
            hint = f"ロットNo. {row.get('lot_no')} に該当する計画が見つかりません。"
        else:
            hint = (
                f"ロットNo. {row.get('lot_no')}（製品名: {row.get('product_name')}）"
                f"の候補：{len(effective_candidates)}件"
            )
            if daily_qty is not None:
                hint += f"　当日実績数：{_format_qty(daily_qty)}（計画数と見比べてご確認ください）"
            if using_fallback:
                hint += "\n※ 製品名が完全一致する候補が無いため、ロットNo.が一致する全ての候補を表示しています。"
        self.lbl_candidate_hint.config(text=hint)

        for candidate in effective_candidates:
            auto_flag = is_auto_confirmable(
                row.get("lot_no"), row.get("product_name"),
                candidate.get("planned_qty"), daily_qty,
                candidate.get("plan_start_datetime"), report_date,
            )
            qty_flag = _is_large_qty_diff(candidate, daily_qty)
            date_flag = _is_large_date_diff(report_date, candidate.get("plan_start_datetime"))
            if auto_flag:
                tags = ("auto_confirmable",)
            elif qty_flag and date_flag:
                tags = ("large_diff_both",)
            elif qty_flag:
                tags = ("large_diff",)
            elif date_flag:
                tags = ("large_date_diff",)
            else:
                tags = ()
            iid = self.tree_candidates.insert("", tk.END, tags=tags, values=(
                candidate.get("lot_no") or "",
                candidate.get("board_name") or "",
                candidate.get("setup_file_no") or "",
                _format_side(candidate.get("production_side")),
                candidate.get("plan_start_datetime") or "",
                _format_planned_qty_cell(candidate.get("planned_qty"), daily_qty),
                _format_qty(candidate.get("order_qty")),
            ))
            self._candidates_by_iid[iid] = candidate

    def _clear_candidate_pane(self):
        """
        左ペインを空にする（右ペインで選択が無い・選択中の行が削除された
        場合）。
        """
        for item in self.tree_candidates.get_children():
            self.tree_candidates.delete(item)
        self._candidates_by_iid = {}
        self._current_staging_iid = None
        self._current_staging_row = None
        self.lbl_candidate_hint.config(text="右の登録待ち一覧から行を選択してください。")

    def _on_candidate_double_click(self, event):
        iid = self.tree_candidates.identify_row(event.y)
        if not iid:
            return
        candidate = self._candidates_by_iid.get(iid)
        if candidate is None:
            return
        self._confirm_candidate(candidate)

    def _make_remove_callback(self, staging_iid):
        """
        _confirm_candidate()（ダブルクリック）・_register_candidate_immediately()
        （右クリック）の両方で使うremove_callbackを組み立てる（登録成功時に
        parent._perform_registration()から呼ばれ、右ペイン・DBから該当行を
        消す）。staging_iidをクロージャで捕まえておくことで、確定操作の時点で
        選択されていた行を、その後別の行が選択されても正しく指し続ける。
        """
        def remove_callback():
            if staging_iid in self._row_by_iid:
                pending_row_id = self._row_by_iid[staging_iid].get("pending_row_id")
                self.tree.delete(staging_iid)
                del self._row_by_iid[staging_iid]
                # 登録済みになった行は履歴として残さず、pending_csv_import_rowsから
                # 即座に物理削除する（models.production_import_stagingの方針）。
                if pending_row_id is not None:
                    delete_pending_csv_import_row(pending_row_id)
                # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする
                # （存在しなくなった行の候補を表示し続けないため）。
                if self._current_staging_iid == staging_iid:
                    self._clear_candidate_pane()

        return remove_callback

    def _confirm_candidate(self, candidate):
        """
        左ペインの候補をダブルクリックで確定した際の処理。以前ui.kitting_
        production_entry.KittingProductionEntryWindow._on_csv_staging_row_
        confirmed()が担っていた処理のうち、候補選択（select_plan_candidate_
        by_lot()モーダルダイアログの呼び出しと戻り値の受け取り）より後の部分
        （転記・登録準備）を、本ウインドウのメソッドとして移植したもの。
        select_plan_candidate_by_lot()自体はこの経路では呼ばない（左ペイン
        での選択が既にその代替のため）。

        self._parentはui.kitting_production_entry.KittingProductionEntry
        Windowのインスタンスであり、そのsearch_plan()・entry_daily_qty・
        _pending_csv_row_removal・_pending_csv_report_dateを直接呼び出す/
        書き換える。これらはウインドウの前面・フォーカス状態に依存しない
        （前回の調査で確認済み）ため、本ウインドウを閉じずに実行できる。
        parent側は最後にentry_daily_qty.focus_set()を呼ぶことで（Windows上の
        本Tk環境では既に確認済みの通り）実質的に前面へ上がってくるため、
        ユーザーはそのまま生産実績入力画面で入力を続けられる。

        従来通り、実際の登録（_start_registration()→_perform_registration()）は
        呼ばない。あくまで転記のみで、ユーザーがNG面1/面2を確認した上で
        「登録」ボタンまたはEnterで確定する一直線フローに乗せる
        （即時登録したい場合は右クリック→_register_candidate_immediately()を
        使う）。
        """
        row = self._current_staging_row
        staging_iid = self._current_staging_iid
        if row is None or staging_iid is None:
            return

        self._parent.search_plan(candidate["kitting_list_no"], lot_no=candidate["lot_no"])
        self._parent.entry_daily_qty.delete(0, tk.END)
        self._parent.entry_daily_qty.insert(0, f"{row['daily_qty']:g}")
        self._parent._pending_csv_row_removal = self._make_remove_callback(staging_iid)
        self._parent._pending_csv_report_date = row.get("report_date")
        self._parent.entry_daily_qty.focus_set()

    def _on_candidate_right_click(self, event):
        """
        右クリックされた候補について、確認ダイアログを経由せず即座に登録する
        （右クリック＝即時登録、ダブルクリック＝従来の一直線フローへの転記、
        という使い分け）。
        """
        iid = self.tree_candidates.identify_row(event.y)
        if not iid:
            return
        self.tree_candidates.selection_set(iid)
        candidate = self._candidates_by_iid.get(iid)
        if candidate is None:
            return
        row = self._current_staging_row
        staging_iid = self._current_staging_iid
        if row is None or staging_iid is None:
            return
        self._register_candidate_immediately(row, staging_iid, candidate)

    def _register_candidate_immediately(self, row, staging_iid, candidate):
        """
        右クリックでの即時登録。_confirm_candidate()と同じ転記処理を行った上で、
        続けてparent._perform_registration()を直接呼び、確認ダイアログ
        （parent._show_registration_confirm_dialog()）を経由せず即座に登録を
        完了させる。

        row・staging_iidを引数として明示的に受け取る（self._current_staging_
        row/iidを暗黙に参照しない）理由：_on_bulk_register()（Shift+S一括登録）
        から、右ペインで複数選択された各行に対して順番に呼び出すため。左ペイン
        の「現在表示中の候補」の選択状態（self._current_staging_row/iid）と、
        一括登録でこれから処理する行は必ずしも一致しない（一括登録は右ペインの
        複数選択に基づき、左ペインの表示は特定の1行のみを指すため）。

        確認ダイアログの回避方法について（検討結果）：parent._perform_
        registration(daily_qty, preview) 自体には確認ダイアログは含まれて
        いない。確認は、その手前の一直線フローの入口であるparent._start_
        registration()が、NG入力欄の検証（_validate_ng_inputs()）→preview
        の組み立て（_build_registration_preview()）→確認ダイアログ
        （_show_registration_confirm_dialog()）→承認されたら_perform_
        registration()、という順で行っている。したがって_perform_
        registration()自体には一切手を加えず、_start_registration()を
        経由しない（_build_registration_preview()を直接呼んでpreviewを
        組み立て、_perform_registration()へ直接渡す）ことで、確認ダイアログを
        自然にスキップできる。

        NG面1・面2は対象にしない（save_qty_by_side={}で登録する）。
        is_auto_confirmable()の自動確定要件はNG数量を考慮しない実績数量・
        日付のみの判定であり、即時登録もその範囲（実績数量の登録のみ）に
        留めるのが安全なため（NG入力欄に他の計画から持ち越された値が
        誤って使われるリスクも避けられる。search_plan()が呼ぶ_setup_ng_
        side_ui()により、NG入力欄自体は新しい計画の状態に更新されるが、
        その値を今回のNG申告として使うかどうかは別問題であり、ここでは
        意図的に使わない）。

        面連動（反対側の面への自動登録）：_perform_registration()内部の
        _register_opposite_side_daily_result()呼び出しがそのまま働くため、
        通常の登録フローと同じく自動的に行われる。
        """
        parent = self._parent
        daily_qty = row["daily_qty"]

        parent.search_plan(candidate["kitting_list_no"], lot_no=candidate["lot_no"])
        parent.entry_daily_qty.delete(0, tk.END)
        parent.entry_daily_qty.insert(0, f"{daily_qty:g}")
        parent._pending_csv_row_removal = self._make_remove_callback(staging_iid)
        parent._pending_csv_report_date = row.get("report_date")

        preview = parent._build_registration_preview(daily_qty, {})
        parent._perform_registration(daily_qty, preview)

    def _on_bulk_register(self, event=None):
        """
        右ペイン（登録待ち一覧）で複数選択された行のうち、要件を満たすもの
        （lot_no・製品名でmatchedが1件に定まり、かつis_auto_confirmable()の
        要件を満たすもの）のみを一括で即時登録する（Shift+Sショートカット）。

        各選択行についてfind_matching_plan_items()で候補を都度再照合する
        （選択・表示時点でキャッシュされたrow["candidates"]/row["matched"]は
        古くなっている可能性があるため使わない。_load_staged_rows_from_db()の
        docstring参照）。group_active_plan_items_by_lot()は選択件数分の
        N+1を避けるためループの前に1回だけ取得する（CSV取込時と同じ考え方）。

        要件を満たさない行（候補0件・複数件・自動確定要件不一致）はスキップし、
        件数のみ記録して次の行へ進む（削除・除外等の副作用は一切与えない。
        ステージング一覧にそのまま残り続ける）。

        非同期化について（検討結果）：1件あたりの登録処理はDB書き込み数件
        程度で、選択件数が数十〜百件程度までは体感できる遅延にはならないと
        判断し、LoadingWindowによる非同期化は行わなかった。加えて、後述の
        通り登録の都度表示される確認不要の完了通知を抑制してはいるものの、
        _perform_registration()内のエラー時messagebox.showerror()（rare
        pathのため抑制しない）が発生した場合はその場でブロッキングダイアログ
        になるため、そもそも「バックグラウンドで静かに処理が進む」性質の
        操作ではなく、LoadingWindowの効果が薄いという判断もある。

        完了通知の抑制について：_perform_registration()は登録成功のたびに
        messagebox.showinfo("登録完了", ...)を表示するが、複数件の一括操作で
        逐一クリックさせるのは一括操作の趣旨に反するため、本メソッドの実行中
        のみ一時的にmessagebox.showinfo()を無効化し、最後に1回だけ件数
        サマリを表示する（try/finallyで必ず元に戻す）。エラーダイアログ
        （messagebox.showerror）は抑制しない（個別の失敗は稀であり、必ず
        ユーザーの目に入るようにするため）。

        登録成功の判定：_perform_registration()自体は成功/失敗を戻り値で
        知らせないため、呼び出し後にiidがself._row_by_iidから消えているか
        （remove_callbackが実行されたか）で判定する。
        """
        selected_iids = list(self.tree.selection())
        if not selected_iids:
            return

        plan_items_by_lot = group_active_plan_items_by_lot()

        registered_count = 0
        skipped_count = 0
        failed_count = 0

        original_showinfo = messagebox.showinfo
        messagebox.showinfo = lambda *a, **kw: None
        try:
            for iid in selected_iids:
                row = self._row_by_iid.get(iid)
                if row is None:
                    continue

                product_name_normalized = normalize_product_name(row["product_name"])
                _, matched = find_matching_plan_items(
                    row["lot_no"], product_name_normalized, plan_items_by_lot,
                )
                if len(matched) != 1:
                    skipped_count += 1
                    continue

                candidate = matched[0]
                if not is_auto_confirmable(
                    row.get("lot_no"), row.get("product_name"),
                    candidate.get("planned_qty"), row.get("daily_qty"),
                    candidate.get("plan_start_datetime"), row.get("report_date"),
                ):
                    skipped_count += 1
                    continue

                self._register_candidate_immediately(row, iid, candidate)
                if iid not in self._row_by_iid:
                    registered_count += 1
                else:
                    failed_count += 1
        finally:
            messagebox.showinfo = original_showinfo

        message = f"{registered_count}件を登録しました。\n{skipped_count}件は要件を満たさないためスキップしました。"
        if failed_count:
            message += f"\n{failed_count}件は登録中にエラーが発生しました（詳細は個別のエラーダイアログを参照）。"
        messagebox.showinfo("一括登録完了", message, parent=self)

    # ------------------------------------------------------------------
    # ウインドウを閉じる
    # ------------------------------------------------------------------

    def _on_close(self):
        """
        ウインドウを閉じる前に、失われると困るデータが残っていないか順番に
        確認する。

        1. 不一致リスト（self._mismatched_rows）：「不一致として除外」した
           際に入力した理由（自由記述）を含むデータで、対応する
           pending_csv_import_rowsの行は_apply_mismatch()の時点で既に
           DBから物理削除済みのため、**このウインドウのメモリ上にしか
           存在しない**。CSV出力せずに閉じると完全に失われる。そのため、
           1件以上残っている場合はブロッキングの確認ダイアログ（askyesno）
           で出力を促す（_confirm_and_export_mismatched_before_close()）。
           「いいえ」を選んだ場合はテキストの通りデータが失われる前提で
           閉じる操作を続行する。ファイル選択自体をキャンセルした場合は
           「出力を促したのに何も出力されないまま閉じる」事故になるため、
           閉じる操作自体を中止する（ダイアログを閉じずに残す）。

        2. 未登録行（self._row_by_iid、pending_csv_import_rows）：以前は
           これも確認ダイアログだったが、ステージングデータの永続化により
           閉じても失われなくなった（メインメニューの「実績CSV取込状況」
           からいつでも再開できる）ため、確認ゲート自体は不要と判断済み
           （既存の非ブロッキングな案内のみ、_show_closing_notice()参照）。
           不一致リストの確認（ブロッキング、ウインドウを閉じる**前**に
           完結させる必要がある）→ウインドウを閉じる→未登録行の案内
           （非ブロッキング、閉じた**後**に表示）という順序になる。

        登録不可リスト（self._unregistrable_rows）については、同様の確認は
        追加しない：不一致リストと異なり、対応するpending_csv_import_rowsの
        行はCSV出力するまでDBに残ったまま（削除されるのはon_export_
        unregistrable_csv()の実行時のみ）であり、ウインドウを閉じても実データ
        は失われず、再度開けば同じ内容（"no_candidates"の行）が再構築される
        （reasonも人間の自由記述ではなく固定文言REASON_NO_CANDIDATESのため、
        再現不能な情報が失われる心配も無い）。
        """
        if self._mismatched_rows:
            if not self._confirm_and_export_mismatched_before_close():
                return

        remaining = len(self._row_by_iid)
        parent = self._parent
        self.destroy()
        if remaining:
            self._show_closing_notice(parent, remaining)

    def _confirm_and_export_mismatched_before_close(self):
        """
        不一致リストが1件以上残っている状態で閉じようとした際に呼ばれる。

        「はい」：on_export_mismatched_csv()をその場で実行する。実際に
        CSVへ出力できた場合（戻り値True）のみ、閉じる操作を続行してよいと
        判断する（True）。ファイル選択をキャンセルした・書き込みエラーに
        なった場合（戻り値False）は、出力を促したのに何も出力されないまま
        閉じてしまうことになるため、閉じる操作自体を中止する（False）。

        「いいえ」：データが失われることを理解した上での選択として扱い、
        出力せずに閉じてよい（True）。

        戻り値：True＝このままウインドウを閉じてよい、False＝閉じる操作を
        中止する（ダイアログを閉じずに残す）。
        """
        count = len(self._mismatched_rows)
        if not messagebox.askyesno(
            "不一致リスト未出力",
            f"不一致リストが未出力です（{count}件）。CSV出力しますか？\n"
            "「いいえ」を選ぶと、このデータは失われます。",
            parent=self,
        ):
            return True

        return self.on_export_mismatched_csv()

    @staticmethod
    def _show_closing_notice(parent, remaining_count):
        """
        親ウインドウ（呼び出し元のui.kitting_production_entry.
        KittingProductionEntryWindow）上に、自動的に消える非ブロッキングの
        案内を表示する。messagebox（「OK」クリックを要求するモーダル）は
        「閉じる操作自体を妨げない」という要件に合わないため使わず、
        枠のみのToplevel + after()による自動destroy()で実装した
        （実装コストが低く、閉じる処理をブロックしない方法として採用）。

        親ウインドウが既に閉じられている等、表示できない場合は静かに諦める
        （案内が出せないこと自体はエラー扱いしない。データはDBに永続化
        済みのため、通知が出せなくても実害は無い）。
        """
        try:
            if parent is None or not parent.winfo_exists():
                return
            notice = tk.Toplevel(parent)
            notice.overrideredirect(True)
            notice.attributes("-topmost", True)
            ttk.Label(
                notice,
                text=(
                    f"{remaining_count}件が未処理のまま残っています。\n"
                    "メインメニューの「実績CSV取込状況」からいつでも再開できます。"
                ),
                padding=10, background="#fff3cd", relief=tk.SOLID, borderwidth=1,
            ).pack()
            parent.update_idletasks()
            x = parent.winfo_rootx() + 40
            y = parent.winfo_rooty() + 40
            notice.geometry(f"+{x}+{y}")
            notice.after(3000, notice.destroy)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # 登録不可リスト（machine判定："no_candidates"）
    # ------------------------------------------------------------------

    def on_export_unregistrable_csv(self):
        """
        登録不可（"no_candidates"＝該当lot_noの計画が見つからなかった）行を
        CSV出力する。該当行が1件も無い場合は出力せずメッセージのみ表示する。

        出力成功後、該当行を一覧（self.tree・self._row_by_iid）・DB
        （pending_csv_import_rows、delete_pending_csv_import_row()）からも
        削除する（self._unregistrable_rows自体は保持したまま：ウインドウを
        閉じるまで参照可能にしておく従来の方針は変えない）。DBから削除しないと、
        CSVに出力済みでも「未処理」のまま残り続け、次回ステージング一覧を
        開いた際に再び表示されてしまうため。
        """
        if not self._unregistrable_rows:
            messagebox.showinfo("登録不可リスト", "登録不可の行はありません。", parent=self)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_unregistrable.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._unregistrable_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        REASON_NO_CANDIDATES,
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return

        iids_to_remove = [
            iid for iid, row in self._row_by_iid.items()
            if row.get("status") == "no_candidates"
        ]
        for iid in iids_to_remove:
            pending_row_id = self._row_by_iid[iid].get("pending_row_id")
            self.tree.delete(iid)
            del self._row_by_iid[iid]
            # 除外済み（CSV出力で対応済みとした）行は履歴として残さず、
            # pending_csv_import_rowsから即座に物理削除する
            # （models.production_import_stagingの方針。__init__()参照）。
            if pending_row_id is not None:
                delete_pending_csv_import_row(pending_row_id)
            # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする。
            if self._current_staging_iid == iid:
                self._clear_candidate_pane()

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)

    # ------------------------------------------------------------------
    # 不一致として除外（人間の個別判断）
    # ------------------------------------------------------------------

    def _on_right_click(self, event):
        """
        右クリックされた行に対して「不一致として除外」のコンテキストメニューを
        表示する。

        複数行選択時の一括対応：右クリックされた行が、現在複数選択されている
        行の一部であれば、選択されている全行が対象になる（一括不一致マーク、
        _mark_multiple_as_mismatched()）。それ以外（未選択の状態でクリック、
        または選択範囲外の行をクリック）の場合は、クリックされた行1件のみを
        選択し直した上で対象にする（多くのアプリの右クリックの慣習と同じ
        挙動、かつ既存の単一行動作をそのまま維持する）。
        """
        clicked_iid = self.tree.identify_row(event.y)
        if not clicked_iid:
            return

        current_selection = self.tree.selection()
        if len(current_selection) > 1 and clicked_iid in current_selection:
            target_iids = list(current_selection)
        else:
            self.tree.selection_set(clicked_iid)
            target_iids = [clicked_iid]

        menu = tk.Menu(self, tearoff=0)
        if len(target_iids) > 1:
            menu.add_command(
                label=f"不一致として除外（選択中の{len(target_iids)}件）",
                command=lambda: self._mark_multiple_as_mismatched(target_iids),
            )
        else:
            menu.add_command(
                label="不一致として除外",
                command=lambda: self._mark_as_mismatched(target_iids[0]),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _apply_mismatch(self, iid, reason):
        """
        1行を「不一致として除外」する共通処理（理由の入力は含まない）。
        一覧（self.tree・self._row_by_iid）・DB（pending_csv_import_rows）から
        即座に削除し、self._mismatched_rows（不一致リストCSV出力用、ウインドウを
        閉じるまで保持）へ追加する。self._unregistrable_rows（machine判定の
        "no_candidates"、CSV出力時にまとめて削除）とは別枠で管理する
        （__init__()のコメント参照。既存の登録不可リストと混同しないため）。
        _mark_as_mismatched()（単一行）・_mark_multiple_as_mismatched()
        （複数行一括）の両方から、理由決定後に呼ばれる。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        pending_row_id = row.get("pending_row_id")
        self.tree.delete(iid)
        del self._row_by_iid[iid]
        if pending_row_id is not None:
            delete_pending_csv_import_row(pending_row_id)
        # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする。
        if self._current_staging_iid == iid:
            self._clear_candidate_pane()

        self._mismatched_rows.append({**row, "mismatch_reason": reason})

    def _mark_as_mismatched(self, iid):
        """
        人間が「この行に一致する計画は無い／違う」と判断した場合の、単一行の
        除外操作。除外理由：人間の個別判断のため理由は様々であり得る
        （ロットNoの入力ミス・対象外の製品・二重入力等）。REASON_NO_CANDIDATES
        （機械判定による固定文言1種類）とは異なり、任意入力（自由記述）の
        ダイアログで尋ね、空欄・キャンセル時はREASON_MISMATCH_DEFAULTを使う。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        reason = simpledialog.askstring(
            "不一致として除外",
            f"ロットNo. {row.get('lot_no')} を不一致として除外します。\n"
            "除外理由（任意）：",
            parent=self,
        )
        reason = (reason or "").strip() or REASON_MISMATCH_DEFAULT

        self._apply_mismatch(iid, reason)
        self._update_status_label()

    def _mark_multiple_as_mismatched(self, iids):
        """
        複数選択された行を一括で「不一致として除外」する（右ペインを複数選択
        状態で右クリックした場合）。理由は全行共通で1回だけ尋ね
        （simpledialog.askstring()、_mark_as_mismatched()と同じ任意入力・
        デフォルトフォールバック方式）、全行に同じ理由を適用する。
        """
        valid_iids = [iid for iid in iids if iid in self._row_by_iid]
        if not valid_iids:
            return

        lot_no_preview = "、".join(self._row_by_iid[iid].get("lot_no", "") for iid in valid_iids[:5])
        if len(valid_iids) > 5:
            lot_no_preview += " 他"
        reason = simpledialog.askstring(
            "不一致として除外",
            f"選択中の{len(valid_iids)}件（ロットNo: {lot_no_preview}）を"
            "まとめて不一致として除外します。\n除外理由（任意、全件共通）：",
            parent=self,
        )
        reason = (reason or "").strip() or REASON_MISMATCH_DEFAULT

        for iid in valid_iids:
            self._apply_mismatch(iid, reason)
        self._update_status_label()

    def on_export_mismatched_csv(self):
        """
        「不一致として除外」された行（self._mismatched_rows）をCSV出力する。
        on_export_unregistrable_csv()と同じ形式（utf-8-sig、列構成：lot_no・
        product_name・daily_qty・report_date・reason）だが、reason列は行ごとに
        個別入力された除外理由をそのまま使う（REASON_NO_CANDIDATESのような
        固定文言1種類ではない）。

        既にpending_csv_import_rowsからの削除・一覧からの除去は
        _mark_as_mismatched()の時点で完了済みのため、ここではCSVへの書き出しの
        みを行う（on_export_unregistrable_csv()と異なり、削除処理は無い）。

        戻り値：実際にCSVへの書き出しが完了した場合True、それ以外
        （出力対象が無い・ファイル選択をキャンセルした・書き込みエラー）は
        False。ボタン押下（既存の呼び出し方）では戻り値は使われないが、
        _on_close()（ウインドウを閉じる前に未出力の不一致リストがあれば
        出力を促す、_confirm_and_export_mismatched_before_close()参照）が
        「実際に出力できたか」を判定するために利用する。
        """
        if not self._mismatched_rows:
            messagebox.showinfo("不一致リスト", "不一致として除外した行はありません。", parent=self)
            return True

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_mismatched.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return False

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._mismatched_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        row.get("mismatch_reason", REASON_MISMATCH_DEFAULT),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return False

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)
        return True
