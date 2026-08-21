import sys
import os

# プロジェクトルートを検索パスに追加
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from ui.login_window import LoginWindow

if __name__ == "__main__":
    app = LoginWindow()
    app.mainloop()
