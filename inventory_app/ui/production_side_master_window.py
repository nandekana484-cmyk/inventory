# ui/production_side_master_window.py
"""
生産面マスタの画面（2026-10-07新設、2026-10-08に編集モード・判定結果列等を
追加）。構成基板数マスター・基板丁数マスター（ui/board_structure_import_
window.py・ui/parts_attributes_import_window.py）と同じ「CSVをマスタとした
差分同期」方式のCSV取込・出力に加え、画面上での直接編集（先行面・後行面の
有無の切替、行の追加・削除）を提供する。

一覧の1行＝(setup_file_no, mounting_line)。保存先（models.production_side_master）
は(setup_file_no, mounting_line, production_side)単位で持つため、画面編集時は
「元の状態」と「現在の状態」を行ごとに比較し、変化した面（先行面・後行面）だけを
upsert/deleteする（CSV取込のような全件差分同期をここで使うと、画面編集では
持っていない補助列（ボンド打ちフラグ等）がNoneで上書きされてしまうため、
あえて使わない）。

編集モード（2026-10-08追加）：通常は閲覧専用（一覧クリックで内容は変わらない）。
「編集」ボタンで編集モードに入り、先行面・後行面セルのクリック・行の追加・削除が
可能になる。「保存」で確定して閲覧専用へ戻る、「元に戻す」で未保存の変更を
すべて取り消して閲覧専用へ戻る。
"""
import csv
import threading
import queue
import unicodedata

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from models.production_side_master import (
    list_production_side_groups, get_production_side_count_summary,
    compute_production_side_sync_plan, apply_production_side_sync,
    normalize_setup_file_no, normalize_mounting_line,
    upsert_production_side, delete_production_side, delete_production_side_group,
    list_production_side_rows_raw, compute_file_no_status_map,
    SIDE_LABEL_TO_VALUE, SIDE_VALUE_TO_LABEL,
)
from models.operation_log import log_operation
from models.kitting_plan import (
    find_master_plan_discrepancies, find_unregistered_production_side_file_nos,
    find_registered_file_nos_missing_line_combinations, list_mounting_lines_by_file_no,
)
from services.csv_parsing_common import _open_csv_with_fallback
from ui.loading_window import LoadingWindow
from ui.warnings_list_window import WarningsListWindow
from ui.window_utils import center_window
from ui.checkable_treeview import CHECKED_MARK, UNCHECKED_MARK

# CSVの列名（固定、列名ゆらぎ吸収は行わない。本タスクの仕様で列名が明示されているため）
COL_NO = "No"
COL_SELECT = "選択"
COL_DELETE = "削除"
COL_SETUP_FILE_NO = "セットアップファイルNo"
COL_MOUNTING_LINE = "実装ライン"
COL_PRODUCTION_SIDE = "生産面"
COL_BOND_FLAG = "ボンド打ちフラグ"
COL_COMMON_PARTS_GROUP = "共通部品グループ"
COL_LINE_PRIORITY = "ライン優先順位"
COL_TAKT_TIME = "タクト時間"

CSV_COLUMNS = [
    COL_NO, COL_SELECT, COL_DELETE, COL_SETUP_FILE_NO, COL_MOUNTING_LINE,
    COL_PRODUCTION_SIDE, COL_BOND_FLAG, COL_COMMON_PARTS_GROUP,
    COL_LINE_PRIORITY, COL_TAKT_TIME,
]

_PREVIEW_HEAD_COUNT = 10

_COLUMN_LABELS = {
    "setup_file_no": "セットアップファイルNo", "mounting_line": "実装ライン",
    "has_side1": "先行面", "has_side2": "後行面", "status": "判定結果（ファイルNo単位）",
}
_SEARCHABLE_COLUMNS = ("setup_file_no", "mounting_line")
_KEY_COLUMN = "setup_file_no"

# ファイルNo単位の判定結果（True/False/None、models.production_side_master.
# compute_file_no_status_map()の戻り値の値）の表示ラベル。
_FILE_STATUS_LABELS = {True: "2回目あり", False: "1回目のみ", None: "未登録"}
_FILE_STATUS_SORT_WEIGHT = {True: 0, False: 1, None: 2}

# 行の背景色（未保存の変更の種類ごと。既存の画面で使っている配色を踏襲し、
# 「追加」は緑系（ui.plan_candidate_dialogのauto_confirmableと同じ）、
# 「変更」は黄系（同large_diffと同じ）、「削除予定」は赤系（同large_diff_both
# と同じ）を再利用する。新しい配色を増やさないことで、アプリ全体の配色の
# 意味が利用者にとって一貫するようにする。
_ROW_TAG_ADDED = "row_added"
_ROW_TAG_CHANGED = "row_changed"
_ROW_TAG_DELETED = "row_deleted"
_ROW_BG_ADDED = "#c8f7c5"
_ROW_BG_CHANGED = "#fff3cd"
_ROW_BG_DELETED = "#ffb3b3"

# 判定結果（ファイルNo単位）の文字色による区別（上の背景色とは別のプロパティ
# のため、同じ行に両方のタグを付けても競合しない）。
_CLASS_TAG_SIDE2 = "class_side2"
_CLASS_TAG_SIDE1_ONLY = "class_side1_only"
_CLASS_FG_SIDE2 = "#0d47a1"
_CLASS_FG_SIDE1_ONLY = "#7a4a00"


def _normalize_for_search(text) -> str:
    if text is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    return normalized.lower().strip()


