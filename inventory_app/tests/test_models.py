import sys
import os

# プロジェクトルート（inventory_app）をモジュール検索パスに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.workers import create_worker, get_active_workers

def test_run():
    print("==========================================")
    print("      モデル動作確認テスト実行")
    print("==========================================")
    
    # 作業者登録テスト
    try:
        create_worker("W001", "山田 太郎", "admin")
        print("[OK] 作業者登録成功 (W001: 山田 太郎)")
    except Exception as e:
        print(f"[SKIP] 作業者登録 (登録済み等): {e}")

    workers = get_active_workers()
    print(f"[DATA] 有効な作業者一覧: {workers}")
    print("==========================================")

if __name__ == "__main__":
    test_run()
