# ui/lot_progress_window.py
"""
「ロット進捗チェック」画面。

services.production_service.check_lot_progress()（日報・月報の実績データには
依存せず、現在アクティブな全ロットを対象に構成基板数チェック・進捗算出を
まとめて行う関数、2026-09-26実装・2026-09-28ロット単位判定に修正）を呼び出し、
結果をファイルNo.単位の一覧として表示する。

本ファイルはcheck_lot_progress()が返す値を表示用に並べ替えるだけであり、
数量・状態判定のロジックには一切手を加えない（画面側で計算し直さない）。
"""
import csv
import queue
import threading

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from services.production_service import check_lot_progress
from ui.loading_window import LoadingWindow
from ui.daily_report_window import configure_lot_stripe_tags, configure_status_color_tags

# ui.daily_report_window.configure_lot_stripe_tags()・configure_status_color_
# tags()が実際にtag_configure()するタグ名と同じ文字列を、こちらのtree.insert()の
# tags引数にも使う必要がある（daily_report_window.py側の定数は同モジュール内の
# プライベート定数のため、ここでは同じ文字列リテラルを独自定義する。日報・月報・
# ロット進捗チェックの3画面で同じ色・同じタグ名にするための取り決め）。
_LOT_STRIPE_TAG_A = "lot_stripe_a"
_LOT_STRIPE_TAG_B = "lot_stripe_b"
_STATUS_COLOR_TAG_NEEDS_REVIEW = "status_needs_review"
_STATUS_COLOR_TAG_SHORTFALL = "status_shortfall_note"
# services.production_service._lot_status_color_category()の戻り値
# （"needs_review"|"shortfall"|None）から、上記タグ名を引く（2026-09-28追加、
# ui.daily_report_window._STATUS_COLOR_CATEGORY_TO_TAGと同じ対応関係）。
_STATUS_COLOR_CATEGORY_TO_TAG = {
    "needs_review": _STATUS_COLOR_TAG_NEEDS_REVIEW,
    "shortfall": _STATUS_COLOR_TAG_SHORTFALL,
}

COLUMNS = (
    "lot_no", "status", "board_count", "actual_file_count", "board_name",
    "setup_file_no", "order_qty", "actual_qty", "lot_completed", "surplus_qty",
    "not_produced_qty", "remarks",
)
HEADERS = {
    "lot_no": "ロットNo.", "status": "状態", "board_count": "構成基板数",
    "actual_file_count": "実ファイルNo.数", "board_name": "基板名",
    "setup_file_no": "ファイルNo.", "order_qty": "発注数", "actual_qty": "実績",
    "lot_completed": "引落", "surplus_qty": "仕掛", "not_produced_qty": "未生産",
    "remarks": "備考",
}
_NUMERIC_COLUMNS = {
    "board_count", "actual_file_count", "order_qty", "actual_qty",
    "lot_completed", "surplus_qty", "not_produced_qty",
}
_LEFT_ALIGNED_COLUMNS = {"lot_no", "status", "board_name", "setup_file_no", "remarks"}
_COLUMN_WIDTHS = {
    "lot_no": 100, "status": 110, "board_count": 90, "actual_file_count": 100,
    "board_name": 200, "setup_file_no": 90, "order_qty": 80, "actual_qty": 80,
    "lot_completed": 80, "surplus_qty": 80, "not_produced_qty": 80, "remarks": 260,
}

STATUS_LABELS = {
    "match": "一致",
    "shortfall": "不足",
    "excess": "超過",
    "unregistered": "マスタ未登録",
    "board_count_inconsistent": "構成基板数不一致",
}
# 「一部未登録」はstatus自体の値ではなく、unregistered_board_namesが非空という
# 横断的な条件のため、絞り込み用の選択肢としてのみ別途用意する（_apply_filters()参照）。
STATUS_FILTER_OPTIONS = ["すべて", "一致", "不足", "超過", "マスタ未登録", "構成基板数不一致", "一部未登録"]


def _format_number(value):
    """None・空値は空文字列、それ以外は末尾の無駄な小数点を落として表示する。"""
    if value is None:
        return ""
    return f"{value:g}"


