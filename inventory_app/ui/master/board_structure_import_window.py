# ui/master/board_structure_import_window.py
import threading
import queue
import unicodedata

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from models.board_structure_master import (
    list_board_structure, get_board_structure_count_summary,
    compute_board_structure_sync_plan, apply_board_structure_sync,  # noqa: F401 （compute_* は re-export）
)
from services.board_structure_csv_service import (  # noqa: F401 （re-export）
    _ENCODINGS_TO_TRY, COL_BOARD_NAME, COL_BOARD_COUNT, COL_BOARD_COUNT_CANDIDATES, _PREVIEW_HEAD_COUNT,
    open_csv_with_fallback as _open_csv_with_fallback,
    parse_board_structure_csv as _parse_board_structure_csv,
    _format_value,
    build_confirmation_message as _build_confirmation_message,
    build_completion_message as _build_completion_message,
)
from models.operation_log import log_operation
from ui.loading_window import LoadingWindow
from ui.system.warnings_list_window import WarningsListWindow
from ui.common.window_utils import center_window

# 一覧の列見出し・列ごとの比較方法（検索・ソート用）
_COLUMN_LABELS = {"board_name": "基板名", "board_count": "構成基板数"}
_NUMERIC_COLUMNS = {"board_count"}
_SEARCHABLE_COLUMNS = ("board_name", "board_count")
_KEY_COLUMN = "board_name"


def _normalize_for_search(text) -> str:
    """
    検索語・検索対象値の表記ゆれ（全角/半角、大文字/小文字、前後空白）を吸収する。
    models.board_structure_master.normalize_board_name()と同じロジックだが、
    UI層からmodels層への依存を作らないため複製している（同モジュールの
    docstring参照：「4行程度の純粋関数のため、共通化による依存関係の複雑化
    よりも複製の方が実装・保守ともに単純」という既存の判断を踏襲）。
    """
    if text is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(text))
    return normalized.lower().strip()


def _cell_display_text(row, col):
    """Treeviewに表示する文字列と同じ形式で、検索対象の値を文字列化する。"""
    value = row.get(col)
    return "" if value is None else str(value)


