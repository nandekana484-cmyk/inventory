# ui/daily_drawdown_window.py
import csv
import os
import tempfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from services.lot_status_history import get_daily_drawdown

DRAWDOWN_HEADERS = [
    "ロットNo", "基板名", "ファイルNo", "面", "その日の引落", "累計引落", "仕掛", "未生産",
]


def _side_label(production_side):
    if production_side is None:
        return ""
    return f"面{production_side}"


def _drawdown_row_to_values(row):
    return [
        row["lot_no"],
        row["board_name"] or "",
        row["setup_file_no"],
        _side_label(row["production_side"]),
        f"{row['daily_drawdown']:.0f}",
        f"{row['cumulative_lot_completed']:.0f}",
        f"{row['surplus_qty']:.0f}",
        f"{row['not_produced_qty']:.0f}",
    ]


class DailyDrawdownWindow(tk.Toplevel):
    """
    日々の引落一覧：指定日に、models.lot_status_history（実績の登録・修正の
    たびに記録されるロット状態の履歴）上でロット・ファイルNo単位の引落
    （lot_completed）が前日以前と比べて増えた行だけを一覧表示する
    （services.lot_status_history.get_daily_drawdown()参照）。

    日報・月報とは異なり、production_daily.report_date（ユーザーが指定する
    任意の日付、後から過去日付として登録し直すこともできる）ではなく、
    lot_status_history.recorded_at（実際に書き込まれた壁時計時刻、後から
    遡って変わらない）を基準にする点が異なる（services/lot_status_history.py
    モジュールdocstring参照）。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.target_date = datetime.now().strftime("%Y-%m-%d")
        self.report_rows = []

        self.title("日々の引落一覧")
        self.geometry("900x520")

        date_frame = ttk.Frame(self, padding=10)
        date_frame.pack(fill=tk.X)

        ttk.Label(date_frame, text="対象日：").pack(side=tk.LEFT, padx=5)
        self.date_entry = DateEntry(date_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(date_frame, text="集計", command=self.on_aggregate).pack(side=tk.LEFT, padx=10)

        self.lbl_count = ttk.Label(date_frame, text="")
        self.lbl_count.pack(side=tk.LEFT, padx=10)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("lot_no", "board_name", "file_no", "side", "daily_drawdown",
                "cumulative", "surplus_qty", "not_produced_qty")
        headers = dict(zip(cols, DRAWDOWN_HEADERS))
        widths = {
            "lot_no": 110, "board_name": 170, "file_no": 90, "side": 50,
            "daily_drawdown": 90, "cumulative": 90, "surplus_qty": 80, "not_produced_qty": 80,
        }
        left_aligned = {"lot_no", "board_name", "file_no", "side"}

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

        self.on_aggregate()

    def on_aggregate(self):
        target_date = self.date_entry.get()
        try:
            self.report_rows = get_daily_drawdown(target_date)
        except Exception as e:
            messagebox.showerror("エラー", f"集計に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        self.target_date = target_date
        self.title(f"日々の引落一覧（{target_date}）")
        self.lbl_count.config(text=f"{len(self.report_rows)}件")

        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in self.report_rows:
            self.tree.insert("", tk.END, values=_drawdown_row_to_values(row))

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "表示中のデータがありません。", parent=self.winfo_toplevel())
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile=f"daily_drawdown_{self.target_date.replace('-', '')}.csv",
            filetypes=[("CSV files", "*.csv")], parent=self.winfo_toplevel(),
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(DRAWDOWN_HEADERS)
                for row in self.report_rows:
                    writer.writerow(_drawdown_row_to_values(row))
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを出力しました：\n{save_path}", parent=self.winfo_toplevel())
