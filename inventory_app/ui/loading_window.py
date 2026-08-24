# ui/loading_window.py
import tkinter as tk
from tkinter import ttk


class LoadingWindow(tk.Toplevel):
    """
    生成に時間のかかる画面を開く前に表示する「読み込み中」ウィンドウ。
    """
    def __init__(self, parent, message="生産実績画面を読み込んでいます…"):
        super().__init__(parent)
        self.title("読み込み中")
        self.geometry("300x120")
        self.resizable(False, False)

        frame = ttk.Frame(self, padding=20)
        frame.pack(expand=True, fill=tk.BOTH)

        ttk.Label(
            frame, text=message, anchor=tk.CENTER, justify=tk.CENTER, wraplength=260
        ).pack(expand=True, fill=tk.X, pady=(0, 10))

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill=tk.X)
        self.progress.start(10)

        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
