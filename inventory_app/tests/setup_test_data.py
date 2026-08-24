import sys
import os
import sqlite3

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import DB_PATH

def setup_data():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # マスタ登録
    # 旧BOM（board_definitions / component_groups / component_bom）はフェーズ4で
    # プロジェクトから完全削除されたため、ここでの投入も廃止した。
    # BOMは新BOM基盤（共有フォルダのTSV → services.bom_service / models.bom_master）
    # 側でテストすること。
    cur.execute("INSERT OR REPLACE INTO final_products VALUES ('PROD-A', '制御基板A製品')")

    # ロット登録
    cur.execute("INSERT OR REPLACE INTO lots VALUES ('LOT-202608-01', 'PROD-A', 100, 'active')")

    con.commit()
    con.close()
    print("[OK] テスト用マスタ（製品・ロット）を作成しました。")

if __name__ == '__main__':
    setup_data()
