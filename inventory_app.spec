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
import sys

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
)

block_cipher = None

# PyInstallerは.specファイルをexec()で実行するため__file__が無く、代わりに
# 実行時にSPECPATH（.specファイル自身の場所）を名前空間へ注入する。
PROJECT_ROOT = os.path.abspath(SPECPATH)
APP_DIR = os.path.join(PROJECT_ROOT, "inventory_app")

# バージョン番号・ビルド日付は inventory_app/version.py を唯一の参照元とする
# （二重管理しない）。.exeのファイルバージョン情報（Windowsのプロパティ→詳細
# タブに表示される値）もここから生成する。
sys.path.insert(0, APP_DIR)
from version import APP_VERSION, BUILD_DATE  # noqa: E402

_version_tuple = tuple(int(p) for p in APP_VERSION.split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=_version_tuple,
        prodvers=_version_tuple,
        mask=0x3F,
        flags=0x0,
        OS=0x4,
        fileType=0x1,
        subtype=0x0,
        date=(0, 0),
    ),
    kids=[
        StringFileInfo([
            StringTable("041104B0", [
                StringStruct("CompanyName", ""),
                StringStruct("FileDescription", "部品在庫管理アプリ"),
                StringStruct("FileVersion", f"{APP_VERSION} ({BUILD_DATE})"),
                StringStruct("InternalName", "InventoryApp"),
                StringStruct("OriginalFilename", "InventoryApp.exe"),
                StringStruct("ProductName", "部品在庫管理アプリ"),
                StringStruct("ProductVersion", f"{APP_VERSION} ({BUILD_DATE})"),
            ]),
        ]),
        VarFileInfo([VarStruct("Translation", [1041, 1200])]),
    ],
)

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

# db/schema.sql は db/init_db.py::init_database()/init_database_at() が
# os.path.join(os.path.dirname(__file__), 'schema.sql') で実行時に読む非Pythonの
# データファイルであり、PyInstallerの通常のimport解析では検出・同梱されない
# （2026-10-06、.exeの再ビルド・実機検証中に発見：「新しいデータベースを作成」
# 操作時にFileNotFoundErrorが発生し、新規DBフォルダだけ作成されてinventory.db
# 本体が作られない不具合として判明した。コード自体の既存の振る舞い・バグでは
# なく、.spec側でのバンドル漏れだったため、ここで明示的にdatasへ追加して
# 修正する）。
datas.append((os.path.join(APP_DIR, "db", "schema.sql"), "db"))

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
    version=version_info,
)
