"""
実績CSV取込のステージング一覧（「確認・選択・転記」方式）。右＝登録待ちの保留行、左＝選択した行の候補。
候補をダブルクリックすると、親（KittingProductionEntryWindow）の search_plan()・entry_daily_qty・
_pending_csv_row_removal・_pending_csv_report_date へ直接転記する。候補は DB に保存せず、表示のたびに再照合する。
設計と経緯は docs/domain/production_import_staging.md。
"""
import csv
import logging

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from services.production_import_service import (
    STAGING_STATUS_LABELS,
    EXCLUSION_REASON_LABELS,
    normalize_product_name,
    group_active_plan_items_by_lot,
)
from models.kitting_plan import (
    find_matching_plan_items, classify_side1_only_plan,
    SIDE1_ONLY_CLASS_SINGLE_SIDE, SIDE1_ONLY_CLASS_WAITING_SIDE2, SIDE1_ONLY_CLASS_UNREGISTERED,
    SIDE1_ONLY_CLASS_LABELS,
)
from models.production import list_daily_production_by_kitting_no
from models.production_import_staging import (
    list_pending_csv_import_rows,
    delete_pending_csv_import_row,
    upsert_pending_csv_import_row,
)
from models.lot_status_history import record_lot_status_snapshot

logger = logging.getLogger(__name__)
# ui.plan_candidate_dialog の並べ替え・ハイライト判定を、重複させないためにそのまま使う（_ 始まりだが意図的な再利用）。
from ui.plan_candidate_dialog import (
    _sort_candidates_by_closeness,
    _is_large_qty_diff,
    _is_large_date_diff,
    _parse_flexible_date,
    _parse_plan_start_datetime,
    _format_qty,
    _format_planned_qty_cell,
    _format_side,
    _COLS as _CANDIDATE_COLS,
    _HEADERS as _CANDIDATE_HEADERS,
    _RIGHT_ALIGNED as _CANDIDATE_RIGHT_ALIGNED,
)
from ui.window_utils import center_window
from ui.highlight_colors import MISMATCH_RED

# 「候補なし」行を CSV 出力するときの理由。import_production_csv() の unmatched の理由と同じ文言。
REASON_NO_CANDIDATES = "計画が見つからない（該当lot_noの計画なし）"

# 「不一致として除外」で理由が空欄・キャンセルのときの既定の文言（理由は人が判断するので自由記述）。
REASON_MISMATCH_DEFAULT = "不一致と判断（詳細理由未入力）"


_SIDE1_ONLY_CONFIRM_REASONS = {
    SIDE1_ONLY_CLASS_WAITING_SIDE2: "生産面マスターにより、この計画には後行面（面2）があることが分かっています（面2はまだ計画データに取り込まれていません）。",
    SIDE1_ONLY_CLASS_UNREGISTERED: "この計画のセットアップファイルNo・実装ラインの組み合わせは、生産面マスターに登録がありません（後行面があるかどうか不明です）。",
}


def _confirm_side1_only_if_applicable(parent, candidate):
    """
    候補が面1だけの計画で、生産面マスターの分類が「面2待ち」「未登録」なら、理由を示す確認ダイアログを出す（D-9x）。
    登録は禁止しない。それ以外は常に True。False のとき、呼び出し元は転記も登録もせずに戻ること（保留行は残る）。
    """
    classification = classify_side1_only_plan(candidate)
    if classification not in _SIDE1_ONLY_CONFIRM_REASONS:
        return True

    reason = _SIDE1_ONLY_CONFIRM_REASONS[classification]
    message = (
        f"{reason}\n\n"
        f"計画（{candidate['kitting_list_no']}）はこのまま登録できますが、"
        "本当にこの計画へ登録してよいか確認してください。続けますか？"
    )
    return messagebox.askyesno("面1のみの計画への登録", message, parent=parent.winfo_toplevel())


# Shift+S・Shift+Q で許容する、払い出し日と生産予定日の差（日付のみで比較）。
# 両ショートカットの違いは、この日数の違いだけにする（利用者の決定、D-101）。
AUTO_REGISTER_MAX_DAYS_SHIFT_S = 1
AUTO_REGISTER_MAX_DAYS_SHIFT_Q = 4

# evaluate_auto_register_eligibility()の戻り値status。
AUTO_REGISTER_ELIGIBLE = "eligible"
AUTO_REGISTER_INELIGIBLE = "ineligible"

# 登録できる行を、日付の差で「Shift+S で可」「Shift+Q でのみ可」の2段階に分ける（D-101）。
STATUS_ELIGIBLE_SHIFT_S = "eligible_shift_s"
STATUS_ELIGIBLE_SHIFT_Q = "eligible_shift_q"

# 登録できない理由のコード。STAGING_STATUS_LABELS（production_import_service）のキーにも使う。
REASON_NO_CANDIDATES_CODE = "no_candidates"
REASON_PRODUCT_NAME_MISMATCH = "product_name_mismatch"
REASON_MULTIPLE_CANDIDATES = "multiple_candidates"
REASON_EXISTING = "existing"
REASON_DUPLICATE = "duplicate"
REASON_SIDE2_WAIT = "side2_wait"
REASON_SIDE_MASTER_UNREGISTERED = "side_master_unregistered"
REASON_QTY_MISMATCH = "qty_mismatch"
REASON_DATE_UNPARSEABLE = "date_unparseable"
REASON_DATE_DIFF_TOO_LARGE = "date_diff_too_large"

# 一括登録の完了メッセージで、スキップ理由ごとの件数に付ける短いラベル（一覧の「状態」列とは別）。
AUTO_REGISTER_SKIP_REASON_LABELS = {
    REASON_NO_CANDIDATES_CODE: "候補なし",
    REASON_PRODUCT_NAME_MISMATCH: "製品名不一致",
    REASON_MULTIPLE_CANDIDATES: "候補複数（未選択）",
    REASON_EXISTING: "既に実績登録済み",
    REASON_DUPLICATE: "同一計画への重複候補",
    REASON_SIDE2_WAIT: "面2待ち",
    REASON_SIDE_MASTER_UNREGISTERED: "生産面マスター未登録",
    REASON_QTY_MISMATCH: "数量不一致",
    REASON_DATE_UNPARSEABLE: "日付を解釈できない",
    REASON_DATE_DIFF_TOO_LARGE: "日付の差が許容日数を超える",
}


def _date_only_diff_days(report_date, plan_start_datetime):
    """
    払い出し日と生産予定日の差の日数（絶対値）を、日付だけで比較する。どちらかが解釈できなければ None。
    時刻まで含めると暦日の差とずれる（実データの生産予定日には全件時刻がある。7/23 と 7/21 23:50 は2日だが、1日になってしまう）。
    """
    reference = _parse_flexible_date(report_date)
    if reference is None:
        return None
    dt = _parse_plan_start_datetime(plan_start_datetime)
    if dt is None:
        return None
    return abs((dt.date() - reference.date()).days)


def _is_qty_mismatch(planned_qty, daily_qty) -> bool:
    """計画数と CSV の実績数が一致しないか。比較できなければ不一致（安全側）とする。"""
    try:
        return float(planned_qty) != float(daily_qty)
    except (TypeError, ValueError):
        return True


