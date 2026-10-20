"""
登録待ち一覧で、すでに実績がある計画へ別の行を登録しようとしたときの確認（D-176）。内容は services.production_entry_service.
duplicate_registration_info() の戻り値。ボタンは「合計して登録」「手入力で対応」「キャンセル」。
"""
import tkinter as tk
from tkinter import ttk

from ui.common.window_utils import center_window

DUPLICATE_SUM = "sum"
DUPLICATE_MANUAL = "manual"


def _short_date(value):
    """"2026-08-06" → "8/6"。解釈できなければそのまま。"""
    try:
        _year, month, day = str(value).split("-")
        return f"{int(month)}/{int(day)}"
    except (TypeError, ValueError):
        return str(value or "（日付なし）")


def build_duplicate_lines(info):
    """確認ダイアログに出す文言の行（テストでも使う）。"""
    lines = [
        "この計画には、すでに実績が登録されています。どちらが正しいかを確かめてください。",
        "",
        f"・計画：{info['kitting_list_no']}（ロットNo. {info['lot_no']}）",
    ]
    lines += [f"・今の実績：{_short_date(d)}　数量 {q:g}" for d, q in info["existing"]]
    lines += [
        f"・登録しようとしている行：{_short_date(info['row_date'])}　数量 {info['row_qty']:g}",
        "",
        f"「合計して登録」：合計 {info['total']:g}（日付 {_short_date(info['combined_date'])}）を、この計画の1件の実績にします。",
        "「手入力で対応」：登録せずに、この計画を生産実績入力で開きます（登録待ちの行は残します）。",
    ]
    return lines


def ask_duplicate_resolution(parent, info):
    """戻り値: DUPLICATE_SUM / DUPLICATE_MANUAL / None（キャンセル）。"""
    # 親が最小化中だと transient のダイアログが表示されず、入力だけを握ったままになる（UI_WORKFLOW_FIXES_NOTES.md）。
    if parent.state() == "iconic":
        parent.deiconify()
    dialog = tk.Toplevel(parent)
    dialog.title("同じ計画への登録")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)
    result = {"value": None}

    ttk.Label(dialog, text="\n".join(build_duplicate_lines(info)), justify=tk.LEFT, padding=15).pack()

    btn_frame = ttk.Frame(dialog, padding=(15, 0, 15, 15))
    btn_frame.pack(fill=tk.X)

    def choose(value):
        result["value"] = value
        dialog.destroy()

    ttk.Button(btn_frame, text="キャンセル", command=lambda: choose(None)).pack(side=tk.RIGHT)
    ttk.Button(btn_frame, text="手入力で対応", command=lambda: choose(DUPLICATE_MANUAL)).pack(side=tk.RIGHT, padx=5)
    ttk.Button(btn_frame, text="合計して登録", command=lambda: choose(DUPLICATE_SUM)).pack(side=tk.RIGHT)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    dialog.bind("<Escape>", lambda e: choose(None))

    center_window(dialog, parent)
    dialog.wait_window()
    return result["value"]
