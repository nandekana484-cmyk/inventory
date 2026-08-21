import csv
import os
import sqlite3

from config import DB_PATH
from models.snapshots import insert_snapshot_items


def _get_worker_id(worker):
    """作業者情報をworker_idへ変換する。"""
    if isinstance(worker, dict):
        return worker.get("worker_id") or "SYSTEM"

    if worker:
        return str(worker)

    return "SYSTEM"


def _open_csv_with_fallback(file_path):
    """
    UTF-8 BOM付き、UTF-8、CP932の順でCSVを試す。
    """
    encodings = ["utf-8-sig", "utf-8", "cp932"]

    last_error = None

    for encoding in encodings:
        try:
            f = open(
                file_path,
                mode="r",
                encoding=encoding,
                newline=""
            )

            # ヘッダーを読んで文字コードを確認
            f.read(1024)
            f.seek(0)
            return f

        except UnicodeDecodeError as exc:
            last_error = exc

    raise ValueError(
        f"CSVの文字コードを判定できませんでした: {last_error}"
    )


def _get_value(row, keys):
    """CSV列名候補から最初に見つかった値を取得する。"""
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()

    return ""


def _parse_quantity(value):
    """数量文字列を数値に変換する。"""
    value = str(value).strip().replace(",", "")

    if value == "":
        return 0.0

    try:
        return float(value)
    except ValueError:
        raise ValueError(f"数量が数値ではありません: {value}")


def _load_part_code_map():
    """部品マスタからpart_id→code96の対応を取得する。"""
    con = sqlite3.connect(DB_PATH)

    try:
        rows = con.execute(
            """
            SELECT part_id, code96
            FROM parts
            WHERE is_active = 1
            """
        ).fetchall()

        return {
            str(part_id): str(code96)
            for part_id, code96 in rows
        }

    finally:
        con.close()


def parse_and_import_snapshot(
    file_path: str,
    snapshot_date: str,
    worker=None,
) -> int:
    """
    スナップショットCSVを取り込みます。

    主な対応列：
    - part_id / 部品ID / リールID / 部品番号
    - code96 / 96コード / 部品コード96
    - snapshot_qty / 在庫数 / qty / 数量

    part_idが部品マスタに登録されている場合は、
    parts.code96を正として使用します。
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)

    ext = os.path.splitext(file_path)[1].lower()

    if ext not in [".csv", ".txt"]:
        raise ValueError(
            "現在サポートされている形式はCSVのみです。"
        )

    worker_id = _get_worker_id(worker)
    code_map = _load_part_code_map()

    items = []
    seen_part_ids = set()

    with _open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise ValueError("CSVヘッダーが見つかりません。")

        for line_no, row in enumerate(reader, start=2):
            part_id = _get_value(
                row,
                [
                    "part_id",
                    "partID",
                    "部品ID",
                    "リールID",
                    "部品番号",
                    "part",
                ],
            )

            csv_code96 = _get_value(
                row,
                [
                    "code96",
                    "96コード",
                    "部品コード96",
                    "code",
                ],
            )

            qty_text = _get_value(
                row,
                [
                    "snapshot_qty",
                    "在庫数",
                    "qty",
                    "数量",
                ],
            )

            if not part_id and not csv_code96:
                # 完全空行は無視
                continue

            if part_id and part_id in seen_part_ids:
                raise ValueError(
                    f"{line_no}行目: "
                    f"同じpart_idが重複しています: {part_id}"
                )

            # part_idがある場合、部品マスタのcode96を正とする
            if part_id:
                seen_part_ids.add(part_id)

                master_code96 = code_map.get(part_id)

                if master_code96:
                    code96 = master_code96
                elif csv_code96:
                    # マスタ未登録でもCSVのcode96があれば取り込む
                    code96 = csv_code96
                else:
                    raise ValueError(
                        f"{line_no}行目: "
                        f"part_id={part_id}の96コードが不明です。"
                    )
            else:
                # part_idなしでcode96だけの場合は許可
                code96 = csv_code96

            qty = _parse_quantity(qty_text)

            items.append(
                {
                    "part_id": part_id or None,
                    "code96": code96,
                    "snapshot_qty": qty,
                }
            )

    if not items:
        raise ValueError(
            "取り込む有効なデータが見つかりませんでした。"
        )

    return insert_snapshot_items(
        snapshot_date=snapshot_date,
        items=items,
        source_file=file_path,
        imported_by=worker_id,
    )