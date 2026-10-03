import sys
import os
import multiprocessing

# .exe化（PyInstaller、--windowed/console=False）時は、コンソール自体が
# 存在しないため sys.stdout・sys.stderr が None になる。この状態で
# print()・warningsモジュール・logging（ハンドラ未設定時のlastResort）等、
# 標準出力/エラー出力に書き込もうとするコードが一度でも呼ばれると、
# None.write()でAttributeErrorが発生する。本アプリはトップレベル例外
# ハンドラを持たない設計のため（既知の制約、UI_WORKFLOW_FIXES_NOTES.md
# 参照）、この例外によりアプリ全体が起動直後に無言でクラッシュする
# （.exe化の動作確認で実際にこの現象を確認した：console=Trueでビルドすると
# 正常起動するが、console=Falseでは即座に終了し、ウィンドウも一切表示
# されない）。ダミーの書き込み先に差し替えることで回避する。開発環境・
# console=Trueビルドではsys.stdout/stderrは元々Noneではないため、この分岐は
# 何もしない（通常のprint()・標準エラー出力の挙動に影響しない）。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

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
