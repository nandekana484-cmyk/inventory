import os
import queue
import shutil
import socket
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import tkinter.font as tkfont
from datetime import datetime
import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables
from services.db_lock_service import (
    acquire_lock, release_lock, update_heartbeat, get_lock_info, LockFileCorruptedError,
)
from ui.kitting_plan_import import KittingPlanImportWindow
from ui.kitting_production_entry import KittingProductionEntryWindow
from ui.loading_window import LoadingWindow
from ui.inventory_input_window import InventoryInputWindow
from ui.theoretical_inventory_import_window import TheoreticalInventoryImportWindow
from ui.inventory_diff_window import InventoryDiffWindow
from ui.ng_input_window import NgInputWindow
from ui.parts_attributes_import_window import PartsAttributesImportWindow
from ui.board_structure_import_window import BoardStructureImportWindow
from ui.production_side_master_window import ProductionSideMasterWindow
from ui.wip_expansion_window import WipExpansionWindow
from ui.worker_management_window import WorkerManagementWindow
from ui.worker_registration_window import WorkerRegistrationWindow
from ui.operation_log_window import OperationLogWindow
from models.operation_log import log_operation, get_inventory_diff_export_status
from models.db_lifecycle_log import record_db_lifecycle_event, OPERATION_TYPE_CREATE, OPERATION_TYPE_CREATE_WITH_CARRY_OVER, OPERATION_TYPE_RESTORE_FROM_BACKUP
from ui.db_delete_helper import confirm_and_delete_database
from services.db_migration_carryover import carry_over_incomplete_lots
from services.backup_service import (
    backup_databases, validate_monthly_db_backup, restore_backup_as_new_local_db,
)
from services.master_merge_service import merge_master_from_backup
from services.unprocessed_check_service import check_unprocessed_items
from services.app_settings_service import load_last_db_path
from ui.window_utils import center_window
from version import get_version_label


