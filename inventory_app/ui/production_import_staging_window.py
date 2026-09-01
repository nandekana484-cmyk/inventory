# ui/production_import_staging_window.py
"""
実績CSV取込のステージング一覧（「確認・選択・転記」方式）。

services.production_import_service.parse_production_csv_for_staging() の
結果（DBへは未登録のCSV行一覧）を表示する。行をダブルクリックすると、
呼び出し元（ui.kitting_production_entry.KittingProductionEntryWindow）に
選択を委譲する。実際の計画選択・実績記入欄への転記・登録確認ダイアログ〜登録
処理は、呼び出し元の既存の仕組み（search_plan()・_start_registration()）に
そのまま任せる（本ウインドウはCSV行の一覧表示・候補への引き渡しのみを担い、
DBアクセスは一切行わない）。

登録完了の検知はコールバック方式：呼び出し元が実際の登録に成功した時点で、
本ウインドウから渡されたremove_callbackを呼んでもらうことで、該当行を
一覧から消す（本ウインドウ側からDBの状態を能動的にポーリングはしない）。
"""
import csv

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from services.production_import_service import STAGING_STATUS_LABELS

# "候補なし"（find_matching_plan_items()の候補が0件）行をCSV出力する際の理由欄。
# services.production_import_service.import_production_csv()のunmatched理由
# 文言と揃えている。
REASON_NO_CANDIDATES = "計画が見つからない（該当lot_noの計画なし）"


class ProductionImportStagingWindow(tk.Toplevel):
    def __init__(self, parent, staged_rows, on_row_confirmed):
        """
        staged_rows：parse_production_csv_for_staging()の"rows"（辞書のリスト。
        各要素は"lot_no"/"product_name"/"daily_qty"/"report_date"/"worker_id"/
        "candidates"/"matched"/"status"を持つ）。

        on_row_confirmed(row, remove_callback)：一覧の行がダブルクリックされ、
        candidatesが1件以上ある場合に呼ばれるコールバック。呼び出し元は
        候補選択ダイアログの表示〜計画確定〜実績記入欄への転記までを行い、
        実際の登録（既存の_start_registration()フロー）が完了した時点で
        remove_callback()を呼び、この一覧から該当行を消してもらうこと。
        candidatesが0件（"no_candidates"）の行は、on_row_confirmed()を呼ばず
        本ウインドウ内で直接「候補なし」メッセージを表示するだけに留める。
        """
        super().__init__(parent)
        self.title("実績CSV取込：登録待ち一覧")
        self.geometry("820x420")
        self.on_row_confirmed = on_row_confirmed
        self._row_by_iid = {}
        # "no_candidates"（候補なし＝登録不可）と判定された行を、一覧から消えても
        # 参照できるよう別途保持しておく（CSV出力ボタン用）。一覧本体
        # （self._row_by_iid）は登録完了のたびに行が消えていくが、こちらは
        # ウインドウを閉じるまで消さない。
        self._unregistrable_rows = [
            row for row in staged_rows if row.get("status") == "no_candidates"
        ]

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("lot_no", "product_name", "report_date", "worker_id", "daily_qty", "status")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("lot_no", text="ロットNo")
        self.tree.heading("product_name", text="製品名")
        self.tree.heading("report_date", text="払い出し日（参考）")
        self.tree.heading("worker_id", text="作業者")
        self.tree.heading("daily_qty", text="実績数")
        self.tree.heading("status", text="状態")
        self.tree.column("lot_no", width=100, anchor=tk.W)
        self.tree.column("product_name", width=180, anchor=tk.W)
        self.tree.column("report_date", width=120, anchor=tk.CENTER)
        self.tree.column("worker_id", width=90, anchor=tk.W)
        self.tree.column("daily_qty", width=70, anchor=tk.E)
        self.tree.column("status", width=160, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        for row in staged_rows:
            iid = self.tree.insert("", tk.END, values=(
                row.get("lot_no", ""),
                row.get("product_name", ""),
                row.get("report_date") or "",
                row.get("worker_id", ""),
                row.get("daily_qty", ""),
                STAGING_STATUS_LABELS.get(row.get("status"), row.get("status", "")),
            ))
            self._row_by_iid[iid] = row

        self.tree.bind("<Double-1>", self._on_double_click)

        ttk.Label(
            self,
            text="行をダブルクリックすると計画の候補選択に進みます。登録が完了すると一覧から自動的に消えます。",
            foreground="gray",
        ).pack(anchor=tk.W, padx=10)

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="登録不可リストをCSV出力", command=self.on_export_unregistrable_csv).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="閉じる", command=self._on_close).pack(side=tk.LEFT, padx=5)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        """
        まだ一覧に残っている（＝未登録の）行がある場合は確認ダイアログを表示し、
        承認された場合のみ閉じる。一覧が空（全行登録完了）の場合は確認なしで
        そのまま閉じる。
        """
        if self._row_by_iid:
            if not messagebox.askyesno(
                "確認",
                "まだ登録していない項目があります。閉じてもよろしいですか？",
                parent=self,
            ):
                return
        self.destroy()

    def on_export_unregistrable_csv(self):
        """
        登録不可（"no_candidates"＝該当lot_noの計画が見つからなかった）行を
        CSV出力する。該当行が1件も無い場合は出力せずメッセージのみ表示する。
        """
        if not self._unregistrable_rows:
            messagebox.showinfo("登録不可リスト", "登録不可の行はありません。", parent=self)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_unregistrable.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._unregistrable_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        REASON_NO_CANDIDATES,
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        if not row.get("candidates"):
            messagebox.showinfo(
                "候補なし",
                f"ロットNo. {row.get('lot_no')} に該当する計画が見つかりません。",
                parent=self,
            )
            return

        def remove_callback():
            if iid in self._row_by_iid:
                self.tree.delete(iid)
                del self._row_by_iid[iid]

        self.on_row_confirmed(row, remove_callback)
