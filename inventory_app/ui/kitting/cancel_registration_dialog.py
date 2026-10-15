"""
実績の取り消しの確認ダイアログ（D-129）。内容は services.production_service.build_cancellation_preview() の戻り値。
ボタンは「取り消して登録待ちに戻す」「取り消すだけ」「キャンセル」。どちらで取り消しても、実績・反対側の面の実績・NG申告の取り消しは同じ。
"""
import tkinter as tk
from tkinter import ttk

from ui.common.window_utils import center_window

CANCEL_AND_RETURN = "return"
CANCEL_ONLY = "cancel_only"


def _side_label(side):
    return f"面{side}" if side else "面不明"


def build_cancellation_lines(preview):
    """確認ダイアログに出す文言の行（テストでも使う）。"""
    p = preview["primary"]
    lines = [
        "次の実績を取り消します。",
        f"・取り消す実績：ロットNo {p['lot_no']}　計画No {p['kitting_list_no']}　{_side_label(p['production_side'])}"
        f"　数量 {p['daily_qty']:g}　日付 {p['report_date']}",
    ]
    o = preview["opposite"]
    if o:
        lines.append(
            f"・連動して取り消す反対側の面の実績：計画No {o['kitting_list_no']}　{_side_label(o['production_side'])}"
            f"　数量 {o['daily_qty']:g}　日付 {o['report_date']}"
        )
    for d in preview["ng_declarations"]:
        lines.append(
            f"・同時に取り消す NG申告：計画No {d['kitting_list_no']}　{_side_label(d['production_side'])}　NG数量 {d['ng_qty']:g}"
        )
    r = preview["restored_row"]
    lines += [
        "",
        "「取り消して登録待ちに戻す」を選ぶと、登録待ち一覧に次の1行を作ります：",
        f"　ロットNo {r['lot_no']}　製品名 {r['product_name']}　数量 {r['daily_qty']:g}　日付 {r['report_date']}",
        "",
        "取り消した実績は元に戻せません。",
    ]
    return lines


def ask_cancel_registration(parent, preview):
    """戻り値: CANCEL_AND_RETURN / CANCEL_ONLY / None（キャンセル）。"""
    # 親が最小化中だと transient のダイアログが表示されず、入力だけを握ったままになる（UI_WORKFLOW_FIXES_NOTES.md）。
    if parent.state() == "iconic":
        parent.deiconify()
    dialog = tk.Toplevel(parent)
    dialog.title("実績の取り消し")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)
    result = {"value": None}

    ttk.Label(dialog, text="\n".join(build_cancellation_lines(preview)), justify=tk.LEFT, padding=15).pack()

    btn_frame = ttk.Frame(dialog, padding=(15, 0, 15, 15))
    btn_frame.pack(fill=tk.X)

    def choose(value):
        result["value"] = value
        dialog.destroy()

    ttk.Button(btn_frame, text="キャンセル", command=lambda: choose(None)).pack(side=tk.RIGHT)
    ttk.Button(btn_frame, text="取り消すだけ", command=lambda: choose(CANCEL_ONLY)).pack(side=tk.RIGHT, padx=5)
    btn_return = ttk.Button(btn_frame, text="取り消して登録待ちに戻す", command=lambda: choose(CANCEL_AND_RETURN))
    btn_return.pack(side=tk.RIGHT)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
    dialog.bind("<Escape>", lambda e: choose(None))

    center_window(dialog, parent)
    dialog.wait_window()
    return result["value"]
