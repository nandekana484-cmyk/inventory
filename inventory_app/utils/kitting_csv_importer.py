import csv
import re
from datetime import datetime
from models.production import upsert_production_record

def parse_and_import_kitting_csv(file_path: str, worker_id: str = "SYSTEM"):
    """
    キッティングリストCSV（16列フォーマット）を解析し、生産計画・実績データをDBへ登録する。
    """
    imported_count = 0
    skipped_count = 0

    with open(file_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)

        for row in reader:
            if not row or len(row) < 10:
                continue

            # 16項目のマッピング
            # 0: キッティングリストNo / 1: 削除フラグ / 2: セットアップファイルNo. / 3: ロットNo
            # 4: 実装ライン / 5: 機種基板名 / 6: 数量 / 7: 累計 / 8: 発注数
            # 9: 生産面 / 10: 状態 / 11: 実装開始日時 / 12: 実装終了日時
            # 13: 実装期限 / 14: 実装開始日時(実績) / 15: 実装終了日時(実績)
            
            delete_flag = row[1].strip()
            if delete_flag in ["1", "削除", "TRUE", "true"]:
                skipped_count += 1
                continue

            board_group_id = row[5].strip()
            if not board_group_id:
                continue

            # 数量・累計・発注数の取得
            try:
                plan_qty = float(row[8].strip() or row[6].strip() or 0)  # 発注数（無ければ数量）
                actual_qty = float(row[7].strip() or 0)                  # 累計（実績）
            except ValueError:
                plan_qty, actual_qty = 0.0, 0.0

            # 日付抽出（実装開始日時 または 実装開始日時(実績)）
            raw_date = row[14].strip() if len(row) > 14 and row[14].strip() else row[11].strip()
            match = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", raw_date)
            if match:
                prod_date = match.group(1).replace("/", "-")
            else:
                prod_date = datetime.now().strftime("%Y-%m-%d")

            # DBへ登録・更新
            upsert_production_record(prod_date, board_group_id, plan_qty, actual_qty, worker_id)
            imported_count += 1

    return imported_count, skipped_count

