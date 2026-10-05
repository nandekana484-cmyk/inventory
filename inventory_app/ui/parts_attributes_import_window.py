# ui/parts_attributes_import_window.py
import csv
import threading
import queue
import unicodedata

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from models.parts_attributes import (
    list_parts_attributes, get_parts_attributes_count,
    compute_parts_attributes_sync_plan, apply_parts_attributes_sync,
)
from models.operation_log import log_operation
from ui.loading_window import LoadingWindow
from ui.warnings_list_window import WarningsListWindow
from ui.window_utils import center_window

# エンコーディング自動判定の候補（この順で試す）
_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp932"]

# 部品属性CSVの列名（拡張ポイント）：既存データ構成の多数列のうち、
# 新BOM計算（丁取り数統合）に必要な5列のみを抽出する。
COL_PART_NO = "96コード"
COL_TEITORI = "丁取り数"
COL_PART_TYPE = "部品種別"
COL_SUPPLY_TYPE = "部品支給区分"
COL_FULL_QTY = "フル数量"

# 確認ダイアログ・完了メッセージで「先頭○件」として表示する最大件数
_PREVIEW_HEAD_COUNT = 10

# 一覧の列見出し・列ごとの比較方法（検索・ソート用）
_COLUMN_LABELS = {
    "part_no": "96コード", "teitori": "丁取り数", "part_type": "部品種別",
    "supply_type": "部品支給区分", "full_qty": "フル数量",
}
_NUMERIC_COLUMNS = {"teitori", "full_qty"}
_SEARCHABLE_COLUMNS = ("part_no", "teitori", "part_type", "supply_type", "full_qty")
_KEY_COLUMN = "part_no"


def _normalize_for_search(text) -> str:
    """
    検索語・検索対象値の表記ゆれ（全角/半角、大文字/小文字、前後空白）を吸収する。
    ui/board_structure_import_window.py::_normalize_for_search()と同一実装
    （UI層からmodels層への依存を作らないための複製、同ファイルのコメント参照）。
    """
    if text is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    return normalized.lower().strip()


def _cell_display_text(row, col):
    """Treeviewに表示する文字列と同じ形式で、検索対象の値を文字列化する。"""
    value = row.get(col)
    return "" if value is None else str(value)


def _open_csv_with_fallback(file_path):
    """utf-8-sig → utf-8 → cp932 の順でエンコーディングを判定して開く。"""
    last_error = None
    for encoding in _ENCODINGS_TO_TRY:
        try:
            f = open(file_path, mode="r", encoding=encoding, newline="")
            f.read(2048)
            f.seek(0)
            return f
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError(f"CSVの文字コードを判定できませんでした: {last_error}")


