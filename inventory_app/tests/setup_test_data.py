import sys
import os
import sqlite3

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from config import DB_PATH

def setup_data():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # マスタ登録
    cur.execute("INSERT OR REPLACE INTO final_products VALUES ('PROD-A', '制御基板A製品')")
    cur.execute("INSERT OR REPLACE INTO board_definitions VALUES ('BOARD-A1', 'PROD-A', 'FILE-01', 2)")
    cur.execute("INSERT OR REPLACE INTO component_groups VALUES ('GRP-A1-1', 'BOARD-A1', 1)")
    cur.execute("INSERT OR REPLACE INTO component_groups VALUES ('GRP-A1-2', 'BOARD-A1', 2)")
    
    # BOM登録 (GRP-A1-1 に C96-12345 を 2個使用)
    cur.execute("INSERT OR REPLACE INTO component_bom (group_id, code96, usage_qty) VALUES ('GRP-A1-1', 'C96-12345', 2)")
    
    # ロット登録
    cur.execute("INSERT OR REPLACE INTO lots VALUES ('LOT-202608-01', 'PROD-A', 100, 'active')")

    con.commit()
    con.close()
    print("[OK] テスト用マスタ（製品・基板・BOM・ロット）を作成しました。")

if __name__ == '__main__':
    setup_data()
