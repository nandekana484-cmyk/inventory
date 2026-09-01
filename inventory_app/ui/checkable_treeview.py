# ui/checkable_treeview.py
"""
チェックボックス付き一覧（Treeview）の共通部品。

各行に選択状態（☑/☐）を持たせ、行クリックでのトグル・「全選択」「全解除」による
一括切替に対応する。ui/kitting_production_entry.py のNG部品確認ダイアログで使用。

editable_columns で指定した列は、セルのダブルクリックで一時的なEntryを重ねて
値を編集できる（ui/kitting_production_entry.py::on_plan_cell_double_click()の
「セル上に一時的なEntryを重ねる」パターンをベースに、値の書き戻し・数値
バリデーションを追加したもの。元のパターンは値のコピー用でTreeviewへの
書き戻しを行わないが、本クラスの編集機能は明示的に self.tree.set() で
書き戻す）。
"""
import tkinter as tk
from tkinter import ttk

CHECKED_MARK = "☑"
UNCHECKED_MARK = "☐"

_EDIT_ERROR_BG = "#ffb3b3"
_EDIT_NORMAL_BG = "white"


class CheckableTreeview(ttk.Frame):
    """
    先頭列にチェック状態（☑/☐）を表示するTreeview（縦スクロールバー付き）。

    columns：[(col_key, heading_text, width, anchor), ...]（チェック列は自動追加する
    ため含めない）。
    editable_columns：セルダブルクリックで編集可能にする col_key の集合（省略可、
    デフォルトはどの列も編集不可）。チェック列は対象外（チェックのトグルは
    従来通り行クリックで行う）。
    """

    CHECK_COL = "_checked"

    def __init__(self, parent, columns, height=8, editable_columns=None):
        super().__init__(parent)
        self._data_columns = [c[0] for c in columns]
        all_columns = (self.CHECK_COL,) + tuple(self._data_columns)
        self._all_columns = all_columns
        # col_key -> データ列内でのインデックス（get_row_values()の戻り値タプルに
        # 対応。呼び出し元がタプルの位置に依存せず特定の列を取り出すための
        # 公開辞書。ui.ng_input_window.on_register()が使用）。
        self.column_index = {key: i for i, key in enumerate(self._data_columns)}
        self._editable_columns = set(editable_columns or [])

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
        # ダブルクリックは<Button-1>の後に発火するため、編集可能列をダブルクリック
        # した場合はチェックのトグル（1回分）に加えて編集用Entryが開く
        # （ダブルクリック＝クリック2回のため、トグル自体は最終的に元の状態へ
        # 戻るが、その過程で1回余分にトグルイベントは発生する）。
        self.tree.bind("<Double-1>", self._on_double_click)

        self._checked = {}  # iid -> bool
        self._edit_entry = None

    def clear(self):
        """全行を削除する（展開結果の作り直し等、表示内容を一新する際に使う）。

        編集用オーバーレイEntryが開いたままだと、削除後のTreeview上に浮いた
        まま残ってしまう（Entryはtree項目の子ではなくplace()で重ねた別ウィジェット
        のため、行削除だけでは連動して消えない）ため、先に明示的に閉じる。
        """
        self.close_cell_edit_entry()
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

    def get_row_value(self, iid, col_key):
        """指定col_keyの現在値を1つだけ返す（column_indexを使った位置非依存アクセス）。"""
        return self.get_row_values(iid)[self.column_index[col_key]]

    # ------------------------------------------------------------------
    # セル編集（ダブルクリック）
    # ------------------------------------------------------------------

    def _on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id or not column_id:
            return

        try:
            col_pos = int(column_id.replace("#", "")) - 1
        except ValueError:
            return
        if col_pos < 0 or col_pos >= len(self._all_columns):
            return

        col_key = self._all_columns[col_pos]
        if col_key not in self._editable_columns:
            return

        self._start_cell_edit(row_id, col_key, column_id)

    def _start_cell_edit(self, row_id, col_key, column_id):
        bbox = self.tree.bbox(row_id, column_id)
        if not bbox:
            return
        x, y, width, height = bbox

        self.close_cell_edit_entry()

        current_text = self.tree.set(row_id, col_key)

        entry = tk.Entry(self.tree)
        entry.insert(0, current_text)
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)
        entry.place(x=x, y=y, width=width, height=height)
        entry.focus_set()

        entry.bind("<Return>", lambda e: self._commit_cell_edit(row_id, col_key, entry, force_close=False))
        entry.bind("<FocusOut>", lambda e: self._commit_cell_edit(row_id, col_key, entry, force_close=True))
        entry.bind("<Escape>", lambda e: self.close_cell_edit_entry())

        self._edit_entry = entry

    def _commit_cell_edit(self, row_id, col_key, entry, force_close):
        """
        Entryの内容を検証し、数値として妥当なら self.tree.set() で書き戻して
        編集を終了する。数値として不正（空欄・非数値）な場合は書き戻さず、
        元の値のまま維持した上でEntryの背景色を変えてエラーを示す。

        force_close=True（FocusOutから呼ばれた場合）は、不正な値のままでも
        フォーカスが外れる以上Entryを開いたままにできないため、書き戻さずに
        Entryを閉じる（＝編集前の値のまま確定）。
        force_close=False（Returnから呼ばれた場合）は、不正な値ならEntryを
        閉じずに残し、ユーザーがその場で訂正できるようにする。
        """
        text = entry.get().strip()
        try:
            float(text)
        except ValueError:
            entry.configure(background=_EDIT_ERROR_BG)
            if force_close:
                self.close_cell_edit_entry()
            return

        self.tree.set(row_id, col_key, text)
        self.close_cell_edit_entry()

    def close_cell_edit_entry(self):
        if self._edit_entry is not None:
            entry = self._edit_entry
            self._edit_entry = None
            try:
                entry.destroy()
            except tk.TclError:
                pass
