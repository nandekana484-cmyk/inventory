"""登録待ち一覧の「登録済みリスト」（対象外一覧）の表示・戻す・CSV 出力。ProductionImportStagingWindow の mixin。
production_import_staging_window.py から移した（関数の中身は変えていない。D-190）。
"""
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from services.production_import_service import EXCLUSION_REASON_LABELS
from models.production_import_staging import upsert_pending_csv_import_row
from ui.common.window_utils import center_window
from services.staging_eligibility import _load_staged_rows_from_db


class StagingAlreadyRegisteredMixin:
    def add_already_registered_rows(self, rows):
        """
        新しい取込で見つかった「登録済み」行を追記する。既に開いている一覧に、もう一度取り込んだときに呼ぶ
        （lift() だけでは今回の判定結果が失われる）。件数表示と詳細ウインドウも更新する。
        """
        if not rows:
            return
        self._already_registered_rows.extend(rows)
        self._update_status_label()
        if self._already_registered_window is not None and self._already_registered_window.winfo_exists():
            self._populate_already_registered_tree()

    def on_show_already_registered_list(self):
        """「登録済みリストを表示」ボタン。開いていれば前面に出すだけで、多重に開かない。"""
        if self._already_registered_window is not None and self._already_registered_window.winfo_exists():
            self._already_registered_window.lift()
            return

        window = tk.Toplevel(self)
        window.title("実績CSV取込：対象外一覧（登録済み／数量0）")
        window.geometry("980x400")
        center_window(window, self)
        window.transient(self)
        # 親が最小化中だと transient のウインドウが表示されないので、先に元に戻す（UI_WORKFLOW_FIXES_NOTES.md グループO）。
        if self.state() == "iconic":
            self.deiconify()

        ttk.Label(
            window,
            text=(
                "通常のステージング一覧には表示されなかった行です（理由は「理由」列を参照）。\n"
                "右クリックで通常の一覧に戻して訂正できます（複数選択中は選択中の全行が対象）。"
            ),
            foreground="gray", padding=(10, 10, 10, 0),
        ).pack(anchor=tk.W)

        action_frame = ttk.Frame(window, padding=(10, 5, 10, 10))
        action_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(
            action_frame, text="対象外一覧をCSV出力", command=self.on_export_already_registered_csv,
        ).pack(side=tk.LEFT)

        tree_frame = ttk.Frame(window, padding=(10, 5, 10, 0))
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = (
            "lot_no", "product_name", "daily_qty", "report_date",
            "matched_kitting_list_no", "existing_qty", "reason",
        )
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="extended")
        tree.heading("lot_no", text="ロットNo")
        tree.heading("product_name", text="製品名")
        tree.heading("daily_qty", text="CSVの実績数")
        tree.heading("report_date", text="払い出し日（参考）")
        tree.heading("matched_kitting_list_no", text="一致した計画（キッティングリストNo）")
        tree.heading("existing_qty", text="登録済みの数量")
        tree.heading("reason", text="対象外の理由")
        tree.column("lot_no", width=100, anchor=tk.W)
        tree.column("product_name", width=180, anchor=tk.W)
        tree.column("daily_qty", width=90, anchor=tk.E)
        tree.column("report_date", width=120, anchor=tk.CENTER)
        tree.column("matched_kitting_list_no", width=170, anchor=tk.W)
        tree.column("existing_qty", width=100, anchor=tk.E)
        tree.column("reason", width=140, anchor=tk.W)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        tree.configure(yscrollcommand=vsb.set)
        vsb.configure(command=tree.yview)

        tree.bind("<Button-3>", self._on_already_registered_right_click)

        self._already_registered_window = window
        self._already_registered_tree = tree
        self._populate_already_registered_tree()

    def _populate_already_registered_tree(self):
        """登録済みリストの詳細ウインドウの Treeview を作り直す（追記・戻す操作の後に呼ぶ）。"""
        tree = self._already_registered_tree
        for item in tree.get_children():
            tree.delete(item)
        self._already_registered_by_iid = {}

        for entry in self._already_registered_rows:
            iid = tree.insert("", tk.END, values=(
                entry.get("lot_no", ""),
                entry.get("product_name", ""),
                entry.get("daily_qty", ""),
                entry.get("report_date") or "",
                entry.get("matched_kitting_list_no") or "",
                entry.get("existing_qty") if entry.get("existing_qty") is not None else "",
                EXCLUSION_REASON_LABELS.get(entry.get("reason"), entry.get("reason") or ""),
            ))
            self._already_registered_by_iid[iid] = entry

    def _on_already_registered_right_click(self, event):
        """
        登録済みリスト詳細ウインドウの右クリックメニュー。「通常の一覧に戻す」の1操作だけを出す
        （戻した後は、通常の一覧の「不一致として除外」「候補なしとする」で対応できるため）。複数選択時の扱いは _on_right_click() と同じ。
        """
        tree = self._already_registered_tree
        clicked_iid = tree.identify_row(event.y)
        if not clicked_iid:
            return

        current_selection = tree.selection()
        if len(current_selection) > 1 and clicked_iid in current_selection:
            target_iids = list(current_selection)
        else:
            tree.selection_set(clicked_iid)
            target_iids = [clicked_iid]

        menu = tk.Menu(self._already_registered_window, tearoff=0)
        label = "通常の一覧に戻す（訂正する）"
        if len(target_iids) > 1:
            label += f"（選択中の{len(target_iids)}件）"
        menu.add_command(label=label, command=lambda: self._revert_already_registered_rows(target_iids))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _revert_already_registered_rows(self, iids):
        """
        登録済みリストの行を保留行として DB に書き戻し、登録待ち一覧を全件読み直す（まれな訂正操作なので、個別に差し込まない）。
        """
        entries = [self._already_registered_by_iid[iid] for iid in iids if iid in self._already_registered_by_iid]
        if not entries:
            return

        count = len(entries)
        lot_no_preview = "、".join(e.get("lot_no", "") for e in entries[:5])
        if count > 5:
            lot_no_preview += " 他"
        if not messagebox.askyesno(
            "通常の一覧に戻す",
            f"選択中の{count}件（ロットNo: {lot_no_preview}）を通常の登録待ち一覧に戻します。"
            "よろしいですか？",
            parent=self._already_registered_window,
        ):
            return

        for entry in entries:
            upsert_pending_csv_import_row({
                "csv_row_no": entry.get("csv_row_no"),
                "lot_no": entry["lot_no"],
                "product_name": entry["product_name"],
                "daily_qty": entry["daily_qty"],
                "report_date": entry.get("report_date"),
                "worker_id": entry.get("worker_id"),
            }, import_batch_id=entry.get("import_batch_id"))
            self._already_registered_rows.remove(entry)

        # 戻した行を候補付きで表示するには再照合が必要なので、_load_staged_rows_from_db() で全件読み直す。
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._row_by_iid = {}
        for row in _load_staged_rows_from_db():
            self._insert_staging_row(row)

        self._populate_already_registered_tree()
        self._update_status_label()

    def on_export_already_registered_csv(self):
        """
        登録済みリストを CSV 出力する。DB に対応する行が無いので、出力後に一覧や DB から消すものは無い（出力後も「戻す」ができる）。
        """
        if not self._already_registered_rows:
            messagebox.showinfo("対象外一覧", "対象外と判定された行はありません。", parent=self._already_registered_window)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_excluded_rows.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self._already_registered_window,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "lot_no", "product_name", "daily_qty", "report_date",
                    "matched_kitting_list_no", "existing_qty", "reason",
                ])
                for entry in self._already_registered_rows:
                    writer.writerow([
                        entry.get("lot_no", ""),
                        entry.get("product_name", ""),
                        entry.get("daily_qty", ""),
                        entry.get("report_date") or "",
                        entry.get("matched_kitting_list_no") or "",
                        entry.get("existing_qty") if entry.get("existing_qty") is not None else "",
                        EXCLUSION_REASON_LABELS.get(entry.get("reason"), entry.get("reason") or ""),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self._already_registered_window)
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self._already_registered_window)
