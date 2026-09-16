# ui/production_import_staging_window.py
"""
実績CSV取込のステージング一覧（「確認・選択・転記」方式）。

models.production_import_staging.pending_csv_import_rows（DBへは未登録の
CSV行一覧、_load_staged_rows_from_db()参照）を表示する。行をダブルクリック
すると、呼び出し元（ui.kitting_production_entry.KittingProductionEntryWindow）
に選択を委譲する。実際の計画選択・実績記入欄への転記・登録確認ダイアログ〜
登録処理（production_dailyへの書き込み）は、呼び出し元の既存の仕組み
（search_plan()・_start_registration()）にそのまま任せる（本ウインドウ自身が
production_dailyへ書き込むことは無いが、ステージングデータ自体
（pending_csv_import_rows）の読み込み・削除は本ウインドウ・本モジュールの
責務である）。

登録完了の検知はコールバック方式：呼び出し元が実際の登録に成功した時点で、
本ウインドウから渡されたremove_callbackを呼んでもらうことで、該当行を
一覧から消す（本ウインドウ側からDBの状態を能動的にポーリングはしない）。

ステージングデータの永続化（models.production_import_staging）：以前は
staged_rows（候補情報candidates/matchedを含む）を呼び出し元がメモリ上で
保持し、本ウインドウを閉じると失われる構造だったが、CSVの生の行データ
（lot_no・product_name・daily_qty・report_date・worker_id）をDB
（pending_csv_import_rows）へ永続化するよう変更した。候補（candidates/
matched）自体はDBに保存せず、_load_staged_rows_from_db()が表示のたびに
models.kitting_plan.find_matching_plan_items()で再照合する（計画の変更に
追随できるよう、常に最新のDB状態を反映するため）。登録・除外が確定した
行はdelete_pending_csv_import_row()で即座に物理削除し、履歴としては
残さない。
"""
import csv

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from services.production_import_service import (
    STAGING_STATUS_LABELS,
    normalize_product_name,
    group_active_plan_items_by_lot,
)
from models.kitting_plan import find_matching_plan_items
from models.production_import_staging import (
    list_pending_csv_import_rows,
    delete_pending_csv_import_row,
)

# "候補なし"（find_matching_plan_items()の候補が0件）行をCSV出力する際の理由欄。
# services.production_import_service.import_production_csv()のunmatched理由
# 文言と揃えている。
REASON_NO_CANDIDATES = "計画が見つからない（該当lot_noの計画なし）"

# 「不一致として除外」時、除外理由の入力を空欄のまま・キャンセルした場合の
# デフォルト文言。REASON_NO_CANDIDATES（機械判定による固定理由1種類のみ）とは
# 異なり、不一致は人間が個別に判断するため理由は行ごとに様々であり得る
# （例：ロットNoの入力ミス、対象外の製品、二重入力等）。そのため理由入力欄は
# 任意入力（自由記述）とし、何も入力されなかった場合のみこのデフォルト文言を
# 使う方針とした。
REASON_MISMATCH_DEFAULT = "不一致と判断（詳細理由未入力）"


def _load_staged_rows_from_db():
    """
    models.production_import_staging.list_pending_csv_import_rows()で未処理行を
    DBから取得し、各行についてmodels.kitting_plan.find_matching_plan_items()で
    候補（candidates/matched）・状態（status）を再計算した上で、従来と同じ形の
    辞書リスト（{"pending_row_id", "row", "lot_no", "product_name", "daily_qty",
    "report_date", "worker_id", "candidates", "matched", "status"}）を組み立てる。

    候補をDBに保存せず必ず再照合する理由はmodels.production_import_stagingの
    docstring参照。plan_items_by_lot（services.production_import_service.
    group_active_plan_items_by_lot()）は本関数内で1回だけ取得し、未処理行数分の
    find_matching_plan_items()呼び出しで使い回す（CSV取込時と同じN+1回避）。
    """
    pending_rows = list_pending_csv_import_rows()
    if not pending_rows:
        return []

    plan_items_by_lot = group_active_plan_items_by_lot()

    staged_rows = []
    for row in pending_rows:
        product_name_normalized = normalize_product_name(row["product_name"])
        candidates, matched = find_matching_plan_items(
            row["lot_no"], product_name_normalized, plan_items_by_lot,
        )

        if not candidates:
            status = "no_candidates"
        else:
            unique_kitting_nos = {c["kitting_list_no"] for c in matched}
            status = "auto_resolvable" if len(unique_kitting_nos) == 1 else "needs_selection"

        staged_rows.append({
            "pending_row_id": row["pending_row_id"],
            "row": row.get("csv_row_no"),
            "lot_no": row["lot_no"],
            "product_name": row["product_name"],
            "daily_qty": row["daily_qty"],
            "report_date": row["report_date"],
            "worker_id": row["worker_id"],
            "candidates": candidates,
            "matched": matched,
            "status": status,
        })
    return staged_rows


