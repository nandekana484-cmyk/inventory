import sys
import platform
import sqlite3
import importlib


# 今の .exe（inventory_app.spec）が使う部品。ここに無いもの（pandas・openpyxl・fastapi・uvicorn・
# python-dotenv、PDF読み取り用の部品）は、アプリが使わないので確かめない（D-186、D-187）。
RUNTIME_PACKAGES = [
    ("tkcalendar", "tkcalendar"),
    ("babel", "babel"),          # tkcalendar が使う
    ("reportlab", "reportlab"),  # 帳票の PDF 出力
]

# テストと .exe のビルドにだけ要る部品。
DEV_PACKAGES = [
    ("pytest", "pytest"),
    ("PyInstaller", "pyinstaller"),
]


def _check_packages(packages):
    missing = 0
    for module_name, pip_name in packages:
        try:
            mod = importlib.import_module(module_name)
            version = getattr(mod, "__version__", "Ver不明")
            print(f"  - {module_name:<14}: OK (Ver: {version})")
        except ImportError:
            missing += 1
            print(f"  - {module_name:<14}: 未インストール (pip install {pip_name} が必要です)")
    return missing


def check_environment():
    print("=" * 50)
    print("      部品在庫管理アプリ - 開発環境チェック")
    print("=" * 50)

    print(f"[OS]             : {platform.system()} {platform.release()} ({platform.architecture()[0]})")
    print(f"[Python Exec]    : {sys.executable}")
    print(f"[Python Version] : {platform.python_version()}")

    if sys.version_info >= (3, 10):
        print("                 └─ OK: 推奨バージョンです (3.10+)")
    else:
        print("                 └─ WARN: 3.10以上を推奨します")
    print("-" * 50)

    print("[標準ライブラリチェック]")
    try:
        import tkinter
        print(f"  - tkinter      : OK (Tcl/Tk Ver: {tkinter.Tcl().eval('info patchlevel')})")
    except ImportError:
        print("  - tkinter      : NG (Pythonインストール時にTcl/Tkが含まれていません)")
    print(f"  - sqlite3      : OK (SQLite Engine Ver: {sqlite3.sqlite_version})")
    print("-" * 50)

    print("[アプリの実行に必要な部品 (pip)]")
    missing = _check_packages(RUNTIME_PACKAGES)
    print("-" * 50)

    print("[テストと .exe のビルドに必要な部品 (pip)]")
    missing += _check_packages(DEV_PACKAGES)
    print("=" * 50)
    return missing


if __name__ == "__main__":
    sys.exit(1 if check_environment() else 0)
