# ui/worker_management_window.py
import tkinter as tk
from tkinter import ttk, messagebox

from models.workers import get_all_workers, upsert_worker, set_worker_active


class WorkerManagementWindow(tk.Toplevel):
    """
    ログイン作業者（workers）の登録・編集・有効/無効切り替えを行う画面。

    production_daily.worker_id・audit_log.worker_id 等、過去実績から
    作業者IDが参照される可能性があるため、削除（DELETE）は提供せず、
    is_active フラグによる無効化のみをサポートする
    （無効化した作業者はログイン画面の一覧から外れるが、履歴の参照は壊れない）。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.title("作業者管理")
        self.geometry("640x520")

        frame_input = ttk.LabelFrame(self, text="作業者情報の登録・編集", padding=10)
        frame_input.pack(fill=tk.X, padx=10, pady=10)

        ttk.Label(frame_input, text="作業者ID:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.entry_worker_id = ttk.Entry(frame_input, width=15)
        self.entry_worker_id.grid(row=0, column=1, sticky=tk.W, padx=5, pady=3)

        ttk.Label(frame_input, text="氏名:").grid(row=0, column=2, sticky=tk.W, pady=3)
        self.entry_name = ttk.Entry(frame_input, width=20)
        self.entry_name.grid(row=0, column=3, sticky=tk.W, padx=5, pady=3)

        ttk.Label(frame_input, text="役割:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.combo_role = ttk.Combobox(frame_input, width=13, values=["operator", "admin"])
        self.combo_role.set("operator")
        self.combo_role.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)

        self.var_active = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame_input, text="有効", variable=self.var_active).grid(
            row=1, column=2, sticky=tk.W, padx=5, pady=3
        )

        btn_save = ttk.Button(frame_input, text="保存 / 更新", command=self.save_worker)
        btn_save.grid(row=1, column=3, sticky=tk.E, padx=10)

        btn_clear = ttk.Button(frame_input, text="入力欄クリア（新規登録）", command=self.clear_form)
        btn_clear.grid(row=0, column=4, padx=10)

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
        self.btn_toggle_active = ttk.Button(
            btn_frame, text="選択行の有効/無効を切り替え", command=self.toggle_active, state=tk.DISABLED
        )
        self.btn_toggle_active.pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="閉じる", command=self.destroy).pack(side=tk.RIGHT, padx=5)

        self.load_workers()

    def load_workers(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for w in get_all_workers():
            status = "有効" if w["is_active"] else "無効"
            self.tree.insert("", tk.END, values=(w["worker_id"], w["name"], w["role"], status))

    def on_select(self, event):
        sel = self.tree.selection()
        if not sel:
            self.btn_toggle_active.config(state=tk.DISABLED)
            return
        values = self.tree.item(sel[0], "values")
        self.entry_worker_id.delete(0, tk.END)
        self.entry_worker_id.insert(0, values[0])
        self.entry_name.delete(0, tk.END)
        self.entry_name.insert(0, values[1])
        self.combo_role.set(values[2])
        self.var_active.set(values[3] == "有効")
        self.btn_toggle_active.config(state=tk.NORMAL)

    def clear_form(self):
        self.entry_worker_id.delete(0, tk.END)
        self.entry_name.delete(0, tk.END)
        self.combo_role.set("operator")
        self.var_active.set(True)
        for iid in self.tree.selection():
            self.tree.selection_remove(iid)
        self.btn_toggle_active.config(state=tk.DISABLED)

    def save_worker(self):
        worker_id = self.entry_worker_id.get().strip()
        name = self.entry_name.get().strip()
        role = self.combo_role.get().strip() or "operator"

        if not worker_id:
            messagebox.showwarning("エラー", "作業者IDを入力してください。", parent=self.winfo_toplevel())
            return
        if not name:
            messagebox.showwarning("エラー", "氏名を入力してください。", parent=self.winfo_toplevel())
            return

        is_active = self.var_active.get()
        upsert_worker(worker_id, name, role, is_active)
        self.load_workers()
        messagebox.showinfo("完了", f"作業者「{name}」を保存しました。", parent=self.winfo_toplevel())

    def toggle_active(self):
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
        self.load_workers()
        self.var_active.set(new_active)
        messagebox.showinfo("完了", f"作業者「{name}」を{action_label}しました。", parent=self.winfo_toplevel())
