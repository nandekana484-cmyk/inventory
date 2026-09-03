# ui/board_structure_import_window.py
import csv

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from models.board_structure_master import (
    list_board_structure, upsert_board_structure, delete_board_structure_not_in,
)

# エンコーディング自動判定の候補（この順で試す。ui/parts_attributes_import_window.py と同一）
_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp932"]

# 構成基板数CSVの列名
COL_BOARD_NAME = "基板名"
COL_BOARD_COUNT = "構成基板数"
# 構成基板数列の候補列名（この順で最初に一致したものを使う）
COL_BOARD_COUNT_CANDIDATES = ["構成基板数", "構成数"]


def _open_csv_with_fallback(file_path):
    """utf-8-sig → utf-8 → cp932 の順でエンコーディングを判定して開く。
    ui/parts_attributes_import_window.py::_open_csv_with_fallback() と同一実装。"""
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


def _import_board_structure_csv(file_path):
    """
    構成基板数CSV（基板名・構成基板数の2列、カンマ区切り）を解析し、
    models.board_structure_master.upsert_board_structure() へ保存する。

    以前はui/parts_attributes_import_window.py（タブ区切りTSV）に合わせて
    delimiter="\\t"としていたが、実運用のCSVはカンマ区切りであり、
    区切り文字の不一致によりヘッダーが正しく認識されず全行がスキップされる
    （インポート0件になる）不具合が発覚したため、カンマ区切りに修正した。

    CSVをマスタとした差分同期：全行のupsertが正常に完了した後、今回のCSVに
    含まれていた board_name 一覧と現在のテーブル内容を比較し、CSVに存在しない
    既存 board_name を models.board_structure_master.delete_board_structure_not_in() で
    削除する。削除は「全行upsertが正常に完了した後」にのみ実行するため、CSV解析中に
    例外が発生した場合は削除は行われない。

    事故防止ガード：CSVにデータ行が1件も無い場合（空ファイル・ヘッダーのみ等）、
    または有効な基板名を含む行が1件も無い場合は、全件削除という事故を避けるため
    同期処理そのものを中断し、upsert・削除のいずれも行わない。

    必須列：基板名（欠けている・空の行は警告してスキップ）
    任意列：構成基板数。候補列名（COL_BOARD_COUNT_CANDIDATES＝「構成基板数」
    「構成数」）のいずれかがヘッダーに存在すればそれを使う。いずれも見つからず、
    かつCSVがちょうど2列構成（基板名列＋もう1列）の場合に限り、その「もう1列」
    を構成基板数として位置ベースで読み込むフォールバックを行う（実運用のCSVで
    2列目にヘッダー名自体が付いていない実例が発覚したため）。3列以上ある場合は
    誤った列を拾うリスクを避けるためフォールバックしない（候補列名一致のみ）。
    数値変換できない場合は警告のうえNoneのまま保存する。

    区切り文字・文字コードの不一致でヘッダーが正しく認識されないと、
    全行が「基板名が空」としてスキップされてしまう（部品属性インポートと同種の
    パターン）。これに気づきやすくするため、基板名空欄によるスキップが読み込み
    行数の9割以上を占める場合は、通常の行単位警告とは別に、ファイル形式の確認を
    促す注意喚起メッセージを warnings の先頭に追加する。同様に、基板名は取得
    できたが構成基板数が未取得（空欄・非数値・列自体が無い）だった行が、
    インポート件数の5割以上を占める場合も、列名・区切り文字の確認を促す
    注意喚起メッセージを追加する。

    戻り値：{"imported": 成功件数(upsert件数), "deleted": 削除件数,
             "board_count_missing": 構成基板数が未取得だったimported行数,
             "notices": 警告とは別の情報メッセージのリスト（列位置フォールバック発動時等）,
             "warnings": 警告メッセージのリスト}
    """
    warnings = []
    notices = []

    with _open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f, delimiter=",")
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if not rows:
        return {
            "imported": 0,
            "deleted": 0,
            "board_count_missing": 0,
            "notices": [],
            "warnings": ["CSVにデータ行が1件も無いため、インポートを中断しました（削除も行っていません）。"],
        }

    # 構成基板数列の決定：候補列名を優先し、いずれも無くCSVがちょうど2列
    # （基板名列＋もう1列）の場合のみ、その「もう1列」を位置ベースで採用する。
    board_count_col = next((c for c in COL_BOARD_COUNT_CANDIDATES if c in fieldnames), None)
    if board_count_col is None:
        other_columns = [fn for fn in fieldnames if fn != COL_BOARD_NAME]
        if len(fieldnames) == 2 and len(other_columns) == 1:
            board_count_col = other_columns[0]
            notices.append(
                f"列名が想定（{'/'.join(COL_BOARD_COUNT_CANDIDATES)}）と異なるため、"
                "2列目を構成基板数として読み込みました。"
            )

    imported = 0
    seen_board_names = []
    skipped_count = 0
    board_count_missing_count = 0

    for i, row in enumerate(rows, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
        board_name = (row.get(COL_BOARD_NAME) or "").strip()
        if not board_name:
            warnings.append(f"{i}行目: {COL_BOARD_NAME}が空のためスキップしました。")
            skipped_count += 1
            continue

        board_count_raw = (row.get(board_count_col) or "").strip() if board_count_col is not None else ""
        board_count = None
        if board_count_raw:
            try:
                board_count = float(board_count_raw)
            except ValueError:
                warnings.append(
                    f"{i}行目: {COL_BOARD_COUNT}「{board_count_raw}」を数値に変換できないため未設定のまま保存しました。"
                )

        upsert_board_structure(board_name, board_count)
        imported += 1
        seen_board_names.append(board_name)
        if board_count is None:
            board_count_missing_count += 1

    total_rows = len(rows)
    if total_rows > 0 and skipped_count / total_rows >= 0.9:
        warnings.insert(
            0,
            f"※ 読み込んだ{total_rows}行中{skipped_count}行"
            f"（{skipped_count / total_rows * 100:.0f}%）が{COL_BOARD_NAME}空欄でスキップされました。"
            "区切り文字（タブ/カンマ）や文字コードがファイルの実際の形式と"
            "一致していない可能性があります。ファイル形式をご確認ください。",
        )

    if imported > 0 and board_count_missing_count / imported >= 0.5:
        warnings.append(
            f"※ 登録した{imported}行中{board_count_missing_count}行"
            f"（{board_count_missing_count / imported * 100:.0f}%）で{COL_BOARD_COUNT}を取得できませんでした。"
            f"区切り文字や列名（{'/'.join(COL_BOARD_COUNT_CANDIDATES)}）が"
            "ファイルの実際の形式と一致していない可能性があります。ファイル形式をご確認ください。"
        )

    if not seen_board_names:
        warnings.append("有効な基板名を含む行が1件も無かったため、削除は行っていません。")
        return {
            "imported": imported, "deleted": 0, "board_count_missing": board_count_missing_count,
            "notices": notices, "warnings": warnings,
        }

    deleted_board_names = delete_board_structure_not_in(seen_board_names)

    return {
        "imported": imported, "deleted": len(deleted_board_names),
        "board_count_missing": board_count_missing_count, "notices": notices, "warnings": warnings,
    }


class BoardStructureImportWindow(tk.Toplevel):
    """
    構成基板数マスタをCSVからインポートする画面。

    生産実績入力画面（ui/kitting_production_entry.py）の計画情報欄「構成基板数」
    表示で、plan["board_name"]をキーに参照される。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.selected_csv_path = None

        self.title("構成基板数マスタインポート")
        self.geometry("560x480")

        select_frame = ttk.Frame(self, padding=10)
        select_frame.pack(fill=tk.X)

        ttk.Button(select_frame, text="CSV選択", command=self.on_select_csv).pack(side=tk.LEFT, padx=5)
        self.lbl_csv_path = ttk.Label(select_frame, text="（未選択）", foreground="blue")
        self.lbl_csv_path.pack(side=tk.LEFT, padx=5)

        ttk.Button(select_frame, text="インポート実行", command=self.on_import_execute).pack(side=tk.LEFT, padx=15)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("board_name", "board_count")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("board_name", text="基板名")
        self.tree.heading("board_count", text="構成基板数")
        self.tree.column("board_name", width=280, anchor=tk.W)
        self.tree.column("board_count", width=120, anchor=tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.load_board_structure()

    def load_board_structure(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in list_board_structure():
            self.tree.insert("", tk.END, values=(
                row["board_name"],
                row.get("board_count") if row.get("board_count") is not None else "",
            ))

    def on_select_csv(self):
        file_path = filedialog.askopenfilename(
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*"),
            ],
            parent=self.winfo_toplevel(),
        )
        if not file_path:
            return
        self.selected_csv_path = file_path
        self.lbl_csv_path.config(text=file_path)

    def on_import_execute(self):
        if not self.selected_csv_path:
            messagebox.showwarning("警告", "CSVファイルを選択してください。", parent=self.winfo_toplevel())
            return

        try:
            result = _import_board_structure_csv(self.selected_csv_path)
        except ValueError as e:
            messagebox.showerror("エラー", f"構成基板数CSV取込中にエラーが発生しました：\n{e}", parent=self.winfo_toplevel())
            return

        self.load_board_structure()

        msg = (
            f"成功件数：{result['imported']}件\n"
            f"構成基板数未取得件数：{result.get('board_count_missing', 0)}件\n"
            f"警告件数：{len(result['warnings'])}件\n"
            f"削除件数：{result.get('deleted', 0)}件"
        )

        notices = result.get("notices") or []
        if notices:
            msg += "\n\n" + "\n".join(notices)

        warnings = result["warnings"]
        if warnings:
            shown = "\n".join(warnings[:10])
            more = f"\n...ほか{len(warnings) - 10}件" if len(warnings) > 10 else ""
            msg += f"\n\n{shown}{more}"

        messagebox.showinfo("構成基板数CSV取込結果", msg, parent=self.winfo_toplevel())
