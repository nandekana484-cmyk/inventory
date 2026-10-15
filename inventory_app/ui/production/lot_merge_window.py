# ui/production/lot_merge_window.py
"""
ロット統合の画面（D-145、第1段階）。ロット進捗チェックの「ロット統合」ボタンから開く。
X と X- の両方に有効な計画があるロットの組（と統合済みの組）を並べ、「統合する」「解除する」を行う。
並べる数字は今の計算のまま（統合しない計算）。統合後の試算と、集計への反映は第2段階で行う。
"""
import tkinter as tk
from tkinter import messagebox, ttk

from services.inventory_diff_service import is_current_db_inventory_exported
from services.lot_merge_service import LotMergeError, list_merge_candidates, merge_lots, unmerge_lot
from ui.common.window_utils import center_window
from ui.production.lot_progress_window import STATUS_LABELS

COLUMNS = ("merge_state", "lot_no", "role", "board_name", "setup_file_no", "production_side",
           "order_qty", "actual_qty", "status")
HEADERS = {
    "merge_state": "統合", "lot_no": "ロットNo.", "role": "区分", "board_name": "基板名",
    "setup_file_no": "ファイルNo.", "production_side": "面", "order_qty": "発注数", "actual_qty": "実績", "status": "状態",
}
_WIDTHS = {"merge_state": 150, "lot_no": 100, "role": 90, "board_name": 200, "setup_file_no": 90,
           "production_side": 50, "order_qty": 80, "actual_qty": 80, "status": 130}
_NUMERIC = {"production_side", "order_qty", "actual_qty"}


def _fmt(value):
    return "" if value is None else f"{value:g}" if isinstance(value, (int, float)) else str(value)


def build_candidate_rows(candidates):
    """list_merge_candidates() の結果を、表示用の行（組ごとに、統合先・統合されるロットのファイルNo.・面ごとの行）にする。"""
    rows = []
    for pair in candidates:
        if pair["merged"]:
            m = pair["merge"]
            state = f"統合済み（{m['merged_at'][:10]}）"
        else:
            state = "未統合"
        for lot_no, role in ((pair["target_lot"], "統合先"), (pair["member_lot"], "統合される")):
            lot = pair["lots"][lot_no]
            base = {"merge_state": state, "lot_no": lot_no, "role": role, "_member_lot": pair["member_lot"],
                    "_merged": pair["merged"]}
            if lot is None:
                rows.append({**base, "board_name": "（有効な計画なし）", "setup_file_no": "", "production_side": "",
                             "order_qty": "", "actual_qty": "", "status": ""})
                continue
            status = STATUS_LABELS.get(lot["status"], lot["status"])
            for board in lot["boards"]:
                for f in board["files"]:
                    rows.append({**base, "board_name": board["board_name"], "setup_file_no": f["setup_file_no"],
                                 "production_side": _fmt(f["production_side"]), "order_qty": _fmt(f["order_qty"]),
                                 "actual_qty": _fmt(f["file_actual"]), "status": status})
    return rows


