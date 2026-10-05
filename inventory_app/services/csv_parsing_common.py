# services/csv_parsing_common.py
"""
列名ゆらぎに対応した汎用CSVパーサ（複数のCSVインポート機能が共有する）。

元は services/master_import_service.py（部品マスタCSVインポート専用）に
置かれていたが、services/production_import_service.py（実績CSV取込）も
同じ parse_csv_generic() を再利用していたため、master_import_service.py
削除時に本モジュールへ切り出した（メインメニュー整理、CANONICAL_DESIGN_DECISIONS.md
D-50参照）。中身（_ENCODINGS_TO_TRY・_open_csv_with_fallback・
_resolve_column_map・parse_csv_generic）は移動前から一切変更していない。
"""
import csv

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

    column_map（例：COLUMN_MAP_PARTS / COLUMN_MAP_PRODUCTION）で指定された
    canonical key ごとに、候補列名リストから実際のCSV列名を解決し、
    各行を以下の形式の dict に変換したリストを返す：

        {
            "<canonical_key>": "値" or None（欠損列 or 空セル）,
            ...,
            "_extra": {"<未マッチの元列名>": "値", ...},
        }

    列名解決のみを行い、必須列チェック・重複検知・型変換などの
    ドメイン固有ロジックは呼び出し側が担う。
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