def build_display_rows(lot_results):
    """
    check_lot_progress()の戻り値（ロット単位でグルーピングされた構造）を、
    画面表示用の「ファイルNo.ごとに1行」のフラットなリストへ変換する。

    check_lot_progress()が算出した値（status・board_count・各種数量・
    "status_remarks"（確認事項の文言）・"status_color_category"（文字色
    カテゴリ）」は全てそのまま使い、ここでの再計算・文言の組み立ては一切
    行わない（2026-09-28、文言・色の定義をservices層の
    services.production_service._build_lot_status_remarks()・
    _lot_status_color_category()に一本化したのに伴い、本関数が独自に持って
    いた「備考」組み立てロジックを削除し、evaluate_lot_status()の戻り値を
    そのまま使うよう変更した。日報・月報（"confirmation_note"）と同じ内容の
    文言が、この画面の「備考」欄にもそのまま表示される）。

    ロットの並び順（lot_no昇順、check_lot_progress()自体がソート済み）は
    そのまま維持し、各ロットの実データ行の後に、そのロットの
    missing_file_entries（shortfallの場合の「未確定」仮想行）を続けて
    追加することで、同一ロットの行が必ず連続するようにする。

    戻り値の各行辞書は、COLUMNSに対応する表示用の値に加えて、絞り込み・
    文字色判定にのみ使う内部キー（"_status_raw"・"_has_unregistered"・
    "_status_color_category"）を持つ（これらはCOLUMNSに含まれないため、
    Treeviewの列やCSV出力には出ない）。
    """
    rows = []
    for lot in lot_results:
        lot_no = lot["lot_no"]
        status = lot["status"]
        status_label = STATUS_LABELS.get(status, status)
        has_unregistered = bool(lot["unregistered_board_names"])

        # board_count_inconsistentの場合はboard_count自体が定まらない
        # （board_count_valuesを参照）ため、この列は空欄にする。
        board_count_display = "" if status == "board_count_inconsistent" else _format_number(lot["board_count"])
        actual_file_count = len(lot["visible_file_nos"])
        remarks = lot["status_remarks"]
        status_color_category = lot["status_color_category"]

        def _make_row(board_name, f):
            return {
                "lot_no": lot_no,
                "status": status_label,
                "board_count": board_count_display,
                "actual_file_count": actual_file_count,
                "board_name": board_name,
                "setup_file_no": f["setup_file_no"],
                "order_qty": _format_number(f["order_qty"]),
                "actual_qty": _format_number(f["file_actual"]),
                "lot_completed": _format_number(f["lot_completed"]),
                "surplus_qty": _format_number(f["surplus_qty"]),
                "not_produced_qty": _format_number(f["not_produced_qty"]),
                "remarks": remarks,
                "_status_raw": status,
                "_has_unregistered": has_unregistered,
                "_status_color_category": status_color_category,
            }

        for board in lot["boards"]:
            for f in board["files"]:
                rows.append(_make_row(board["board_name"], f))

        # 「未確定」仮想行は特定のboard_nameに紐づかない（ロット単位の不足数分の
        # プレースホルダーのため）、board_name列は空欄にする。
        for f in lot["missing_file_entries"]:
            rows.append(_make_row("", f))

    return rows


