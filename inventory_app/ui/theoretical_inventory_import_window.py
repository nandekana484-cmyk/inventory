# ui/theoretical_inventory_import_window.py
import csv

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from models.theoretical_inventory import list_theoretical_inventory, upsert_theoretical_inventory

# エンコーディング自動判定の候補（この順で試す）
_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp932"]

# 理論在庫CSVの列名："96コード","在庫数" の2列で確定している。
COL_PART_NO = "96コード"
COL_QTY = "在庫数"


def _open_csv_with_fallback(file_path):
    """utf-8-sig → utf-8 → cp932 の順でエンコーディングを判定して開く。"""
    last_error = None
    for encoding in _ENCODINGS_TO_TRY:
        try:
            f = open(file_path, mode="r", encoding=encoding, newline="")
            f.read(2048)
            f.seek(0)
            return f
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError(f"CSVの文字コードを判定できませんでした: {last_error}")


def parse_theoretical_inventory_csv(file_path):
    """
    理論在庫CSV（96コード・在庫数の2列）を解析し、
    models.theoretical_inventory.upsert_theoretical_inventory() へ保存する。

    必須列：96コード・在庫数（欠けている・空の行は警告してスキップ）
    同一96コードが複数行ある場合は、後の行の値で上書きする
    （models.theoretical_inventory.upsert_theoretical_inventory() が
    常に上書き更新するため、重複行を個別にエラー扱いにはしない）。

    戻り値：{"imported": 成功件数, "warnings": 警告メッセージのリスト}
    """
    imported = 0
    warnings = []

    with _open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
            part_no = (row.get(COL_PART_NO) or "").strip()
            qty_raw = (row.get(COL_QTY) or "").strip()

            if not part_no or not qty_raw:
                warnings.append(f"{i}行目: {COL_PART_NO}または{COL_QTY}が空のためスキップしました。")
                continue

            try:
                qty = int(float(qty_raw))
            except ValueError:
                warnings.append(f"{i}行目: {COL_QTY}「{qty_raw}」を数値に変換できないためスキップしました。")
                continue

            upsert_theoretical_inventory(part_no, qty)
            imported += 1

    return {"imported": imported, "warnings": warnings}


class TheoreticalInventoryImportWindow(tk.Toplevel):
    """
    他部門管理の理論在庫（96コード・在庫数）をCSVからインポートする画面。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.selected_csv_path = None

        self.title("理論在庫インポート")
        self.geometry("500x500")

        select_frame = ttk.Frame(self, padding=10)
        select_frame.pack(fill=tk.X)

        ttk.Button(select_frame, text="CSV選択", command=self.on_select_csv).pack(side=tk.LEFT, padx=5)
        self.lbl_csv_path = ttk.Label(select_frame, text="（未選択）", foreground="blue")
        self.lbl_csv_path.pack(side=tk.LEFT, padx=5)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("part_no", "qty")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("part_no", text="96コード")
        self.tree.heading("qty", text="理論在庫数量")
        self.tree.column("part_no", width=220, anchor=tk.W)
        self.tree.column("qty", width=140, anchor=tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="インポート実行", command=self.on_import_execute).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.load_theoretical_inventory()

    def load_theoretical_inventory(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in list_theoretical_inventory():
            self.tree.insert("", tk.END, values=(row["part_no"], row["qty"]))

    def on_select_csv(self):
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not file_path:
            return
        self.selected_csv_path = file_path
        self.lbl_csv_path.config(text=file_path)

    def on_import_execute(self):
        if not self.selected_csv_path:
            messagebox.showwarning("警告", "CSVファイルを選択してください。")
            return

        try:
            result = parse_theoretical_inventory_csv(self.selected_csv_path)
        except ValueError as e:
            messagebox.showerror("エラー", f"理論在庫CSV取込中にエラーが発生しました：\n{e}")
            return

        self.load_theoretical_inventory()

        msg = f"成功件数：{result['imported']}件\n警告件数：{len(result['warnings'])}件"
        warnings = result["warnings"]
        if warnings:
            shown = "\n".join(warnings[:10])
            more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
            msg += f"\n\n{shown}{more}"

        messagebox.showinfo("理論在庫CSV取込結果", msg)
