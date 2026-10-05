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
from ui.wip_expansion_window import WipExpansionWindow
from ui.worker_management_window import WorkerManagementWindow
from ui.worker_registration_window import WorkerRegistrationWindow
from ui.operation_log_window import OperationLogWindow
from models.operation_log import log_operation, get_inventory_diff_export_status
from ui.db_delete_helper import confirm_and_delete_database
from services.db_migration_carryover import carry_over_incomplete_lots
from services.backup_service import (
    backup_databases, validate_monthly_db_backup, restore_backup_as_new_local_db,
)
from services.master_merge_service import merge_master_from_backup
from services.unprocessed_check_service import check_unprocessed_items
from services.app_settings_service import load_last_db_path
from ui.window_utils import center_window


class MainWindow(tk.Tk):
    def __init__(self, current_worker):
        super().__init__()
        self.current_worker = current_worker
        self._pc_name = socket.gethostname()
        self._worker_name = current_worker.get("name", "unknown")
        self._lock_acquired = False
        # 前回終了時のDBパス復元に失敗した場合、__init__()の最後（UI構築完了後）
        # にユーザーへ通知するためのフラグ。ここで先に案内すると、まだウィンドウの
        # 体裁が整っていない状態でダイアログが割り込むため、あえて最後に回す。
        self._restore_last_db_failed_path = None

        # 起動時、前回選択されていたDBパス（永続化済み）があればそちらへ切り替える。
        # 無い場合（初回起動・設定ファイル削除等）は、従来通りconfig.DB_PATH
        # （モジュール読み込み時点のデフォルト＝APP_DATA_DIR配下のローカルDB）を
        # そのまま使う。記憶されていたパスが現在は存在しない（ファイル削除・
        # 共有フォルダが利用不可等）場合は、デフォルトのまま起動を続け、
        # UI構築完了後にその旨を通知する。
        last_db_path = load_last_db_path()
        if last_db_path and last_db_path != config.DB_PATH:
            if os.path.exists(last_db_path):
                config.set_db_path(last_db_path)
            else:
                self._restore_last_db_failed_path = last_db_path

        # 既定DB（未選択、config.is_default_db()）の間は、ロックファイルにも
        # 一切触れない（D-56・D-57で確定した「既定DBは読み書きしない」方針を、
        # .lock（services.db_lock_service.py、DB本体とは別ファイル）にも拡張する。
        # 詳細はCANONICAL_DESIGN_DECISIONS.md参照）。既定DBパスに他プロセスの
        # 古い・有効なロックが残っていても、ロック取得自体を試みないため影響を
        # 受けず起動できる。self._lock_acquiredは__init__()冒頭（47行目付近）で
        # 既にFalse初期化済みのため、ここでは単に取得処理をスキップするだけで、
        # 以降のハートビート（_heartbeat()）・解放（_release_current_lock()）は
        # 既存のif self._lock_acquired:ガードにより自然に何もしなくなる。
        #
        # 既定DB以外の場合は従来通り：起動時点のconfig.DB_PATHに対してロックを
        # 取得できなければ、他PC・他ユーザーが使用中とみなしてここで起動を中断する
        # （以降のUI構築は行わない）。LoginWindow側は self.winfo_exists() を見てから
        # mainloop() を呼ぶ想定（destroy済みのTkルートでmainloop()を呼ばないように
        # するため）。
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

        self.title("部品在庫管理アプリ - メインメニュー")
        # 月次データ・共通マスタを左右2列表示にしたことで縦に短くなった分、
        # ウィンドウの高さは詰め、横幅は上部のデータベース選択欄（前月引き継ぎ
        # チェックボックス等を含む）と左右2列のボタン群の両方が収まる幅に広げた
        # （winfo_reqwidth()実測値920前後に基づく）。
        # 共有フォルダ関連の2ボタンはヘッダー行の右上へ移動したため、
        # 「データベース選択」領域は再び1段のみとなり、高さは元の940x600に戻す。
        self.geometry("940x600")
        center_window(self)

        # メインメニューから開く画面の多重表示防止用：key -> 開いているToplevelインスタンス。
        # ウィンドウが閉じられたら _open_singleton_window() が設定した
        # WM_DELETE_WINDOWハンドラ経由で自動的にエントリが削除される。
        self._open_windows = {}
        # KittingProductionEntryWindowは非同期（別スレッドでのデータ事前取得）で開くため、
        # 生成完了までの間に連打された場合に二重にスレッドを起こさないためのガード。
        self._kitting_entry_loading = False

        # 過去に topmost=True が設定されていた場合の後遺症を防ぐため明示的に無効化する。
        # main_window（root）はフォーカス制御（lift/focus_force/grab_set等）を一切行わない。
        self.attributes("-topmost", False)

        # データベース選択領域（最上部）
        # ローカルdb/フォルダからの選択・新規作成。共有フォルダ（UNCパス等）
        # 上のDBを直接開く／新規作成する2ボタンは、ヘッダー行の右上へ移動した
        # （db_select_row2として同居していたが、メインメニューの右上に独立して
        # 配置する方針に変更したため）。
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

        # on_backup_databases()用（2026-10-02追加、同じ非同期パターン）
        self._backup_result_queue = queue.Queue()
        self._backup_loading_window = None
        self._backup_destination_folder = None

        # on_merge_master_from_backup()用（2026-10-03追加、同じ非同期パターン）
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

        # 月次DB（config.DB_PATH）・マスタDB（config.MASTER_DB_PATH）を同時に
        # バックアップする手動ボタン（2026-10-02追加）。
        self.btn_backup_databases = ttk.Button(
            header_frame, text="バックアップ", command=self.on_backup_databases,
        )
        self.btn_backup_databases.pack(side=tk.RIGHT, padx=(0, 10))

        # バックアップ済みの月次DB（.db）を選び、新しいローカルDBフォルダへ
        # 取り込んで切り替えるボタン（メインメニュー整理、CANONICAL_DESIGN_
        # DECISIONS.md D-50参照）。以前の「共有フォルダのDBを開く」機能を
        # バックアップの復元に転用する運用（旧D-45）は、バックアップ原本が
        # そのまま本番DBになってしまう・マスタDBは復元されない・D-20（ローカル+
        # バックアップ方針）と整合しないという3点の問題があったため、本ボタン
        # （新フォルダへコピーしてから切り替える、原本・元のDBとも不変）に
        # 置き換えた。「バックアップ」ボタンの隣（pack順序上、直後にpackする
        # ことでside=tk.RIGHTの並びで隣接表示になる）に配置し、権限制限は無い。
        self.btn_restore_from_backup = ttk.Button(
            header_frame, text="バックアップの呼び出し", command=self.on_restore_from_backup,
        )
        self.btn_restore_from_backup.pack(side=tk.RIGHT, padx=(0, 10))

        # バックアップ済みマスタDB（master_backup_*.db）から、現在のmaster.dbに
        # 不足しているレコードを取り込むボタン（2026-10-03追加）。マスタデータ
        # （特にworkers＝ログイン可能な作業者とその役割）に影響する操作のため、
        # 作業者管理画面（ui/worker_management_window.py）の編集・有効/無効
        # 切替と同様、admin役割の作業者にのみメニューへ表示する（operatorの
        # 場合はボタン自体を生成・packしない）。self.btn_merge_masterはNoneで
        # 初期化しておき、_menu_widgetsへの追加もこの条件に合わせる。
        self.btn_merge_master = None
        if current_worker.get("role") == "admin":
            self.btn_merge_master = ttk.Button(
                header_frame, text="マスタデータを他PCから取り込む", command=self.on_merge_master_from_backup,
            )
            self.btn_merge_master.pack(side=tk.RIGHT, padx=(0, 10))

        # 現在接続中のDBパスを常時表示する行（ヘッダー直下）。切り替え操作の
        # たびに最新のフルパスへ更新される（_update_current_db_label()参照。
        # 呼び出し箇所：起動時のこの直後、_try_switch_db_path()＝
        # on_switch_database()・on_restore_from_backup()共通、
        # on_create_database()の両分岐）。
        # 長いパスでウィンドウの横幅が押し広げられてレイアウトが崩れないよう、
        # _truncate_path_for_display()で必要に応じて中央を省略表示する
        # （フルパスは自己管理のself._current_db_full_pathに保持）。
        db_path_frame = ttk.Frame(self, padding=(10, 0, 10, 8))
        db_path_frame.pack(fill=tk.X)
        ttk.Label(db_path_frame, text="接続中のデータベース：", font=("Helvetica", 9)).pack(side=tk.LEFT)
        self._current_db_full_path = ""
        self.lbl_current_db_path = ttk.Label(db_path_frame, text="-", font=("Helvetica", 9), foreground="#333333")
        self.lbl_current_db_path.pack(side=tk.LEFT)

        # 在庫値出力済みDBの警告（CANONICAL_DESIGN_DECISIONS.md D-5x参照）。
        # 現在のDBラベルの直下に、出力済みの場合のみ目立つ色で常時表示する
        # （禁止ではなく警告のため、表示するだけで入力自体は妨げない）。
        # 再評価のタイミングは現在DBラベルと同じ（_update_current_db_label()
        # から呼ばれる_refresh_inventory_diff_export_warning()に集約）。
        self.lbl_inventory_diff_export_warning = ttk.Label(
            self, text="", font=("Helvetica", 9, "bold"), foreground="#b00020",
        )
        # _update_current_db_label()の最初の呼び出しは、_menu_widgets・
        # _default_db_locked_widgets（ボタン一覧）の構築後に行う
        # （_apply_widget_states()がこれらを参照するため）。

        # メニューボタン領域
        # 月次データ（config.DB_PATH切り替えの対象＝月ごとのDBフォルダに入っている
        # データ：キッティング計画・生産実績・在庫関連・操作履歴等）と、共通マスタ
        # （config.MASTER_DB_PATH＝board_structure_master・parts_attributes・
        # workers・parts・final_products、月次DB切り替えとは独立の固定ローカル
        # ファイル）を左右2列に分けて表示する（共通マスタを左、月次データを右。
        # マスタDB分離の経緯はCANONICAL_DESIGN_DECISIONS.md D-38・D-42参照）。
        # 左列末尾の「ツール」見出し配下（PDF読み取り・操作履歴）は例外で、
        # 依存先は月次DB（inventory_stock・operation_logいずれもconfig.DB_PATH
        # 側のテーブル）のままであり、月をまたいで使い回す共通マスタではない。
        # 使用頻度の低い補助機能をまとめて置くという配置上の都合であり、
        # 依存DBによる厳密な列分けの例外であることに注意（D-50参照）。
        body_frame = ttk.Frame(self, padding=20)
        body_frame.pack(expand=True, fill=tk.BOTH)
        # 在庫値出力済み警告ラベル（上で生成済み）をpack(before=...)で挿入する際の
        # 基準。body_frameより前に挿入することで、db_path_frame（現在DBラベル）の
        # 直下に常に表示される（警告ラベル自体はpack/pack_forget()を繰り返すため、
        # 呼び出し順だけに依存せずこの基準で位置を固定する）。
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

        # 作業者登録（新規登録のみ、role不問で常に表示）は、ui/login_window.py
        # （ログイン前）と同じ導線をログイン後にも提供する（2026-10-03追加）。
        btn_worker_registration = ttk.Button(
            master_frame, text="作業者登録（新規）", command=self.open_worker_registration
        )
        btn_worker_registration.pack(fill=tk.X, pady=5)

        # 「3. 作業者管理」（既存作業者の編集・有効/無効切替）は、admin役割の
        # 作業者にのみメニューへ表示する（2026-10-03追加）。operatorの場合は
        # ボタン自体を生成・packしない（CANONICAL_DESIGN_DECISIONS.md参照）。
        # ボタンが存在しない場合に備え、self.btn_worker_managementはNoneで
        # 初期化しておく（_menu_widgetsへの追加もこの条件に合わせる）。
        self.btn_worker_management = None
        if current_worker.get("role") == "admin":
            self.btn_worker_management = ttk.Button(
                master_frame, text="3. 作業者管理", command=self.open_worker_management
            )
            self.btn_worker_management.pack(fill=tk.X, pady=5)

        # 「ツール」見出し：PDF読み取り・操作履歴（メインメニュー整理、
        # CANONICAL_DESIGN_DECISIONS.md D-50参照）。以前は月次データ側に項目8・9
        # として番号付きで配置されていたが、共通マスタ側へ移動し番号を外した
        # （以降この2つに番号は付けない。右列「月次データ」の1〜7は変更なし）。
        # 依存先DB自体は変わらず月次DB（config.DB_PATH、inventory_stock・
        # operation_logいずれも月次DB側のテーブル）のままであり、月をまたいで
        # 使い回す共通マスタになったわけではない。使用頻度の低い補助機能を
        # まとめて置くという配置上の都合であることに注意。
        ttk.Label(master_frame, text="ツール", font=("Helvetica", 11, "bold")).pack(anchor=tk.W, pady=(15, 5))

        btn_pdf_ocr_import = ttk.Button(
            master_frame, text="PDF読み取り（在庫照合）", command=self.open_pdf_ocr_import
        )
        btn_pdf_ocr_import.pack(fill=tk.X, pady=5)

        btn_operation_log = ttk.Button(
            master_frame, text="操作履歴", command=self.open_operation_log
        )
        btn_operation_log.pack(fill=tk.X, pady=5)

        ttk.Separator(body_frame, orient="horizontal").pack(fill=tk.X, pady=15)

        # ログアウトボタンは他のメニューボタンと違い誤操作を避けたいため、
        # fill=tk.Xで全幅に広げず、横幅を約半分程度に抑えて中央に配置する。
        btn_logout = ttk.Button(body_frame, text="ログアウト", command=self.on_logout, width=35)
        btn_logout.pack(pady=5)

        # メインメニュー全体の操作可否を一括で切り替えるための対象ウィジェット一覧
        # （_set_menu_enabled()参照）。carry_over_incomplete_lots()実行中、
        # config.DB_PATHが旧DB→新DBの間で一時的に入れ替わるため、他のボタンから
        # 新規にウィンドウを開けたりDBを切り替えられたりすると、その一時的な
        # 切り替わりの間にデータ不整合が起きる恐れがある。
        # 注意：chk_carry_over（前月引き継ぎチェック）はここには含めない。
        # 既定DB（未選択）の間はこのチェック自体を使えなくする（かつチェック済み
        # 状態も強制解除する）必要があり、通常のボタンと同じ単純なstate切替では
        # 済まないため、_apply_widget_states()内で個別に扱う。
        self._menu_widgets = [
            self.db_folder_combobox, self.btn_switch_database, self.entry_new_db_folder,
            self.btn_create_database,
            self.btn_backup_databases, self.btn_restore_from_backup,
            btn_kitting_import, btn_kitting_production, btn_inventory_input,
            btn_theoretical_import, btn_inventory_diff, btn_ng_input, btn_wip_expansion,
            btn_pdf_ocr_import, btn_operation_log,
            btn_parts_attributes_import,
            btn_worker_registration, btn_board_structure_import, btn_logout,
        ]
        # 「3. 作業者管理」・「マスタデータを他PCから取り込む」はいずれも
        # admin役割の場合のみ生成されるため、存在する場合だけ_menu_widgetsへ
        # 追加する（2026-10-03追加）。
        if self.btn_worker_management is not None:
            self._menu_widgets.append(self.btn_worker_management)
        if self.btn_merge_master is not None:
            self._menu_widgets.append(self.btn_merge_master)

        # 既定DB（APP_DATA_DIR/db/inventory.db、フォルダ名なし＝「未選択」）を
        # 開いている間、無効化する業務ボタンの一覧（_apply_widget_states()参照、
        # CANONICAL_DESIGN_DECISIONS.md D-5x参照）。_menu_widgetsとは別に新設した
        # （carry_over_incomplete_lots()実行中の一括無効化とは独立に判定する
        # ため）。DB選択・新規作成・ローカルDB削除・バックアップの呼び出し・
        # ログアウトはここに含めない（既定DBの間も常に操作できる必要がある）。
        # ここに含めた全ウィジェットは、_menu_widgetsにも含まれていること
        # （_apply_widget_states()が両方のゲートを合成するため、どちらか一方にしか
        # 含まれていないと一方のゲートが効かなくなる）。
        self._default_db_locked_widgets = [
            btn_kitting_import, btn_kitting_production, btn_inventory_input,
            btn_theoretical_import, btn_inventory_diff, btn_ng_input, btn_wip_expansion,
            btn_pdf_ocr_import, btn_operation_log,
            btn_board_structure_import, btn_parts_attributes_import, btn_worker_registration,
            self.btn_backup_databases,
        ]
        if self.btn_worker_management is not None:
            self._default_db_locked_widgets.append(self.btn_worker_management)
        if self.btn_merge_master is not None:
            self._default_db_locked_widgets.append(self.btn_merge_master)

        # _set_menu_enabled()が管理する一括無効化フラグ（carry_over_incomplete_lots()
        # 実行中等）。_apply_widget_states()が、これと既定DBロック
        # （config.is_default_db()）の両方を合成して最終的なstateを決める。
        self._menu_enabled = True
        self._apply_widget_states()
        self._update_current_db_label()

        # ウィンドウを閉じる（×ボタン・Alt+F4等）際にロックファイルを解放してから
        # 終了する。on_logout()はdestroy()を直接呼ぶためこのprotocolハンドラを
        # 経由しない → on_logout()側でも個別にロック解放する必要がある。
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
        services.db_lock_service.acquire_lock()のラッパー。

        ロックファイルが壊れていて読み取れない場合（LockFileCorruptedError）、
        自動では解除・上書きしない。「他の利用者が本当に使用中でないか確認した
        上で、強制的にロックを取得するか」をユーザーに確認するダイアログを表示し、
        「はい」が選ばれた場合のみforce=Trueで再取得する（誤って他者の使用中の
        ロックを奪わないよう、確認なしの自動上書きは行わない）。

        戻り値：取得できたか。他者が有効なロックを保持中で取得できない
        （破損とは無関係の）通常の失敗はFalseを返す。呼び出し元は従来通り、
        Falseの場合にget_lock_info()で使用者情報を表示すればよい。
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
        メインメニュー全体（DB選択領域＋操作メニューのボタン群）の操作可否を
        一括で切り替える。carry_over_incomplete_lots()実行中の他画面操作を
        防ぐために使う（on_create_database()参照）。

        実際のwidget.config(state=...)はここでは行わず、フラグ
        （self._menu_enabled）を更新してから_apply_widget_states()に委ねる。
        既定DB（未選択）による無効化（_default_db_locked_widgets）と
        独立に管理し、どちらか一方の解除が他方の無効状態を誤って外さない
        ようにするため（CANONICAL_DESIGN_DECISIONS.md D-5x参照）。
        """
        self._menu_enabled = enabled
        self._apply_widget_states()

    def _apply_widget_states(self):
        """
        _menu_widgets（_set_menu_enabled()が管理する一括無効化。
        carry_over_incomplete_lots()実行中等）と_default_db_locked_widgets
        （既定DB＝未選択の間の業務ボタン無効化。config.is_default_db()に連動）
        の2つのゲートを合成し、各ウィジェットの最終的なstateを決める。

        両ゲートとも「Trueなら制限なし」側で持つ（self._menu_enabled・
        not default_db_open）ため、素直にANDするだけで「どちらかが無効化を
        要求していれば無効」という振る舞いになり、一方の解除（例：carry_over
        完了によるself._menu_enabled=True化）が、既定DBロックで無効のままに
        すべきボタンまで誤って有効化してしまうことがない（逆方向も同様）。

        self.db_folder_combobox（state="readonly"が通常の有効状態。ttk.Comboboxは
        NORMALにすると自由入力が可能になってしまうため、readonly/disabledの
        2状態で切り替える）だけ特別扱いする。

        chk_carry_over（前月引き継ぎチェック）は_menu_widgetsに含めていない：
        既定DBの間は「新しいデータベースを作成」自体は有効のままにする一方、
        このチェックボックスだけは使えなくし、チェック済みの状態も強制的に
        解除する（既定DBを引き継ぎ元にした不定な動作を防ぐため）、という
        他のボタンとは異なる挙動が必要なため個別に扱う。
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
        メインメニューから開く画面の多重表示防止用の共通ヘルパー。

        key に対応するウィンドウが既に開いていれば（self._open_windows に登録済み・
        winfo_exists()もTrue）新規生成せず前面に出すだけにする。無ければ factory() で
        新規生成し、WM_DELETE_WINDOWで閉じられた際に self._open_windows から
        該当エントリを削除してから通常のdestroy()を行うようにする（各ウィンドウ
        クラス自体には一切手を入れず、外側からprotocol()を設定するだけで済む）。
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
        self._open_windows（_open_singleton_window()経由で開いたウィンドウ）の
        うち、現在も実際に存在しているものが1つでもあるか確認する
        （on_create_database()の引き継ぎ確認ダイアログ用）。

        非同期で開く生産実績入力画面（open_kitting_production_entry()）は、
        ウィンドウ生成が完了した時点でのみ self._open_windows に登録される
        ため、読み込み中（スレッド完了待ち）の状態は「開いている」扱いには
        ならない（その時点ではまだ実体となるウィンドウが存在しないため）。
        """
        return any(w.winfo_exists() for w in self._open_windows.values())

    def _confirm_proceed_despite_exported_db(self) -> bool:
        """
        現在のDBが在庫値出力済み（models.operation_log.get_inventory_diff_
        export_status()）の場合、月次データへ書き込む画面（1〜6）を開く前に
        確認ダイアログを表示する（CANONICAL_DESIGN_DECISIONS.md D-5x参照）。
        「いいえ」なら呼び出し元は画面を開かずに戻ること。

        対象外（このチェックを呼ばない画面）：在庫値出力（7）自体・日報月報等の
        閲覧系・ツール・共通マスタ・DB管理（いずれも月次データへの新規入力を
        伴わない、または在庫値出力の完了判定そのものに関わるため）。

        既定DB（未選択）の間は、これらの画面を開くボタン自体が無効化されている
        （_default_db_locked_widgets参照）ため通常この関数は呼ばれないが、
        念のため既定DBに対してはget_inventory_diff_export_status()を呼ばない
        （既定DBのファイルを一切作らない方針を守るため、_update_current_db_label()
        と同じ理由）。
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
        生産実績入力画面を開く。計画一覧のDBアクセス（KittingProductionEntryWindow.
        _fetch_plan_list_rows()）は重く、UIスレッドで同期実行するとその間ロード画面
        含め一切描画更新されない（フリーズしたように見える）ため、別スレッドで
        事前に取得し、完了をポーリングで検知してからUIスレッド上でウィジェットを
        生成する（ui.kitting_plan_import.KittingPlanImportWindowの
        threading.Thread + queue.Queue + after()ポーリングパターンを踏襲）。

        多重表示防止：非同期のため _open_singleton_window() をそのまま使えない
        （factory()を呼んだ時点でウィンドウが即座には出来ていない）。
        - 既にウィンドウが開いている場合：新規スレッドは起こさず、前面に出した上で
          既存ウィンドウの load_plan_list()（同期版、「更新」ボタンと同じ経路）を
          呼んでデータのみ最新化する。
        - 読み込み中（スレッド完了待ち）に再度呼ばれた場合：_kitting_entry_loading
          フラグで二重にスレッドを起こさないようにする（この場合on_readyは
          呼ばれない。既に進行中の別呼び出しに任せる）。

        on_ready：ウインドウの用意ができた時点（既存流用・新規作成いずれも）で
        呼ばれるコールバック（引数：KittingProductionEntryWindowインスタンス）。
        非同期のため、単純にopen_kitting_production_entry()の直後に処理を
        続けることができない場面向けの汎用フック。省略時（None）は何もしない
        （従来通りの呼び出し）。（旧：メインメニュー「実績CSV取込状況」ボタンが
        生産実績入力画面を開いた直後に続けてステージング一覧を開くために使って
        いたが、同ボタンは生産実績入力画面側（KittingProductionEntryWindowの
        「実績CSV取込状況」ボタン）へ移設され、直接その画面のインスタンス上で
        open_pending_csv_staging_window()を呼ぶだけで済むようになったため、
        現在この引数を使う呼び出し元は無い）
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
        在庫差異レポートを開く前に、NG一覧（ui.ng_input_window）・仕掛一覧
        （ui.wip_expansion_window）に未処理（未展開/未確定、かつ対象外指定
        されていない）項目が残っていないか確認する
        （services.unprocessed_check_service.check_unprocessed_items()）。

        1件以上あれば確認ダイアログを表示する。強制ブロックはせず、あくまで
        注意喚起として実装する（「はい」を選べば従来通りレポートを開ける）。
        「いいえ」の場合はレポートを開かず、どちらの画面で確認すべきかを
        案内するメッセージを表示する。
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

    def open_worker_management(self):
        self._open_singleton_window(
            "worker_management",
            lambda: WorkerManagementWindow(self, current_worker=self.current_worker),
        )

    def open_worker_registration(self):
        """
        新規作業者登録画面を開く（2026-10-03追加、role不問で常にメニューに
        表示するボタンから呼ばれる）。ui/login_window.py（ログイン前）と
        同じ画面クラスを使う。
        """
        self._open_singleton_window(
            "worker_registration",
            lambda: WorkerRegistrationWindow(self, current_worker=self.current_worker),
        )

    def open_operation_log(self):
        self._open_singleton_window("operation_log", lambda: OperationLogWindow(self))

    def on_logout(self):
        """
        ログアウトし、ログイン画面に戻る。

        開いている子ウィンドウ（_open_windowsで管理している多重表示防止対象、
        および対象外のUnifiedReportWindow/UnmatchedProductionWindow等）は、
        個別にクローズ処理を呼ぶ必要はない。Tkinterの仕様上、親（MainWindow=このself）を
        destroy()すると、それを親として開いた全Toplevelも連動して破棄されるため。

        ui.login_window は本モジュールをトップレベルでimportしているため
        （循環import）、ここでは関数内importで回避する。
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
        現在接続中のDBパス（config.get_current_db_label()、実体はconfig.DB_PATH
        をそのまま返す）を、ヘッダー直下のラベル（self.lbl_current_db_path）へ
        反映する。既定DB（config.is_default_db()、未選択として扱う）の場合は、
        パスではなく「未選択」であることを示す案内文を表示する
        （CANONICAL_DESIGN_DECISIONS.md D-5x参照）。

        この関数は、DB選択状態・在庫値出力済み状態のいずれかが変わり得る
        全ての操作の後に呼ぶことで、両方の再評価（_apply_widget_states()・
        _refresh_inventory_diff_export_warning()）を一括して行う入口として
        機能する。呼び出し箇所：__init__()の初回表示に加え、config.DB_PATHを
        変更する全ての操作の後（_try_switch_db_path()＝on_switch_database()・
        on_restore_from_backup()が共通で経由する、on_create_database()の
        引き継ぎ無し分岐、_poll_create_db_queue()＝on_create_database()の
        引き継ぎ有り分岐の非同期完了時、on_delete_local_database()の削除後）。
        """
        self._current_db_full_path = config.get_current_db_label()
        is_default = config.is_default_db()

        if is_default:
            self.lbl_current_db_path.config(
                text="未選択（データベースを選択するか新規作成してください）",
                foreground="#b00020",
            )
            # 既定DBは「アプリからは一切読み書きしない」方針（CANONICAL_
            # DESIGN_DECISIONS.md D-5x参照）のため、在庫値出力済み判定
            # （get_inventory_diff_export_status()）もここでは呼ばない。
            # この関数は内部でmodels.operation_log.init_operation_log_table()
            # （CREATE TABLE IF NOT EXISTS）を実行し、sqlite3.connect()が
            # 存在しないDBファイルを新規作成してしまうため、既定DBに対して
            # 呼ぶと「ファイルを作らない」という既定DBの前提を破ってしまう。
            self.lbl_inventory_diff_export_warning.pack_forget()
        else:
            self.lbl_current_db_path.config(
                text=self._truncate_path_for_display(self._current_db_full_path),
                foreground="#333333",
            )
            self._refresh_inventory_diff_export_warning()

        self._apply_widget_states()

    def _refresh_inventory_diff_export_warning(self):
        """
        現在のDBが在庫値出力済みかどうかを再評価し、ヘッダー直下の警告ラベル
        （self.lbl_inventory_diff_export_warning）の表示/非表示を切り替える
        （CANONICAL_DESIGN_DECISIONS.md D-5x参照）。禁止ではなく警告のため、
        表示するだけで他の操作を妨げない。
        """
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

    # ラベル本体（接頭辞「接続中のデータベース：」を除いた、パス部分のみ）に
    # 許容する最大描画幅（ピクセル）。ウィンドウ幅940px・接頭辞ラベルの実測幅
    # （Helvetica 9で約135px）・左右パディング・ウィンドウ枠を差し引いた
    # 安全側の値（実測での合計オーバーフローが無いことを確認した上で設定）。
    _DB_PATH_LABEL_MAX_WIDTH_PX = 680

    @classmethod
    def _truncate_path_for_display(cls, path: str) -> str:
        """
        表示用にパスを省略する。指定フォントでの描画幅が
        _DB_PATH_LABEL_MAX_WIDTH_PX以下ならそのまま返す。超える場合は、
        先頭（ローカルのドライブレター、または共有フォルダのUNCサーバー名側）と
        末尾（フォルダ名・ファイル名側。今どのDBかを判別するのに最も重要な情報）を
        残しながら中央を1文字ずつ削り、"…"で省略する。

        文字数ではなくピクセル幅で判定する理由：共有フォルダのUNCパスには
        日本語のフォルダ名（全角文字、半角の概ね2倍の描画幅）が含まれることが
        多く、固定文字数での省略では全角文字が連続する場合に実際の描画幅が
        ウィンドウ幅を超えてしまう（実測で確認済み）。
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
        new_pathに対してロック取得を試み、成功すれば現在のロックを解放してから
        config.DB_PATHをnew_pathへ切り替える共通処理。on_switch_database()・
        on_restore_from_backup()から使う（on_create_database()の前月引き継ぎ
        分岐は、config.DB_PATHの切り替えタイミングがcarry_over_incomplete_lots()
        の契約と密接に絡むため、ここでは共通化せず個別に処理している）。

        失敗時（他者が有効なロックを保持中）はエラーダイアログで使用者情報を
        表示してFalseを返す。呼び出し元はこれ以上処理を進めないこと。
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
        """
        ローカルdb/フォルダ配下の月別DBを削除する
        （ui.db_delete_helper.confirm_and_delete_database()、安全対策込み）。
        削除後は一覧（db_folder_combobox）を再取得する。
        """
        folder = self.db_folder_var.get().strip()
        if not folder:
            messagebox.showwarning("警告", "削除するフォルダを選択してください。", parent=self.winfo_toplevel())
            return

        db_path = os.path.join(config.APP_DATA_DIR, "db", folder, "inventory.db")
        if confirm_and_delete_database(self.winfo_toplevel(), db_path, current_worker=self.current_worker):
            self._load_db_folders()
            # 削除対象は常に現在接続中のDB以外（is_current_database()が拒否する
            # ため、現在のDBを削除できることは無い）だが、既定DB判定・在庫値
            # 出力済み警告の再評価を一貫して行うため、他の変更操作と同じく
            # ここでも呼んでおく（現在のconfig.DB_PATHは変化しないため実質的な
            # 変化は無い想定だが、再評価のタイミングを一元化する方針に合わせる）。
            self._update_current_db_label()

    def on_restore_from_backup(self):
        """
        バックアップ済みの月次DB（.db）を選び、新しいローカルDBフォルダへ
        取り込んで切り替える（メインメニュー整理、共有フォルダ3ボタン廃止に
        伴う代替機能。CANONICAL_DESIGN_DECISIONS.md D-50参照）。

        旧方針（D-45、「共有フォルダのDBを開く」機能をバックアップの復元に
        転用する）は、バックアップ原本がそのまま本番DBになってしまう・
        マスタDBは復元されない・D-20（ローカル+バックアップ方針）と整合
        しないという3点の問題があったため、本機能（新フォルダへコピーして
        から切り替える、原本・元のDBとも不変）に置き換えた。

        処理の流れ：
        a. ファイル選択ダイアログで.dbファイルを選ぶ。
        b. services.backup_service.validate_monthly_db_backup()で妥当性
           チェック（SQLiteとして開ける・PRAGMA integrity_check・月次DBの
           テーブル構成であること）。マスタDBのバックアップ・無関係な
           ファイルはここで理由付きで拒否する。
        c. 取り込み先フォルダ名を入力させる（既定値はバックアップ選択時刻
           から生成。検証はon_create_database()と同じ規則＝非空・重複禁止）。
        d. services.backup_service.restore_backup_as_new_local_db()で
           sqlite3.Connection.backup()経由でコピーする（原本は読み取り専用
           接続のみで開かれ、一切変更されない）。
        e. _try_switch_db_path()で取り込んだDBへ切り替え、
           init_kitting_plan_tables()で古いバックアップでもテーブル構成を
           最新化する（on_create_database()の新規作成分岐と同じ考え方）。
        f. 完了メッセージに「元のDBは残っており『切り替え』で戻せること」
           「マスタデータは対象外であること」を明記する。

        失敗時（妥当性チェック不合格・コピー中の例外・ロック取得失敗）は、
        作成途中のフォルダ（存在すれば）を削除し、現在のDB・ロックは
        変更しない。
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
            # "db"という名前は、config.is_default_db()（親フォルダ名が"db"かどうか
            # で既定DBを判定する仕組み）と衝突するため予約する（CANONICAL_
            # DESIGN_DECISIONS.md D-5x参照）。
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
        新しいデータベースを作成する。「前月から未完了分を引き継ぐ」チェックが
        OFFの場合は従来通り同期的にまっさらなDBを作成する。

        ONの場合、services.db_migration_carryover.carry_over_incomplete_lots()
        （旧DBの未完了ロットの計画・実績を新DBへコピーする処理。件数によっては
        時間がかかり得る）を、ui.kitting_plan_import.KittingPlanImportWindow等と
        同じ非同期パターン（LoadingWindow＋threading.Thread(daemon=True)＋
        queue.Queue＋self.after(200, ...)ポーリング）で実行し、UIスレッドを
        ブロックしないようにする。
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

        # 新DBは直前にinit_database_at()で作成したばかりのフォルダのため、通常は
        # ロック取得に失敗することはないが、念のため他パスと同様に確認する
        # （万一失敗した場合、新DBファイル自体は作成済みだが未使用のまま残る）。
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
            messagebox.showinfo("完了", "新しいデータベースを作成しました。", parent=self.winfo_toplevel())
            self._load_db_folders()
            self.db_folder_var.set(folder)
            self.new_db_folder_var.set("")
            return

        # 引き継ぎあり：init_kitting_plan_tables()は新DB側でcarry_over_incomplete_lots()
        # 内のcreate_plan_batch()等が最初に呼ばれた時点で自動的に初期化されるため、
        # ここで個別に呼ぶ必要はない。
        self._set_menu_enabled(False)
        self._create_db_loading_window = LoadingWindow(self, message="前月からの未完了分を引き継いでいます…")

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
        # carry_over_incomplete_lots()は成否に関わらず、戻る時点でconfig.DB_PATHを
        # 必ずnew_db_pathにする契約（services/db_migration_carryover.py参照）のため、
        # success/failureどちらの分岐でもラベルを更新する。
        self._update_current_db_label()

        if success:
            summary = payload["summary"]
            folder_name = payload["folder"]
            failed_lot_nos = summary.get("failed_lot_nos") or []
            skipped_lot_nos = summary.get("skipped_lot_nos") or []

            # config.DB_PATHはこの時点で既にnew_db_pathへ切り替わっている
            # （carry_over_incomplete_lots()の契約、上のコメント参照）ため、
            # ここでlog_operation()を呼ぶと新DB側のoperation_logに記録される
            # （旧DB側には一切書き込まれない）。
            log_operation(
                self.current_worker.get("name", "unknown"),
                "未完了計画のDB間引き継ぎ",
                detail=f"成功{summary['lots_copied']}件 / スキップ{len(skipped_lot_nos)}件 / "
                       f"失敗{len(failed_lot_nos)}件",
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
        """
        月次DB（config.DB_PATH）・マスタDB（config.MASTER_DB_PATH）を、選択した
        フォルダへ同時にバックアップする（2026-10-02追加）。

        保存先フォルダはfiledialog.askdirectory()で選択させる。バックアップ処理
        （services.backup_service.backup_databases()、sqlite3.Connection.backup()
        経由）は、on_create_database()の引き継ぎ処理と同じ非同期パターン
        （LoadingWindow＋threading.Thread(daemon=True)＋queue.Queue＋
        self.after(200, ...)ポーリング）で実行し、UIスレッドをブロックしない。
        バックアップ中は他のDB操作ボタンもあわせて無効化する。
        """
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
        バックアップされたマスタDB（master_backup_*.db）から、現在のmaster.db
        （config.MASTER_DB_PATH）に不足しているレコードだけを取り込む
        （2026-10-03追加）。

        admin限定操作（判断の理由）：workersテーブルを含むマスタデータ全般に
        影響する操作であり、特にworkers（ログイン可能な作業者とその役割）に
        他PCのadmin・operatorが追加され得るため、作業者管理画面（編集・有効/
        無効切替）と同じ基準でadmin限定とした。メインメニュー側でadmin以外には
        このボタン自体を表示しない設計だが、本メソッド自体にも念のため同じ
        チェックを入れる（直接呼び出された場合への対策、ui/worker_management_
        window.py::_require_admin()と同じ考え方）。

        ファイル選択はfiledialog.askopenfilename()、実行は既存の非同期パターン
        （LoadingWindow＋threading.Thread(daemon=True)＋queue.Queue＋
        self.after(200, ...)ポーリング、on_backup_databases()と同じ構造）で
        行い、UIスレッドをブロックしない。実行中は他のDB操作ボタンもあわせて
        無効化する。
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