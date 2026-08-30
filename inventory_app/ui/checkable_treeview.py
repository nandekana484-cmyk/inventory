# ui/checkable_treeview.py
"""
チェックボックス付き一覧（Treeview）の共通部品。

各行に選択状態（☑/☐）を持たせ、行クリックでのトグル・「全選択」「全解除」による
一括切替に対応する。ui/kitting_production_entry.py のNG部品確認ダイアログで使用。

ui/ng_input_window.py の部品一覧（現状はselectmode="extended"のTreeview標準選択の
みで、全選択/全解除ボタンは無い）に同様の機能を追加する場合も、このクラスをそのまま
流用できる想定。
"""
import tkinter as tk
from tkinter import ttk

CHECKED_MARK = "☑"
UNCHECKED_MARK = "☐"


class CheckableTreeview(ttk.Frame):
    """
    先頭列にチェック状態（☑/☐）を表示するTreeview（縦スクロールバー付き）。

    columns：[(col_key, heading_text, width, anchor), ...]（チェック列は自動追加する
    ため含めない）。
    """

    CHECK_COL = "_checked"

    def __init__(self, parent, columns, height=8):
        super().__init__(parent)
        self._data_columns = [c[0] for c in columns]
        all_columns = (self.CHECK_COL,) + tuple(self._data_columns)

        self.tree = ttk.Treeview(self, columns=all_columns, show="headings", height=height)
        self.tree.heading(self.CHECK_COL, text="選択")
        self.tree.column(self.CHECK_COL, width=45, anchor=tk.CENTER, stretch=False)

        for col_key, heading_text, width, anchor in columns:
            self.tree.heading(col_key, text=heading_text)
            self.tree.column(col_key, width=width, anchor=anchor)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(expand=True, fill=tk.BOTH)

        # チェック列に限らず、行のどこをクリックしてもその行のチェック状態をトグルする
        # （チェック用の小さな列だけをクリック対象にすると誤操作を誘発しやすいため）。
        self.tree.bind("<Button-1>", self._on_click)

        self._checked = {}  # iid -> bool

    def clear(self):
        """全行を削除する（展開結果の作り直し等、表示内容を一新する際に使う）。"""
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self._checked.clear()

    def insert_row(self, iid, values, checked=True):
        """
        1行追加する。values は columns で指定した列の順のタプル（チェック列は含めない）。
        """
        display_values = (CHECKED_MARK if checked else UNCHECKED_MARK,) + tuple(values)
        self.tree.insert("", tk.END, iid=iid, values=display_values)
        self._checked[iid] = checked

    def _on_click(self, event):
        row_id = self.tree.identify_row(event.y)
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell" or not row_id:
            return
        self.set_checked(row_id, not self._checked.get(row_id, False))

    def set_checked(self, iid, checked):
        self._checked[iid] = checked
        self.tree.set(iid, self.CHECK_COL, CHECKED_MARK if checked else UNCHECKED_MARK)

    def select_all(self):
        for iid in self._checked:
            self.set_checked(iid, True)

    def deselect_all(self):
        for iid in self._checked:
            self.set_checked(iid, False)

    def get_checked_iids(self):
        """チェックが入っているiidのリストを、挿入順で返す。"""
        return [iid for iid in self.tree.get_children("") if self._checked.get(iid)]

    def get_row_values(self, iid):
        """チェック列を除いた実データ値のタプルを返す。"""
        return self.tree.item(iid, "values")[1:]
