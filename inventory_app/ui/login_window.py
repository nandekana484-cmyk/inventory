import tkinter as tk
from tkinter import ttk, messagebox
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from models.workers import get_active_workers
from ui.main_window import MainWindow

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

    def on_login(self):
        selected_name = self.worker_combobox.get()
        if not selected_name:
            messagebox.showwarning("警告", "作業者を選択してください。")
            return

        selected_worker = self.worker_dict[selected_name]
        self.destroy()  # ログイン画面を閉じる
        
        # メイン画面を起動
        app = MainWindow(current_worker=selected_worker)
        app.mainloop()
