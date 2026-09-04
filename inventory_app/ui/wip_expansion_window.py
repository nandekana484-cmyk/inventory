# ui/wip_expansion_window.py
import threading
import queue

import tkinter as tk
from tkinter import ttk, messagebox

from services.bom_service import get_shared_bom_service
from models.wip_board_snapshot import list_wip_snapshot
from models.wip_scrap_records import save_wip_scrap_records, list_wip_scrap_summary
from ui.checkable_treeview import CheckableTreeview
from ui.loading_window import LoadingWindow

# アプリ全体で共有する単一のBOMServiceインスタンス（services.bom_service.
# get_shared_bom_service()参照）。ui.ng_input_window.pyと共有するため、
# 先にどちらの画面を開いても共有フォルダのインデックス構築は1回で済む。
_bom_service = get_shared_bom_service()


class WipExpansionWindow(tk.Toplevel):
    """
    仕掛（WIP）展開画面。

    月報（ui/monthly_report_window.py）の「仕掛数量抽出」で保存された
    models.wip_board_snapshot（仕掛基板一覧のスナップショット）を右ペインに
    一覧表示し、行をダブルクリックするとその基板の仕掛数量分をBOM展開して
    左ペインに部品一覧を表示する（ui/ng_input_window.py の左右ペイン構成を
    複製・適応したもの）。

    NG入力画面と異なり、本画面は展開結果の確認・閲覧のみを目的とする
    （DBへの登録操作は行わない。仕掛の部品はまだ消費されていない在庫として
    扱われるため、NGのように「登録」して確定させる対象ではない）。

    フロー：
      1. 右ペインの一覧（models.wip_board_snapshot.list_wip_snapshot()）から
         行をダブルクリック
      2. その行のfile_no・生産面・mounting_line・surplus_qty（仕掛数量）を使い、
         BOMService.expand_wip_to_parts() でBOM展開
      3. 展開結果（96コードごとの数量）をCheckableTreeview（ui.checkable_treeview）に
         表示。デフォルト全選択状態（閲覧用のため、チェックの意味自体は無いが、
         NG入力画面と見た目を揃えるため踏襲した）
    """
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker
        self.current_row = None

        # 右ペイン（仕掛一覧）の絞り込み基盤：ui.ng_input_window.py の
        # NG一覧（_all_ng_rows等）と同じパターンをWIP一覧用に再実装したもの。
        self._all_wip_rows = []
        self._wip_filter_vars = {}
        self._wip_checkbox_filters = {}
        self._wip_checkbox_buttons = {}
        self._wip_col_index = {}
        self._wip_filter_labels = {}
        self._wip_sort_states = {}

        self.title("仕掛展開")
        self.geometry("1150x600")

        self.create_widgets()

    def create_widgets(self):
        container = ttk.Frame(self)
        container.pack(expand=True, fill=tk.BOTH)

        left_frame = ttk.Frame(container)
        left_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        right_frame = ttk.Labelframe(container, text="仕掛基板一覧（スナップショット）", padding=5)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 15), pady=5)

        info_frame = ttk.LabelFrame(left_frame, text="対象仕掛基板（右の一覧をダブルクリックで選択）", padding=10)
        info_frame.pack(fill=tk.X, padx=15, pady=10)

        self.lbl_wip_info = ttk.Label(info_frame, text="-", foreground="blue")
        self.lbl_wip_info.pack(anchor=tk.W)

        parts_frame = ttk.LabelFrame(left_frame, text="使用部品一覧（展開結果・閲覧のみ）", padding=10)
        parts_frame.pack(expand=True, fill=tk.BOTH, padx=15, pady=(0, 10))

        self.tree = CheckableTreeview(
            parts_frame,
            columns=[
                ("part_no", "96コード", 220, tk.W),
                ("item_type_label", "区分", 70, tk.CENTER),
                ("qty_per_product", "1台あたり数量", 140, tk.E),
                ("consumed_qty", "消費数量（仕掛数量×員数）", 200, tk.E),
            ],
            height=10,
            # 消費数量のみダブルクリックで編集可能にする（96コード列は編集不可のまま。
            # 本画面は登録処理を持たないため、編集結果は表示上の確認・閲覧にのみ使う）。
            editable_columns={"consumed_qty"},
        )

        parts_btn_row = ttk.Frame(parts_frame)
        parts_btn_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(parts_btn_row, text="全選択", command=self.tree.select_all).pack(side=tk.LEFT)
        ttk.Button(parts_btn_row, text="全解除", command=self.tree.deselect_all).pack(side=tk.LEFT, padx=(5, 0))

        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(left_frame, padding=10)
        btn_frame.pack(fill=tk.X)
        self.btn_confirm = ttk.Button(
            btn_frame, text="仕掛確定登録", command=self.on_confirm, state=tk.DISABLED,
        )
        self.btn_confirm.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="仕掛製品レポート", command=self.open_wip_product_report).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="仕掛96レポート", command=self.open_wip_parts_report).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self._create_wip_list_widgets(right_frame)

    def open_wip_product_report(self):
        """循環import回避のため、ここで都度importする（ui.ng_input_window.open_product_ng_report()と同じ理由）。"""
        from ui.wip_product_report_window import WipProductReportWindow
        WipProductReportWindow(self)

    def open_wip_parts_report(self):
        """循環import回避のため、ここで都度importする。"""
        from ui.wip_parts_report_window import WipPartsReportWindow
        WipPartsReportWindow(self)

    # ------------------------------------------------------------------
    # 展開処理（左ペイン）
    # ------------------------------------------------------------------

    def on_wip_list_double_click(self, event):
        """
        仕掛一覧の行をダブルクリックすると、その行のfile_no・生産面・
        mounting_line・surplus_qty（仕掛数量）を使ってBOM展開する。
        """
        row_id = self.tree_wip_list.identify_row(event.y)
        if not row_id:
            return
        values = self.tree_wip_list.item(row_id, "values")

        kitting_list_no = values[self._wip_col_index["kitting_list_no"]]
        board_name = values[self._wip_col_index["board_name"]]
        file_no = values[self._wip_col_index["file_no"]]
        side_text = values[self._wip_col_index["side"]]
        lot_no = values[self._wip_col_index["lot_no"]] or None
        mounting_line = values[self._wip_col_index["mounting_line"]] or None
        surplus_qty_text = values[self._wip_col_index["surplus_qty"]]

        self._expand_row(kitting_list_no, board_name, file_no, side_text, lot_no, mounting_line, surplus_qty_text)

    def expand_by_identity(self, kitting_list_no, lot_no=None, production_side=None):
        """
        外部（ui.wip_product_report_window.WipProductReportWindow等）から、
        kitting_list_no（・lot_no・production_side）を指定して該当基板を
        自動展開するための入口（ui.product_ng_report_window.ProductNgReportWindow
        がui.ng_input_window.NgInputWindowの検索欄を埋めてon_expand()を呼ぶのと
        同じ役割）。

        self._all_wip_rows（_fetch_wip_list_rows()の結果、仕掛一覧の全件）から
        一致する行を検索し、on_wip_list_double_click()と同じ展開処理
        （_expand_row()）を呼ぶ。lot_no・production_sideを渡すとその条件でも
        絞り込む（省略時はkitting_list_noのみで一致した最初の行を使う）。

        一致する行が無い場合はエラーダイアログを表示してFalseを返す
        （呼び出し時点でwip_board_snapshotの内容が変わっている等、
        通常は起こらないはずだが念のため）。
        """
        idx = self._wip_col_index
        for row in self._all_wip_rows:
            if row[idx["kitting_list_no"]] != kitting_list_no:
                continue
            if lot_no is not None and (row[idx["lot_no"]] or None) != lot_no:
                continue
            if production_side is not None and str(row[idx["side"]]) != str(production_side):
                continue
            self._expand_row(
                row[idx["kitting_list_no"]], row[idx["board_name"]], row[idx["file_no"]],
                row[idx["side"]], row[idx["lot_no"]] or None, row[idx["mounting_line"]] or None,
                row[idx["surplus_qty"]],
            )
            return True

        messagebox.showerror(
            "検索エラー", f"キッティングリストNo. {kitting_list_no} の仕掛データが見つかりません。",
            parent=self.winfo_toplevel(),
        )
        return False

    def _run_bom_expansion_async(self, work_fn, on_success):
        """
        BOMService.expand_wip_to_parts()（共有フォルダへのファイルアクセスを
        伴い得るため実行時間が読めない）を非同期化する共通ヘルパー。
        ui.kitting_plan_import.KittingPlanImportWindow.on_start_import()等で
        確立済みのLoadingWindow＋threading.Thread(daemon=True)＋queue.Queue＋
        self.after(200,...)ポーリングパターンをそのまま踏襲する
        （ui.ng_input_window.NgInputWindow._run_bom_expansion_async()と同じ実装。
        両画面それぞれが小規模なUI部品を個別に持つ既存の設計方針
        （NG一覧・仕掛一覧のフィルタ・ソート実装が共通コンポーネント化されて
        いないのと同じ考え方）に合わせ、共通モジュールへの切り出しは行わず
        画面ごとに複製している）。

        呼び出し元（_expand_row()）は、入力検証・実装ライン特定（既に
        Treeview行またはexpand_by_identity()の呼び出し引数から確定済み）を
        あらかじめ済ませた上で、BOM展開部分のみをwork_fnとして渡す。

        on_success(parts)：展開成功時、UIスレッド上で呼ばれるコールバック。
        FileNotFoundError/ValueErrorはここで捕捉し従来と同じ文言でエラー
        ダイアログを表示、それ以外の例外はself.after()のコールバック内で
        再送出しTkinterの通常の例外報告に委ねる（元のコードの挙動を維持）。
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

    def _expand_row(self, kitting_list_no, board_name, file_no, side_text, lot_no, mounting_line, surplus_qty_text):
        """
        on_wip_list_double_click()・expand_by_identity()共通の展開処理本体
        （値の取得元がTreeviewの行かexpand_by_identity()の検索結果かの違いを
        吸収し、以降のバリデーション・BOM展開ロジックを一本化する）。

        入力検証まではUIスレッドで同期的に行い、実際のBOM展開（_bom_service.
        expand_wip_to_parts()）のみを_run_bom_expansion_async()で非同期化する。
        """
        try:
            side = int(side_text)
        except (TypeError, ValueError):
            messagebox.showerror("エラー", f"生産面を数値として解釈できません: {side_text!r}", parent=self.winfo_toplevel())
            return
        if side not in (1, 2):
            messagebox.showerror("エラー", f"生産面は1または2である必要があります（値: {side}）。", parent=self.winfo_toplevel())
            return

        try:
            surplus_qty = float(surplus_qty_text)
        except (TypeError, ValueError):
            messagebox.showerror("エラー", f"仕掛数量を数値として解釈できません: {surplus_qty_text!r}", parent=self.winfo_toplevel())
            return
        if surplus_qty <= 0:
            messagebox.showwarning("警告", "仕掛数量が0以下のため展開できません。", parent=self.winfo_toplevel())
            return

        self.current_row = {
            "kitting_list_no": kitting_list_no,
            "board_name": board_name,
            "file_no": file_no,
            "side": side,
            "lot_no": lot_no,
            "mounting_line": mounting_line,
            "surplus_qty": surplus_qty,
        }

        wip_record = {
            "setup_file_no": file_no,
            "production_side": side,
            "wip_qty": surplus_qty,
            "mounting_line": mounting_line,
            "lot_no": lot_no,
        }

        def on_success(parts):
            line_text = f" / 実装ライン: {mounting_line}" if mounting_line else ""
            self.lbl_wip_info.config(
                text=f"キッティングNo.: {kitting_list_no} / {file_no}（{board_name}） / "
                     f"生産面: {side} / ロットNo: {lot_no or '-'}{line_text} / 仕掛数量: {surplus_qty:g}"
            )

            self.load_parts_tree(parts, surplus_qty)

            if not parts:
                messagebox.showwarning(
                    "警告",
                    f"file_no「{file_no}」・生産面{side}のBOMが登録されていない、または対象部品がありません。",
                    parent=self.winfo_toplevel(),
                )
            self.btn_confirm.config(state=tk.NORMAL if parts else tk.DISABLED)

        self._run_bom_expansion_async(
            lambda: _bom_service.expand_wip_to_parts(wip_record), on_success,
        )

    def on_confirm(self):
        """
        選択された部品を仕掛展開結果として確定登録する。対象kitting_list_no・
        lot_no・production_sideの既存wip_scrap_records（あれば）は全て削除した上で、
        今回チェック済み（選択済み）の部品のみを登録し直す（delete-then-insert。
        ui.ng_input_window.NgInputWindow.on_register() と同じパターン）。
        """
        if not self.current_row:
            return

        checked_iids = self.tree.get_checked_iids()
        if not checked_iids:
            messagebox.showwarning("入力エラー", "確定登録する部品を選択してください。", parent=self.winfo_toplevel())
            return

        kitting_list_no = self.current_row["kitting_list_no"]
        file_no = self.current_row["file_no"]
        side = self.current_row["side"]
        lot_no = self.current_row.get("lot_no")
        mounting_line = self.current_row.get("mounting_line")

        records = []
        for iid in checked_iids:
            part_no = self.tree.get_row_value(iid, "part_no")
            consumed_qty_text = self.tree.get_row_value(iid, "consumed_qty")
            try:
                consumed_qty = float(consumed_qty_text)
            except ValueError:
                continue
            records.append({"part_no": part_no, "qty": consumed_qty})

        if not records:
            return

        save_wip_scrap_records(kitting_list_no, file_no, side, records, lot_no=lot_no, mounting_line=mounting_line)

        messagebox.showinfo(
            "登録完了", f"{len(records)}件の仕掛展開結果を確定登録しました。", parent=self.winfo_toplevel(),
        )

        # 登録内容を右ペインの仕掛一覧（状態列）へ即時反映する
        self.load_wip_list()

    def load_parts_tree(self, parts, wip_qty):
        """
        展開結果をCheckableTreeviewへ反映する。ui.ng_input_window.load_parts_tree()と
        同じく、呼び出しのたびに前回の内容をclear()してから作り直し、デフォルト
        全選択状態で表示する（本画面では選択状態自体は登録に使わないが、
        NG入力画面と見た目を揃えるため踏襲した）。

        item_type="board"（基板自身、K行の96コード）の行は、区分列に「基板」と
        表示して通常部品と区別する（ui.ng_input_window.load_parts_tree()と同じ方針）。
        """
        self.tree.clear()
        for part in parts:
            qty_per_product = (part["qty"] / wip_qty) if wip_qty else 0
            item_type_label = "基板" if part.get("item_type") == "board" else ""
            self.tree.insert_row(
                part["part_no"],
                (part["part_no"], item_type_label, f"{qty_per_product:g}", f"{part['qty']:g}"),
                checked=True,
            )

    # ------------------------------------------------------------------
    # 仕掛一覧（右ペイン）
    # ------------------------------------------------------------------

    def _create_wip_list_widgets(self, right_frame):
        """
        右ペイン（仕掛一覧）のウィジェットを構築する。ui.ng_input_window.py の
        NG一覧（_create_ng_list_widgets()）と同じ構成
        （絞り込みエリア→Treeview→水平/垂直スクロールバー→更新ボタン、のpack順）を
        WIP一覧用に再実装したもの。
        """
        cols_wip = ("kitting_list_no", "board_name", "file_no", "side", "lot_no",
                    "mounting_line", "surplus_qty", "status", "created_at")
        self._wip_col_index = {key: i for i, key in enumerate(cols_wip)}

        self._wip_filter_labels = {
            "kitting_list_no": "キッティングNo.",
            "board_name": "基板名",
            "file_no": "file_no",
            "side": "生産面",
            "lot_no": "ロットNo.",
            "mounting_line": "実装ライン",
            "surplus_qty": "仕掛数量",
            "status": "状態",
            "created_at": "抽出日時",
        }

        wip_filter_frame = ttk.LabelFrame(right_frame, text="絞り込み", padding=8)
        wip_filter_frame.pack(fill=tk.X, pady=(0, 5))

        wip_filter_row1 = ttk.Frame(wip_filter_frame)
        wip_filter_row1.pack(fill=tk.X, pady=(0, 4))
        wip_filter_row2 = ttk.Frame(wip_filter_frame)
        wip_filter_row2.pack(fill=tk.X)

        # テキスト部分一致：キッティングNo./仕掛数量/抽出日時
        self._add_wip_filter_entry(wip_filter_row1, "kitting_list_no", self._wip_filter_labels["kitting_list_no"], width=14)
        # チェックボックス式ポップアップ：候補が限られる列（基板名/file_no/生産面/ロットNo./実装ライン）
        self._add_wip_checkbox_filter_button(wip_filter_row1, "board_name")
        self._add_wip_checkbox_filter_button(wip_filter_row1, "file_no")
        self._add_wip_checkbox_filter_button(wip_filter_row1, "side")
        self._add_wip_checkbox_filter_button(wip_filter_row1, "lot_no")
        self._add_wip_checkbox_filter_button(wip_filter_row1, "mounting_line")
        self._add_wip_checkbox_filter_button(wip_filter_row1, "status")

        self._add_wip_filter_entry(wip_filter_row2, "surplus_qty", self._wip_filter_labels["surplus_qty"], width=8)
        self._add_wip_filter_entry(wip_filter_row2, "created_at", self._wip_filter_labels["created_at"], width=16)

        ttk.Button(
            wip_filter_row2, text="絞り込みクリア", command=self.clear_wip_filters
        ).pack(side=tk.LEFT, padx=(15, 0))

        self.tree_wip_list = ttk.Treeview(right_frame, columns=cols_wip, show="headings")
        for col_key in cols_wip:
            self.tree_wip_list.heading(
                col_key, text=self._wip_filter_labels[col_key],
                command=lambda c=col_key: self.sort_wip_list(c),
            )
        self.tree_wip_list.column("kitting_list_no", width=140, anchor=tk.W)
        self.tree_wip_list.column("board_name", width=140, anchor=tk.W)
        self.tree_wip_list.column("file_no", width=90, anchor=tk.W)
        self.tree_wip_list.column("side", width=60, anchor=tk.CENTER)
        self.tree_wip_list.column("lot_no", width=100, anchor=tk.W)
        self.tree_wip_list.column("mounting_line", width=90, anchor=tk.W)
        self.tree_wip_list.column("surplus_qty", width=90, anchor=tk.E)
        self.tree_wip_list.column("status", width=90, anchor=tk.CENTER)
        self.tree_wip_list.column("created_at", width=140, anchor=tk.W)

        vsb_wip = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_wip_list.yview)
        self.tree_wip_list.configure(yscrollcommand=vsb_wip.set)

        hsb_wip = ttk.Scrollbar(right_frame, orient="horizontal", command=self.tree_wip_list.xview)
        self.tree_wip_list.configure(xscrollcommand=hsb_wip.set)

        # pack順序：ui.ng_input_window.py のNG一覧と同じ理由により、
        # 「更新」ボタン→水平スクロールバーの順でside=tk.BOTTOMにpackする。
        ttk.Button(right_frame, text="更新", command=self.load_wip_list).pack(
            side=tk.BOTTOM, fill=tk.X, pady=(5, 0)
        )
        hsb_wip.pack(side=tk.BOTTOM, fill=tk.X)
        vsb_wip.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree_wip_list.pack(expand=True, fill=tk.BOTH)
        self.tree_wip_list.bind("<Double-1>", self.on_wip_list_double_click)

        self.load_wip_list()

    def _add_wip_filter_entry(self, parent, col_key, label_text, width):
        ttk.Label(parent, text=f"{label_text}:").pack(side=tk.LEFT, padx=(5, 2))
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.pack(side=tk.LEFT, padx=(0, 5))
        entry.bind("<KeyRelease>", self.apply_wip_filters)
        self._wip_filter_vars[col_key] = var

    def _add_wip_checkbox_filter_button(self, parent, col_key):
        label_text = self._wip_filter_labels[col_key]
        button = tk.Button(
            parent, text=f"{label_text} ▼", relief=tk.RAISED,
            command=lambda c=col_key: self.open_wip_checkbox_filter_popup(c),
        )
        button.pack(side=tk.LEFT, padx=(5, 5))
        self._wip_checkbox_buttons[col_key] = button
        self._wip_checkbox_default_bg = button.cget("background")

    def _update_wip_filter_button_style(self, col_key):
        button = self._wip_checkbox_buttons.get(col_key)
        if button is None:
            return
        label_text = self._wip_filter_labels[col_key]
        active = col_key in self._wip_checkbox_filters
        button.configure(
            text=f"{label_text} ▼●" if active else f"{label_text} ▼",
            background="#cfe8ff" if active else self._wip_checkbox_default_bg,
        )

    def open_wip_checkbox_filter_popup(self, col_key):
        """
        基板名/file_no/生産面/ロットNo./実装ライン用の、エクセルのオートフィルタ風
        チェックボックス式絞り込みポップアップを開く（ui.ng_input_window.py の
        open_ng_checkbox_filter_popup() と同じ設計）。
        """
        label_text = self._wip_filter_labels[col_key]
        col_index = self._wip_col_index[col_key]

        other_predicates = self._wip_filter_predicates()
        other_predicates.pop(col_key, None)
        if other_predicates:
            base_rows = [row for row in self._all_wip_rows if self._wip_row_matches(row, other_predicates)]
        else:
            base_rows = self._all_wip_rows

        full_values = sorted({str(row[col_index]) for row in base_rows})

        current_selection = self._wip_checkbox_filters.get(col_key)
        checked_values = set(full_values) if current_selection is None else set(current_selection)

        # selfが最小化状態だと、transient(self)したポップアップがstate()="withdrawn"
        # のまま実際には表示されない（ui.plan_candidate_dialog._show_candidate_list_dialog()
        # と同じ理由・同じ対策、UI_WORKFLOW_FIXES_NOTES.md参照）。
        if self.state() == "iconic":
            self.deiconify()

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
                self._wip_checkbox_filters.pop(col_key, None)
            else:
                self._wip_checkbox_filters[col_key] = selected
            self._update_wip_filter_button_style(col_key)
            popup.destroy()
            self.apply_wip_filters()

        def on_cancel():
            popup.destroy()

        ttk.Button(btn_frame2, text="OK", command=on_ok).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 5))
        ttk.Button(btn_frame2, text="キャンセル", command=on_cancel).pack(side=tk.LEFT, expand=True, fill=tk.X)

        return popup

    @staticmethod
    def _fetch_wip_list_rows():
        """
        WIP一覧のDBアクセス部分のみを行う（Tkinterウィジェットには一切触れない）。
        models.wip_board_snapshot.list_wip_snapshot() の内容に、
        models.wip_scrap_records.list_wip_scrap_summary()（確定登録済みの
        仕掛展開結果）を(kitting_list_no, lot_no, production_side)キーで
        突き合わせ、「確定済み」「未確定」の状態列を付与する
        （ui.ng_input_window._fetch_ng_list_rows()の未展開／展開済み判定と同じ考え方）。

        キー比較時、wip_board_snapshot.production_sideはTEXT列・
        wip_scrap_records.production_sideはINTEGER列と型が異なるため、
        str()で揃えてから比較する。
        """
        confirmed_keys = {
            (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
            for s in list_wip_scrap_summary()
        }

        rows = []
        for row in list_wip_snapshot():
            key = (row["kitting_list_no"], row["lot_no"] or "", str(row["production_side"]))
            status = "確定済み" if key in confirmed_keys else "未確定"
            rows.append((
                row["kitting_list_no"],
                row["board_name"],
                row["file_no"],
                row["production_side"],
                row["lot_no"] or "",
                row["mounting_line"] or "",
                f"{row['surplus_qty']:g}",
                status,
                row["created_at"] or "",
            ))
        return rows

    def _populate_wip_tree(self, rows):
        for item in self.tree_wip_list.get_children():
            self.tree_wip_list.delete(item)
        for values in rows:
            self.tree_wip_list.insert("", tk.END, values=values)

    def load_wip_list(self):
        """DB取得とTreeview更新をまとめて同期的に行う（「更新」ボタン・画面表示時から使用）。"""
        rows = self._fetch_wip_list_rows()
        self._all_wip_rows = rows
        for var in self._wip_filter_vars.values():
            var.set("")
        self._wip_checkbox_filters.clear()
        for col_key in self._wip_checkbox_buttons:
            self._update_wip_filter_button_style(col_key)
        self._populate_wip_tree(rows)

    def _wip_filter_predicates(self):
        predicates = {}
        for col_key, var in self._wip_filter_vars.items():
            text = var.get().strip()
            if not text:
                continue
            needle = text.lower()
            predicates[col_key] = lambda value, needle=needle: needle in value.lower()

        for col_key, selected_values in self._wip_checkbox_filters.items():
            predicates[col_key] = lambda value, selected=selected_values: value in selected

        return predicates

    def _wip_row_matches(self, row, predicates):
        for col_key, predicate in predicates.items():
            col_index = self._wip_col_index[col_key]
            if not predicate(str(row[col_index])):
                return False
        return True

    def apply_wip_filters(self, event=None):
        predicates = self._wip_filter_predicates()
        if not predicates:
            filtered = self._all_wip_rows
        else:
            filtered = [row for row in self._all_wip_rows if self._wip_row_matches(row, predicates)]
        self._populate_wip_tree(filtered)

    def clear_wip_filters(self):
        for var in self._wip_filter_vars.values():
            var.set("")
        self._wip_checkbox_filters.clear()
        for col_key in self._wip_checkbox_buttons:
            self._update_wip_filter_button_style(col_key)
        self.apply_wip_filters()

    def sort_wip_list(self, col):
        numeric_cols = {"surplus_qty"}

        def sort_key(value):
            if col in numeric_cols:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

        ascending = self._wip_sort_states.get(col, True)

        items = [
            (self.tree_wip_list.set(iid, col), iid)
            for iid in self.tree_wip_list.get_children("")
        ]
        items.sort(key=lambda t: sort_key(t[0]), reverse=not ascending)

        for index, (_, iid) in enumerate(items):
            self.tree_wip_list.move(iid, "", index)

        self._wip_sort_states[col] = not ascending