def _is_candidate_already_registered(candidate, lot_no) -> bool:
    """
    候補の計画に、既に実績が1件以上あるか。「完了済み」とは別の概念（完了済みの計画は、候補の時点で除外されている）。
    """
    return bool(list_daily_production_by_kitting_no(candidate["kitting_list_no"], lot_no))


def is_auto_confirmable(lot_no, product_name, planned_qty, daily_qty, plan_start_datetime, report_date,
                         max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_S):
    """
    数量が完全一致し、払い出し日と生産予定日の差が max_date_diff_days 日以内（日付のみで比較）なら True。
    比較できない場合は False（安全側）。lot_no・product_name は判定に使わない（呼び出し元で一致を確認済み）。
    services/ ではなくここに置くのは、日付の解釈に ui.plan_candidate_dialog を使うため（services から ui への依存を避けた）。
    """
    try:
        if float(planned_qty) != float(daily_qty):
            return False
    except (TypeError, ValueError):
        return False

    date_diff = _date_only_diff_days(report_date, plan_start_datetime)
    if date_diff is None or date_diff > max_date_diff_days:
        return False

    return True


def evaluate_auto_register_eligibility(row, candidates, matched, plan_key_counts, max_date_diff_days):
    """
    1件の保留行が自動登録（Shift+S／Shift+Q）できるかを判定する。一覧の状態表示・Shift+S・Shift+Q の共通の判定（D-101）。
    判定の優先順位:
      1. 候補なし  2. 製品名の不一致  3. 候補が複数  4. 既に実績あり  5. 同じ計画への重複
      6. 面2待ち／生産面マスター未登録  7. 数量の不一致  8. 日付を解釈できない  9. 日付の差が許容を超える
      10. 登録可（差が1日以内なら Shift+S、2日以上なら Shift+Q のみ）
    candidates・matched は find_matching_plan_items() の戻り値。一括登録では必ず再照合した値を渡すこと。
    plan_key_counts は {(kitting_list_no, lot_no): 単一候補に解決できる行の数}（ループの外で1回だけ計算する）。
    戻り値は (status, reason, candidate, date_diff)。各理由の意味は docs/domain/production_import_staging.md。
    """
    if not candidates:
        return AUTO_REGISTER_INELIGIBLE, REASON_NO_CANDIDATES_CODE, None, None
    if not matched:
        return AUTO_REGISTER_INELIGIBLE, REASON_PRODUCT_NAME_MISMATCH, None, None

    unique_kitting_nos = {c["kitting_list_no"] for c in matched}
    if len(unique_kitting_nos) > 1:
        return AUTO_REGISTER_INELIGIBLE, REASON_MULTIPLE_CANDIDATES, None, None

    candidate = matched[0]
    kitting_list_no = candidate["kitting_list_no"]
    lot_no = row["lot_no"]

    if list_daily_production_by_kitting_no(kitting_list_no, lot_no):
        return AUTO_REGISTER_INELIGIBLE, REASON_EXISTING, candidate, None

    if plan_key_counts.get((kitting_list_no, lot_no), 0) > 1:
        return AUTO_REGISTER_INELIGIBLE, REASON_DUPLICATE, candidate, None

    classification = classify_side1_only_plan(candidate)
    if classification == SIDE1_ONLY_CLASS_WAITING_SIDE2:
        return AUTO_REGISTER_INELIGIBLE, REASON_SIDE2_WAIT, candidate, None
    if classification == SIDE1_ONLY_CLASS_UNREGISTERED:
        return AUTO_REGISTER_INELIGIBLE, REASON_SIDE_MASTER_UNREGISTERED, candidate, None

    if _is_qty_mismatch(candidate.get("planned_qty"), row.get("daily_qty")):
        return AUTO_REGISTER_INELIGIBLE, REASON_QTY_MISMATCH, candidate, None

    date_diff = _date_only_diff_days(row.get("report_date"), candidate.get("plan_start_datetime"))
    if date_diff is None:
        return AUTO_REGISTER_INELIGIBLE, REASON_DATE_UNPARSEABLE, candidate, None
    if date_diff > max_date_diff_days:
        return AUTO_REGISTER_INELIGIBLE, REASON_DATE_DIFF_TOO_LARGE, candidate, date_diff

    return AUTO_REGISTER_ELIGIBLE, None, candidate, date_diff


def _load_staged_rows_from_db():
    """
    未処理の保留行を DB から読み、候補を再照合して状態を判定した辞書のリストを作る。plan_items_by_lot は1回だけ取得して使い回す。
    状態は最も広い Shift+Q の許容日数（4日）で判定し、合格した行を日付の差で「Shift+S で可」「Shift+Q でのみ可」に分ける。
    "no_candidates" は登録不可リストの CSV 出力の判定に使うので、文字列を変えないこと。
    """
    pending_rows = list_pending_csv_import_rows()
    if not pending_rows:
        return []

    plan_items_by_lot = group_active_plan_items_by_lot()

    resolved = []
    for row in pending_rows:
        product_name_normalized = normalize_product_name(row["product_name"])
        candidates, matched = find_matching_plan_items(
            row["lot_no"], product_name_normalized, plan_items_by_lot,
        )
        resolved.append({
            "pending_row_id": row["pending_row_id"],
            "row": row.get("csv_row_no"),
            "lot_no": row["lot_no"],
            "product_name": row["product_name"],
            "daily_qty": row["daily_qty"],
            "report_date": row["report_date"],
            "worker_id": row["worker_id"],
            "candidates": candidates,
            "matched": matched,
        })

    # 単一候補に解決できる行について、同じ (kitting_list_no, lot_no) を候補とする行数を数える（D-6 の一意キーはこのペア）。
    plan_key_counts = {}
    for item in resolved:
        if len(item["matched"]) == 1:
            key = (item["matched"][0]["kitting_list_no"], item["lot_no"])
            plan_key_counts[key] = plan_key_counts.get(key, 0) + 1

    staged_rows = []
    for item in resolved:
        eligibility, reason, _candidate, date_diff = evaluate_auto_register_eligibility(
            item, item["candidates"], item["matched"], plan_key_counts,
            max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_Q,
        )
        if eligibility == AUTO_REGISTER_ELIGIBLE:
            item["status"] = (
                STATUS_ELIGIBLE_SHIFT_S if date_diff <= AUTO_REGISTER_MAX_DAYS_SHIFT_S
                else STATUS_ELIGIBLE_SHIFT_Q
            )
        else:
            item["status"] = reason
        staged_rows.append(item)

    return staged_rows


def open_or_notify(parent, already_registered_rows=None):
    """
    未処理の保留行か、直前の取込の「登録済み」行が1件でもあれば一覧を開き、どちらも無ければ案内だけ出す。
    全行が「登録済み」でも、想定どおりの重複か確認できるよう開く。開かなかったら None を返す。
    """
    staged_rows = _load_staged_rows_from_db()
    if not staged_rows and not already_registered_rows:
        messagebox.showinfo("実績CSV取込状況", "未処理の取込データはありません。", parent=parent)
        return None
    return ProductionImportStagingWindow(parent, staged_rows, already_registered_rows=already_registered_rows)


