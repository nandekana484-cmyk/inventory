# ui/production/lot_merge_window.py
"""
ロット統合の画面（D-145・D-147）。ロット進捗チェックの「ロット統合」ボタンから開く。
X と X- の両方に有効な計画があるロットの組（と統合済みの組）を並べ、「統合する」「解除する」を行う。
一覧の数字はロットごとの値（統合しない計算）。統合する前に、統合した場合の発注数・完成数・残りを試算して確認に出す（D-153）。
"""
import tkinter as tk
from tkinter import messagebox, ttk

from services.inventory_diff_service import is_current_db_inventory_exported
from services.lot_merge_service import LotMergeError, list_merge_candidates, merge_lots, preview_merge, unmerge_lot
from services.lot_merge_items_service import format_side_conflict
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


def format_merge_preview(preview) -> str:
    """preview_merge() の結果を、確認ダイアログの文にする（D-153）。"""
    target, member = preview["target_lot"], preview["member_lot"]
    after = preview["after"]
    lines = ["統合した場合（試算）："]
    for lot in (target, member):
        b = preview["before"].get(lot)
        if b is None:
            lines.append(f"・今の {lot}：有効な計画なし")
        else:
            lines.append(f"・今の {lot}：発注数 {_fmt(b['order_quantity'])}、完成数 {_fmt(b['completed'])}、残り {_fmt(b['remaining'])}")
    lines.append(f"・統合後の {target}（+{member}）：発注数 {_fmt(after['order_quantity'])}（{target} の値）、"
                 f"完成数 {_fmt(after['completed'])}、残り {_fmt(after['remaining'])}")
    return "\n".join(lines)


def build_candidate_rows(candidates):
    """list_merge_candidates() の結果を、表示用の行（組ごとに、統合先・統合されるロットのファイルNo.・面ごとの行）にする。"""
    rows = []
    for pair in candidates:
        if pair["merged"]:
            m = pair["merge"]
            state = f"統合済み（{m['merged_at'][:10]}）"
        else:
            state = "未統合（面の構成が食い違い）" if pair.get("side_conflicts") else "未統合"
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
        self._conflicts_by_member = {}

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
                 "「-1」「K」「.1」などは統合しません。一覧の数字は、ロットごと（統合しない計算）の値です。",
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
        self._conflicts_by_member = {p["member_lot"]: p.get("side_conflicts", []) for p in candidates}
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
        conflicts = self._conflicts_by_member.get(member)
        if conflicts:
            # 面の構成が食い違う組は統合させない（D-168）。merge_lots() 側でも同じ理由で断る。
            messagebox.showwarning(
                "統合できません", "\n".join(format_side_conflict(c) + "。" for c in conflicts), parent=self,
            )
            return
        if not self._confirm_exported_db():
            return
        try:
            preview_text = format_merge_preview(preview_merge(member))
        except (LotMergeError, ValueError) as e:
            preview_text = f"（試算できませんでした：{e}）"
        if not messagebox.askyesno(
            "ロット統合",
            f"{member} を {target} へ統合しますか？\n\n{preview_text}\n\n"
            f"統合すると、{member} の実績の日付の引落が、後から増えることがあります（例：7/15 に +1）。",
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
