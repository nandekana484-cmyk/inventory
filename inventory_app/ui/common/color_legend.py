"""
一覧の色の意味を1行で示す凡例（D-181）。色の値は ui/common/highlight_colors.py の定数を、行の色付け（tag_configure）と同じものを渡す。
"""
import tkinter as tk
from tkinter import ttk


def build_color_legend(parent, entries, title="色の意味："):
    """
    entries: [(説明, 背景色 or None, 文字色 or None), ...]。背景色が None なら背景は付けない（文字色だけの凡例）。
    戻り値は凡例の Frame（呼び出し元で pack する）。見本のラベルは frame.swatches に (説明, Label) で持つ（テストで色を確かめるため）。
    """
    frame = ttk.Frame(parent)
    ttk.Label(frame, text=title, foreground="gray").pack(side=tk.LEFT)
    frame.swatches = []
    for text, background, foreground in entries:
        options = {"text": text, "padx": 4, "relief": tk.GROOVE, "borderwidth": 1}
        if background:
            options["background"] = background
        if foreground:
            options["foreground"] = foreground
        label = tk.Label(frame, **options)
        label.pack(side=tk.LEFT, padx=(0, 3))
        frame.swatches.append((text, label))
    return frame
