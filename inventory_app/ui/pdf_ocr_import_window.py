# ui/pdf_ocr_import_window.py
import os
import queue
import threading

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from services.pdf_ocr_service import extract_pdf_rows, write_rows_to_csv, match_against_inventory
from ui.loading_window import LoadingWindow

# 信頼度（0〜100）がこの値未満の行は、左ペインで警告色に強調表示する。
# テキスト抽出経路の行は常に100扱いのため、この強調はOCR経路でのみ発生する。
_LOW_CONFIDENCE_THRESHOLD = 70

# セル編集時、空欄のまま確定しようとした場合のEntry背景色（エラー表示）。
# ui.checkable_treeview.CheckableTreeviewの_EDIT_ERROR_BGと同じ配色方針。
_EDIT_ERROR_BG = "#ffb3b3"


class PdfOcrImportWindow(tk.Toplevel):
    """
    PDFファイルを選択→変換（テキスト抽出、またはテキスト層が無い場合はOCR）→
    左ペインに全行プレビュー→「照合・抽出」で右ペインに在庫一致行のみを
    プレビュー→左右それぞれ独立してCSV保存、という一連の流れを提供する画面。

    ui/ng_input_window.py・ui/wip_expansion_window.py と同じ左右ペイン構成
    （左：変換結果、右：一覧・絞り込みに相当する照合結果）を踏襲している。
    ただし本画面は左右とも独立したCSV保存を持つ点が異なる（NG入力画面・
    仕掛展開画面は右ペインが「一覧の絞り込み表示」であり、保存対象は
    常に左ペインの展開結果1つだけだった）。

    列分解（96コード・名称・数量等への分割）は、実際のPDFレイアウトが未確定の
    ため、services.pdf_ocr_service._split_line_to_columns()の素朴な空白区切り
    実装をそのまま反映したプレビューになっている点に注意（列見出しは「列1」
    「列2」…という機械的な仮表示）。

    self.extracted_rows：PDFから抽出した全行（左ペインの表示・保存対象、
    「照合・抽出」の入力データにもなる）。self.confidences：extracted_rowsと
    同じ順序・件数の信頼度リスト（0〜100、OCR経路のみ100未満になり得る）。
    左ペインの「信頼度」列・低信頼度行の強調表示に使う。self.matched_rows：
    「照合・抽出」実行後の一致行のみ（右ペインの表示・保存対象。未実行の
    間はNoneのまま）。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.title("PDF読み取り（在庫照合）")
        self.geometry("1150x600")

        self.selected_pdf_path = None
        self.extracted_rows = None
        self.confidences = None
        self.matched_rows = None
        self._result_queue = queue.Queue()
        self._loading_window = None
        self._edit_entry = None

        self.create_widgets()

    def create_widgets(self):
        select_frame = ttk.Frame(self, padding=10)
        select_frame.pack(fill=tk.X)

        ttk.Button(select_frame, text="PDF選択", command=self.on_select_pdf).pack(side=tk.LEFT, padx=5)
        self.lbl_pdf_path = ttk.Label(select_frame, text="（未選択）", foreground="blue")
        self.lbl_pdf_path.pack(side=tk.LEFT, padx=5)

        self.btn_convert = ttk.Button(
            select_frame, text="変換実行", command=self.on_convert, state=tk.DISABLED,
        )
        self.btn_convert.pack(side=tk.LEFT, padx=15)

        self.btn_match = ttk.Button(
            select_frame, text="照合・抽出", command=self.on_match, state=tk.DISABLED,
        )
        self.btn_match.pack(side=tk.LEFT, padx=5)

        self.lbl_method = ttk.Label(select_frame, text="", foreground="green")
        self.lbl_method.pack(side=tk.LEFT, padx=10)

        container = ttk.Frame(self)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Labelframe(container, text="変換結果（全行）", padding=5)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(15, 5), pady=5)

        self.tree_all = ttk.Treeview(left_frame, show="headings")
        self.tree_all.pack(expand=True, fill=tk.BOTH)
        self.tree_all.bind("<Double-1>", self._on_tree_all_double_click)

        self.btn_save_all = ttk.Button(
            left_frame, text="CSV保存（全行）", command=self.on_save_all_csv, state=tk.DISABLED,
        )
        self.btn_save_all.pack(fill=tk.X, pady=(5, 0))

        right_frame = ttk.Labelframe(container, text="照合・抽出結果（在庫一致行のみ）", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(5, 15), pady=5)

        self.lbl_match_status = ttk.Label(
            right_frame, text="未実行（左側の変換結果に対して「照合・抽出」を実行してください）",
            foreground="gray",
        )
        self.lbl_match_status.pack(anchor=tk.W, pady=(0, 5))

        self.tree_matched = ttk.Treeview(right_frame, show="headings")
        self.tree_matched.pack(expand=True, fill=tk.BOTH)

        self.btn_save_matched = ttk.Button(
            right_frame, text="CSV保存（一致行）", command=self.on_save_matched_csv, state=tk.DISABLED,
        )
        self.btn_save_matched.pack(fill=tk.X, pady=(5, 0))

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

    def on_select_pdf(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")], parent=self.winfo_toplevel(),
        )
        if not file_path:
            return
        self.selected_pdf_path = file_path
        self.lbl_pdf_path.config(text=file_path)
        self.btn_convert.config(state=tk.NORMAL)
        self.btn_match.config(state=tk.DISABLED)
        self.btn_save_all.config(state=tk.DISABLED)
        self.btn_save_matched.config(state=tk.DISABLED)
        self._close_cell_edit_entry()
        self.extracted_rows = None
        self.confidences = None
        self.matched_rows = None
        self.lbl_method.config(text="")
        self._reset_match_status()
        self._clear_tree(self.tree_all)
        self._clear_tree(self.tree_matched)

    def on_convert(self):
        """
        変換処理（テキスト抽出、またはOCR）は数秒〜数十秒かかり得るため
        （特にOCR経路）、既存の非同期パターン（LoadingWindow＋
        threading.Thread(daemon=True)＋queue.Queue＋self.after(200, ...)
        ポーリング）で実行し、UIスレッドをブロックしないようにする。
        """
        if not self.selected_pdf_path:
            return
        self._close_cell_edit_entry()
        self.btn_convert.config(state=tk.DISABLED)
        self.btn_match.config(state=tk.DISABLED)
        self.lbl_method.config(text="")
        # 再変換時、前回の照合結果（右ペイン）は今回の変換結果とは無関係になる
        # ため、あわせてリセットする。
        self.matched_rows = None
        self.btn_save_matched.config(state=tk.DISABLED)
        self._reset_match_status()
        self._clear_tree(self.tree_matched)

        self._loading_window = LoadingWindow(
            self, message="PDFを読み取っています（OCRが必要な場合は時間がかかります）…",
        )

        t = threading.Thread(
            target=self._run_convert_in_thread, args=(self.selected_pdf_path,), daemon=True,
        )
        t.start()
        self.after(200, self._poll_result_queue)

    def _run_convert_in_thread(self, pdf_path):
        try:
            result = extract_pdf_rows(pdf_path)
            self._result_queue.put((True, result))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._result_queue.put((False, f"{e}\n{tb}"))

    def _poll_result_queue(self):
        try:
            result = self._result_queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_result_queue)
            return

        success, payload = result
        if self._loading_window is not None:
            self._loading_window.destroy()
            self._loading_window = None
        self.btn_convert.config(state=tk.NORMAL)

        if not success:
            messagebox.showerror(
                "変換エラー", f"PDFの読み取り中にエラーが発生しました。\n\n{payload}",
                parent=self.winfo_toplevel(),
            )
            return

        self.extracted_rows = payload["rows"]
        self.confidences = payload["confidences"]
        method_label = "テキスト抽出" if payload["method"] == "text" else "OCR（画像認識）"
        self.lbl_method.config(
            text=f"抽出方式: {method_label} / {payload['page_count']}ページ / {len(payload['rows'])}行",
        )
        self._populate_tree(self.tree_all, self.extracted_rows, confidences=self.confidences)
        has_rows = bool(self.extracted_rows)
        self.btn_match.config(state=tk.NORMAL if has_rows else tk.DISABLED)
        self.btn_save_all.config(state=tk.NORMAL if has_rows else tk.DISABLED)

        if not has_rows:
            messagebox.showwarning(
                "警告", "PDFから文字を読み取れませんでした。", parent=self.winfo_toplevel(),
            )
        else:
            self._notify_low_confidence_rows_if_any()

    def _notify_low_confidence_rows_if_any(self):
        """
        変換完了時、信頼度が_LOW_CONFIDENCE_THRESHOLD未満の行が1件でもあれば、
        内容確認・修正を促す案内メッセージを表示する。あわせて最初の該当行を
        選択状態にし`tree.see()`でスクロール位置を合わせることで、案内を
        読んだ直後にその行へすぐ目が行くようにする（セルはダブルクリックで
        手動編集できる。_on_tree_all_double_click()参照）。
        """
        low_confidence_iids = [
            iid for iid in self.tree_all.get_children("")
            if "low_confidence" in self.tree_all.item(iid, "tags")
        ]
        if not low_confidence_iids:
            return

        self.tree_all.selection_set(low_confidence_iids[0])
        self.tree_all.see(low_confidence_iids[0])

        messagebox.showinfo(
            "確認のお願い",
            f"信頼度が低い行が{len(low_confidence_iids)}件あります。"
            "内容を確認し、誤認識があればセルをダブルクリックして修正してください。",
            parent=self.winfo_toplevel(),
        )

    def on_match(self):
        """
        変換結果（self.extracted_rows、左ペイン）を、inventory_stockに登録
        済みの96コード一覧と照合する。左ペインの内容は一切変更せず、一致行
        （self.matched_rows）のみを右ペインに新たに表示する。
        """
        if not self.extracted_rows:
            messagebox.showwarning("警告", "先に変換を実行してください。", parent=self.winfo_toplevel())
            return

        result = match_against_inventory(self.extracted_rows)
        self.matched_rows = result["matched_rows"]
        self._populate_tree(self.tree_matched, self.matched_rows)
        self.btn_save_matched.config(state=tk.NORMAL if self.matched_rows else tk.DISABLED)
        self.lbl_match_status.config(
            text=f"一致：{result['matched_count']}件 / 除外：{result['excluded_count']}件",
            foreground="green",
        )

        messagebox.showinfo(
            "照合結果",
            f"一致件数：{result['matched_count']}件\n除外件数：{result['excluded_count']}件"
            "\n\n（除外＝96コードが認識できなかった行、または在庫登録が無い他部署の部品と想定される行）",
            parent=self.winfo_toplevel(),
        )

    def _reset_match_status(self):
        self.lbl_match_status.config(
            text="未実行（左側の変換結果に対して「照合・抽出」を実行してください）",
            foreground="gray",
        )

    def _clear_tree(self, tree):
        tree.delete(*tree.get_children())
        tree["columns"] = ()

    def _populate_tree(self, tree, rows, confidences=None):
        """
        行ごとに列数が異なり得るため（素朴な空白区切りの結果）、最大列数に
        合わせて「列1」「列2」…という機械的な列見出しを構築する
        （実レイアウト確定前の暫定プレビュー表示）。左右どちらのTreeviewにも
        使える共通処理。

        confidences（rowsと同じ順序・同じ件数のリスト）を渡すと、末尾に
        「信頼度」列を追加し、_LOW_CONFIDENCE_THRESHOLD未満の行には
        警告色（背景色）のタグを付けて視覚的に強調する。現状は左ペイン
        （変換結果全行）のみがこの引数を渡す（右ペインの照合結果は
        confidences=None のまま、信頼度列を持たない）。
        """
        self._clear_tree(tree)
        if not rows:
            return

        max_cols = max(len(row) for row in rows)
        cols = [f"col{i}" for i in range(max_cols)]
        if confidences is not None:
            cols = cols + ["confidence"]
        tree["columns"] = cols
        tree["show"] = "headings"
        for i in range(max_cols):
            tree.heading(cols[i], text=f"列{i + 1}")
            tree.column(cols[i], width=120, anchor=tk.W)
        if confidences is not None:
            tree.heading("confidence", text="信頼度")
            tree.column("confidence", width=80, anchor=tk.E)
            tree.tag_configure("low_confidence", background="#ffb3b3")

        for i, row in enumerate(rows):
            padded = row + [""] * (max_cols - len(row))
            tags = ()
            if confidences is not None:
                confidence = confidences[i]
                padded = padded + [f"{confidence:.0f}"]
                if confidence < _LOW_CONFIDENCE_THRESHOLD:
                    tags = ("low_confidence",)
            tree.insert("", tk.END, values=padded, tags=tags)

    # ------------------------------------------------------------------
    # 左ペイン（変換結果全行）のセル編集
    # ------------------------------------------------------------------
    #
    # ui.checkable_treeview.CheckableTreeviewの編集パターン（セルダブル
    # クリック→一時的なEntryをbbox()の位置に重ねる→Return/FocusOutで
    # 書き戻し→Escapeでキャンセル）を踏襲している。ただしCheckableTreeviewの
    # バリデーションは編集対象を数値専用（float()変換）としており、本画面が
    # 扱う96コード・名称・数量等の混在テキストにはそのまま使えないため、
    # 「空欄にしない」という簡易バリデーションに差し替えている。
    # また「信頼度」列（OCRの実際の認識結果を示す値）は編集対象外とする。

    def _on_tree_all_double_click(self, event):
        region = self.tree_all.identify_region(event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree_all.identify_row(event.y)
        column_id = self.tree_all.identify_column(event.x)
        if not row_id or not column_id:
            return

        try:
            col_pos = int(column_id.replace("#", "")) - 1
        except ValueError:
            return
        columns = self.tree_all["columns"]
        if col_pos < 0 or col_pos >= len(columns):
            return

        col_key = columns[col_pos]
        if col_key == "confidence":
            return

        self._start_cell_edit(row_id, col_key, column_id)

    def _start_cell_edit(self, row_id, col_key, column_id):
        bbox = self.tree_all.bbox(row_id, column_id)
        if not bbox:
            return
        x, y, width, height = bbox

        self._close_cell_edit_entry()

        current_text = self.tree_all.set(row_id, col_key)

        entry = tk.Entry(self.tree_all)
        entry.insert(0, current_text)
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()

        entry.bind("<Return>", lambda e: self._commit_cell_edit(row_id, col_key, entry, force_close=False))
        entry.bind("<FocusOut>", lambda e: self._commit_cell_edit(row_id, col_key, entry, force_close=True))
        entry.bind("<Escape>", lambda e: self._close_cell_edit_entry())

        self._edit_entry = entry

    def _commit_cell_edit(self, row_id, col_key, entry, force_close):
        """
        Entryの内容を検証し、空欄でなければTreeview表示とself.extracted_rows
        （元データ）の両方に書き戻す。空欄（不正値）の場合は書き戻さず、
        Entryの背景色を変えてエラーを示す。

        force_close=True（FocusOutから呼ばれた場合）は、不正な値のままでも
        フォーカスが外れる以上Entryを開いたままにできないため、書き戻さずに
        Entryを閉じる（＝編集前の値のまま確定）。
        force_close=False（Returnから呼ばれた場合）は、不正な値ならEntryを
        閉じずに残し、ユーザーがその場で訂正できるようにする。
        """
        text = entry.get().strip()
        if not text:
            entry.configure(background=_EDIT_ERROR_BG)
            if force_close:
                self._close_cell_edit_entry()
            return

        self.tree_all.set(row_id, col_key, text)
        self._write_back_to_extracted_rows(row_id, col_key, text)
        self._close_cell_edit_entry()

    def _write_back_to_extracted_rows(self, row_id, col_key, text):
        """
        編集内容をself.extracted_rows（「照合・抽出」・CSV保存で実際に
        使われる元データ）にも反映する。

        row_idからself.extracted_rows内の対応するインデックスは、
        tree_all.index(row_id)（Treeview上の表示順位置）で求める。
        _populate_tree()はself.extracted_rowsと同じ順序で行を挿入しており、
        tree_allに対してはフィルタ・ソート等の並び替えを一切行っていないため、
        この位置がそのままself.extracted_rowsのインデックスと一致する。

        col_key（"col0"・"col1"…）から列位置を取り出す。対象行が元々
        その列数より短い場合（空白区切りの結果、行ごとに列数が異なり得る
        ため）は、空文字列で埋めて拡張してから代入する。
        """
        row_index = self.tree_all.index(row_id)
        col_position = int(col_key.replace("col", ""))

        row = self.extracted_rows[row_index]
        if col_position >= len(row):
            row.extend([""] * (col_position + 1 - len(row)))
        row[col_position] = text

    def _close_cell_edit_entry(self):
        if self._edit_entry is not None:
            entry = self._edit_entry
            self._edit_entry = None
            try:
                entry.destroy()
            except tk.TclError:
                pass

    def _save_rows_to_csv(self, rows, title):
        if not rows:
            messagebox.showwarning("警告", "保存する内容がありません。", parent=self.winfo_toplevel())
            return

        default_name = os.path.splitext(os.path.basename(self.selected_pdf_path))[0] + ".csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")], parent=self.winfo_toplevel(),
        )
        if not save_path:
            return

        try:
            write_rows_to_csv(rows, save_path)
        except Exception as e:
            messagebox.showerror("エラー", f"CSV保存に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"{title}をCSV保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_save_all_csv(self):
        """左ペイン（変換結果・全行）をCSV保存する。"""
        self._save_rows_to_csv(self.extracted_rows, "変換結果（全行）")

    def on_save_matched_csv(self):
        """右ペイン（照合・抽出結果・一致行のみ）をCSV保存する。"""
        self._save_rows_to_csv(self.matched_rows, "照合・抽出結果（一致行）")
