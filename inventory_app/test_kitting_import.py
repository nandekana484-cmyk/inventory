import sys
import tempfile
import os

sys.path.append(r"C:\work\inventory\inventory_app")

# 実DB（config.DB_PATHのデフォルト＝inventory_app/db/inventory.db）を汚さないよう、
# CSVインポート機能を呼び出す前に一時DBへ差し替える。
# create_plan_batch()（services.kitting_import_service経由で呼ばれる）が
# 初回呼び出し時にinit_kitting_plan_tables()でテーブルを自動作成するため、
# 一時DBファイルは空のまま（事前初期化不要）で問題ない。
import config
_tmp_dir = tempfile.mkdtemp(prefix="test_kitting_import_")
config.DB_PATH = os.path.join(_tmp_dir, "test_kitting_import.db")

from services.kitting_import_service import import_kitting_plan_csv

batch_id, inserted_count = import_kitting_plan_csv(
    r"C:\work\inventory\inventory_app\imports\test_kitting_small.csv",
    worker_id="TEST_USER"
)

print("batch_id:", batch_id)
print("inserted_count:", inserted_count)
print("(このテスト実行は一時DBに対して行われました:", config.DB_PATH, ")")