def _parse_production_side_csv(file_path):
    """
    生産面マスタCSV（No,選択,削除,セットアップファイルNo,実装ライン,生産面,
    ボンド打ちフラグ,共通部品グループ,ライン優先順位,タクト時間）を解析する
    （DBへの書き込みは一切行わない。取込前の確認ダイアログ用の差分計算まで行う）。

    No・選択・削除列は読み取らない（仕様通り無視）。

    必須：セットアップファイルNo・実装ライン・生産面（いずれかが空の行は
    警告の上スキップ）。生産面は「先行面」「後行面」のいずれかであること
    （それ以外の値の行は警告の上スキップ）。

    CSV内の重複キー（正規化後の(setup_file_no, mounting_line, production_side)
    が同じ行が複数）：最後に出現した行の値を採用する（既存のON CONFLICT上書き
    の挙動と一致させるため）。重複の件数は警告として全件記録する。

    「後行面だけがあり先行面が無い組」：(setup_file_no, mounting_line)単位で
    集計し、全件を警告として記録する（登録を妨げない、情報提供のみ）。

    ボンド打ちフラグ・共通部品グループ・ライン優先順位・タクト時間は、
    判定には使わず値をそのまま（文字列として）保存する。

    戻り値：{
        "resolved_rows": [{"setup_file_no", "mounting_line", "production_side",
            "bond_flag", "common_parts_group", "line_priority", "takt_time"}, ...]
            （重複解決後、1キー1件、setup_file_no/mounting_lineは正規化済み）,
        "plan": compute_production_side_sync_plan()の戻り値,
        "total_rows": CSVの有効データ行数,
        "skipped_count": 必須値空欄・生産面不正でスキップした行数,
        "warnings": [...]（必須値空欄・生産面不正・CSV内重複・後行面のみの組、全て含む全件表示用）,
        "abort_reason": str または None,
    }
    """
    warnings = []

    with _open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return {
            "resolved_rows": [], "plan": None, "total_rows": 0, "skipped_count": 0,
            "warnings": [], "abort_reason": "CSVにデータ行が1件も無いため、インポートを中断しました（削除も行っていません）。",
        }

    skipped_count = 0
    # (setup_file_no_norm, mounting_line_norm, production_side) -> [(row_no, row_data), ...]
    occurrences = {}
    order = []

    for i, row in enumerate(rows, start=2):
        setup_file_no_raw = (row.get(COL_SETUP_FILE_NO) or "").strip()
        mounting_line_raw = (row.get(COL_MOUNTING_LINE) or "").strip()
        side_label = (row.get(COL_PRODUCTION_SIDE) or "").strip()

        if not setup_file_no_raw or not mounting_line_raw or not side_label:
            warnings.append(f"{i}行目: セットアップファイルNo・実装ライン・生産面のいずれかが空のためスキップしました。")
            skipped_count += 1
            continue

        if side_label not in SIDE_LABEL_TO_VALUE:
            warnings.append(f"{i}行目: 生産面「{side_label}」は「先行面」「後行面」のいずれでもないためスキップしました。")
            skipped_count += 1
            continue

        norm_file = normalize_setup_file_no(setup_file_no_raw)
        norm_line = normalize_mounting_line(mounting_line_raw)
        production_side = SIDE_LABEL_TO_VALUE[side_label]
        key = (norm_file, norm_line, production_side)

        data = {
            "setup_file_no": norm_file, "mounting_line": norm_line, "production_side": production_side,
            "bond_flag": row.get(COL_BOND_FLAG) or None,
            "common_parts_group": row.get(COL_COMMON_PARTS_GROUP) or None,
            "line_priority": row.get(COL_LINE_PRIORITY) or None,
            "takt_time": row.get(COL_TAKT_TIME) or None,
        }

        if key not in occurrences:
            occurrences[key] = []
            order.append(key)
        occurrences[key].append((i, data))

    total_rows = len(rows)

    if not order:
        return {
            "resolved_rows": [], "plan": None, "total_rows": total_rows, "skipped_count": skipped_count,
            "warnings": warnings, "abort_reason": "有効な行が1件も無かったため、削除は行っていません。",
        }

    resolved_rows = []
    for key in order:
        occ = occurrences[key]
        final_data = occ[-1][1]
        resolved_rows.append(final_data)
        if len(occ) > 1:
            row_nos = [row_no for row_no, _d in occ]
            warnings.append(
                f"セットアップファイルNo「{key[0]}」実装ライン「{key[1]}」{SIDE_VALUE_TO_LABEL[key[2]]}："
                f"CSV内に{len(occ)}回登場（{row_nos}行目）、最後の行の値を採用しました。"
            )

    # 後行面だけがあり先行面が無い組の警告
    groups_sides = {}
    for key in order:
        group_key = (key[0], key[1])
        groups_sides.setdefault(group_key, set()).add(key[2])
    for group_key, sides in groups_sides.items():
        if sides == {"2"}:
            warnings.append(
                f"セットアップファイルNo「{group_key[0]}」実装ライン「{group_key[1]}」："
                "後行面のみの登録で、先行面がありません。"
            )

    plan = compute_production_side_sync_plan(resolved_rows)

    return {
        "resolved_rows": resolved_rows, "plan": plan, "total_rows": total_rows,
        "skipped_count": skipped_count, "warnings": warnings, "abort_reason": None,
    }


def _build_confirmation_message(parse_result):
    plan = parse_result["plan"]
    lines = [
        f"読み込んだ行数：{parse_result['total_rows']}行",
        "",
        f"新規追加：{len(plan['to_add'])}件",
        f"値が変わる：{len(plan['to_update'])}件",
        f"変更なし：{len(plan['unchanged'])}件",
        f"削除：{len(plan['to_delete'])}件",
    ]
    if plan["to_delete"]:
        lines.append("")
        lines.append("※ このCSVに無いデータは削除されます。")
        head = plan["to_delete"][:_PREVIEW_HEAD_COUNT]
        for item in head:
            lines.append(f"　・{item['setup_file_no']} / {item['mounting_line']} / {SIDE_VALUE_TO_LABEL.get(item['production_side'], item['production_side'])}")
        remaining = len(plan["to_delete"]) - len(head)
        if remaining > 0:
            lines.append(f"　　...ほか{remaining}件")

    if parse_result["warnings"]:
        lines.append("")
        lines.append(f"※ 警告：{len(parse_result['warnings'])}件（詳細は取込後に別ウィンドウで確認できます）")

    lines.append("")
    lines.append("この内容で取込を実行しますか？")
    return "\n".join(lines)


