import os
import sys

# .exe化（PyInstaller）されているかどうかの判定。sys.frozenはPyInstaller等の
# ビルドツールが実行時に設定する属性で、通常の`python main.py`実行では
# 存在しないため、getattr()で安全に判定する。
IS_FROZEN = getattr(sys, "frozen", False)

if IS_FROZEN:
    # .exe化時：__file__ではなくsys.executable（実行ファイル自身のパス）を
    # 基準にする。PyInstallerのonefile形式では、実行のたびにOSの一時フォルダ
    # （sys._MEIPASS）へ自己展開してから動くため、__file__はこの一時フォルダを
    # 指してしまい、起動のたびに（そしてプロセス終了後は）変わってしまう。
    # sys.executableは実際に配置された.exeファイルの場所を指すため安定している。
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    # 開発環境：従来通り、このファイル（config.py）自身の場所を基準にする。
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# アプリ固有データ（ローカルDB・ログ・OCR言語データ・設定ファイル等、実行時に
# 読み書きするデータ）の保存先。
#
# 2026-10-06変更：.exe実行時は%LOCALAPPDATA%\InventoryApp\から「.exe本体が
# 置かれているフォルダ」（BASE_DIR）へ変更した。理由：利用者がデータの場所を
# 把握しやすくし、.exeをフォルダごと移動・複製（コピー運用）できるようにする
# ため。旧方式（%LOCALAPPDATA%、CANONICAL_DESIGN_DECISIONS.md D-48・§19.5）を
# 選んだ当時の理由
# （Program Files配下は標準ユーザーだと書き込み権限が無いことが多い／
# アンインストール・再インストールのたびにインストール先フォルダの中身が
# 入れ替えられる）は、本アプリがインストーラを持たない単一.exe配布（本体を
# 任意の書き込み可能なフォルダに置いて使う運用）であるため、今回の変更でも
# 実質的には再発しない。ただし「書き込み不可なフォルダに置かれる」という
# 問題自体は依然あり得るため、以下で起動時に明示的に検査し、書き込めない
# 場合は（%LOCALAPPDATA%等への自動切り替えは行わず）エラーを表示して終了する
# （.exe実行時のみ。開発環境の保存先・挙動は変更していない）。
APP_DATA_DIR = BASE_DIR

if IS_FROZEN:
    try:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        _write_test_path = os.path.join(APP_DATA_DIR, ".write_test")
        with open(_write_test_path, "w", encoding="utf-8") as _f:
            _f.write("ok")
        os.remove(_write_test_path)
    except OSError:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            "このフォルダにはデータを保存できません。\n"
            "書き込み可能なフォルダへ .exe を移動してください。\n\n"
            f"保存先: {APP_DATA_DIR}",
            "起動エラー",
            0x10,  # MB_ICONERROR
        )
        sys.exit(1)
else:
    # 初回起動時、専用フォルダが存在しなければ作成する（開発環境ではBASE_DIRと
    # 同一のため既に存在しており、実質何もしない）。
    os.makedirs(APP_DATA_DIR, exist_ok=True)

# ローカルDBの親フォルダ名。新規作成・バックアップの呼び出しが組み立てる
# フォルダ名付きDBパスは os.path.join(APP_DATA_DIR, DB_ROOT_FOLDER_NAME, folder,
# 'inventory.db') の形を取るのに対し、既定DB（フォルダ名なし）はこの直下
# （1階層浅い場所）に置かれる。is_default_db()の判定・フォルダ名の予約の
# 両方でこの定数を参照する。
DB_ROOT_FOLDER_NAME = 'db'

# データベースファイルの保存パス
DB_PATH = os.path.join(APP_DATA_DIR, DB_ROOT_FOLDER_NAME, 'inventory.db')

# マスタDB（board_structure_master・parts_attributes・workers・parts・
# final_products）の保存パス。月次DB（DB_PATH）と異なり、set_db_path()による
# 切り替え機構の対象外の固定パス（ローカルに1つ）とする。
MASTER_DB_PATH = os.path.join(APP_DATA_DIR, 'db', 'master.db')


def set_db_path(path: str):
    """アプリ全体で使用するDBパスを切り替える唯一の入口。
    直接 config.DB_PATH = ... と代入するのではなく、
    必ずこの関数を経由して切り替えること。

    切り替えのたびに、選択中のDBパスをservices.app_settings_service経由で
    永続化する（次回起動時、ui.main_window.MainWindow.__init__()が
    load_last_db_path()でこれを読み、前回選択していたDBへ自動的に
    再接続するために使う）。

    services.app_settings_serviceはconfig（このモジュール自身）をimportして
    いるため、モジュールのトップレベルでimportすると循環importになる。
    関数内でのみimportすることで、実際に呼ばれる時点（＝両モジュールの
    読み込みが完了した後）まで解決を遅延させ、これを回避している。"""
    global DB_PATH
    DB_PATH = path

    from services.app_settings_service import save_last_db_path
    save_last_db_path(path)


