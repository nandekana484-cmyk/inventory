# ui/inventory_input_window.py
import csv

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from models.inventory import list_inventory, upsert_inventory, upsert_inventory_stock, delete_inventory

# エンコーディング自動判定の候補（この順で試す）
_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp932"]

# 在庫CSVの列名（拡張ポイント）：現時点でフォーマットは以下の13列で確定している。
# "部品ID","部品種別","96コード","部品支給区分","フル数量","棚種別","ID",
# "在庫数","シート数","出庫記録なし期間（日）","満数リール","在庫状態","マスタCHK使用数"
# このうち実際に取り込むのは 96コード・在庫数・棚種別・部品支給区分・マスタCHK使用数の5列。
COL_PART_NO = "96コード"
COL_QTY = "在庫数"
COL_SHELF_TYPE = "棚種別"
COL_SUPPLY_TYPE = "部品支給区分"
COL_MASTER_CHK_QTY = "マスタCHK使用数"


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


def _import_inventory_csv(file_path):
    """
    在庫CSVを解析し、models.inventory.upsert_inventory_stock() へ保存する。

    必須列：96コード・在庫数（欠けている・空の行は警告してスキップ）
    任意列：棚種別・部品支給区分・マスタCHK使用数（無ければ保存値はNoneのまま）

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

            shelf_type = (row.get(COL_SHELF_TYPE) or "").strip() or None
            supply_type = (row.get(COL_SUPPLY_TYPE) or "").strip() or None

            master_chk_qty_raw = (row.get(COL_MASTER_CHK_QTY) or "").strip()
            master_chk_qty = None
            if master_chk_qty_raw:
                try:
                    master_chk_qty = float(master_chk_qty_raw)
                except ValueError:
                    warnings.append(
                        f"{i}行目: {COL_MASTER_CHK_QTY}「{master_chk_qty_raw}」を数値に変換できないため未設定のまま保存しました。"
                    )

            upsert_inventory_stock(part_no, qty, shelf_type, supply_type, master_chk_qty)
            imported += 1

    return {"imported": imported, "warnings": warnings}


class InventoryInputWindow(tk.Toplevel):
    """
    96コードごとの在庫数量（stock_qty）＋棚種別・部品支給区分・マスタCHK使用数を
    入力・編集する画面。CSVインポート（在庫CSV）にも対応する。

    仕掛展開（expand_wip_to_parts）・仕損展開（expand_scrap_to_parts）・
    在庫差異レポートとの突き合わせは services.inventory_diff_service 側で行うため、
    この画面のロジックには影響しない。

    TODO（バーコード検索統合の移植元）：
    旧仕様の ui/physical_count.py（PhysicalCountWindow、本統合作業で削除済み。
    内容は git 履歴から参照可能）に、以下のバーコード検索UXが実装済みだった。
    実装時はこの画面（entry_part_no への入力欄）に統合することを想定する：
      - バーコード入力欄＋Enterキー（<Return>バインド）または「検索」ボタンで発火
      - 入力値を Treeview の各行（part_no列）と照合し、一致する行を選択状態にする
      - 一致しない場合は messagebox.showwarning で「未発見」を通知
    また、旧 physical_count テーブルは count_date（棚卸日）単位で履歴を
    保持していたが、現行の inventory_stock は part_no のみをキーとした
    現在値のみの最小構成（上書き型）である。日付・履歴管理（is_checked等の
    確認状態を含む）が必要になった場合は、models.inventory 側のテーブル定義・
    関数の拡張が別途必要（本タスクでは実施しない）。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.title("在庫入力")
        self.geometry("760x520")

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("part_no", "stock_qty", "shelf_type", "supply_type", "master_chk_qty")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("part_no", text="96コード")
        self.tree.heading("stock_qty", text="在庫数")
        self.tree.heading("shelf_type", text="棚種別")
        self.tree.heading("supply_type", text="部品支給区分")
        self.tree.heading("master_chk_qty", text="マスタCHK使用数")
        self.tree.column("part_no", width=180, anchor=tk.W)
        self.tree.column("stock_qty", width=90, anchor=tk.E)
        self.tree.column("shelf_type", width=100, anchor=tk.W)
        self.tree.column("supply_type", width=120, anchor=tk.W)
        self.tree.column("master_chk_qty", width=120, anchor=tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        entry_frame = ttk.LabelFrame(self, text="部品情報（手動入力：在庫数のみ更新）", padding=10)
        entry_frame.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(entry_frame, text="96コード：").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.entry_part_no = ttk.Entry(entry_frame, width=25)
        self.entry_part_no.grid(row=0, column=1, sticky=tk.W, pady=3, padx=5)

        ttk.Label(entry_frame, text="在庫数量：").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.entry_stock_qty = ttk.Entry(entry_frame, width=25)
        self.entry_stock_qty.grid(row=1, column=1, sticky=tk.W, pady=3, padx=5)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="追加 / 更新", command=self.on_upsert).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="削除", command=self.on_delete).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSVインポート", command=self.on_csv_import).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.load_inventory()

    def load_inventory(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in list_inventory():
            self.tree.insert("", tk.END, values=(
                row["part_no"],
                row["stock_qty"],
                row.get("shelf_type") or "",
                row.get("supply_type") or "",
                row.get("master_chk_qty") if row.get("master_chk_qty") is not None else "",
            ))

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        part_no, stock_qty = values[0], values[1]
        self.entry_part_no.delete(0, tk.END)
        self.entry_part_no.insert(0, part_no)
        self.entry_stock_qty.delete(0, tk.END)
        self.entry_stock_qty.insert(0, stock_qty)

    def on_upsert(self):
        part_no = self.entry_part_no.get().strip()
        if not part_no:
            messagebox.showwarning("入力エラー", "96コードを入力してください。", parent=self.winfo_toplevel())
            return

        try:
            qty = int(self.entry_stock_qty.get().strip())
        except ValueError:
            messagebox.showwarning("入力エラー", "在庫数量には整数を入力してください。", parent=self.winfo_toplevel())
            return

        upsert_inventory(part_no, qty)
        self.load_inventory()
        messagebox.showinfo("完了", f"96コード {part_no} の在庫数量を登録しました。", parent=self.winfo_toplevel())

    def on_delete(self):
        part_no = self.entry_part_no.get().strip()
        if not part_no:
            messagebox.showwarning("入力エラー", "削除する96コードを入力してください。", parent=self.winfo_toplevel())
            return

        if not messagebox.askyesno("確認", f"96コード {part_no} の在庫データを削除します。よろしいですか？", parent=self.winfo_toplevel()):
            return

        delete_inventory(part_no)
        self.load_inventory()
        self.entry_part_no.delete(0, tk.END)
        self.entry_stock_qty.delete(0, tk.END)
        messagebox.showinfo("完了", f"96コード {part_no} を削除しました。", parent=self.winfo_toplevel())

    def on_csv_import(self):
        """
        在庫CSV（96コード・部品種別・部品支給区分・棚種別・在庫数・マスタCHK使用数等の13列）を
        取り込み、models.inventory.upsert_inventory_stock() へ保存する。
        """
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")], parent=self.winfo_toplevel())
        if not file_path:
            return

        try:
            result = _import_inventory_csv(file_path)
        except ValueError as e:
            messagebox.showerror("エラー", f"在庫CSV取込中にエラーが発生しました：\n{e}", parent=self.winfo_toplevel())
            return

        self.load_inventory()

        msg = f"成功件数：{result['imported']}件\n警告件数：{len(result['warnings'])}件"
        warnings = result["warnings"]
        if warnings:
            shown = "\n".join(warnings[:10])
            more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
            msg += f"\n\n{shown}{more}"

        messagebox.showinfo("在庫CSV取込結果", msg, parent=self.winfo_toplevel())