def _build_completion_message(result, count_summary):
    plan = result["plan"]
    msg = (
        f"読み込んだ行数：{result['total_rows']}行\n"
        f"追加：{len(plan['to_add'])}件 / 更新：{len(plan['to_update'])}件 / "
        f"変更なし：{len(plan['unchanged'])}件 / 削除：{len(plan['to_delete'])}件\n"
        f"スキップした行数：{result['skipped_count']}件\n"
        f"取込後の登録件数：{count_summary['total']}組"
    )
    if result["warnings"]:
        msg += f"\n\n警告：{len(result['warnings'])}件（詳細は別ウィンドウで確認できます）"
    return msg


class ProductionSideMasterWindow(tk.Toplevel):
    """
    生産面マスタの一覧・編集・CSV取込/出力画面。

    self._rows：画面内でのみ保持する編集中の状態（DBからの読み込み結果＋
    画面上の追加・削除・切替を反映した最新状態）。各要素：
        {"setup_file_no", "mounting_line", "has_side1", "has_side2",
         "orig_has_side1", "orig_has_side2", "is_new", "pending_delete"}
    "orig_has_side1"/"orig_has_side2"は読み込み時点（または直前の保存時点）の
    値で、保存時にこれと現在値を比較し、変化した面だけをupsert/deleteする
    （差分だけを個別に反映する方式。CSV取込のような全件差分同期をここで使うと、
    画面編集では持っていない補助列（ボンド打ちフラグ等）がNoneで上書きされて
    しまうため、あえて使わない）。

    "is_new"：画面上で追加し、まだ保存していない行。
    "pending_delete"：画面上で削除を指示したが、まだ保存していない行
    （2026-10-08改訂：即座にself._rowsから取り除くのではなく、保存するまで
    一覧に残し、背景色で区別する。「元に戻す」で取り消せるようにするため）。
    is_new かつ pending_delete の行（追加した直後に同じ編集セッション内で
    削除した行）は、保存対象が何も無いためself._rowsから即座に取り除く
    （on_delete_row()参照）。
    """
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker or {}
        self.selected_csv_path = None

        self._rows = []
        self._mode = "view"
        self._sort_column = _KEY_COLUMN
        self._sort_ascending = True

        self.title("生産面マスタ")
        self.geometry("760x640")
        center_window(self, parent)

        top_frame = ttk.Frame(self, padding=10)
        top_frame.pack(fill=tk.X)
        ttk.Button(top_frame, text="CSV選択", command=self.on_select_csv).pack(side=tk.LEFT, padx=(0, 5))
        self.lbl_csv_path = ttk.Label(top_frame, text="（未選択）", foreground="blue")
        self.lbl_csv_path.pack(side=tk.LEFT, padx=5)
        self.btn_import = ttk.Button(top_frame, text="CSV取込", command=self.on_import_execute)
        self.btn_import.pack(side=tk.LEFT, padx=15)
        ttk.Button(top_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT)

        # 計画データとの食い違い・未登録の確認（2026-10-07新設・2026-10-08
        # ファイルNo単位に改訂、D-9x改訂参照）。マスターでは後行面なしだが
        # 計画データに面2がある場合、計画データを優先して面2のみ表示する
        # 既存方針（D-8）は変えないが、その食い違いの事実は利用者が確認できる
        # ようにする。
        check_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        check_frame.pack(fill=tk.X)
        ttk.Button(
            check_frame, text="計画との食い違いを確認", command=self.on_show_discrepancies,
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(
            check_frame, text="マスター未登録のファイルNoを確認", command=self.on_show_unregistered,
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(
            check_frame, text="実装ライン単位の未登録組み合わせ（参考情報）", command=self.on_show_missing_lines,
        ).pack(side=tk.LEFT)

        filter_frame = ttk.Frame(self, padding=(10, 5, 10, 0))
        filter_frame.pack(fill=tk.X)
        ttk.Label(filter_frame, text="検索：").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.search_var, width=24).pack(side=tk.LEFT, padx=(5, 5))
        self.search_var.trace_add("write", lambda *_args: self._apply_filter_and_render())
        ttk.Button(filter_frame, text="クリア", command=self.on_clear_search).pack(side=tk.LEFT)

        status_frame = ttk.Frame(self, padding=(10, 5, 10, 0))
        status_frame.pack(fill=tk.X)
        self.lbl_count = ttk.Label(status_frame, text="登録件数: -組")
        self.lbl_count.pack(side=tk.LEFT)
        # 編集モード中であることを示すラベル（見出し・色での表示、2026-10-08追加）。
        self.lbl_mode = ttk.Label(status_frame, text="", font=("", 10, "bold"))
        self.lbl_mode.pack(side=tk.LEFT, padx=(15, 0))

        edit_frame = ttk.Frame(self, padding=(10, 5, 10, 0))
        edit_frame.pack(fill=tk.X)
        ttk.Label(edit_frame, text="セットアップファイルNo：").pack(side=tk.LEFT)
        self.entry_new_file_no = ttk.Entry(edit_frame, width=10)
        self.entry_new_file_no.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(edit_frame, text="実装ライン：").pack(side=tk.LEFT)
        self.entry_new_mounting_line = ttk.Entry(edit_frame, width=6)
        self.entry_new_mounting_line.pack(side=tk.LEFT, padx=(0, 10))
        self.btn_add_row = ttk.Button(edit_frame, text="行を追加", command=self.on_add_row)
        self.btn_add_row.pack(side=tk.LEFT)
        self.btn_delete_row = ttk.Button(edit_frame, text="選択行を削除", command=self.on_delete_row)
        self.btn_delete_row.pack(side=tk.LEFT, padx=(5, 0))

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.lbl_empty = ttk.Label(
            tree_frame, text="検索条件に一致するデータがありません。", foreground="#666666",
        )

        cols = ("setup_file_no", "mounting_line", "has_side1", "has_side2", "status")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for col in cols:
            self.tree.heading(col, text=_COLUMN_LABELS[col], command=lambda c=col: self.sort_by_column(c))
        self.tree.column("setup_file_no", width=160, anchor=tk.W)
        self.tree.column("mounting_line", width=90, anchor=tk.W)
        self.tree.column("has_side1", width=70, anchor=tk.CENTER)
        self.tree.column("has_side2", width=70, anchor=tk.CENTER)
        self.tree.column("status", width=170, anchor=tk.CENTER)

        self.tree.tag_configure(_ROW_TAG_ADDED, background=_ROW_BG_ADDED)
        self.tree.tag_configure(_ROW_TAG_CHANGED, background=_ROW_BG_CHANGED)
        self.tree.tag_configure(_ROW_TAG_DELETED, background=_ROW_BG_DELETED)
        self.tree.tag_configure(_CLASS_TAG_SIDE2, foreground=_CLASS_FG_SIDE2, font=("", 9, "bold"))
        self.tree.tag_configure(_CLASS_TAG_SIDE1_ONLY, foreground=_CLASS_FG_SIDE1_ONLY)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Button-1>", self._on_tree_click)

        hint_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        hint_frame.pack(fill=tk.X)
        ttk.Label(
            hint_frame,
            text=(
                "通常は閲覧専用です。「編集」を押すと、先行面・後行面のセルをクリックして切り替えたり、"
                "行の追加・削除ができます。"
            ),
            foreground="gray",
        ).pack(anchor=tk.W)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        self.btn_edit = ttk.Button(btn_frame, text="編集", command=self.on_enter_edit_mode)
        self.btn_edit.pack(side=tk.LEFT)
        self.btn_save = ttk.Button(btn_frame, text="保存", command=self.on_save)
        self.btn_save.pack(side=tk.LEFT, padx=(5, 0))
        self.btn_revert = ttk.Button(btn_frame, text="元に戻す", command=self.on_revert)
        self.btn_revert.pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(btn_frame, text="閉じる", command=self.on_close).pack(side=tk.RIGHT, padx=5)

        self._update_column_headers()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.load_rows()

    # ------------------------------------------------------------------
    # 一覧の読み込み・表示
    # ------------------------------------------------------------------

    def load_rows(self):
        """DBから全件を再取得し、編集状態を作り直す（未保存の変更は失われる。
        取込・保存・「元に戻す」の直後のみ呼ぶこと）。閲覧専用モードへ戻す。"""
        groups = list_production_side_groups()
        self._rows = [
            {
                **g, "orig_has_side1": g["has_side1"], "orig_has_side2": g["has_side2"],
                "is_new": False, "pending_delete": False,
            }
            for g in groups
        ]
        self._set_mode("view")
        self._apply_filter_and_render()

    def on_clear_search(self):
        self.search_var.set("")

    def sort_by_column(self, col):
        if self._sort_column == col:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = col
            self._sort_ascending = True
        self._update_column_headers()
        self._apply_filter_and_render()

    def _update_column_headers(self):
        for col, label in _COLUMN_LABELS.items():
            if col == self._sort_column:
                arrow = " ▲" if self._sort_ascending else " ▼"
                self.tree.heading(col, text=label + arrow)
            else:
                self.tree.heading(col, text=label)

    def _row_matches_search(self, row, needle_normalized):
        if not needle_normalized:
            return True
        for col in _SEARCHABLE_COLUMNS:
            if needle_normalized in _normalize_for_search(row.get(col)):
                return True
        return False

    def _row_edit_tag(self, row):
        """未保存の変更の種類（追加／削除予定／変更）を示す背景タグ。無ければNone。"""
        if row["is_new"]:
            return _ROW_TAG_ADDED
        if row["pending_delete"]:
            return _ROW_TAG_DELETED
        if row["has_side1"] != row["orig_has_side1"] or row["has_side2"] != row["orig_has_side2"]:
            return _ROW_TAG_CHANGED
        return None

    def _current_status_map(self):
        """
        ファイルNo単位の判定結果（2回目あり/1回目のみ/未登録）を、画面の
        現在の状態（未保存の変更を含む）から計算する。保存を試みた場合に
        削除される行（pending_delete）は対象から除外し、「保存したらどうなるか」
        を表す（2026-10-08追加、D-9x改訂）。
        """
        return compute_file_no_status_map([r for r in self._rows if not r["pending_delete"]])

    def _apply_filter_and_render(self):
        needle = _normalize_for_search(self.search_var.get())
        filtered = [row for row in self._rows if self._row_matches_search(row, needle)]
        status_map = self._current_status_map()

        col = self._sort_column
        if col in ("has_side1", "has_side2"):
            # 昇順＝有り（チェック済み）を先頭にまとめる、降順＝その逆。
            # 同じファイルNoの行がまとまって見えるよう、常に(setup_file_no,
            # mounting_line)をタイブレーク（第2・第3キー）にする。
            filtered.sort(key=lambda r: (not r[col], r["setup_file_no"], r["mounting_line"]))
            if not self._sort_ascending:
                filtered.reverse()
        elif col == "status":
            filtered.sort(
                key=lambda r: (
                    _FILE_STATUS_SORT_WEIGHT.get(status_map.get(r["setup_file_no"]), 2),
                    r["setup_file_no"], r["mounting_line"],
                ),
                reverse=not self._sort_ascending,
            )
        else:
            filtered.sort(
                key=lambda r: (str(r.get(col) or ""), r["setup_file_no"], r["mounting_line"]),
                reverse=not self._sort_ascending,
            )

        # iidを行の識別に使うsetup_file_no/mounting_lineの組に揃える
        # （クリックハンドラでの照合用。is_newの行はDB側に同名の既存行が無い
        # 前提のため、同じ(setup_file_no, mounting_line)が重複することは
        # on_add_row()側のチェックで防止している）。
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in filtered:
            iid = f"{row['setup_file_no']}\x1f{row['mounting_line']}"
            status = status_map.get(row["setup_file_no"])
            tags = []
            edit_tag = self._row_edit_tag(row)
            if edit_tag:
                tags.append(edit_tag)
            if status is True:
                tags.append(_CLASS_TAG_SIDE2)
            elif status is False:
                tags.append(_CLASS_TAG_SIDE1_ONLY)
            self.tree.insert("", tk.END, iid=iid, tags=tuple(tags), values=(
                row["setup_file_no"], row["mounting_line"],
                CHECKED_MARK if row["has_side1"] else UNCHECKED_MARK,
                CHECKED_MARK if row["has_side2"] else UNCHECKED_MARK,
                _FILE_STATUS_LABELS.get(status, "未登録"),
            ))

        if filtered:
            self.lbl_empty.pack_forget()
        else:
            self.lbl_empty.pack(fill=tk.X, pady=(0, 5), before=self.tree)

        self._update_count_label(len(filtered), bool(needle))

    def _count_unsaved_changes(self):
        return sum(1 for row in self._rows if self._row_edit_tag(row) is not None)

    def _update_count_label(self, displayed_count=None, filter_active=False):
        total = sum(1 for row in self._rows if not row["pending_delete"])
        base = f"登録件数: {total}組"
        if filter_active and displayed_count is not None:
            base = f"表示: {displayed_count}組 / " + base
        unsaved = self._count_unsaved_changes()
        if unsaved:
            base += f"　【未保存の変更 {unsaved}件】"
        self.lbl_count.config(text=base)

    # ------------------------------------------------------------------
    # 編集モードの切替
    # ------------------------------------------------------------------

    def _set_mode(self, mode):
        """
        mode："view"（閲覧専用）または"edit"（編集中）。ボタン・入力欄の
        有効/無効と、モード表示ラベルの文言・色を切り替える（2026-10-08追加）。
        既存の他画面（既定DB未選択時の操作禁止等）と同じ、ウィジェットを
        隠すのではなくstate=DISABLEDで無効化する方針に揃える。
        """
        self._mode = mode
        is_edit = mode == "edit"

        self.btn_edit.config(state=tk.DISABLED if is_edit else tk.NORMAL)
        self.btn_add_row.config(state=tk.NORMAL if is_edit else tk.DISABLED)
        self.btn_delete_row.config(state=tk.NORMAL if is_edit else tk.DISABLED)
        self.btn_save.config(state=tk.NORMAL if is_edit else tk.DISABLED)
        self.btn_revert.config(state=tk.NORMAL if is_edit else tk.DISABLED)
        self.entry_new_file_no.config(state=tk.NORMAL if is_edit else tk.DISABLED)
        self.entry_new_mounting_line.config(state=tk.NORMAL if is_edit else tk.DISABLED)

        if is_edit:
            self.lbl_mode.config(text="■ 編集モード（保存または元に戻すまで、他の操作にご注意ください）", foreground="#b00000")
        else:
            self.lbl_mode.config(text="閲覧専用", foreground="#666666")

    def on_enter_edit_mode(self):
        self._set_mode("edit")

    def on_revert(self):
        """未保存の変更をすべて取り消し、保存済みの状態を再読み込みして閲覧専用に戻る。"""
        self.load_rows()

    # ------------------------------------------------------------------
    # 編集（先行面/後行面の切替・行の追加・削除）
    # ------------------------------------------------------------------

    def _find_row(self, setup_file_no, mounting_line):
        for row in self._rows:
            if row["setup_file_no"] == setup_file_no and row["mounting_line"] == mounting_line:
                return row
        return None

    def _on_tree_click(self, event):
        """
        閲覧専用モードでは何もしない（一覧をクリックしても内容が変わらない、
        2026-10-08追加）。編集モード中も、先行面・後行面のセル以外をクリック
        しても何も起きない（既存の仕様を維持）。削除予定（pending_delete）の
        行はクリックしても切り替わらない（削除予定行の内容を変えても保存時に
        使われないため）。
        """
        if self._mode != "edit":
            return

        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id or not column_id:
            return

        col_pos = int(column_id.replace("#", "")) - 1
        cols = ("setup_file_no", "mounting_line", "has_side1", "has_side2", "status")
        if col_pos < 0 or col_pos >= len(cols):
            return
        col_key = cols[col_pos]
        if col_key not in ("has_side1", "has_side2"):
            return

        setup_file_no, mounting_line = row_id.split("\x1f")
        row = self._find_row(setup_file_no, mounting_line)
        if row is None or row["pending_delete"]:
            return
        row[col_key] = not row[col_key]
        self._apply_filter_and_render()
        self.tree.selection_set(row_id)

    def on_add_row(self):
        if self._mode != "edit":
            return
        setup_file_no_raw = self.entry_new_file_no.get().strip()
        mounting_line_raw = self.entry_new_mounting_line.get().strip()
        if not setup_file_no_raw or not mounting_line_raw:
            messagebox.showwarning(
                "警告", "セットアップファイルNo・実装ラインの両方を入力してください。", parent=self.winfo_toplevel(),
            )
            return

        norm_file = normalize_setup_file_no(setup_file_no_raw)
        norm_line = normalize_mounting_line(mounting_line_raw)

        existing = self._find_row(norm_file, norm_line)
        if existing is not None and not existing["pending_delete"]:
            messagebox.showwarning(
                "警告",
                f"セットアップファイルNo「{norm_file}」実装ライン「{norm_line}」は既に登録されています。",
                parent=self.winfo_toplevel(),
            )
            return
        if existing is not None and existing["pending_delete"]:
            messagebox.showwarning(
                "警告",
                f"セットアップファイルNo「{norm_file}」実装ライン「{norm_line}」は削除予定です。"
                "先に「元に戻す」または保存してから追加してください。",
                parent=self.winfo_toplevel(),
            )
            return

        self._rows.append({
            "setup_file_no": norm_file, "mounting_line": norm_line,
            "has_side1": False, "has_side2": False,
            "orig_has_side1": False, "orig_has_side2": False,
            "is_new": True, "pending_delete": False,
        })
        self.entry_new_file_no.delete(0, tk.END)
        self.entry_new_mounting_line.delete(0, tk.END)
        self._apply_filter_and_render()

    def on_delete_row(self):
        if self._mode != "edit":
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("警告", "削除する行を一覧から選択してください。", parent=self.winfo_toplevel())
            return
        setup_file_no, mounting_line = sel[0].split("\x1f")
        row = self._find_row(setup_file_no, mounting_line)
        if row is None or row["pending_delete"]:
            return
        if not messagebox.askyesno(
            "確認", f"セットアップファイルNo「{setup_file_no}」実装ライン「{mounting_line}」を削除します。よろしいですか？",
            parent=self.winfo_toplevel(),
        ):
            return

        if row["is_new"]:
            # 保存前に追加した行をそのまま削除する場合、保存対象自体が無いため
            # 即座に取り除く（削除予定として残す必要が無い）。
            self._rows.remove(row)
        else:
            row["pending_delete"] = True
        self._apply_filter_and_render()

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------

    def _build_pending_changes(self):
        """
        未保存の変更を、保存前の確認ダイアログ用に整理する（2026-10-08新設）。
        戻り値：(changes, file_no_status_changes)
          changes：[{"setup_file_no","mounting_line","kind"（"add"/"update"/"delete"）,
                     "before","after"}, ...]（表示用の文字列はSIDE_VALUE_TO_LABEL等を
                     使って呼び出し側で組み立てる）
          file_no_status_changes：[(setup_file_no, before_label, after_label), ...]
        """
        changes = []
        for row in self._rows:
            if row["is_new"] and row["pending_delete"]:
                continue  # 追加した直後に削除した行は保存対象が無い
            if row["is_new"]:
                changes.append({
                    "setup_file_no": row["setup_file_no"], "mounting_line": row["mounting_line"],
                    "kind": "add", "before": None,
                    "after": (row["has_side1"], row["has_side2"]),
                })
            elif row["pending_delete"]:
                changes.append({
                    "setup_file_no": row["setup_file_no"], "mounting_line": row["mounting_line"],
                    "kind": "delete", "before": (row["orig_has_side1"], row["orig_has_side2"]),
                    "after": None,
                })
            elif row["has_side1"] != row["orig_has_side1"] or row["has_side2"] != row["orig_has_side2"]:
                changes.append({
                    "setup_file_no": row["setup_file_no"], "mounting_line": row["mounting_line"],
                    "kind": "update", "before": (row["orig_has_side1"], row["orig_has_side2"]),
                    "after": (row["has_side1"], row["has_side2"]),
                })

        affected_file_nos = sorted({c["setup_file_no"] for c in changes})
        old_status_map = compute_file_no_status_map([
            {"setup_file_no": r["setup_file_no"], "has_side1": r["orig_has_side1"], "has_side2": r["orig_has_side2"]}
            for r in self._rows
        ])
        new_status_map = self._current_status_map()

        file_no_status_changes = []
        for fn in affected_file_nos:
            before = old_status_map.get(fn)
            after = new_status_map.get(fn)
            if before != after:
                file_no_status_changes.append((fn, _FILE_STATUS_LABELS[before], _FILE_STATUS_LABELS[after]))

        return changes, file_no_status_changes

    @staticmethod
    def _format_sides(sides):
        if sides is None:
            return "（無し）"
        has1, has2 = sides
        parts = []
        parts.append("先行面" if has1 else "先行面なし")
        parts.append("後行面" if has2 else "後行面なし")
        return "・".join(parts)

    def _build_save_confirmation_message(self, changes, file_no_status_changes):
        lines = [f"変更件数：{len(changes)}件", ""]
        kind_labels = {"add": "追加", "update": "変更", "delete": "削除"}
        head = changes[:_PREVIEW_HEAD_COUNT]
        for c in head:
            lines.append(
                f"　・[{kind_labels[c['kind']]}] {c['setup_file_no']} / {c['mounting_line']}："
                f"{self._format_sides(c['before'])} → {self._format_sides(c['after'])}"
            )
        remaining = len(changes) - len(head)
        if remaining > 0:
            lines.append(f"　　...ほか{remaining}件")

        if file_no_status_changes:
            lines.append("")
            lines.append("※ 以下のファイルNoは判定結果が変わります：")
            for fn, before_label, after_label in file_no_status_changes:
                lines.append(f"　・ファイルNo {fn} が「{before_label}」→「{after_label}」")

        lines.append("")
        lines.append("この内容で保存しますか？")
        return "\n".join(lines)

    def on_save(self):
        if self._mode != "edit":
            return
        changes, file_no_status_changes = self._build_pending_changes()
        if not changes:
            messagebox.showinfo("保存", "未保存の変更はありません。", parent=self.winfo_toplevel())
            return

        if not messagebox.askyesno(
            "保存前の確認", self._build_save_confirmation_message(changes, file_no_status_changes),
            parent=self.winfo_toplevel(),
        ):
            return

        changed_count = 0
        deleted_group_count = 0
        for row in self._rows:
            if row["is_new"] and row["pending_delete"]:
                continue
            fn, ml = row["setup_file_no"], row["mounting_line"]
            if row["pending_delete"]:
                delete_production_side_group(fn, ml)
                deleted_group_count += 1
                continue
            if row["has_side1"] != row["orig_has_side1"]:
                if row["has_side1"]:
                    upsert_production_side(fn, ml, "1")
                else:
                    delete_production_side(fn, ml, "1")
                changed_count += 1
            if row["has_side2"] != row["orig_has_side2"]:
                if row["has_side2"]:
                    upsert_production_side(fn, ml, "2")
                else:
                    delete_production_side(fn, ml, "2")
                changed_count += 1

        log_operation(
            self.current_worker.get("name", "unknown"),
            "生産面マスタ編集",
            detail=f"面の変更{changed_count}件 / 組の削除{deleted_group_count}件",
        )

        messagebox.showinfo("完了", "生産面マスタを保存しました。", parent=self.winfo_toplevel())
        self.load_rows()

    def on_close(self):
        if self._count_unsaved_changes():
            if not messagebox.askyesno(
                "確認", "保存していない変更があります。保存せずに閉じますか？", parent=self.winfo_toplevel(),
            ):
                return
        self.destroy()

    # ------------------------------------------------------------------
    # CSV取込
    # ------------------------------------------------------------------

    def on_select_csv(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], parent=self.winfo_toplevel(),
        )
        if not file_path:
            return
        self.selected_csv_path = file_path
        self.lbl_csv_path.config(text=file_path)

    def on_import_execute(self):
        if not self.selected_csv_path:
            messagebox.showwarning("警告", "CSVファイルを選択してください。", parent=self.winfo_toplevel())
            return
        if self._count_unsaved_changes():
            if not messagebox.askyesno(
                "確認", "画面上の未保存の変更は、CSV取込を行うと失われます。続けますか？",
                parent=self.winfo_toplevel(),
            ):
                return

        self.btn_import.config(state=tk.DISABLED)
        loading = LoadingWindow(self, message="CSVを読み込んでいます…")
        result_queue = queue.Queue()
        file_path = self.selected_csv_path

        def _work():
            try:
                result_queue.put((True, _parse_production_side_csv(file_path)))
            except Exception as e:
                result_queue.put((False, e))

        threading.Thread(target=_work, daemon=True).start()

        def _poll():
            try:
                success, payload = result_queue.get_nowait()
            except queue.Empty:
                self.after(200, _poll)
                return

            loading.destroy()

            if not success:
                self.btn_import.config(state=tk.NORMAL)
                messagebox.showerror(
                    "エラー", f"生産面マスタCSV取込中にエラーが発生しました：\n{payload}\n"
                    "データは取込前の状態のままです。", parent=self.winfo_toplevel(),
                )
                return

            parse_result = payload
            if parse_result["abort_reason"]:
                self.btn_import.config(state=tk.NORMAL)
                messagebox.showwarning("警告", parse_result["abort_reason"], parent=self.winfo_toplevel())
                return

            self._confirm_and_apply(parse_result)

        self.after(200, _poll)

    def _confirm_and_apply(self, parse_result):
        proceed = messagebox.askyesno(
            "取込前の確認", _build_confirmation_message(parse_result), parent=self.winfo_toplevel(),
        )
        if not proceed:
            self.btn_import.config(state=tk.NORMAL)
            return
        self._apply_import(parse_result)

    def _apply_import(self, parse_result):
        self.btn_import.config(state=tk.DISABLED)
        loading = LoadingWindow(self, message="生産面マスタCSVを取り込んでいます…")
        result_queue = queue.Queue()
        resolved_rows = parse_result["resolved_rows"]

        def _work():
            try:
                applied_plan = apply_production_side_sync(resolved_rows)
                result_queue.put((True, applied_plan))
            except Exception as e:
                result_queue.put((False, e))

        threading.Thread(target=_work, daemon=True).start()

        def _poll():
            try:
                success, payload = result_queue.get_nowait()
            except queue.Empty:
                self.after(200, _poll)
                return

            loading.destroy()
            self.btn_import.config(state=tk.NORMAL)

            if not success:
                messagebox.showerror(
                    "エラー", f"生産面マスタCSV取込中に予期しないエラーが発生しました：\n{payload}\n"
                    "データは取込前の状態のままです（登録・更新・削除は1つのトランザクションのため、"
                    "一部だけ反映されることはありません）。", parent=self.winfo_toplevel(),
                )
                return

            applied_plan = payload
            self.load_rows()
            count_summary = get_production_side_count_summary()
            result = {**parse_result, "plan": applied_plan}

            log_operation(
                self.current_worker.get("name", "unknown"),
                "生産面マスタCSV取込",
                detail=(
                    f"追加{len(applied_plan['to_add'])}件 更新{len(applied_plan['to_update'])}件 "
                    f"変更なし{len(applied_plan['unchanged'])}件 削除{len(applied_plan['to_delete'])}件"
                ),
            )

            messagebox.showinfo(
                "生産面マスタCSV取込結果", _build_completion_message(result, count_summary),
                parent=self.winfo_toplevel(),
            )

            if result["warnings"]:
                WarningsListWindow(self, "生産面マスタCSV取込：警告一覧", result["warnings"])

        self.after(200, _poll)

    # ------------------------------------------------------------------
    # CSV出力
    # ------------------------------------------------------------------

    def on_export_csv(self):
        """
        取込と同じ列構成でCSV出力する。出力したファイルをそのまま取り込み
        直せるよう、Noは1から振り直し、選択・削除列は空にする。

        画面でまだ保存していない変更（追加した行・切替中の状態・削除予定）は
        出力に含めない（DBに保存済みの内容を出力する。取込と同じ理由で保存
        済みのデータが正である設計に揃える）。保存していない変更がある場合は
        出力前に確認する。
        """
        if self._count_unsaved_changes():
            if not messagebox.askyesno(
                "確認",
                "画面上の未保存の変更はCSV出力に含まれません（保存済みの内容のみ出力します）。続けますか？",
                parent=self.winfo_toplevel(),
            ):
                return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="production_side_master.csv",
            parent=self.winfo_toplevel(),
        )
        if not save_path:
            return

        rows = list_production_side_rows_raw()
        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_COLUMNS)
                for i, row in enumerate(rows, start=1):
                    writer.writerow([
                        i, "", "",
                        row["setup_file_no"], row["mounting_line"],
                        SIDE_VALUE_TO_LABEL.get(row["production_side"], row["production_side"]),
                        row.get("bond_flag") or "", row.get("common_parts_group") or "",
                        row.get("line_priority") or "", row.get("takt_time") or "",
                    ])
        except OSError as e:
            messagebox.showerror("エラー", f"CSV出力中にエラーが発生しました：\n{e}", parent=self.winfo_toplevel())
            return

        log_operation(
            self.current_worker.get("name", "unknown"),
            "生産面マスタCSV出力",
            detail=f"{len(rows)}行 / {save_path}",
        )
        messagebox.showinfo("完了", f"生産面マスタをCSVへ出力しました：\n{save_path}", parent=self.winfo_toplevel())

    # ------------------------------------------------------------------
    # 計画データとの食い違い・未登録の確認
    # ------------------------------------------------------------------

    def on_show_discrepancies(self):
        """
        「マスターではこのファイルNoは後行面なし（1回目のみ）と判定されて
        いるが、計画データには（いずれかの実装ラインに）このファイルNoの
        面2の計画が存在する」ファイルNoを一覧表示する（2026-10-07新設、
        2026-10-08ファイルNo単位に改訂、D-9x改訂参照）。計画データを優先する
        既存方針（D-8）は変更せず、食い違いの事実を確認できるのみ。
        """
        discrepancies = find_master_plan_discrepancies()
        win = tk.Toplevel(self)
        win.title("生産面マスタ：計画との食い違い一覧（ファイルNo単位）")
        win.geometry("620x420")
        center_window(win, self)

        ttk.Label(
            win, padding=10,
            text=(
                f"マスターでは「1回目のみ」と判定されているファイルNoのうち、"
                f"計画データには面2の計画が存在するもの：{len(discrepancies)}件\n"
                "（計画データを優先し、一覧・候補は従来どおり面2のみ表示しています。"
                "マスターの登録内容が古い可能性があります。）"
            ),
            wraplength=600, justify=tk.LEFT,
        ).pack(fill=tk.X)

        cols = ("setup_file_no", "mounting_lines", "lot_nos")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("setup_file_no", text="セットアップファイルNo")
        tree.heading("mounting_lines", text="面2の計画がある実装ライン")
        tree.heading("lot_nos", text="面2の計画があるロットNo")
        tree.column("setup_file_no", width=140, anchor=tk.W)
        tree.column("mounting_lines", width=140, anchor=tk.W)
        tree.column("lot_nos", width=280, anchor=tk.W)
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(expand=True, fill=tk.BOTH, padx=10, pady=(0, 10))
        for item in discrepancies:
            tree.insert("", tk.END, values=(
                item["setup_file_no"], ", ".join(item["mounting_lines"]), ", ".join(item["lot_nos"]),
            ))

        ttk.Button(win, text="閉じる", command=win.destroy).pack(pady=(0, 10))

    def on_show_unregistered(self):
        """
        計画データに存在するが、生産面マスターに全く登録が無い（どの実装
        ラインにも1件も登録が無い）setup_file_noを一覧表示する（2026-10-07
        新設、2026-10-08ファイルNo単位に改訂、D-9x改訂参照）。
        classify_side1_only_plan()の「生産面マスター未登録」はこの一覧と
        同じ判定基準。

        CSV出力：生産面マスターへの登録に使えるよう、各ファイルNoについて
        計画データに実在する実装ラインをすべて行に展開して出力する（生産面列は
        空のまま出力し、利用者が先行面/後行面を判断して埋めてから取り込み直す
        想定。生産面は必須項目のため、空のままでは通常の取込で警告の上スキップ
        される＝誤って登録されない）。
        """
        unregistered = find_unregistered_production_side_file_nos()
        file_nos = [item["setup_file_no"] for item in unregistered]
        lines_by_file = list_mounting_lines_by_file_no(file_nos)

        win = tk.Toplevel(self)
        win.title("生産面マスタ：未登録のファイルNo一覧")
        win.geometry("480x420")
        center_window(win, self)

        ttk.Label(
            win, padding=10,
            text=f"計画データに存在するが、生産面マスターに登録が無いファイルNo：{len(unregistered)}件",
            wraplength=440, justify=tk.LEFT,
        ).pack(fill=tk.X)

        cols = ("setup_file_no", "mounting_lines")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("setup_file_no", text="セットアップファイルNo")
        tree.heading("mounting_lines", text="計画データ上の実装ライン")
        tree.column("setup_file_no", width=200, anchor=tk.W)
        tree.column("mounting_lines", width=220, anchor=tk.W)
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(expand=True, fill=tk.BOTH, padx=10, pady=(0, 10))
        for item in unregistered:
            fn = item["setup_file_no"]
            tree.insert("", tk.END, values=(fn, ", ".join(lines_by_file.get(fn, []))))

        def _export():
            save_path = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                initialfile="production_side_master_unregistered.csv",
                parent=win,
            )
            if not save_path:
                return
            try:
                with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.writer(f)
                    writer.writerow(CSV_COLUMNS)
                    i = 1
                    for item in unregistered:
                        fn = item["setup_file_no"]
                        for ml in lines_by_file.get(fn, [fn]) if lines_by_file.get(fn) else [""]:
                            writer.writerow([i, "", "", fn, ml, "", "", "", "", ""])
                            i += 1
            except OSError as e:
                messagebox.showerror("エラー", f"CSV出力中にエラーが発生しました：\n{e}", parent=win)
                return
            messagebox.showinfo("完了", f"未登録のファイルNo一覧をCSVへ出力しました：\n{save_path}", parent=win)

        btn_frame = ttk.Frame(win, padding=(10, 0, 10, 10))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="CSV出力", command=_export).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="閉じる", command=win.destroy).pack(side=tk.RIGHT)

    def on_show_missing_lines(self):
        """
        参考情報専用（判定には使わない、2026-10-08新設、D-9x改訂）：ファイルNo
        自体は生産面マスターに登録があるが、計画データ上のこの実装ラインには
        登録が無い組み合わせを一覧表示する。
        """
        missing = find_registered_file_nos_missing_line_combinations()
        win = tk.Toplevel(self)
        win.title("生産面マスタ：実装ライン単位の未登録組み合わせ（参考情報）")
        win.geometry("520x420")
        center_window(win, self)

        ttk.Label(
            win, padding=10,
            text=(
                f"ファイルNo自体は登録済みだが、計画データ上のこの実装ラインには"
                f"登録が無い組：{len(missing)}件\n"
                "（参考情報です。ファイルNo単位の判定（片面の製品/面2待ち/生産面マスター未登録）には使いません。）"
            ),
            wraplength=480, justify=tk.LEFT,
        ).pack(fill=tk.X)

        cols = ("setup_file_no", "mounting_line")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("setup_file_no", text="セットアップファイルNo")
        tree.heading("mounting_line", text="実装ライン")
        tree.column("setup_file_no", width=200, anchor=tk.W)
        tree.column("mounting_line", width=160, anchor=tk.W)
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(expand=True, fill=tk.BOTH, padx=10, pady=(0, 10))
        for item in missing:
            tree.insert("", tk.END, values=(item["setup_file_no"], item["mounting_line"]))

        ttk.Button(win, text="閉じる", command=win.destroy).pack(pady=(0, 10))
