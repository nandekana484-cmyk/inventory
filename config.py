import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# DBパス設定
DB_PATH = os.path.join(BASE_DIR, 'db', 'inventory.db')

# フォルダパス設定
LOG_DIR = os.path.join(BASE_DIR, 'logs')
EXPORT_DIR = os.path.join(BASE_DIR, 'exports')
IMPORT_DIR = os.path.join(BASE_DIR, 'imports')

DEBUG = True
