"""登録待ち一覧の右クリックでの「不一致」「候補なし」の印付けと、不一致リストの CSV 出力。ProductionImportStagingWindow の mixin。
production_import_staging_window.py から移した（関数の中身は変えていない。D-190）。
"""
import csv
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from models.production_import_staging import delete_pending_csv_import_row

# 「不一致として除外」で理由が空欄・キャンセルのときの既定の文言（理由は人が判断するので自由記述）。
REASON_MISMATCH_DEFAULT = "不一致と判断（詳細理由未入力）"


class StagingMarkingMixin:
    def _on_right_click(self, event):
        """
        右クリックした行に「不一致として除外」などのメニューを出す。
        右クリックした行が複数選択の一部なら選択中の全行が対象。そうでなければ、その行だけを選び直して対象にする。
        """
        clicked_iid = self.tree.identify_row(event.y)
        if not clicked_iid:
            return

        current_selection = self.tree.selection()
        if len(current_selection) > 1 and clicked_iid in current_selection:
            target_iids = list(current_selection)
        else:
            self.tree.selection_set(clicked_iid)
            target_iids = [clicked_iid]

        menu = tk.Menu(self, tearoff=0)
        if len(target_iids) > 1:
            menu.add_command(
                label=f"不一致として除外（選択中の{len(target_iids)}件）",
                command=lambda: self._mark_multiple_as_mismatched(target_iids),
            )
            menu.add_command(
                label=f"候補なしとする（選択中の{len(target_iids)}件）",
                command=lambda: self._mark_multiple_as_no_candidates(target_iids),
            )
        else:
            menu.add_command(
                label="不一致として除外",
                command=lambda: self._mark_as_mismatched(target_iids[0]),
            )
            menu.add_command(
                label="候補なしとする",
                command=lambda: self._mark_as_no_candidates(target_iids[0]),
            )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _apply_mismatch(self, iid, reason):
        """
        1行を「不一致として除外」する共通処理。一覧と DB から即座に削除し、_mismatched_rows（閉じるまで保持）へ移す。
        登録不可リスト（_unregistrable_rows）とは別に管理する。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        pending_row_id = row.get("pending_row_id")
        self.tree.delete(iid)
        del self._row_by_iid[iid]
        if pending_row_id is not None:
            delete_pending_csv_import_row(pending_row_id)
        # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする。
        if self._current_staging_iid == iid:
            self._clear_candidate_pane()

        self._mismatched_rows.append({**row, "mismatch_reason": reason})

    def _mark_as_mismatched(self, iid):
        """
        人が「この行に一致する計画は無い」と判断した1行を除外する。理由は自由記述で尋ね、空欄・キャンセルなら既定の文言を使う。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        reason = simpledialog.askstring(
            "不一致として除外",
            f"ロットNo. {row.get('lot_no')} を不一致として除外します。\n"
            "除外理由（任意）：",
            parent=self,
        )
        reason = (reason or "").strip() or REASON_MISMATCH_DEFAULT

        self._apply_mismatch(iid, reason)
        self._update_status_label()

    def _mark_multiple_as_mismatched(self, iids):
        """複数選択した行を一括で「不一致として除外」する。理由は1回だけ尋ね、全行に同じ理由を使う。"""
        valid_iids = [iid for iid in iids if iid in self._row_by_iid]
        if not valid_iids:
            return

        lot_no_preview = "、".join(self._row_by_iid[iid].get("lot_no", "") for iid in valid_iids[:5])
        if len(valid_iids) > 5:
            lot_no_preview += " 他"
        reason = simpledialog.askstring(
            "不一致として除外",
            f"選択中の{len(valid_iids)}件（ロットNo: {lot_no_preview}）を"
            "まとめて不一致として除外します。\n除外理由（任意、全件共通）：",
            parent=self,
        )
        reason = (reason or "").strip() or REASON_MISMATCH_DEFAULT

        for iid in valid_iids:
            self._apply_mismatch(iid, reason)
        self._update_status_label()

    def _apply_no_candidates(self, iid):
        """
        1行を人の判断で「候補なしとする」（登録不可リストへ移す）。CSV の理由欄は、機械判定と同じ固定の文言になる。
        DB の行はここで即座に削除する。削除しないと、再度開いたときに再照合で候補が見つかり、登録待ち一覧に復活する
        （機械判定の行は候補が無いので復活せず、CSV 出力時の削除でよい）。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        pending_row_id = row.get("pending_row_id")
        self.tree.delete(iid)
        del self._row_by_iid[iid]
        if pending_row_id is not None:
            delete_pending_csv_import_row(pending_row_id)
        # 削除された行が左ペインの候補表示元だった場合、候補表示をクリアする。
        if self._current_staging_iid == iid:
            self._clear_candidate_pane()

        self._unregistrable_rows.append(row)

    def _mark_as_no_candidates(self, iid):
        """
        人が「対応する計画は無い」と判断した1行を登録不可リストへ移す。理由は固定の文言なので、誤クリック防止のため確認だけ挟む。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        if not messagebox.askyesno(
            "候補なしとする",
            f"ロットNo. {row.get('lot_no')} を候補なしとして登録不可リストに移動します。よろしいですか？",
            parent=self,
        ):
            return

        self._apply_no_candidates(iid)
        self._update_status_label()

    def _mark_multiple_as_no_candidates(self, iids):
        """複数選択した行を一括で「候補なしとする」。確認ダイアログは1回だけ。"""
        valid_iids = [iid for iid in iids if iid in self._row_by_iid]
        if not valid_iids:
            return

        lot_no_preview = "、".join(self._row_by_iid[iid].get("lot_no", "") for iid in valid_iids[:5])
        if len(valid_iids) > 5:
            lot_no_preview += " 他"
        if not messagebox.askyesno(
            "候補なしとする",
            f"選択中の{len(valid_iids)}件（ロットNo: {lot_no_preview}）を"
            "まとめて候補なしとして登録不可リストに移動します。よろしいですか？",
            parent=self,
        ):
            return

        for iid in valid_iids:
            self._apply_no_candidates(iid)
        self._update_status_label()

    def on_export_mismatched_csv(self):
        """
        「不一致として除外」した行を CSV 出力する（理由は行ごとの入力値）。DB からは除外時に削除済みなので、ここでは書き出すだけ。
        実際に書き出せたら True。_on_close() が「出力できたか」の判定に使う。
        """
        if not self._mismatched_rows:
            messagebox.showinfo("不一致リスト", "不一致として除外した行はありません。", parent=self)
            return True

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_mismatched.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return False

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._mismatched_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        row.get("mismatch_reason", REASON_MISMATCH_DEFAULT),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return False

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)
        return True
