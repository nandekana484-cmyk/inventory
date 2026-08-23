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

def init_database_at(db_path: str):
    """
    指定パスに新しい月次データベースを初期化する（DB選択UIの新規作成用）。

    schema.sql による基本テーブル一式と、production_daily の
    plan_item_id / kitting_list_no 列（本来 migration_002 で追加される列）を作成する。
    kitting_plan_batches / kitting_plan_items テーブルは対象外。
    呼び出し側で config.DB_PATH を db_path に切り替えたうえで、
    models.kitting_plan.init_kitting_plan_tables() を別途呼び出すこと。
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')

    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"{schema_path} が見つかりません。")

    con = sqlite3.connect(db_path)
    try:
        with open(schema_path, 'r', encoding='utf-8') as f:
            con.executescript(f.read())

        for ddl in [
            "ALTER TABLE production_daily ADD COLUMN plan_item_id INTEGER",
            "ALTER TABLE production_daily ADD COLUMN kitting_list_no TEXT",
        ]:
            try:
                con.execute(ddl)
            except sqlite3.OperationalError:
                pass

        con.commit()
    finally:
        con.close()


if __name__ == '__main__':
    init_database()
