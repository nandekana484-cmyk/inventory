# ui/parts_attributes_import_window.py
import csv

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from models.parts_attributes import list_parts_attributes, upsert_parts_attributes

# エンコーディング自動判定の候補（この順で試す）
_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp932"]

# 部品属性CSVの列名（拡張ポイント）：既存データ構成の多数列のうち、
# 新BOM計算（丁取り数統合）に必要な5列のみを抽出する。
COL_PART_NO = "96コード"
COL_TEITORI = "丁取り数"
COL_PART_TYPE = "部品種別"
COL_SUPPLY_TYPE = "部品支給区分"
COL_FULL_QTY = "フル数量"


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


def _import_parts_attributes_csv(file_path):
    """
    部品属性CSV（96コード・丁取り数・部品種別・部品支給区分・フル数量ほか
    多数列を含む既存フォーマット）を解析し、必要5列のみ抽出して
    models.parts_attributes.upsert_parts_attributes() へ保存する。

    必須列：96コード（欠けている・空の行は警告してスキップ）
    任意列：丁取り数・部品種別・部品支給区分・フル数量
      （丁取り数・フル数量が数値変換できない場合は警告のうえNoneのまま保存する）

    戻り値：{"imported": 成功件数, "warnings": 警告メッセージのリスト}
    """
    imported = 0
    warnings = []

    with _open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
            part_no = (row.get(COL_PART_NO) or "").strip()
            if not part_no:
                warnings.append(f"{i}行目: {COL_PART_NO}が空のためスキップしました。")
                continue

            teitori_raw = (row.get(COL_TEITORI) or "").strip()
            teitori = None
            if teitori_raw:
                try:
                    teitori = int(float(teitori_raw))
                except ValueError:
                    warnings.append(
                        f"{i}行目: {COL_TEITORI}「{teitori_raw}」を数値に変換できないため未設定のまま保存しました。"
                    )

            part_type = (row.get(COL_PART_TYPE) or "").strip() or None
            supply_type = (row.get(COL_SUPPLY_TYPE) or "").strip() or None

            full_qty_raw = (row.get(COL_FULL_QTY) or "").strip()
            full_qty = None
            if full_qty_raw:
                try:
                    full_qty = int(float(full_qty_raw))
                except ValueError:
                    warnings.append(
                        f"{i}行目: {COL_FULL_QTY}「{full_qty_raw}」を数値に変換できないため未設定のまま保存しました。"
                    )

            upsert_parts_attributes(part_no, teitori, part_type, supply_type, full_qty)
            imported += 1

    return {"imported": imported, "warnings": warnings}


class PartsAttributesImportWindow(tk.Toplevel):
    """
    部品属性マスタ（丁取り数等）をCSVからインポートする画面。

    新BOM計算ロジック（services.bom_service.BOMService._calculate_bom）で、
    BOM TSVの係数が0かつRフラグがある行の qty 計算（部品員数 ÷ 丁取り数）に使われる。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.selected_csv_path = None

        self.title("部品属性（丁取り数）インポート")
        self.geometry("700x520")

        select_frame = ttk.Frame(self, padding=10)
        select_frame.pack(fill=tk.X)

        ttk.Button(select_frame, text="CSV選択", command=self.on_select_csv).pack(side=tk.LEFT, padx=5)
        self.lbl_csv_path = ttk.Label(select_frame, text="（未選択）", foreground="blue")
        self.lbl_csv_path.pack(side=tk.LEFT, padx=5)

        ttk.Button(select_frame, text="インポート実行", command=self.on_import_execute).pack(side=tk.LEFT, padx=15)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("part_no", "teitori", "part_type", "supply_type", "full_qty")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("part_no", text="96コード")
        self.tree.heading("teitori", text="丁取り数")
        self.tree.heading("part_type", text="部品種別")
        self.tree.heading("supply_type", text="部品支給区分")
        self.tree.heading("full_qty", text="フル数量")
        self.tree.column("part_no", width=180, anchor=tk.W)
        self.tree.column("teitori", width=90, anchor=tk.E)
        self.tree.column("part_type", width=100, anchor=tk.W)
        self.tree.column("supply_type", width=120, anchor=tk.W)
        self.tree.column("full_qty", width=100, anchor=tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.load_parts_attributes()

    def load_parts_attributes(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in list_parts_attributes():
            self.tree.insert("", tk.END, values=(
                row["part_no"],
                row.get("teitori") if row.get("teitori") is not None else "",
                row.get("part_type") or "",
                row.get("supply_type") or "",
                row.get("full_qty") if row.get("full_qty") is not None else "",
            ))

    def on_select_csv(self):
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], parent=self.winfo_toplevel())
        if not file_path:
            return
        self.selected_csv_path = file_path
        self.lbl_csv_path.config(text=file_path)

    def on_import_execute(self):
        if not self.selected_csv_path:
            messagebox.showwarning("警告", "CSVファイルを選択してください。", parent=self.winfo_toplevel())
            return

        try:
            result = _import_parts_attributes_csv(self.selected_csv_path)
        except ValueError as e:
            messagebox.showerror("エラー", f"部品属性CSV取込中にエラーが発生しました：\n{e}", parent=self.winfo_toplevel())
            return

        self.load_parts_attributes()

        msg = f"成功件数：{result['imported']}件\n警告件数：{len(result['warnings'])}件"
        warnings = result["warnings"]
        if warnings:
            shown = "\n".join(warnings[:10])
            more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
            msg += f"\n\n{shown}{more}"

        messagebox.showinfo("部品属性CSV取込結果", msg, parent=self.winfo_toplevel())