def open_or_notify(parent, on_row_confirmed):
    """
    未処理の保留行（pending_csv_import_rows）が1件でもあればProductionImport
    StagingWindowを開き、無ければ案内メッセージのみ表示する（新規CSV取込直後
    ・メインメニューの「実績CSV取込状況」からの再開、両方の入口から使う
    共通のエントリーポイント）。

    戻り値：開いたProductionImportStagingWindow、または未処理行が無く
    開かなかった場合はNone。
    """
    staged_rows = _load_staged_rows_from_db()
    if not staged_rows:
        messagebox.showinfo("実績CSV取込状況", "未処理の取込データはありません。", parent=parent)
        return None
    return ProductionImportStagingWindow(parent, staged_rows, on_row_confirmed)


class ProductionImportStagingWindow(tk.Toplevel):
    def __init__(self, parent, staged_rows, on_row_confirmed):
        """
        staged_rows：_load_staged_rows_from_db()が組み立てる辞書のリスト
        （各要素は"pending_row_id"/"row"/"lot_no"/"product_name"/"daily_qty"/
        "report_date"/"worker_id"/"candidates"/"matched"/"status"を持つ）。
        本クラス自体はopen_or_notify()経由で呼ばれる想定で、直接staged_rowsを
        組み立てて渡すのは主にテスト用途。

        on_row_confirmed(row, remove_callback)：一覧の行がダブルクリックされ、
        candidatesが1件以上ある場合に呼ばれるコールバック。呼び出し元は
        候補選択ダイアログの表示〜計画確定〜実績記入欄への転記までを行い、
        実際の登録（既存の_start_registration()フロー）が完了した時点で
        remove_callback()を呼び、この一覧から該当行を消してもらうこと。
        candidatesが0件（"no_candidates"）の行は、on_row_confirmed()を呼ばず
        本ウインドウ内で直接「候補なし」メッセージを表示するだけに留める。
        """
        super().__init__(parent)
        self._parent = parent
        self.title("実績CSV取込：登録待ち一覧")
        self.geometry("820x420")
        self.on_row_confirmed = on_row_confirmed
        self._row_by_iid = {}
        # "no_candidates"（候補なし＝登録不可）と判定された行を、一覧から消えても
        # 参照できるよう別途保持しておく（CSV出力ボタン用）。一覧本体
        # （self._row_by_iid）は登録完了のたびに行が消えていくが、こちらは
        # ウインドウを閉じるまで消さない。
        self._unregistrable_rows = [
            row for row in staged_rows if row.get("status") == "no_candidates"
        ]
        # 「不一致として除外」（右クリックメニュー）で個別に人間が判断した行を
        # 保持する別リスト。self._unregistrable_rows（machine判定の"no_candidates"、
        # CSV出力時にまとめて削除）とは異なり、こちらは除外を選んだ時点で即座に
        # pending_csv_import_rowsから削除する（_mark_as_mismatched()参照）。
        self._mismatched_rows = []

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("lot_no", "product_name", "report_date", "worker_id", "daily_qty", "status")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        self.tree.heading("lot_no", text="ロットNo")
        self.tree.heading("product_name", text="製品名")
        self.tree.heading("report_date", text="払い出し日（参考）")
        self.tree.heading("worker_id", text="作業者")
        self.tree.heading("daily_qty", text="実績数")
        self.tree.heading("status", text="状態")
        self.tree.column("lot_no", width=100, anchor=tk.W)
        self.tree.column("product_name", width=180, anchor=tk.W)
        self.tree.column("report_date", width=120, anchor=tk.CENTER)
        self.tree.column("worker_id", width=90, anchor=tk.W)
        self.tree.column("daily_qty", width=70, anchor=tk.E)
        self.tree.column("status", width=160, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        for row in staged_rows:
            iid = self.tree.insert("", tk.END, values=(
                row.get("lot_no", ""),
                row.get("product_name", ""),
                row.get("report_date") or "",
                row.get("worker_id", ""),
                row.get("daily_qty", ""),
                STAGING_STATUS_LABELS.get(row.get("status"), row.get("status", "")),
            ))
            self._row_by_iid[iid] = row

        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        ttk.Label(
            self,
            text=(
                "行をダブルクリックすると計画の候補選択に進みます。登録が完了すると一覧から自動的に消えます。\n"
                "右クリックで「不一致として除外」できます（計画が無い／異なると人間が判断した行）。"
            ),
            foreground="gray",
        ).pack(anchor=tk.W, padx=10)

        # 登録不可（machine判定）・不一致（人間の判断で除外）、それぞれの件数を
        # 分かりやすく常時表示する。件数が変わるたび_update_status_label()で
        # 更新する。
        self.lbl_status = ttk.Label(self, foreground="gray")
        self.lbl_status.pack(anchor=tk.W, padx=10, pady=(2, 0))
        self._update_status_label()

        btn_frame = ttk.Frame(self)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="登録不可リストをCSV出力", command=self.on_export_unregistrable_csv).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="不一致リストをCSV出力", command=self.on_export_mismatched_csv).pack(
            side=tk.LEFT, padx=5
        )
        ttk.Button(btn_frame, text="閉じる", command=self._on_close).pack(side=tk.LEFT, padx=5)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _update_status_label(self):
        """登録不可・不一致、それぞれの件数表示を最新化する。"""
        self.lbl_status.config(
            text=(
                f"登録不可：{len(self._unregistrable_rows)}件　"
                f"不一致として除外：{len(self._mismatched_rows)}件"
            )
        )

    def _on_close(self):
        """
        以前は一覧に未登録の行が残っている場合「閉じてもよろしいですか？」の
        確認ダイアログ（はい/いいえ、キャンセル可能なブロッキングダイアログ）を
        表示していたが、ステージングデータの永続化（models.production_import_
        staging.pending_csv_import_rows）により、閉じても未処理行は失われなく
        なった（メインメニューの「実績CSV取込状況」からいつでも再開できる）ため、
        確認ゲート自体が不要になった。代わりに、未処理行が残っている場合のみ、
        閉じる操作自体は妨げない非ブロッキングな案内を表示する
        （_show_closing_notice()参照）。
        """
        remaining = len(self._row_by_iid)
        parent = self._parent
        self.destroy()
        if remaining:
            self._show_closing_notice(parent, remaining)

    @staticmethod
    def _show_closing_notice(parent, remaining_count):
        """
        親ウインドウ（呼び出し元のui.kitting_production_entry.
        KittingProductionEntryWindow）上に、自動的に消える非ブロッキングの
        案内を表示する。messagebox（「OK」クリックを要求するモーダル）は
        「閉じる操作自体を妨げない」という要件に合わないため使わず、
        枠のみのToplevel + after()による自動destroy()で実装した
        （実装コストが低く、閉じる処理をブロックしない方法として採用）。

        親ウインドウが既に閉じられている等、表示できない場合は静かに諦める
        （案内が出せないこと自体はエラー扱いしない。データはDBに永続化
        済みのため、通知が出せなくても実害は無い）。
        """
        try:
            if parent is None or not parent.winfo_exists():
                return
            notice = tk.Toplevel(parent)
            notice.overrideredirect(True)
            notice.attributes("-topmost", True)
            ttk.Label(
                notice,
                text=(
                    f"{remaining_count}件が未処理のまま残っています。\n"
                    "メインメニューの「実績CSV取込状況」からいつでも再開できます。"
                ),
                padding=10, background="#fff3cd", relief=tk.SOLID, borderwidth=1,
            ).pack()
            parent.update_idletasks()
            x = parent.winfo_rootx() + 40
            y = parent.winfo_rooty() + 40
            notice.geometry(f"+{x}+{y}")
            notice.after(3000, notice.destroy)
        except tk.TclError:
            pass

    def on_export_unregistrable_csv(self):
        """
        登録不可（"no_candidates"＝該当lot_noの計画が見つからなかった）行を
        CSV出力する。該当行が1件も無い場合は出力せずメッセージのみ表示する。

        出力成功後、該当行を一覧（self.tree・self._row_by_iid）・DB
        （pending_csv_import_rows、delete_pending_csv_import_row()）からも
        削除する（self._unregistrable_rows自体は保持したまま：ウインドウを
        閉じるまで参照可能にしておく従来の方針は変えない）。DBから削除しないと、
        CSVに出力済みでも「未処理」のまま残り続け、次回ステージング一覧を
        開いた際に再び表示されてしまうため。
        """
        if not self._unregistrable_rows:
            messagebox.showinfo("登録不可リスト", "登録不可の行はありません。", parent=self)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_unregistrable.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._unregistrable_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        REASON_NO_CANDIDATES,
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return

        iids_to_remove = [
            iid for iid, row in self._row_by_iid.items()
            if row.get("status") == "no_candidates"
        ]
        for iid in iids_to_remove:
            pending_row_id = self._row_by_iid[iid].get("pending_row_id")
            self.tree.delete(iid)
            del self._row_by_iid[iid]
            # 除外済み（CSV出力で対応済みとした）行は履歴として残さず、
            # pending_csv_import_rowsから即座に物理削除する
            # （models.production_import_stagingの方針。__init__()参照）。
            if pending_row_id is not None:
                delete_pending_csv_import_row(pending_row_id)

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        if not row.get("candidates"):
            messagebox.showinfo(
                "候補なし",
                f"ロットNo. {row.get('lot_no')} に該当する計画が見つかりません。",
                parent=self,
            )
            return

        def remove_callback():
            if iid in self._row_by_iid:
                pending_row_id = self._row_by_iid[iid].get("pending_row_id")
                self.tree.delete(iid)
                del self._row_by_iid[iid]
                # 登録済みになった行は履歴として残さず、pending_csv_import_rowsから
                # 即座に物理削除する（models.production_import_stagingの方針。
                # __init__()参照）。
                if pending_row_id is not None:
                    delete_pending_csv_import_row(pending_row_id)

        self.on_row_confirmed(row, remove_callback)

    def _on_right_click(self, event):
        """
        右クリックされた行を選択状態にした上で、「不一致として除外」の
        単一メニュー項目を持つコンテキストメニューを表示する。
        """
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="不一致として除外",
            command=lambda: self._mark_as_mismatched(iid),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _mark_as_mismatched(self, iid):
        """
        人間が「この行に一致する計画は無い／違う」と判断した場合の除外操作。
        一覧（self.tree・self._row_by_iid）・DB（pending_csv_import_rows）から
        即座に削除し、self._mismatched_rows（不一致リストCSV出力用、ウインドウを
        閉じるまで保持）へ追加する。self._unregistrable_rows（machine判定の
        "no_candidates"、CSV出力時にまとめて削除）とは別枠で管理する
        （__init__()のコメント参照。既存の登録不可リストと混同しないため）。

        除外理由：人間の個別判断のため理由は様々であり得る（ロットNoの
        入力ミス・対象外の製品・二重入力等）。REASON_NO_CANDIDATES
        （機械判定による固定文言1種類）とは異なり、任意入力（自由記述）の
        ダイアログで尋ね、空欄・キャンセル時はREASON_MISMATCH_DEFAULTを使う。
        """
        row = self._row_by_iid.get(iid)
        if row is None:
            return

        reason = simpledialog.askstring(
            "不一致として除外",
            f"ロットNo. {row.get('lot_no')} を不一致として除外します。\n"
            "除外理由（任意）：",
            parent=self,
        )
        reason = (reason or "").strip() or REASON_MISMATCH_DEFAULT

        pending_row_id = row.get("pending_row_id")
        self.tree.delete(iid)
        del self._row_by_iid[iid]
        if pending_row_id is not None:
            delete_pending_csv_import_row(pending_row_id)

        self._mismatched_rows.append({**row, "mismatch_reason": reason})
        self._update_status_label()

    def on_export_mismatched_csv(self):
        """
        「不一致として除外」された行（self._mismatched_rows）をCSV出力する。
        on_export_unregistrable_csv()と同じ形式（utf-8-sig、列構成：lot_no・
        product_name・daily_qty・report_date・reason）だが、reason列は行ごとに
        個別入力された除外理由をそのまま使う（REASON_NO_CANDIDATESのような
        固定文言1種類ではない）。

        既にpending_csv_import_rowsからの削除・一覧からの除去は
        _mark_as_mismatched()の時点で完了済みのため、ここではCSVへの書き出しの
        みを行う（on_export_unregistrable_csv()と異なり、削除処理は無い）。
        """
        if not self._mismatched_rows:
            messagebox.showinfo("不一致リスト", "不一致として除外した行はありません。", parent=self)
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialfile="production_import_mismatched.csv",
            filetypes=[("CSV files", "*.csv")],
            parent=self,
        )
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["lot_no", "product_name", "daily_qty", "report_date", "reason"])
                for row in self._mismatched_rows:
                    writer.writerow([
                        row.get("lot_no", ""),
                        row.get("product_name", ""),
                        row.get("daily_qty", ""),
                        row.get("report_date") or "",
                        row.get("mismatch_reason", REASON_MISMATCH_DEFAULT),
                    ])
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self)
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self)
