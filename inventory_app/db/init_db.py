import sqlite3
import os
import sys

# プロジェクトルート（inventory_app）をモジュール検索パスに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import DB_PATH

def init_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
    
    if not os.path.exists(schema_path):
        print(f"Error: {schema_path} が見つかりません。")
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    
    with open(schema_path, 'r', encoding='utf-8') as f:
        cur.executescript(f.read())
        
    con.commit()
    con.close()
    print(f"データベースの初期化が完了しました: {DB_PATH}")

if __name__ == '__main__':
    init_database()
