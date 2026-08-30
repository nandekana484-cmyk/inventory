# ui/ng_input_window.py
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

from services.production_service import search_plan_by_kitting_no
from services.bom_service import BOMService
from models.scrap_records import (
    list_scrap_summary_by_kitting_no,
    list_scrap_records_by_kitting_no,
    replace_scrap_records,
)
from models.kitting_plan import find_plan_item_by_kitting_no
from models.ng_declarations import get_ng_declaration, list_ng_declarations_latest
from ui.checkable_treeview import CheckableTreeview

_bom_service = BOMService()


class NgInputWindow(tk.Toplevel):
    """
    NG（仕損）実績入力画面。

    入力フロー：
      1a. キッティングリストNo.を入力（計画あり登録）
          ── または ──
      1b. ファイルNo.＋生産面を入力（計画外登録。kitting_plan_itemsに対応する
          計画が存在しない場合。キッティングリストNo.欄が空欄の場合にこちらが
          使われる）
      2. NG数量（枚数）を入力。生産実績入力画面（ui.kitting_production_entry.py）で
         当日既に申告済み（models.ng_declarations）であれば、Entryが空欄の場合に
         限り自動的に表示される（_resolve_ng_qty()）
      3. 「展開」ボタン →
         1aの場合：計画からfile_no・生産面を特定し、search_plan_by_kitting_no()で
                   計画情報を取得した上でBOM展開
         1bの場合：入力されたfile_no・生産面をそのまま使い、計画を介さず
                   BOMService.expand_scrap_to_parts() を直接呼んでBOM展開
                   （planned_qty超過警告は計画が無いためスキップする）
      4. 使用部品一覧（96コードごとの消費数量）をCheckableTreeview（ui.checkable_treeview）に
         表示。デフォルト全選択状態。「全選択」「全解除」ボタンで一括切替可能
      5. 実際に仕損とする部品のチェックを必要に応じて外す
      6. 「仕損登録」ボタン → チェック済み行のみ models.scrap_records.replace_scrap_records() で
         保存（対象kitting_list_no・production_sideの既存レコードは全て削除してから
         登録し直すdelete-then-insert。既存レコードがある場合＝上書きになる場合のみ
         事前に確認ダイアログを表示する。1bの場合は is_unplanned=True を渡す）
      7. 保存済みのscrap_recordsは services.inventory_diff_service 側で
         96コード単位に集計され、在庫差異レポートへ反映される
         （is_unplannedの有無に関わらず、part_no単位のSUMに含まれる）
      8. 右ペインのNG一覧（kitting_list_no・production_side単位の集計、
         models.ng_declarations×models.scrap_records のアプリ層マージ）は、
         「未展開」（申告のみ）／「展開済み」（申告＋展開済み）／
         「展開済み（申告記録なし）」（展開済みデータのみ）の3状態を区別して表示する。
         行をダブルクリックすると、その計画（または計画外のfile_no＋生産面）が
         自動的に再展開される（on_ng_list_double_click()）
    """
    def __init__(self, parent, current_worker):
        super().__init__(parent)
        self.current_worker = current_worker
        # {"kitting_list_no", "file_no", "side", "ng_qty", "is_unplanned"}
        # 計画外（is_unplanned=True）の場合、kitting_list_noには対応する計画が
        # 存在しないため file_no をそのまま流用する（scrap_records.kitting_list_no は
        # NOT NULL制約があり、実在するkitting_list_noの命名規則
        # "{file_no}-{side}-{種別}-{日付}-{連番}" とは形が異なるため実データと
        # 衝突しない）。
        self.current_plan = None

        # NG一覧（右ペイン）の絞り込み基盤：ui.kitting_production_entry.py の
        # 計画一覧（tree_plan_list）と同じパターンをNG一覧用に再実装したもの
        # （フィルタ・ソートのロジック自体はインスタンス状態に強く依存しているため、
        # そのまま流用はできず、同じ設計をコピー＆適応している）。
        # - _all_ng_rows：_fetch_ng_list_rows() の全件結果（フィルタ前）。
        # - _ng_filter_vars：列key -> テキスト部分一致フィルタ入力欄のStringVar。
        # - _ng_checkbox_filters：列key -> 選択済み値の集合（チェックボックス式）。
        # - _ng_checkbox_buttons：列key -> ▼ボタンウィジェット。
        # - _ng_col_index：列key -> rowタプル内でのインデックス。
        self._all_ng_rows = []
        self._ng_filter_vars = {}
        self._ng_checkbox_filters = {}
        self._ng_checkbox_buttons = {}
        self._ng_col_index = {}
        self._ng_filter_labels = {}
        self._ng_sort_states = {}

        self.title("NG（仕損）入力")
        self.geometry("1150x600")

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
                ("qty_per_product", "1台あたり数量", 140, tk.E),
                ("consumed_qty", "消費数量（NG数×員数）", 180, tk.E),
            ],
            height=10,
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
        """
        右ペイン（NG一覧）のウィジェットを構築する。ui.kitting_production_entry.py の
        計画一覧（create_widgets()内、tree_plan_list関連部分）と同じ構成
        （絞り込みエリア→Treeview→水平/垂直スクロールバー→更新ボタン、のpack順）を
        NG一覧用に再実装したもの。
        """
        cols_ng = ("kitting_list_no", "lot_no", "board_name", "file_no", "side",
                   "is_unplanned", "status", "declared_ng_qty", "part_count",
                   "record_count", "last_report_date")
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

        vsb_ng = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_ng_list.yview)
        self.tree_ng_list.configure(yscrollcommand=vsb_ng.set)

        hsb_ng = ttk.Scrollbar(right_frame, orient="horizontal", command=self.tree_ng_list.xview)
        self.tree_ng_list.configure(xscrollcommand=hsb_ng.set)

        # pack順序：ui.kitting_production_entry.py の計画一覧と同じ理由により、
        # 「更新」ボタン→水平スクロールバーの順でside=tk.BOTTOMにpackする
        # （Tkのpackはpackを呼んだ順にcavityを消費するため、ボタンを先に確保しないと
        # 水平スクロールバーがウィンドウ最下端を先に取ってしまい、ボタンとの間に
        # 割り込んで視覚的に切り離された配置になる）。
        ttk.Button(right_frame, text="更新", command=self.load_ng_list).pack(
            side=tk.BOTTOM, fill=tk.X, pady=(5, 0)
        )
        hsb_ng.pack(side=tk.BOTTOM, fill=tk.X)
        vsb_ng.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_ng_list.pack(expand=True, fill=tk.BOTH)
        self.tree_ng_list.bind("<Double-1>", self.on_ng_list_double_click)

        self.load_ng_list()

    def on_ng_list_double_click(self, event):
        """
        NG一覧の行をダブルクリックすると、その行のkitting_list_no（計画あり）または
        file_no＋生産面（計画外、is_unplanned列で判定）を左ペインの検索欄へ反映し、
        on_expand()（「展開」ボタンと同じ処理）を自動実行する。

        NG数量（entry_ng_qty）はここでは変更しない。未入力・0以下の場合は
        on_expand()側の既存バリデーションがそのまま働き、通常の「展開」操作時と
        同じ入力エラーメッセージが表示される。

        計画あり行の場合、NG一覧は既にlot_no列（models.scrap_records.
        list_scrap_summary_by_kitting_no()等でkitting_list_noとあわせて集計済み）を
        持っているため、on_expand()へ渡す。実DBで同一kitting_list_noが複数の
        異なるlot_noにまたがって存在するケースが478件確認されており、これにより
        曖昧な単体検索（search_plan_by_kitting_no(kitting_list_no)のみ）を経由せず、
        一意に計画を特定できる。計画外行はlot_noを持たないため渡さない。
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

    def on_expand(self, lot_no=None):
        """
        lot_no：呼び出し元（NG一覧のダブルクリック等）が既にlot_noを把握している
        場合に渡す。指定があれば_expand_from_kitting_no()経由で
        search_plan_by_kitting_no(kitting_no, lot_no)による一意特定の経路を使う。
        省略時（検索欄への直接入力による「展開」ボタン操作）は従来通り
        曖昧な単体検索のまま（次のステップ：候補選択UIで対応予定）。
        ファイルNo.＋生産面検索（計画外）はlot_noを扱わないため無関係。
        """
        kitting_no = self.entry_kitting_no.get().strip()
        if kitting_no:
            # キッティングリストNo.が入力されていれば、従来通り計画あり登録として扱う
            # （ファイルNo.欄に何か入っていても無視する＝キッティングNo.優先）。
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
        NG数量Entryが空欄の場合、現在の申告（models.ng_declarations、生産実績
        入力画面 ui.kitting_production_entry.py で申告済み。get_ng_declaration()は
        report_dateを問わず「1計画・面＝1レコード」の現在値を返す）があれば
        自動的にEntryへ表示した上でその値を使う。Entryに既に値が入力されていれば
        そちらを優先する（申告値と異なる枚数で展開したい場合に上書きできるように
        するため）。

        lot_no：計画あり（1a）の場合は選択中の計画のlot_no、計画外（1b）の場合は
        None（get_ng_declaration()側でlot_no=NULLの申告のみに絞り込まれる）。

        数値として不正・0以下の場合はエラーメッセージを表示してNoneを返す
        （呼び出し元はNoneを受け取ったら展開処理を中断すること）。
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

    def _expand_from_kitting_no(self, kitting_no, lot_no=None):
        """
        キッティングリストNo.検索（計画あり）での展開。

        lot_no：呼び出し元が既にlot_noを把握している場合に渡すと、
        search_plan_by_kitting_no(kitting_no, lot_no)がfind_plan_item_by_kitting_no()
        を(kitting_list_no, lot_no)で呼び一意に計画を特定する。

        省略時（検索欄への直接入力による「展開」操作）で、該当kitting_no に複数の
        lot_no候補がある場合は、ui.plan_candidate_dialog.select_plan_candidate()
        でユーザーに選択させ、選ばれたlot_noで改めて検索し直す（候補が1件のみの
        場合はダイアログを経由せず従来通りそのまま確定する。ui.plan_candidate_dialog
        は元々、生産実績入力画面 ui.kitting_production_entry.KittingProductionEntryWindow
        と共用するために切り出したもの。同画面はキッティングリストNo.検索欄を廃止し
        計画一覧からの選択に一本化したため、現在この経路を使うのは本画面のみ）。
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

        try:
            parts = _bom_service.expand_scrap_to_parts({
                "setup_file_no": file_no,
                "production_side": side,
                "ng_qty": ng_qty,
                "lot_no": plan.get("lot_no"),
            })
        except FileNotFoundError as e:
            messagebox.showerror("BOMエラー", f"BOM TSVが見つかりません：\n{e}", parent=self.winfo_toplevel())
            return
        except ValueError as e:
            messagebox.showerror("BOMエラー", f"BOM展開に失敗しました：\n{e}", parent=self.winfo_toplevel())
            return

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

    def _expand_from_file_no(self, file_no, side):
        """
        ファイルNo.＋生産面検索（計画外）での展開。kitting_plan_itemsを一切参照しない
        （BOMService.expand_scrap_to_parts()はfile_no・sideのみで完結する設計のため）。
        計画が無いため、planned_qty超過警告は行わない。
        """
        ng_qty = self._resolve_ng_qty(file_no, side, None)
        if ng_qty is None:
            return

        try:
            parts = _bom_service.expand_scrap_to_parts({
                "setup_file_no": file_no,
                "production_side": side,
                "ng_qty": ng_qty,
                "lot_no": None,
            })
        except FileNotFoundError as e:
            messagebox.showerror("BOMエラー", f"BOM TSVが見つかりません：\n{e}", parent=self.winfo_toplevel())
            return
        except ValueError as e:
            messagebox.showerror("BOMエラー", f"BOM展開に失敗しました：\n{e}", parent=self.winfo_toplevel())
            return

        self.current_plan = {
            "kitting_list_no": file_no,
            "file_no": file_no,
            "side": side,
            "ng_qty": ng_qty,
            "is_unplanned": True,
            "lot_no": None,
        }
        self.lbl_plan_info.config(
            text=f"file_no: {file_no} / 生産面: {side} / （計画外登録）"
        )

        self.load_parts_tree(parts, ng_qty)

        if not parts:
            messagebox.showwarning(
                "警告",
                f"file_no「{file_no}」・生産面{side}のBOMが登録されていない、または対象部品がありません。",
            parent=self.winfo_toplevel())
        self.btn_register.config(state=tk.NORMAL if parts else tk.DISABLED)

    def load_parts_tree(self, parts, ng_qty):
        """
        展開結果をCheckableTreeviewへ反映する。呼び出しのたびに前回の内容を
        clear()してから作り直す（NG一覧からの再展開等、on_expand()が複数回
        呼ばれる場合に古い行が残らないようにするため）。デフォルト全選択状態
        （insert_row()のchecked=True）で表示する。
        """
        self.tree.clear()
        for part in parts:
            qty_per_product = (part["qty"] / ng_qty) if ng_qty else 0
            self.tree.insert_row(
                part["part_no"],
                (part["part_no"], f"{qty_per_product:g}", f"{part['qty']:g}"),
                checked=True,
            )

    def on_register(self):
        """
        選択された部品を登録する。対象kitting_list_noの既存scrap_records（あれば）は
        全て削除した上で、今回チェック済み（選択済み）の部品のみを登録し直す
        （delete-then-insert。「後からの展開・登録を正として上書きする」ため）。
        既存レコードがある場合＝上書きになる場合のみ、事前に確認ダイアログを表示する
        （初回登録の場合は確認不要）。
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
            part_no, _qty_per_product, consumed_qty_text = self.tree.get_row_values(iid)
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

        messagebox.showinfo("登録完了", f"{len(records)}件の仕損実績を登録しました（{report_date}）。", parent=self.winfo_toplevel())

        # 登録内容を右ペインのNG一覧へ即時反映する
        self.load_ng_list()

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
        """
        ロットNo./基板名/file_no/生産面/計画外用の、エクセルのオートフィルタ風
        チェックボックス式絞り込みポップアップを開く（ui.kitting_production_entry.py の
        open_plan_checkbox_filter_popup() と同じ設計）。
        """
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

        popup = tk.Toplevel(self)
        popup.title(f"{label_text} の絞り込み")
        popup.geometry("280x420")
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
        NG一覧のDBアクセス部分のみを行う（Tkinterウィジェットには一切触れない）。

        models.ng_declarations.list_ng_declarations_latest()（申告、kitting_list_no・
        lot_no・production_side単位の最新1件）と
        models.scrap_records.list_scrap_summary_by_kitting_no()（BOM展開済み、
        同じくkitting_list_no・lot_no・production_side単位）を、
        (kitting_list_no, lot_no, production_side) キーでアプリ層マージし、
        以下の「状態」を列として付与する：
          - 未展開：申告のみあり、まだBOM展開・scrap_records登録されていない
          - 展開済み：申告・展開済みデータの両方がある
          - 展開済み（申告記録なし）：scrap_recordsのみあり、申告記録がない
            （本機能導入前に登録された既存データ等）

        マージキーにlot_noを含める理由：実DBで同一kitting_list_noが複数の異なる
        lot_noにまたがって存在するケースが478件確認されており、含めないと
        別ロットの申告・展開済みデータが1行に誤って混同されてしまう。

        lot_noは、list_ng_declarations_latest()・list_scrap_summary_by_kitting_no()
        側で既にkitting_list_no・production_sideとあわせて集計済みの値をそのまま
        使う（改めて計画を検索し直さない）。is_unplanned=0（計画あり）の行のみ、
        そのlot_noを使って models.kitting_plan.find_plan_item_by_kitting_no()
        （kitting_list_no・lot_noの両方を渡すため一意に特定できる）で基板名を
        補完する（list_active_plan_items()は「1回目除外」ロジックがあるため
        使わない）。is_unplanned=1（計画外）の行は計画詳細が存在しないため
        基板名は空欄のままとする。

        戻り値：Treeviewへそのまま渡せる values タプルのリスト。
        """
        declarations = {
            (d["kitting_list_no"], d["lot_no"] or "", d["production_side"]): d
            for d in list_ng_declarations_latest()
        }
        expanded = {
            (s["kitting_list_no"], s["lot_no"] or "", s["production_side"]): s
            for s in list_scrap_summary_by_kitting_no()
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
