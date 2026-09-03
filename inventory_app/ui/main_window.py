import os
import queue
import threading
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
from ui.parts_attributes_import_window import PartsAttributesImportWindow
from ui.board_structure_import_window import BoardStructureImportWindow
from ui.wip_expansion_window import WipExpansionWindow
from ui.worker_management_window import WorkerManagementWindow
from services.db_migration_carryover import carry_over_incomplete_lots


class MainWindow(tk.Tk):
    def __init__(self, current_worker):
        super().__init__()
        self.current_worker = current_worker
        self.title("部品在庫管理アプリ - メインメニュー")
        # 月次データ・共通マスタを左右2列表示にしたことで縦に短くなった分、
        # ウィンドウの高さは詰め、横幅は上部のデータベース選択欄（前月引き継ぎ
        # チェックボックス等を含む）と左右2列のボタン群の両方が収まる幅に広げた
        # （winfo_reqwidth()実測値920前後に基づく）。
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
        db_select_frame = ttk.Labelframe(self, text="データベース選択", padding=10)
        db_select_frame.pack(fill=tk.X, padx=10, pady=(10, 0))

        self.db_folder_var = tk.StringVar()
        self.db_folder_combobox = ttk.Combobox(
            db_select_frame, textvariable=self.db_folder_var, state="readonly", width=30
        )
        self.db_folder_combobox.pack(side=tk.LEFT, padx=(0, 10))
        self._load_db_folders()

        self.btn_switch_database = ttk.Button(db_select_frame, text="切り替え", command=self.on_switch_database)
        self.btn_switch_database.pack(side=tk.LEFT)

        ttk.Separator(db_select_frame, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=10)

        ttk.Label(db_select_frame, text="新規フォルダ名：").pack(side=tk.LEFT)
        self.new_db_folder_var = tk.StringVar()
        self.entry_new_db_folder = ttk.Entry(db_select_frame, textvariable=self.new_db_folder_var, width=15)
        self.entry_new_db_folder.pack(side=tk.LEFT, padx=(0, 10))

        self.carry_over_var = tk.BooleanVar(value=False)
        self.chk_carry_over = ttk.Checkbutton(
            db_select_frame, text="前月(現在のDB)から未完了分を引き継ぐ", variable=self.carry_over_var,
        )
        self.chk_carry_over.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_create_database = ttk.Button(
            db_select_frame, text="新しいデータベースを作成", command=self.on_create_database,
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
            btn_kitting_import, btn_kitting_production, btn_inventory_input,
            btn_theoretical_import, btn_inventory_diff, btn_ng_input, btn_wip_expansion,
            btn_master, btn_master_import, btn_parts_attributes_import,
            btn_worker_management, btn_board_structure_import, btn_logout,
        ]

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
        self._open_singleton_window("inventory_diff", lambda: InventoryDiffWindow(self))

    def open_master_import(self):
        self._open_singleton_window("master_import", lambda: MasterImportWindow(self))

    def open_ng_input(self):
        self._open_singleton_window("ng_input", lambda: NgInputWindow(self, self.current_worker))

    def open_wip_expansion(self):
        self._open_singleton_window("wip_expansion", lambda: WipExpansionWindow(self, self.current_worker))

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

        self.current_worker = None
        self.destroy()

        from ui.login_window import LoginWindow
        LoginWindow().mainloop()

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
            messagebox.showwarning("警告", "切り替え先のフォルダを選択してください。", parent=self.winfo_toplevel())
            return

        config.set_db_path(os.path.join(config.BASE_DIR, "db", folder, "inventory.db"))
        messagebox.showinfo("完了", "データベースを切り替えました。", parent=self.winfo_toplevel())

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

        new_db_path = os.path.join(config.BASE_DIR, "db", folder, "inventory.db")
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

        if not carry_over:
            config.set_db_path(new_db_path)
            init_kitting_plan_tables()
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

        if success:
            summary = payload["summary"]
            folder_name = payload["folder"]
            msg = (
                "新しいデータベースを作成しました。\n"
                f"未完了ロット {summary['lots_copied']}件・"
                f"計画行 {summary['kitting_plan_items_copied']}件・"
                f"実績 {summary['production_daily_copied']}件を引き継ぎました。"
            )

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