def _parse_parts_attributes_csv(file_path):
    """
    部品属性TSV（96コード・丁取り数・部品種別・部品支給区分・フル数量ほか
    多数列を含む既存フォーマット。タブ区切り、実ファイルは.tsv）を解析する
    （DBへの書き込みは一切行わない。取込前の確認ダイアログ用の差分計算まで行う）。

    必須列：96コード（欠けている・空の行は警告してスキップ）
    任意列：丁取り数・部品種別・部品支給区分・フル数量
      （丁取り数・フル数量が数値変換できない場合は警告のうえNoneのまま保存する）

    CSV内の重複キー（同じ96コードの行が複数）：最後に出現した行の値を最終的な
    登録値として採用する（既存のON CONFLICT上書きの挙動と一致させるため）。
    値が全て同じ重複は件数のみを、値が食い違う重複は該当行番号・各値・採用される
    値（最後の行）を区別して記録する（5列すべてが一致する場合のみ「値が同じ」
    とみなす）。

    区切り文字・文字コードの不一致でヘッダーが正しく認識されないと、
    全行が「96コードが空」としてスキップされてしまう（実際に発生した事例）。
    これに気づきやすくするため、96コード空欄によるスキップが読み込み行数の
    9割以上を占める場合は、行単位の警告（warnings）とは別に、ファイル形式の
    確認を促す注意喚起メッセージを notices に追加する（警告件数の集計を
    汚さないため）。

    戻り値：{
        "resolved_rows": [(part_no, teitori, part_type, supply_type, full_qty), ...]
            （重複解決後、1キー1件）,
        "plan": compute_parts_attributes_sync_plan()の戻り値（追加・更新・変更なし・削除）,
        "total_rows": CSVの有効データ行数（ヘッダーを除く全行）,
        "skipped_empty_key_count": 96コードが空でスキップした行数,
        "value_missing_count": 丁取り数またはフル数量が読み取れなかった行数
            （生の行単位。重複キーで最終的に上書きされた行も含めて数える）,
        "duplicates": {
            "same_value": [{"part_no":, "count":, "rows": [row_no,...]}],
            "diff_value": [{"part_no":, "occurrences": [{"row_no":, "value":}],
                             "final_value":}],
        },
        "notices": [...],
        "warnings": [...]（行単位のみ。96コード空欄スキップ・数値変換失敗）,
        "abort_reason": str または None（有効な96コードを含む行が1件も無い場合の中断理由）,
    }
    """
    warnings = []
    notices = []

    with _open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        return {
            "resolved_rows": [], "plan": None, "total_rows": 0,
            "skipped_empty_key_count": 0, "value_missing_count": 0,
            "duplicates": {"same_value": [], "diff_value": []},
            "notices": [], "warnings": [],
            "abort_reason": "CSVにデータ行が1件も無いため、インポートを中断しました（削除も行っていません）。",
        }

    skipped_count = 0
    value_missing_count = 0
    # part_no -> [(row_no, (teitori, part_type, supply_type, full_qty)), ...]
    occurrences_by_part_no = {}
    order = []

    for i, row in enumerate(rows, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
        part_no = (row.get(COL_PART_NO) or "").strip()
        if not part_no:
            warnings.append(f"{i}行目: {COL_PART_NO}が空のためスキップしました。")
            skipped_count += 1
            continue

        row_value_missing = False

        teitori_raw = (row.get(COL_TEITORI) or "").strip()
        teitori = None
        if teitori_raw:
            try:
                teitori = int(float(teitori_raw))
            except ValueError:
                warnings.append(
                    f"{i}行目: {COL_TEITORI}「{teitori_raw}」を数値に変換できないため未設定のまま保存しました。"
                )
                row_value_missing = True

        part_type = (row.get(COL_PART_TYPE) or "").strip() or None
        supply_type = (row.get(COL_SUPPLY_TYPE) or "").strip() or None

        full_qty_raw = (row.get(COL_FULL_QTY) or "").strip()
        full_qty = None
        if full_qty_raw:
            try:
                full_qty = int(float(full_qty_raw))
            except ValueError:
                warnings.append(
                    f"{i}行目: {COL_FULL_QTY}「{full_qty_raw}」を数値に変換できないため未設定のまま保存しました。"
                )
                row_value_missing = True

        if row_value_missing:
            value_missing_count += 1

        value = (teitori, part_type, supply_type, full_qty)
        if part_no not in occurrences_by_part_no:
            occurrences_by_part_no[part_no] = []
            order.append(part_no)
        occurrences_by_part_no[part_no].append((i, value))

    total_rows = len(rows)
    if total_rows > 0 and skipped_count / total_rows >= 0.9:
        notices.insert(
            0,
            f"※ 読み込んだ{total_rows}行中{skipped_count}行"
            f"（{skipped_count / total_rows * 100:.0f}%）が{COL_PART_NO}空欄でスキップされました。"
            "区切り文字（タブ/カンマ）や文字コードがファイルの実際の形式と"
            "一致していない可能性があります。ファイル形式をご確認ください。",
        )

    if not order:
        return {
            "resolved_rows": [], "plan": None, "total_rows": total_rows,
            "skipped_empty_key_count": skipped_count, "value_missing_count": value_missing_count,
            "duplicates": {"same_value": [], "diff_value": []},
            "notices": notices, "warnings": warnings,
            "abort_reason": "有効な96コードを含む行が1件も無かったため、削除は行っていません。",
        }

    same_value_dups = []
    diff_value_dups = []
    resolved_rows = []
    for part_no in order:
        occurrences = occurrences_by_part_no[part_no]
        final_value = occurrences[-1][1]
        resolved_rows.append((part_no,) + final_value)
        if len(occurrences) > 1:
            distinct_values = {value for _row_no, value in occurrences}
            if len(distinct_values) == 1:
                same_value_dups.append({
                    "part_no": part_no, "count": len(occurrences),
                    "rows": [row_no for row_no, _v in occurrences],
                })
            else:
                diff_value_dups.append({
                    "part_no": part_no,
                    "occurrences": [{"row_no": row_no, "value": value} for row_no, value in occurrences],
                    "final_value": final_value,
                })

    plan = compute_parts_attributes_sync_plan(resolved_rows)

    return {
        "resolved_rows": resolved_rows, "plan": plan, "total_rows": total_rows,
        "skipped_empty_key_count": skipped_count, "value_missing_count": value_missing_count,
        "duplicates": {"same_value": same_value_dups, "diff_value": diff_value_dups},
        "notices": notices, "warnings": warnings, "abort_reason": None,
    }


def _format_value_tuple(value):
    teitori, part_type, supply_type, full_qty = value
    parts = [
        f"丁取り数={teitori if teitori is not None else ''}",
        f"部品種別={part_type or ''}",
        f"部品支給区分={supply_type or ''}",
        f"フル数量={full_qty if full_qty is not None else ''}",
    ]
    return "/".join(parts)


def _build_confirmation_message(parse_result):
    """取込前の確認ダイアログの本文を組み立てる。"""
    plan = parse_result["plan"]
    dup = parse_result["duplicates"]

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
            lines.append(f"　・{item['part_no']}（現在の値：{_format_value_tuple(item['value'])}）")
        remaining = len(plan["to_delete"]) - len(head)
        if remaining > 0:
            lines.append(f"　　...ほか{remaining}件")

    if dup["same_value"]:
        total_dup_rows = sum(d["count"] for d in dup["same_value"])
        lines.append("")
        lines.append(
            f"※ CSV内に同じ96コードが複数回登場する行が{len(dup['same_value'])}件"
            f"（合計{total_dup_rows}行）ありますが、値はいずれも同じでした。"
        )

    if dup["diff_value"]:
        lines.append("")
        lines.append(f"※ CSV内に同じ96コードで値が食い違う重複が{len(dup['diff_value'])}件あります：")
        for d in dup["diff_value"][:_PREVIEW_HEAD_COUNT]:
            occ_text = " / ".join(
                f"{o['row_no']}行目=({_format_value_tuple(o['value'])})" for o in d["occurrences"]
            )
            lines.append(
                f"　・{d['part_no']}：{occ_text}　→　採用値：({_format_value_tuple(d['final_value'])})（最後の行）"
            )
        remaining = len(dup["diff_value"]) - _PREVIEW_HEAD_COUNT
        if remaining > 0:
            lines.append(f"　　...ほか{remaining}件")

    lines.append("")
    lines.append("この内容で取込を実行しますか？")

    return "\n".join(lines)


def _build_completion_message(result, total_count):
    plan = result["plan"]
    msg = (
        f"読み込んだ行数：{result['total_rows']}行\n"
        f"追加：{len(plan['to_add'])}件 / 更新：{len(plan['to_update'])}件 / "
        f"変更なし：{len(plan['unchanged'])}件 / 削除：{len(plan['to_delete'])}件\n"
        f"登録されなかった行数（{COL_PART_NO}が空欄）：{result['skipped_empty_key_count']}件\n"
        f"値が読み取れず空で登録した件数：{result['value_missing_count']}件\n"
        f"取込後の登録件数：{total_count}件"
    )

    notices = result.get("notices") or []
    if notices:
        msg += "\n\n【ファイル形式についての注意】\n" + "\n".join(notices)

    warnings = result.get("warnings") or []
    if warnings:
        msg += f"\n\n警告：{len(warnings)}件（詳細は別ウィンドウで確認できます）"

    return msg


class PartsAttributesImportWindow(tk.Toplevel):
    """
    部品属性マスタ（丁取り数等）をCSVからインポートする画面。

    新BOM計算ロジック（services.bom_service.BOMService._calculate_bom）で、
    BOM TSVの係数が0かつRフラグがある行の qty 計算（部品員数 ÷ 丁取り数）に使われる。

    取込は「CSVをマスタとした差分同期」（CSVに無い既存データは削除）のため、
    実行前に内訳（追加・更新・変更なし・削除）を確認ダイアログで示し、「いいえ」
    を選べば何も変更しない2段階方式にしている（ui/board_structure_import_window.py
    と同じ設計、CANONICAL_DESIGN_DECISIONS.md D-6x参照）。
    """
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker or {}
        self.selected_csv_path = None
        self._pending_parse_result = None

        # self._all_rows：DBから読み込んだ全件（検索・ソートの対象元）。
        # ui/board_structure_import_window.py::BoardStructureImportWindowと同じ
        # 設計（検索・ソートは表示専用で取込には影響しない）。
        self._all_rows = []
        self._sort_column = _KEY_COLUMN
        self._sort_ascending = True

        self.title("部品属性（丁取り数）インポート")
        self.geometry("700x600")
        center_window(self, parent)

        select_frame = ttk.Frame(self, padding=10)
        select_frame.pack(fill=tk.X)

        ttk.Button(select_frame, text="CSV選択", command=self.on_select_csv).pack(side=tk.LEFT, padx=5)
        self.lbl_csv_path = ttk.Label(select_frame, text="（未選択）", foreground="blue")
        self.lbl_csv_path.pack(side=tk.LEFT, padx=5)

        self.btn_import = ttk.Button(select_frame, text="インポート実行", command=self.on_import_execute)
        self.btn_import.pack(side=tk.LEFT, padx=15)

        filter_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        filter_frame.pack(fill=tk.X)
        ttk.Label(filter_frame, text="検索：").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(filter_frame, textvariable=self.search_var, width=28).pack(side=tk.LEFT, padx=(5, 5))
        self.search_var.trace_add("write", lambda *_args: self._apply_filter_and_render())
        ttk.Button(filter_frame, text="クリア", command=self.on_clear_search).pack(side=tk.LEFT)

        count_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        count_frame.pack(fill=tk.X)
        self.lbl_count = ttk.Label(count_frame, text="登録件数: -件")
        self.lbl_count.pack(side=tk.LEFT)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.lbl_empty = ttk.Label(
            tree_frame, text="検索条件に一致するデータがありません。", foreground="#666666",
        )

        cols = ("part_no", "teitori", "part_type", "supply_type", "full_qty")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for col in cols:
            self.tree.heading(col, text=_COLUMN_LABELS[col], command=lambda c=col: self.sort_by_column(c))
        self.tree.column("part_no", width=180, anchor=tk.W)
        self.tree.column("teitori", width=90, anchor=tk.E)
        self.tree.column("part_type", width=100, anchor=tk.W)
        self.tree.column("supply_type", width=120, anchor=tk.W)
        self.tree.column("full_qty", width=100, anchor=tk.E)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self._update_column_headers()
        self.load_parts_attributes()

    def load_parts_attributes(self):
        """
        DBから全件を再取得し、現在の検索語・ソート状態を保ったまま一覧を
        更新する（取込完了後の再読込もこの関数を使うため、検索語・ソートが
        クリアされることはない）。
        """
        self._all_rows = list_parts_attributes()
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
            if needle_normalized in _normalize_for_search(_cell_display_text(row, col)):
                return True
        return False

    def _is_empty_value(self, row, col):
        value = row.get(col)
        if col in _NUMERIC_COLUMNS:
            return value is None
        return not value

    def _apply_filter_and_render(self):
        needle = _normalize_for_search(self.search_var.get())

        filtered = [row for row in self._all_rows if self._row_matches_search(row, needle)]

        col = self._sort_column
        is_numeric = col in _NUMERIC_COLUMNS
        with_value = [row for row in filtered if not self._is_empty_value(row, col)]
        without_value = [row for row in filtered if self._is_empty_value(row, col)]
        if is_numeric:
            with_value.sort(key=lambda r: float(r[col]), reverse=not self._sort_ascending)
        else:
            with_value.sort(key=lambda r: str(r.get(col) or ""), reverse=not self._sort_ascending)
        # 値が空のデータは、昇順・降順どちらでも常に末尾にまとめる
        # （ui/board_structure_import_window.py::_apply_filter_and_render()と同じ理由）。
        ordered = with_value + without_value

        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in ordered:
            self.tree.insert("", tk.END, values=(
                row["part_no"],
                row.get("teitori") if row.get("teitori") is not None else "",
                row.get("part_type") or "",
                row.get("supply_type") or "",
                row.get("full_qty") if row.get("full_qty") is not None else "",
            ))

        if ordered:
            self.lbl_empty.pack_forget()
        else:
            self.lbl_empty.pack(fill=tk.X, pady=(0, 5), before=self.tree)

        self._update_count_label(len(ordered), bool(needle))

    def _update_count_label(self, displayed_count=None, filter_active=False):
        total = get_parts_attributes_count()
        base = f"登録件数: {total}件"
        if filter_active and displayed_count is not None:
            base = f"表示: {displayed_count}件 / " + base
        self.lbl_count.config(text=base)

    def on_select_csv(self):
        file_path = filedialog.askopenfilename(
            filetypes=[
                ("TSV files", "*.tsv"),
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
            parent=self.winfo_toplevel(),
        )
        if not file_path:
            return
        self.selected_csv_path = file_path
        self.lbl_csv_path.config(text=file_path)

    def on_import_execute(self):
        """
        インポートは2段階で実行する：
        ①CSV解析＋差分計算（_parse_parts_attributes_csv()、DB書き込みなし）を
          別スレッドで実行し、結果を確認ダイアログで提示する。
        ②利用者が確認ダイアログで「はい」を選んだ場合のみ、_apply_import()で
          実際の登録・更新・削除を1トランザクションで確定する（別スレッド）。
        いずれの段階も、ui.kitting_plan_import.KittingPlanImportWindow.
        on_start_import()で確立済みのLoadingWindow＋threading.Thread(daemon=True)＋
        queue.Queue＋self.after(200,...)ポーリングパターンを踏襲する。
        """
        if not self.selected_csv_path:
            messagebox.showwarning("警告", "CSVファイルを選択してください。", parent=self.winfo_toplevel())
            return

        self.btn_import.config(state=tk.DISABLED)
        loading = LoadingWindow(self, message="CSVを読み込んでいます…")
        result_queue = queue.Queue()
        file_path = self.selected_csv_path

        def _work():
            try:
                result_queue.put((True, _parse_parts_attributes_csv(file_path)))
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
                if isinstance(payload, ValueError):
                    messagebox.showerror(
                        "エラー", f"部品属性CSV取込中にエラーが発生しました：\n{payload}\n"
                        "データは取込前の状態のままです。", parent=self.winfo_toplevel(),
                    )
                else:
                    messagebox.showerror(
                        "エラー", f"部品属性CSV取込中に予期しないエラーが発生しました：\n{payload}\n"
                        "データは取込前の状態のままです。", parent=self.winfo_toplevel(),
                    )
                return

            parse_result = payload
            if parse_result["abort_reason"]:
                self.btn_import.config(state=tk.NORMAL)
                messagebox.showwarning("警告", parse_result["abort_reason"], parent=self.winfo_toplevel())
                return

            self._pending_parse_result = parse_result
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
        loading = LoadingWindow(self, message="部品属性CSVを取り込んでいます…")
        result_queue = queue.Queue()
        resolved_rows = parse_result["resolved_rows"]

        def _work():
            try:
                applied_plan = apply_parts_attributes_sync(resolved_rows)
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
                    "エラー", f"部品属性CSV取込中に予期しないエラーが発生しました：\n{payload}\n"
                    "データは取込前の状態のままです（登録・更新・削除は1つのトランザクションのため、"
                    "一部だけ反映されることはありません）。", parent=self.winfo_toplevel(),
                )
                return

            applied_plan = payload
            self.load_parts_attributes()
            total_count = get_parts_attributes_count()

            result = {**parse_result, "plan": applied_plan}

            log_operation(
                self.current_worker.get("name", "unknown"),
                "部品属性インポート",
                detail=(
                    f"追加{len(applied_plan['to_add'])}件 更新{len(applied_plan['to_update'])}件 "
                    f"変更なし{len(applied_plan['unchanged'])}件 削除{len(applied_plan['to_delete'])}件"
                ),
            )

            messagebox.showinfo(
                "部品属性CSV取込結果", _build_completion_message(result, total_count),
                parent=self.winfo_toplevel(),
            )

            warnings = parse_result.get("warnings") or []
            if warnings:
                WarningsListWindow(self, "部品属性CSV取込：警告一覧", warnings)

        self.after(200, _poll)
