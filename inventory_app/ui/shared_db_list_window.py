# ui/shared_db_list_window.py
"""
共有フォルダ上の月別DB一覧画面。

ユーザーが指定した共有フォルダの親ディレクトリ（例：\\\\server\\share\\InventoryDB\\）
直下をスキャンし、"inventory.db"が存在するサブフォルダを一覧表示する
（services.shared_db_scan_service.scan_shared_db_folders()。
ui.main_window.MainWindow._load_db_folders()のローカル版と同じ考え方）。

親ディレクトリのパスはservices.app_settings_service経由で永続化され、
次回この画面を開いた際に自動的に前回と同じ場所を再スキャンする。
"""
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from services.shared_db_scan_service import scan_shared_db_folders
from services.app_settings_service import save_shared_db_root, load_shared_db_root
from ui.db_delete_helper import confirm_and_delete_database


def _format_size(size_bytes):
    """バイト数を人間が読みやすい単位（B/KB/MB/GB/TB）に変換する。取得失敗時（None）は"-"。"""
    if size_bytes is None:
        return "-"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


class SharedDbListWindow(tk.Toplevel):
    """
    switch_callback：一覧から選んだinventory.dbのフルパス(str)を受け取り、
    切り替えに成功したか（bool）を返すcallable。ui.main_window.MainWindowの
    _switch_to_shared_db()（既存のon_open_shared_database()と同じ
    _try_switch_db_path()経由の切り替え処理）を渡してもらう想定。
    本画面自体はロック取得・config.set_db_path()の呼び出しには一切関与しない
    （一覧表示・親ディレクトリの永続化のみに責務を限定する）。
    """

    def __init__(self, parent, switch_callback, current_worker=None):
        super().__init__(parent)
        self._switch_callback = switch_callback
        self.current_worker = current_worker or {}

        self.title("共有フォルダのDB一覧")
        self.geometry("760x420")

        self.root_dir_var = tk.StringVar(value=load_shared_db_root() or "")

        self._create_widgets()
        # 前回指定した親ディレクトリが永続化されていれば、画面を開いた時点で
        # 自動的に再スキャンする（毎回手動で「参照」し直す手間を無くすため）。
        if self.root_dir_var.get():
            self.refresh(show_empty_message=False)

    def _create_widgets(self):
        top_frame = ttk.Frame(self, padding=10)
        top_frame.pack(fill=tk.X)

        ttk.Label(top_frame, text="親ディレクトリ：").pack(side=tk.LEFT)
        self.entry_root_dir = ttk.Entry(top_frame, textvariable=self.root_dir_var, width=48)
        self.entry_root_dir.pack(side=tk.LEFT, padx=(5, 5), fill=tk.X, expand=True)
        ttk.Button(top_frame, text="参照...", command=self.on_browse_root_dir).pack(side=tk.LEFT)
        ttk.Button(top_frame, text="再スキャン", command=self.refresh).pack(side=tk.LEFT, padx=(5, 0))

        list_frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        list_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("folder_name", "modified_at", "size")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings")
        self.tree.heading("folder_name", text="フォルダ名")
        self.tree.heading("modified_at", text="最終更新日時")
        self.tree.heading("size", text="サイズ")
        self.tree.column("folder_name", width=340, anchor=tk.W)
        self.tree.column("modified_at", width=160, anchor=tk.W)
        self.tree.column("size", width=90, anchor=tk.E)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Double-1>", lambda e: self.on_switch())

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="このDBに切り替える", command=self.on_switch).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="削除", command=self.on_delete).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT)

    def on_browse_root_dir(self):
        selected = filedialog.askdirectory(
            title="共有フォルダの親ディレクトリを選択",
            initialdir=self.root_dir_var.get() or os.path.expanduser("~"),
            parent=self,
        )
        if not selected:
            return
        self.root_dir_var.set(selected)
        self.refresh()

    def refresh(self, show_empty_message: bool = True):
        """
        root_dir_varの内容でスキャンし、一覧を更新する。有効なパスであれば
        services.app_settings_service.save_shared_db_root()で永続化する
        （次回この画面を開いた際に自動的に再スキャンできるようにするため）。

        show_empty_message：スキャン結果が0件だった場合に情報ダイアログを
        出すかどうか。__init__()からの自動スキャン時はFalse（前回指定した
        フォルダが一時的に見えないだけの場合等に、開いた瞬間ダイアログが
        出るのを避けるため）、ユーザーが明示的に「参照」「再スキャン」を
        押した場合はTrue。
        """
        root_dir = self.root_dir_var.get().strip()
        if not root_dir:
            if show_empty_message:
                messagebox.showwarning("警告", "親ディレクトリを指定してください。", parent=self)
            return

        if not os.path.isdir(root_dir):
            if show_empty_message:
                messagebox.showerror(
                    "エラー", f"指定されたフォルダが見つかりません：\n{root_dir}", parent=self,
                )
            self._populate([])
            return

        save_shared_db_root(root_dir)
        rows = scan_shared_db_folders(root_dir)
        self._populate(rows)

        if not rows and show_empty_message:
            messagebox.showinfo(
                "情報", "このフォルダ配下にデータベース（inventory.db）が見つかりませんでした。",
                parent=self,
            )

    def _populate(self, rows):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in rows:
            # iidにinventory.dbのフルパスをそのまま使うことで、選択行から
            # 別途パスを組み立て直す必要をなくす（on_switch()参照）。
            self.tree.insert("", tk.END, iid=row["path"], values=(
                row["folder_name"],
                row["modified_at"] or "-",
                _format_size(row["size_bytes"]),
            ))

    def on_switch(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("警告", "切り替え先を選択してください。", parent=self)
            return

        path = sel[0]
        if self._switch_callback(path):
            self.destroy()

    def on_delete(self):
        """
        一覧で選択中のDBを削除する（ui.db_delete_helper.confirm_and_delete_database()、
        安全対策込み）。削除後は一覧を再取得する（この画面自体は閉じない。
        「このDBに切り替える」と異なり、削除は続けて別の行にも行いたい場合が
        あるため）。
        """
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("警告", "削除対象を選択してください。", parent=self)
            return

        path = sel[0]
        if confirm_and_delete_database(self, path, current_worker=self.current_worker):
            self.refresh(show_empty_message=False)
