import tkinter as tk
from tkinter import ttk
from ui.production_entry import ProductionEntryWindow
from ui.snapshot_import import SnapshotImportWindow
from ui.physical_count import PhysicalCountWindow
from ui.master_management import MasterManagementWindow
from ui.reconciliation import ReconciliationWindow
from ui.kitting_plan_import import KittingPlanImportWindow
from ui.kitting_production_entry import KittingProductionEntryWindow


class MainWindow(tk.Tk):
    def __init__(self, current_worker):
        super().__init__()
        self.current_worker = current_worker
        self.title("部品在庫管理アプリ - メインメニュー")
        self.geometry("600x600")

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

        btn_production = ttk.Button(body_frame, text="1. 日次生産入力（旧）", command=self.open_production_entry)
        btn_production.pack(fill=tk.X, pady=5)

        btn_snapshot = ttk.Button(body_frame, text="2. 在庫スナップショット取込", command=self.open_snapshot_import)
        btn_snapshot.pack(fill=tk.X, pady=5)

        btn_count = ttk.Button(body_frame, text="3. バーコード実地カウント（棚卸）", command=self.open_physical_count)
        btn_count.pack(fill=tk.X, pady=5)

        btn_master = ttk.Button(body_frame, text="4. マスターデータ管理", command=self.open_master_management)
        btn_master.pack(fill=tk.X, pady=5)

        btn_reconcile = ttk.Button(body_frame, text="5. 在庫照合・棚卸差分集計", command=self.open_reconciliation)
        btn_reconcile.pack(fill=tk.X, pady=5)

        ttk.Separator(body_frame, orient="horizontal").pack(fill=tk.X, pady=15)

        btn_kitting_import = ttk.Button(
            body_frame, text="6. キッティング計画CSV取込", command=self.open_kitting_plan_import
        )
        btn_kitting_import.pack(fill=tk.X, pady=5)

        btn_kitting_production = ttk.Button(
            body_frame, text="7. 生産実績入力（キッティングリストNo.）", command=self.open_kitting_production_entry
        )
        btn_kitting_production.pack(fill=tk.X, pady=5)

    def open_production_entry(self):
        ProductionEntryWindow(self, self.current_worker)

    def open_snapshot_import(self):
        SnapshotImportWindow(self, self.current_worker)

    def open_physical_count(self):
        PhysicalCountWindow(self, self.current_worker)

    def open_master_management(self):
        MasterManagementWindow(self, self.current_worker)

    def open_reconciliation(self):
        ReconciliationWindow(self, self.current_worker)

    def open_kitting_plan_import(self):
        KittingPlanImportWindow(self, self.current_worker)

    def open_kitting_production_entry(self):
        KittingProductionEntryWindow(self, self.current_worker)