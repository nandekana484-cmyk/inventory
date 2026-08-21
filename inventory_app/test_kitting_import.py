import sys

sys.path.append(r"C:\work\inventory\inventory_app")

from services.kitting_import_service import import_kitting_plan_csv

batch_id, inserted_count = import_kitting_plan_csv(
    r"C:\work\inventory\inventory_app\imports\test_kitting_small.csv",
    worker_id="TEST_USER"
)

print("batch_id:", batch_id)
print("inserted_count:", inserted_count)
