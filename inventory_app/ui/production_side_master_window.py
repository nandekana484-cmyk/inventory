# ui/production_side_master_window.py
"""
生産面マスタの画面（2026-10-07新設）。構成基板数マスター・基板丁数マスター
（ui/board_structure_import_window.py・ui/parts_attributes_import_window.py）
と同じ「CSVをマスタとした差分同期」方式のCSV取込・出力に加え、画面上での
直接編集（先行面・後行面の有無の切替、行の追加・削除）を提供する。

一覧の1行＝(setup_file_no, mounting_line)。保存先（models.production_side_master）
は(setup_file_no, mounting_line, production_side)単位で持つため、画面編集時は
「元の状態」と「現在の状態」を行ごとに比較し、変化した面（先行面・後行面）だけを
upsert/deleteする（CSV取込のような全件差分同期をここで使うと、画面編集では
持っていない補助列（ボンド打ちフラグ等）がNoneで上書きされてしまうため、
あえて使わない）。
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
    list_production_side_rows_raw,
    SIDE_LABEL_TO_VALUE, SIDE_VALUE_TO_LABEL,
)
from models.operation_log import log_operation
from models.kitting_plan import find_master_plan_discrepancies, find_unregistered_production_side_combinations
from services.csv_parsing_common import _open_csv_with_fallback
from ui.loading_window import LoadingWindow
from ui.warnings_list_window import WarningsListWindow
from ui.window_utils import center_window

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
    "has_side1": "先行面", "has_side2": "後行面",
}
_SEARCHABLE_COLUMNS = ("setup_file_no", "mounting_line")
_KEY_COLUMN = "setup_file_no"

CHECK_MARK = "✓"
NO_MARK = ""


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
         "orig_has_side1", "orig_has_side2"}
    "orig_has_side1"/"orig_has_side2"は読み込み時点（または直前の保存時点）の
    値で、保存時にこれと現在値を比較し、変化した面だけをupsert/deleteする
    （差分だけを個別に反映する方式。CSV取込のような全件差分同期をここで使うと、
    画面編集では持っていない補助列（ボンド打ちフラグ等）がNoneで上書きされて
    しまうため、あえて使わない）。

    self._deleted_groups：画面上で削除した、かつ保存済みDBに存在していた
    (setup_file_no, mounting_line)のリスト（保存時にdelete_production_side_group()
    を呼ぶ対象）。
    """
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker or {}
        self.selected_csv_path = None

        self._rows = []
        self._deleted_groups = []
        self._dirty = False
        self._sort_column = _KEY_COLUMN
        self._sort_ascending = True

        self.title("生産面マスタ")
        self.geometry("640x600")
        center_window(self, parent)

        top_frame = ttk.Frame(self, padding=10)
        top_frame.pack(fill=tk.X)
        ttk.Button(top_frame, text="CSV選択", command=self.on_select_csv).pack(side=tk.LEFT, padx=(0, 5))
        self.lbl_csv_path = ttk.Label(top_frame, text="（未選択）", foreground="blue")
        self.lbl_csv_path.pack(side=tk.LEFT, padx=5)
        self.btn_import = ttk.Button(top_frame, text="CSV取込", command=self.on_import_execute)
        self.btn_import.pack(side=tk.LEFT, padx=15)
        ttk.Button(top_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT)

        # 計画データとの食い違い・未登録の組み合わせの確認（2026-10-07新設、
        # D-9x §4参照）。マスターでは後行面なしだが計画データに面2がある場合、
        # 計画データを優先して面2のみ表示する既存方針（D-8）は変えないが、
        # その食い違いの事実は利用者が確認できるようにする。
        check_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        check_frame.pack(fill=tk.X)
        ttk.Button(
            check_frame, text="計画との食い違いを確認", command=self.on_show_discrepancies,
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(
            check_frame, text="マスター未登録の組み合わせを確認", command=self.on_show_unregistered,
        ).pack(side=tk.LEFT)

        filter_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        filter_frame.pack(fill=tk.X)
        ttk.Label(filter_frame, text="検索：").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.search_var, width=24).pack(side=tk.LEFT, padx=(5, 5))
        self.search_var.trace_add("write", lambda *_args: self._apply_filter_and_render())
        ttk.Button(filter_frame, text="クリア", command=self.on_clear_search).pack(side=tk.LEFT)

        count_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        count_frame.pack(fill=tk.X)
        self.lbl_count = ttk.Label(count_frame, text="登録件数: -組")
        self.lbl_count.pack(side=tk.LEFT)

        edit_frame = ttk.Frame(self, padding=(10, 5, 10, 0))
        edit_frame.pack(fill=tk.X)
        ttk.Label(edit_frame, text="セットアップファイルNo：").pack(side=tk.LEFT)
        self.entry_new_file_no = ttk.Entry(edit_frame, width=10)
        self.entry_new_file_no.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(edit_frame, text="実装ライン：").pack(side=tk.LEFT)
        self.entry_new_mounting_line = ttk.Entry(edit_frame, width=6)
        self.entry_new_mounting_line.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Button(edit_frame, text="行を追加", command=self.on_add_row).pack(side=tk.LEFT)
        ttk.Button(edit_frame, text="選択行を削除", command=self.on_delete_row).pack(side=tk.LEFT, padx=(5, 0))

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.lbl_empty = ttk.Label(
            tree_frame, text="検索条件に一致するデータがありません。", foreground="#666666",
        )

        cols = ("setup_file_no", "mounting_line", "has_side1", "has_side2")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for col in cols:
            self.tree.heading(col, text=_COLUMN_LABELS[col], command=lambda c=col: self.sort_by_column(c))
        self.tree.column("setup_file_no", width=160, anchor=tk.W)
        self.tree.column("mounting_line", width=100, anchor=tk.W)
        self.tree.column("has_side1", width=90, anchor=tk.CENTER)
        self.tree.column("has_side2", width=90, anchor=tk.CENTER)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Button-1>", self._on_tree_click)

        hint_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        hint_frame.pack(fill=tk.X)
        ttk.Label(
            hint_frame, text="先行面・後行面の列をクリックすると有無を切り替えられます。",
            foreground="gray",
        ).pack(anchor=tk.W)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        self.btn_save = ttk.Button(btn_frame, text="保存", command=self.on_save)
        self.btn_save.pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="閉じる", command=self.on_close).pack(side=tk.RIGHT, padx=5)

        self._update_column_headers()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.load_rows()

    # ------------------------------------------------------------------
    # 一覧の読み込み・表示
    # ------------------------------------------------------------------

    def load_rows(self):
        """DBから全件を再取得し、編集状態を作り直す（未保存の変更は失われる。
        取込・保存の直後のみ呼ぶこと）。"""
        groups = list_production_side_groups()
        self._rows = [
            {**g, "orig_has_side1": g["has_side1"], "orig_has_side2": g["has_side2"]}
            for g in groups
        ]
        self._deleted_groups = []
        self._dirty = False
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

    def _apply_filter_and_render(self):
        needle = _normalize_for_search(self.search_var.get())
        filtered = [row for row in self._rows if self._row_matches_search(row, needle)]

        col = self._sort_column
        if col in ("has_side1", "has_side2"):
            # 昇順＝有り（チェック済み）を先頭にまとめる、降順＝その逆
            filtered.sort(key=lambda r: (not r[col], r["setup_file_no"], r["mounting_line"]))
            if not self._sort_ascending:
                filtered.reverse()
        else:
            filtered.sort(key=lambda r: (str(r.get(col) or "")), reverse=not self._sort_ascending)

        # iidを(setup_file_no, mounting_line)に揃える（クリックハンドラでの照合用）
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in filtered:
            iid = f"{row['setup_file_no']}\x1f{row['mounting_line']}"
            self.tree.insert("", tk.END, iid=iid, values=(
                row["setup_file_no"], row["mounting_line"],
                CHECK_MARK if row["has_side1"] else NO_MARK,
                CHECK_MARK if row["has_side2"] else NO_MARK,
            ))

        if filtered:
            self.lbl_empty.pack_forget()
        else:
            self.lbl_empty.pack(fill=tk.X, pady=(0, 5), before=self.tree)

        self._update_count_label(len(filtered), bool(needle))

    def _update_count_label(self, displayed_count=None, filter_active=False):
        base = f"登録件数: {len(self._rows)}組"
        if filter_active and displayed_count is not None:
            base = f"表示: {displayed_count}組 / " + base
        if self._dirty:
            base += "　【未保存の変更があります】"
        self.lbl_count.config(text=base)

    # ------------------------------------------------------------------
    # 編集（先行面/後行面の切替・行の追加・削除）
    # ------------------------------------------------------------------

    def _find_row(self, setup_file_no, mounting_line):
        for row in self._rows:
            if row["setup_file_no"] == setup_file_no and row["mounting_line"] == mounting_line:
                return row
        return None

    def _on_tree_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id or not column_id:
            return

        col_pos = int(column_id.replace("#", "")) - 1
        cols = ("setup_file_no", "mounting_line", "has_side1", "has_side2")
        if col_pos < 0 or col_pos >= len(cols):
            return
        col_key = cols[col_pos]
        if col_key not in ("has_side1", "has_side2"):
            return

        setup_file_no, mounting_line = row_id.split("\x1f")
        row = self._find_row(setup_file_no, mounting_line)
        if row is None:
            return
        row[col_key] = not row[col_key]
        self._dirty = True
        self._apply_filter_and_render()
        self.tree.selection_set(row_id)

    def on_add_row(self):
        setup_file_no_raw = self.entry_new_file_no.get().strip()
        mounting_line_raw = self.entry_new_mounting_line.get().strip()
        if not setup_file_no_raw or not mounting_line_raw:
            messagebox.showwarning(
                "警告", "セットアップファイルNo・実装ラインの両方を入力してください。", parent=self.winfo_toplevel(),
            )
            return

        norm_file = normalize_setup_file_no(setup_file_no_raw)
        norm_line = normalize_mounting_line(mounting_line_raw)

        if self._find_row(norm_file, norm_line) is not None:
            messagebox.showwarning(
                "警告",
                f"セットアップファイルNo「{norm_file}」実装ライン「{norm_line}」は既に登録されています。",
                parent=self.winfo_toplevel(),
            )
            return

        self._rows.append({
            "setup_file_no": norm_file, "mounting_line": norm_line,
            "has_side1": False, "has_side2": False,
            "orig_has_side1": False, "orig_has_side2": False,
        })
        self._dirty = True
        self.entry_new_file_no.delete(0, tk.END)
        self.entry_new_mounting_line.delete(0, tk.END)
        self._apply_filter_and_render()

    def on_delete_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("警告", "削除する行を一覧から選択してください。", parent=self.winfo_toplevel())
            return
        setup_file_no, mounting_line = sel[0].split("\x1f")
        row = self._find_row(setup_file_no, mounting_line)
        if row is None:
            return
        if not messagebox.askyesno(
            "確認", f"セットアップファイルNo「{setup_file_no}」実装ライン「{mounting_line}」を削除します。よろしいですか？",
            parent=self.winfo_toplevel(),
        ):
            return

        if row["orig_has_side1"] or row["orig_has_side2"]:
            self._deleted_groups.append((setup_file_no, mounting_line))
        self._rows.remove(row)
        self._dirty = True
        self._apply_filter_and_render()

    def on_save(self):
        if not self._dirty:
            messagebox.showinfo("保存", "未保存の変更はありません。", parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno("確認", "変更を保存します。よろしいですか？", parent=self.winfo_toplevel()):
            return

        changed_count = 0
        for row in self._rows:
            fn, ml = row["setup_file_no"], row["mounting_line"]
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

        deleted_group_count = len(self._deleted_groups)
        for fn, ml in self._deleted_groups:
            delete_production_side_group(fn, ml)

        log_operation(
            self.current_worker.get("name", "unknown"),
            "生産面マスタ編集",
            detail=f"面の変更{changed_count}件 / 組の削除{deleted_group_count}件",
        )

        messagebox.showinfo("完了", "生産面マスタを保存しました。", parent=self.winfo_toplevel())
        self.load_rows()

    def on_close(self):
        if self._dirty:
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
        if self._dirty:
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

        画面でまだ保存していない変更（追加した行・切替中の状態）は出力に含めない
        （DBに保存済みの内容を出力する。取込と同じ理由で保存済みのデータが
        正である設計に揃える）。保存していない変更がある場合は出力前に確認する。
        """
        if self._dirty:
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
    # 計画データとの食い違い・未登録の組み合わせの確認
    # ------------------------------------------------------------------

    def on_show_discrepancies(self):
        """
        「マスターでは後行面なしだが、計画データには面2の計画が存在する」組を
        一覧表示する（2026-10-07新設、D-9x §4参照）。計画データを優先する
        既存方針（D-8）は変更せず、食い違いの事実を確認できるのみ。
        """
        discrepancies = find_master_plan_discrepancies()
        win = tk.Toplevel(self)
        win.title("生産面マスタ：計画との食い違い一覧")
        win.geometry("620x420")
        center_window(win, self)

        ttk.Label(
            win, padding=10,
            text=(
                f"マスターでは「後行面なし」と登録されているが、計画データには"
                f"面2の計画が存在する組：{len(discrepancies)}件\n"
                "（計画データを優先し、一覧・候補は従来どおり面2のみ表示しています。"
                "マスターの登録内容が古い可能性があります。）"
            ),
            wraplength=600, justify=tk.LEFT,
        ).pack(fill=tk.X)

        cols = ("setup_file_no", "mounting_line", "lot_nos")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("setup_file_no", text="セットアップファイルNo")
        tree.heading("mounting_line", text="実装ライン")
        tree.heading("lot_nos", text="面2の計画があるロットNo")
        tree.column("setup_file_no", width=140, anchor=tk.W)
        tree.column("mounting_line", width=90, anchor=tk.W)
        tree.column("lot_nos", width=340, anchor=tk.W)
        vsb = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(expand=True, fill=tk.BOTH, padx=10, pady=(0, 10))
        for item in discrepancies:
            tree.insert("", tk.END, values=(
                item["setup_file_no"], item["mounting_line"], ", ".join(item["lot_nos"]),
            ))

        ttk.Button(win, text="閉じる", command=win.destroy).pack(pady=(0, 10))

    def on_show_unregistered(self):
        """
        計画データに存在するが、生産面マスターに全く登録が無い
        (setup_file_no, mounting_line)の組を一覧表示する（2026-10-07新設、
        D-9x §4参照）。生産面マスターへの登録に使えるよう、取込と同じ列構成で
        CSV出力できる（生産面列は空のまま出力し、利用者が先行面/後行面を
        判断して埋めてから取り込み直す想定。生産面は必須項目のため、空の
        ままでは通常の取込で警告の上スキップされる＝誤って登録されない）。
        """
        unregistered = find_unregistered_production_side_combinations()
        win = tk.Toplevel(self)
        win.title("生産面マスタ：未登録の組み合わせ一覧")
        win.geometry("480x420")
        center_window(win, self)

        ttk.Label(
            win, padding=10,
            text=f"計画データに存在するが、生産面マスターに登録が無い組：{len(unregistered)}件",
            wraplength=440, justify=tk.LEFT,
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
        for item in unregistered:
            tree.insert("", tk.END, values=(item["setup_file_no"], item["mounting_line"]))

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
                    for i, item in enumerate(unregistered, start=1):
                        writer.writerow([
                            i, "", "", item["setup_file_no"], item["mounting_line"],
                            "", "", "", "", "",
                        ])
            except OSError as e:
                messagebox.showerror("エラー", f"CSV出力中にエラーが発生しました：\n{e}", parent=win)
                return
            messagebox.showinfo("完了", f"未登録の組み合わせをCSVへ出力しました：\n{save_path}", parent=win)

        btn_frame = ttk.Frame(win, padding=(10, 0, 10, 10))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="CSV出力", command=_export).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="閉じる", command=win.destroy).pack(side=tk.RIGHT)
