# ui/wip_scrap_correction_window.py
import tkinter as tk
from tkinter import ttk, messagebox

from models.wip_scrap_records import (
    list_wip_scrap_records_by_kitting_no, update_wip_scrap_record, delete_wip_scrap_record,
)
from models.operation_log import log_operation


class WipScrapCorrectionWindow(tk.Toplevel):
    """
    仕掛（WIP）展開結果（models.wip_scrap_records）を96コード単位の明細行で個別に
    修正・削除するためのウィンドウ。ui.scrap_correction_window.ScrapCorrectionWindow
    （NG実績版）と同じ設計・同じ構造（ui.kitting_production_entry.
    ActualCorrectionWindowに由来）を踏襲する：対象の明細一覧をTreeviewで表示→
    行を選択→数量修正または削除→即座にUPDATE/DELETE（models.wip_scrap_records.
    update_wip_scrap_record()/delete_wip_scrap_record()、いずれも対象行の存在確認を
    しない単純な主キー指定UPDATE/DELETE）。

    ui.wip_expansion_window.WipExpansionWindow の仕掛一覧から、選択中の行
    （kitting_list_no・lot_no・production_side）を指定して開く。

    注意：wip_scrap_recordsは、仕掛展開画面の「展開」→「仕掛確定登録」操作
    （save_wip_scrap_records()）により、対象kitting_list_no・lot_no・
    production_side単位で全削除→再登録される「洗い替え」の対象でもある。
    そのため本画面での個別修正・削除は、対象の基板が再展開・再確定登録されると
    （新しいidの行に置き換わり）失われる。この注意を画面上に常時表示する。
    """
    def __init__(self, parent, kitting_list_no, lot_no=None, production_side=None, on_updated=None,
                 current_worker=None):
        super().__init__(parent)
        self.kitting_list_no = kitting_list_no
        self.lot_no = lot_no
        self.production_side = production_side
        self.on_updated = on_updated
        self.current_worker = current_worker or {}

        self.title(f"仕掛実績修正（{kitting_list_no}）")
        self.geometry("520x460")

        ttk.Label(
            self,
            text="注意：この画面での修正は、対象の計画が再展開・再登録されると失われる可能性があります。",
            foreground="red", wraplength=480, justify=tk.LEFT,
        ).pack(fill=tk.X, padx=15, pady=(15, 0))

        hist_frame = ttk.LabelFrame(self, text="仕掛実績明細（96コード単位）", padding=10)
        hist_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=(10, 5))

        cols = ("part_no", "qty", "production_side", "created_at")
        self.tree = ttk.Treeview(hist_frame, columns=cols, show="headings")
        self.tree.heading("part_no", text="96コード")
        self.tree.heading("qty", text="消費数量")
        self.tree.heading("production_side", text="面")
        self.tree.heading("created_at", text="登録日時")
        self.tree.column("part_no", width=170, anchor=tk.W)
        self.tree.column("qty", width=90, anchor=tk.E)
        self.tree.column("production_side", width=50, anchor=tk.CENTER)
        self.tree.column("created_at", width=140, anchor=tk.W)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_record)

        edit_frame = ttk.LabelFrame(self, text="選択した明細の修正", padding=10)
        edit_frame.pack(fill=tk.X, padx=15, pady=(5, 15))

        ttk.Label(edit_frame, text="消費数量：").pack(side=tk.LEFT, padx=5)
        self.entry_edit_qty = ttk.Entry(edit_frame, width=10)
        self.entry_edit_qty.pack(side=tk.LEFT, padx=5)

        self.btn_update = ttk.Button(edit_frame, text="修正", command=self.on_update,
                                      state=tk.DISABLED)
        self.btn_update.pack(side=tk.LEFT, padx=5)

        self.btn_delete = ttk.Button(edit_frame, text="削除", command=self.on_delete,
                                      state=tk.DISABLED)
        self.btn_delete.pack(side=tk.LEFT, padx=5)

        self.load_records()

    def load_records(self):
        """
        対象kitting_list_no・lot_no（・指定があればproduction_side）の明細行を
        再取得してTreeviewへ反映する。id列をそのままTreeviewのiidとして使う
        （ui.scrap_correction_window.ScrapCorrectionWindow.load_records()と同じ
        パターン）ことで、on_update()/on_delete()が選択行のidをそのまま
        int(sel[0])で取り出せる。
        """
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in list_wip_scrap_records_by_kitting_no(
            self.kitting_list_no, self.lot_no, self.production_side,
        ):
            self.tree.insert("", tk.END, iid=str(rec["id"]), values=(
                rec["part_no"], f"{rec['qty']:g}", rec["production_side"], rec["created_at"],
            ))
        self.entry_edit_qty.delete(0, tk.END)
        self.btn_update.config(state=tk.DISABLED)
        self.btn_delete.config(state=tk.DISABLED)

    def on_select_record(self, event):
        sel = self.tree.selection()
        if not sel:
            self.btn_update.config(state=tk.DISABLED)
            self.btn_delete.config(state=tk.DISABLED)
            return
        values = self.tree.item(sel[0], "values")
        self.entry_edit_qty.delete(0, tk.END)
        self.entry_edit_qty.insert(0, values[1])
        self.btn_update.config(state=tk.NORMAL)
        self.btn_delete.config(state=tk.NORMAL)

    def on_update(self):
        sel = self.tree.selection()
        if not sel:
            return
        record_id = int(sel[0])

        try:
            qty = float(self.entry_edit_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "消費数量には数値を入力してください。", parent=self.winfo_toplevel())
            return

        update_wip_scrap_record(record_id, qty)
        log_operation(
            self.current_worker.get("name", "unknown"),
            "仕掛実績修正",
            detail=f"{self.kitting_list_no}{f'（ロットNo. {self.lot_no}）' if self.lot_no else ''} / {qty:g}",
        )
        self._after_change()
        messagebox.showinfo("修正完了", "仕掛実績を修正しました。", parent=self.winfo_toplevel())

    def on_delete(self):
        sel = self.tree.selection()
        if not sel:
            return
        record_id = int(sel[0])

        if not messagebox.askyesno(
            "確認", "選択した仕掛実績を削除します。よろしいですか？", parent=self.winfo_toplevel(),
        ):
            return

        delete_wip_scrap_record(record_id)
        log_operation(
            self.current_worker.get("name", "unknown"),
            "仕掛実績削除",
            detail=f"{self.kitting_list_no}{f'（ロットNo. {self.lot_no}）' if self.lot_no else ''}",
        )
        self._after_change()
        messagebox.showinfo("削除完了", "仕掛実績を削除しました。", parent=self.winfo_toplevel())

    def _after_change(self):
        self.load_records()
        if self.on_updated:
            self.on_updated()
