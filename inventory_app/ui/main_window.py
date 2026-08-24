import os
import tkinter as tk
from tkinter import ttk, messagebox
import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables
from ui.master_management import MasterManagementWindow
from ui.kitting_plan_import import KittingPlanImportWindow
from ui.kitting_production_entry import KittingProductionEntryWindow
from ui.loading_window import LoadingWindow
from ui.inventory_input_window import InventoryInputWindow
from ui.theoretical_inventory_import_window import TheoreticalInventoryImportWindow
from ui.inventory_diff_window import InventoryDiffWindow
from ui.master_import_window import MasterImportWindow
from ui.ng_input_window import NgInputWindow


class MainWindow(tk.Tk):
    def __init__(self, current_worker):
        super().__init__()
        self.current_worker = current_worker
        self.title("部品在庫管理アプリ - メインメニュー")
        self.geometry("600x660")

        # データベース選択領域（最上部）
        db_select_frame = ttk.Labelframe(self, text="データベース選択", padding=10)
        db_select_frame.pack(fill=tk.X, padx=10, pady=(10, 0))

        self.db_folder_var = tk.StringVar()
        self.db_folder_combobox = ttk.Combobox(
            db_select_frame, textvariable=self.db_folder_var, state="readonly", width=30
        )
        self.db_folder_combobox.pack(side=tk.LEFT, padx=(0, 10))
        self._load_db_folders()

        ttk.Button(db_select_frame, text="切り替え", command=self.on_switch_database).pack(side=tk.LEFT)

        ttk.Separator(db_select_frame, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Label(db_select_frame, text="新規フォルダ名：").pack(side=tk.LEFT)
        self.new_db_folder_var = tk.StringVar()
        self.entry_new_db_folder = ttk.Entry(db_select_frame, textvariable=self.new_db_folder_var, width=15)
        self.entry_new_db_folder.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(db_select_frame, text="新しいデータベースを作成", command=self.on_create_database).pack(side=tk.LEFT)

        # ヘッダー領域
        header_frame = ttk.Frame(self, padding=10)
        header_frame.pack(fill=tk.X)

        worker_name = current_worker.get('name', '未設定')
        worker_role = current_worker.get('role', 'operator')
        ttk.Label(
            header_frame,
            text=f"ログイン作業者: {worker_name} ({worker_role})",
            font=("Helvetica", 11, "bold")
        ).pack(side=tk.LEFT)

        # メニューボタン領域
        body_frame = ttk.Frame(self, padding=20)
        body_frame.pack(expand=True, fill=tk.BOTH)

        ttk.Label(body_frame, text="操作メニューを選択してください", font=("Helvetica", 12)).pack(pady=10)

        btn_master = ttk.Button(body_frame, text="4. マスターデータ管理", command=self.open_master_management)
        btn_master.pack(fill=tk.X, pady=5)

        ttk.Separator(body_frame, orient="horizontal").pack(fill=tk.X, pady=15)

        btn_kitting_import = ttk.Button(
            body_frame, text="6. キッティング計画CSV取込", command=self.open_kitting_plan_import
        )
        btn_kitting_import.pack(fill=tk.X, pady=5)

        btn_kitting_production = ttk.Button(
            body_frame, text="7. 生産実績入力（キッティングリストNo.）", command=self.open_kitting_production_entry
        )
        btn_kitting_production.pack(fill=tk.X, pady=5)

        btn_inventory_input = ttk.Button(
            body_frame, text="8. 96部品在庫入力", command=self.open_inventory_input
        )
        btn_inventory_input.pack(fill=tk.X, pady=5)

        btn_theoretical_import = ttk.Button(
            body_frame, text="9. 理論在庫インポート", command=self.open_theoretical_inventory_import
        )
        btn_theoretical_import.pack(fill=tk.X, pady=5)

        btn_inventory_diff = ttk.Button(
            body_frame, text="10. 在庫差異レポート", command=self.open_inventory_diff
        )
        btn_inventory_diff.pack(fill=tk.X, pady=5)

        btn_master_import = ttk.Button(
            body_frame, text="11. マスタインポート", command=self.open_master_import
        )
        btn_master_import.pack(fill=tk.X, pady=5)

        btn_ng_input = ttk.Button(
            body_frame, text="12. NG（仕損）入力", command=self.open_ng_input
        )
        btn_ng_input.pack(fill=tk.X, pady=5)

    def open_master_management(self):
        MasterManagementWindow(self, self.current_worker)

    def open_kitting_plan_import(self):
        KittingPlanImportWindow(self, self.current_worker)

    def open_kitting_production_entry(self):
        loading = LoadingWindow(self)

        def _load():
            loading.destroy()
            KittingProductionEntryWindow(self, self.current_worker)

        self.after(100, _load)

    def open_inventory_input(self):
        InventoryInputWindow(self)

    def open_theoretical_inventory_import(self):
        TheoreticalInventoryImportWindow(self)

    def open_inventory_diff(self):
        InventoryDiffWindow(self)

    def open_master_import(self):
        MasterImportWindow(self)

    def open_ng_input(self):
        NgInputWindow(self, self.current_worker)

    def _load_db_folders(self):
        db_root = os.path.join(config.BASE_DIR, "db")
        folders = []
        if os.path.isdir(db_root):
            folders = sorted(
                name for name in os.listdir(db_root)
                if os.path.isfile(os.path.join(db_root, name, "inventory.db"))
            )
        self.db_folder_combobox["values"] = folders
        if folders:
            self.db_folder_combobox.current(0)

    def on_switch_database(self):
        folder = self.db_folder_var.get().strip()
        if not folder:
            messagebox.showwarning("警告", "切り替え先のフォルダを選択してください。")
            return

        config.DB_PATH = os.path.join(config.BASE_DIR, "db", folder, "inventory.db")
        messagebox.showinfo("完了", "データベースを切り替えました。")

    def on_create_database(self):
        folder = self.new_db_folder_var.get().strip()
        if not folder:
            messagebox.showwarning("警告", "作成するフォルダ名を入力してください。")
            return

        new_db_path = os.path.join(config.BASE_DIR, "db", folder, "inventory.db")
        if os.path.exists(new_db_path):
            messagebox.showwarning("警告", f"フォルダ「{folder}」のデータベースは既に存在します。")
            return

        init_database_at(new_db_path)
        config.DB_PATH = new_db_path
        init_kitting_plan_tables()

        messagebox.showinfo("完了", "新しいデータベースを作成しました。")
        self._load_db_folders()
        self.db_folder_var.set(folder)
        self.new_db_folder_var.set("")