class LotProgressWindow(tk.Toplevel):
    """
    ロット進捗チェック画面。生産実績入力画面（KittingProductionEntryWindow）の
    「日報出力」「月報出力」ボタンの隣から開く（多重表示防止は呼び出し元が
    self._lot_progress_window属性＋winfo_exists()で行う、既存のcsv_staging_window
    と同じパターン）。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.title("ロット進捗チェック")
        self.geometry("1400x650")

        self._all_rows = []
        self._queue = queue.Queue()
        self._loading_window = None
        self._sort_states = {}

        # --- 上部：絞り込みエリア ---
        filter_frame = ttk.Frame(self, padding=10)
        filter_frame.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(filter_frame, text="状態：").pack(side=tk.LEFT, padx=(0, 5))
        self.status_filter_var = tk.StringVar(value="すべて")
        self.combo_status_filter = ttk.Combobox(
            filter_frame, textvariable=self.status_filter_var, values=STATUS_FILTER_OPTIONS,
            state="readonly", width=14,
        )
        self.combo_status_filter.pack(side=tk.LEFT, padx=(0, 15))
        self.combo_status_filter.bind("<<ComboboxSelected>>", lambda e: self._apply_filters())

        ttk.Label(filter_frame, text="ロットNo.：").pack(side=tk.LEFT, padx=(0, 5))
        self.lot_no_filter_var = tk.StringVar()
        entry_lot_no = ttk.Entry(filter_frame, textvariable=self.lot_no_filter_var, width=20)
        entry_lot_no.pack(side=tk.LEFT, padx=(0, 15))
        self.lot_no_filter_var.trace_add("write", lambda *a: self._apply_filters())

        ttk.Button(filter_frame, text="再集計", command=self.refresh).pack(side=tk.LEFT)

        # --- 下部：ボタン・件数表示を先にside=BOTTOMでpackし、領域を確保する
        # （過去にスクロールバーが潰れて表示されない問題があったため、下部要素を
        # 先にpackしてから、後述のTreeview＋スクロールバー用フレームをexpand=Trueで
        # packする順序を厳守する）。 ---
        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT)

        self.lbl_count = ttk.Label(self, foreground="gray")
        self.lbl_count.pack(side=tk.BOTTOM, anchor=tk.W, padx=10, pady=(0, 5))

        # --- Treeview＋スクロールバー（tree_frame内でスクロールバーを先にpack） ---
        tree_frame = ttk.Frame(self, padding=(10, 0, 10, 0))
        tree_frame.pack(side=tk.TOP, expand=True, fill=tk.BOTH)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical")
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree = ttk.Treeview(tree_frame, columns=COLUMNS, show="headings", yscrollcommand=vsb.set)
        vsb.config(command=self.tree.yview)
        for c in COLUMNS:
            self.tree.heading(c, text=HEADERS[c], command=lambda col=c: self.sort_by_column(col))
            self.tree.column(
                c, width=_COLUMN_WIDTHS.get(c, 90),
                anchor=tk.W if c in _LEFT_ALIGNED_COLUMNS else tk.E,
            )
        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        configure_lot_stripe_tags(self.tree)
        configure_status_color_tags(self.tree)

        self.refresh()

    def refresh(self):
        """
        check_lot_progress()を別スレッドで実行する（既存の非同期パターン、
        LoadingWindow＋threading.Thread(daemon=True)＋queue.Queue＋
        self.after(200, ...)ポーリングを踏襲）。693ロット規模で約1.3〜1.5秒
        かかることを実測済みのため、同期実行するとUIスレッドが一瞬フリーズ
        したように見えるため非同期化する。
        """
        self._loading_window = LoadingWindow(self, message="ロット進捗を集計しています…")
        threading.Thread(target=self._run_check_in_thread, daemon=True).start()
        self.after(200, self._poll_queue)

    def _run_check_in_thread(self):
        try:
            results = check_lot_progress()
            self._queue.put((True, results))
        except Exception as e:
            self._queue.put((False, str(e)))

    def _poll_queue(self):
        try:
            success, payload = self._queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_queue)
            return

        if self._loading_window is not None:
            self._loading_window.destroy()
            self._loading_window = None

        if not success:
            messagebox.showerror(
                "エラー", f"ロット進捗の集計に失敗しました：\n{payload}", parent=self.winfo_toplevel(),
            )
            return

        self._all_rows = build_display_rows(payload)
        self._apply_filters()

    def _apply_filters(self):
        """
        状態・ロットNo.の絞り込みを適用して再描画する。status・lot_no・
        has_unregisteredはいずれもロット単位の属性であり、同一ロットの全行
        （実データ行・「未確定」仮想行とも）で共通の値を持つため、行単位で
        フィルタしても結果的にロット単位のフィルタと同じになる。
        """
        status_filter = self.status_filter_var.get()
        lot_no_filter = self.lot_no_filter_var.get().strip()

        def keep(row):
            if status_filter == "一部未登録":
                # 「一部未登録」は status=="unregistered"（全board_nameが未登録）
                # を含まない。全board_name未登録のロットは既に「マスタ未登録」
                # フィルタで参照できるため、ここでは「登録済みboard_countとの
                # 比較自体は成立するが、一部のboard_nameだけ未登録」というケース
                # のみに絞る（_has_unregisteredだけを見ると、status=="unregistered"
                # のロット（unregistered_board_namesが全board_name分＝非空になる）
                # まで誤って含んでしまうバグがあったため、statusのチェックを追加した）。
                if row["_status_raw"] == "unregistered" or not row["_has_unregistered"]:
                    return False
            elif status_filter != "すべて":
                if STATUS_LABELS.get(row["_status_raw"], row["_status_raw"]) != status_filter:
                    return False
            if lot_no_filter and lot_no_filter not in row["lot_no"]:
                return False
            return True

        filtered = [r for r in self._all_rows if keep(r)]
        self._render_rows(filtered)

    def _render_rows(self, rows):
        """
        rowsをTreeviewへ描画する。ロット単位の縞模様タグ（lot_no→タグの
        辞書方式、ui.daily_report_window.populate_report_tree()と同じ考え方）と、
        状態による文字色タグを組み合わせて適用する（背景色＝縞模様、文字色＝
        状態、で担当を分けているため、同一行に両方のタグを付けても競合しない）。
        """
        for item in self.tree.get_children():
            self.tree.delete(item)

        lot_tag_map = {}
        use_tag_b = False
        displayed_lot_nos = set()
        for row in rows:
            lot_no = row["lot_no"]
            if lot_no not in lot_tag_map:
                use_tag_b = not use_tag_b
                lot_tag_map[lot_no] = _LOT_STRIPE_TAG_B if use_tag_b else _LOT_STRIPE_TAG_A
            displayed_lot_nos.add(lot_no)

            tags = [lot_tag_map[lot_no]]
            color_tag = _STATUS_COLOR_CATEGORY_TO_TAG.get(row["_status_color_category"])
            if color_tag:
                tags.append(color_tag)

            values = [row[c] for c in COLUMNS]
            self.tree.insert("", tk.END, values=values, tags=tuple(tags))

        self.lbl_count.config(text=f"表示中: {len(displayed_lot_nos)}ロット / {len(rows)}行")

    def sort_by_column(self, col):
        """
        列ヘッダークリックでのソート。ロットの行がバラバラにならないよう、
        現在表示されている行を「ロット単位のブロック」（同一lot_noの連続する
        行のまとまり、_render_rows()が常にロット単位で連続描画するため
        安全に検出できる）にまとめ、各ブロックの**先頭行**の値を代表値として
        ブロック単位でソートする（ブロック内の行の順序はそのまま保持する）。

        列によっては（board_name・setup_file_no・発注数等）同一ロット内で
        行ごとに値が異なるため、「先頭行の値」はロット全体を代表する値では
        なく、あくまで簡易的な代表値であることに注意（lot_no・状態・構成
        基板数・実ファイルNo.数はロット内で共通の値のため、これらの列では
        代表値の選び方によらず正しくソートされる）。
        """
        ascending = self._sort_states.get(col, True)

        def sort_key(value):
            if col in _NUMERIC_COLUMNS:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return float("-inf")
            return value

        children = list(self.tree.get_children(""))
        blocks = []
        current_lot = object()
        for iid in children:
            lot_no = self.tree.set(iid, "lot_no")
            if lot_no != current_lot:
                blocks.append([])
                current_lot = lot_no
            blocks[-1].append(iid)

        def block_sort_key(block):
            return sort_key(self.tree.set(block[0], col))

        blocks.sort(key=block_sort_key, reverse=not ascending)

        index = 0
        for block in blocks:
            for iid in block:
                self.tree.move(iid, "", index)
                index += 1

        self._sort_states[col] = not ascending

    def on_export_csv(self):
        """
        現在表示中（絞り込み・ソート後）の内容をそのままCSV出力する
        （utf-8-sig、既存のCSV出力機能と同じエンコーディング）。
        """
        children = self.tree.get_children("")
        if not children:
            messagebox.showinfo("CSV出力", "表示中のデータがありません。", parent=self.winfo_toplevel())
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="lot_progress.csv",
            filetypes=[("CSV files", "*.csv")], parent=self.winfo_toplevel(),
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow([HEADERS[c] for c in COLUMNS])
                for iid in children:
                    writer.writerow(self.tree.item(iid)["values"])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())