class MainWindow(tk.Tk):
    def __init__(self, current_worker):
        super().__init__()
        self.current_worker = current_worker
        self._pc_name = socket.gethostname()
        self._worker_name = current_worker.get("name", "unknown")
        self._lock_acquired = False
        # 前回の DB の復元に失敗したときの通知は、UI を作り終えてから出す（途中でダイアログが割り込まないように）。
        self._restore_last_db_failed_path = None

        # 前回選んでいた DB があれば切り替える。無ければ既定DB のまま。記憶したパスが今は無ければ、
        # 既定DB で起動し、UI を作り終えてから知らせる。
        last_db_path = load_last_db_path()
        if last_db_path and last_db_path != config.DB_PATH:
            if os.path.exists(last_db_path):
                config.set_db_path(last_db_path)
            else:
                self._restore_last_db_failed_path = last_db_path

        # 既定DB（未選択）の間は、ロックファイルにも一切触れない（D-56・D-57 の「既定DB は読み書きしない」を .lock にも広げた）。
        # 既定DB 以外でロックを取れなければ、ほかの PC・利用者が使用中とみなして起動を中断する
        # （LoginWindow は winfo_exists() を見てから mainloop() する）。
        if not config.is_default_db():
            if not self._acquire_lock_with_corruption_handling(config.DB_PATH):
                messagebox.showerror(
                    "データベース使用中",
                    "このデータベースは他の利用者が使用中のため起動できません。\n\n"
                    + self._format_lock_info(get_lock_info(config.DB_PATH)),
                    parent=self,
                )
                self.destroy()
                return
            self._lock_acquired = True

        self.title(f"部品在庫管理アプリ - メインメニュー {get_version_label()}")
        # 幅1020: 「データベース選択」行の必要幅（実測986px）に余白を足した値。高さ620: admin 表示の必要高さが599pxまで迫ったため、余裕を持たせた。
        # 実測値と経緯は docs/domain/main_menu.md。
        self.geometry("1020x620")
        center_window(self)

        # 多重表示の防止用（key -> 開いている Toplevel）。閉じると、_open_singleton_window() が設定したハンドラでエントリを消す。
        self._open_windows = {}
        # KittingProductionEntryWindowは非同期（別スレッドでのデータ事前取得）で開くため、
        # 生成完了までの間に連打された場合に二重にスレッドを起こさないためのガード。
        self._kitting_entry_loading = False

        # 過去に topmost=True が設定されていた場合の後遺症を防ぐため明示的に無効化する。
        # main_window（root）はフォーカス制御（lift/focus_force/grab_set等）を一切行わない。
        self.attributes("-topmost", False)

        # データベース選択領域（最上部）。ローカルの db/ フォルダから選択・新規作成する。
        db_select_frame = ttk.Labelframe(self, text="データベース選択", padding=10)
        db_select_frame.pack(fill=tk.X, padx=10, pady=(10, 0))

        db_select_row1 = ttk.Frame(db_select_frame)
        db_select_row1.pack(fill=tk.X)

        self.db_folder_var = tk.StringVar()
        self.db_folder_combobox = ttk.Combobox(
            db_select_row1, textvariable=self.db_folder_var, state="readonly", width=30
        )
        self.db_folder_combobox.pack(side=tk.LEFT, padx=(0, 10))
        self._load_db_folders()

        self.btn_switch_database = ttk.Button(db_select_row1, text="切り替え", command=self.on_switch_database)
        self.btn_switch_database.pack(side=tk.LEFT)

        self.btn_delete_local_database = ttk.Button(
            db_select_row1, text="削除", command=self.on_delete_local_database,
        )
        self.btn_delete_local_database.pack(side=tk.LEFT, padx=(5, 0))

        ttk.Separator(db_select_row1, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Label(db_select_row1, text="新規フォルダ名：").pack(side=tk.LEFT)
        self.new_db_folder_var = tk.StringVar()
        self.entry_new_db_folder = ttk.Entry(db_select_row1, textvariable=self.new_db_folder_var, width=15)
        self.entry_new_db_folder.pack(side=tk.LEFT, padx=(0, 10))

        self.carry_over_var = tk.BooleanVar(value=False)
        self.chk_carry_over = ttk.Checkbutton(
            db_select_row1, text="前月(現在のDB)から未完了分を引き継ぐ", variable=self.carry_over_var,
        )
        self.chk_carry_over.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_create_database = ttk.Button(
            db_select_row1, text="新しいデータベースを作成", command=self.on_create_database,
        )
        self.btn_create_database.pack(side=tk.LEFT)

        # on_create_database()の引き継ぎ処理（非同期）用
        self._create_db_result_queue = queue.Queue()
        self._create_db_loading_window = None

        # on_backup_databases() 用（同じ非同期パターン）
        self._backup_result_queue = queue.Queue()
        self._backup_loading_window = None
        self._backup_destination_folder = None

        # on_merge_master_from_backup() 用（同じ非同期パターン）
        self._merge_master_result_queue = queue.Queue()
        self._merge_master_loading_window = None

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

        # 月次DB とマスタDB を同時にバックアップする。
        self.btn_backup_databases = ttk.Button(
            header_frame, text="バックアップ", command=self.on_backup_databases,
        )
        self.btn_backup_databases.pack(side=tk.RIGHT, padx=(0, 10))

        # バックアップの月次DB を新しいローカルフォルダへコピーしてから切り替える（原本も今の DB も変えない。D-50）。
        # 「バックアップ」ボタンの直後に pack して、side=RIGHT で隣に並べる。
        self.btn_restore_from_backup = ttk.Button(
            header_frame, text="バックアップの呼び出し", command=self.on_restore_from_backup,
        )
        self.btn_restore_from_backup.pack(side=tk.RIGHT, padx=(0, 10))

        # マスタDB のバックアップから足りないレコードを取り込む。workers（ログインできる作業者と役割）も変わるので admin にだけ表示する
        # （operator ではボタンを作らないので None のまま。_menu_widgets への追加もこれに合わせる）。
        self.btn_merge_master = None
        if current_worker.get("role") == "admin":
            self.btn_merge_master = ttk.Button(
                header_frame, text="マスタデータを他PCから取り込む", command=self.on_merge_master_from_backup,
            )
            self.btn_merge_master.pack(side=tk.RIGHT, padx=(0, 10))

        # 接続中の DB のパスを常に表示する（DB を変える操作のたびに _update_current_db_label() で更新）。
        # 長いパスでウィンドウが広がらないよう、中央を省略して表示する（フルパスは _current_db_full_path）。
        db_path_frame = ttk.Frame(self, padding=(10, 0, 10, 8))
        db_path_frame.pack(fill=tk.X)
        ttk.Label(db_path_frame, text="接続中のデータベース：", font=("Helvetica", 9)).pack(side=tk.LEFT)
        self._current_db_full_path = ""
        self.lbl_current_db_path = ttk.Label(db_path_frame, text="-", font=("Helvetica", 9), foreground="#333333")
        self.lbl_current_db_path.pack(side=tk.LEFT)

        # 在庫値出力済みの DB なら、パスの直下に警告を出す（D-5x。警告だけで、入力は妨げない）。
        self.lbl_inventory_diff_export_warning = ttk.Label(
            self, text="", font=("Helvetica", 9, "bold"), foreground="#b00020",
        )
        # _update_current_db_label() の初回の呼び出しは、ボタン一覧（_menu_widgets など）を作った後にすること（_apply_widget_states() が参照する）。

        # メニューボタン領域。左＝共通マスタ（master.db。DB を切り替えても変わらない）、右＝月次データ（config.DB_PATH）。
        # 左の「ツール」（PDF読み取り・操作履歴）は月次DB を使うが、使用頻度が低いのでここに置いた例外（D-50）。
        body_frame = ttk.Frame(self, padding=20)
        body_frame.pack(expand=True, fill=tk.BOTH)
        # 在庫値出力済みの警告ラベルを pack(before=...) で入れる位置の基準。表示・非表示を繰り返しても、パスの直下に固定される。
        self._body_frame = body_frame

        ttk.Label(body_frame, text="操作メニューを選択してください", font=("Helvetica", 12)).pack(pady=(0, 10))

        columns_frame = ttk.Frame(body_frame)
        columns_frame.pack(fill=tk.BOTH, expand=True)

        master_frame = ttk.Frame(columns_frame)
        master_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        ttk.Separator(columns_frame, orient="vertical").pack(side=tk.LEFT, fill=tk.Y)

        monthly_frame = ttk.Frame(columns_frame)
        monthly_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))

        ttk.Label(monthly_frame, text="月次データ", font=("Helvetica", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))

        btn_kitting_import = ttk.Button(
            monthly_frame, text="1. 生産計画読込", command=self.open_kitting_plan_import
        )
        btn_kitting_import.pack(fill=tk.X, pady=5)

        btn_kitting_production = ttk.Button(
            monthly_frame, text="2. 生産実績入力", command=self.open_kitting_production_entry
        )
        btn_kitting_production.pack(fill=tk.X, pady=5)

        btn_ng_input = ttk.Button(
            monthly_frame, text="3. NG・仕損展開", command=self.open_ng_input
        )
        btn_ng_input.pack(fill=tk.X, pady=5)

        btn_wip_expansion = ttk.Button(
            monthly_frame, text="4. 仕掛部品展開", command=self.open_wip_expansion
        )
        btn_wip_expansion.pack(fill=tk.X, pady=5)

        btn_inventory_input = ttk.Button(
            monthly_frame, text="5. 在庫値入力", command=self.open_inventory_input
        )
        btn_inventory_input.pack(fill=tk.X, pady=5)

        btn_theoretical_import = ttk.Button(
            monthly_frame, text="6. 理論値入力", command=self.open_theoretical_inventory_import
        )
        btn_theoretical_import.pack(fill=tk.X, pady=5)

        btn_inventory_diff = ttk.Button(
            monthly_frame, text="7. 在庫値出力", command=self.open_inventory_diff
        )
        btn_inventory_diff.pack(fill=tk.X, pady=5)

        ttk.Label(master_frame, text="共通マスタ", font=("Helvetica", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))

        btn_board_structure_import = ttk.Button(
            master_frame, text="1. 構成基板数マスター", command=self.open_board_structure_import
        )
        btn_board_structure_import.pack(fill=tk.X, pady=5)

        btn_parts_attributes_import = ttk.Button(
            master_frame, text="2. 基板丁数マスター", command=self.open_parts_attributes_import
        )
        btn_parts_attributes_import.pack(fill=tk.X, pady=5)

        # 生産面マスター（D-9x）。表示条件と権限は、構成基板数マスター・基板丁数マスターと同じ（ロールを問わず表示）。
        btn_production_side_master = ttk.Button(
            master_frame, text="3. 生産面マスター", command=self.open_production_side_master
        )
        btn_production_side_master.pack(fill=tk.X, pady=5)

        # 新規アカウント登録（ロールを問わず表示）。ログイン画面と同じ画面を、ログイン後にも開けるようにする。
        btn_worker_registration = ttk.Button(
            master_frame, text="4. 新規アカウント登録", command=self.open_worker_registration
        )
        btn_worker_registration.pack(fill=tk.X, pady=5)

        # アカウント管理は admin にだけ表示する（operator ではボタンを作らないので None のまま）。
        self.btn_worker_management = None
        if current_worker.get("role") == "admin":
            self.btn_worker_management = ttk.Button(
                master_frame, text="5. アカウント管理", command=self.open_worker_management
            )
            self.btn_worker_management.pack(fill=tk.X, pady=5)

        # 「ツール」見出し（D-50）。番号は付けない。月次DB を使うが、配置上の都合でここに置いている。
        ttk.Label(master_frame, text="ツール", font=("Helvetica", 11, "bold")).pack(anchor=tk.W, pady=(15, 5))

        btn_pdf_ocr_import = ttk.Button(
            master_frame, text="PDF読み取り（在庫照合）", command=self.open_pdf_ocr_import
        )
        btn_pdf_ocr_import.pack(fill=tk.X, pady=5)
        # 常に無効にする（D-8x）。機能のコードは残す。_menu_widgets にも _default_db_locked_widgets にも入れないので、DISABLED が保たれる。
        btn_pdf_ocr_import.config(state=tk.DISABLED)

        btn_operation_log = ttk.Button(
            master_frame, text="操作履歴", command=self.open_operation_log
        )
        btn_operation_log.pack(fill=tk.X, pady=5)

        ttk.Separator(body_frame, orient="horizontal").pack(fill=tk.X, pady=15)

        # ログアウトは誤操作を避けたいので、全幅にせず半分程度の幅で中央に置く。
        btn_logout = ttk.Button(body_frame, text="ログアウト", command=self.on_logout, width=35)
        btn_logout.pack(pady=5)

        # メニュー全体を一括で無効にする対象（_set_menu_enabled()）。前月引き継ぎの実行中は config.DB_PATH が一時的に入れ替わるので、
        # ほかの画面を開いたり DB を切り替えたりさせない。chk_carry_over は _apply_widget_states() で個別に扱うので入れない。
        self._menu_widgets = [
            self.db_folder_combobox, self.btn_switch_database, self.entry_new_db_folder,
            self.btn_create_database,
            self.btn_backup_databases, self.btn_restore_from_backup,
            btn_kitting_import, btn_kitting_production, btn_inventory_input,
            btn_theoretical_import, btn_inventory_diff, btn_ng_input, btn_wip_expansion,
            btn_operation_log,
            btn_parts_attributes_import, btn_production_side_master,
            btn_worker_registration, btn_board_structure_import, btn_logout,
        ]
        # btn_pdf_ocr_import は入れない（常に無効）。admin のときだけ作るボタンは、あるときだけ追加する。
        if self.btn_worker_management is not None:
            self._menu_widgets.append(self.btn_worker_management)
        if self.btn_merge_master is not None:
            self._menu_widgets.append(self.btn_merge_master)

        # 既定DB（未選択）の間に無効にする業務ボタン（D-5x）。DB の選択・新規作成・削除・バックアップの呼び出し・ログアウトは入れない。
        # ここに入れたものは、必ず _menu_widgets にも入れること（_apply_widget_states() が両方を合成するため）。
        self._default_db_locked_widgets = [
            btn_kitting_import, btn_kitting_production, btn_inventory_input,
            btn_theoretical_import, btn_inventory_diff, btn_ng_input, btn_wip_expansion,
            btn_operation_log,
            btn_board_structure_import, btn_parts_attributes_import, btn_production_side_master,
            btn_worker_registration,
            self.btn_backup_databases,
        ]
        if self.btn_worker_management is not None:
            self._default_db_locked_widgets.append(self.btn_worker_management)
        if self.btn_merge_master is not None:
            self._default_db_locked_widgets.append(self.btn_merge_master)

        # 一括無効化のフラグ。_apply_widget_states() が、これと既定DB かどうかを合成して state を決める。
        self._menu_enabled = True
        self._apply_widget_states()
        self._update_current_db_label()

        # ×ボタンなどで閉じるときにロックを解放する。on_logout() はこのハンドラを通らないので、そちらでも個別に解放すること。
        self.protocol("WM_DELETE_WINDOW", self._on_app_close)
        # 5分間隔でロックファイルの最終更新時刻を更新し（生存確認）、
        # LOCK_STALE_SECONDS（30分）以上更新が無いロックとして自動解除されるのを防ぐ。
        self.after(300000, self._heartbeat)

        if self._restore_last_db_failed_path:
            messagebox.showwarning(
                "データベース接続の復元に失敗",
                "前回使用していたデータベースが見つかりませんでした：\n"
                f"{self._restore_last_db_failed_path}\n\n"
                "デフォルトのローカルデータベースに接続しました。",
                parent=self,
            )

    def _acquire_lock_with_corruption_handling(self, db_path: str) -> bool:
        """
        acquire_lock() のラッパー。ロックファイルが壊れていたら自動では上書きせず、
        使用中でないかを利用者に確認し、「はい」のときだけ強制的に取得する（他者のロックを奪わないため）。
        """
        try:
            return acquire_lock(db_path, self._worker_name, self._pc_name)
        except LockFileCorruptedError:
            force = messagebox.askyesno(
                "ロックファイル異常",
                "ロックファイルの状態が不正です（内容を正しく読み取れません）。\n"
                "他の利用者が本当に使用中でないか、必ず確認してから続行してください。\n\n"
                "使用中でないことを確認した上で、強制的にロックを取得しますか？",
                icon="warning",
                parent=self.winfo_toplevel(),
            )
            if not force:
                return False
            return acquire_lock(db_path, self._worker_name, self._pc_name, force=True)

    @staticmethod
    def _format_lock_info(info) -> str:
        if not info:
            return "（ロック保持者の情報を取得できませんでした）"
        return (
            f"作業者：{info.get('worker_name')}\n"
            f"PC：{info.get('pc_name')}\n"
            f"取得時刻：{info.get('acquired_at')}\n"
            f"最終更新時刻：{info.get('last_updated')}"
        )

    def _heartbeat(self):
        if not self.winfo_exists():
            return
        if self._lock_acquired:
            update_heartbeat(config.DB_PATH)
        self.after(300000, self._heartbeat)

    def _release_current_lock(self):
        if self._lock_acquired:
            release_lock(config.DB_PATH)
            self._lock_acquired = False

    def _on_app_close(self):
        """ウィンドウの×ボタン・Alt+F4等での終了時、ロックを解放してから閉じる。"""
        self._release_current_lock()
        self.destroy()

    def _set_menu_enabled(self, enabled: bool):
        """
        メニュー全体の操作可否を一括で切り替える（前月引き継ぎの実行中に使う）。state の設定は _apply_widget_states() に任せる。
        既定DB による無効化とは別のフラグで持つ（一方の解除で、もう一方の無効状態を外さないため。D-5x）。
        """
        self._menu_enabled = enabled
        self._apply_widget_states()

    def _apply_widget_states(self):
        """
        一括無効化（_menu_widgets）と既定DB による無効化（_default_db_locked_widgets）を合成して、各ウィジェットの state を決める。
        どちらも「True なら制限なし」で持つので、AND するだけで「どちらかが無効なら無効」になる。
        db_folder_combobox は readonly/disabled で切り替える（NORMAL にすると自由入力になる）。
        chk_carry_over は、既定DB の間は使えなくし、チェックも外す（既定DB を引き継ぎ元にしないため）。
        """
        default_db_open = config.is_default_db()

        for widget in self._menu_widgets:
            locked_by_default_db = default_db_open and widget in self._default_db_locked_widgets
            enabled = self._menu_enabled and not locked_by_default_db
            if widget is self.db_folder_combobox:
                widget.config(state="readonly" if enabled else "disabled")
            else:
                widget.config(state=tk.NORMAL if enabled else tk.DISABLED)

        carry_over_enabled = self._menu_enabled and not default_db_open
        self.chk_carry_over.config(state=tk.NORMAL if carry_over_enabled else tk.DISABLED)
        if not carry_over_enabled:
            self.carry_over_var.set(False)

    def _open_singleton_window(self, key, factory):
        """
        多重表示を防いで画面を開く。開いていれば前面に出すだけ。閉じたときに _open_windows から外すハンドラを、外から設定する。
        """
        existing = self._open_windows.get(key)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return existing

        window = factory()
        self._open_windows[key] = window

        def _on_close(w=window, k=key):
            self._open_windows.pop(k, None)
            w.destroy()

        window.protocol("WM_DELETE_WINDOW", _on_close)
        return window

    def _has_open_child_windows(self) -> bool:
        """
        _open_singleton_window() で開いた画面が1つでも残っているか（引き継ぎの確認ダイアログ用）。
        生産実績入力画面は生成が終わってから登録されるので、読み込み中は「開いている」扱いにならない。
        """
        return any(w.winfo_exists() for w in self._open_windows.values())

    def _confirm_proceed_despite_exported_db(self) -> bool:
        """
        在庫値出力済みの DB なら、月次データに書き込む画面（1〜6）を開く前に確認する（D-5x）。False なら開かずに戻ること。
        既定DB には get_inventory_diff_export_status() を呼ばない（呼ぶと DB ファイルが作られてしまう）。
        """
        if config.is_default_db():
            return True
        status = get_inventory_diff_export_status()
        if not status["exported"]:
            return True
        return messagebox.askyesno(
            "出力済みデータベース",
            "このDBは在庫値出力済みです。入力した内容はこの古いDBに保存されます。\n"
            "続けますか？",
            parent=self,
        )

    def open_kitting_plan_import(self):
        if not self._confirm_proceed_despite_exported_db():
            return
        self._open_singleton_window(
            "kitting_plan_import", lambda: KittingPlanImportWindow(self, self.current_worker)
        )

    def open_kitting_production_entry(self, on_ready=None):
        """
        生産実績入力画面を開く。計画一覧の取得は重いので別スレッドで行い、終わってから UI スレッドで画面を作る。
        既に開いていれば、前面に出してデータだけ更新する。読み込み中に再度呼ばれたら、スレッドを二重に起こさない。
        on_ready: 画面の用意ができたときに呼ぶコールバック（現在は使う呼び出し元が無い）。
        """
        if not self._confirm_proceed_despite_exported_db():
            return
        key = "kitting_production_entry"
        existing = self._open_windows.get(key)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            existing.load_plan_list()
            if on_ready is not None:
                on_ready(existing)
            return

        if self._kitting_entry_loading:
            return
        self._kitting_entry_loading = True

        loading = LoadingWindow(self)
        result_queue = queue.Queue()

        def _fetch_in_thread():
            try:
                rows = KittingProductionEntryWindow._fetch_plan_list_rows()
                result_queue.put((True, rows))
            except Exception as e:
                result_queue.put((False, e))

        threading.Thread(target=_fetch_in_thread, daemon=True).start()

        def _poll():
            try:
                success, payload = result_queue.get_nowait()
            except queue.Empty:
                self.after(200, _poll)
                return

            self._kitting_entry_loading = False
            loading.destroy()
            if not success:
                messagebox.showerror(
                    "エラー", f"生産実績入力画面の読み込みに失敗しました：\n{payload}",
                    parent=self,
                )
                return

            window = KittingProductionEntryWindow(self, self.current_worker, preloaded_plan_rows=payload)
            self._open_windows[key] = window

            def _on_close(w=window):
                self._open_windows.pop(key, None)
                w.destroy()

            window.protocol("WM_DELETE_WINDOW", _on_close)
            if on_ready is not None:
                on_ready(window)

        self.after(200, _poll)

    def open_inventory_input(self):
        if not self._confirm_proceed_despite_exported_db():
            return
        self._open_singleton_window(
            "inventory_input", lambda: InventoryInputWindow(self, current_worker=self.current_worker)
        )

    def open_theoretical_inventory_import(self):
        if not self._confirm_proceed_despite_exported_db():
            return
        self._open_singleton_window(
            "theoretical_inventory_import",
            lambda: TheoreticalInventoryImportWindow(self, current_worker=self.current_worker),
        )

    def open_inventory_diff(self):
        """
        在庫差異レポートを開く前に、NG一覧・仕掛一覧に未処理の項目が残っていないか確認する。
        残っていれば確認ダイアログを出す（注意喚起だけで、「はい」なら開ける）。
        """
        result = check_unprocessed_items()
        ng_count = result["ng_unprocessed_count"]
        wip_count = result["wip_unprocessed_count"]

        if ng_count or wip_count:
            lines = []
            if ng_count:
                lines.append(f"・NG一覧に未展開のNG報告が{ng_count}件")
            if wip_count:
                lines.append(f"・仕掛一覧に未確定の仕掛が{wip_count}件")

            if not messagebox.askyesno(
                "未処理項目の確認",
                "\n".join(lines) + "あります。\n"
                "対応するか、対象外に指定してから作成してください。\n"
                "それでも作成しますか？",
                parent=self,
            ):
                messagebox.showinfo(
                    "在庫差異レポート",
                    "NG一覧・仕掛一覧で未処理項目を確認してから、改めて作成してください。",
                    parent=self,
                )
                return

        self._open_singleton_window(
            "inventory_diff", lambda: InventoryDiffWindow(self, current_worker=self.current_worker),
        )

    def open_ng_input(self):
        if not self._confirm_proceed_despite_exported_db():
            return
        self._open_singleton_window("ng_input", lambda: NgInputWindow(self, self.current_worker))

    def open_wip_expansion(self):
        if not self._confirm_proceed_despite_exported_db():
            return
        self._open_singleton_window("wip_expansion", lambda: WipExpansionWindow(self, self.current_worker))

    def open_pdf_ocr_import(self):
        """循環import回避のため、ここで都度importする。"""
        from ui.pdf_ocr_import_window import PdfOcrImportWindow
        self._open_singleton_window("pdf_ocr_import", lambda: PdfOcrImportWindow(self))

    def open_parts_attributes_import(self):
        self._open_singleton_window(
            "parts_attributes_import",
            lambda: PartsAttributesImportWindow(self, current_worker=self.current_worker),
        )

    def open_board_structure_import(self):
        self._open_singleton_window(
            "board_structure_import",
            lambda: BoardStructureImportWindow(self, current_worker=self.current_worker),
        )

    def open_production_side_master(self):
        self._open_singleton_window(
            "production_side_master",
            lambda: ProductionSideMasterWindow(self, current_worker=self.current_worker),
        )

    def open_worker_management(self):
        self._open_singleton_window(
            "worker_management",
            lambda: WorkerManagementWindow(self, current_worker=self.current_worker),
        )

    def open_worker_registration(self):
        """新規作業者登録画面を開く（ログイン画面と同じ画面）。"""
        self._open_singleton_window(
            "worker_registration",
            lambda: WorkerRegistrationWindow(self, current_worker=self.current_worker),
        )

    def open_operation_log(self):
        self._open_singleton_window("operation_log", lambda: OperationLogWindow(self))

    def on_logout(self):
        """
        ログアウトしてログイン画面に戻る。子ウィンドウは、親（self）を destroy() すれば一緒に破棄される。
        ui.login_window はこのモジュールをトップレベルで import しているので、関数内で import する（循環 import の回避）。
        """
        if not messagebox.askyesno(
            "ログアウト確認",
            "ログアウトしますか？\n開いている画面はすべて閉じられます。",
            parent=self,
        ):
            return

        self._release_current_lock()
        self.current_worker = None
        self.destroy()

        from ui.login_window import LoginWindow
        LoginWindow().mainloop()

    def _update_current_db_label(self):
        """
        接続中の DB パスをラベルに表示する（既定DB なら「未選択」の案内。D-5x）。
        DB の選択状態や在庫値出力済み状態が変わりうる操作の後には、必ずこれを呼ぶ（ボタンの状態と警告をまとめて再評価する入口）。
        """
        self._current_db_full_path = config.get_current_db_label()
        is_default = config.is_default_db()

        if is_default:
            self.lbl_current_db_path.config(
                text="未選択（データベースを選択するか新規作成してください）",
                foreground="#b00020",
            )
            # 既定DB には在庫値出力済みの判定を呼ばない。init_operation_log_table() の CREATE TABLE IF NOT EXISTS で、
            # 存在しない DB ファイルが作られてしまう（既定DB は読み書きしない方針、D-5x）。
            self.lbl_inventory_diff_export_warning.pack_forget()
        else:
            self.lbl_current_db_path.config(
                text=self._truncate_path_for_display(self._current_db_full_path),
                foreground="#333333",
            )
            self._refresh_inventory_diff_export_warning()

        self._apply_widget_states()

    def _refresh_inventory_diff_export_warning(self):
        """在庫値出力済みかを再評価し、警告ラベルを出し入れする（D-5x。警告だけで、操作は妨げない）。"""
        status = get_inventory_diff_export_status()
        if status["exported"]:
            self.lbl_inventory_diff_export_warning.config(
                text=(
                    f"※ 在庫値出力済み（最終出力：{status['last_exported_at']}）。"
                    "新しい入力は新しいデータベースを作成して行ってください。"
                ),
            )
            self.lbl_inventory_diff_export_warning.pack(
                fill=tk.X, padx=10, pady=(0, 8), before=self._body_frame,
            )
        else:
            self.lbl_inventory_diff_export_warning.pack_forget()

    # パスのラベルの最大描画幅（px）。ウインドウ幅 940px 当時に、接頭辞の幅（約135px）や余白を引いて決めた安全側の値。
    _DB_PATH_LABEL_MAX_WIDTH_PX = 680

    @classmethod
    def _truncate_path_for_display(cls, path: str) -> str:
        """
        表示用に、パスの中央を "…" で省略する（先頭と、DB を見分けるのに大事な末尾は残す）。
        日本語のフォルダ名は半角の約2倍の幅になるので、文字数ではなくピクセル幅で判定する。
        """
        font = tkfont.Font(font=("Helvetica", 9))
        if font.measure(path) <= cls._DB_PATH_LABEL_MAX_WIDTH_PX:
            return path

        ellipsis = "…"
        head_len = len(path) // 2
        tail_len = len(path) - head_len
        while head_len > 0 or tail_len > 0:
            candidate = path[:head_len] + ellipsis + path[-tail_len:] if tail_len else path[:head_len] + ellipsis
            if font.measure(candidate) <= cls._DB_PATH_LABEL_MAX_WIDTH_PX:
                return candidate
            # 先頭・末尾のうち長い方から1文字ずつ削り、両側の情報をできるだけ
            # バランス良く残す。
            if head_len >= tail_len:
                head_len -= 1
            else:
                tail_len -= 1

        return ellipsis

    def _load_db_folders(self):
        db_root = os.path.join(config.APP_DATA_DIR, "db")
        folders = []
        if os.path.isdir(db_root):
            folders = sorted(
                name for name in os.listdir(db_root)
                if os.path.isfile(os.path.join(db_root, name, "inventory.db"))
            )
        self.db_folder_combobox["values"] = folders
        if folders:
            self.db_folder_combobox.current(0)

    def _try_switch_db_path(self, new_path: str, error_title: str = "切替不可") -> bool:
        """
        new_path のロックを取れたら、今のロックを放して config.DB_PATH を切り替える。失敗したら使用者を表示して False（呼び出し元は先に進まないこと）。
        前月引き継ぎの新規作成は、切り替えのタイミングが carry_over_incomplete_lots() の契約に絡むので、これを使わない。
        """
        if not self._acquire_lock_with_corruption_handling(new_path):
            messagebox.showerror(
                error_title,
                "このデータベースは他の利用者が使用中です。\n\n"
                + self._format_lock_info(get_lock_info(new_path)),
                parent=self.winfo_toplevel(),
            )
            return False

        self._release_current_lock()
        config.set_db_path(new_path)
        self._lock_acquired = True
        self._update_current_db_label()
        return True

    def on_switch_database(self):
        folder = self.db_folder_var.get().strip()
        if not folder:
            messagebox.showwarning("警告", "切り替え先のフォルダを選択してください。", parent=self.winfo_toplevel())
            return

        new_path = os.path.join(config.APP_DATA_DIR, "db", folder, "inventory.db")
        if new_path == config.DB_PATH:
            messagebox.showinfo("情報", "既にこのデータベースを使用中です。", parent=self.winfo_toplevel())
            return

        if not self._try_switch_db_path(new_path):
            return
        messagebox.showinfo("完了", "データベースを切り替えました。", parent=self.winfo_toplevel())

    def on_delete_local_database(self):
        """ローカルの月別 DB を削除する（ui.db_delete_helper、安全確認込み）。削除後に一覧を読み直す。"""
        folder = self.db_folder_var.get().strip()
        if not folder:
            messagebox.showwarning("警告", "削除するフォルダを選択してください。", parent=self.winfo_toplevel())
            return

        db_path = os.path.join(config.APP_DATA_DIR, "db", folder, "inventory.db")
        if confirm_and_delete_database(self.winfo_toplevel(), db_path, current_worker=self.current_worker):
            self._load_db_folders()
            # 今の DB は削除できないので実質の変化は無いが、再評価の入口を1つにそろえるため呼ぶ。
            self._update_current_db_label()

    def on_restore_from_backup(self):
        """
        バックアップの月次DB を選び、新しいローカルフォルダへコピーしてから切り替える（原本も今の DB も変えない。D-50）。
        手順: ファイル選択 → 妥当性チェック → フォルダ名の入力 → コピー → 切り替えと init_kitting_plan_tables() → 完了の案内。
        失敗したら作りかけのフォルダを消し、今の DB とロックは変えない。手順の詳細は docs/db.md。
        """
        backup_file_path = filedialog.askopenfilename(
            title="取り込む月次DBバックアップファイルを選択",
            filetypes=[("SQLite データベース", "*.db"), ("すべてのファイル", "*.*")],
            parent=self.winfo_toplevel(),
        )
        if not backup_file_path:
            return

        validation = validate_monthly_db_backup(backup_file_path)
        if not validation["ok"]:
            messagebox.showerror("取り込み不可", validation["reason"], parent=self.winfo_toplevel())
            return

        default_folder_name = "restore_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = simpledialog.askstring(
            "取り込み先フォルダ名",
            "新しいデータベースを作成するフォルダ名を入力してください：",
            initialvalue=default_folder_name,
            parent=self.winfo_toplevel(),
        )
        if not folder_name:
            return
        folder_name = folder_name.strip()
        if not folder_name:
            messagebox.showwarning("警告", "フォルダ名を入力してください。", parent=self.winfo_toplevel())
            return
        if folder_name.lower() == config.DB_ROOT_FOLDER_NAME:
            # "db" は既定DB の判定（親フォルダ名が "db" か）とぶつかるので予約する。
            messagebox.showwarning(
                "警告", f"フォルダ名「{folder_name}」は予約されているため使用できません。",
                parent=self.winfo_toplevel(),
            )
            return

        new_folder = os.path.join(config.APP_DATA_DIR, "db", folder_name)
        new_db_path = os.path.join(new_folder, "inventory.db")
        if os.path.exists(new_db_path):
            messagebox.showwarning(
                "警告", f"フォルダ「{folder_name}」のデータベースは既に存在します。", parent=self.winfo_toplevel(),
            )
            return

        try:
            restore_backup_as_new_local_db(backup_file_path, folder_name)
        except Exception as e:
            if os.path.isdir(new_folder):
                shutil.rmtree(new_folder, ignore_errors=True)
            messagebox.showerror(
                "取り込みエラー", f"バックアップの取り込み中にエラーが発生しました：\n{e}",
                parent=self.winfo_toplevel(),
            )
            return

        if not self._try_switch_db_path(new_db_path, error_title="切替不可"):
            # ロック取得失敗：_try_switch_db_path()は失敗時に現在のDB・ロックを
            # 変更しない契約のため、コピー済みの新フォルダだけを後始末する。
            shutil.rmtree(new_folder, ignore_errors=True)
            return

        init_kitting_plan_tables()
        log_operation(
            self.current_worker.get("name", "unknown"),
            "バックアップからの取り込み",
            detail=f"元ファイル: {backup_file_path} / 取り込み先: {folder_name}",
        )
        # 履歴を master.db にも記録する（取り込み先の DB を後で消しても残る）。
        record_db_lifecycle_event(
            OPERATION_TYPE_RESTORE_FROM_BACKUP, folder_name,
            worker_name=self.current_worker.get("name", "unknown"),
            source_info=backup_file_path,
        )

        messagebox.showinfo(
            "取り込み完了",
            "バックアップからデータベースを取り込み、切り替えました。\n\n"
            "・元のデータベースはそのまま残っており、「切り替え」で戻せます。\n"
            "・マスタデータ（作業者・構成基板数マスタ等）はこの操作では取り込まれません。"
            "必要な場合は「マスタデータを他PCから取り込む」（admin限定）を使ってください。",
            parent=self.winfo_toplevel(),
        )
        self._load_db_folders()
        self.db_folder_var.set(folder_name)

    def on_create_database(self):
        """
        新しい DB を作る。前月からの引き継ぎが OFF なら同期的に作り、ON なら carry_over_incomplete_lots() を別スレッドで実行する。
        """
        folder = self.new_db_folder_var.get().strip()
        if not folder:
            messagebox.showwarning("警告", "作成するフォルダ名を入力してください。", parent=self.winfo_toplevel())
            return
        if folder.lower() == config.DB_ROOT_FOLDER_NAME:
            messagebox.showwarning(
                "警告", f"フォルダ名「{folder}」は予約されているため使用できません。",
                parent=self.winfo_toplevel(),
            )
            return

        new_db_path = os.path.join(config.APP_DATA_DIR, "db", folder, "inventory.db")
        if os.path.exists(new_db_path):
            messagebox.showwarning("警告", f"フォルダ「{folder}」のデータベースは既に存在します。", parent=self.winfo_toplevel())
            return

        # 引き継ぎ処理はconfig.DB_PATHを一時的に旧DBへ切り替えるため、切り替え前の
        # 現在のDBパス（＝引き継ぎ元）をここで確定させておく。
        old_db_path = config.DB_PATH
        carry_over = self.carry_over_var.get()

        if carry_over and self._has_open_child_windows():
            if not messagebox.askyesno(
                "確認",
                "他の画面が開いています。引き継ぎ処理中は操作しないでください。続行しますか？",
                parent=self.winfo_toplevel(),
            ):
                return

        init_database_at(new_db_path)

        # 作ったばかりなので通常は取れるが、念のため確認する（失敗すると、新しい DB ファイルは使われずに残る）。
        if not self._acquire_lock_with_corruption_handling(new_db_path):
            messagebox.showerror(
                "作成不可",
                "新しいデータベースは既に他の利用者が使用中です。\n\n"
                + self._format_lock_info(get_lock_info(new_db_path)),
                parent=self.winfo_toplevel(),
            )
            return
        self._release_current_lock()
        self._lock_acquired = True

        if not carry_over:
            config.set_db_path(new_db_path)
            init_kitting_plan_tables()
            self._update_current_db_label()
            # 履歴を master.db に記録する。
            record_db_lifecycle_event(
                OPERATION_TYPE_CREATE, folder,
                worker_name=self.current_worker.get("name", "unknown"),
            )
            messagebox.showinfo("完了", "新しいデータベースを作成しました。", parent=self.winfo_toplevel())
            self._load_db_folders()
            self.db_folder_var.set(folder)
            self.new_db_folder_var.set("")
            return

        # 引き継ぎありでは、新しい DB のテーブルは引き継ぎ処理の中で作られるので、ここで init_kitting_plan_tables() は呼ばない。
        self._set_menu_enabled(False)
        self._create_db_loading_window = LoadingWindow(self, message="前月からの未完了分を引き継いでいます…")
        # 完了時に master.db の履歴へ、引き継ぎ元として記録するフォルダ名。
        self._create_db_old_db_folder = os.path.basename(os.path.dirname(old_db_path))

        t = threading.Thread(
            target=self._run_carry_over_in_thread,
            args=(old_db_path, new_db_path, folder),
            daemon=True,
        )
        t.start()
        self.after(200, self._poll_create_db_queue)

    def _run_carry_over_in_thread(self, old_db_path, new_db_path, folder):
        try:
            worker_id = self.current_worker.get("worker_id", "SYSTEM")
            summary = carry_over_incomplete_lots(old_db_path, new_db_path, imported_by=worker_id)
            self._create_db_result_queue.put((True, {"summary": summary, "folder": folder}))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._create_db_result_queue.put((False, f"{e}\n{tb}"))

    def _poll_create_db_queue(self):
        try:
            result = self._create_db_result_queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_create_db_queue)
            return

        success, payload = result
        if self._create_db_loading_window is not None:
            self._create_db_loading_window.destroy()
            self._create_db_loading_window = None
        self._set_menu_enabled(True)
        # carry_over_incomplete_lots() は成否に関わらず config.DB_PATH を新しい DB にして戻るので、どちらの場合もラベルを更新する。
        self._update_current_db_label()

        if success:
            summary = payload["summary"]
            folder_name = payload["folder"]
            failed_lot_nos = summary.get("failed_lot_nos") or []
            skipped_lot_nos = summary.get("skipped_lot_nos") or []

            # この時点で config.DB_PATH は新しい DB なので、操作履歴は新しい DB に記録される。
            log_operation(
                self.current_worker.get("name", "unknown"),
                "未完了計画のDB間引き継ぎ",
                detail=f"成功{summary['lots_copied']}件 / スキップ{len(skipped_lot_nos)}件 / "
                       f"失敗{len(failed_lot_nos)}件",
            )
            # 履歴を master.db にも記録する。
            record_db_lifecycle_event(
                OPERATION_TYPE_CREATE_WITH_CARRY_OVER, folder_name,
                worker_name=self.current_worker.get("name", "unknown"),
                source_info=self._create_db_old_db_folder,
            )

            msg = (
                "新しいデータベースを作成しました。\n"
                f"未完了ロット {summary['lots_copied']}件・"
                f"計画行 {summary['kitting_plan_items_copied']}件・"
                f"実績 {summary['production_daily_copied']}件を引き継ぎました。"
            )

            if skipped_lot_nos:
                msg += f"\n（前回までに引き継ぎ済みのため {len(skipped_lot_nos)}件をスキップしました）"

            duplicate_warnings = summary.get("duplicate_lot_warnings") or []
            if duplicate_warnings:
                reason_labels = {
                    "suspected_duplicate": "重複疑いあり（1年以上前の既存計画と同じロットNo.）",
                    "undetermined": "判定不能（実装開始予定日が不明のため要確認）",
                }
                lines = [
                    f"・{w['lot_no']}：{reason_labels.get(w['reason'], w['reason'])}"
                    f"（新DB既存：{w['existing_plan_start_datetime'] or '不明'} / "
                    f"引き継ぎ元：{w['old_plan_start_datetime'] or '不明'}）"
                    for w in duplicate_warnings[:10]
                ]
                more = f"\n...ほか{len(duplicate_warnings) - 10}件" if len(duplicate_warnings) > 10 else ""
                msg += (
                    f"\n\n※ ロットNo.重複の疑いがあります（{len(duplicate_warnings)}件）。"
                    "新DBに既に同じロットNo.の計画が存在していました。誤って別ロットが"
                    "混同されていないか確認してください。\n" + "\n".join(lines) + more
                )

            if failed_lot_nos:
                lines = [
                    f"・{f['lot_no']}：{f['error']}"
                    for f in failed_lot_nos[:10]
                ]
                more = f"\n...ほか{len(failed_lot_nos) - 10}件" if len(failed_lot_nos) > 10 else ""
                msg += (
                    f"\n\n※ 一部のロットの引き継ぎに失敗しました（{len(failed_lot_nos)}件）。\n"
                    + "\n".join(lines) + more
                    + "\n\nもう一度「前月から未完了分を引き継ぐ」を実行すると、"
                    "既に成功した分はスキップされ、失敗した分だけ再試行されます。"
                )
                messagebox.showwarning("一部失敗", msg, parent=self.winfo_toplevel())
            else:
                messagebox.showinfo("完了", msg, parent=self.winfo_toplevel())
        else:
            # payload（失敗時）は例外メッセージ文字列のため、フォルダ名は
            # 入力欄からそのまま取る（クリアはこの後まとめて行う）。
            folder_name = self.new_db_folder_var.get().strip()
            messagebox.showerror(
                "引き継ぎエラー",
                f"未完了分の引き継ぎ中にエラーが発生しました。\n"
                f"新しいデータベース自体は作成済みです（切り替え済み）。\n\n{payload}",
                parent=self.winfo_toplevel(),
            )

        self._load_db_folders()
        self.db_folder_var.set(folder_name)
        self.new_db_folder_var.set("")

    def on_backup_databases(self):
        """月次DB とマスタDB を、選んだフォルダへ同時にバックアップする（別スレッドで実行し、その間は DB 操作のボタンを無効にする）。"""
        destination_folder = filedialog.askdirectory(
            title="バックアップ保存先フォルダを選択", parent=self.winfo_toplevel(),
        )
        if not destination_folder:
            return

        # _poll_backup_queue()はこのメソッドのローカル変数を参照できないため、
        # インスタンス属性に保持しておく（log_operation()の詳細欄で使う）。
        self._backup_destination_folder = destination_folder

        self._set_menu_enabled(False)
        self._backup_loading_window = LoadingWindow(self, message="データベースをバックアップしています…")

        t = threading.Thread(
            target=self._run_backup_in_thread,
            args=(destination_folder,),
            daemon=True,
        )
        t.start()
        self.after(200, self._poll_backup_queue)

    def _run_backup_in_thread(self, destination_folder):
        try:
            result = backup_databases(destination_folder)
            self._backup_result_queue.put((True, result))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._backup_result_queue.put((False, f"{e}\n{tb}"))

    def _poll_backup_queue(self):
        try:
            result = self._backup_result_queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_backup_queue)
            return

        success, payload = result
        if self._backup_loading_window is not None:
            self._backup_loading_window.destroy()
            self._backup_loading_window = None
        self._set_menu_enabled(True)

        if success:
            lines = []
            if payload["inventory_backup_path"]:
                lines.append(f"・月次DB：{payload['inventory_backup_path']}")
            if payload["master_backup_path"]:
                lines.append(f"・マスタDB：{payload['master_backup_path']}")

            skipped = payload.get("skipped") or []
            skipped_labels = {"inventory": "月次DB", "master": "マスタDB"}
            if skipped:
                lines.append(
                    "\n※ 次のDBはまだ一度も作成されていないためスキップしました：\n  "
                    + "、".join(skipped_labels.get(s, s) for s in skipped)
                )

            log_operation(
                self.current_worker.get("name", "unknown"),
                "手動バックアップ",
                detail=f"保存先: {self._backup_destination_folder}",
            )

            messagebox.showinfo(
                "バックアップ完了",
                "以下のファイルを保存しました。\n\n" + "\n".join(lines),
                parent=self.winfo_toplevel(),
            )
        else:
            messagebox.showerror(
                "バックアップエラー",
                f"バックアップ中にエラーが発生しました。\n\n{payload}",
                parent=self.winfo_toplevel(),
            )

    def on_merge_master_from_backup(self):
        """
        マスタDB のバックアップから、今の master.db に足りないレコードだけを取り込む（別スレッドで実行）。
        workers（ログインできる作業者と役割）も変わるので admin 限定。ボタンは admin にしか出さないが、直接呼ばれた場合に備えてここでも確認する。
        """
        if self.current_worker.get("role") != "admin":
            messagebox.showerror(
                "権限がありません",
                "この操作はadmin役割の作業者のみ実行できます。",
                parent=self.winfo_toplevel(),
            )
            return

        backup_file_path = filedialog.askopenfilename(
            title="取り込むマスタDBバックアップファイルを選択",
            filetypes=[("SQLite データベース", "master_backup_*.db"), ("すべてのファイル", "*.*")],
            parent=self.winfo_toplevel(),
        )
        if not backup_file_path:
            return

        self._set_menu_enabled(False)
        self._merge_master_loading_window = LoadingWindow(
            self, message="マスタデータを取り込んでいます…",
        )

        t = threading.Thread(
            target=self._run_merge_master_in_thread,
            args=(backup_file_path,),
            daemon=True,
        )
        t.start()
        self.after(200, self._poll_merge_master_queue)

    def _run_merge_master_in_thread(self, backup_file_path):
        try:
            result = merge_master_from_backup(backup_file_path)
            self._merge_master_result_queue.put((True, result))
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._merge_master_result_queue.put((False, f"{e}\n{tb}"))

    def _poll_merge_master_queue(self):
        try:
            result = self._merge_master_result_queue.get_nowait()
        except queue.Empty:
            self.after(200, self._poll_merge_master_queue)
            return

        success, payload = result
        if self._merge_master_loading_window is not None:
            self._merge_master_loading_window.destroy()
            self._merge_master_loading_window = None
        self._set_menu_enabled(True)

        if success:
            table_labels = {
                "board_structure_master": "構成基板数マスタ",
                "parts_attributes": "部品属性マスタ",
                "workers": "作業者",
                "parts": "部品マスタ",
                "final_products": "完成品マスタ",
            }
            lines = []
            no_change_labels = []
            for table, added in payload.items():
                label = table_labels.get(table, table)
                if added > 0:
                    lines.append(f"・{label}：{added}件追加")
                else:
                    no_change_labels.append(label)
            if no_change_labels:
                lines.append(f"・{'、'.join(no_change_labels)}：変更なし")

            log_operation(
                self.current_worker.get("name", "unknown"),
                "マスタデータ取り込み",
                detail=", ".join(f"{table_labels.get(t, t)}:{n}件" for t, n in payload.items()),
            )

            messagebox.showinfo(
                "取り込み完了",
                "マスタデータの取り込みが完了しました。\n\n" + "\n".join(lines),
                parent=self.winfo_toplevel(),
            )
        else:
            messagebox.showerror(
                "取り込みエラー",
                f"マスタデータの取り込み中にエラーが発生しました。\n\n{payload}",
                parent=self.winfo_toplevel(),
            )