def get_current_db_label() -> str:
    """UI表示用に、現在接続中のDBパスを返す。"""
    return DB_PATH


def is_default_db() -> bool:
    """
    現在のconfig.DB_PATHが「既定DB」（APP_DATA_DIR/db/inventory.db、
    フォルダ名を挟まない1階層浅いパス）かどうかを判定する。

    既定DBは、起動時にユーザーが一度もDBを選択・新規作成・呼び出し
    （on_switch_database()・on_create_database()・on_restore_from_backup()）
    していない場合に使われる、モジュール読み込み時点のDB_PATHそのもの
    （上記のDB_PATH定義を参照）。これらの操作はいずれも
    os.path.join(APP_DATA_DIR, DB_ROOT_FOLDER_NAME, folder, 'inventory.db')
    という、フォルダ名を1つ挟む形でパスを組み立てるため、親フォルダ名が
    folder名になる。既定DBだけが親フォルダ名が"db"（DB_ROOT_FOLDER_NAME）
    そのものになる、というパスの深さの違いで判定できる（追加の状態保持は
    不要。ファイルが存在するかどうか・データが入っているかどうかは問わない
    ——既定DBは一律「未選択」として扱う方針のため）。

    フォルダ名の予約（"db"という名前のフォルダをユーザーが作成できないよう
    禁止する、ui/main_window.py参照）と組み合わせることで、この判定が
    既定DB以外のケースで誤って成立することを防いでいる。
    """
    return os.path.basename(os.path.dirname(DB_PATH)) == DB_ROOT_FOLDER_NAME

# 各種フォルダパス（DB_PATHと同じ理由でAPP_DATA_DIR基準）
LOG_DIR = os.path.join(APP_DATA_DIR, 'logs')
EXPORT_DIR = os.path.join(APP_DATA_DIR, 'exports')
IMPORT_DIR = os.path.join(APP_DATA_DIR, 'imports')

DEBUG = True

# ========== BOM設定（新規） ==========

# 共有フォルダのBOMデータパス
BOM_FOLDER_PATH = os.getenv(
    'BOM_FOLDER_PATH',
    r'\\192.168.5.151\みんなの広場\【基板実装課-実装技術】\◆標準関連\◆作業指導票\◆WPCSマスタ\◆機種ラインマスタ'
)

# BOM読み込み時のエンコーディング優先順位
BOM_ENCODINGS = ['utf-8-sig', 'utf-8', 'cp932']

# ========== PDF読み取り・OCR設定（新規） ==========

# Tesseract OCR本体（tesseract.exe）のパス。
# pytesseractはOS側にインストール済みのTesseract本体を別途必要とし、PATHに
# 無ければ pytesseract.pytesseract.tesseract_cmd に明示的にフルパスを設定する
# 必要がある。開発環境ではWindows版公式ビルド（UB-Mannheim版、winget経由で
# インストール）のデフォルトインストール先を既定値とし、環境ごとに異なる場合は
# 環境変数 TESSERACT_CMD で上書きできるようにする（BOM_FOLDER_PATHと同じ方針）。
TESSERACT_CMD = os.getenv(
    'TESSERACT_CMD',
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
)

# 日本語言語データ（jpn.traineddata）を含むtessdataディレクトリ。
# 標準インストール先（Program Files配下）は開発環境によって書き込み権限が無く
# 追加の言語データを配置できない場合があったため、APP_DATA_DIR配下の
# tessdata_local/ に個別配置する運用とした（開発環境ではBASE_DIR直下、
# .exe化時は.exe本体のフォルダ配下のtessdata_local\。.gitignore対象、
# Git管理外。新しい開発環境では jpn.traineddata・eng.traineddata・
# osd.traineddata を別途このフォルダに配置する必要がある）。
# 環境変数 TESSDATA_DIR で上書き可能。
TESSDATA_DIR = os.getenv(
    'TESSDATA_DIR',
    os.path.join(APP_DATA_DIR, 'tessdata_local'),
)

# pdf2imageがPDFページの画像化に使うPoppler（pdftoppm等）のbinフォルダ。
# WindowsではPATHに追加されないインストール方法もあるため、環境変数
# POPPLER_PATH で明示的に指定できるようにする。未設定（None）の場合は
# services.pdf_ocr_service側でPATH上のpopplerを探しに行く（pdf2imageの
# デフォルト挙動）。
POPPLER_PATH = os.getenv('POPPLER_PATH', None)