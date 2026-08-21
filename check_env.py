import sys
import platform
import sqlite3
import importlib

def check_environment():
    print("=" * 50)
    print("      部品在庫管理アプリ - 開発環境チェック")
    print("=" * 50)

    # 1. OS & Python バージョン
    print(f"[OS]             : {platform.system()} {platform.release()} ({platform.architecture()[0]})")
    print(f"[Python Exec]    : {sys.executable}")
    print(f"[Python Version] : {platform.python_version()}")
    
    # Python バージョン判定 (3.10以上推奨)
    if sys.version_info >= (3, 10):
        print("                 └─ OK: 推奨バージョンです (3.10+)")
    else:
        print("                 └─ WARN: 3.10以上を推奨します")
    print("-" * 50)

    # 2. 標準ライブラリ (tkinter, sqlite3) の確認
    print("[標準ライブラリチェック]")
    
    # tkinter (GUI)
    try:
        import tkinter
        print(f"  - tkinter      : OK (Tcl/Tk Ver: {tkinter.Tcl().eval('info patchlevel')})")
    except ImportError:
        print("  - tkinter      : NG (Pythonインストール時にTcl/Tkが含まれていません)")

    # sqlite3 (DB)
    print(f"  - sqlite3      : OK (SQLite Engine Ver: {sqlite3.sqlite_version})")
    print("-" * 50)

    # 3. 外部ライブラリ (requirements.txt に記載のパッケージ) のチェック
    print("[外部ライブラリ (pip) チェック]")
    packages = [
        "pandas",
        "openpyxl",
        "pytest",
        "dotenv",        # python-dotenv
        "fastapi",
        "uvicorn",
        "PyInstaller"
    ]

    for pkg in packages:
        try:
            mod = importlib.import_module(pkg)
            version = getattr(mod, "__version__", "Ver不明")
            print(f"  - {pkg:<14}: OK (Ver: {version})")
        except ImportError:
            print(f"  - {pkg:<14}: 未インストール (pip install が必要です)")

    print("=" * 50)

if __name__ == "__main__":
    check_environment()
