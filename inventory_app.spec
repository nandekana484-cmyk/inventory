# -*- mode: python ; coding: utf-8 -*-
"""
inventory_app.spec

部品在庫管理アプリ（inventory_app）をPyInstallerで.exe化するための設定。

方針（調査結果・ユーザー確定事項に基づく）：
- Tesseract OCR本体・Poppler本体（tessdata_local含む）は同梱しない。PDF読み取り
  機能はOS側に別途インストールされている前提のまま（PDF_OCR_FEATURE_NOTES.md
  §3参照）。コード自体（services/pdf_ocr_service.py等）はそのまま同梱され、
  Tesseract未インストール環境では起動時ではなく、実際にPDF読み取り機能を
  使った時点でエラーメッセージが表示される設計（既存のtry/except、config.py
  の TESSERACT_CMD 等のデフォルト値のみ使用）。
- --onefile形式を採用する。config.py（BASE_DIR算出部）のコメントに
  「PyInstallerのonefile形式では実行のたびにOSの一時フォルダ(sys._MEIPASS)へ
  自己展開してから動くため、__file__ではなくsys.executableを基準にする」と
  明記されており、既存コードが最初からonefile形式を前提に設計されていたため。
  起動速度は--onedirより劣る（毎回の自己展開コストがかかる）が、配布物が
  単一ファイルになる利点を優先した。
- アプリ名・アイコンは、専用のアイコンファイルがリポジトリ内に存在しないため
  デフォルトのまま（未設定）とする。
"""
import os

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# PyInstallerは.specファイルをexec()で実行するため__file__が無く、代わりに
# 実行時にSPECPATH（.specファイル自身の場所）を名前空間へ注入する。
PROJECT_ROOT = os.path.abspath(SPECPATH)
APP_DIR = os.path.join(PROJECT_ROOT, "inventory_app")

# pandas・opencv-python-headless・reportlab・babel（tkcalendarの依存）・
# pdfplumber（pdfminer.six等を内部で使う）は、通常のimport解析だけでは
# 取りこぼされやすいデータファイル・隠れたサブモジュールを持つため、
# collect_all()でデータ・バイナリ・隠れimportをまとめて収集する
# （PyInstaller公式ドキュメント推奨の対応方法）。
datas = []
binaries = []
hiddenimports = []

for pkg in ("pandas", "cv2", "reportlab", "babel", "pdfplumber", "pdfminer", "tkcalendar"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

# requirements.txtに記載の各パッケージ・標準ライブラリのうち、import解析での
# 取りこぼしが起きやすいものを明示的に追加する。
hiddenimports += [
    "sqlite3",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.font",
    "openpyxl",
    "pytesseract",
    "pdf2image",
    "PIL",
    "PIL._tkinter_finder",
    "fastapi",
    "uvicorn",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "dotenv",
]

# Tesseract OCR本体・Poppler本体・tessdata_local（同梱しない方針、モジュール
# docstring参照）はdatas/binariesに含めない。コード（services/pdf_ocr_service.py
# 等）自体はPythonソースとして通常通り同梱される（exclude対象ではない）。

a = Analysis(
    [os.path.join(APP_DIR, "main.py")],
    pathex=[APP_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="InventoryApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
