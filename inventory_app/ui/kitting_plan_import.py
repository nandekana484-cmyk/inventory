# ui/kitting_plan_import.py
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from services.kitting_import_service import import_kitting_plan_csv
import config
import sqlite3
import os

from models.kitting_plan import list_plan_batches, mark_batch_deleted
from ui.loading_window import LoadingWindow

class KittingPlanImportWindow(tk.Toplevel):
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        self.title("キッティング計画CSV取込 / 履歴（簡易）")
        self.geometry("1300x720")

        self._result_queue = queue.Queue()
        self._loading_window = None

        self.create_widgets()
        self._load_batch_list()
        self.after(200, self._poll_result_queue)

    def create_widgets(self):
        frame = ttk.Frame(self, padding=10)
        frame.pack(expand=True, fill=tk.BOTH)

        # 上部操作領域
        top_frame = ttk.Frame(frame)
        top_frame.pack(fill=tk.X, pady=(0,8))
        self.file_path_var = tk.StringVar()
        ttk.Entry(top_frame, textvariable=self.file_path_var, width=90).pack(side=tk.LEFT, padx=(0,5))
        ttk.Button(top_frame, text="参照", command=self.browse_file).pack(side=tk.LEFT)
        self.btn_import = ttk.Button(top_frame, text="取込実行", command=self.on_start_import)
        self.btn_import.pack(side=tk.LEFT, padx=(8,5))
        self.lbl_status = ttk.Label(top_frame, text="状態: 待機中")
        self.lbl_status.pack(side=tk.LEFT, padx=(8,0))

        # 中央の左右ペイン（PanedWindow）
        middle = ttk.Panedwindow(frame, orient=tk.HORIZONTAL)
        middle.pack(expand=True, fill=tk.BOTH)

        # 左：バッチ一覧
        left = ttk.Labelframe(middle, text="取込バッチ一覧（有効のみ）", padding=5)
        middle.add(left, weight=1)
        cols_batch = ("plan_batch_id", "source_file", "imported_at", "imported_by", "row_count")
        self.tree_batches = ttk.Treeview(left, columns=cols_batch, show="headings", selectmode="browse", height=20)
        for c, w in zip(cols_batch, (80, 260, 160, 120, 80)):
            self.tree_batches.heading(c, text=c)
            self.tree_batches.column(c, width=w, anchor=tk.W)
        vsb_left = ttk.Scrollbar(left, orient="vertical", command=self.tree_batches.yview)
        self.tree_batches.configure(yscrollcommand=vsb_left.set)
        vsb_left.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_batches.pack(expand=True, fill=tk.BOTH)
        self.tree_batches.bind("<<TreeviewSelect>>", self.on_select_batch)
        ttk.Button(left, text="更新", command=self._load_batch_list).pack(fill=tk.X, pady=(5,0))
        ttk.Button(left, text="削除（ソフト）", command=self._soft_delete_selected).pack(fill=tk.X, pady=(6,0))

        # 右：バッチ内容（横スクロール対応・ソート対応）
        right = ttk.Labelframe(middle, text="バッチ内容（選択バッチ）", padding=5)
        middle.add(right, weight=3)

        # カラム定義（追加の日時列を含む）
        cols_item = (
            "kitting_list_no", "lot_no", "setup_file_no", "board_name", "planned_qty",
            "cumulative_qty_external", "order_qty", "production_side", "status",
            "plan_start_datetime", "plan_end_datetime", "deadline",
            "actual_start_datetime", "actual_end_datetime"
        )

        # Treeview を grid に配置してスクロールバーレイアウトを安定させる
        self.tree_items = ttk.Treeview(right, columns=cols_item, show="headings")
        for c in cols_item:
            # 幅はカラムごとに調整。kitting_list_no と board_name を広めに。
            w = 200 if c in ("kitting_list_no", "board_name") else 130
            self.tree_items.heading(c, text=c, command=lambda _c=c: self._sort_treeview_column(self.tree_items, _c, False))
            self.tree_items.column(c, width=w, anchor=tk.W)

        # スクロールバー（縦・横）
        vsb_right = ttk.Scrollbar(right, orient="vertical", command=self.tree_items.yview)
        hsb_right = ttk.Scrollbar(right, orient="horizontal", command=self.tree_items.xview)
        self.tree_items.configure(yscrollcommand=vsb_right.set, xscrollcommand=hsb_right.set)

        # grid 配置： tree at (0,0), vsb at (0,1), hsb at (1,0)
        self.tree_items.grid(row=0, column=0, sticky="nsew")
        vsb_right.grid(row=0, column=1, sticky="ns")
        hsb_right.grid(row=1, column=0, sticky="ew")

        # 右フレームの grid 行列拡張設定
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # 下部表示ラベル
        bottom = ttk.Frame(frame)
        bottom.pack(fill=tk.X, pady=(8,0))
        self.result_label = ttk.Label(bottom, text="", foreground="blue")
        self.result_label.pack(side=tk.LEFT)

    def browse_file(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files","*.csv"),("All files","*.*")], parent=self.winfo_toplevel())
        if path:
            self.file_path_var.set(path)

    def on_start_import(self):
        file_path = self.file_path_var.get().strip()
        if not file_path or not os.path.isfile(file_path):
            messagebox.showwarning("入力エラー", "CSVファイルを選択してください。", parent=self.winfo_toplevel())
            return
        self.btn_import.config(state=tk.DISABLED)
        self.lbl_status.config(text="状態: 取込中...")
        self.result_label.config(text="")
        self._loading_window = LoadingWindow(self, message="キッティング計画CSVを取り込んでいます…")
        worker_id = self.current_worker.get("worker_id", "SYSTEM")
        t = threading.Thread(target=self._run_import_in_thread, args=(file_path, worker_id), daemon=True)
        t.start()

    def _run_import_in_thread(self, file_path, worker_id):
        try:
            batch_id, count = import_kitting_plan_csv(file_path, worker_id)
            self._result_queue.put((True, {"batch_id": batch_id, "count": count, "file": os.path.basename(file_path)}))
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
        self.btn_import.config(state=tk.NORMAL)

        if not success:
            self.lbl_status.config(text="状態: エラー")
            messagebox.showerror("取込エラー", f"CSV取込中にエラーが発生しました。\n\n{payload}", parent=self.winfo_toplevel())
            self.result_label.config(text="取込失敗")
        else:
            batch_id = payload.get("batch_id")
            count = payload.get("count")
            filename = payload.get("file")
            self.lbl_status.config(text="状態: 完了")
            self.result_label.config(text=f"取込完了：{count}件（バッチID: {batch_id}、ファイル: {filename}）")
            messagebox.showinfo("取込完了", f"{count}件のキッティング計画を取り込みました。", parent=self.winfo_toplevel())
            self._load_batch_list(select_batch_id=batch_id)

        self.after(200, self._poll_result_queue)

    # ---------------- DBユーティリティ ----------------
    def _get_db_connection(self):
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def _load_batch_list(self, select_batch_id=None):
        batches = list_plan_batches(include_deleted=False)
        for iid in self.tree_batches.get_children():
            self.tree_batches.delete(iid)
        for r in batches:
            iid = f"batch_{r['plan_batch_id']}"
            self.tree_batches.insert("", tk.END, iid=iid, values=(
                r["plan_batch_id"], r["source_file"], r["imported_at"], r["imported_by"], r["row_count"]
            ))
        if select_batch_id:
            sel_iid = f"batch_{select_batch_id}"
            if sel_iid in self.tree_batches.get_children():
                self.tree_batches.selection_set(sel_iid)
                self.tree_batches.see(sel_iid)
                self._load_items_for_batch(select_batch_id)
        else:
            children = self.tree_batches.get_children()
            if children:
                first = children[0]
                self.tree_batches.selection_set(first)
                val = self.tree_batches.item(first, "values")
                try:
                    pid = int(val[0])
                    self._load_items_for_batch(pid)
                except Exception:
                    pass

    def on_select_batch(self, event):
        sel = self.tree_batches.selection()
        if not sel:
            return
        vals = self.tree_batches.item(sel[0], "values")
        try:
            batch_id = int(vals[0])
        except Exception:
            return
        self._load_items_for_batch(batch_id)

    def _load_items_for_batch(self, batch_id):
        conn = self._get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT kitting_list_no, lot_no, setup_file_no, board_name, planned_qty,
                   cumulative_qty_external, order_qty, production_side, status,
                   plan_start_datetime, plan_end_datetime, deadline,
                   actual_start_datetime, actual_end_datetime
            FROM kitting_plan_items
            WHERE plan_batch_id = ?
            ORDER BY kitting_list_no
        """, (batch_id,))
        rows = cur.fetchall()
        conn.close()

        # 右Tree をクリアして全件表示
        for iid in self.tree_items.get_children():
            self.tree_items.delete(iid)

        # sqlite3.Row は dict ではないので .get() は使えません。
        # 安全に値を取り出すヘルパーを使います。
        for r in rows:
            def val(key):
                try:
                    v = r[key]
                except Exception:
                    v = None
                if v is None:
                    return ""
                # 数値は見やすく整形（必要なければこの処理を変更してください）
                if isinstance(v, (int, float)):
                    if float(v).is_integer():
                        return f"{int(v)}"
                    return f"{v}"
                return str(v)

            self.tree_items.insert("", tk.END, values=(
                val("kitting_list_no"),
                val("lot_no"),
                val("setup_file_no"),
                val("board_name"),
                val("planned_qty"),
                val("cumulative_qty_external"),
                val("order_qty"),
                val("production_side"),
                val("status"),
                val("plan_start_datetime"),
                val("plan_end_datetime"),
                val("deadline"),
                val("actual_start_datetime"),
                val("actual_end_datetime")
            ))

        self.lbl_status.config(text=f"状態: バッチ {batch_id} 表示中（{len(rows)} 件）")
        self.result_label.config(text=f"バッチ {batch_id} の内容を表示しました。")
    # ---------------- バッチ操作 ----------------
    def _get_selected_batch_id(self):
        sel = self.tree_batches.selection()
        if not sel:
            return None
        vals = self.tree_batches.item(sel[0], "values")
        try:
            return int(vals[0])
        except Exception:
            return None

    def _soft_delete_selected(self):
        bid = self._get_selected_batch_id()
        if not bid:
            messagebox.showwarning("警告", "削除対象のバッチを選択してください。", parent=self.winfo_toplevel())
            return
        if not messagebox.askyesno(
            "確認",
            f"バッチ {bid} を削除（ソフト）します。\n"
            "このバッチに含まれるキッティング計画も無効化され、生産実績入力画面の一覧から消えます。\n"
            "よろしいですか？",
            parent=self.winfo_toplevel(),
        ):
            return
        try:
            mark_batch_deleted(bid, deleted=True)
            messagebox.showinfo(
                "完了",
                f"バッチ {bid} を削除しました（ソフト削除）。\n"
                "このバッチに含まれるキッティング計画も無効化されました。",
                parent=self.winfo_toplevel(),
            )
            self._load_batch_list()
            # 右ペインクリア
            for iid in self.tree_items.get_children():
                self.tree_items.delete(iid)
            self.result_label.config(text=f"バッチ {bid} を削除しました。")
        except Exception as e:
            messagebox.showerror("エラー", f"削除に失敗しました：{e}", parent=self.winfo_toplevel())

    # ---------------- ソート機能 ----------------
    def _sort_treeview_column(self, treeview, col, reverse):
        """
        Treeview の指定カラムでソート。数値は数値として、日付っぽい文字列は文字列のまま比較。
        reverse は現在のソート順を反転するために使う。
        """
        # すべての行を取得
        data = [(treeview.set(k, col), k) for k in treeview.get_children('')]

        # 値を比較しやすい形に変換（数値なら数値に）
        def try_num(s):
            try:
                return float(s.replace(',', '')) if s is not None and s != "" else float('-inf')
            except Exception:
                return s or ""

        # 判定基準：カラム名が数値っぽければ数値変換を試みる
        numeric_cols = {"planned_qty", "cumulative_qty_external", "order_qty", "row_count"}
        if col in numeric_cols:
            data.sort(key=lambda t: try_num(t[0]), reverse=reverse)
        else:
            # 文字列比較（大文字小文字は区別しない）
            data.sort(key=lambda t: (t[0] or "").lower(), reverse=reverse)

        # 順序を入れ替えて再配置
        for index, (val, k) in enumerate(data):
            treeview.move(k, '', index)

        # 次回は逆順で呼べるように header の command を入れ替える
        treeview.heading(col, command=lambda: self._sort_treeview_column(treeview, col, not reverse))