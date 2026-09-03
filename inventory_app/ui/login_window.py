import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.workers import get_active_workers
from ui.main_window import MainWindow
from ui.worker_management_window import WorkerManagementWindow

class LoginWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("部品在庫管理アプリ - ログイン")
        self.geometry("350x220")
        self.resizable(False, False)

        frame = ttk.Frame(self, padding=20)
        frame.pack(expand=True, fill=tk.BOTH)

        ttk.Label(frame, text="作業者ログイン", font=("Helvetica", 14, "bold")).pack(pady=(0, 15))

        ttk.Label(frame, text="作業者を選択してください:").pack(anchor=tk.W)

        # DBから作業者一覧を取得
        self.workers = get_active_workers()
        self.worker_dict = {w['name']: w for w in self.workers}

        self.worker_combobox = ttk.Combobox(
            frame, 
            values=list(self.worker_dict.keys()), 
            state="readonly"
        )
        self.worker_combobox.pack(fill=tk.X, pady=10)

        if self.workers:
            self.worker_combobox.current(0)

        btn_login = ttk.Button(frame, text="ログイン", command=self.on_login)
        btn_login.pack(pady=10)

        btn_worker_management = ttk.Button(
            frame, text="作業者を管理（新規登録・編集）", command=self.open_worker_management
        )
        btn_worker_management.pack(pady=(0, 5))

    def open_worker_management(self):
        """
        ログイン前でも作業者の新規登録・編集ができるようにする。
        workers が0件（初回起動時等）でもログイン不能で行き詰まらないようにするための入口。
        """
        win = WorkerManagementWindow(self)
        self.wait_window(win)
        self.refresh_workers()

    def refresh_workers(self):
        """作業者管理画面での変更をログイン画面のコンボボックスへ反映する。"""
        self.workers = get_active_workers()
        self.worker_dict = {w['name']: w for w in self.workers}
        self.worker_combobox['values'] = list(self.worker_dict.keys())
        if self.workers:
            self.worker_combobox.current(0)
        else:
            self.worker_combobox.set('')

    def on_login(self):
        selected_name = self.worker_combobox.get()
        if not selected_name:
            messagebox.showwarning("警告", "作業者を選択してください。", parent=self.winfo_toplevel())
            return

        selected_worker = self.worker_dict[selected_name]
        self.destroy()  # ログイン画面を閉じる

        # メイン画面を起動
        # 起動時のDBロック取得に失敗した場合、MainWindow.__init__()内で
        # 自身をdestroy()して起動を中断する（ui/main_window.py参照）。destroy()済みの
        # Tkルートはインタプリタごと破棄されるため、その後winfo_exists()等の
        # Tk操作を呼ぶこと自体がTclErrorになる。destroy()前に設定される
        # 素のPython属性 _lock_acquired で判定する。
        app = MainWindow(current_worker=selected_worker)
        if app._lock_acquired:
            app.mainloop()
