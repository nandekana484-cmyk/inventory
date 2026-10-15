"""
面2（反対側の面）を登録したとき、連動で面1の既存の実績が置き換わる前の確認（D-126）。
置き換えの判定は services.production_service.preview_opposite_side_overwrites()。ここは文言とダイアログだけ。
"""
import tkinter as tk
from tkinter import ttk

from ui.common.window_utils import center_window

# 一括登録の確認の戻り値
BULK_REGISTER_ALL = "all"
BULK_SKIP_REPLACED = "skip_replaced"
BULK_CANCEL = "cancel"


def _short_date(value):
    """"2026-10-02" → "10/2"。解釈できなければそのまま。"""
    try:
        _year, month, day = str(value).split("-")
        return f"{int(month)}/{int(day)}"
    except (TypeError, ValueError):
        return str(value)


def format_old_records(change):
    return "、".join(f"{qty:g}（{_short_date(d)}）" for d, qty in change["old"])


def format_opposite_change(change):
    """例：「面1の実績が 800（10/2）→ 700（10/6）に変わります」"""
    return (
        f"面{change['production_side']}の実績が {format_old_records(change)} → "
        f"{change['new_qty']:g}（{_short_date(change['new_date'])}）に変わります"
    )


def ask_bulk_opposite_overwrite(parent, rows):
    """
    一括登録の前に、置き換えが起きる行を一覧で1回だけ確認する。
    rows: [{"lot_no", "kitting_list_no"（登録する計画）, "change"（preview の値）}, ...]。
    戻り値: BULK_REGISTER_ALL（すべて登録）/ BULK_SKIP_REPLACED（置き換えが起きる行を除いて登録）/ BULK_CANCEL（中止）。
    """
    # 親が最小化中だと transient のダイアログが表示されず、入力だけを握ったままになる（UI_WORKFLOW_FIXES_NOTES.md）。
    if parent.state() == "iconic":
        parent.deiconify()
    dialog = tk.Toplevel(parent)
    dialog.title("面1の実績の置き換えの確認")
    dialog.transient(parent)
    dialog.grab_set()
    result = {"value": BULK_CANCEL}

    ttk.Label(
        dialog, padding=(15, 15, 15, 5), justify=tk.LEFT,
        text=(
            f"次の{len(rows)}件は、登録すると反対側の面の既存の実績が置き換わります。\n"
            "置き換えた実績は元に戻せません。"
        ),
    ).pack(fill=tk.X)

    cols = ("lot_no", "kitting_list_no", "before", "after")
    frame = ttk.Frame(dialog, padding=(15, 0))
    frame.pack(expand=True, fill=tk.BOTH)
    vsb = ttk.Scrollbar(frame, orient="vertical")
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    tree = ttk.Treeview(frame, columns=cols, show="headings", height=min(max(len(rows), 3), 12),
                        yscrollcommand=vsb.set)
    vsb.configure(command=tree.yview)
    for col, text, width in (("lot_no", "ロットNo", 100), ("kitting_list_no", "登録する計画No", 170),
                             ("before", "変更前", 160), ("after", "変更後", 110)):
        tree.heading(col, text=text)
        tree.column(col, width=width, anchor=tk.W)
    for row in rows:
        change = row["change"]
        tree.insert("", tk.END, values=(
            row["lot_no"], row["kitting_list_no"],
            f"面{change['production_side']}：{format_old_records(change)}",
            f"{change['new_qty']:g}（{_short_date(change['new_date'])}）",
        ))
    tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

    btn_frame = ttk.Frame(dialog, padding=15)
    btn_frame.pack(fill=tk.X)

    def choose(value):
        result["value"] = value
        dialog.destroy()

    ttk.Button(btn_frame, text="中止", command=lambda: choose(BULK_CANCEL)).pack(side=tk.RIGHT)
    ttk.Button(btn_frame, text="置き換えが起きる行を除いて登録",
               command=lambda: choose(BULK_SKIP_REPLACED)).pack(side=tk.RIGHT, padx=5)
    ttk.Button(btn_frame, text="すべて登録", command=lambda: choose(BULK_REGISTER_ALL)).pack(side=tk.RIGHT)
    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(BULK_CANCEL))
    dialog.bind("<Escape>", lambda e: choose(BULK_CANCEL))

    center_window(dialog, parent)
    dialog.wait_window()
    return result["value"]
