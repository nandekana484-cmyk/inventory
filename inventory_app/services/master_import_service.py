# services/master_import_service.py
"""
部品マスタ（parts）・BOMマスタ（component_bom）のCSVインポートサービス。

CSVフォーマットが未確定のため、列名ゆらぎ・追加列・欠損列に耐えられる
汎用パーサ（parse_csv_generic）と、列名候補を定義する列名マッピング辞書
（COLUMN_MAP_PARTS / COLUMN_MAP_BOM）を拡張ポイントとして用意する。
"""
import csv

from models.master import upsert_part_master, upsert_bom

# 列名マッピング辞書（拡張ポイント）：canonical key -> 候補列名リスト
COLUMN_MAP_PARTS = {
    "part_no": ["part_no", "code96", "部品番号", "部品ID"],
    "name": ["name", "部品名"],
    "shelf": ["shelf", "棚番"],
}

COLUMN_MAP_BOM = {
    "file_no": ["file_no", "基板番号", "setup_file_no"],
    "part_no": ["part_no", "code96", "部品番号"],
    "qty": ["qty", "usage_qty", "使用数量"],
}

# エンコーディング自動判定の候補（この順で試す）
_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "shift_jis", "cp932"]


def _open_csv_with_fallback(file_path):
    """utf-8-sig → utf-8 → shift_jis → cp932 の順でエンコーディングを判定して開く。"""
    last_error = None
    for encoding in _ENCODINGS_TO_TRY:
        try:
            f = open(file_path, mode="r", encoding=encoding, newline="")
            f.read(2048)
            f.seek(0)
            return f
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError(f"CSVの文字コードを判定できませんでした: {last_error}")


def _resolve_column_map(header, column_map):
    """
    ヘッダー行と列名マッピング辞書から、canonical key -> 実際のCSV列名 の対応を作る。
    候補列名がヘッダーに見つからない canonical key は None（欠損列）とする。
    """
    resolved = {}
    for canonical_key, candidates in column_map.items():
        resolved[canonical_key] = next((c for c in candidates if c in header), None)
    return resolved


def parse_csv_generic(file_path, column_map):
    """
    列名ゆらぎに対応した汎用CSVパーサ（拡張ポイント）。

    column_map（例：COLUMN_MAP_PARTS / COLUMN_MAP_BOM）で指定された
    canonical key ごとに、候補列名リストから実際のCSV列名を解決し、
    各行を以下の形式の dict に変換したリストを返す：

        {
            "<canonical_key>": "値" or None（欠損列 or 空セル）,
            ...,
            "_extra": {"<未マッチの元列名>": "値", ...},
        }

    列名解決のみを行い、必須列チェック・重複検知・型変換などの
    ドメイン固有ロジックは呼び出し側（import_parts_csv / import_bom_csv）が担う。
    追加列（column_map に定義のない列）は "_extra" にそのまま保持する
    （将来の拡張のため）。
    """
    with _open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        resolved = _resolve_column_map(header, column_map)
        matched_columns = {v for v in resolved.values() if v is not None}

        rows = []
        for raw_row in reader:
            row = {}
            for canonical_key, csv_column in resolved.items():
                if csv_column is None:
                    row[canonical_key] = None
                else:
                    value = raw_row.get(csv_column)
                    row[canonical_key] = value.strip() if value is not None else None

            row["_extra"] = {
                k: v for k, v in raw_row.items()
                if k is not None and k not in matched_columns
            }
            rows.append(row)

    return rows


def import_parts_csv(file_path):
    """
    部品マスタCSVを解析し、parts テーブルへ保存する。

    - 必須列：part_no（欠けている・空の行は警告してスキップ）
    - part_no が重複する行は警告してスキップ
    - 追加列は無視する（_extra には保持されるが未使用）

    戻り値：{"rows": 解析した全行, "imported": 取込件数, "warnings": 警告メッセージのリスト}
    """
    rows = parse_csv_generic(file_path, COLUMN_MAP_PARTS)

    imported = 0
    warnings = []
    seen_part_no = set()

    for i, row in enumerate(rows, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
        part_no = row.get("part_no")
        if not part_no:
            warnings.append(f"{i}行目: part_no が空のためスキップしました。")
            continue

        if part_no in seen_part_no:
            warnings.append(f"{i}行目: part_no「{part_no}」が重複しているためスキップしました。")
            continue
        seen_part_no.add(part_no)

        name = row.get("name") or ""
        shelf = row.get("shelf") or ""

        upsert_part_master(part_no, name, shelf)
        imported += 1

    return {"rows": rows, "imported": imported, "warnings": warnings}


def import_bom_csv(file_path):
    """
    BOMマスタCSVを解析する。

    - 必須列：file_no, part_no（欠けている・空の行は警告してスキップ）
    - qty は int に変換（変換不可・空は警告してスキップ）
    - 同一 (file_no, part_no) の重複行は警告してスキップ
    - file_no → component_groups → component_bom の結合ロジックは後日実装のため、
      実際の保存（models.master.upsert_bom）は NotImplementedError を送出する。
      解析・検証・プレビューは今回のCSVインポート機能として完全に動作するが、
      保存のみ結合ロジック確定後まで保留される。

    戻り値：{"rows": 解析した全行, "imported": 取込件数, "warnings": 警告メッセージのリスト}
    """
    rows = parse_csv_generic(file_path, COLUMN_MAP_BOM)

    imported = 0
    warnings = []
    seen_keys = set()

    for i, row in enumerate(rows, start=2):
        file_no = row.get("file_no")
        part_no = row.get("part_no")
        qty_raw = row.get("qty")

        if not file_no or not part_no:
            warnings.append(f"{i}行目: file_no または part_no が空のためスキップしました。")
            continue

        key = (file_no, part_no)
        if key in seen_keys:
            warnings.append(
                f"{i}行目: file_no「{file_no}」・part_no「{part_no}」の組み合わせが"
                "重複しているためスキップしました。"
            )
            continue
        seen_keys.add(key)

        if qty_raw in (None, ""):
            warnings.append(f"{i}行目: qty が空のためスキップしました。")
            continue

        try:
            qty = int(float(qty_raw))
        except (TypeError, ValueError):
            warnings.append(f"{i}行目: qty「{qty_raw}」を数値に変換できないためスキップしました。")
            continue

        try:
            upsert_bom(file_no, part_no, qty)
            imported += 1
        except NotImplementedError:
            warnings.append(
                f"{i}行目: file_no「{file_no}」のBOM保存は、"
                "file_no→component_groups の結合ロジック確定後まで保留されます。"
            )

    return {"rows": rows, "imported": imported, "warnings": warnings}