class LotMergeWindow(tk.Toplevel):
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.title("ロット統合")
        self.geometry("1100x520")
        center_window(self, parent)
        self.current_worker = current_worker or {}
        self._member_by_iid = {}
        self._merged_by_member = {}

        # 下の要素を先に pack する（Treeview を先に pack するとボタンが潰れる）。
        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btn_frame, text="統合する", command=self.on_merge).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="解除する", command=self.on_unmerge).pack(side=tk.LEFT, padx=(5, 0))
        ttk.Button(btn_frame, text="再読込", command=self.refresh).pack(side=tk.LEFT, padx=(15, 0))
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT)

        ttk.Label(
            self, foreground="gray", padding=(10, 8, 10, 0),
            text="末尾に「-」が1つ付いたロット（例 123456-）を、「-」の無いロット（123456）へ統合します。"
                 "「-1」「K」「.1」などは統合しません。ここに出す数字は、統合しない今の計算のままです。",
        ).pack(side=tk.TOP, anchor=tk.W)
        self.lbl_count = ttk.Label(self, foreground="gray")
        self.lbl_count.pack(side=tk.BOTTOM, anchor=tk.W, padx=10)

        tree_frame = ttk.Frame(self, padding=(10, 5, 10, 0))
        tree_frame.pack(side=tk.TOP, expand=True, fill=tk.BOTH)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree = ttk.Treeview(tree_frame, columns=COLUMNS, show="headings", selectmode="browse",
                                 yscrollcommand=vsb.set)
        vsb.config(command=self.tree.yview)
        for c in COLUMNS:
            self.tree.heading(c, text=HEADERS[c])
            self.tree.column(c, width=_WIDTHS[c], anchor=tk.E if c in _NUMERIC else tk.W)
        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        self.tree.tag_configure("merged", background="#e8f0fe")

        self.refresh()

    def refresh(self):
        try:
            candidates = list_merge_candidates()
        except Exception as e:
            messagebox.showerror("エラー", f"候補を読み込めませんでした：\n{e}", parent=self)
            return
        self.tree.delete(*self.tree.get_children())
        self._member_by_iid.clear()
        self._merged_by_member = {p["member_lot"]: p["merged"] for p in candidates}
        for i, row in enumerate(build_candidate_rows(candidates)):
            iid = f"r{i}"
            self.tree.insert("", tk.END, iid=iid, values=[row[c] for c in COLUMNS],
                             tags=("merged",) if row["_merged"] else ())
            self._member_by_iid[iid] = row["_member_lot"]
        merged = sum(1 for v in self._merged_by_member.values() if v)
        self.lbl_count.config(text=f"{len(candidates)}組（統合済み {merged}組・未統合 {len(candidates) - merged}組）")

    def _selected_member(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("ロット統合", "一覧から組を選んでください。", parent=self)
            return None
        return self._member_by_iid[sel[0]]

    def _confirm_exported_db(self):
        """在庫値出力済みの DB なら、ほかの操作と同じ警告を出す（D-58。禁止はしない）。"""
        return not is_current_db_inventory_exported() or messagebox.askyesno(
            "出力済みデータベース",
            "このDBは在庫値出力済みです。入力した内容はこの古いDBに保存されます。\n続けますか？",
            parent=self,
        )

    def _worker_name(self):
        return self.current_worker.get("name", "unknown")

    def on_merge(self):
        member = self._selected_member()
        if member is None:
            return
        if self._merged_by_member.get(member):
            messagebox.showinfo("ロット統合", f"{member} はすでに統合済みです。", parent=self)
            return
        target = member[:-1]
        if not self._confirm_exported_db():
            return
        if not messagebox.askyesno(
            "ロット統合",
            f"{member} を {target} へ統合しますか？\n\n"
            f"統合すると、{member} の実績の日付の引落が、後から増えることがあります（例：7/15 に +1）。\n"
            "（この版では、ロット進捗・引落などの集計にはまだ反映されません）",
            parent=self,
        ):
            return
        try:
            merge_lots(member, self._worker_name())
        except LotMergeError as e:
            messagebox.showwarning("統合できません", str(e), parent=self)
            return
        self.refresh()
        messagebox.showinfo("ロット統合", f"{member} を {target} へ統合しました。", parent=self)

    def on_unmerge(self):
        member = self._selected_member()
        if member is None:
            return
        if not self._merged_by_member.get(member):
            messagebox.showinfo("ロット統合", f"{member} は統合されていません。", parent=self)
            return
        target = member[:-1]
        if not self._confirm_exported_db():
            return
        if not messagebox.askyesno(
            "ロット統合の解除",
            f"{member} と {target} の統合を解除しますか？\n\n統合している間に登録した実績などはそのまま残ります。",
            parent=self,
        ):
            return
        try:
            unmerge_lot(member, self._worker_name())
        except LotMergeError as e:
            messagebox.showwarning("解除できません", str(e), parent=self)
            return
        self.refresh()
        messagebox.showinfo("ロット統合", f"{member} と {target} の統合を解除しました。", parent=self)
