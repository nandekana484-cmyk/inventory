import threading
import queue

import tkinter as tk
from tkinter import ttk, messagebox

from services.bom_service import get_shared_bom_service
from models.wip_board_snapshot import list_wip_snapshot
from models.wip_scrap_records import save_wip_scrap_records, list_wip_scrap_summary
from models.wip_exclusion_list import (
    mark_wip_excluded, unmark_wip_excluded, list_wip_exclusions,
)
from models.operation_log import log_operation
from ui.checkable_treeview import CheckableTreeview
from ui.loading_window import LoadingWindow
from ui.wip_scrap_correction_window import WipScrapCorrectionWindow
from ui.window_utils import center_window

# 共有の BOMService（NG入力画面と共有するので、共有フォルダのインデックス作成は1回で済む）。
_bom_service = get_shared_bom_service()


class WipExpansionWindow(tk.Toplevel):
    """
    仕掛（WIP）展開画面。実績レポートの「仕掛数量抽出」で保存した仕掛基板のスナップショットを右ペインに表示し、
    行をダブルクリックすると、その仕掛数量分を BOM 展開して左ペインに部品を表示する。
    チェックした部品は「確定」で wip_scrap_records に登録する（on_confirm()）。詳細は docs/domain/wip_expansion.md。
    """
    # 仕掛一覧の列順。_fetch_wip_list_rows() のタプルと一致させること（unprocessed_check_service もこの定義を参照する）。
    WIP_LIST_COLUMNS = (
        "kitting_list_no", "board_name", "file_no", "side", "lot_no",
        "mounting_line", "surplus_qty", "status", "created_at", "excluded",
    )

    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker
        self.current_row = None

        # 仕掛一覧の絞り込み（NG 一覧と同じ設計）。
        self._all_wip_rows = []
        self._wip_filter_vars = {}
        self._wip_checkbox_filters = {}
        self._wip_checkbox_buttons = {}
        self._wip_col_index = {}
        self._wip_filter_labels = {}
        self._wip_sort_states = {}

        self.title("仕掛展開")
        self.geometry("1150x600")
        center_window(self, parent)
        # 開いた直後に最大化する（NG入力画面と同じ）。
        self.state("zoomed")

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
            # 消費数量だけを編集できる（96コード列は編集不可）。
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
        仕掛一覧の行をダブルクリックすると、その行の file_no・生産面・実装ライン・仕掛数量で BOM 展開する。
        スナップショットの無い確定済み行（STATUS_CONFIRMED_ORPHAN）は展開できないので、「実績修正」を案内して終わる。
        """
        row_id = self.tree_wip_list.identify_row(event.y)
        if not row_id:
            return
        values = self.tree_wip_list.item(row_id, "values")

        status = values[self._wip_col_index["status"]]
        if status == self.STATUS_CONFIRMED_ORPHAN:
            messagebox.showinfo(
                "展開できません",
                "スナップショットに存在しないため展開できません。"
                "『実績修正』で確認・訂正してください。",
                parent=self.winfo_toplevel(),
            )
            return

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
        ほかの画面（仕掛製品レポートなど）から、kitting_list_no（と lot_no・production_side）を指定して自動展開する入口。
        一覧の全件から一致する行を探して展開する（lot_no 等を省略すると、kitting_list_no が一致する最初の行）。
        見つからなければエラーを出して False。スナップショットの無い確定済み行は展開できないので、検索の対象から外す。
        """
        idx = self._wip_col_index
        for row in self._all_wip_rows:
            if row[idx["status"]] == self.STATUS_CONFIRMED_ORPHAN:
                continue
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

    def _get_selected_wip_row_identity(self):
        """選択中の行から、対象外リストの識別キー (kitting_list_no, lot_no, file_no, side) を取り出す。選択が無ければ None。"""
        sel = self.tree_wip_list.selection()
        if not sel:
            messagebox.showwarning("警告", "対象の行を選択してください。", parent=self.winfo_toplevel())
            return None

        values = self.tree_wip_list.item(sel[0], "values")
        kitting_list_no = values[self._wip_col_index["kitting_list_no"]]
        lot_no = values[self._wip_col_index["lot_no"]] or None
        file_no = values[self._wip_col_index["file_no"]]
        side_text = values[self._wip_col_index["side"]]

        try:
            side = int(side_text)
        except (TypeError, ValueError):
            messagebox.showerror("エラー", f"生産面を判別できません: {side_text!r}", parent=self.winfo_toplevel())
            return None

        return kitting_list_no, lot_no, file_no, side

    def _prompt_wip_exclusion_reason(self):
        """対象外にする理由（任意）を尋ねる。OK なら理由（空欄なら None）、キャンセルなら False。"""
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

    def on_mark_wip_excluded(self):
        """選択中の行を「対象外」にする（理由は任意入力。キャンセルなら何もしない）。"""
        identity = self._get_selected_wip_row_identity()
        if identity is None:
            return
        kitting_list_no, lot_no, file_no, side = identity

        reason = self._prompt_wip_exclusion_reason()
        if reason is False:
            return

        worker_id = (self.current_worker or {}).get("worker_id", "SYSTEM")
        mark_wip_excluded(kitting_list_no, lot_no, file_no, side, reason, worker_id)
        log_operation(
            (self.current_worker or {}).get("name", "unknown"),
            "仕掛対象外にする",
            detail=f"{kitting_list_no}{f'（ロットNo. {lot_no}）' if lot_no else ''} / 面{side}",
        )
        self.load_wip_list()

    def on_unmark_wip_excluded(self):
        """選択中の行の「対象外」を解除する。"""
        identity = self._get_selected_wip_row_identity()
        if identity is None:
            return
        kitting_list_no, lot_no, file_no, side = identity

        if not messagebox.askyesno(
            "確認",
            f"キッティングリストNo. {kitting_list_no}"
            f"{f'（ロットNo. {lot_no}）' if lot_no else ''} の対象外指定を解除しますか？",
            parent=self.winfo_toplevel(),
        ):
            return

        unmark_wip_excluded(kitting_list_no, lot_no, file_no, side)
        log_operation(
            (self.current_worker or {}).get("name", "unknown"),
            "仕掛対象外解除",
            detail=f"{kitting_list_no}{f'（ロットNo. {lot_no}）' if lot_no else ''} / 面{side}",
        )
        self.load_wip_list()

    def on_open_wip_scrap_correction(self):
        """
        選択中の行の wip_scrap_records 明細（96コード単位）を修正画面で開く。面ごとに分かれているので、その面だけを表示する。
        修正のたびに仕掛一覧の状態も更新する（on_updated=self.load_wip_list）。
        """
        identity = self._get_selected_wip_row_identity()
        if identity is None:
            return
        kitting_list_no, lot_no, file_no, side = identity

        WipScrapCorrectionWindow(
            self, kitting_list_no=kitting_list_no, lot_no=lot_no, production_side=side,
            on_updated=self.load_wip_list, current_worker=self.current_worker,
        )

    def _run_bom_expansion_async(self, work_fn, on_success):
        """
        BOM 展開（共有フォルダを読むので時間が読めない）を別スレッドで実行する共通処理（NG入力画面と同じ実装を、画面ごとに持っている）。
        入力検証は呼び出し元が先に済ませ、展開だけを work_fn で渡す。FileNotFoundError・ValueError は従来の文言で表示し、
        それ以外は Tkinter の通常の例外報告に任せる。
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
        ダブルクリックと expand_by_identity() に共通の展開処理。入力検証は UI スレッドで行い、BOM 展開だけを非同期にする。
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
        チェックした部品を、仕掛展開の結果として確定登録する。その kitting_list_no・lot_no・面の既存の wip_scrap_records を
        削除してから登録し直す（NG入力画面の on_register() と同じ）。
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
        log_operation(
            (self.current_worker or {}).get("name", "unknown"),
            "仕掛確定登録",
            detail=f"{kitting_list_no}{f'（ロットNo. {lot_no}）' if lot_no else ''} / 面{side} / {len(records)}件",
        )

        messagebox.showinfo(
            "登録完了", f"{len(records)}件の仕掛展開結果を確定登録しました。", parent=self.winfo_toplevel(),
        )

        # 登録内容を右ペインの仕掛一覧（状態列）へ即時反映する
        self.load_wip_list()

    def load_parts_tree(self, parts, wip_qty):
        """
        展開結果を表示し直す（前の内容を消してから作る。全行チェック済み）。基板自身の行は区分列に「基板」と出す（NG入力画面と同じ）。
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

    def _get_bulk_wip_expand_targets(self):
        """
        「一括展開・登録」の対象（未確定で、対象外でない行）を返す。表示用の _all_wip_rows ではなく、list_wip_snapshot() の生の値を使う。
        スナップショットはキーの一意性が保証されていない（テーブル全体を差し替える方式）ので、dict にまとめずリストのまま走査する。
        """
        confirmed_keys = {
            (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
            for s in list_wip_scrap_summary()
        }
        excluded_keys = {
            (e["kitting_list_no"], e["lot_no"] or "", e["file_no"], str(e["production_side"]))
            for e in list_wip_exclusions()
        }

        targets = []
        for row in list_wip_snapshot():
            key = (row["kitting_list_no"], row["lot_no"] or "", str(row["production_side"]))
            if key in confirmed_keys:
                continue
            exclusion_key = (
                row["kitting_list_no"], row["lot_no"] or "", row["file_no"], str(row["production_side"]),
            )
            if exclusion_key in excluded_keys:
                continue
            targets.append(row)
        return targets

    def _bulk_expand_and_register_one_wip(self, target):
        """
        一括展開・登録の1行分（バックグラウンドで実行するので、ウィジェットには触れない）。
        スナップショットの行は file_no・生産面・実装ライン・仕掛数量を持っているので、計画の検索は無い。
        実装ラインが空欄の行だけ TSV の候補を調べ、複数あればエラーにする（1件ならそれを使い、0件なら None）。
        """
        kitting_list_no = target["kitting_list_no"]
        file_no = target["file_no"]
        lot_no = target["lot_no"]
        side = int(target["production_side"])
        mounting_line = target["mounting_line"]
        surplus_qty = target["surplus_qty"]

        if not mounting_line:
            lines = _bom_service.list_mounting_lines(file_no, side)
            if len(lines) == 1:
                mounting_line = lines[0]
            elif len(lines) >= 2:
                raise ValueError(
                    f"複数の実装ライン候補（{', '.join(lines)}）が存在するため、"
                    "一括処理では自動選択できません。個別に展開・登録してください。"
                )

        wip_record = {
            "setup_file_no": file_no,
            "production_side": side,
            "wip_qty": surplus_qty,
            "mounting_line": mounting_line,
            "lot_no": lot_no,
        }

        parts = _bom_service.expand_wip_to_parts(wip_record)
        if not parts:
            raise ValueError(f"file_no「{file_no}」・生産面{side}のBOMが登録されていない、または対象部品がありません。")

        records = [{"part_no": part["part_no"], "qty": part["qty"]} for part in parts]
        save_wip_scrap_records(kitting_list_no, file_no, side, records, lot_no=lot_no, mounting_line=mounting_line)

    def _run_bulk_wip_expand_worker(self, targets):
        """対象行を1件ずつ処理する。1件のエラーで全体を止めない。戻り値は {"total", "success_count", "failures"}。"""
        success_count = 0
        failures = []
        for target in targets:
            side = target["production_side"]
            label = f"キッティングリストNo. {target['kitting_list_no']}"
            if target["lot_no"]:
                label += f"（ロットNo. {target['lot_no']}）"
            label += f" / 面{side}"
            try:
                self._bulk_expand_and_register_one_wip(target)
                success_count += 1
            except Exception as e:
                failures.append({"label": label, "error": str(e)})

        return {"total": len(targets), "success_count": success_count, "failures": failures}

    def _show_bulk_wip_expand_result(self, result):
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
        未確定で対象外でない行を、すべて一括で展開・登録する（チェックの確認はしない）。別スレッドで実行する。
        _run_bom_expansion_async() は1件用なので使わず、行ごとの成否を追う（NG入力画面と同じ）。
        """
        targets = self._get_bulk_wip_expand_targets()
        if not targets:
            messagebox.showinfo(
                "一括展開・登録", "対象となる未確定項目（対象外を除く）がありません。",
                parent=self.winfo_toplevel(),
            )
            return

        if not messagebox.askyesno(
            "確認",
            f"未確定（対象外を除く）の{len(targets)}件を一括展開・登録します。よろしいですか？",
            parent=self.winfo_toplevel(),
        ):
            return

        loading = LoadingWindow(self, message=f"一括展開・登録中です（{len(targets)}件）…")
        result_queue = queue.Queue()

        def _work():
            try:
                result_queue.put((True, self._run_bulk_wip_expand_worker(targets)))
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

            self._show_bulk_wip_expand_result(payload)
            log_operation(
                (self.current_worker or {}).get("name", "unknown"),
                "仕掛一括展開・登録",
                detail=f"対象{payload['total']}件 / 成功{payload['success_count']}件 / "
                       f"失敗{len(payload['failures'])}件",
            )
            # 状態（未確定→確定済み）を反映するため、仕掛一覧を再取得する
            self.load_wip_list()

        self.after(200, _poll)

    # ------------------------------------------------------------------
    # 仕掛一覧（右ペイン）
    # ------------------------------------------------------------------

    def _create_wip_list_widgets(self, right_frame):
        """右ペイン（仕掛一覧）を作る（NG 一覧と同じ構成）。"""
        cols_wip = self.WIP_LIST_COLUMNS
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
            "excluded": "対象外",
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
        self._add_wip_checkbox_filter_button(wip_filter_row1, "excluded")

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
        self.tree_wip_list.column("excluded", width=70, anchor=tk.CENTER)

        # スナップショットの無い確定済み行は、状態の文字だけでなく文字色でも区別する。
        self.tree_wip_list.tag_configure("confirmed_orphan", foreground="#b30000")

        vsb_wip = ttk.Scrollbar(right_frame, orient="vertical", command=self.tree_wip_list.yview)
        self.tree_wip_list.configure(yscrollcommand=vsb_wip.set)

        hsb_wip = ttk.Scrollbar(right_frame, orient="horizontal", command=self.tree_wip_list.xview)
        self.tree_wip_list.configure(xscrollcommand=hsb_wip.set)

        # pack 順の罠: ボタン行→水平スクロールバーの順に side=BOTTOM で pack する（NG 一覧と同じ）。
        wip_action_frame = ttk.Frame(right_frame)
        wip_action_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))
        ttk.Button(wip_action_frame, text="更新", command=self.load_wip_list).pack(
            side=tk.LEFT, expand=True, fill=tk.X
        )
        ttk.Button(wip_action_frame, text="対象外にする", command=self.on_mark_wip_excluded).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
        )
        ttk.Button(wip_action_frame, text="対象外解除", command=self.on_unmark_wip_excluded).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
        )
        ttk.Button(wip_action_frame, text="一括展開・登録", command=self.on_bulk_expand_register).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
        )
        ttk.Button(wip_action_frame, text="実績修正", command=self.on_open_wip_scrap_correction).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(5, 0)
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
        """基板名/file_no/生産面/ロットNo./実装ライン のチェックボックス式絞り込みポップアップを開く（NG 一覧と同じ設計）。"""
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

    # スナップショットの無い確定済み行の状態。「確定済み」「未確定」と別の値にして、一括展開・登録の対象や未確定件数に入らないようにする。
    STATUS_CONFIRMED_ORPHAN = "確定済み(スナップショットなし)"

    @staticmethod
    def _fetch_wip_list_rows():
        """
        仕掛一覧の DB アクセスだけを行う（ウィジェットに触れない）。
        スナップショットと確定済みの展開結果を (kitting_list_no, lot_no, production_side) で合わせ、「確定済み」「未確定」を付ける。
        production_side はスナップショットが TEXT、確定済みが INTEGER なので、str() にそろえて比べる。
        対象外の行も一覧から消さず「対象外」列で示す。スナップショットから消えた確定済み行も、実績修正で見つけられるよう末尾に足す。
        """
        summaries = list_wip_scrap_summary()
        confirmed_keys = {
            (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
            for s in summaries
        }
        excluded_keys = {
            (e["kitting_list_no"], e["lot_no"] or "", e["file_no"], str(e["production_side"]))
            for e in list_wip_exclusions()
        }

        rows = []
        snapshot_keys = set()
        for row in list_wip_snapshot():
            key = (row["kitting_list_no"], row["lot_no"] or "", str(row["production_side"]))
            snapshot_keys.add(key)
            status = "確定済み" if key in confirmed_keys else "未確定"
            exclusion_key = (row["kitting_list_no"], row["lot_no"] or "", row["file_no"], str(row["production_side"]))
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
                "対象外" if exclusion_key in excluded_keys else "",
            ))

        for s in summaries:
            key = (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
            if key in snapshot_keys:
                continue
            exclusion_key = (s["kitting_list_no"], s["lot_no"] or "", s["file_no"], str(s["production_side"]))
            rows.append((
                s["kitting_list_no"],
                "",
                s["file_no"],
                str(s["production_side"]),
                s["lot_no"] or "",
                "",
                "",
                WipExpansionWindow.STATUS_CONFIRMED_ORPHAN,
                "",
                "対象外" if exclusion_key in excluded_keys else "",
            ))
        return rows

    def _populate_wip_tree(self, rows):
        status_index = self._wip_col_index["status"]
        for item in self.tree_wip_list.get_children():
            self.tree_wip_list.delete(item)
        for values in rows:
            is_orphan = values[status_index] == self.STATUS_CONFIRMED_ORPHAN
            self.tree_wip_list.insert(
                "", tk.END, values=values,
                tags=("confirmed_orphan",) if is_orphan else (),
            )

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
