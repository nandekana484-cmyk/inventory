import sys
import os
import multiprocessing

# プロジェクトルートを検索パスに追加
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from ui.login_window import LoginWindow

if __name__ == "__main__":
    # services.pdf_ocr_service._extract_lines_via_ocr()がProcessPoolExecutor
    # でページ単位のOCRを並列化する（2026-09-25追加）ため、Windows上で
    # PyInstallerにより.exe化した際に子プロセスがアプリ自体を無限に再起動
    # しないよう、multiprocessing公式ドキュメントの推奨通りfreeze_support()を
    # 呼ぶ（開発環境（python.exeで直接実行）では何もしない安全な呼び出しで、
    # .exe化後にのみ効果を持つ）。
    multiprocessing.freeze_support()
    app = LoginWindow()
    app.mainloop()
