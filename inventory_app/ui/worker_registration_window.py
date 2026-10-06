# ui/worker_registration_window.py
"""
作業者の新規登録専用の画面（2026-10-03新設）。

旧ui/worker_management_window.py::WorkerManagementWindowは「新規登録」と
「既存作業者の編集・有効/無効切替」の両方を1画面で担っていたが、編集・
有効/無効切替にはログイン中の作業者がadminであることを要求する方針に
変更したため（CANONICAL_DESIGN_DECISIONS.md参照）、ログイン前（adminの
概念自体が存在しない状況）でも必ず使える「新規登録」だけを、本画面として
分離した。

admin役割の選択可否：master.dbにrole='admin'の作業者が1人も存在しない
場合（初回導入時等）のみ、役割の選択肢に"admin"を含める。既に1人でも
adminが存在する場合は、選択肢を"operator"のみとする（以後のadmin追加は、
既存adminが管理画面から役割を変更する運用を想定）。
"""
import tkinter as tk
from tkinter import ttk, messagebox

from models.workers import create_worker, any_admin_exists, is_worker_id_taken
from ui.window_utils import center_window

# 作業者IDの長さ上限（2026-10-06追加、自由入力化に伴う実装上の判断）。
# DBのworker_id列自体はTEXT型で長さ制約が無く、既存のログイン画面も
# 作業者IDを直接入力させる場面が無いため、他の既存の型・制約からの
# 直接の制限値は存在しない。実用上の一覧表示・入力欄の見た目を考慮し、
# 1〜32文字という常識的な上限を本画面側の検証として新たに設けた
# （DBスキーマ自体は変更していない）。
WORKER_ID_MAX_LENGTH = 32


class WorkerRegistrationWindow(tk.Toplevel):
    """
    氏名・役割を入力して新規作業者を登録するだけのシンプルな画面。
    ui/login_window.py（ログイン前）・ui/main_window.py（ログイン後）の
    両方から開ける（current_workerは記録用、ログイン前はNone/{}）。
    """
    def __init__(self, parent, current_worker=None, on_registered=None):
        super().__init__(parent)
        self.current_worker = current_worker or {}
        self.on_registered = on_registered
        self.title("新規アカウント登録")
        self.geometry("360x260")
        self.resizable(False, False)
        center_window(self, parent)

        frame = ttk.Frame(self, padding=20)
        frame.pack(expand=True, fill=tk.BOTH)

        ttk.Label(frame, text="新規作業者登録", font=("Helvetica", 14, "bold")).pack(pady=(0, 15))

        # 作業者ID入力欄（2026-10-06追加、自由入力化。以前はUUID由来で
        # 自動生成しユーザーには入力させていなかった）。
        row_id = ttk.Frame(frame)
        row_id.pack(fill=tk.X, pady=5)
        ttk.Label(row_id, text="ID:", width=8).pack(side=tk.LEFT)
        self.entry_worker_id = ttk.Entry(row_id)
        self.entry_worker_id.pack(side=tk.LEFT, expand=True, fill=tk.X)

        row_name = ttk.Frame(frame)
        row_name.pack(fill=tk.X, pady=5)
        ttk.Label(row_name, text="氏名:", width=8).pack(side=tk.LEFT)
        self.entry_name = ttk.Entry(row_name)
        self.entry_name.pack(side=tk.LEFT, expand=True, fill=tk.X)

        row_role = ttk.Frame(frame)
        row_role.pack(fill=tk.X, pady=5)
        ttk.Label(row_role, text="役割:", width=8).pack(side=tk.LEFT)

        # admin役割の選択可否（モジュールdocstring参照）。既に1人でもadminが
        # 存在する場合は"operator"のみを選択肢とし、admin自体を選べなくする
        # （readonlyコンボボックスのため、一覧に無い値は入力もできない）。
        if any_admin_exists():
            role_values = ["operator"]
        else:
            role_values = ["operator", "admin"]
        self.combo_role = ttk.Combobox(row_role, values=role_values, state="readonly")
        self.combo_role.set("operator")
        self.combo_role.pack(side=tk.LEFT, expand=True, fill=tk.X)

        btn_register = ttk.Button(frame, text="登録", command=self.register)
        btn_register.pack(pady=(20, 5))

        ttk.Button(frame, text="閉じる", command=self.destroy).pack()

    def register(self):
        worker_id = self.entry_worker_id.get().strip()
        name = self.entry_name.get().strip()
        role = self.combo_role.get().strip() or "operator"

        if not worker_id:
            messagebox.showwarning("エラー", "IDを入力してください。", parent=self.winfo_toplevel())
            return
        if len(worker_id) > WORKER_ID_MAX_LENGTH:
            messagebox.showwarning(
                "エラー", f"IDは{WORKER_ID_MAX_LENGTH}文字以内で入力してください。",
                parent=self.winfo_toplevel(),
            )
            return
        if is_worker_id_taken(worker_id):
            messagebox.showwarning(
                "エラー",
                f"ID「{worker_id}」は既に使われています（全角/半角・大文字/小文字の"
                "違いのみの場合も含みます）。別のIDを入力してください。",
                parent=self.winfo_toplevel(),
            )
            return
        if not name:
            messagebox.showwarning("エラー", "氏名を入力してください。", parent=self.winfo_toplevel())
            return

        # 新規登録のため、既存IDへの上書きを許すupsert_worker()ではなく
        # create_worker()（単純なINSERT）を使う（2026-10-06変更。重複チェック
        # は上で既に行っているが、念のため新規登録という操作の意味に即した
        # 関数を使う）。
        create_worker(worker_id, name, role)

        # ログイン前（current_workerが空）に登録した場合、操作者名として
        # 記録しようが無いため"unknown"のままになる（既存のmain_window.py等の
        # 他の操作履歴記録と同じ扱い）。
        from models.operation_log import log_operation
        log_operation(
            self.current_worker.get("name", "unknown"),
            "作業者登録",
            detail=f"{worker_id}（{name}、{role}）",
        )

        messagebox.showinfo(
            "完了", f"作業者「{name}」（役割: {role}）を登録しました。", parent=self.winfo_toplevel(),
        )
        if self.on_registered is not None:
            self.on_registered()
        self.destroy()
