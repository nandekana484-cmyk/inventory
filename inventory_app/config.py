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