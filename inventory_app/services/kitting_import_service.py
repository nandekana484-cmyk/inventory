# services/kitting_import_service.py
import csv
import os
import traceback
from typing import Callable, Optional

from models.kitting_plan import (
    create_plan_batch, create_plan_version, get_connection,
    upsert_pending_kitting_plan_item, find_pending_kitting_plan_item,
    delete_pending_kitting_plan_item,
)

# 保留（pending_kitting_plan_items）から確定へ引き継ぐ際、現在のCSV行の値が
# 空欄・未設定であれば保留側の値で補う対象フィールド。識別キー自体
# （lot_no・setup_file_no・production_side・order_qty）は一致が前提のため対象外。
_PENDING_MERGE_FIELDS = (
    "mounting_line", "board_name", "planned_qty", "cumulative_qty_external",
    "status", "plan_start_datetime", "plan_end_datetime", "deadline",
    "actual_start_datetime", "actual_end_datetime",
)

def _to_float(val, default=0.0):
    try:
        return float(val.strip())
    except (ValueError, AttributeError):
        return default

def _to_int_flag(val):
    v = (val or "").strip()
    return 1 if v in ["1", "削除", "TRUE", "true"] else 0

def _open_csv_with_fallback_encoding(file_path: str):
    """utf-8-sig -> cp932 の順で試す。成功したエンコーディングでファイルオブジェクトを返す。"""
    encodings_to_try = ["utf-8-sig", "cp932"]
    last_error = None
    for enc in encodings_to_try:
        try:
            with open(file_path, mode="r", encoding=enc) as f:
                f.read(4096)  # 少し読んでデコード可能か確認
            return open(file_path, mode="r", encoding=enc, newline="")
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError(f"CSVの文字コードを判定できませんでした（utf-8-sig / cp932 失敗）。詳細: {last_error}")

def _normalize_row_to_16cols(row):
    """
    CSV 行を16列に正規化して返す。
    - len < 16: 末尾を空文字で埋める
    - len > 16: 16以降を最後の列に結合（CSV内に予期せぬカンマがある場合の保険）
    """
    if row is None:
        return [""] * 16
    # trim whitespace for all fields
    row = [ (c.strip() if c is not None else "") for c in row ]
    if len(row) == 16:
        return row
    if len(row) < 16:
        return row + [""] * (16 - len(row))
    # len(row) > 16: combine extras into last field
    first15 = row[:15]
    rest = row[15:]
    combined_last = ",".join(rest)
    return first15 + [combined_last]

def _merge_from_pending(item: dict, pending_row: dict) -> dict:
    """
    現在のCSV行（item）のうち空欄・未設定のフィールドを、保留行（pending_row）の
    対応する値で補う。識別キー自体は一致が前提のため対象にしない。
    現在行の値が既にあれば、それを優先しそのまま使う（保留側で上書きしない）。
    """
    merged = dict(item)
    for field in _PENDING_MERGE_FIELDS:
        if not merged.get(field) and pending_row.get(field):
            merged[field] = pending_row[field]
    return merged