class BoardStructureImportWindow(tk.Toplevel):
    """
    構成基板数マスタをCSVからインポートする画面。

    生産実績入力画面（ui/kitting/kitting_production_entry.py）の計画情報欄「構成基板数」
    表示で、plan["board_name"]をキーに参照される。

    取込は「CSVをマスタとした差分同期」（CSVに無い既存データは削除）のため、
    実行前に内訳（追加・更新・変更なし・削除）を確認ダイアログで示し、「いいえ」
    を選べば何も変更しない2段階方式にしている（CANONICAL_DESIGN_DECISIONS.md
    D-6x参照）。
    """
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker or {}
        self.selected_csv_path = None
        self._pending_parse_result = None

        # self._all_rows：DBから読み込んだ全件（検索・ソートの対象元）。
        # Treeviewには、ここから検索・絞り込みチェックボックス・ソートを適用した
        # 結果のみを表示する（取込処理・差分計算は常にこのキャッシュを経由せず
        # DBへ直接アクセスするため、検索・ソートは表示専用で取込には影響しない）。
        self._all_rows = []
        self._sort_column = _KEY_COLUMN
        self._sort_ascending = True

        self.title("構成基板数マスタインポート")
        self.geometry("560x560")
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
        self.missing_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filter_frame, text="構成基板数なしのみ表示", variable=self.missing_only_var,
            command=self._apply_filter_and_render,
        ).pack(side=tk.LEFT, padx=(15, 0))

        count_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        count_frame.pack(fill=tk.X)
        self.lbl_count = ttk.Label(count_frame, text="登録件数: -件")
        self.lbl_count.pack(side=tk.LEFT)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.lbl_empty = ttk.Label(
            tree_frame, text="検索条件に一致するデータがありません。", foreground="#666666",
        )

        cols = ("board_name", "board_count")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for col in cols:
            self.tree.heading(col, text=_COLUMN_LABELS[col], command=lambda c=col: self.sort_by_column(c))
        self.tree.column("board_name", width=280, anchor=tk.W)
        self.tree.column("board_count", width=120, anchor=tk.E)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self._update_column_headers()
        self.load_board_structure()

    def load_board_structure(self):
        """
        DBから全件を再取得し、現在の検索語・チェックボックス・ソート状態を
        保ったまま一覧を更新する（取込完了後の再読込もこの関数を使うため、
        検索語・ソートがクリアされることはない）。
        """
        self._all_rows = list_board_structure()
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

    def _apply_filter_and_render(self):
        needle = _normalize_for_search(self.search_var.get())
        missing_only = self.missing_only_var.get()

        filtered = [
            row for row in self._all_rows
            if self._row_matches_search(row, needle)
            and (not missing_only or row.get("board_count") is None)
        ]

        col = self._sort_column
        is_numeric = col in _NUMERIC_COLUMNS
        with_value = [row for row in filtered if row.get(col) is not None]
        without_value = [row for row in filtered if row.get(col) is None]
        if is_numeric:
            with_value.sort(key=lambda r: float(r[col]), reverse=not self._sort_ascending)
        else:
            with_value.sort(key=lambda r: str(r.get(col) or ""), reverse=not self._sort_ascending)
        # 値が空のデータは、昇順・降順どちらでも常に末尾にまとめる
        # （LotProgressWindow等の既存ソート実装はfloat("-inf")フォールバックで
        # 空値を扱っており、降順では末尾ではなく先頭に来てしまうため、
        # 今回は意図的にその方式を採らず、空値グループを分離して常に末尾に
        # 連結する方式にした）。
        ordered = with_value + without_value

        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in ordered:
            self.tree.insert("", tk.END, values=(
                row["board_name"],
                row.get("board_count") if row.get("board_count") is not None else "",
            ))

        if ordered:
            self.lbl_empty.pack_forget()
        else:
            self.lbl_empty.pack(fill=tk.X, pady=(0, 5), before=self.tree)

        self._update_count_label(len(ordered), bool(needle) or missing_only)

    def _update_count_label(self, displayed_count=None, filter_active=False):
        summary = get_board_structure_count_summary()
        base = f"登録件数: {summary['total']}件（うち構成基板数なし {summary['board_count_missing']}件）"
        if filter_active and displayed_count is not None:
            base = f"表示: {displayed_count}件 / " + base
        self.lbl_count.config(text=base)

    def on_select_csv(self):
        file_path = filedialog.askopenfilename(
            filetypes=[
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
        ①CSV解析＋差分計算（_parse_board_structure_csv()、DB書き込みなし）を
          別スレッドで実行し、結果を確認ダイアログで提示する。
        ②利用者が確認ダイアログで「はい」を選んだ場合のみ、_apply_import()で
          実際の登録・更新・削除を1トランザクションで確定する（別スレッド）。
        いずれの段階も、ui.kitting.kitting_plan_import.KittingPlanImportWindow.
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
                result_queue.put((True, _parse_board_structure_csv(file_path)))
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
                        "エラー", f"構成基板数CSV取込中にエラーが発生しました：\n{payload}\n"
                        "データは取込前の状態のままです。", parent=self.winfo_toplevel(),
                    )
                else:
                    messagebox.showerror(
                        "エラー", f"構成基板数CSV取込中に予期しないエラーが発生しました：\n{payload}\n"
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
        loading = LoadingWindow(self, message="構成基板数CSVを取り込んでいます…")
        result_queue = queue.Queue()
        resolved_rows = parse_result["resolved_rows"]

        def _work():
            try:
                applied_plan = apply_board_structure_sync(resolved_rows)
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
                    "エラー", f"構成基板数CSV取込中に予期しないエラーが発生しました：\n{payload}\n"
                    "データは取込前の状態のままです（登録・更新・削除は1つのトランザクションのため、"
                    "一部だけ反映されることはありません）。", parent=self.winfo_toplevel(),
                )
                return

            applied_plan = payload
            self.load_board_structure()
            count_summary = get_board_structure_count_summary()

            result = {**parse_result, "plan": applied_plan}

            log_operation(
                self.current_worker.get("name", "unknown"),
                "構成基板数マスタインポート",
                detail=(
                    f"追加{len(applied_plan['to_add'])}件 更新{len(applied_plan['to_update'])}件 "
                    f"変更なし{len(applied_plan['unchanged'])}件 削除{len(applied_plan['to_delete'])}件"
                ),
            )

            messagebox.showinfo(
                "構成基板数CSV取込結果", _build_completion_message(result, count_summary),
                parent=self.winfo_toplevel(),
            )

            warnings = parse_result.get("warnings") or []
            if warnings:
                WarningsListWindow(self, "構成基板数CSV取込：警告一覧", warnings)

        self.after(200, _poll)
