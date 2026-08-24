# services/bom_file_service.py
"""
共有フォルダ上のBOM TSVファイルを走査・読み込みするためのファイルアクセス層。

file_no は共有フォルダ内のTSVファイル名（拡張子を除いたファイル名）に対応する、
という前提でインデックスを構築する。
"""
import csv
import glob
import os
from typing import Dict, List, Optional

import config

# エンコーディング自動判定の候補（この順で試す）
_ENCODINGS_TO_TRY = getattr(config, "BOM_ENCODINGS", ["utf-8-sig", "utf-8", "cp932"])

# TSV列名（拡張ポイント）：実TSVフォーマットのヘッダー名ゆらぎが判明した場合は
# ここを調整する。列名そのものは現時点でユーザー指定の3列のみ確定している。
COL_SIDE = "先行面・後行面"
COL_QTY_PER_PRODUCT = "部品員数"
COL_COEFFICIENT = "マスターCHK員数係数"

# 型変換対象の列（拡張ポイント）
_INT_COLUMNS = (COL_SIDE,)
_FLOAT_COLUMNS = (COL_QTY_PER_PRODUCT, COL_COEFFICIENT)


class BOMFileIndex:
    """
    共有フォルダ内の *.tsv を file_no 単位でインデックス化し、読み込む。

    - build_index(): フォルダを走査し file_no -> {"path":, "mtime":} を構築する。
    - read_tsv(file_no): 該当ファイルを読み込み、List[dict] を返す（結果はメモリ内キャッシュする）。
    - list_available_file_nos(): インデックス済みの file_no 一覧を返す。
    """

    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self._index: Dict[str, dict] = {}
        self._cache: Dict[str, List[dict]] = {}
        self.build_index()

    def build_index(self):
        """
        共有フォルダを走査し、file_no → {path, mtime} のインデックスを（再）構築する。
        併せて読み込みキャッシュもクリアする（フォルダの中身が更新された可能性があるため）。

        フォルダが存在しない場合は例外を出さず、空のインデックスとする
        （共有フォルダが一時的にアクセスできない状況でもアプリ自体は起動できるようにするため）。
        """
        self._index = {}
        self._cache = {}

        if not self.folder_path or not os.path.isdir(self.folder_path):
            return

        pattern = os.path.join(self.folder_path, "*.tsv")
        for path in glob.glob(pattern):
            file_no = os.path.splitext(os.path.basename(path))[0]
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            self._index[file_no] = {"path": path, "mtime": mtime}

    def list_available_file_nos(self) -> List[str]:
        """インデックス済みの file_no 一覧（ソート済み）を返す。"""
        return sorted(self._index.keys())

    def _open_with_fallback(self, path: str):
        """utf-8-sig → utf-8 → cp932（config.BOM_ENCODINGS）の順でエンコーディングを判定して開く。"""
        last_error = None
        for encoding in _ENCODINGS_TO_TRY:
            try:
                f = open(path, mode="r", encoding=encoding, newline="")
                f.read(2048)
                f.seek(0)
                return f
            except (UnicodeDecodeError, UnicodeError) as e:
                last_error = e
                continue
        raise ValueError(f"BOM TSVの文字コードを判定できませんでした: {last_error}")

    def read_tsv(self, file_no: str) -> List[dict]:
        """
        file_no に対応するTSVを読み込み、List[dict] を返す。

        - 1行目をヘッダーとして扱う（タブ区切り）
        - 型変換：COL_SIDE → int、COL_QTY_PER_PRODUCT / COL_COEFFICIENT → float
          （変換対象列が空欄の場合は None のまま、値がある場合のみ変換する）
        - 型変換対象外の列も含め、空欄セルはすべて None に変換する
        - 同一 file_no の読み込み結果はメモリ内キャッシュし、以降は再読み込みしない
          （キャッシュをクリアしたい場合は build_index() を呼び直すこと）

        例外：
        - 該当ファイルが見つからない場合 FileNotFoundError
        - 型変換に失敗した場合 ValueError（対象ファイル・行番号・列名を含める）
        """
        if file_no in self._cache:
            return self._cache[file_no]

        entry = self._index.get(file_no)
        if entry is None:
            raise FileNotFoundError(
                f"file_no={file_no!r} に対応するBOM TSVが見つかりません"
                f"（検索フォルダ: {self.folder_path}）。"
            )

        rows: List[dict] = []
        with self._open_with_fallback(entry["path"]) as f:
            reader = csv.DictReader(f, delimiter="\t")
            for line_no, raw_row in enumerate(reader, start=2):  # 1行目はヘッダー
                row = {}
                for key, value in raw_row.items():
                    if key is None:
                        # DictReaderが列数不一致で拾う余剰値（Noneキー）は無視する
                        continue
                    if isinstance(value, str):
                        value = value.strip()
                    row[key] = value if value not in ("", None) else None

                for col in _INT_COLUMNS:
                    if col in row and row[col] is not None:
                        try:
                            row[col] = int(float(row[col]))
                        except (TypeError, ValueError):
                            raise ValueError(
                                f"{os.path.basename(entry['path'])} の{line_no}行目: "
                                f"列「{col}」の値「{row[col]}」を整数に変換できません。"
                            )

                for col in _FLOAT_COLUMNS:
                    if col in row and row[col] is not None:
                        try:
                            row[col] = float(row[col])
                        except (TypeError, ValueError):
                            raise ValueError(
                                f"{os.path.basename(entry['path'])} の{line_no}行目: "
                                f"列「{col}」の値「{row[col]}」を数値に変換できません。"
                            )

                rows.append(row)

        self._cache[file_no] = rows
        return rows
