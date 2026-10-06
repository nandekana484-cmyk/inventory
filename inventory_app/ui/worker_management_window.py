# ui/worker_management_window.py
import tkinter as tk
from tkinter import ttk, messagebox

from models.workers import get_all_workers, upsert_worker, set_worker_active
from models.operation_log import log_operation
from ui.window_utils import center_window
from ui.worker_registration_window import WorkerRegistrationWindow


class WorkerManagementWindow(tk.Toplevel):
    """
    既存のログイン作業者（workers）の編集（氏名・役割の変更）・有効/無効切り替えを
    行う画面。

    2026-10-03、新規登録機能はui/worker_registration_window.py::
    WorkerRegistrationWindowへ分離した（ログイン前でも使える必要がある登録と、
    既存作業者の変更を区別するため）。本画面は既存作業者の編集・有効/無効
    切替のみを担当し、新規登録フォームは持たない。

    role制限（重要、二重の防御）：編集（save_worker()）・有効/無効切替
    （toggle_active()）は、ログイン中の作業者（current_worker）のroleが
    "admin"であることを要求する。
      1. 見た目の対策（2026-10-03追加）：__init__()でself._is_adminを判定し、
         admin以外の場合は保存・切替ボタンをstate="disabled"のままにする
         （on_select()で行を選択してもNORMALへ戻さない）。あわせて「この画面の
         操作にはadmin権限が必要です」という案内ラベルを表示する。
      2. 実処理の対策（従来通り）：save_worker()・toggle_active()の冒頭で
         _require_admin()を呼ぶ。ボタンのstateを直接書き換える、または本画面を
         直接インスタンス化してメソッドを呼び出す等、1.の見た目の対策を
         バイパスする経路があっても、こちらが最終的な防御として機能する
         （メインメニュー側でadmin以外にはこの画面自体を開くボタンを表示しない
         設計と合わせ、3重の防御になる。CANONICAL_DESIGN_DECISIONS.md参照）。

    production_daily.worker_id・audit_log.worker_id 等、過去実績から
    作業者IDが参照される可能性があるため、削除（DELETE）は提供せず、
    is_active フラグによる無効化のみをサポートする
    （無効化した作業者はログイン画面の一覧から外れるが、履歴の参照は壊れない）。
    """
    def __init__(self, parent, current_worker=None):
        super().__init__(parent)
        self.current_worker = current_worker or {}
        # ボタンのグレーアウト判定用（2026-10-03追加）。_require_admin()と
        # 同じ条件だが、こちらは見た目（state="disabled"）の制御にのみ使う。
        # 実際の操作可否は引き続きsave_worker()・toggle_active()冒頭の
        # _require_admin()が担う（ボタンのstateを直接書き換えてバイパスされた
        # 場合でも、関数側のチェックで拒否されるようにするため）。
        self._is_admin = self.current_worker.get("role") == "admin"
        self.title("アカウント管理")
        self.geometry("640x480")
        center_window(self, parent)

        if not self._is_admin:
            ttk.Label(
                self, text="この画面の操作にはadmin権限が必要です（閲覧のみ可能）",
                foreground="#cc0000", font=("Helvetica", 10, "bold"),
            ).pack(fill=tk.X, padx=10, pady=(10, 0))

        frame_input = ttk.LabelFrame(self, text="選択した作業者の編集", padding=10)
        frame_input.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(frame_input, text="作業者ID:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.lbl_worker_id = ttk.Label(frame_input, text="（一覧から選択してください）")
        self.lbl_worker_id.grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)

        ttk.Label(frame_input, text="氏名:").grid(row=0, column=2, sticky=tk.W, pady=3)
        self.entry_name = ttk.Entry(frame_input, width=20)
        self.entry_name.grid(row=0, column=3, sticky=tk.W, padx=5, pady=3)

        ttk.Label(frame_input, text="役割:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.combo_role = ttk.Combobox(frame_input, width=13, values=["operator", "admin"], state="readonly")
        self.combo_role.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)

        self.btn_save = ttk.Button(frame_input, text="保存（編集を反映）", command=self.save_worker, state=tk.DISABLED)
        self.btn_save.grid(row=1, column=3, sticky=tk.E, padx=10)

        frame_list = ttk.Frame(self, padding=5)
        frame_list.pack(expand=True, fill=tk.BOTH, padx=10)

        cols = ("worker_id", "name", "role", "status")
        self.tree = ttk.Treeview(frame_list, columns=cols, show="headings")
        self.tree.heading("worker_id", text="作業者ID")
        self.tree.heading("name", text="氏名")
        self.tree.heading("role", text="役割")
        self.tree.heading("status", text="状態")
        self.tree.column("worker_id", width=120, anchor=tk.W)
        self.tree.column("name", width=180, anchor=tk.W)
        self.tree.column("role", width=100, anchor=tk.W)
        self.tree.column("status", width=80, anchor=tk.CENTER)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)
        # 「新規作成」（2026-10-06追加）：本画面には以前、新規作成機能が
        # 無かった（既存作業者の編集・有効/無効切替のみ）。ui.worker_
        # registration_window.WorkerRegistrationWindow（ログイン画面・
        # メインメニュー「3. 新規アカウント登録」からも開ける、同じ登録画面）
        # をモーダル的に開き、登録完了後にload_workers()で一覧を更新する
        # （検証ロジックを本画面側に複製しない）。admin限定の画面のため、
        # ボタン自体は常にNORMAL（admin以外はこの画面を開けない設計、
        # モジュールdocstring参照）。
        ttk.Button(btn_frame, text="新規作成", command=self.on_create_new_worker).pack(side=tk.LEFT, padx=5)
        self.btn_toggle_active = ttk.Button(
            btn_frame, text="選択行の有効/無効を切り替え", command=self.toggle_active, state=tk.DISABLED
        )
        self.btn_toggle_active.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.load_workers()

    def _require_admin(self) -> bool:
        """
        ログイン中の作業者がadmin役割かどうかを確認する。admin以外の場合は
        エラーダイアログを表示してFalseを返す（呼び出し元はこれ以上処理を
        進めないこと）。メインメニュー側のボタン非表示とは独立に、本画面の
        編集・有効/無効切替メソッドの入口で必ず確認する（直接インスタンス化
        された場合への対策、モジュールdocstring参照）。
        """
        if self.current_worker.get("role") != "admin":
            messagebox.showerror(
                "権限がありません",
                "この操作はadmin役割の作業者のみ実行できます。",
                parent=self.winfo_toplevel(),
            )
            return False
        return True

    def on_create_new_worker(self):
        if not self._require_admin():
            return
        win = WorkerRegistrationWindow(self, current_worker=self.current_worker, on_registered=self.load_workers)
        self.wait_window(win)

    def load_workers(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for w in get_all_workers():
            status = "有効" if w["is_active"] else "無効"
            self.tree.insert("", tk.END, values=(w["worker_id"], w["name"], w["role"], status))

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            self.lbl_worker_id.config(text="（一覧から選択してください）")
            self.entry_name.delete(0, tk.END)
            self.combo_role.set("")
            self.btn_save.config(state=tk.DISABLED)
            self.btn_toggle_active.config(state=tk.DISABLED)
            return
        values = self.tree.item(sel[0], "values")
        self.lbl_worker_id.config(text=values[0])
        self.entry_name.delete(0, tk.END)
        self.entry_name.insert(0, values[1])
        self.combo_role.set(values[2])
        # admin以外は、行を選択してもボタンを有効化しない（見た目の対策。
        # __init__()のコメント・_require_admin()参照）。
        if self._is_admin:
            self.btn_save.config(state=tk.NORMAL)
            self.btn_toggle_active.config(state=tk.NORMAL)

    def save_worker(self):
        if not self._require_admin():
            return

        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("エラー", "編集する作業者を一覧から選択してください。", parent=self.winfo_toplevel())
            return

        values = self.tree.item(sel[0], "values")
        worker_id = values[0]
        is_active = values[3] == "有効"

        name = self.entry_name.get().strip()
        role = self.combo_role.get().strip() or "operator"

        if not name:
            messagebox.showwarning("エラー", "氏名を入力してください。", parent=self.winfo_toplevel())
            return

        # is_activeは本画面では変更しない（toggle_active()専用、モジュール
        # docstring参照）ため、一覧に表示されている現在の値をそのまま引き継ぐ。
        upsert_worker(worker_id, name, role, is_active)
        log_operation(
            self.current_worker.get("name", "unknown"),
            "作業者編集",
            detail=f"{worker_id}（{name}）",
        )
        self.load_workers()
        messagebox.showinfo("完了", f"作業者「{name}」を保存しました。", parent=self.winfo_toplevel())

    def toggle_active(self):
        if not self._require_admin():
            return

        sel = self.tree.selection()
        if not sel:
            return
        values = self.tree.item(sel[0], "values")
        worker_id, name, current_status = values[0], values[1], values[3]
        new_active = current_status != "有効"

        action_label = "有効化" if new_active else "無効化"
        if not messagebox.askyesno(
            "確認", f"作業者「{name}」を{action_label}します。よろしいですか？",
            parent=self.winfo_toplevel(),
        ):
            return

        set_worker_active(worker_id, new_active)
        log_operation(
            self.current_worker.get("name", "unknown"),
            f"作業者{action_label}",
            detail=f"{worker_id}（{name}）",
        )
        self.load_workers()
        messagebox.showinfo("完了", f"作業者「{name}」を{action_label}しました。", parent=self.winfo_toplevel())
