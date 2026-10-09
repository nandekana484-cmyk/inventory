import threading
import queue
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

from services.production_service import search_plan_by_kitting_no
from services.bom_service import get_shared_bom_service
from models.scrap_records import (
    list_scrap_summary_by_kitting_no,
    list_scrap_records_by_kitting_no,
    replace_scrap_records,
)
from models.kitting_plan import find_plan_item_by_kitting_no
from models.ng_declarations import get_ng_declaration, list_ng_declarations_latest
from models.ng_exclusion_list import (
    mark_ng_excluded, unmark_ng_excluded, list_ng_exclusions,
)
from models.operation_log import log_operation
from ui.checkable_treeview import CheckableTreeview
from ui.loading_window import LoadingWindow
from ui.scrap_correction_window import ScrapCorrectionWindow
from ui.window_utils import center_window

# 共有の BOMService（仕掛展開画面と共有するので、共有フォルダのインデックス作成は1回で済む）。
_bom_service = get_shared_bom_service()


class NgInputWindow(tk.Toplevel):
    """
    NG（仕損）実績入力画面。キッティングリストNo.（計画あり）か、ファイルNo.＋生産面（計画外）で BOM を展開し、
    チェックした部品を scrap_records に登録する（その計画・面の既存分は削除してから登録し直す）。
    右ペインの NG 一覧は、申告と展開済みを合わせて「未展開／展開済み／展開済み（申告記録なし）」を表示する。
    入力の流れの詳細は docs/domain/ng_input.md。
    """
    # NG 一覧の列順。_fetch_ng_list_rows() のタプルと一致させること（unprocessed_check_service もこの定義を参照する）。
    NG_LIST_COLUMNS = (
        "kitting_list_no", "lot_no", "board_name", "file_no", "side",
        "is_unplanned", "status", "declared_ng_qty", "part_count",
        "record_count", "last_report_date", "excluded",
    )

    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        # 計画外のときは kitting_list_no に file_no を入れる（NOT NULL のため。実在の kitting_list_no とは形が違うので衝突しない）。
        self.current_plan = None

        # NG 一覧の絞り込み（生産実績入力画面の計画一覧と同じ設計）。_all_ng_rows は絞り込み前の全件、
        # _ng_checkbox_filters にキーが無い列＝絞り込み無し。
        self._all_ng_rows = []
        self._ng_filter_vars = {}
        self._ng_checkbox_filters = {}
        self._ng_checkbox_buttons = {}
        self._ng_col_index = {}
        self._ng_filter_labels = {}
        self._ng_sort_states = {}

        self.title("NG（仕損）入力")
        self.geometry("1150x600")
        center_window(self, parent)
        # 開いた直後に最大化する。直前の geometry() の値が、最大化を解除したときの大きさと位置になる。
        # center_window() は非表示のまま位置を決めるので、「小さく出てから広がる」動きにはならない。
        self.state("zoomed")

        self.create_widgets()

    def create_widgets(self):
        container = ttk.Frame(self)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Frame(container)
        left_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        right_frame = ttk.Labelframe(container, text="NG一覧", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 15), pady=5)

        search_frame = ttk.LabelFrame(left_frame, text="対象計画・NG数量", padding=10)
        search_frame.pack(fill=tk.X, padx=15, pady=10)

        ttk.Label(search_frame, text="キッティングリストNo.：").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.entry_kitting_no = ttk.Entry(search_frame, width=20)
        self.entry_kitting_no.grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)

        ttk.Label(search_frame, text="（未入力の場合、下のファイルNo.＋生産面で計画外登録）",
                  foreground="gray").grid(row=0, column=2, columnspan=2, sticky=tk.W, padx=(10, 0))

        ttk.Label(search_frame, text="ファイルNo.（計画外）：").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.entry_file_no = ttk.Entry(search_frame, width=20)
        self.entry_file_no.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)

        ttk.Label(search_frame, text="生産面：").grid(row=1, column=2, sticky=tk.E, padx=(10, 2), pady=3)
        self.combo_side = ttk.Combobox(
            search_frame, width=8, state="readonly", values=("面1", "面2")
        )
        self.combo_side.grid(row=1, column=3, sticky=tk.W, pady=3)

        ttk.Label(search_frame, text="NG数量（枚数）：").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.entry_ng_qty = ttk.Entry(search_frame, width=20)
        self.entry_ng_qty.grid(row=2, column=1, sticky=tk.W, padx=5, pady=3)

        self.btn_expand = ttk.Button(search_frame, text="展開", command=self.on_expand)
        self.btn_expand.grid(row=0, column=4, rowspan=3, padx=15)

        self.lbl_plan_info = ttk.Label(search_frame, text="-", foreground="blue")
        self.lbl_plan_info.grid(row=3, column=0, columnspan=5, sticky=tk.W, pady=(8, 0))

        parts_frame = ttk.LabelFrame(left_frame, text="使用部品一覧（仕損とする部品を選択・チェックボックス）", padding=10)
        parts_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=(0, 10))

        self.tree = CheckableTreeview(
            parts_frame,
            columns=[
                ("part_no", "96コード", 220, tk.W),
                ("item_type_label", "区分", 70, tk.CENTER),
                ("qty_per_product", "1台あたり数量", 140, tk.E),
                ("consumed_qty", "消費数量（NG数×員数）", 180, tk.E),
            ],
            height=10,
            # 消費数量だけを編集できる（96コードを誤って書き換えて保存する事故を防ぐ）。
            editable_columns={"consumed_qty"},
        )

        parts_btn_row = ttk.Frame(parts_frame)
        parts_btn_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(parts_btn_row, text="全選択", command=self.tree.select_all).pack(side=tk.LEFT)
        ttk.Button(parts_btn_row, text="全解除", command=self.tree.deselect_all).pack(side=tk.LEFT, padx=(5, 0))

        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(left_frame, padding=10)
        btn_frame.pack(fill=tk.X)
        self.btn_register = ttk.Button(btn_frame, text="仕損登録", command=self.on_register, state=tk.DISABLED)
        self.btn_register.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="製品NGレポート", command=self.open_product_ng_report).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="96NGレポート", command=self.open_parts_ng_report).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self._create_ng_list_widgets(right_frame)

    def open_product_ng_report(self):
        """循環import回避のため、ここで都度importする。"""
        from ui.product_ng_report_window import ProductNgReportWindow
        ProductNgReportWindow(self)

    def open_parts_ng_report(self):
        """循環import回避のため、ここで都度importする。"""
        from ui.parts_ng_report_window import PartsNgReportWindow
        PartsNgReportWindow(self)

    def _create_ng_list_widgets(self, right_frame):
        """右ペイン（NG 一覧）を作る（生産実績入力画面の計画一覧と同じ構成）。"""
        cols_ng = self.NG_LIST_COLUMNS
        self._ng_col_index = {key: i for i, key in enumerate(cols_ng)}

        self._ng_filter_labels = {
            "kitting_list_no": "キッティングNo.",
            "lot_no": "ロットNo.",
            "board_name": "基板名",
            "file_no": "file_no",
            "side": "生産面",
            "is_unplanned": "計画外",
            "status": "状態",
            "declared_ng_qty": "申告NG数量",
            "part_count": "部品種類数",
            "record_count": "レコード数",
            "last_report_date": "最終報告日",
            "excluded": "対象外",
        }

        ng_filter_frame = ttk.LabelFrame(right_frame, text="絞り込み", padding=8)
        ng_filter_frame.pack(fill=tk.X, pady=(0, 5))

        ng_filter_row1 = ttk.Frame(ng_filter_frame)
        ng_filter_row1.pack(fill=tk.X, pady=(0, 4))
        ng_filter_row2 = ttk.Frame(ng_filter_frame)
        ng_filter_row2.pack(fill=tk.X)

        # テキスト部分一致：キッティングNo./申告NG数量/部品種類数/レコード数/最終報告日
        self._add_ng_filter_entry(ng_filter_row1, "kitting_list_no", self._ng_filter_labels["kitting_list_no"], width=14)
        # チェックボックス式ポップアップ：候補が限られる列（ロットNo./基板名/file_no/生産面/計画外/状態）
        self._add_ng_checkbox_filter_button(ng_filter_row1, "lot_no")
        self._add_ng_checkbox_filter_button(ng_filter_row1, "board_name")
        self._add_ng_checkbox_filter_button(ng_filter_row1, "file_no")
        self._add_ng_checkbox_filter_button(ng_filter_row1, "side")
        self._add_ng_checkbox_filter_button(ng_filter_row1, "is_unplanned")
        self._add_ng_checkbox_filter_button(ng_filter_row1, "status")
        self._add_ng_checkbox_filter_button(ng_filter_row1, "excluded")

        self._add_ng_filter_entry(ng_filter_row2, "declared_ng_qty", self._ng_filter_labels["declared_ng_qty"], width=8)
        self._add_ng_filter_entry(ng_filter_row2, "part_count", self._ng_filter_labels["part_count"], width=8)
        self._add_ng_filter_entry(ng_filter_row2, "record_count", self._ng_filter_labels["record_count"], width=8)
        self._add_ng_filter_entry(ng_filter_row2, "last_report_date", self._ng_filter_labels["last_report_date"], width=12)

        ttk.Button(
            ng_filter_row2, text="絞り込みクリア", command=self.clear_ng_filters
        ).pack(side=tk.LEFT, padx=(15, 0))

        self.tree_ng_list = ttk.Treeview(right_frame, columns=cols_ng, show="headings")
        for col_key in cols_ng:
            self.tree_ng_list.heading(
                col_key, text=self._ng_filter_labels[col_key],
                command=lambda c=col_key: self.sort_ng_list(c),
            )
        self.tree_ng_list.column("kitting_list_no", width=140, anchor=tk.W)
        self.tree_ng_list.column("lot_no", width=90, anchor=tk.W)
        self.tree_ng_list.column("board_name", width=120, anchor=tk.W)
        self.tree_ng_list.column("file_no", width=80, anchor=tk.W)
        self.tree_ng_list.column("side", width=60, anchor=tk.CENTER)
        self.tree_ng_list.column("is_unplanned", width=60, anchor=tk.CENTER)
        self.tree_ng_list.column("status", width=140, anchor=tk.W)
        self.tree_ng_list.column("declared_ng_qty", width=90, anchor=tk.E)
        self.tree_ng_list.column("part_count", width=80, anchor=tk.E)
        self.tree_ng_list.column("record_count", width=80, anchor=tk.E)
        self.tree_ng_list.column("last_report_date", width=100, anchor=tk.W)
        self.tree_ng_list.column("excluded", width=70, anchor=tk.CENTER)

        vsb_ng = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_ng_list.yview)
        self.tree_ng_list.configure(yscrollcommand=vsb_ng.set)

        hsb_ng = ttk.Scrollbar(right_frame, orient="horizontal", command=self.tree_ng_list.xview)
        self.tree_ng_list.configure(xscrollcommand=hsb_ng.set)

        # pack 順の罠: ボタン行→水平スクロールバーの順に side=BOTTOM で pack する（逆だと、スクロールバーがボタンとの間に割り込む）。
        ng_action_frame = ttk.Frame(right_frame)
        ng_action_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        ttk.Button(ng_action_frame, text="更新", command=self.load_ng_list).pack(
            side=tk.LEFT, expand=True, fill=tk.X
        )
        ttk.Button(ng_action_frame, text="対象外にする", command=self.on_mark_ng_excluded).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
        )
        ttk.Button(ng_action_frame, text="対象外解除", command=self.on_unmark_ng_excluded).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
        )
        ttk.Button(ng_action_frame, text="一括展開・登録", command=self.on_bulk_expand_register).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
        )
        ttk.Button(ng_action_frame, text="実績修正", command=self.on_open_scrap_correction).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
        )
        hsb_ng.pack(side=tk.BOTTOM, fill=tk.X)
        vsb_ng.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_ng_list.pack(expand=True, fill=tk.BOTH)
        self.tree_ng_list.bind("<Double-1>", self.on_ng_list_double_click)

        self.load_ng_list()

    def on_ng_list_double_click(self, event):
        """
        NG 一覧の行をダブルクリックすると、その計画（計画外なら file_no＋生産面）を検索欄に入れて展開する。
        NG 数量は変えない（未入力なら通常どおり入力エラーになる）。計画ありの行は lot_no も渡す（kitting_list_no は重複するため）。
        """
        row_id = self.tree_ng_list.identify_row(event.y)
        if not row_id:
            return
        values = self.tree_ng_list.item(row_id, "values")
        kitting_list_no = values[self._ng_col_index["kitting_list_no"]]
        lot_no = values[self._ng_col_index["lot_no"]] or None
        file_no = values[self._ng_col_index["file_no"]]
        side_text = values[self._ng_col_index["side"]]
        is_unplanned = values[self._ng_col_index["is_unplanned"]] == "計画外"

        self.entry_kitting_no.delete(0, tk.END)
        self.entry_file_no.delete(0, tk.END)
        self.combo_side.set("")

        if is_unplanned:
            self.entry_file_no.insert(0, file_no)
            self.combo_side.set(side_text)
            self.on_expand()
        else:
            self.entry_kitting_no.insert(0, kitting_list_no)
            self.on_expand(lot_no=lot_no)

    def _get_selected_ng_row_identity(self):
        """
        選択中の行から、対象外リストの識別キー (kitting_list_no, side, lot_no) を取り出す。選択が無い・生産面が想定外なら None。
        """
        sel = self.tree_ng_list.selection()
        if not sel:
            messagebox.showwarning("警告", "対象の行を選択してください。", parent=self.winfo_toplevel())
            return None

        values = self.tree_ng_list.item(sel[0], "values")
        kitting_list_no = values[self._ng_col_index["kitting_list_no"]]
        lot_no = values[self._ng_col_index["lot_no"]] or None
        side_text = values[self._ng_col_index["side"]]

        if side_text not in ("面1", "面2"):
            messagebox.showerror("エラー", f"生産面を判別できません: {side_text!r}", parent=self.winfo_toplevel())
            return None
        side = int(side_text[-1])

        return kitting_list_no, side, lot_no

    def _prompt_ng_exclusion_reason(self):
        """
        対象外にする理由（任意）を尋ねる。OK なら理由（空欄なら None）、キャンセルなら False（None と区別するため）。
        """
        result = {"confirmed": False, "reason": ""}

        # 最小化中だと transient のダイアログが表示されないため、先に元に戻す（UI_WORKFLOW_FIXES_NOTES.md）。
        if self.state() == "iconic":
            self.deiconify()

        dialog = tk.Toplevel(self)
        dialog.title("対象外にする理由（任意）")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(dialog, text="対象外にする理由があれば入力してください（空欄可）：").pack(padx=15, pady=(15, 5))
        entry = ttk.Entry(dialog, width=40)
        entry.pack(padx=15, pady=5)
        entry.focus_set()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=(5, 15))

        def on_ok(event=None):
            result["confirmed"] = True
            result["reason"] = entry.get().strip()
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        ttk.Button(btn_frame, text="OK", command=on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="キャンセル", command=on_cancel).pack(side=tk.LEFT, padx=5)
        dialog.bind("<Return>", on_ok)
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)

        center_window(dialog, self)
        dialog.wait_window()
        if not result["confirmed"]:
            return False
        return result["reason"] or None

    def on_mark_ng_excluded(self):
        """選択中の行を「対象外」にする（理由は任意入力。キャンセルなら何もしない）。"""
        identity = self._get_selected_ng_row_identity()
        if identity is None:
            return
        kitting_list_no, side, lot_no = identity

        reason = self._prompt_ng_exclusion_reason()
        if reason is False:
            return

        worker_id = (self.current_worker or {}).get("worker_id", "SYSTEM")
        mark_ng_excluded(kitting_list_no, side, lot_no, reason, worker_id)
        log_operation(
            (self.current_worker or {}).get("name", "unknown"),
            "NG対象外にする",
            detail=f"{kitting_list_no}{f'（ロットNo. {lot_no}）' if lot_no else ''} / 面{side}",
        )
        self.load_ng_list()

    def on_unmark_ng_excluded(self):
        """選択中の行の「対象外」を解除する（対象外でない行に呼んでも害は無いので、状態は確かめない）。"""
        identity = self._get_selected_ng_row_identity()
        if identity is None:
            return
        kitting_list_no, side, lot_no = identity

        if not messagebox.askyesno(
            "確認",
            f"キッティングリストNo. {kitting_list_no}"
            f"{f'（ロットNo. {lot_no}）' if lot_no else ''} の対象外指定を解除しますか？",
            parent=self.winfo_toplevel(),
        ):
            return

        unmark_ng_excluded(kitting_list_no, side, lot_no)
        log_operation(
            (self.current_worker or {}).get("name", "unknown"),
            "NG対象外解除",
            detail=f"{kitting_list_no}{f'（ロットNo. {lot_no}）' if lot_no else ''} / 面{side}",
        )
        self.load_ng_list()

    def on_open_scrap_correction(self):
        """
        選択中の行の scrap_records 明細（96コード単位）を修正画面で開く。面ごとに分かれているので、その面だけを表示する。
        修正のたびに NG 一覧の集計も更新する（on_updated=self.load_ng_list）。
        """
        identity = self._get_selected_ng_row_identity()
        if identity is None:
            return
        kitting_list_no, side, lot_no = identity

        ScrapCorrectionWindow(
            self, kitting_list_no=kitting_list_no, lot_no=lot_no, production_side=side,
            on_updated=self.load_ng_list, current_worker=self.current_worker,
        )

    def on_expand(self, lot_no=None):
        """
        lot_no: 呼び出し元が lot_no を把握していれば渡す（一意に特定する）。省略時は _expand_from_kitting_no() が候補を選ばせる。
        """
        kitting_no = self.entry_kitting_no.get().strip()
        if kitting_no:
            # キッティングリストNo.があれば、計画ありとして扱う（ファイルNo.欄は無視する）。
            self._expand_from_kitting_no(kitting_no, lot_no=lot_no)
            return

        file_no = self.entry_file_no.get().strip()
        side_text = self.combo_side.get().strip()
        if not file_no or not side_text:
            messagebox.showwarning(
                "入力エラー",
                "キッティングリストNo.、またはファイルNo.＋生産面を入力してください。",
                parent=self.winfo_toplevel(),
            )
            return
        side = 1 if side_text == "面1" else 2
        self._expand_from_file_no(file_no, side)

    def _resolve_ng_qty(self, kitting_list_no, side, lot_no):
        """
        NG 数量の欄が空なら、現在の NG 申告（日付を問わない「1計画・面＝1レコード」）の値を入れて使う。欄に値があればそちらを優先する。
        lot_no は計画ありなら計画の lot_no、計画外なら None。不正・0以下ならエラーを出して None（呼び出し元は展開を中断すること）。
        """
        text = self.entry_ng_qty.get().strip()
        if not text:
            declaration = get_ng_declaration(kitting_list_no, side, lot_no=lot_no)
            if declaration:
                self.entry_ng_qty.insert(0, f"{declaration['ng_qty']:g}")
                text = self.entry_ng_qty.get().strip()

        if not text:
            messagebox.showwarning("入力エラー", "NG数量には数値を入力してください。", parent=self.winfo_toplevel())
            return None
        try:
            ng_qty = float(text)
        except ValueError:
            messagebox.showwarning("入力エラー", "NG数量には数値を入力してください。", parent=self.winfo_toplevel())
            return None
        if ng_qty <= 0:
            messagebox.showwarning("入力エラー", "NG数量には0より大きい数値を入力してください。", parent=self.winfo_toplevel())
            return None
        return ng_qty

    def _run_bom_expansion_async(self, work_fn, on_success):
        """
        BOM 展開（共有フォルダを読むので時間が読めない）を別スレッドで実行する共通処理。
        ダイアログを伴う計画の特定・入力検証は、呼び出し元が UI スレッドで先に済ませ、展開だけを work_fn で渡す。
        FileNotFoundError・ValueError は既知のエラーとして従来の文言で表示し、それ以外は Tkinter の通常の例外報告に任せる。
        """
        loading = LoadingWindow(self, message="BOM展開中です（共有フォルダへアクセスしています）…")
        result_queue = queue.Queue()

        def _work():
            try:
                result_queue.put((True, work_fn()))
            except Exception as e:
                result_queue.put((False, e))

        threading.Thread(target=_work, daemon=True).start()

        def _poll():
            try:
                success, payload = result_queue.get_nowait()
            except queue.Empty:
                self.after(200, _poll)
                return

            loading.destroy()

            if not success:
                if isinstance(payload, FileNotFoundError):
                    messagebox.showerror("BOMエラー", f"BOM TSVが見つかりません：\n{payload}", parent=self.winfo_toplevel())
                    return
                if isinstance(payload, ValueError):
                    messagebox.showerror("BOMエラー", f"BOM展開に失敗しました：\n{payload}", parent=self.winfo_toplevel())
                    return
                raise payload

            on_success(payload)

        self.after(200, _poll)

    def _expand_from_kitting_no(self, kitting_no, lot_no=None):
        """
        キッティングリストNo.での展開（計画あり）。lot_no があれば一意に特定する。
        無ければ、lot_no の候補が複数あるときに ui.plan_candidate_dialog で選ばせる（候補が1件ならそのまま）。
        計画の特定までは UI スレッドで行い、BOM 展開だけを非同期にする。
        """
        plan, candidates = search_plan_by_kitting_no(kitting_no, lot_no)
        if candidates is not None:
            from ui.plan_candidate_dialog import select_plan_candidate
            chosen = select_plan_candidate(self.winfo_toplevel(), kitting_no, candidates)
            if chosen is None:
                return
            plan, candidates = search_plan_by_kitting_no(kitting_no, chosen["lot_no"])

        if not plan:
            messagebox.showerror("検索エラー", f"キッティングリストNo. {kitting_no} の計画が見つかりません。", parent=self.winfo_toplevel())
            self.current_plan = None
            self.btn_register.config(state=tk.DISABLED)
            return

        file_no = plan["setup_file_no"]
        try:
            side = int(plan["production_side"])
        except (TypeError, ValueError):
            messagebox.showerror(
                "エラー",
                f"生産面（production_side）を数値として解釈できません: {plan['production_side']!r}",
            parent=self.winfo_toplevel())
            return
        if side not in (1, 2):
            messagebox.showerror("エラー", f"生産面（production_side）は1または2である必要があります（値: {side}）。", parent=self.winfo_toplevel())
            return

        ng_qty = self._resolve_ng_qty(kitting_no, side, plan.get("lot_no"))
        if ng_qty is None:
            return

        planned_qty = plan.get("planned_qty")
        if planned_qty is not None and ng_qty > planned_qty:
            messagebox.showwarning(
                "警告",
                f"NG数量（{ng_qty:g}）が計画数（{planned_qty:g}）を超えています。\n入力内容はそのまま登録できます。",
            parent=self.winfo_toplevel())

        scrap_record = {
            "setup_file_no": file_no,
            "production_side": side,
            "ng_qty": ng_qty,
            "lot_no": plan.get("lot_no"),
            "mounting_line": plan.get("mounting_line"),
        }

        def on_success(parts):
            self.current_plan = {
                "kitting_list_no": kitting_no,
                "file_no": file_no,
                "side": side,
                "ng_qty": ng_qty,
                "is_unplanned": False,
                "lot_no": plan.get("lot_no"),
            }
            self.lbl_plan_info.config(
                text=f"file_no: {file_no} / 生産面: {side} / ロットNo: {plan.get('lot_no', '-')}"
            )

            self.load_parts_tree(parts, ng_qty)

            if not parts:
                messagebox.showwarning(
                    "警告",
                    f"file_no「{file_no}」・生産面{side}のBOMが登録されていない、または対象部品がありません。",
                parent=self.winfo_toplevel())
            self.btn_register.config(state=tk.NORMAL if parts else tk.DISABLED)

        self._run_bom_expansion_async(
            lambda: _bom_service.expand_scrap_to_parts(scrap_record), on_success,
        )

    def _select_mounting_line(self, lines):
        """複数の実装ラインから1つを選ばせるモーダルダイアログ。キャンセルなら None（呼び出し元は展開を中断すること）。"""
        selected = {"value": None}

        # 最小化中だと transient のダイアログが表示されないため、先に元に戻す（UI_WORKFLOW_FIXES_NOTES.md）。
        if self.state() == "iconic":
            self.deiconify()

        dialog = tk.Toplevel(self)
        dialog.title("実装ラインの選択")
        dialog.transient(self)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="このファイルNo.には複数の実装ラインが存在します。\n"
                 "使用するラインを選択してください。",
        ).pack(padx=15, pady=(15, 5))

        combo = ttk.Combobox(dialog, values=lines, state="readonly", width=15)
        combo.current(0)
        combo.pack(padx=15, pady=5)

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=(5, 15))

        def on_ok():
            selected["value"] = combo.get()
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        ttk.Button(btn_frame, text="OK", command=on_ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="キャンセル", command=on_cancel).pack(side=tk.LEFT, padx=5)

        center_window(dialog, self)
        dialog.wait_window()
        return selected["value"]

    def _expand_from_file_no(self, file_no, side):
        """
        ファイルNo.＋生産面での展開（計画外）。計画を参照しないので、予定生産数の超過警告は出さない。
        TSV の実装ラインが複数あれば選ばせる（1件ならそのまま、0件なら None で BOMService の既定に任せる）。
        実装ラインの一覧取得と選択は UI スレッドで行い、BOM 展開だけを非同期にする。
        """
        ng_qty = self._resolve_ng_qty(file_no, side, None)
        if ng_qty is None:
            return

        try:
            lines = _bom_service.list_mounting_lines(file_no, side)
        except FileNotFoundError as e:
            messagebox.showerror("BOMエラー", f"BOM TSVが見つかりません：\n{e}", parent=self.winfo_toplevel())
            return
        except ValueError as e:
            messagebox.showerror("BOMエラー", f"BOM展開に失敗しました：\n{e}", parent=self.winfo_toplevel())
            return

        mounting_line = None
        if len(lines) == 1:
            mounting_line = lines[0]
        elif len(lines) >= 2:
            mounting_line = self._select_mounting_line(lines)
            if mounting_line is None:
                return

        scrap_record = {
            "setup_file_no": file_no,
            "production_side": side,
            "ng_qty": ng_qty,
            "lot_no": None,
            "mounting_line": mounting_line,
        }

        def on_success(parts):
            self.current_plan = {
                "kitting_list_no": file_no,
                "file_no": file_no,
                "side": side,
                "ng_qty": ng_qty,
                "is_unplanned": True,
                "lot_no": None,
            }
            line_text = f" / 実装ライン: {mounting_line}" if mounting_line else ""
            self.lbl_plan_info.config(
                text=f"file_no: {file_no} / 生産面: {side}{line_text} / （計画外登録）"
            )

            self.load_parts_tree(parts, ng_qty)

            if not parts:
                messagebox.showwarning(
                    "警告",
                    f"file_no「{file_no}」・生産面{side}のBOMが登録されていない、または対象部品がありません。",
                parent=self.winfo_toplevel())
            self.btn_register.config(state=tk.NORMAL if parts else tk.DISABLED)

        self._run_bom_expansion_async(
            lambda: _bom_service.expand_scrap_to_parts(scrap_record), on_success,
        )

    def load_parts_tree(self, parts, ng_qty):
        """
        展開結果を表示し直す（前の内容を消してから作る。全行チェック済み）。
        基板自身の行（item_type="board"）は区分列に「基板」と出す（96コード列に文字を足すと、保存時に96コードを書き換えてしまうため）。
        """
        self.tree.clear()
        for part in parts:
            qty_per_product = (part["qty"] / ng_qty) if ng_qty else 0
            item_type_label = "基板" if part.get("item_type") == "board" else ""
            self.tree.insert_row(
                part["part_no"],
                (part["part_no"], item_type_label, f"{qty_per_product:g}", f"{part['qty']:g}"),
                checked=True,
            )

    def on_register(self):
        """
        チェックした部品を登録する。その計画・面の既存の scrap_records を削除してから登録し直す（後からの展開・登録を正とする）。
        上書きになる場合だけ、事前に確認する。
        """
        if not self.current_plan:
            return

        checked_iids = self.tree.get_checked_iids()
        if not checked_iids:
            messagebox.showwarning("入力エラー", "仕損として登録する部品を選択してください。", parent=self.winfo_toplevel())
            return

        report_date = datetime.now().strftime("%Y-%m-%d")
        kitting_list_no = self.current_plan["kitting_list_no"]
        file_no = self.current_plan["file_no"]
        side = self.current_plan["side"]
        lot_no = self.current_plan.get("lot_no")
        is_unplanned = self.current_plan.get("is_unplanned", False)

        records = []
        for iid in checked_iids:
            # 列の位置ではなく列名で取る（位置で取り出していたとき、列を足して要素数が合わず例外になった）。
            part_no = self.tree.get_row_value(iid, "part_no")
            consumed_qty_text = self.tree.get_row_value(iid, "consumed_qty")
            try:
                consumed_qty = float(consumed_qty_text)
            except ValueError:
                continue
            records.append({"part_no": part_no, "ng_qty": consumed_qty})

        if not records:
            return

        existing = list_scrap_records_by_kitting_no(kitting_list_no, lot_no)
        if existing:
            if not messagebox.askyesno(
                "確認",
                f"キッティングリストNo. {kitting_list_no}"
                f"{f'（ロットNo. {lot_no}）' if lot_no else ''} の既存のNG登録内容"
                f"（{len(existing)}件）を置き換えます。よろしいですか？",
                parent=self.winfo_toplevel(),
            ):
                return

        replace_scrap_records(
            kitting_list_no, file_no, side, records, report_date,
            lot_no=lot_no, is_unplanned=is_unplanned,
        )
        log_operation(
            (self.current_worker or {}).get("name", "unknown"),
            "仕損登録",
            detail=f"{kitting_list_no}{f'（ロットNo. {lot_no}）' if lot_no else ''} / 面{side} / {len(records)}件",
        )

        messagebox.showinfo("登録完了", f"{len(records)}件の仕損実績を登録しました（{report_date}）。", parent=self.winfo_toplevel())

        # 登録内容を右ペインのNG一覧へ即時反映する
        self.load_ng_list()

    def _get_bulk_expand_targets(self):
        """
        「一括展開・登録」の対象（未展開で、対象外でない行）を返す。表示用に整形した _all_ng_rows ではなく、
        list_ng_declarations_latest() の生の値（production_side は int、ng_qty は float）を使う。
        """
        declarations = {
            (d["kitting_list_no"], d["lot_no"] or "", d["production_side"]): d
            for d in list_ng_declarations_latest()
        }
        expanded_keys = {
            (s["kitting_list_no"], s["lot_no"] or "", s["production_side"])
            for s in list_scrap_summary_by_kitting_no()
        }
        excluded_keys = {
            (e["kitting_list_no"], e["lot_no"] or "", e["production_side"])
            for e in list_ng_exclusions()
        }

        return [
            declaration
            for key, declaration in declarations.items()
            if key not in expanded_keys and key not in excluded_keys
        ]

    def _bulk_expand_and_register_one(self, target, report_date):
        """
        一括展開・登録の1行分（バックグラウンドで実行するので、ウィジェットには触れない）。
        候補が複数ある行は、ダイアログを出せないのでエラーにする（ほかの行は続ける）。
        チェックの確認はせず、展開した全部品を登録する（対象は未展開の行なので、実質は新規追加）。
        """
        kitting_list_no = target["kitting_list_no"]
        lot_no = target["lot_no"]
        is_unplanned = bool(target["is_unplanned"])
        ng_qty = target["ng_qty"]

        if is_unplanned:
            file_no = target["file_no"]
            side = int(target["production_side"])

            lines = _bom_service.list_mounting_lines(file_no, side)
            mounting_line = None
            if len(lines) == 1:
                mounting_line = lines[0]
            elif len(lines) >= 2:
                raise ValueError(
                    f"複数の実装ライン候補（{', '.join(lines)}）が存在するため、"
                    "一括処理では自動選択できません。個別に展開・登録してください。"
                )

            scrap_record = {
                "setup_file_no": file_no,
                "production_side": side,
                "ng_qty": ng_qty,
                "lot_no": None,
                "mounting_line": mounting_line,
            }
        else:
            plan, candidates = search_plan_by_kitting_no(kitting_list_no, lot_no)
            if candidates is not None:
                raise ValueError(
                    "複数の計画候補が見つかったため、一括処理では自動選択できません。"
                    "個別に展開・登録してください。"
                )
            if not plan:
                raise ValueError(f"キッティングリストNo. {kitting_list_no} の計画が見つかりません。")

            file_no = plan["setup_file_no"]
            try:
                side = int(plan["production_side"])
            except (TypeError, ValueError):
                raise ValueError(
                    f"生産面（production_side）を数値として解釈できません: {plan['production_side']!r}"
                )

            scrap_record = {
                "setup_file_no": file_no,
                "production_side": side,
                "ng_qty": ng_qty,
                "lot_no": plan.get("lot_no"),
                "mounting_line": plan.get("mounting_line"),
            }

        parts = _bom_service.expand_scrap_to_parts(scrap_record)
        if not parts:
            raise ValueError(f"file_no「{file_no}」・生産面{side}のBOMが登録されていない、または対象部品がありません。")

        records = [{"part_no": part["part_no"], "ng_qty": part["qty"]} for part in parts]
        replace_scrap_records(
            kitting_list_no, file_no, side, records, report_date,
            lot_no=lot_no, is_unplanned=is_unplanned,
        )

    def _run_bulk_expand_worker(self, targets, report_date):
        """
        対象行を1件ずつ処理する。1件のエラーで全体を止めない。戻り値は {"total", "success_count", "failures"}。
        """
        success_count = 0
        failures = []
        for target in targets:
            side = target["production_side"]
            label = f"キッティングリストNo. {target['kitting_list_no']}"
            if target["lot_no"]:
                label += f"（ロットNo. {target['lot_no']}）"
            label += f" / 面{side}"
            try:
                self._bulk_expand_and_register_one(target, report_date)
                success_count += 1
            except Exception as e:
                failures.append({"label": label, "error": str(e)})

        return {"total": len(targets), "success_count": success_count, "failures": failures}

    def _show_bulk_expand_result(self, result):
        total = result["total"]
        success_count = result["success_count"]
        failures = result["failures"]
        failure_count = len(failures)

        lines = [f"対象: {total}件", f"成功: {success_count}件", f"失敗: {failure_count}件"]
        if failures:
            max_show = 20
            lines.append("")
            lines.append("【エラー内容】")
            for f in failures[:max_show]:
                lines.append(f"・{f['label']}\n  {f['error']}")
            if failure_count > max_show:
                lines.append(f"…ほか{failure_count - max_show}件")

        message = "\n".join(lines)
        if failure_count:
            messagebox.showwarning("一括展開・登録 完了", message, parent=self.winfo_toplevel())
        else:
            messagebox.showinfo("一括展開・登録 完了", message, parent=self.winfo_toplevel())

    def on_bulk_expand_register(self):
        """
        未展開で対象外でない行を、すべて一括で展開・登録する（チェックの確認はしない）。別スレッドで実行する。
        _run_bom_expansion_async() は1件用なので使わず、行ごとの成否を追う。想定外の例外もキュー経由でダイアログに出す（アプリを落とさない）。
        """
        targets = self._get_bulk_expand_targets()
        if not targets:
            messagebox.showinfo(
                "一括展開・登録", "対象となる未展開項目（対象外を除く）がありません。",
                parent=self.winfo_toplevel(),
            )
            return

        if not messagebox.askyesno(
            "確認",
            f"未展開（対象外を除く）の{len(targets)}件を一括展開・登録します。よろしいですか？",
            parent=self.winfo_toplevel(),
        ):
            return

        report_date = datetime.now().strftime("%Y-%m-%d")
        loading = LoadingWindow(self, message=f"一括展開・登録中です（{len(targets)}件）…")
        result_queue = queue.Queue()

        def _work():
            try:
                result_queue.put((True, self._run_bulk_expand_worker(targets, report_date)))
            except Exception as e:
                import traceback
                result_queue.put((False, f"{e}\n{traceback.format_exc()}"))

        threading.Thread(target=_work, daemon=True).start()

        def _poll():
            try:
                success, payload = result_queue.get_nowait()
            except queue.Empty:
                self.after(200, _poll)
                return

            loading.destroy()

            if not success:
                messagebox.showerror(
                    "一括展開・登録エラー", f"予期しないエラーが発生しました。\n\n{payload}",
                    parent=self.winfo_toplevel(),
                )
                return

            self._show_bulk_expand_result(payload)
            log_operation(
                (self.current_worker or {}).get("name", "unknown"),
                "NG一括展開・登録",
                detail=f"対象{payload['total']}件 / 成功{payload['success_count']}件 / "
                       f"失敗{len(payload['failures'])}件",
            )
            # 状態（未展開→展開済み）を反映するため、NG一覧を再取得する
            self.load_ng_list()

        self.after(200, _poll)

    # ------------------------------------------------------------------
    # NG一覧（右ペイン）
    # ------------------------------------------------------------------

    def _add_ng_filter_entry(self, parent, col_key, label_text, width):
        """絞り込みエリアに列1つ分のラベル+Entryを追加し、StringVarを登録する。"""
        ttk.Label(parent, text=f"{label_text}:").pack(side=tk.LEFT, padx=(5, 2))
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.pack(side=tk.LEFT, padx=(0, 5))
        entry.bind("<KeyRelease>", self.apply_ng_filters)
        self._ng_filter_vars[col_key] = var

    def _add_ng_checkbox_filter_button(self, parent, col_key):
        """絞り込みエリアに、エクセルのオートフィルタ風チェックボックス式ポップアップを開く▼ボタンを追加する。"""
        label_text = self._ng_filter_labels[col_key]
        button = tk.Button(
            parent, text=f"{label_text} ▼", relief=tk.RAISED,
            command=lambda c=col_key: self.open_ng_checkbox_filter_popup(c),
        )
        button.pack(side=tk.LEFT, padx=(5, 5))
        self._ng_checkbox_buttons[col_key] = button
        self._ng_checkbox_default_bg = button.cget("background")

    def _update_ng_filter_button_style(self, col_key):
        button = self._ng_checkbox_buttons.get(col_key)
        if button is None:
            return
        label_text = self._ng_filter_labels[col_key]
        active = col_key in self._ng_checkbox_filters
        button.configure(
            text=f"{label_text} ▼●" if active else f"{label_text} ▼",
            background="#cfe8ff" if active else self._ng_checkbox_default_bg,
        )

    def open_ng_checkbox_filter_popup(self, col_key):
        """ロットNo./基板名/file_no/生産面/計画外 のチェックボックス式絞り込みポップアップを開く（生産実績入力画面と同じ設計）。"""
        label_text = self._ng_filter_labels[col_key]
        col_index = self._ng_col_index[col_key]

        other_predicates = self._ng_filter_predicates()
        other_predicates.pop(col_key, None)
        if other_predicates:
            base_rows = [row for row in self._all_ng_rows if self._ng_row_matches(row, other_predicates)]
        else:
            base_rows = self._all_ng_rows

        full_values = sorted({str(row[col_index]) for row in base_rows})

        current_selection = self._ng_checkbox_filters.get(col_key)
        checked_values = set(full_values) if current_selection is None else set(current_selection)

        # 最小化中だと transient のダイアログが表示されないため、先に元に戻す（UI_WORKFLOW_FIXES_NOTES.md）。
        if self.state() == "iconic":
            self.deiconify()

        popup = tk.Toplevel(self)
        popup.title(f"{label_text} の絞り込み")
        popup.geometry("280x420")
        center_window(popup, self)
        popup.transient(self)
        popup.grab_set()

        ttk.Label(popup, text="検索：").pack(anchor=tk.W, padx=10, pady=(10, 0))
        search_var = tk.StringVar()
        search_entry = ttk.Entry(popup, textvariable=search_var)
        search_entry.pack(fill=tk.X, padx=10, pady=(0, 5))
        search_entry.focus_set()

        list_outer = ttk.Frame(popup)
        list_outer.pack(expand=True, fill=tk.BOTH, padx=10)

        canvas = tk.Canvas(list_outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_outer, orient="vertical", command=canvas.yview)
        checklist_frame = ttk.Frame(canvas)
        checklist_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=checklist_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        check_vars = {value: tk.BooleanVar(value=(value in checked_values)) for value in full_values}
        row_widgets = {
            value: ttk.Checkbutton(checklist_frame, text=value, variable=check_vars[value])
            for value in full_values
        }

        def rebuild_visible(*_args):
            needle = search_var.get().strip().lower()
            for widget in row_widgets.values():
                widget.pack_forget()
            for value in full_values:
                if needle and needle not in value.lower():
                    continue
                row_widgets[value].pack(anchor=tk.W, fill=tk.X)

        rebuild_visible()
        search_var.trace_add("write", rebuild_visible)

        btn_frame1 = ttk.Frame(popup)
        btn_frame1.pack(fill=tk.X, padx=10, pady=(5, 0))

        def select_all():
            for var in check_vars.values():
                var.set(True)

        def deselect_all():
            for var in check_vars.values():
                var.set(False)

        ttk.Button(btn_frame1, text="全選択", command=select_all).pack(side=tk.LEFT)
        ttk.Button(btn_frame1, text="全解除", command=deselect_all).pack(side=tk.LEFT, padx=(5, 0))

        btn_frame2 = ttk.Frame(popup)
        btn_frame2.pack(fill=tk.X, padx=10, pady=10)

        def on_ok():
            selected = {value for value, var in check_vars.items() if var.get()}
            if selected == set(full_values):
                self._ng_checkbox_filters.pop(col_key, None)
            else:
                self._ng_checkbox_filters[col_key] = selected
            self._update_ng_filter_button_style(col_key)
            popup.destroy()
            self.apply_ng_filters()

        def on_cancel():
            popup.destroy()

        ttk.Button(btn_frame2, text="OK", command=on_ok).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        ttk.Button(btn_frame2, text="キャンセル", command=on_cancel).pack(side=tk.LEFT, expand=True, fill=tk.X)

        return popup

    @staticmethod
    def _fetch_ng_list_rows():
        """
        NG 一覧の DB アクセスだけを行う（ウィジェットに触れない）。
        NG 申告と展開済み（scrap_records）を (kitting_list_no, lot_no, production_side) で合わせ、状態を付ける:
        未展開（申告だけ）／展開済み（両方）／展開済み（申告記録なし）（展開済みだけ。機能の導入前のデータなど）。
        キーに lot_no を含めるのは kitting_list_no が重複するため。対象外の行も一覧から消さず、「対象外」列で示す（解除の導線を残す）。
        """
        declarations = {
            (d["kitting_list_no"], d["lot_no"] or "", d["production_side"]): d
            for d in list_ng_declarations_latest()
        }
        expanded = {
            (s["kitting_list_no"], s["lot_no"] or "", s["production_side"]): s
            for s in list_scrap_summary_by_kitting_no()
        }
        excluded_keys = {
            (e["kitting_list_no"], e["lot_no"] or "", e["production_side"])
            for e in list_ng_exclusions()
        }

        rows = []
        for key in sorted(set(declarations) | set(expanded)):
            kitting_list_no, lot_no, side = key
            declaration = declarations.get(key)
            summary = expanded.get(key)

            if declaration and summary:
                status = "展開済み"
            elif declaration:
                status = "未展開"
            else:
                status = "展開済み（申告記録なし）"

            representative = summary or declaration
            is_unplanned = bool(representative["is_unplanned"])
            file_no = representative["file_no"]

            board_name = ""
            if not is_unplanned:
                plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no) if lot_no \
                    else find_plan_item_by_kitting_no(kitting_list_no)
                if plan:
                    board_name = plan["board_name"] or ""

            declared_ng_qty_text = f"{declaration['ng_qty']:g}" if declaration else ""
            part_count_text = str(summary["part_count"]) if summary else "0"
            record_count_text = str(summary["record_count"]) if summary else "0"
            last_report_date = (summary["last_report_date"] if summary else None) or \
                (declaration["report_date"] if declaration else "")

            rows.append((
                kitting_list_no,
                lot_no,
                board_name,
                file_no,
                f"面{side}" if side in (1, 2) else str(side),
                "計画外" if is_unplanned else "",
                status,
                declared_ng_qty_text,
                part_count_text,
                record_count_text,
                last_report_date,
                "対象外" if key in excluded_keys else "",
            ))

        return rows

    def _populate_ng_tree(self, rows):
        for item in self.tree_ng_list.get_children():
            self.tree_ng_list.delete(item)
        for values in rows:
            self.tree_ng_list.insert("", tk.END, values=values)

    def load_ng_list(self):
        """DB取得とTreeview更新をまとめて同期的に行う（「更新」ボタン・NG登録直後から使用）。"""
        rows = self._fetch_ng_list_rows()
        self._all_ng_rows = rows
        for var in self._ng_filter_vars.values():
            var.set("")
        self._ng_checkbox_filters.clear()
        for col_key in self._ng_checkbox_buttons:
            self._update_ng_filter_button_style(col_key)
        self._populate_ng_tree(rows)

    def _ng_filter_predicates(self):
        predicates = {}
        for col_key, var in self._ng_filter_vars.items():
            text = var.get().strip()
            if not text:
                continue
            needle = text.lower()
            predicates[col_key] = lambda value, needle=needle: needle in value.lower()

        for col_key, selected_values in self._ng_checkbox_filters.items():
            predicates[col_key] = lambda value, selected=selected_values: value in selected

        return predicates

    def _ng_row_matches(self, row, predicates):
        for col_key, predicate in predicates.items():
            col_index = self._ng_col_index[col_key]
            if not predicate(str(row[col_index])):
                return False
        return True

    def apply_ng_filters(self, event=None):
        predicates = self._ng_filter_predicates()
        if not predicates:
            filtered = self._all_ng_rows
        else:
            filtered = [row for row in self._all_ng_rows if self._ng_row_matches(row, predicates)]
        self._populate_ng_tree(filtered)

    def clear_ng_filters(self):
        for var in self._ng_filter_vars.values():
            var.set("")
        self._ng_checkbox_filters.clear()
        for col_key in self._ng_checkbox_buttons:
            self._update_ng_filter_button_style(col_key)
        self.apply_ng_filters()

    def sort_ng_list(self, col):
        numeric_cols = {"declared_ng_qty", "part_count", "record_count"}

        def sort_key(value):
            if col in numeric_cols:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

        ascending = self._ng_sort_states.get(col, True)

        items = [
            (self.tree_ng_list.set(iid, col), iid)
            for iid in self.tree_ng_list.get_children("")
        ]
        items.sort(key=lambda t: sort_key(t[0]), reverse=not ascending)

        for index, (_, iid) in enumerate(items):
            self.tree_ng_list.move(iid, "", index)

        self._ng_sort_states[col] = not ascending
