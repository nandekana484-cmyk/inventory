# ui/master_import_window.py
import threading
import queue

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from services.master_import_service import import_parts_csv
from ui.loading_window import LoadingWindow


class MasterImportWindow(tk.Toplevel):
    """
    部品マスタ（parts）をCSVからインポートする画面。
    CSVフォーマットは未確定のため、列名ゆらぎ・追加/欠損列に耐えられる
    services.master_import_service を経由して取り込む。

    BOM（新BOM基盤）のインポートは ui.parts_attributes_import_window
    （丁取り数等の部品属性）と共有フォルダのTSV（services.bom_service）に
    完全移行しており、本画面の対象外。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.title("マスタインポート")
        self.geometry("800x520")

        notebook = ttk.Notebook(self)
        notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

        notebook.add(PartsImportTab(notebook), text=" 部品マスタインポート ")


class _BaseImportTab(ttk.Frame):
    """
    「CSV選択」「インポート実行」「プレビューTreeview」を持つタブの共通実装。
    プレビュー列（PREVIEW_COLS）と実際のインポート処理（run_import）は
    サブクラスで指定する（部品マスタ／BOMマスタ以外のタブを将来追加する際の拡張ポイント）。
    """
    PREVIEW_COLS = ()
    PREVIEW_HEADERS = {}

    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.selected_csv_path = None

        select_frame = ttk.Frame(self)
        select_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(select_frame, text="CSV選択", command=self.on_select_csv).pack(side=tk.LEFT, padx=5)
        self.lbl_csv_path = ttk.Label(select_frame, text="（未選択）", foreground="blue")
        self.lbl_csv_path.pack(side=tk.LEFT, padx=5)

        self.btn_import = ttk.Button(select_frame, text="インポート実行", command=self.on_import_execute)
        self.btn_import.pack(side=tk.LEFT, padx=15)

        tree_frame = ttk.Frame(self)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        self.tree = ttk.Treeview(tree_frame, columns=self.PREVIEW_COLS, show="headings")
        for c in self.PREVIEW_COLS:
            self.tree.heading(c, text=self.PREVIEW_HEADERS.get(c, c))
            self.tree.column(c, width=150, anchor=tk.W)
        self.tree.pack(expand=True, fill=tk.BOTH)

    def on_select_csv(self):
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], parent=self.winfo_toplevel())
        if not file_path:
            return
        self.selected_csv_path = file_path
        self.lbl_csv_path.config(text=file_path)

    def load_preview(self, rows):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in rows:
            self.tree.insert("", tk.END, values=[row.get(c, "") for c in self.PREVIEW_COLS])

    def run_import(self):
        """サブクラスで import_parts_csv() 等を呼び出して結果dictを返す。"""
        raise NotImplementedError

    def on_import_execute(self):
        """
        run_import()（サブクラスがimport_parts_csv()等を呼ぶ、DB・ファイル
        アクセスのみでTkinterに触れない）を別スレッドで実行し、UIスレッドを
        ブロックしないようにする。ui.kitting_plan_import.KittingPlanImportWindow.
        on_start_import()で確立済みのLoadingWindow＋threading.Thread(daemon=True)＋
        queue.Queue＋self.after(200,...)ポーリングパターンをそのまま踏襲する。

        _BaseImportTabの共通実装のため、ここを直すだけで現在の部品マスタ
        インポートタブだけでなく、将来追加される全てのタブにも自動的に反映される。
        """
        if not self.selected_csv_path:
            messagebox.showwarning("警告", "CSVファイルを選択してください。", parent=self.winfo_toplevel())
            return

        self.btn_import.config(state=tk.DISABLED)
        loading = LoadingWindow(self, message="CSVを取り込んでいます…")
        result_queue = queue.Queue()

        def _work():
            try:
                result_queue.put((True, self.run_import()))
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
                # 元の実装もExceptionを広く捕捉していたため（列名ゆらぎ等、
                # 様々な理由でrun_import()が失敗し得るため）、挙動を変えず
                # 同じ文言でエラーダイアログを表示する。
                messagebox.showerror("エラー", f"インポート処理中にエラーが発生しました：\n{payload}", parent=self.winfo_toplevel())
                return

            result = payload
            self.load_preview(result["rows"])

            msg = f"取込件数：{result['imported']}件"
            warnings = result["warnings"]
            if warnings:
                shown = "\n".join(warnings[:10])
                more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
                msg += f"\n\n警告（{len(warnings)}件）：\n{shown}{more}"

            messagebox.showinfo("インポート結果", msg, parent=self.winfo_toplevel())

        self.after(200, _poll)


class PartsImportTab(_BaseImportTab):
    PREVIEW_COLS = ("part_no", "name", "shelf")
    PREVIEW_HEADERS = {"part_no": "部品番号", "name": "部品名", "shelf": "棚番"}

    def run_import(self):
        return import_parts_csv(self.selected_csv_path)
