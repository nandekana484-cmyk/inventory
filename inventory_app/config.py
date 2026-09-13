import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# データベースファイルの保存パス
DB_PATH = os.path.join(BASE_DIR, 'db', 'inventory.db')


def set_db_path(path: str):
    """アプリ全体で使用するDBパスを切り替える唯一の入口。
    直接 config.DB_PATH = ... と代入するのではなく、
    必ずこの関数を経由して切り替えること。"""
    global DB_PATH
    DB_PATH = path


def get_current_db_label() -> str:
    """UI表示用に、現在接続中のDBパスを返す。"""
    return DB_PATH

# 各種フォルダパス
LOG_DIR = os.path.join(BASE_DIR, 'logs')
EXPORT_DIR = os.path.join(BASE_DIR, 'exports')
IMPORT_DIR = os.path.join(BASE_DIR, 'imports')

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
# 追加の言語データを配置できない場合があったため、プロジェクト直下の
# tessdata_local/ に個別配置する運用とした（.gitignore対象、Git管理外。
# 新しい開発環境では jpn.traineddata・eng.traineddata・osd.traineddata を
# 別途このフォルダに配置する必要がある）。環境変数 TESSDATA_DIR で上書き可能。
TESSDATA_DIR = os.getenv(
    'TESSDATA_DIR',
    os.path.join(BASE_DIR, 'tessdata_local'),
)

# pdf2imageがPDFページの画像化に使うPoppler（pdftoppm等）のbinフォルダ。
# WindowsではPATHに追加されないインストール方法もあるため、環境変数
# POPPLER_PATH で明示的に指定できるようにする。未設定（None）の場合は
# services.pdf_ocr_service側でPATH上のpopplerを探しに行く（pdf2imageの
# デフォルト挙動）。
POPPLER_PATH = os.getenv('POPPLER_PATH', None)