"""
実績修正画面。完了済み計画も含め、production_daily の実績を修正・削除する。

リファクタリング段階2で ui/kitting_production_entry.py から移した（クラスの中身は変えていない）。
旧位置からは re-export しているので、既存の呼び出し側はそのまま動く。
"""
import tkinter as tk
from tkinter import ttk, messagebox

from services.production_service import (
    get_daily_history,
    build_linked_correction_preview,
    apply_linked_correction,
)
from models.operation_log import log_operation
from ui.window_utils import center_window


class ActualCorrectionWindow(tk.Toplevel):
    """
    完了済み計画も含め、production_daily の実績を修正・削除するためのウィンドウ。
    """
    def __init__(self, parent, kitting_list_no, lot_no, on_updated=None, current_worker=None):
        super().__init__(parent)
        self.kitting_list_no = kitting_list_no
        self.lot_no = lot_no
        self.on_updated = on_updated
        self.current_worker = current_worker or {}

        self.title(f"実績修正（{kitting_list_no}）")
        self.geometry("500x420")
        center_window(self, parent)

        hist_frame = ttk.LabelFrame(self, text="実績履歴", padding=10)
        hist_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=(15, 5))

        cols = ("report_date", "daily_qty", "worker_id")
        self.tree = ttk.Treeview(hist_frame, columns=cols, show="headings")
        self.tree.heading("report_date", text="日付")
        self.tree.heading("daily_qty", text="当日実績")
        self.tree.heading("worker_id", text="作業者")
        self.tree.column("report_date", width=150)
        self.tree.column("daily_qty", width=100, anchor=tk.E)
        self.tree.column("worker_id", width=150)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<<TreeviewSelect>>", self.on_select_history)

        edit_frame = ttk.LabelFrame(self, text="選択した実績の修正", padding=10)
        edit_frame.pack(fill=tk.X, padx=15, pady=(5, 15))

        ttk.Label(edit_frame, text="実績数：").pack(side=tk.LEFT, padx=5)
        self.entry_edit_qty = ttk.Entry(edit_frame, width=10)
        self.entry_edit_qty.pack(side=tk.LEFT, padx=5)

        self.btn_update = ttk.Button(edit_frame, text="修正", command=self.on_update,
                                      state=tk.DISABLED)
        self.btn_update.pack(side=tk.LEFT, padx=5)

        self.btn_delete = ttk.Button(edit_frame, text="削除", command=self.on_delete,
                                      state=tk.DISABLED)
        self.btn_delete.pack(side=tk.LEFT, padx=5)

        self.load_history()

    def load_history(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for rec in get_daily_history(self.kitting_list_no, self.lot_no):
            self.tree.insert("", tk.END, iid=str(rec["prod_log_id"]), values=(
                rec["report_date"], f"{rec['daily_qty']:.0f}", rec["worker_id"]
            ))
        self.entry_edit_qty.delete(0, tk.END)
        self.btn_update.config(state=tk.DISABLED)
        self.btn_delete.config(state=tk.DISABLED)

    def on_select_history(self, event):
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
        """
        実績を修正する。両面の計画があるロットでは、反対側の面の実績も同じ数量にそろえる（D-112）。
        連動の有無にかかわらず、確定前に必ず確認ダイアログを出す。
        """
        sel = self.tree.selection()
        if not sel:
            return
        prod_log_id = int(sel[0])

        try:
            daily_qty = float(self.entry_edit_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "実績数には数値を入力してください。", parent=self.winfo_toplevel())
            return

        preview = build_linked_correction_preview(prod_log_id, "update", new_daily_qty=daily_qty)

        if not self._show_correction_confirm_dialog(preview, "update"):
            return

        apply_linked_correction(preview, "update", self.current_worker.get("worker_id", "unknown"))
        log_operation(
            self.current_worker.get("name", "unknown"),
            "生産実績修正",
            detail=self._build_operation_log_detail(preview, "update"),
        )
        self.load_history()
        if self.on_updated:
            self.on_updated()
        messagebox.showinfo("修正完了", "実績を修正しました。", parent=self.winfo_toplevel())

    def on_delete(self):
        """
        実績の削除。両面の計画があるロットの場合、反対側の面の実績も連動して
        削除する（D-112、on_update()と同じ考え方）。
        """
        sel = self.tree.selection()
        if not sel:
            return
        prod_log_id = int(sel[0])

        preview = build_linked_correction_preview(prod_log_id, "delete")

        if not self._show_correction_confirm_dialog(preview, "delete"):
            return

        apply_linked_correction(preview, "delete", self.current_worker.get("worker_id", "unknown"))
        log_operation(
            self.current_worker.get("name", "unknown"),
            "生産実績削除",
            detail=self._build_operation_log_detail(preview, "delete"),
        )
        self.load_history()
        if self.on_updated:
            self.on_updated()
        messagebox.showinfo("削除完了", "実績を削除しました。", parent=self.winfo_toplevel())

    def _show_correction_confirm_dialog(self, preview, action):
        """
        修正・削除の確認ダイアログ（D-112）。反対側の実績の変化と、相手の特定方法（a/b）を示す。
        b で特定した場合や、連動前から両面が食い違っていた場合は「※」で強調する。
        """
        primary = preview["primary"]
        secondary = preview.get("secondary")

        action_label = "修正" if action == "update" else "削除"
        lines = [f"【{action_label}する実績】"]
        lines.append(f"　計画No：{primary['kitting_list_no']}（ロットNo. {primary['lot_no']}）")
        if action == "update":
            lines.append(f"　数量：{primary['old_daily_qty']:g} → {primary['new_daily_qty']:g}")
        else:
            lines.append(f"　数量：{primary['old_daily_qty']:g}（削除）")
        lines.append(f"　日付：{primary['report_date']}")

        if not preview["has_opposite_plan"]:
            lines.append("")
            lines.append("※ 反対側の面の計画が無いため、この面だけの変更です。")
        elif secondary is None or secondary.get("ambiguous"):
            lines.append("")
            lines.append(
                "※ 反対側の面の実績の相手を一意に特定できなかったため、"
                "この面だけを変更します（反対側は変更しません）。"
            )
        else:
            lines.append("")
            lines.append("【連動して変わる反対側の実績】")
            lines.append(f"　計画No：{secondary['kitting_list_no']}")
            if secondary["action"] == "insert":
                lines.append(f"　数量：（実績なし） → {secondary['new_daily_qty']:g}（新規登録）")
            elif secondary["action"] == "update":
                lines.append(f"　数量：{secondary['old_daily_qty']:g} → {secondary['new_daily_qty']:g}")
            elif secondary["action"] == "delete":
                lines.append(f"　数量：{secondary['old_daily_qty']:g} → （削除）")
            else:  # noop
                lines.append("　（実績が無いため、反対側は変更しません）")

            method_text = (
                "同じ数量・日付の実績から特定" if secondary["method"] == "a"
                else "登録時の自動入力と同じ方法（最も近い計画）で特定"
            )
            lines.append(f"　相手の特定方法：{method_text}")
            if secondary["method"] == "b":
                lines.append("※ 修正前の数量・日付と完全に一致する実績が無かったため、bの方法で特定しました。")
            if secondary.get("pre_existing_mismatch"):
                lines.append(
                    f"※ 連動前の反対側の実績（{secondary['old_daily_qty']:g}、"
                    f"{secondary['report_date']}）は、この面の変更前の値と一致していませんでした。"
                    "連動前から両面の実績が食い違っていた可能性があります。"
                )

        lines.append("")
        lines.append("この内容で確定しますか？")
        return messagebox.askyesno(f"{action_label}の確認", "\n".join(lines), parent=self.winfo_toplevel())

    @staticmethod
    def _build_operation_log_detail(preview, action):
        """操作履歴に、連動して変更した反対側の実績も分かる形で記録する（D-112）。"""
        primary = preview["primary"]
        action_label = "修正" if action == "update" else "削除"
        if action == "update":
            detail = f"{primary['kitting_list_no']} / ロットNo. {primary['lot_no']} / {primary['new_daily_qty']:g}"
        else:
            detail = f"{primary['kitting_list_no']} / ロットNo. {primary['lot_no']} / {action_label}"

        secondary = preview.get("secondary")
        if secondary and not secondary.get("ambiguous") and secondary["action"] != "noop":
            if secondary["action"] == "insert":
                sec_text = f"新規登録 {secondary['new_daily_qty']:g}"
            elif secondary["action"] == "update":
                sec_text = f"{secondary['old_daily_qty']:g}→{secondary['new_daily_qty']:g}"
            else:
                sec_text = f"{action_label}"
            detail += f"（連動: {secondary['kitting_list_no']} / {sec_text}）"
        return detail
