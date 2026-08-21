import sys
import os

# プロジェクトルート（inventory_app）をモジュール検索パスに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.workers import create_worker, get_active_workers
from models.parts import add_part, get_part_by_id

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

    # 部品登録テスト
    try:
        add_part("REEL-001", "C96-12345", "RESISTOR", "A-01", "リール")
        print("[OK] 部品登録成功 (REEL-001)")
    except Exception as e:
        print(f"[SKIP] 部品登録 (登録済み等): {e}")

    part = get_part_by_id("REEL-001")
    print(f"[DATA] 取得した部品情報: {part}")
    print("==========================================")

if __name__ == "__main__":
    test_run()
