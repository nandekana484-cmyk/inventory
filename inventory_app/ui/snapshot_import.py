import os
import sys
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

# プロジェクトルートをimport対象に追加
sys.path.append(
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..")
    )
)

from models.snapshots import get_snapshot_history
from services.snapshot_service import parse_and_import_snapshot


class SnapshotImportWindow(tk.Toplevel):
    """在庫スナップショット取込画面"""

    def __init__(self, parent, current_worker):
        super().__init__(parent)

        self.current_worker = current_worker
        self.selected_file_path = ""

        self.title("在庫スナップショット取込")
        self.geometry("1050x560")
        self.minsize(900, 500)

        self.create_widgets()
        self.refresh_history_table()

    def create_widgets(self):
        """画面部品を作成する"""

        # =========================
        # 取込設定
        # =========================
        import_frame = ttk.LabelFrame(
            self,
            text="スナップショットファイルの取込",
            padding=15,
        )
        import_frame.pack(
            fill=tk.X,
            padx=15,
            pady=10,
        )

        # 基準日
        ttk.Label(
            import_frame,
            text="スナップショット基準日（YYYY-MM-DD）:",
        ).grid(
            row=0,
            column=0,
            sticky=tk.W,
            padx=5,
            pady=5,
        )

        self.entry_date = ttk.Entry(
            import_frame,
            width=15,
        )
        self.entry_date.grid(
            row=0,
            column=1,
            sticky=tk.W,
            padx=5,
            pady=5,
        )
        self.entry_date.insert(
            0,
            datetime.now().strftime("%Y-%m-%d"),
        )

        # ファイル選択
        ttk.Label(
            import_frame,
            text="対象CSVファイル:",
        ).grid(
            row=1,
            column=0,
            sticky=tk.W,
            padx=5,
            pady=5,
        )

        file_select_frame = ttk.Frame(import_frame)
        file_select_frame.grid(
            row=1,
            column=1,
            columnspan=2,
            sticky=tk.EW,
            padx=5,
            pady=5,
        )

        self.lbl_file_path = ttk.Label(
            file_select_frame,
            text="未選択",
            foreground="gray",
            width=60,
            anchor=tk.W,
        )
        self.lbl_file_path.pack(
            side=tk.LEFT,
            padx=(0, 10),
        )

        btn_browse = ttk.Button(
            file_select_frame,
            text="参照...",
            command=self.browse_file,
        )
        btn_browse.pack(side=tk.LEFT)

        # 作業者表示
        worker_id = self.get_worker_id()

        ttk.Label(
            import_frame,
            text=f"取込作業者: {worker_id}",
        ).grid(
            row=0,
            column=2,
            sticky=tk.E,
            padx=10,
            pady=5,
        )

        # 実行ボタン
        btn_import = ttk.Button(
            import_frame,
            text="取込実行",
            command=self.on_import,
        )
        btn_import.grid(
            row=2,
            column=1,
            sticky=tk.E,
            padx=5,
            pady=10,
        )

        import_frame.columnconfigure(1, weight=1)

        # =========================
        # 取込履歴
        # =========================
        history_frame = ttk.LabelFrame(
            self,
            text="取込済みスナップショット一覧",
            padding=10,
        )
        history_frame.pack(
            expand=True,
            fill=tk.BOTH,
            padx=15,
            pady=10,
        )

        columns = (
            "batch_id",
            "date",
            "count",
            "source_file",
            "imported_by",
            "imported_at",
        )

        self.tree = ttk.Treeview(
            history_frame,
            columns=columns,
            show="headings",
            height=12,
        )

        self.tree.heading(
            "batch_id",
            text="取込ID",
        )
        self.tree.heading(
            "date",
            text="スナップショット基準日",
        )
        self.tree.heading(
            "count",
            text="取込件数",
        )
        self.tree.heading(
            "source_file",
            text="ファイル名",
        )
        self.tree.heading(
            "imported_by",
            text="取込者",
        )
        self.tree.heading(
            "imported_at",
            text="取込実行日時",
        )

        self.tree.column(
            "batch_id",
            width=70,
            anchor=tk.CENTER,
        )
        self.tree.column(
            "date",
            width=150,
            anchor=tk.CENTER,
        )
        self.tree.column(
            "count",
            width=90,
            anchor=tk.E,
        )
        self.tree.column(
            "source_file",
            width=250,
            anchor=tk.W,
        )
        self.tree.column(
            "imported_by",
            width=100,
            anchor=tk.CENTER,
        )
        self.tree.column(
            "imported_at",
            width=180,
            anchor=tk.CENTER,
        )

        # 最新バッチを色分け
        self.tree.tag_configure(
            "latest",
            background="#E2F0D9",
        )

        scrollbar_y = ttk.Scrollbar(
            history_frame,
            orient=tk.VERTICAL,
            command=self.tree.yview,
        )
        self.tree.configure(
            yscrollcommand=scrollbar_y.set,
        )

        scrollbar_x = ttk.Scrollbar(
            history_frame,
            orient=tk.HORIZONTAL,
            command=self.tree.xview,
        )
        self.tree.configure(
            xscrollcommand=scrollbar_x.set,
        )

        self.tree.grid(
            row=0,
            column=0,
            sticky="nsew",
        )
        scrollbar_y.grid(
            row=0,
            column=1,
            sticky="ns",
        )
        scrollbar_x.grid(
            row=1,
            column=0,
            sticky="ew",
        )

        history_frame.rowconfigure(
            0,
            weight=1,
        )
        history_frame.columnconfigure(
            0,
            weight=1,
        )

    def get_worker_id(self):
        """current_workerからworker_idを取得する"""

        if isinstance(self.current_worker, dict):
            return self.current_worker.get(
                "worker_id",
                "SYSTEM",
            )

        if self.current_worker:
            return str(self.current_worker)

        return "SYSTEM"

    def browse_file(self):
        """CSVファイルを選択する"""

        file_path = filedialog.askopenfilename(
            title="スナップショットCSVファイルを選択",
            filetypes=[
                ("CSV Files", "*.csv"),
                ("Text Files", "*.txt"),
                ("All Files", "*.*"),
            ],
        )

        if not file_path:
            return

        self.selected_file_path = file_path

        self.lbl_file_path.config(
            text=file_path,
            foreground="black",
        )

    def validate_snapshot_date(self, value):
        """基準日をYYYY-MM-DD形式で検証する"""

        try:
            datetime.strptime(
                value,
                "%Y-%m-%d",
            )
            return True
        except ValueError:
            return False

    def on_import(self):
        """スナップショットを取り込む"""

        snapshot_date = self.entry_date.get().strip()

        if not snapshot_date:
            messagebox.showwarning(
                "入力エラー",
                "スナップショット基準日を入力してください。",
                parent=self,
            )
            return

        if not self.validate_snapshot_date(snapshot_date):
            messagebox.showwarning(
                "入力エラー",
                "基準日はYYYY-MM-DD形式で入力してください。",
                parent=self,
            )
            return

        if not self.selected_file_path:
            messagebox.showwarning(
                "ファイル未選択",
                "取込対象のCSVファイルを選択してください。",
                parent=self,
            )
            return

        worker_id = self.get_worker_id()

        try:
            # 新しいsnapshot_service.pyの引数に合わせる
            count = parse_and_import_snapshot(
                file_path=self.selected_file_path,
                snapshot_date=snapshot_date,
                worker=worker_id,
            )

            messagebox.showinfo(
                "成功",
                f"スナップショットデータ "
                f"{count:,}件の取込が完了しました。",
                parent=self,
            )

            # 選択状態を初期化
            self.selected_file_path = ""
            self.lbl_file_path.config(
                text="未選択",
                foreground="gray",
            )

            self.refresh_history_table()

        except Exception as exc:
            messagebox.showerror(
                "取込エラー",
                f"取込処理中にエラーが発生しました。\n\n{exc}",
                parent=self,
            )

    def refresh_history_table(self):
        """スナップショット取込履歴を更新する"""

        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            history = get_snapshot_history()
        except Exception as exc:
            messagebox.showerror(
                "履歴取得エラー",
                f"取込履歴を取得できませんでした。\n\n{exc}",
                parent=self,
            )
            return

        if not history:
            return

        # get_snapshot_history()はbatch_id降順を想定
        latest_batch_id = history[0].get("batch_id")

        for row in history:
            batch_id = row.get("batch_id")

            tag = ()
            if batch_id == latest_batch_id:
                tag = ("latest",)

            self.tree.insert(
                "",
                tk.END,
                values=(
                    batch_id or "-",
                    row.get("snapshot_date") or "-",
                    f"{row.get('row_count', 0):,}",
                    row.get("source_file") or "-",
                    row.get("imported_by") or "-",
                    row.get("imported_at") or "-",
                ),
                tags=tag,
            )