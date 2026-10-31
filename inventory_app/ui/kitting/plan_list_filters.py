"""生産実績入力画面の計画一覧の絞り込み（文字・日付範囲・チェックボックス）と、一覧の取得・表示・更新。KittingProductionEntryWindow の mixin。
kitting_production_entry.py から移した（関数の中身は変えていない。D-190）。
"""
import logging
import tkinter as tk
from datetime import date
from tkinter import ttk
from tkcalendar import DateEntry
from services.production_service import calculate_lot_completion
from services.lot_merge_service import merged_lot_nos
from models.kitting_plan import classify_side1_only_plan, list_active_plan_items
from ui.common.column_filter import CheckboxFilterPopup, build_filter_predicates, date_range_predicate, filter_rows, row_matches
from services.production_entry_service import (
    PLAN_ROW_STALE_INDEX, STALE_MARK, filter_plan_rows_by_end_and_stale, is_plan_row_completed, load_stale_plan_keys,
    plan_row_tag,
)

logger = logging.getLogger(__name__)


class PlanListFiltersMixin:
    def _add_plan_filter_entry(self, parent, col_key, label_text, width):
        """絞り込みエリアに列1つ分のラベル+Entryを追加し、StringVarを登録する。"""
        ttk.Label(parent, text=f"{label_text}:").pack(side=tk.LEFT, padx=(5, 2))
        var = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.pack(side=tk.LEFT, padx=(0, 5))
        # メモリ内の部分一致なので、キー入力ごとに即時反映してよい（DB 検索のデバウンスとは別）。
        entry.bind("<KeyRelease>", self.apply_plan_filters)
        self._plan_filter_vars[col_key] = var

    def _add_plan_date_range_filter(self, parent):
        """
        「実装開始予定日」の期間指定（開始日・終了日の DateEntry）を追加する。
        DateEntry は当日が初期選択されるため、生成直後にクリアして「絞り込み無し」から始める。
        """
        ttk.Label(parent, text=f"{self._plan_filter_labels['plan_start_datetime']}:").pack(
            side=tk.LEFT, padx=(5, 2)
        )
        self._plan_date_from_entry = DateEntry(parent, date_pattern="yyyy-mm-dd", width=10, locale="ja_JP")
        self._plan_date_from_entry.delete(0, tk.END)
        self._plan_date_from_entry.pack(side=tk.LEFT, padx=(0, 2))

        ttk.Label(parent, text="〜").pack(side=tk.LEFT, padx=(0, 2))

        self._plan_date_to_entry = DateEntry(parent, date_pattern="yyyy-mm-dd", width=10, locale="ja_JP")
        self._plan_date_to_entry.delete(0, tk.END)
        self._plan_date_to_entry.pack(side=tk.LEFT, padx=(0, 5))

        for date_entry in (self._plan_date_from_entry, self._plan_date_to_entry):
            date_entry.bind("<<DateEntrySelected>>", self.apply_plan_filters)
            date_entry.bind("<KeyRelease>", self.apply_plan_filters)

    def _plan_date_range_predicate(self):
        """
        期間フィルタから plan_start_datetime 用の述語を作る。両方空欄なら None。
        実データは "YYYY/MM/DD HH:MM:SS" なので、先頭10文字をスラッシュ区切りにそろえて文字列比較する。
        """
        from_text = self._plan_date_from_entry.get().strip() if self._plan_date_from_entry else ""
        to_text = self._plan_date_to_entry.get().strip() if self._plan_date_to_entry else ""
        return date_range_predicate(from_text, to_text)

    def _add_plan_checkbox_filter_button(self, parent, col_key):
        """
        チェックボックス式ポップアップを開く▼ボタンを追加する。
        ttk.Button はテーマによって背景色を変えられないため、絞り込み中の色を付けられる tk.Button を使う。
        """
        label_text = self._plan_filter_labels[col_key]
        button = tk.Button(
            parent, text=f"{label_text} ▼", relief=tk.RAISED,
            command=lambda c=col_key: self.open_plan_checkbox_filter_popup(c),
        )
        button.pack(side=tk.LEFT, padx=(5, 5))
        self._plan_checkbox_buttons[col_key] = button
        self._plan_checkbox_default_bg = button.cget("background")

    def _update_plan_filter_button_style(self, col_key):
        """指定列のチェックボックス式フィルタが有効かどうかをボタンの見た目に反映する。"""
        button = self._plan_checkbox_buttons.get(col_key)
        if button is None:
            return
        label_text = self._plan_filter_labels[col_key]
        active = col_key in self._plan_checkbox_filters
        button.configure(
            text=f"{label_text} ▼●" if active else f"{label_text} ▼",
            background="#cfe8ff" if active else self._plan_checkbox_default_bg,
        )

    def open_plan_checkbox_filter_popup(self, col_key):
        """
        lot_no/file_no/board_name のチェックボックス式絞り込みポップアップを開く。
        候補は、この列以外の絞り込みを適用した結果の distinct 値（エクセルのオートフィルタと同じ）。
        """
        label_text = self._plan_filter_labels[col_key]
        col_index = self._plan_col_index[col_key]

        other_predicates = self._plan_filter_predicates()
        other_predicates.pop(col_key, None)
        if other_predicates:
            base_rows = [row for row in self._all_plan_rows if self._plan_row_matches(row, other_predicates)]
        else:
            base_rows = self._all_plan_rows

        full_values = sorted({str(row[col_index]) for row in base_rows})

        def on_selected(selected):
            # None＝全部チェック＝絞り込みなし
            if selected is None:
                self._plan_checkbox_filters.pop(col_key, None)
            else:
                self._plan_checkbox_filters[col_key] = selected
            self._update_plan_filter_button_style(col_key)

        # ポップアップ本体は3画面共通（ui/common/column_filter.py、段階6）。OK なら on_selected → 閉じる → 絞り込みを適用。
        return CheckboxFilterPopup(
            self, label_text, full_values, self._plan_checkbox_filters.get(col_key),
            on_selected, self.apply_plan_filters,
        ).popup

    @staticmethod
    def _fetch_plan_list_rows():
        """
        計画一覧の DB アクセスだけを行う（ウィジェットに触れない）。別スレッドから呼んでよい。
        接続は呼び出し先が都度張るので、スレッド間で共有しない。
        """
        rows = []
        lot_completion_cache = {}
        # 「古い計画を隠す」の判定は、月次DB のビュー v_plan_stale を一覧を作るときに1回だけ読む（絞り込みのたびには読まない。D-192・D-193）。
        # 失敗しても一覧は出す（何も隠さない）。
        try:
            stale_keys = load_stale_plan_keys()
        except Exception:
            logger.exception("古い計画の判定（v_plan_stale）を読めませんでした（何も隠しません）")
            stale_keys = set()

        # 完了済みも取得し、表示/非表示は「入力済みを隠す」で切り替える。
        for plan_item in list_active_plan_items(include_completed=True):
            kitting_list_no = plan_item["kitting_list_no"]
            lot_no = plan_item["lot_no"]
            planned_qty = plan_item["planned_qty"] or 0
            order_qty = plan_item["order_qty"] or 0
            # list_active_plan_items() の計算済みの値を使い、重複呼び出しを避ける。
            actual_qty = plan_item["app_cumulative_qty"]
            diff = order_qty - actual_qty

            if lot_no not in lot_completion_cache:
                lot_completion_cache[lot_no] = calculate_lot_completion(lot_no)
            lot_info = lot_completion_cache[lot_no]
            lot_completed = lot_info["completed_quantity"]
            lot_remaining = lot_info["remaining_quantity"]

            rows.append((
                kitting_list_no,
                lot_no,
                plan_item["plan_start_datetime"] or "",
                plan_item["setup_file_no"],
                plan_item["board_name"],
                f"{planned_qty:.0f}",
                f"{order_qty:.0f}",
                f"{actual_qty:.0f}",
                f"{diff:.0f}",
                f"{lot_completed:.0f}",
                f"{lot_remaining:.0f}",
                # 隠し要素1: 生産面マスターの分類（D-9x）。row[-1] は production_side という契約なので、その前に置く。
                str(classify_side1_only_plan(plan_item) or ""),
                # 隠し要素2: 計画終了日（「計画終了日が来たものだけ」用。D-192）。
                plan_item["plan_end_datetime"] or "",
                # 隠し要素3: 古い計画の印（「古い計画を隠す」用。D-192）。
                STALE_MARK if (kitting_list_no, lot_no or "") in stale_keys else "",
                # 隠し要素4: production_side。「入力済みを隠す」が (setup_file_no, production_side) で file_actuals を引くのに使う。
                str(plan_item.get("production_side") or ""),
            ))

        return rows

    def _populate_plan_list_tree(self, rows):
        """
        渡された行（全件、または絞り込み後）で Treeview を作り直す。UI スレッド専用。
        _plan_row_iid_by_kitting_no も、実際に挿入した行だけで作り直す。
        """
        self._close_cell_edit_entry()

        for item in self.tree_plan_list.get_children():
            self.tree_plan_list.delete(item)

        self._plan_row_iid_by_kitting_no = {}
        # 末尾の隠し要素4つ（分類・計画終了日・古い計画の印・production_side）は Treeview に渡さない。
        col_count = len(self._plan_col_index)
        for values in rows:
            classification = values[col_count] if len(values) > col_count else ""
            tag = plan_row_tag(classification)
            iid = self.tree_plan_list.insert(
                "", tk.END, values=values[:col_count], tags=(tag,) if tag else (),
            )
            self._plan_row_iid_by_kitting_no[(values[0], values[1])] = iid

    def load_plan_list(self):
        """
        DB 取得と Treeview 更新を同期的に行う（「更新」ボタンなど）。更新のたびに絞り込みとソートは解除される。
        """
        rows = self._fetch_plan_list_rows()
        self._all_plan_rows = rows
        for var in self._plan_filter_vars.values():
            var.set("")
        self._plan_checkbox_filters.clear()
        for col_key in self._plan_checkbox_buttons:
            self._update_plan_filter_button_style(col_key)
        self._hide_completed_var.set(False)
        self._hide_stale_var.set(False)
        self._only_ended_var.set(False)
        self._stale_hidden_label.config(text="")
        if self._plan_date_from_entry is not None:
            self._plan_date_from_entry.delete(0, tk.END)
        if self._plan_date_to_entry is not None:
            self._plan_date_to_entry.delete(0, tk.END)
        self._populate_plan_list_tree(rows)

    def _refresh_plan_list_for_lot(self, lot_no):
        """
        登録直後に、同じ lot_no の行だけを部分更新する（_perform_registration() から呼ぶ）。
        統合したロットは完成数が共通なので、統合後のロットに入るすべてのロットの行を更新する（D-150）。
        """
        for each_lot_no in merged_lot_nos(lot_no):
            self._refresh_plan_list_rows_of_lot(each_lot_no)

    def _refresh_plan_list_rows_of_lot(self, lot_no):
        """
        lot_no の行だけを部分更新する。
        list_active_plan_items(lot_no=...) は部分一致（LIKE）なので、取得後に lot_no の完全一致で絞り直す。
        絞り込みで非表示の行も _all_plan_rows は更新する（解除したときに古い値が出ないように）。
        既存 iid への set() だけなので、並び順と選択状態は変わらない。
        """
        plan_items = list_active_plan_items(lot_no=lot_no, include_completed=True)
        plan_items = [item for item in plan_items if item.get("lot_no") == lot_no]
        if not plan_items:
            return

        lot_info = calculate_lot_completion(lot_no)
        lot_completed = lot_info["completed_quantity"]
        lot_remaining = lot_info["remaining_quantity"]

        row_index_by_key = {
            (row[0], row[1]): i for i, row in enumerate(self._all_plan_rows)
        }
        plan_list_cols = sorted(self._plan_col_index, key=self._plan_col_index.get)

        for plan_item in plan_items:
            kitting_list_no = plan_item["kitting_list_no"]
            planned_qty = plan_item["planned_qty"] or 0
            order_qty = plan_item["order_qty"] or 0
            actual_qty = plan_item["app_cumulative_qty"]
            diff = order_qty - actual_qty

            key = (kitting_list_no, lot_no)
            row_index = row_index_by_key.get(key)
            # 古い計画の印は、一覧を作ったときの判定を引き継ぐ。登録して実績が入ったら外す（実績のある計画は隠さない）。
            was_stale = row_index is not None and self._all_plan_rows[row_index][PLAN_ROW_STALE_INDEX] == STALE_MARK
            new_row = (
                kitting_list_no,
                lot_no,
                plan_item["plan_start_datetime"] or "",
                plan_item["setup_file_no"],
                plan_item["board_name"],
                f"{planned_qty:.0f}",
                f"{order_qty:.0f}",
                f"{actual_qty:.0f}",
                f"{diff:.0f}",
                f"{lot_completed:.0f}",
                f"{lot_remaining:.0f}",
                # _fetch_plan_list_rows()と同じ末尾の隠し要素（分類・計画終了日・古い計画の印・production_side）。
                str(classify_side1_only_plan(plan_item) or ""),
                plan_item["plan_end_datetime"] or "",
                STALE_MARK if was_stale and actual_qty <= 0 else "",
                str(plan_item.get("production_side") or ""),
            )

            if row_index is not None:
                self._all_plan_rows[row_index] = new_row

            iid = self._plan_row_iid_by_kitting_no.get(key)
            if iid is not None and self.tree_plan_list.exists(iid):
                for col, value in zip(plan_list_cols, new_row):
                    self.tree_plan_list.set(iid, col, value)
                classification = new_row[len(plan_list_cols)]
                tag = plan_row_tag(classification)
                self.tree_plan_list.item(iid, tags=(tag,) if tag else ())

    def _plan_filter_predicates(self):
        """
        現在の絞り込み状態から、列ごとの述語（value: str -> bool）の辞書を作る。指定の無い列は含めない。
        """
        predicates = build_filter_predicates(
            {col_key: var.get() for col_key, var in self._plan_filter_vars.items()}, self._plan_checkbox_filters,
        )

        date_range_predicate = self._plan_date_range_predicate()
        if date_range_predicate is not None:
            predicates["plan_start_datetime"] = date_range_predicate

        return predicates

    def _plan_row_matches(self, row, predicates):
        return row_matches(row, predicates, self._plan_col_index)

    def apply_plan_filters(self, event=None):
        """
        _all_plan_rows に全フィルタを AND で適用して Treeview に反映する（DB にはアクセスしない）。
        「入力済みを隠す」は、行の (file_no, 面) の実績合計が、その行の発注数以上かで判定する。
        ロット完成数と比べると、実績0のロットが 0 >= 0 で「完了」と誤判定される。
        """
        predicates = self._plan_filter_predicates()
        filtered = filter_rows(self._all_plan_rows, predicates, self._plan_col_index)

        if self._hide_completed_var.get():
            lot_no_index = self._plan_col_index["lot_no"]
            file_no_index = self._plan_col_index["file_no"]
            order_qty_index = self._plan_col_index["order_qty"]

            # このメソッド1回の中だけで使う、ロット単位のキャッシュ。
            lot_completion_cache = {}

            def is_row_completed(row):
                lot_no = row[lot_no_index]
                if lot_no not in lot_completion_cache:
                    lot_completion_cache[lot_no] = calculate_lot_completion(lot_no)
                lot_info = lot_completion_cache[lot_no]
                return is_plan_row_completed(row, lot_info, file_no_index, order_qty_index)

            filtered = [row for row in filtered if not is_row_completed(row)]

        # 「計画終了日が来たものだけ」「古い計画を隠す」。DB には触れず、一覧を作ったときの隠し要素で判定する（D-192）。
        hide_stale = self._hide_stale_var.get()
        filtered, stale_hidden_count = filter_plan_rows_by_end_and_stale(
            filtered, hide_stale, self._only_ended_var.get(), date.today(),
        )
        self._stale_hidden_label.config(text=f"古い計画 {stale_hidden_count}件を隠しています" if hide_stale else "")

        self._populate_plan_list_tree(filtered)

    def clear_plan_filters(self):
        """全フィルタ（テキスト入力欄＋チェックボックス式＋期間指定＋入力済みを隠す）をクリアし、全件表示に戻す。"""
        for var in self._plan_filter_vars.values():
            var.set("")
        self._plan_checkbox_filters.clear()
        for col_key in self._plan_checkbox_buttons:
            self._update_plan_filter_button_style(col_key)
        self._hide_completed_var.set(False)
        self._hide_stale_var.set(False)
        self._only_ended_var.set(False)
        if self._plan_date_from_entry is not None:
            self._plan_date_from_entry.delete(0, tk.END)
        if self._plan_date_to_entry is not None:
            self._plan_date_to_entry.delete(0, tk.END)
        self.apply_plan_filters()
