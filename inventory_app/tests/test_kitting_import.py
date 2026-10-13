"""
計画CSVの取込（services.kitting_import_service.import_kitting_plan_csv()）を、小さなサンプル（imports/test_kitting_small.csv）で確かめる。
以前は inventory_app 直下の単発スクリプト（import した時点で取り込みを実行し、結果を表示するだけ）だった。段階8で tests/ へ移し、
pytest のテストにした。実DBを汚さないよう、一時DBで行う（以前はスクリプトの中で config.DB_PATH を差し替えていた。今は tests/conftest.py が行う）。
create_plan_batch() が初回にテーブルを作るので、一時DBは空のままでよい。
"""
import os

import config
from services.kitting_import_service import import_kitting_plan_csv

SAMPLE_CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "imports", "test_kitting_small.csv")


def test_kitting_import_small_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_kitting_import.db"))
    result = import_kitting_plan_csv(SAMPLE_CSV, worker_id="TEST_USER")

    print("batch_id:", result["batch_id"])
    print("inserted:", result["inserted"])
    print("pending_saved_count:", result["pending_saved_count"])
    print("confirmed_from_pending_count:", result["confirmed_from_pending_count"])
    print("(このテスト実行は一時DBに対して行われました:", config.DB_PATH, ")")

    # 以前のスクリプトを一時DBで実行したときの値（2026-10-13）。
    assert result["batch_id"] == 1
    assert result["inserted"] == 2
    assert result["pending_saved_count"] == 0
    assert result["confirmed_from_pending_count"] == 0
