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
# 読み書きするデータ）の保存先。BASE_DIR（インストール先。.exe化時は
# 典型的にProgram Files配下等を想定）とは意図的に分離する。理由：
#   - Program Files配下は標準ユーザーだと書き込み権限が無いことが多い。
#   - アンインストール・再インストール（バージョンアップ）のたびにインストール
#     フォルダの中身が入れ替えられるのが一般的で、その中にDB等のデータを
#     置いていると再インストールのたびに失われるリスクがある。
# そのため.exe化時は、常に書き込み可能なユーザーごとの領域
# （%LOCALAPPDATA%）配下の専用フォルダに分離する。
# 開発環境では、従来通りBASE_DIR配下のまま（開発時の利便性を優先する方針）。
if IS_FROZEN:
    _local_app_data = os.getenv("LOCALAPPDATA")
    if _local_app_data:
        APP_DATA_DIR = os.path.join(_local_app_data, "InventoryApp")
    else:
        # 通常のWindows環境ではLOCALAPPDATAは必ず設定されているはずだが、
        # 万一取得できない場合のフォールバックとしてBASE_DIRを使う。
        APP_DATA_DIR = BASE_DIR
else:
    APP_DATA_DIR = BASE_DIR

# 初回起動時、専用フォルダが存在しなければ作成する（開発環境ではBASE_DIRと
# 同一のため既に存在しており、実質何もしない）。
os.makedirs(APP_DATA_DIR, exist_ok=True)

# データベースファイルの保存パス
DB_PATH = os.path.join(APP_DATA_DIR, 'db', 'inventory.db')


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
# .exe化時は%LOCALAPPDATA%\InventoryApp\tessdata_local\。.gitignore対象、
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