def import_kitting_plan_csv(
    file_path: str,
    worker_id: str = "SYSTEM",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    batch_size: int = 500
) -> dict:
    """
    安全で堅牢な CSV インポート。
    - progress_callback(current, total) が渡された場合はバッチごとに進捗通知する。

    キッティングNo.（1列目）が空欄の行は、以前は単純に破棄していたが、
    現在はmodels.kitting_plan.pending_kitting_plan_itemsへ保留登録する
    （delete-then-insertではなく、識別キー(lot_no, setup_file_no,
    production_side, order_qty)が一致する既存行があれば上書き、無ければ新規
    追加）。逆に、キッティングNo.が付与された行を処理する際は、その行の
    識別キーが保留テーブルに一致する行として存在するか確認し、存在すれば
    「未確定期間からの確定」として扱う：保留側の行を削除した上で、現在行の
    値（保留側にしか無かった値があれば_merge_from_pending()で補完）を使って
    通常通りcreate_plan_version()で正式登録する。

    戻り値：{
        "batch_id": int,
        "inserted": int,                       # kitting_plan_itemsへ正式登録した件数
        "pending_saved_count": int,             # 保留テーブルへ新規保存・更新した件数
        "confirmed_from_pending_count": int,    # 保留から確定した件数（insertedの内数）
    }
    """
    # 1) 事前行数カウント（正確な進捗表示のため。大ファイルでは時間がかかるが許容）
    total_lines = 0
    try:
        with open(file_path, "rb") as bf:
            for _ in bf:
                total_lines += 1
    except Exception:
        total_lines = 0
    total_data_rows = max(0, total_lines - 1) if total_lines > 0 else 0

    # 2) Open file with encoding fallback
    f = _open_csv_with_fallback_encoding(file_path)
    reader = csv.reader(f)

    # 3) create plan batch
    batch_id = create_plan_batch(source_file=os.path.basename(file_path), imported_by=worker_id, row_count=0)

    inserted = 0
    errors = []
    buffer = []  # buffer not used for SQL here; we still create versions per row but commit in batches

    try:
        header = next(reader, None)
    except StopIteration:
        f.close()
        return {"batch_id": batch_id, "inserted": 0, "pending_saved_count": 0, "confirmed_from_pending_count": 0}

    pending_saved_count = 0
    confirmed_from_pending_count = 0
    source_file_name = os.path.basename(file_path)

    # 4) Process rows streaming
    for idx, raw_row in enumerate(reader, start=1):
        row = _normalize_row_to_16cols(raw_row)
        kitting_no = row[0].strip()

        item = {
            "delete_flag": _to_int_flag(row[1]),
            "setup_file_no": row[2],
            "lot_no": row[3],
            "mounting_line": row[4],
            "board_name": row[5],
            "planned_qty": _to_float(row[6]),
            "cumulative_qty_external": _to_float(row[7]),
            "order_qty": _to_float(row[8]),
            "production_side": row[9],
            "status": row[10],
            "plan_start_datetime": row[11],
            "plan_end_datetime": row[12],
            "deadline": row[13],
            "actual_start_datetime": row[14],
            "actual_end_datetime": row[15],
        }

        if not kitting_no:
            # キッティングNo.未確定：破棄せず保留テーブルへ保存する
            try:
                upsert_pending_kitting_plan_item(
                    {**item, "source_file": source_file_name}, created_by=worker_id,
                )
                pending_saved_count += 1
            except Exception as e:
                tb = traceback.format_exc()
                errors.append({"row": idx, "kitting_list_no": "", "error": str(e), "traceback": tb})
                print(f"[kitting_import] ERROR (pending save) at row {idx}: {e}")
            continue

        item["kitting_list_no"] = kitting_no

        # キッティングNo.が付与された行の識別キーが、以前保留登録された行と
        # 一致するか確認する（「未確定期間からの確定」の検知）。
        pending_match = find_pending_kitting_plan_item(
            item["lot_no"], item["setup_file_no"], item["production_side"], item["order_qty"],
        )
        if pending_match is not None:
            item = _merge_from_pending(item, pending_match)

        try:
            # Create new version (this does an INSERT and handles versioning)
            create_plan_version(plan_batch_id=batch_id, kitting_list_no=item["kitting_list_no"], data=item, created_by=worker_id)
            inserted += 1
            if pending_match is not None:
                # 正式登録が成功した後に保留側を削除する（登録が失敗した場合、
                # 保留行は残し次回の取込で再度確定を試みられるようにする）。
                delete_pending_kitting_plan_item(pending_match["pending_id"])
                confirmed_from_pending_count += 1
        except Exception as e:
            tb = traceback.format_exc()
            errors.append({"row": idx, "kitting_list_no": kitting_no, "error": str(e), "traceback": tb})
            # print minimal log for operator
            print(f"[kitting_import] ERROR at row {idx} (kitting_list_no={kitting_no}): {e}")
            # continue processing other rows

        # commit per batch_size steps to reduce IO and avoid long transactions
        if inserted % batch_size == 0 and inserted > 0:
            # no explicit action needed because create_plan_version commits internally,
            # but we can optionally print progress
            if progress_callback:
                try:
                    progress_callback(inserted, total_data_rows or inserted)
                except Exception:
                    pass

    f.close()

    # 5) update batch row_count
    conn = get_connection()
    with conn:
        cur = conn.cursor()
        cur.execute("UPDATE kitting_plan_batches SET row_count = ? WHERE plan_batch_id = ?", (inserted, batch_id))

    # 6) summary log
    if errors:
        print(f"[kitting_import] completed with {len(errors)} error(s). See first 5 below:")
        for e in errors[:5]:
            print(f" row {e['row']}, kitting_list_no={e['kitting_list_no']}, err={e['error']}")
    else:
        print(f"[kitting_import] completed successfully. inserted: {inserted}")

    if pending_saved_count:
        print(f"[kitting_import] saved {pending_saved_count} row(s) with empty kitting_list_no to pending table.")
    if confirmed_from_pending_count:
        print(f"[kitting_import] confirmed {confirmed_from_pending_count} row(s) from pending table.")

    return {
        "batch_id": batch_id,
        "inserted": inserted,
        "pending_saved_count": pending_saved_count,
        "confirmed_from_pending_count": confirmed_from_pending_count,
    }