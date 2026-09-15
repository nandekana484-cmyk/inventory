import os
import queue
import socket
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkinter.font as tkfont
import config
from db.init_db import init_database_at
from models.kitting_plan import init_kitting_plan_tables
from services.db_lock_service import (
    acquire_lock, release_lock, update_heartbeat, get_lock_info, LockFileCorruptedError,
)
from ui.master_management import MasterManagementWindow
from ui.kitting_plan_import import KittingPlanImportWindow
from ui.kitting_production_entry import KittingProductionEntryWindow
from ui.loading_window import LoadingWindow
from ui.inventory_input_window import InventoryInputWindow
from ui.theoretical_inventory_import_window import TheoreticalInventoryImportWindow
from ui.inventory_diff_window import InventoryDiffWindow
from ui.master_import_window import MasterImportWindow
from ui.ng_input_window import NgInputWindow
from ui.parts_attributes_import_window import PartsAttributesImportWindow
from ui.board_structure_import_window import BoardStructureImportWindow
from ui.wip_expansion_window import WipExpansionWindow
from ui.worker_management_window import WorkerManagementWindow
from ui.shared_db_list_window import SharedDbListWindow
from ui.db_delete_helper import confirm_and_delete_database
from services.db_migration_carryover import carry_over_incomplete_lots
from services.unprocessed_check_service import check_unprocessed_items
from services.app_settings_service import load_last_db_path


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

        # 起動時点のconfig.DB_PATHに対してロックを取得できなければ、他PC・他ユーザーが
        # 使用中とみなしてここで起動を中断する（以降のUI構築は行わない）。
        # LoginWindow側は self.winfo_exists() を見てから mainloop() を呼ぶ想定
        # （destroy済みのTkルートでmainloop()を呼ばないようにするため）。
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

        # 共有フォルダ（UNCパス等）上のDBを直接開く／新規作成する2ボタン。
        # 以前は「データベース選択」領域内の2段目（db_select_row2）に置かれて
        # いたが、メインメニューの右上に配置する方針に変更した。side=tk.RIGHTで
        # ヘッダー行の右端へパックする（先に「新規作成」を、後から「開く」を
        # packすることで、右端から見て「開く」が左・「新規作成」が右という
        # 従来通りの左右順序になる）。
        self.btn_create_shared_database = ttk.Button(
            header_frame, text="共有フォルダに新規作成", command=self.on_create_shared_database,
        )
        self.btn_create_shared_database.pack(side=tk.RIGHT)

        self.btn_open_shared_database = ttk.Button(
            header_frame, text="共有フォルダのDBを開く", command=self.on_open_shared_database,
        )
        self.btn_open_shared_database.pack(side=tk.RIGHT, padx=(0, 10))

        self.btn_shared_db_list = ttk.Button(
            header_frame, text="共有フォルダのDB一覧", command=self.open_shared_db_list,
        )
        self.btn_shared_db_list.pack(side=tk.RIGHT, padx=(0, 10))

        # 現在接続中のDBパスを常時表示する行（ヘッダー直下）。ローカル・共有フォルダ
        # （UNC）のどちらでも、切り替え操作のたびに最新のフルパスへ更新される
        # （_update_current_db_label()参照。呼び出し箇所：起動時のこの直後、
        # _try_switch_db_path()＝on_switch_database()・_switch_to_shared_db()
        # （on_open_shared_database()・SharedDbListWindow「このDBに切り替える」共通）・
        # on_create_shared_database()共通、on_create_database()の両分岐）。
        # 長いUNCパスでウィンドウの横幅が押し広げられてレイアウトが崩れないよう、
        # _truncate_path_for_display()で必要に応じて中央を省略表示する
        # （フルパスは自己管理のself._current_db_full_pathに保持）。
        db_path_frame = ttk.Frame(self, padding=(10, 0, 10, 8))
        db_path_frame.pack(fill=tk.X)
        ttk.Label(db_path_frame, text="接続中のデータベース：", font=("Helvetica", 9)).pack(side=tk.LEFT)
        self._current_db_full_path = ""
        self.lbl_current_db_path = ttk.Label(db_path_frame, text="-", font=("Helvetica", 9), foreground="#333333")
        self.lbl_current_db_path.pack(side=tk.LEFT)
        self._update_current_db_label()

        # メニューボタン領域
        # 月次データ（config.DB_PATH切り替えの対象＝月ごとのDBフォルダに入っている
        # データ：キッティング計画・生産実績・在庫関連）と、共通マスタ（作業者・
        # 部品マスタ等）を左右2列に分けて表示する（共通マスタを左、月次データを右）。
        # 注：現状は月次・共通いずれのテーブルも同一のDBファイル（config.DB_PATH）に
        # 同居しており、DB切り替え時は両方まとめて切り替わる（ファイルレベルでの
        # 分離は無い）。ここでの区分けはあくまでデータの性質によるUI上の整理であり、
        # 実際に別ファイルに分かれているわけではない。
        body_frame = ttk.Frame(self, padding=20)
        body_frame.pack(expand=True, fill=tk.BOTH)

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

        # PDFから読み取った96コード一覧をinventory_stockと照合し、当部署の
        # 部品だけを抽出する機能。既存の月次データ項目（1〜7）は番号が既に
        # ドキュメント・利用者に定着しているため、割り込ませて振り直すのではなく
        # 末尾に追加する形にした（在庫値入力・在庫値出力と同じinventory_stockを
        # 扱う点では関連が深いが、月をまたいで使い回すマスタ的な性質ではなく
        # 都度のPDF取込という運用データのため、共通マスタ側ではなくこちらに配置）。
        btn_pdf_ocr_import = ttk.Button(
            monthly_frame, text="8. PDF読み取り（在庫照合）", command=self.open_pdf_ocr_import
        )
        btn_pdf_ocr_import.pack(fill=tk.X, pady=5)

        ttk.Label(master_frame, text="共通マスタ", font=("Helvetica", 11, "bold")).pack(anchor=tk.W, pady=(0, 5))

        btn_board_structure_import = ttk.Button(
            master_frame, text="1. 構成基板数マスター", command=self.open_board_structure_import
        )
        btn_board_structure_import.pack(fill=tk.X, pady=5)

        btn_parts_attributes_import = ttk.Button(
            master_frame, text="2. 基板丁数マスター", command=self.open_parts_attributes_import
        )
        btn_parts_attributes_import.pack(fill=tk.X, pady=5)

        btn_worker_management = ttk.Button(
            master_frame, text="3. 作業者管理", command=self.open_worker_management
        )
        btn_worker_management.pack(fill=tk.X, pady=5)

        btn_master = ttk.Button(master_frame, text="4. マスターデータ管理", command=self.open_master_management)
        btn_master.pack(fill=tk.X, pady=5)

        btn_master_import = ttk.Button(
            master_frame, text="5. マスターインポート", command=self.open_master_import
        )
        btn_master_import.pack(fill=tk.X, pady=5)

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
        self._menu_widgets = [
            self.db_folder_combobox, self.btn_switch_database, self.entry_new_db_folder,
            self.chk_carry_over, self.btn_create_database,
            self.btn_open_shared_database, self.btn_create_shared_database,
            btn_kitting_import, btn_kitting_production, btn_inventory_input,
            btn_theoretical_import, btn_inventory_diff, btn_ng_input, btn_wip_expansion,
            btn_pdf_ocr_import,
            btn_master, btn_master_import, btn_parts_attributes_import,
            btn_worker_management, btn_board_structure_import, btn_logout,
        ]

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

        self.db_folder_combobox（state="readonly"が通常の有効状態。ttk.Comboboxは
        NORMALにすると自由入力が可能になってしまうため、readonly/disabledの
        2状態で切り替える）だけ特別扱いする。
        """
        for widget in self._menu_widgets:
            if widget is self.db_folder_combobox:
                widget.config(state="readonly" if enabled else "disabled")
            else:
                widget.config(state=tk.NORMAL if enabled else tk.DISABLED)

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

    def open_master_management(self):
        self._open_singleton_window(
            "master_management", lambda: MasterManagementWindow(self, self.current_worker)
        )

    def open_kitting_plan_import(self):
        self._open_singleton_window(
            "kitting_plan_import", lambda: KittingPlanImportWindow(self, self.current_worker)
        )

    def open_kitting_production_entry(self):
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
          フラグで二重にスレッドを起こさないようにする。
        """
        key = "kitting_production_entry"
        existing = self._open_windows.get(key)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            existing.load_plan_list()
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

        self.after(200, _poll)

    def open_inventory_input(self):
        self._open_singleton_window("inventory_input", lambda: InventoryInputWindow(self))

    def open_theoretical_inventory_import(self):
        self._open_singleton_window(
            "theoretical_inventory_import", lambda: TheoreticalInventoryImportWindow(self)
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

        self._open_singleton_window("inventory_diff", lambda: InventoryDiffWindow(self))

    def open_master_import(self):
        self._open_singleton_window("master_import", lambda: MasterImportWindow(self))

    def open_ng_input(self):
        self._open_singleton_window("ng_input", lambda: NgInputWindow(self, self.current_worker))

    def open_wip_expansion(self):
        self._open_singleton_window("wip_expansion", lambda: WipExpansionWindow(self, self.current_worker))

    def open_pdf_ocr_import(self):
        """循環import回避のため、ここで都度importする。"""
        from ui.pdf_ocr_import_window import PdfOcrImportWindow
        self._open_singleton_window("pdf_ocr_import", lambda: PdfOcrImportWindow(self))

    def open_parts_attributes_import(self):
        self._open_singleton_window(
            "parts_attributes_import", lambda: PartsAttributesImportWindow(self)
        )

    def open_board_structure_import(self):
        self._open_singleton_window(
            "board_structure_import", lambda: BoardStructureImportWindow(self)
        )

    def open_worker_management(self):
        self._open_singleton_window("worker_management", lambda: WorkerManagementWindow(self))

    def on_logout(self):
        """
        ログアウトし、ログイン画面に戻る。

        開いている子ウィンドウ（_open_windowsで管理している多重表示防止対象、
        および対象外のDailyReportWindow/MonthlyReportWindow/UnmatchedProductionWindow等）は、
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
        反映する。

        呼び出し箇所：__init__()の初回表示に加え、config.DB_PATHを変更する
        全ての操作の後（_try_switch_db_path()＝on_switch_database()・
        _switch_to_shared_db()（on_open_shared_database()・SharedDbListWindow
        「このDBに切り替える」共通）・on_create_shared_database()が共通で経由する、
        on_create_database()の引き継ぎ無し分岐、_poll_create_db_queue()＝
        on_create_database()の引き継ぎ有り分岐の非同期完了時）。
        """
        self._current_db_full_path = config.get_current_db_label()
        self.lbl_current_db_path.config(
            text=self._truncate_path_for_display(self._current_db_full_path)
        )

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
        on_open_shared_database()・on_create_shared_database()から使う
        （on_create_database()の前月引き継ぎ分岐は、config.DB_PATHの切り替え
        タイミングがcarry_over_incomplete_lots()の契約と密接に絡むため、
        ここでは共通化せず個別に処理している）。

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
        if confirm_and_delete_database(self.winfo_toplevel(), db_path):
            self._load_db_folders()

    def _shared_dialog_initial_dir(self) -> str:
        """
        共有フォルダ選択ダイアログの初期ディレクトリ。ドキュメントフォルダが
        存在すればそこを、無ければユーザーのホームディレクトリを使う
        （共有フォルダ自体をブックマークする仕組みは持たないため、実装しやすさ優先）。
        """
        documents = os.path.join(os.path.expanduser("~"), "Documents")
        return documents if os.path.isdir(documents) else os.path.expanduser("~")

    def on_open_shared_database(self):
        """
        共有フォルダ（UNCパス等）上の既存の.dbファイルを直接選択して切り替える。
        ローカルのdb/フォルダ限定だった既存の「切り替え」プルダウンとは別経路で、
        任意のパスをconfig.set_db_path()に渡せるようにする。

        実際の切り替え判定・処理は_switch_to_shared_db()に委ねる
        （ui.shared_db_list_window.SharedDbListWindow「このDBに切り替える」と
        同じ処理を共有するため）。
        """
        selected = filedialog.askopenfilename(
            title="共有フォルダのデータベースファイルを選択",
            initialdir=self._shared_dialog_initial_dir(),
            filetypes=[("SQLite データベース", "*.db"), ("すべてのファイル", "*.*")],
            parent=self.winfo_toplevel(),
        )
        if not selected:
            return

        self._switch_to_shared_db(selected)

    def _switch_to_shared_db(self, path: str) -> bool:
        """
        共有フォルダ上のinventory.dbパス（絶対パス化前でも可）へ切り替える
        共通処理。on_open_shared_database()（ファイル選択ダイアログ経由）と
        ui.shared_db_list_window.SharedDbListWindow「このDBに切り替える」
        （一覧からの選択経由）の両方から、パスの取得元だけを変えて共通で使う。

        戻り値：切り替えが完了した（既に使用中だった場合を含む）ならTrue、
        他者が使用中で切り替えられなかった場合はFalse。
        SharedDbListWindow側は、Trueが返った場合のみ一覧画面を閉じる。
        """
        new_path = os.path.abspath(path)
        if new_path == os.path.abspath(config.DB_PATH):
            messagebox.showinfo("情報", "既にこのデータベースを使用中です。", parent=self.winfo_toplevel())
            return True

        if not self._try_switch_db_path(new_path, error_title="開けません"):
            return False

        messagebox.showinfo("完了", f"データベースを切り替えました：\n{new_path}", parent=self.winfo_toplevel())
        return True

    def open_shared_db_list(self):
        """
        共有フォルダ上の月別DB一覧画面（ui.shared_db_list_window.SharedDbListWindow）を
        開く。一覧から選ばれたDBへの切り替えは_switch_to_shared_db()に委ねる
        （既存のon_open_shared_database()と同じ切り替え経路を再利用する）。
        """
        self._open_singleton_window(
            "shared_db_list", lambda: SharedDbListWindow(self, self._switch_to_shared_db),
        )

    def on_create_shared_database(self):
        """
        共有フォルダ内の新規フォルダにinventory.dbを新規作成して切り替える。
        既存のon_create_database()（ローカルdb/フォルダ限定・前月引き継ぎ対応）とは
        別経路。前月引き継ぎには対応しない（必要な場合はローカルで作成してから
        ファイルごと共有フォルダへ移動する運用を想定）。
        """
        selected_dir = filedialog.askdirectory(
            title="新しいデータベースを作成するフォルダを選択",
            initialdir=self._shared_dialog_initial_dir(),
            parent=self.winfo_toplevel(),
        )
        if not selected_dir:
            return

        new_path = os.path.join(os.path.abspath(selected_dir), "inventory.db")
        if os.path.exists(new_path):
            messagebox.showwarning(
                "警告",
                f"選択したフォルダには既にデータベースが存在します：\n{new_path}",
                parent=self.winfo_toplevel(),
            )
            return

        init_database_at(new_path)

        if not self._try_switch_db_path(new_path, error_title="作成不可"):
            return

        init_kitting_plan_tables()
        messagebox.showinfo("完了", f"新しいデータベースを作成しました：\n{new_path}", parent=self.winfo_toplevel())

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