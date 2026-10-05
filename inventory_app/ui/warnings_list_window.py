# ui/warnings_list_window.py
import tkinter as tk
from tkinter import ttk

from ui.window_utils import center_window


class WarningsListWindow(tk.Toplevel):
    """
    取込警告（CSVインポート等）の全件を確認できるスクロール可能な一覧ウィンドウ。

    ui/board_structure_import_window.py・ui/parts_attributes_import_window.py の
    両方から共通で使う。完了メッセージ（messagebox）には件数のみを表示し、
    詳細はこちらに分離する（10件を超える場合も含め、全件を確認できるようにするため）。
    """
    def __init__(self, parent, title, warnings):
        super().__init__(parent)
        self.title(title)
        self.geometry("640x400")
        center_window(self, parent)

        frame = ttk.Frame(self, padding=10)
        frame.pack(expand=True, fill=tk.BOTH)

        text = tk.Text(frame, wrap=tk.WORD)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(expand=True, fill=tk.BOTH)

        text.insert(tk.END, "\n".join(warnings))
        text.config(state=tk.DISABLED)

        ttk.Button(self, text="閉じる", command=self.destroy).pack(pady=(0, 10))
