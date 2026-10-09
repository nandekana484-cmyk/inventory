# ui/daily_drawdown_window.py
import csv
import os
import tempfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from services.lot_status_history import get_daily_drawdown, get_drawdown_date_range
from ui.window_utils import center_window

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
    日々の引落一覧：指定日（対象日）までの実績（production_daily.report_date、
    CSV由来の実績は払出し日、手入力の実績は入力した日）だけを使って計算した
    ロットの完成数が、対象日の前日までの完成数と比べて増えた行だけを一覧表示
    する（services.lot_status_history.get_daily_drawdown()参照）。

    2026-10-09、以前の方式（models.lot_status_history、実績の登録・修正の
    たびに記録される壁時計時刻recorded_atを基準に前日以前と比較する方式）
    から、本方式（report_dateを基準に、対象日・前日それぞれの時点の完成数を
    都度再計算する方式）へ変更した（D-115での評価、D-116・D-29改訂参照）。
    対象日は「アプリに登録した日」ではなく「実績の日付（払出し日・入力日）」
    になった。もとの実績・構成基板数マスタ・計画のいずれかが変われば、
    過去の対象日の結果も再計算のたびに変わりうる（もとのデータに変更が無い
    限り、同じ対象日の結果は何度計算しても変わらない）。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.report_rows = []

        first_date, last_date = get_drawdown_date_range()
        initial_date = last_date or datetime.now().strftime("%Y-%m-%d")
        self.target_date = initial_date

        self.title("日々の引落一覧")
        self.geometry("900x560")
        center_window(self, parent)

        date_frame = ttk.Frame(self, padding=10)
        date_frame.pack(fill=tk.X)

        ttk.Label(date_frame, text="対象日：").pack(side=tk.LEFT, padx=5)
        self.date_entry = DateEntry(date_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.date_entry.pack(side=tk.LEFT, padx=5)
        # 初期値を「引落のある最後の日付」にする（2026-10-09追加、D-116）。
        # 以前の実装は、DateEntry自身の既定値（今日の日付）のまま特に上書き
        # していなかった（self.target_date = datetime.now()...は設定していた
        # ものの、on_aggregate()内でself.date_entry.get()の値（今日の日付）に
        # 即座に上書きされており、実際には使われていなかった）。
        try:
            self.date_entry.set_date(datetime.strptime(initial_date, "%Y-%m-%d").date())
        except (ValueError, TypeError):
            pass

        ttk.Button(date_frame, text="集計", command=self.on_aggregate).pack(side=tk.LEFT, padx=10)

        self.lbl_count = ttk.Label(date_frame, text="")
        self.lbl_count.pack(side=tk.LEFT, padx=10)

        # 対象日の意味の説明（2026-10-09追加、D-116）。
        explanation_frame = ttk.Frame(self, padding=(10, 0))
        explanation_frame.pack(fill=tk.X)
        ttk.Label(
            explanation_frame,
            text="対象日は実績の日付（CSV由来の実績は払出し日、手入力の実績は入力した日）です。",
            foreground="gray",
        ).pack(side=tk.LEFT, padx=5)

        # 引落のあるデータの日付範囲（対象日選択の手がかり、2026-10-09追加、D-116）。
        range_frame = ttk.Frame(self, padding=(10, 0, 10, 5))
        range_frame.pack(fill=tk.X)
        if first_date and last_date:
            range_text = f"データの日付範囲：{first_date} 〜 {last_date}"
        else:
            range_text = "データの日付範囲：（実績が登録されていません）"
        ttk.Label(range_frame, text=range_text, foreground="gray").pack(side=tk.LEFT, padx=5)

        # --- 下部に配置するボタン行を先にpackし、領域を確保する（2026-10-08
        # 追加、スクロールバー新設に伴う既知のpack順序の対策。
        # ui/production_import_staging_window.py等の既存の対策と同じ理由：
        # Treeview（expand=True, fill=BOTH）を先にpackすると、後からpackする
        # スクロールバー・ボタン行の領域が残らない）。
        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

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
            # stretch=False（2026-10-08追加）：既定のstretch=Trueのままだと
            # 列幅が表示領域に合わせて自動伸縮し、横スクロールバーを追加しても
            # 実質動かせる余地が生まれない（画面修正5項目§5・D-99と同じ理由）。
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E, stretch=False)

        # 縦・横スクロールバー（2026-10-08追加）。スクロールバーをTreeviewより
        # 先にpackする（既存の他一覧と同じ順序、同じ理由：同じtree_frame内の
        # 兄弟同士でも、Treeview（expand=True, fill=BOTH）を先にpackすると
        # スクロールバー分の領域が残らない）。
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal")
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.configure(command=self.tree.yview)
        hsb.configure(command=self.tree.xview)

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
