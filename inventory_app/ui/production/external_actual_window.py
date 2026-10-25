# ui/production/external_actual_window.py
"""
現場CSV取込画面。現場実績入力アプリが書き出した実績CSVを登録待ちにため、今の実績・NG との差を見て、選んだ行だけを登録する。
操作は「ファイルを選ぶ → 取り込む → 一覧で確認 → 選んだ行を登録」だけ。自動では登録しない。
入力の受け取りと表示だけを行い、検査・差の計算・登録は services/external_actual_service.py。業務ルールは docs/domain/external_actual.md。
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from services import external_actual_service as svc
from ui.common.async_task import run_in_background
from ui.common.checkable_treeview import CheckableTreeview
from ui.common.color_legend import build_color_legend
from ui.common.column_filter import CheckboxFilterPopup, build_filter_predicates, row_matches
from ui.common.highlight_colors import (
    AUTO_CONFIRMABLE_GREEN, QTY_DIFF_YELLOW, MISMATCH_RED, SIDE_MASTER_UNREGISTERED_GRAY,
)
from ui.common.window_utils import center_window

# (col_key, 見出し, 幅, 位置)
COLUMNS = [
    ("state", "状態", 70, tk.W), ("kitting_list_no", "キッティングNo.", 150, tk.W), ("lot_no", "ロットNo.", 90, tk.W),
    ("side", "面", 35, tk.CENTER), ("report_date", "日付", 85, tk.CENTER), ("qty", "生産数（今→新・差）", 150, tk.W),
    ("ng1", "NG面1（今→新）", 120, tk.CENTER), ("ng2", "NG面2（今→新）", 120, tk.CENTER),
    ("planned_qty", "予定数", 60, tk.E), ("remarks", "備考", 460, tk.W),
]
COL_INDEX = {key: i for i, (key, *_rest) in enumerate(COLUMNS)}
FILTER_COLUMNS = (("state", "状態"), ("lot_no", "ロットNo."))

LEGEND = [
    ("緑：新規・差あり", AUTO_CONFIRMABLE_GREEN, None), ("黄：要確認（1行ずつ選ぶ）", QTY_DIFF_YELLOW, None),
    ("赤：登録できない", MISMATCH_RED, None), ("グレー：変更なし", SIDE_MASTER_UNREGISTERED_GRAY, None),
]
TAG_COLORS = {svc.STATE_GREEN: AUTO_CONFIRMABLE_GREEN, svc.STATE_YELLOW: QTY_DIFF_YELLOW,
              svc.STATE_RED: MISMATCH_RED, svc.STATE_GRAY: SIDE_MASTER_UNREGISTERED_GRAY}
RESULT_LABELS = {
    svc.RESULT_REGISTERED: "登録", svc.RESULT_PARTIAL: "NGエラー", svc.RESULT_FAILED: "失敗",
    svc.RESULT_REFUSED: "登録せず", svc.RESULT_CHANGED: "登録せず", svc.RESULT_SKIPPED: "登録せず",
}


def _num(value) -> str:
    return "－" if value is None else f"{value:g}"


def _arrow(now, new) -> str:
    return f"{_num(now)}→{_num(new)}"


def _row_values(item: dict) -> tuple:
    """一覧の1行（COLUMNS の順）。生産数は「今→新（差）」。"""
    qty = _arrow(item["qty_now"], item["qty_new"])
    if item["qty_now"] is not None:
        qty += f"（{item['qty_new'] - item['qty_now']:+g}）"
    return (item["label"], item["kitting_list_no"], item["lot_no"], item["side"], item["report_date"], qty,
            _arrow(item["ng1_now"], item["ng1_new"]), _arrow(item["ng2_now"], item["ng2_new"]),
            _num(item["planned_qty"]), "／".join(item["remarks"]))


def _error_item(entry: dict, label: str) -> dict:
    """取り込めなかった行（形式エラー・古い書き出し）を、赤の行として一覧に出すための辞書。"""
    return {"row_id": None, "state": svc.STATE_RED, "label": label, "kitting_list_no": entry["kitting_list_no"],
            "lot_no": entry["lot_no"], "side": "", "report_date": "", "qty_now": None, "qty_new": None, "ng1_now": None,
            "ng1_new": None, "ng2_now": None, "ng2_new": None, "planned_qty": None,
            "remarks": [f"CSV {entry['csv_row_no']}行目：" + "、".join(entry["messages"])], "snapshot": None}


class _PreviewTable(CheckableTreeview):
    """登録できない行（赤・グレー・取り込めなかった行）にはチェックが付かない一覧。"""

    def __init__(self, parent, columns, height):
        super().__init__(parent, columns, height=height)
        self.locked_iids = set()

    def set_checked(self, iid, checked):
        super().set_checked(iid, checked and iid not in self.locked_iids)


class ExternalActualWindow(tk.Toplevel):
    def __init__(self, parent, current_worker, on_closed=None):
        super().__init__(parent)
        self._parent = parent
        self.current_worker = current_worker
        self._on_closed = on_closed
        self._items = []                     # evaluate_rows() の結果
        self._error_items = []               # 直前の取り込みで取り込めなかった行（この画面を開いている間だけ表示する）
        self._item_by_iid = {}
        self._filters = {key: None for key, _label in FILTER_COLUMNS}
        self._busy = False
        self.title("現場CSV取込")
        self.geometry("1350x640")
        center_window(self, parent)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._reload()

    # ---------------------------------------------------------------- 画面の組み立て

    def _build(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill=tk.X)
        self.btn_import = ttk.Button(top, text="CSVを選んで取り込む", command=self.on_import)
        self.btn_import.pack(side=tk.LEFT)
        self.btn_reload = ttk.Button(top, text="一覧を更新", command=self._reload)
        self.btn_reload.pack(side=tk.LEFT, padx=(5, 0))
        self.lbl_summary = ttk.Label(top, text="", foreground="gray")
        self.lbl_summary.pack(side=tk.LEFT, padx=15)

        filter_row = ttk.Frame(self, padding=(10, 0))
        filter_row.pack(fill=tk.X)
        for key, label in FILTER_COLUMNS:
            ttk.Button(filter_row, text=f"{label}で絞り込む", command=lambda k=key, t=label: self._open_filter(k, t)).pack(
                side=tk.LEFT, padx=(0, 5))

        bottom = ttk.Frame(self, padding=10)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        # 備考は列に収まらないことがあるので、選んだ行の備考を全文で出す。
        self.lbl_detail = ttk.Label(self, text="", wraplength=1300, justify=tk.LEFT, padding=(10, 0))
        ttk.Button(bottom, text="緑の行をすべて選ぶ", command=self._select_green).pack(side=tk.LEFT)
        ttk.Button(bottom, text="すべて解除", command=lambda: self.table.deselect_all()).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom, text="閉じる", command=self._close).pack(side=tk.RIGHT)
        self.btn_register = ttk.Button(bottom, text="選んだ行を登録", command=self.on_register)
        self.btn_register.pack(side=tk.RIGHT, padx=5)
        build_color_legend(self, LEGEND).pack(side=tk.BOTTOM, anchor=tk.W, padx=10)
        self.lbl_detail.pack(side=tk.BOTTOM, fill=tk.X)

        self.table = _PreviewTable(self, [(k, h, w, a) for k, h, w, a in COLUMNS], height=10)
        self.table.tree.bind("<<TreeviewSelect>>", self._show_detail)
        self.table.pack(expand=True, fill=tk.BOTH, padx=10, pady=(5, 5))
        for state, color in TAG_COLORS.items():
            self.table.tree.tag_configure(state, background=color)

    def _set_busy(self, busy):
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for button in (self.btn_import, self.btn_reload, self.btn_register):
            button.config(state=state)

    def _run(self, message, work, on_success):
        """別スレッドで work() を実行する（読み込み中の表示つき）。実行中はボタンを無効にする。"""
        if self._busy:
            return
        self._set_busy(True)

        def done(payload):
            if self.winfo_exists():
                self._set_busy(False)
                on_success(payload)

        def failed(error, traceback_text):
            if not self.winfo_exists():
                return
            self._set_busy(False)
            if isinstance(error, svc.ExternalActualRefused):
                messagebox.showwarning("現場CSV取込", str(error), parent=self)
            else:
                messagebox.showerror("現場CSV取込", f"エラーが発生しました：\n{error}\n\n{traceback_text}", parent=self)

        run_in_background(self, message, work, done, failed)

    # ---------------------------------------------------------------- 一覧

    def _reload(self):
        self._run("登録待ちを読み込んでいます…", svc.build_preview, self._show_items)

    def _show_items(self, items):
        self._items = items
        self._render()

    def _all_display_items(self):
        return self._error_items + self._items

    def _render(self):
        checked = {self._item_by_iid[i]["row_id"] for i in self.table.get_checked_iids()}
        self.table.clear()
        self.table.locked_iids.clear()
        self._item_by_iid.clear()
        predicates = build_filter_predicates({}, {k: v for k, v in self._filters.items() if v is not None})
        for number, item in enumerate(self._all_display_items()):
            values = _row_values(item)
            if not row_matches(values, predicates, COL_INDEX):
                continue
            iid = f"r{number}"
            self._item_by_iid[iid] = item
            if item["state"] in (svc.STATE_RED, svc.STATE_GRAY):
                self.table.locked_iids.add(iid)
            self.table.insert_row(iid, values, checked=item["row_id"] in checked)
            self.table.tree.item(iid, tags=(item["state"],))
        counts = {s: sum(1 for i in self._all_display_items() if i["state"] == s) for s in TAG_COLORS}
        self.lbl_summary.config(
            text=f"登録待ち {len(self._items)} 行　緑 {counts['green']}／黄 {counts['yellow']}／赤 {counts['red']}／グレー {counts['gray']}"
                 f"　（今のDB：{svc.current_db_folder_name()}）")

    def _show_detail(self, _event=None):
        selected = self.table.tree.selection()
        item = self._item_by_iid.get(selected[0]) if selected else None
        self.lbl_detail.config(text="" if item is None else "備考：" + "／".join(item["remarks"]))

    def _open_filter(self, key, label):
        values = sorted({str(_row_values(i)[COL_INDEX[key]]) for i in self._all_display_items()})
        CheckboxFilterPopup(self, label, values, self._filters[key],
                            lambda selected: self._filters.__setitem__(key, selected), self._render)

    def _select_green(self):
        """緑の行だけを選ぶ。黄の行（確認が要る行）は、1行ずつ自分で選ばないと登録しない。"""
        self.table.deselect_all()
        for iid, item in self._item_by_iid.items():
            if item["state"] == svc.STATE_GREEN:
                self.table.set_checked(iid, True)

    # ---------------------------------------------------------------- 取り込み

    def on_import(self):
        file_path = filedialog.askopenfilename(
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], parent=self, title="現場実績CSVを選ぶ")
        if not file_path:
            return
        warnings = svc.detect_wrong_format_warnings(file_path)
        if warnings and not messagebox.askyesno(
                "CSVフォーマットの確認", "\n".join(warnings) + "\n\nこのまま取り込みを続けますか？", parent=self):
            return
        worker_id = self.current_worker.get("worker_id", "SYSTEM")
        self._run("現場実績CSVを取り込んでいます…", lambda: svc.import_external_actual_file(file_path, worker_id),
                  self._on_imported)

    def _on_imported(self, result):
        if result["fatal"]:
            messagebox.showerror("取り込めません", result["fatal"], parent=self)
            return
        self._error_items = ([_error_item(e, "取込不可") for e in result["errors"]]
                             + [_error_item(e, "古い書き出し") for e in result["stale"]])
        lines = [f"登録待ちにしました：{result['imported']} 行（CSVのデータ行 {result['total_rows']} 行）"]
        if result["replaced"]:
            lines.append(f"同じ計画の登録待ちを置き換えました：{len(result['replaced'])} 行")
        if result["errors"] or result["stale"]:
            lines.append(f"取り込まなかった行：{len(result['errors']) + len(result['stale'])} 行（一覧に赤で出しています）")
        lines += [f"※ {w}" for w in result["warnings"]]
        lines.append("\n実績・NG にはまだ書いていません。一覧で確認して、登録する行を選んでください。")
        (messagebox.showwarning if result["errors"] or result["stale"] or result["warnings"] else messagebox.showinfo)(
            "取り込みました", "\n".join(lines), parent=self)
        self._reload()

    # ---------------------------------------------------------------- 登録

    def on_register(self):
        selected = [self._item_by_iid[i] for i in self.table.get_checked_iids()]
        if not selected:
            messagebox.showinfo("現場CSV取込", "登録する行を選んでください。", parent=self)
            return
        row_ids = self._confirm_registration(selected)
        if not row_ids:
            return
        snapshots = {item["row_id"]: item["snapshot"] for item in selected}
        registered_by = {"worker_id": self.current_worker.get("worker_id", "SYSTEM"), "name": self.current_worker.get("name", "unknown")}
        self._run("登録しています…", lambda: svc.register_rows(row_ids, snapshots, registered_by), self._on_registered)

    def _confirm_registration(self, selected):
        """
        確認ダイアログ（1つ）。件数と内訳、上書きになる行の一覧（今→新。行ごとに外せる）を出す。初めに選ばれているボタンはキャンセル。
        登録する行の row_id のリスト（外した行を除く）。キャンセルや、外して0件になったときは None。
        """
        overwrites = [i for i in selected if i["is_overwrite"]]
        completed = sum(1 for i in selected if i["snapshot"]["completed"])
        summary = (f"選んだ {len(selected)} 行を実績・NG に登録します。\n"
                   f"新規 {len(selected) - len(overwrites)} 行／上書き {len(overwrites)} 行／完成済みロット {completed} 行（新規・上書きの内数）")
        dialog = tk.Toplevel(self)
        dialog.title("登録の確認")
        dialog.transient(self)
        dialog.grab_set()
        outcome = {"ids": None}
        ttk.Label(dialog, text=summary, justify=tk.LEFT, padding=15).pack(anchor=tk.W)
        overwrite_table = None
        if overwrites:
            ttk.Label(dialog, text="上書きになる行（今→新）。登録しない行はチェックを外してください。",
                      padding=(15, 0)).pack(anchor=tk.W)
            overwrite_table = CheckableTreeview(dialog, [
                ("kitting_list_no", "キッティングNo.", 150, tk.W), ("lot_no", "ロットNo.", 90, tk.W),
                ("qty", "生産数", 110, tk.CENTER), ("ng1", "NG面1", 90, tk.CENTER), ("ng2", "NG面2", 90, tk.CENTER),
                ("report_date", "日付", 90, tk.CENTER)], height=min(len(overwrites), 10))
            overwrite_table.pack(fill=tk.BOTH, expand=True, padx=15, pady=5)
            for number, item in enumerate(overwrites):
                values = _row_values(item)
                overwrite_table.insert_row(str(number), (values[1], values[2], _arrow(item["qty_now"], item["qty_new"]),
                                                         values[6], values[7], values[4]), checked=True)

        def confirm():
            skipped = set()
            if overwrite_table is not None:
                kept = set(overwrite_table.get_checked_iids())
                skipped = {overwrites[n]["row_id"] for n in range(len(overwrites)) if str(n) not in kept}
            outcome["ids"] = [i["row_id"] for i in selected if i["row_id"] not in skipped] or None
            dialog.destroy()

        buttons = ttk.Frame(dialog, padding=15)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="登録する", command=confirm).pack(side=tk.RIGHT, padx=(5, 0))
        cancel = ttk.Button(buttons, text="キャンセル", command=dialog.destroy)
        cancel.pack(side=tk.RIGHT)
        dialog.bind("<Return>", lambda e: dialog.destroy())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        center_window(dialog, self)
        cancel.focus_set()                      # center_window() が withdraw/deiconify するので、その後で
        dialog.wait_window()
        return outcome["ids"]

    def _on_registered(self, outcome):
        self._show_results(outcome["results"])
        if outcome["lot_nos"]:
            current = getattr(self._parent, "current_plan", None) or {}
            cumulative = next((r["new_cumulative"] for r in outcome["results"] if r.get("new_cumulative") is not None
                               and r.get("plan_key") == (current.get("kitting_list_no"), current.get("lot_no"))), None)
            self._parent.refresh_after_external_registration(outcome["lot_nos"], cumulative)
        self._reload()

    def _show_results(self, results):
        """行ごとの結果（NG の部分失敗・登録しなかった理由も）。"""
        dialog = tk.Toplevel(self)
        dialog.title("登録結果")
        dialog.transient(self)
        dialog.grab_set()
        done = sum(1 for r in results if r["status"] == svc.RESULT_REGISTERED)
        ttk.Label(dialog, text=f"登録した行：{done}／{len(results)} 行（登録できなかった行は登録待ちに残っています）",
                  padding=10).pack(anchor=tk.W)
        tree = ttk.Treeview(dialog, columns=("result", "kitting", "lot", "message"), show="headings", height=min(len(results), 14))
        for key, text, width in (("result", "結果", 80), ("kitting", "キッティングNo.", 150), ("lot", "ロットNo.", 90), ("message", "内容", 560)):
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor=tk.W)
        for r in results:
            tree.insert("", tk.END, values=(RESULT_LABELS[r["status"]], r["kitting_list_no"], r["lot_no"], r["message"]))
        tree.pack(fill=tk.BOTH, expand=True, padx=10)
        close = ttk.Button(dialog, text="閉じる", command=dialog.destroy)
        close.pack(pady=10)
        dialog.bind("<Return>", lambda e: dialog.destroy())
        center_window(dialog, self)
        close.focus_set()
        dialog.wait_window()

    def _close(self):
        self.destroy()
        if self._on_closed:
            self._on_closed()