class ProductionImportStagingWindow(tk.Toplevel):
    # 選択のたびに DB を伴う候補の再照合をしないよう、デバウンスする（生産実績入力画面と同じ値）。
    CANDIDATE_SELECT_DEBOUNCE_MS = 200

    def __init__(self, parent, staged_rows, already_registered_rows=None):
        """
        parent: KittingProductionEntryWindow。候補を確定するとき、その search_plan() などに直接転記する。
        already_registered_rows: 直前の取込で「登録済み」と判定された行（DB に保存されない）。None なら空。
        """
        super().__init__(parent)
        self._parent = parent
        self.title("実績CSV取込：登録待ち一覧")
        self.geometry("1150x520")
        center_window(self, parent)

        self._row_by_iid = {}
        # 「候補なし」の行。一覧から消えても CSV 出力できるよう、ウインドウを閉じるまで保持する。
        self._unregistrable_rows = [
            row for row in staged_rows if row.get("status") == "no_candidates"
        ]
        # 「不一致として除外」で人が除外した行。除外した時点で pending_csv_import_rows から削除する（_mark_as_mismatched()）。
        self._mismatched_rows = []

        # 「登録済みリスト」。DB に保存されていない行なので、CSV 出力や削除をしても DB 側は何も変わらない。
        self._already_registered_rows = list(already_registered_rows or [])
        # 「登録済みリスト」の詳細ウインドウ。多重に開かない。
        self._already_registered_window = None
        self._already_registered_tree = None
        self._already_registered_by_iid = {}

        # 左ペイン（候補一覧）の状態：右ペインで現在選択中の保留行（iid・row）と、
        # 候補Treeviewのiidからcandidateへのマッピング。
        self._current_staging_iid = None
        self._current_staging_row = None
        self._candidates_by_iid = {}
        self._candidate_select_debounce_id = None
        self._pending_candidate_select_iid = None

        # 列ソートの状態（col -> 次に昇順にするか）。
        self._staging_sort_states = {}

        self._create_widgets(staged_rows)
        self._update_status_label()
        self._clear_candidate_pane()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 開いた直後にキーボードフォーカスを一覧に置く（置かないとどこにもフォーカスが無く、Shift+S が効かない）。
        self.tree.focus_set()

    def _create_widgets(self, staged_rows):
        """
        左右2ペイン。右の登録待ち一覧が選択の起点で、左はその選択に応じて候補を表示する（NG入力・仕掛展開画面と同じ配置）。
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
        左ペイン（候補一覧）。列構成と書式は ui.plan_candidate_dialog のものをそのまま使う。
        pack 順の罠: 下部のヒントを先に side=BOTTOM で pack し、Treeview とスクロールバーは最後に pack する。
        """
        self.lbl_candidate_hint = ttk.Label(
            left_frame, foreground="gray", wraplength=420, justify=tk.LEFT,
        )
        self.lbl_candidate_hint.pack(anchor=tk.W, pady=(0, 5))

        # --- 下部のヒントラベルを先にpackし、領域を確保する ---
        ttk.Label(
            left_frame,
            text=(
                "候補をダブルクリックすると、生産実績入力画面（親ウインドウ）に転記されます。\n"
                "右クリックすると、確認ダイアログ無しで即座に登録します。"
            ),
            foreground="gray",
        ).pack(side=tk.BOTTOM, anchor=tk.W, pady=(5, 0))

        # --- Treeview＋スクロールバーは、下部の領域確保後に最後にpackする ---
        tree_frame = ttk.Frame(left_frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.tree_candidates = ttk.Treeview(
            tree_frame, columns=_CANDIDATE_COLS, show="headings", selectmode="browse",
        )
        for col in _CANDIDATE_COLS:
            self.tree_candidates.heading(col, text=_CANDIDATE_HEADERS[col])
            # 計画数のセルには差分表記（例: "450（差-50）"）が付くので、幅を広めに取る。
            width = 160 if col == "planned_qty" else 100
            self.tree_candidates.column(
                col, width=width, anchor=tk.E if col in _CANDIDATE_RIGHT_ALIGNED else tk.W,
            )
        # 数量差・日付差が大きい候補の背景色（閾値と配色は ui.plan_candidate_dialog と同じ）。両方に該当すれば large_diff_both だけを付ける。
        # auto_confirmable（緑）は large_diff 系と閾値が重ならず同時には該当しないが、念のため最優先で判定する。
        self.tree_candidates.tag_configure("large_diff", background="#fff3cd")
        self.tree_candidates.tag_configure("large_date_diff", background="#ffd9a0")
        self.tree_candidates.tag_configure("large_diff_both", background=MISMATCH_RED)
        self.tree_candidates.tag_configure("auto_confirmable", background="#c8f7c5")
        # 生産面マスターの分類（D-9x）。既存のタグと別の色にし、_populate_candidates() で最優先に判定する。
        self.tree_candidates.tag_configure("needs_side2_wait", background="#cfe2ff")
        self.tree_candidates.tag_configure("side_master_unregistered", background="#e2e3e5")
        # 製品名不一致（ロットNoは一致するが、製品名が一致する候補が無い）。既存の赤（MISMATCH_RED）を使い、ほかのタグより優先する。
        self.tree_candidates.tag_configure("product_name_mismatch", background=MISMATCH_RED)
        # 登録済みの候補は文字色をグレーにする。文字色だけなので、背景色のタグと重ねて付けられる。
        self.tree_candidates.tag_configure("already_registered", foreground="gray")

        # スクロールバーをTreeviewより先にpackする（右ペインと同じ順序）。
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_candidates.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.tree_candidates.configure(yscrollcommand=vsb.set)
        vsb.configure(command=self.tree_candidates.yview)

        self.tree_candidates.bind("<Double-1>", self._on_candidate_double_click)
        self.tree_candidates.bind("<Button-3>", self._on_candidate_right_click)

    def _create_staging_widgets(self, right_frame, staged_rows):
        """
        右ペイン（登録待ち一覧）。
        pack 順の罠: pack は呼んだ順に領域を取る。Treeview を先に pack すると、狭いペインでスクロールバーが 1x1 に潰れる。
        下部の要素を先に pack し、Treeview は最後にする。side=BOTTOM は後のものほど上に積まれるので、見た目と逆順（ボタン→件数→ヒント）で pack する。
        """
        cols = ("lot_no", "product_name", "report_date", "worker_id", "daily_qty", "status")

        # --- 下部に配置するウィジェットを先に生成・packし、領域を確保する ---
        action_frame = ttk.Frame(right_frame)
        action_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(action_frame, text="登録不可リストをCSV出力", command=self.on_export_unregistrable_csv).pack(
            side=tk.LEFT, padx=(0, 5),
        )
        ttk.Button(action_frame, text="不一致リストをCSV出力", command=self.on_export_mismatched_csv).pack(
            side=tk.LEFT, padx=(0, 5),
        )
        ttk.Button(action_frame, text="登録済みリストを表示", command=self.on_show_already_registered_list).pack(
            side=tk.LEFT,
        )

        # 登録不可・不一致などの件数を常に表示する（_update_status_label() で更新）。
        self.lbl_status = ttk.Label(right_frame, foreground="gray")
        self.lbl_status.pack(side=tk.BOTTOM, anchor=tk.W, pady=(2, 5))

        ttk.Label(
            right_frame,
            text=(
                "行を選択すると左ペインに候補が表示されます（Ctrl+クリック・Shift+クリックで複数選択可）。\n"
                "右クリックで「不一致として除外」できます（複数選択中は選択中の全行が対象）。\n"
                "複数選択してShift+Sを押すと、日付の差が1日以内の行のみ一括で即時登録します。\n"
                "Shift+Qは日付の差が4日以内まで対象を広げます（登録前に件数を確認します）。"
            ),
            foreground="gray",
        ).pack(side=tk.BOTTOM, anchor=tk.W, pady=(5, 0))

        # --- Treeview＋スクロールバーは、下部の領域確保後に最後にpackする ---
        tree_frame = ttk.Frame(right_frame)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        # 複数選択（Ctrl/Shift+クリック）を使う。一括登録と、一括の不一致マークで必要。
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("lot_no", text="ロットNo", command=lambda c="lot_no": self.sort_staging_list(c))
        self.tree.heading("product_name", text="製品名", command=lambda c="product_name": self.sort_staging_list(c))
        self.tree.heading("report_date", text="払い出し日（参考）", command=lambda c="report_date": self.sort_staging_list(c))
        self.tree.heading("worker_id", text="作業者", command=lambda c="worker_id": self.sort_staging_list(c))
        self.tree.heading("daily_qty", text="実績数", command=lambda c="daily_qty": self.sort_staging_list(c))
        self.tree.heading("status", text="状態", command=lambda c="status": self.sort_staging_list(c))
        # stretch=False: 伸縮させると列が常に表示幅に収まり、横スクロールが効かなくなる。列幅を固定し、はみ出した分は横スクロールで見る。
        self.tree.column("lot_no", width=100, anchor=tk.W, stretch=False)
        self.tree.column("product_name", width=180, anchor=tk.W, stretch=False)
        self.tree.column("report_date", width=120, anchor=tk.CENTER, stretch=False)
        self.tree.column("worker_id", width=90, anchor=tk.W, stretch=False)
        self.tree.column("daily_qty", width=70, anchor=tk.E, stretch=False)
        self.tree.column("status", width=160, anchor=tk.W, stretch=False)

        # 製品名不一致の行を赤で示す（アプリ共通の「警告・不一致」の色 MISMATCH_RED。新しい色は作らない）。
        self.tree.tag_configure("product_name_mismatch", background=MISMATCH_RED)

        # 横スクロールバーも Treeview より先に pack する（下部の領域を先に取るため）。
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal")
        hsb.pack(side=tk.BOTTOM, fill=tk.X)

        # スクロールバーを Treeview より先に pack する（後にすると、スクロールバーの領域が残らない）。
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.configure(command=self.tree.yview)
        hsb.configure(command=self.tree.xview)

        for row in staged_rows:
            self._insert_staging_row(row)

        self.tree.bind("<<TreeviewSelect>>", self._on_staging_select)
        self.tree.bind("<Button-3>", self._on_right_click)
        # tree ではなくウインドウ全体に bind する。tree に bind すると、tree にフォーカスがあるときしか発火せず、Shift+S が効かないことがあった。
        # この画面にテキスト入力欄は無いので、文字入力を妨げる副作用は無い。tree には bind しない（二重に発火するため）。
        self.bind("<Shift-S>", self._on_bulk_register)
        # Shift+Q（D-101）: 対象は選択中の行だけで、日付の許容日数だけを緩める（全行を対象にした版は危険なため取り消した、D-100）。
        # CapsLock の状態で keysym が変わるので、<Shift-Q> と <Shift-q> の両方を bind する（D-97）。
        self.bind("<Shift-Q>", self._on_bulk_register_extended)
        self.bind("<Shift-q>", self._on_bulk_register_extended)

    def _insert_staging_row(self, row):
        """
        右ペインに1行挿入する共通処理（初期表示と、登録済みリストから戻す操作の両方で使う）。
        製品名不一致の行には赤のタグを付ける。ソートは move() だけなので、タグは保たれる。
        """
        status = row.get("status")
        tags = ("product_name_mismatch",) if status == REASON_PRODUCT_NAME_MISMATCH else ()
        iid = self.tree.insert("", tk.END, tags=tags, values=(
            row.get("lot_no", ""),
            row.get("product_name", ""),
            row.get("report_date") or "",
            row.get("worker_id", ""),
            row.get("daily_qty", ""),
            STAGING_STATUS_LABELS.get(row.get("status"), row.get("status", "")),
        ))
        self._row_by_iid[iid] = row
        return iid

    def _update_status_label(self):
        """表示中・登録不可・不一致・登録済みの件数表示を更新する。表示中の件数は tree から数える（この画面に絞り込みは無い）。"""
        self.lbl_status.config(
            text=(
                f"表示中：{len(self.tree.get_children())}件　"
                f"登録不可：{len(self._unregistrable_rows)}件　"
                f"不一致として除外：{len(self._mismatched_rows)}件　"
                f"登録済み：{len(self._already_registered_rows)}件"
            )
        )

    def sort_staging_list(self, col):
        """列ヘッダーのクリックで昇順/降順を切り替える（NG一覧・仕掛一覧と同じ方式）。"""
        numeric_cols = {"daily_qty"}

        def sort_key(value):
            if col in numeric_cols:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

        ascending = self._staging_sort_states.get(col, True)

        items = [
            (self.tree.set(iid, col), iid)
            for iid in self.tree.get_children("")
        ]
        items.sort(key=lambda t: sort_key(t[0]), reverse=not ascending)

        for index, (_, iid) in enumerate(items):
            self.tree.move(iid, "", index)

        self._staging_sort_states[col] = not ascending

    # ------------------------------------------------------------------
    # 左ペイン（候補一覧）
    # ------------------------------------------------------------------

    def _on_staging_select(self, event=None):
        """登録待ち一覧の選択。DB を伴う候補の再照合はデバウンスする。"""
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
        選択された保留行の候補を左ペインに表示する。製品名まで一致した候補（matched）を優先し、0件なら lot_no が一致する全件を出す。
        近い順（日数差・数量差）に並べ、差の大きい候補と自動確定できる候補に色を付ける。
        計画数のセルには実績数との差を書き添える（Treeview はセル単位の背景色に対応しないため）。
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
            side1_only_classification = classify_side1_only_plan(candidate)
            if using_fallback:
                # この分岐の候補はすべて製品名不一致（matched が空のフォールバック）。ほかのどの条件よりも優先する。
                tags = ("product_name_mismatch",)
            elif side1_only_classification == SIDE1_ONLY_CLASS_WAITING_SIDE2:
                tags = ("needs_side2_wait",)
            elif side1_only_classification == SIDE1_ONLY_CLASS_UNREGISTERED:
                tags = ("side_master_unregistered",)
            elif auto_flag:
                tags = ("auto_confirmable",)
            elif qty_flag and date_flag:
                tags = ("large_diff_both",)
            elif qty_flag:
                tags = ("large_diff",)
            elif date_flag:
                tags = ("large_date_diff",)
            else:
                tags = ()
            # 登録済みは文字色だけのタグなので、背景色の判定とは別に常に重ねる。
            if _is_candidate_already_registered(candidate, row.get("lot_no")):
                tags = tags + ("already_registered",)
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
        """左ペインを空にする（選択が無いとき、または選択中の行が削除されたとき）。"""
        for item in self.tree_candidates.get_children():
            self.tree_candidates.delete(item)
        self._candidates_by_iid = {}
        self._current_staging_iid = None
        self._current_staging_row = None
        self.lbl_candidate_hint.config(text="右の登録待ち一覧から行を選択してください。")

    def _on_candidate_double_click(self, event):
        """登録済みの候補なら、通常の転記ではなく実績修正画面を開く（ダブルクリックだけで開き、選択では開かない）。"""
        iid = self.tree_candidates.identify_row(event.y)
        if not iid:
            return
        candidate = self._candidates_by_iid.get(iid)
        if candidate is None:
            return
        row = self._current_staging_row
        if row is not None and _is_candidate_already_registered(candidate, row.get("lot_no")):
            self._open_correction_window_for_candidate(candidate, row.get("lot_no"))
            return
        self._confirm_candidate(candidate)

    def _open_correction_window_for_candidate(self, candidate, lot_no):
        """
        登録済みの候補から実績修正画面を開き、閉じるまで待ってから一覧を前面に戻す（D-98）。
        保留行には触れない（どうするかは利用者が判断する）。面の連動（D-112）は修正画面側で行う。
        """
        from ui.kitting_production_entry import ActualCorrectionWindow

        correction_win = ActualCorrectionWindow(
            self, kitting_list_no=candidate["kitting_list_no"], lot_no=lot_no,
            current_worker=getattr(self._parent, "current_worker", None),
        )
        self.wait_window(correction_win)
        # 修正内容を候補一覧の表示（登録済み判定・差分表記等）に反映する。
        row = self._current_staging_row
        if row is not None:
            self._populate_candidates(row)
        self._refocus_staging_window()

    def _make_remove_callback(self, staging_iid):
        """
        登録成功時に親の _perform_registration() から呼ばれ、一覧と DB から該当行を消すコールバックを作る。
        staging_iid はクロージャで捕まえるので、後で別の行が選ばれても正しい行を指す。
        消した後は次の行を選ぶ（無ければ前の行、どちらも無ければ選択なし）。消す前に次の行を控えること（消した後は next() できない）。
        一括登録では、バッチの最後の選択処理で上書きされる。
        """
        def remove_callback():
            if staging_iid in self._row_by_iid:
                pending_row_id = self._row_by_iid[staging_iid].get("pending_row_id")
                next_iid = self.tree.next(staging_iid) or self.tree.prev(staging_iid)
                self.tree.delete(staging_iid)
                del self._row_by_iid[staging_iid]
                # 登録済みの行は履歴に残さず、即座に物理削除する。
                if pending_row_id is not None:
                    delete_pending_csv_import_row(pending_row_id)
                # 削除した行の候補を表示し続けないよう、左ペインをクリアする。
                if self._current_staging_iid == staging_iid:
                    self._clear_candidate_pane()
                # 単一登録も一括登録もこのコールバックを通るので、件数表示の更新はここだけでよい。
                self._update_status_label()

                if next_iid and self.tree.exists(next_iid):
                    self.tree.selection_set(next_iid)
                    self.tree.see(next_iid)
                else:
                    self.tree.selection_set(())

        return remove_callback

    def _confirm_candidate(self, candidate):
        """
        左ペインの候補をダブルクリックで確定したとき、親の画面に転記する。登録はしない。
        利用者が NG を確かめたうえで、親の画面の「登録」または Enter で確定する（即時登録は右クリック）。
        親の search_plan() などはウインドウの前面やフォーカスに依存しないので、この画面を閉じずに呼べる。
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
        右クリックした候補を、確認ダイアログなしで即座に登録する（ダブルクリックは転記だけ）。
        次の候補では理由を表示して何もしない: 登録済みの候補（上書きを防ぐ。修正はダブルクリックから）、
        数量が違う候補（_is_qty_mismatch()。ダブルクリックからの通常の登録はできる）。
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

        if _is_candidate_already_registered(candidate, row.get("lot_no")):
            messagebox.showinfo(
                "即時登録できません",
                "この計画には既に実績が登録されているため、右クリックでの即時登録は行えません。\n"
                "ダブルクリックすると、実績修正画面を開けます。",
                parent=self.winfo_toplevel(),
            )
            return
        if _is_qty_mismatch(candidate.get("planned_qty"), row.get("daily_qty")):
            messagebox.showinfo(
                "即時登録できません",
                "計画数とCSVの実績数が一致しないため、右クリックでの即時登録は行えません。\n"
                "ダブルクリックすると、通常の登録確認画面から登録できます。",
                parent=self.winfo_toplevel(),
            )
            return

        self._register_candidate_immediately(row, staging_iid, candidate)

    def _register_candidate_immediately(self, row, staging_iid, candidate, record_history=True):
        """
        右クリックでの即時登録。転記したうえで、親の _perform_registration() を直接呼ぶ。
        確認ダイアログは _start_registration() 側にあるので、そこを通らずに preview を組み立てて渡すことで省く。
        row・staging_iid を引数で受け取るのは、一括登録が複数選択の各行について呼ぶため（左ペインに表示中の行とは限らない）。
        record_history=False は一括登録用（最後にロット単位でまとめて記録する）。
        NG は登録しない（save_qty_by_side={}。自動確定の要件は実績数量と日付だけで、NG 欄の値を誤って使わないため）。
        計画数と実績数が違うときは、完了メッセージの代わりに「数量を修正しますか？」と尋ねる。
        """
        parent = self._parent
        daily_qty = row["daily_qty"]
        kitting_list_no = candidate["kitting_list_no"]
        lot_no = candidate["lot_no"]
        planned_qty = candidate.get("planned_qty")

        # 既に実績があれば、黙って上書きせず確認する。一括登録では事前に除外済みだが、経路を問わず確認する。
        if not parent.confirm_overwrite_if_existing(kitting_list_no, lot_no, daily_qty, row.get("report_date")):
            return

        # 面1だけの計画で「面2待ち」「未登録」なら、理由を示して確認する（D-9x）。一括登録では事前に除外済みだが、経路を問わず確認する。
        if not _confirm_side1_only_if_applicable(parent, candidate):
            return

        parent.search_plan(kitting_list_no, lot_no=lot_no)
        parent.entry_daily_qty.delete(0, tk.END)
        parent.entry_daily_qty.insert(0, f"{daily_qty:g}")
        parent._pending_csv_row_removal = self._make_remove_callback(staging_iid)
        parent._pending_csv_report_date = row.get("report_date")

        preview = parent._build_registration_preview(daily_qty, {})

        # 数量差があれば完了メッセージを差し替えるため、showinfo() を一時的に無効にして内容だけを捕まえる（try/finally で必ず戻す）。
        captured = []
        original_showinfo = messagebox.showinfo
        messagebox.showinfo = lambda title, message, **kw: captured.append((title, message))
        try:
            parent._perform_registration(daily_qty, preview, record_history=record_history)
        finally:
            messagebox.showinfo = original_showinfo

        if captured:
            completion_title, completion_message = captured[0]
        else:
            # 実績の登録自体に失敗して showinfo() まで届かなかったときの既定値。
            completion_title, completion_message = "登録完了", f"実績を登録しました（実績数：{daily_qty:g}）。"

        try:
            mismatch = planned_qty is not None and float(daily_qty) != float(planned_qty)
        except (TypeError, ValueError):
            mismatch = False

        # ダイアログの parent は本ウインドウにする。生産実績入力画面にすると、閉じたときにそちらが前面に戻り、
        # 一覧への前面復帰とフォーカス（D-84）が上書きされる。
        fix_in_entry_window = False
        if not mismatch:
            messagebox.showinfo(completion_title, completion_message, parent=self.winfo_toplevel())
        else:
            diff = float(daily_qty) - float(planned_qty)
            message = (
                f"{completion_message}\n\n"
                f"実績数（{daily_qty:g}）が計画数（{planned_qty:g}、差{diff:+g}）と異なります。"
                "数量を修正しますか？"
            )
            if messagebox.askyesno(completion_title, message, parent=self.winfo_toplevel()):
                # 該当計画を開き直して記入欄に実績数を入れ、フォーカスを渡す。訂正して通常の Enter フローで再登録すると上書きされる。
                # この場合だけ、一覧への復帰をしない（fix_in_entry_window）。
                parent.search_plan(kitting_list_no, lot_no=lot_no)
                parent.entry_daily_qty.delete(0, tk.END)
                parent.entry_daily_qty.insert(0, f"{daily_qty:g}")
                parent.entry_daily_qty.focus_set()
                fix_in_entry_window = True

        # ダブルクリック・Shift+S と同じく、一覧を前面に戻してフォーカスも渡す（次の行の選択は remove_callback で済んでいる）。
        if not fix_in_entry_window:
            self._refocus_staging_window()

    def _refocus_staging_window(self):
        """
        本ウインドウを前面に戻し、キーボードフォーカスも一覧へ渡す。lift() だけではフォーカスが移らない（D-84）。
        最小化されていれば先に deiconify() する。
        """
        if self.state() == "iconic":
            self.deiconify()
        self.lift()
        self.focus_force()
        self.tree.focus_set()

    def _on_bulk_register(self, event=None):
        """Shift+S: 選択中の行のうち、許容日数1日で判定して合格した行だけを一括で即時登録する（D-101）。"""
        self._execute_bulk_register(
            max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_S,
            shortcut_label="Shift+S",
            require_confirm=False,
            trigger_source="bulk_register",
        )

    def _on_bulk_register_extended(self, event=None):
        """
        Shift+Q（D-101）: 対象と条件は Shift+S と同じで、日付の差の許容日数だけを4日に緩める。
        対象は選択中の行だけ（全行を対象にする版は取り消した、D-100）。実行前に件数を示す確認ダイアログを出す。
        """
        self._execute_bulk_register(
            max_date_diff_days=AUTO_REGISTER_MAX_DAYS_SHIFT_Q,
            shortcut_label="Shift+Q",
            require_confirm=True,
            trigger_source="bulk_register_shift_q",
        )

    def _execute_bulk_register(self, max_date_diff_days, shortcut_label, require_confirm, trigger_source):
        """
        Shift+S・Shift+Q の共通本体（D-101）。選択中の行のうち、判定に合格した行だけを一括で即時登録する。
        - 候補は行ごとに再照合する（表示時点のキャッシュは古い可能性がある）。plan_items_by_lot はループの前に1回だけ取得する
        - require_confirm=True（Shift+Q）は、登録の前に件数を示す確認ダイアログを出す。「いいえ」なら何も変えない
        - 合格しない行は理由ごとに数えるだけで、一覧に残す
        - 実行中は showinfo() を無効にし、最後に1回だけ件数を出す（try/finally で戻す）。showerror() は止めない
        - 登録の成否は、iid が _row_by_iid から消えたか（remove_callback が呼ばれたか）で判定する
        - 終わったら、処理した範囲の直後の行を選ぶ
        """
        selected_iids = list(self.tree.selection())
        if not selected_iids:
            return

        # 削除が始まる前に、処理範囲の直後の行（無ければ直前の行）を控えておく（削除が進むと next() で辿れない）。
        ordered_all = list(self.tree.get_children())
        selected_set = set(selected_iids)
        indices = [ordered_all.index(iid) for iid in selected_iids if iid in ordered_all]
        next_after_batch_iid = None
        if indices:
            last_index = max(indices)
            for cand in ordered_all[last_index + 1:]:
                if cand not in selected_set:
                    next_after_batch_iid = cand
                    break
            if next_after_batch_iid is None:
                first_index = min(indices)
                for cand in reversed(ordered_all[:first_index]):
                    if cand not in selected_set:
                        next_after_batch_iid = cand
                        break

        plan_items_by_lot = group_active_plan_items_by_lot()

        # 重複判定のため、一覧に残っている全行（選択の有無を問わない）を最新の状態で再照合して数える。
        plan_key_counts = {}
        for other_iid, other_row in self._row_by_iid.items():
            other_normalized = normalize_product_name(other_row["product_name"])
            _, other_matched = find_matching_plan_items(
                other_row["lot_no"], other_normalized, plan_items_by_lot,
            )
            if len(other_matched) == 1:
                plan_key = (other_matched[0]["kitting_list_no"], other_row["lot_no"])
                plan_key_counts[plan_key] = plan_key_counts.get(plan_key, 0) + 1

        # 行ごとに判定する（まだ登録しない。Shift+Q の確認ダイアログの件数はこの結果から作る）。
        evaluated = []
        for iid in selected_iids:
            row = self._row_by_iid.get(iid)
            if row is None:
                continue
            product_name_normalized = normalize_product_name(row["product_name"])
            candidates, matched = find_matching_plan_items(
                row["lot_no"], product_name_normalized, plan_items_by_lot,
            )
            eligibility, reason, candidate, date_diff = evaluate_auto_register_eligibility(
                row, candidates, matched, plan_key_counts, max_date_diff_days,
            )
            evaluated.append((iid, row, eligibility, reason, candidate, date_diff))

        eligible_items = [e for e in evaluated if e[2] == AUTO_REGISTER_ELIGIBLE]

        if require_confirm:
            # Shift+S では登録されない行＝日付の差が1日を超える行（ほかの条件は同じ）。
            shift_s_extra_count = sum(
                1 for e in eligible_items
                if e[5] is not None and e[5] > AUTO_REGISTER_MAX_DAYS_SHIFT_S
            )
            confirm_message = (
                f"選択した行数：{len(selected_iids)}件\n"
                f"登録される行数：{len(eligible_items)}件\n"
                f"　うち日付の差が2〜4日の行数（Shift+Sでは登録されない行）：{shift_s_extra_count}件\n\n"
                f"{shortcut_label}で一括登録します。よろしいですか？"
            )
            if not messagebox.askyesno(f"{shortcut_label}一括登録の確認", confirm_message, parent=self):
                return

        registered_count = 0
        failed_count = 0
        skip_reason_counts = {}
        # 登録できた行の lot_no（反対側の面も同じ lot_no なので追加は不要）。最後にまとめて履歴を記録する。
        affected_lot_nos = set()

        original_showinfo = messagebox.showinfo
        messagebox.showinfo = lambda *a, **kw: None
        try:
            for iid, row, eligibility, reason, candidate, _date_diff in evaluated:
                if eligibility != AUTO_REGISTER_ELIGIBLE:
                    skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
                    continue

                # 行ごとに履歴を記録すると100件規模で数秒かかるため、最後にロット単位でまとめて記録する。
                self._register_candidate_immediately(row, iid, candidate, record_history=False)
                if iid not in self._row_by_iid:
                    registered_count += 1
                    affected_lot_nos.add(candidate["lot_no"])
                else:
                    failed_count += 1
        finally:
            messagebox.showinfo = original_showinfo

        # 影響したロットごとに1回だけ履歴を記録する。1件の失敗がほかを止めないよう、ロットごとに try/except する。
        for lot_no in affected_lot_nos:
            try:
                record_lot_status_snapshot(lot_no, trigger_source)
            except Exception:
                logger.exception(
                    "lot_status_historyの記録に失敗しました（lot_no=%s, "
                    "trigger_source=%s）。一括登録処理自体はそのまま続行します。",
                    lot_no, trigger_source,
                )

        skipped_count = sum(skip_reason_counts.values())
        message = f"{registered_count}件を登録しました。\n{skipped_count}件は要件を満たさないためスキップしました。"
        skip_reason_lines = [
            f"　・{AUTO_REGISTER_SKIP_REASON_LABELS.get(reason, reason)}：{count}件"
            for reason, count in sorted(skip_reason_counts.items(), key=lambda kv: -kv[1])
        ]
        if skip_reason_lines:
            message += "\n" + "\n".join(skip_reason_lines)
        if failed_count:
            message += f"\n{failed_count}件は登録中にエラーが発生しました（詳細は個別のエラーダイアログを参照）。"
        messagebox.showinfo(f"{shortcut_label}一括登録完了", message, parent=self)

        # 控えておいた「処理範囲の直後の行」を選ぶ。無ければ選択を空にする。
        if next_after_batch_iid and self.tree.exists(next_after_batch_iid):
            self.tree.selection_set(next_after_batch_iid)
            self.tree.see(next_after_batch_iid)
        else:
            self.tree.selection_set(())

        # 一覧を前面に戻してフォーカスも渡す。戻さないと親の記入欄にフォーカスが残り、次の Shift+S が文字入力になる。
        self._refocus_staging_window()

    # ------------------------------------------------------------------
    # ウインドウを閉じる
    # ------------------------------------------------------------------

    def _on_close(self):
        """
        閉じる前に、失われるデータが無いか確認する。
        1. 不一致リスト: DB からは削除済みで、このウインドウのメモリにしか無い。残っていれば CSV 出力を促す。
           ファイル選択をキャンセルしたら閉じない（出力を促したのに何も残らないのを防ぐ）。
        2. 未登録の保留行: DB にあるので失われない。閉じた後に、非ブロッキングの案内を出すだけ。
        登録不可リストは DB に残っていて、再度開けば再構築されるので確認しない。
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
        不一致リストが残っているときに閉じようとしたら呼ぶ。「はい」なら CSV 出力し、実際に出力できたときだけ閉じてよい（True）。
        「いいえ」はデータが失われることを承知のうえとして、閉じてよい（True）。
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
        親ウインドウ上に、自動で消える非ブロッキングの案内を出す（messagebox は閉じる操作を妨げるので使わない）。
        表示できなくてもエラーにしない（データは DB にある）。
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
        登録不可（"no_candidates"）の行を CSV 出力する。出力後、その行を一覧と DB から削除する
        （削除しないと、次に開いたときにまた「未処理」として表示される）。_unregistrable_rows は閉じるまで保持する。
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
            # 対応済みの行は履歴に残さず、即座に物理削除する。
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
        右クリックした行に「不一致として除外」などのメニューを出す。
        右クリックした行が複数選択の一部なら選択中の全行が対象。そうでなければ、その行だけを選び直して対象にする。
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
            menu.add_command(
                label=f"候補なしとする（選択中の{len(target_iids)}件）",
                command=lambda: self._mark_multiple_as_no_candidates(target_iids),
            )
        else:
            menu.add_command(
                label="不一致として除外",
                command=lambda: self._mark_as_mismatched(target_iids[0]),
            )
            menu.add_command(
                label="候補なしとする",
                command=lambda: self._mark_as_no_candidates(target_iids[0]),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _apply_mismatch(self, iid, reason):
        """
        1行を「不一致として除外」する共通処理。一覧と DB から即座に削除し、_mismatched_rows（閉じるまで保持）へ移す。
        登録不可リスト（_unregistrable_rows）とは別に管理する。
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
        人が「この行に一致する計画は無い」と判断した1行を除外する。理由は自由記述で尋ね、空欄・キャンセルなら既定の文言を使う。
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
        """複数選択した行を一括で「不一致として除外」する。理由は1回だけ尋ね、全行に同じ理由を使う。"""
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

    def _apply_no_candidates(self, iid):
        """
        1行を人の判断で「候補なしとする」（登録不可リストへ移す）。CSV の理由欄は、機械判定と同じ固定の文言になる。
        DB の行はここで即座に削除する。削除しないと、再度開いたときに再照合で候補が見つかり、登録待ち一覧に復活する
        （機械判定の行は候補が無いので復活せず、CSV 出力時の削除でよい）。
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

        self._unregistrable_rows.append(row)

    def _mark_as_no_candidates(self, iid):
        """
        人が「対応する計画は無い」と判断した1行を登録不可リストへ移す。理由は固定の文言なので、誤クリック防止のため確認だけ挟む。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        if not messagebox.askyesno(
            "候補なしとする",
            f"ロットNo. {row.get('lot_no')} を候補なしとして登録不可リストに移動します。よろしいですか？",
            parent=self,
        ):
            return

        self._apply_no_candidates(iid)
        self._update_status_label()

    def _mark_multiple_as_no_candidates(self, iids):
        """複数選択した行を一括で「候補なしとする」。確認ダイアログは1回だけ。"""
        valid_iids = [iid for iid in iids if iid in self._row_by_iid]
        if not valid_iids:
            return

        lot_no_preview = "、".join(self._row_by_iid[iid].get("lot_no", "") for iid in valid_iids[:5])
        if len(valid_iids) > 5:
            lot_no_preview += " 他"
        if not messagebox.askyesno(
            "候補なしとする",
            f"選択中の{len(valid_iids)}件（ロットNo: {lot_no_preview}）を"
            "まとめて候補なしとして登録不可リストに移動します。よろしいですか？",
            parent=self,
        ):
            return

        for iid in valid_iids:
            self._apply_no_candidates(iid)
        self._update_status_label()

    def on_export_mismatched_csv(self):
        """
        「不一致として除外」した行を CSV 出力する（理由は行ごとの入力値）。DB からは除外時に削除済みなので、ここでは書き出すだけ。
        実際に書き出せたら True。_on_close() が「出力できたか」の判定に使う。
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

    # ------------------------------------------------------------------
    # 登録済みリスト（is_already_registered()がTrueと判定した行）
    # ------------------------------------------------------------------

    def add_already_registered_rows(self, rows):
        """
        新しい取込で見つかった「登録済み」行を追記する。既に開いている一覧に、もう一度取り込んだときに呼ぶ
        （lift() だけでは今回の判定結果が失われる）。件数表示と詳細ウインドウも更新する。
        """
        if not rows:
            return
        self._already_registered_rows.extend(rows)
        self._update_status_label()
        if self._already_registered_window is not None and self._already_registered_window.winfo_exists():
            self._populate_already_registered_tree()

    def on_show_already_registered_list(self):
        """「登録済みリストを表示」ボタン。開いていれば前面に出すだけで、多重に開かない。"""
        if self._already_registered_window is not None and self._already_registered_window.winfo_exists():
            self._already_registered_window.lift()
            return

        window = tk.Toplevel(self)
        window.title("実績CSV取込：対象外一覧（登録済み／数量0）")
        window.geometry("980x400")
        center_window(window, self)
        window.transient(self)
        # 親が最小化中だと transient のウインドウが表示されないので、先に元に戻す（UI_WORKFLOW_FIXES_NOTES.md グループO）。
        if self.state() == "iconic":
            self.deiconify()

        ttk.Label(
            window,
            text=(
                "通常のステージング一覧には表示されなかった行です（理由は「理由」列を参照）。\n"
                "右クリックで通常の一覧に戻して訂正できます（複数選択中は選択中の全行が対象）。"
            ),
            foreground="gray", padding=(10, 10, 10, 0),
        ).pack(anchor=tk.W)

        action_frame = ttk.Frame(window, padding=(10, 5, 10, 10))
        action_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(
            action_frame, text="対象外一覧をCSV出力", command=self.on_export_already_registered_csv,
        ).pack(side=tk.LEFT)

        tree_frame = ttk.Frame(window, padding=(10, 5, 10, 0))
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = (
            "lot_no", "product_name", "daily_qty", "report_date",
            "matched_kitting_list_no", "existing_qty", "reason",
        )
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        tree.heading("lot_no", text="ロットNo")
        tree.heading("product_name", text="製品名")
        tree.heading("daily_qty", text="CSVの実績数")
        tree.heading("report_date", text="払い出し日（参考）")
        tree.heading("matched_kitting_list_no", text="一致した計画（キッティングリストNo）")
        tree.heading("existing_qty", text="登録済みの数量")
        tree.heading("reason", text="対象外の理由")
        tree.column("lot_no", width=100, anchor=tk.W)
        tree.column("product_name", width=180, anchor=tk.W)
        tree.column("daily_qty", width=90, anchor=tk.E)
        tree.column("report_date", width=120, anchor=tk.CENTER)
        tree.column("matched_kitting_list_no", width=170, anchor=tk.W)
        tree.column("existing_qty", width=100, anchor=tk.E)
        tree.column("reason", width=140, anchor=tk.W)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        tree.configure(yscrollcommand=vsb.set)
        vsb.configure(command=tree.yview)

        tree.bind("<Button-3>", self._on_already_registered_right_click)

        self._already_registered_window = window
        self._already_registered_tree = tree
        self._populate_already_registered_tree()

    def _populate_already_registered_tree(self):
        """登録済みリストの詳細ウインドウの Treeview を作り直す（追記・戻す操作の後に呼ぶ）。"""
        tree = self._already_registered_tree
        for item in tree.get_children():
            tree.delete(item)
        self._already_registered_by_iid = {}

        for entry in self._already_registered_rows:
            iid = tree.insert("", tk.END, values=(
                entry.get("lot_no", ""),
                entry.get("product_name", ""),
                entry.get("daily_qty", ""),
                entry.get("report_date") or "",
                entry.get("matched_kitting_list_no") or "",
                entry.get("existing_qty") if entry.get("existing_qty") is not None else "",
                EXCLUSION_REASON_LABELS.get(entry.get("reason"), entry.get("reason") or ""),
            ))
            self._already_registered_by_iid[iid] = entry

    def _on_already_registered_right_click(self, event):
        """
        登録済みリスト詳細ウインドウの右クリックメニュー。「通常の一覧に戻す」の1操作だけを出す
        （戻した後は、通常の一覧の「不一致として除外」「候補なしとする」で対応できるため）。複数選択時の扱いは _on_right_click() と同じ。
        """
        tree = self._already_registered_tree
        clicked_iid = tree.identify_row(event.y)
        if not clicked_iid:
            return

        current_selection = tree.selection()
        if len(current_selection) > 1 and clicked_iid in current_selection:
            target_iids = list(current_selection)
        else:
            tree.selection_set(clicked_iid)
            target_iids = [clicked_iid]

        menu = tk.Menu(self._already_registered_window, tearoff=0)
        label = "通常の一覧に戻す（訂正する）"
        if len(target_iids) > 1:
            label += f"（選択中の{len(target_iids)}件）"
        menu.add_command(label=label, command=lambda: self._revert_already_registered_rows(target_iids))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _revert_already_registered_rows(self, iids):
        """
        登録済みリストの行を保留行として DB に書き戻し、登録待ち一覧を全件読み直す（まれな訂正操作なので、個別に差し込まない）。
        """
        entries = [self._already_registered_by_iid[iid] for iid in iids if iid in self._already_registered_by_iid]
        if not entries:
            return

        count = len(entries)
        lot_no_preview = "、".join(e.get("lot_no", "") for e in entries[:5])
        if count > 5:
            lot_no_preview += " 他"
        if not messagebox.askyesno(
            "通常の一覧に戻す",
            f"選択中の{count}件（ロットNo: {lot_no_preview}）を通常の登録待ち一覧に戻します。"
            "よろしいですか？",
            parent=self._already_registered_window,
        ):
            return

        for entry in entries:
            upsert_pending_csv_import_row({
                "csv_row_no": entry.get("csv_row_no"),
                "lot_no": entry["lot_no"],
                "product_name": entry["product_name"],
                "daily_qty": entry["daily_qty"],
                "report_date": entry.get("report_date"),
                "worker_id": entry.get("worker_id"),
            }, import_batch_id=entry.get("import_batch_id"))
            self._already_registered_rows.remove(entry)

        # 戻した行を候補付きで表示するには再照合が必要なので、_load_staged_rows_from_db() で全件読み直す。
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._row_by_iid = {}
        for row in _load_staged_rows_from_db():
            self._insert_staging_row(row)

        self._populate_already_registered_tree()
        self._update_status_label()

    def on_export_already_registered_csv(self):
        """
        登録済みリストを CSV 出力する。DB に対応する行が無いので、出力後に一覧や DB から消すものは無い（出力後も「戻す」ができる）。
        """
        if not self._already_registered_rows:
            messagebox.showinfo("対象外一覧", "対象外と判定された行はありません。", parent=self._already_registered_window)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_excluded_rows.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self._already_registered_window,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "lot_no", "product_name", "daily_qty", "report_date",
                    "matched_kitting_list_no", "existing_qty", "reason",
                ])
                for entry in self._already_registered_rows:
                    writer.writerow([
                        entry.get("lot_no", ""),
                        entry.get("product_name", ""),
                        entry.get("daily_qty", ""),
                        entry.get("report_date") or "",
                        entry.get("matched_kitting_list_no") or "",
                        entry.get("existing_qty") if entry.get("existing_qty") is not None else "",
                        EXCLUSION_REASON_LABELS.get(entry.get("reason"), entry.get("reason") or ""),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self._already_registered_window)
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self._already_registered_window)
