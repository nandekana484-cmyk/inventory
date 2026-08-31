# services/bom_file_service.py
"""
共有フォルダ上のBOM TSVファイルを走査・読み込みするためのファイルアクセス層。

file_no は共有フォルダ内のTSVファイル名（拡張子を除いたファイル名）に対応する、
という前提でインデックスを構築する。
"""
import csv
import glob
import logging
import os
import re
from typing import Dict, List, Optional

import config

logger = logging.getLogger(__name__)

# エンコーディング自動判定の候補（この順で試す）
_ENCODINGS_TO_TRY = getattr(config, "BOM_ENCODINGS", ["utf-8-sig", "utf-8", "cp932"])

# TSV列名（拡張ポイント）：実TSVフォーマットのヘッダー名ゆらぎが判明した場合は
# ここを調整する。実TSV（共有フォルダ上の実ファイル）で確認済みの列名。
# 以下は canonical（正規化後）の列名。実データには複数の表記ゆれ
# （パターンA/B/C、_COLUMN_ALIASES参照）があるため、read_tsv() で
# 実際の列名を canonical 名に正規化してから返す。
COL_SETUP_FILE_NO = "セットアップファイルNo."
COL_SIDE = "生産面"
COL_QTY_PER_PRODUCT = "部品員数"
COL_COEFFICIENT = "マスターCHK員数係数"
COL_PART_NO = "96コード"
COL_TYPE = "セットアップ部品種別"
COL_R_FLAG = "減数種別"

# 列名のゆらぎ吸収（拡張ポイント）：canonical名 -> 実TSVで観測された候補列名のリスト。
# 実データにパターンA（330件）・パターンB（182件）・A/Bの列が混在するパターンC
# （5件）の3種類の見出し表記ゆれが存在することを確認済み。
# 列ごとに独立してヘッダーに実在する候補を採用するため、パターンCのように
# 同一ファイル内で一部の列がA形式、別の列がB形式、という混在があっても対応できる。
_COLUMN_ALIASES = {
    COL_SETUP_FILE_NO: ["セットアップファイルNo.", "セットアップファイルNo"],
    COL_SIDE: ["生産面", "先行面・後行面"],
    COL_PART_NO: ["96コード", "基板・部品96コード"],
    COL_TYPE: ["セットアップ部品種別", "基板手付け部品"],
    COL_R_FLAG: ["減数種別", "理論・実装吸着数"],
}

# 型変換対象の列（拡張ポイント）
_INT_COLUMNS = (COL_SIDE,)
_FLOAT_COLUMNS = (COL_QTY_PER_PRODUCT, COL_COEFFICIENT)

# ヘッダー破損検知用（拡張ポイント）：列見出しのはずのセルが、数字のみ・
# 6桁以上（96コード等のデータ値の典型的な形）にマッチする場合、
# データ値が列見出しに紛れ込んだ破損ヘッダーとみなす（file_no=235で実例確認済み）。
_CORRUPTED_HEADER_CELL_PATTERN = re.compile(r"^\d{6,}$")


class BOMFileIndex:
    """
    共有フォルダ内の *.tsv を file_no 単位でインデックス化し、読み込む。

    - build_index(): フォルダを走査し file_no -> {"path":, "mtime":} を構築する。
    - read_tsv(file_no): 該当ファイルを読み込み、List[dict] を返す（結果はメモリ内キャッシュする）。
    - list_available_file_nos(): インデックス済みの file_no 一覧を返す。
    - resolve_file_no(setup_file_no): kitting_plan_items.setup_file_no のような
      先頭ゼロ付き表記から、実際の file_no（索引キー）を解決する。
    - problems: 自動解決できなかった／読み込みに失敗した件を記録する辞書
      （キー: 問題を識別する文字列、値: {"type", "candidates", "message"}）。
      build_index() 実行のたびにクリアしてから再構築する。
    """

    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self._index: Dict[str, dict] = {}
        self._cache: Dict[str, List[dict]] = {}
        self.problems: Dict[str, dict] = {}
        self.build_index()

    def build_index(self):
        """
        共有フォルダを走査し、file_no → {path, mtime} のインデックスを（再）構築する。
        併せて読み込みキャッシュもクリアする（フォルダの中身が更新された可能性があるため）。

        フォルダが存在しない場合は例外を出さず、空のインデックスとする
        （共有フォルダが一時的にアクセスできない状況でもアプリ自体は起動できるようにするため）。

        実際の共有フォルダ構成は「file_no（ファイル番号）名のサブフォルダの中に
        同名の *.tsv が入っている」2階層構成のため、直下のサブフォルダを列挙し、
        その中の *.tsv を対象にインデックスを構築する。
        直下に直接 *.tsv が置かれているケースも念のため許容する。
        直下の非フォルダ・非tsvファイル（*.lnk 等）は無視する。

        1つのサブフォルダに複数の *.tsv が存在する場合（例：432フォルダに
        432_A4-1.tsv と 432_A6.tsv の両方がある）は、各ファイルはそれぞれの
        ファイル名で個別に索引登録した上で、サブフォルダ名だけでは一意に
        TSVを特定できない旨を problems に記録する
        （resolve_file_no() がサブフォルダ番号だけで引こうとした際に
        一意に解決できないことを判定するために使う）。
        """
        self._index = {}
        self._cache = {}
        self.problems = {}

        if not self.folder_path or not os.path.isdir(self.folder_path):
            return

        # 直下に直接 *.tsv が置かれているケースを念のため許容する
        for path in glob.glob(os.path.join(self.folder_path, "*.tsv")):
            self._register_tsv(path)

        # 本来の構成：file_no サブフォルダの中の *.tsv
        with os.scandir(self.folder_path) as entries:
            for entry in entries:
                if not entry.is_dir():
                    continue

                tsv_paths = glob.glob(os.path.join(entry.path, "*.tsv"))

                if len(tsv_paths) >= 2:
                    candidates = sorted(
                        os.path.splitext(os.path.basename(p))[0] for p in tsv_paths
                    )
                    self.problems[entry.name] = {
                        "type": "multiple_tsv_in_subfolder",
                        "candidates": candidates,
                        "message": (
                            f"サブフォルダ「{entry.name}」に複数のBOM TSVが存在するため、"
                            f"サブフォルダ番号だけでは一意にTSVを特定できません"
                            f"（候補: {', '.join(candidates)}）。"
                        ),
                    }

                for path in tsv_paths:
                    self._register_tsv(path, subfolder_name=entry.name)

    def _register_tsv(self, path: str, subfolder_name: str = None):
        """
        1件の *.tsv をインデックスへ登録する。

        file_no はサブフォルダ名から取得することを基本とするが、
        サブフォルダ名と実ファイル名（拡張子除く）が一致しない場合は
        ファイル名を優先し、その旨を警告ログに出す。

        同一 file_no に異なるファイルが既に登録済みの場合（索引キー衝突）は、
        後勝ちで黙って上書きせず、両方を候補として problems に記録した上で、
        一意に決まらないため索引からは除外する。
        """
        tsv_basename = os.path.splitext(os.path.basename(path))[0]
        file_no = subfolder_name if subfolder_name is not None else tsv_basename

        if subfolder_name is not None and subfolder_name != tsv_basename:
            logger.warning(
                "BOM TSVのサブフォルダ名とファイル名が一致しません。"
                "ファイル名（%s）を優先します（サブフォルダ名: %s, パス: %s）。",
                tsv_basename, subfolder_name, path,
            )
            file_no = tsv_basename

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return

        existing = self._index.get(file_no)
        if existing is not None and existing["path"] != path:
            candidates = sorted({existing["path"], path})
            self.problems[file_no] = {
                "type": "index_key_collision",
                "candidates": candidates,
                "message": (
                    f"file_no={file_no!r} に複数の異なるファイルが対応しています"
                    f"（{', '.join(candidates)}）。上書きせず要確認とします。"
                ),
            }
            logger.warning(
                "BOM TSVのfile_noが衝突しました。上書きせず索引から除外します: "
                "file_no=%s, 既存パス=%s, 新規パス=%s",
                file_no, existing["path"], path,
            )
            self._index.pop(file_no, None)
            return

        self._index[file_no] = {"path": path, "mtime": mtime}

    def list_available_file_nos(self) -> List[str]:
        """インデックス済みの file_no 一覧（ソート済み）を返す。"""
        return sorted(self._index.keys())

    def resolve_file_no(self, setup_file_no: str) -> Optional[str]:
        """
        kitting_plan_items.setup_file_no のような表記（例："0103"）から、
        実際の file_no（索引キー、例："103"）を解決する。

        解決順序：
          1. 完全一致（setup_file_no がそのまま索引キーに存在する）
          2. 先頭ゼロを除去した番号で、索引キーに完全一致
             または「番号(_接尾辞)」形式で一致するものを探す
             （例："0161" → "161" → 索引キー "161_A8-1" に一致）
          3. 2で候補が2件以上見つかった場合（例："0432" → "432_A4-1"と"432_A6"）は
             一意に決定できないため None を返し、problems に
             "unresolved_multiple_candidates" として記録する。
          4. 候補が1件も見つからない場合は None を返し、problems に
             "tsv_not_found" として記録する（共有フォルダにTSVが未整備なケース）。

        戻り値：解決できた場合は file_no（str）、できない場合は None。
        """
        if not setup_file_no:
            return None

        original = str(setup_file_no).strip()
        if not original:
            return None

        # 1. 完全一致
        if original in self._index:
            return original

        # 2. 先頭ゼロを除去して候補を探す
        normalized = original.lstrip("0") or "0"
        candidates = self._find_candidates(normalized)

        if len(candidates) == 1:
            return candidates[0]

        if len(candidates) >= 2:
            self.problems[original] = {
                "type": "unresolved_multiple_candidates",
                "candidates": candidates,
                "message": (
                    f"setup_file_no={original!r} は複数候補"
                    f"（{', '.join(candidates)}）のため自動解決不可です。"
                ),
            }
            return None

        # 4. 該当なし
        self.problems[original] = {
            "type": "tsv_not_found",
            "candidates": [],
            "message": (
                f"setup_file_no={original!r} に対応するBOM TSVが見つかりません"
                f"（正規化後: {normalized!r}, 検索フォルダ: {self.folder_path}）。"
            ),
        }
        return None

    def _find_candidates(self, normalized_no: str) -> List[str]:
        """
        先頭ゼロ除去後の番号に対応する索引キー候補を返す（ソート済み）。

        「番号そのもの」（例："161"）と「番号_接尾辞」（例："161_A8-1"）の
        両方の索引キー形式を候補として扱う。
        """
        pattern = re.compile(rf"^{re.escape(normalized_no)}(_.*)?$")
        return sorted(file_no for file_no in self._index if pattern.match(file_no))

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

    def _detect_corrupted_header(self, header: List[str]) -> Optional[str]:
        """
        ヘッダー行のセルに、6桁以上の数字のみの値（96コード等のデータ値らしき
        文字列）が含まれる場合、そのセルを返す（無ければ None）。
        """
        for cell in header or []:
            if cell is not None and _CORRUPTED_HEADER_CELL_PATTERN.match(cell.strip()):
                return cell
        return None

    def _resolve_header_aliases(self, header: List[str]) -> Dict[str, str]:
        """
        ヘッダー行の実際の列名から、canonical 名への対応表を作る。

        _COLUMN_ALIASES の候補のうちヘッダーに実在するものだけを対応させる
        （列ごとに独立して判定するため、同一ファイル内で一部の列がA形式、
        別の列がB形式、という混在（パターンC）にも対応できる）。
        候補に一致しない列名はそのまま（正規化不要）とする。
        """
        mapping = {}
        header_set = set(header or [])
        for canonical, candidates in _COLUMN_ALIASES.items():
            for candidate in candidates:
                if candidate in header_set:
                    mapping[candidate] = canonical
                    break
        return mapping

    def read_tsv(self, file_no: str) -> List[dict]:
        """
        file_no に対応するTSVを読み込み、List[dict] を返す。

        - 1行目をヘッダーとして扱う（タブ区切り）
        - ヘッダーの表記ゆれ（COL_SIDE/COL_PART_NO/COL_TYPE/COL_R_FLAG等の
          実際の列名がパターンA/B/Cで異なる）を _resolve_header_aliases() で
          canonical 名に正規化してから行データを組み立てる。
          呼び出し元（bom_service.py）は正規化後の canonical 名のみを見ればよい。
        - ヘッダー行に列見出しではなくデータ値らしき値（6桁以上の数字のみ）が
          含まれる場合は「破損ヘッダー」とみなし、problems に
          "corrupted_header" として記録した上で ValueError を送出する
          （BOM展開の対象から除外する。実例：file_no=235）。
        - 型変換：COL_SIDE → int、COL_QTY_PER_PRODUCT / COL_COEFFICIENT → float
          （変換対象列が空欄の場合は None のまま、値がある場合のみ変換する）
        - 型変換対象外の列も含め、空欄セルはすべて None に変換する
        - 同一 file_no の読み込み結果はメモリ内キャッシュし、以降は再読み込みしない
          （キャッシュをクリアしたい場合は build_index() を呼び直すこと）

        例外：
        - 該当ファイルが見つからない場合 FileNotFoundError
        - ヘッダーが破損している場合 ValueError（problemsにも記録）
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
            header = reader.fieldnames or []

            corrupted_cell = self._detect_corrupted_header(header)
            if corrupted_cell is not None:
                self.problems[file_no] = {
                    "type": "corrupted_header",
                    "candidates": [],
                    "message": (
                        f"ヘッダー行に列見出しではなくデータ値らしき値"
                        f"（{corrupted_cell!r}）が含まれています。ヘッダー行が"
                        f"破損している可能性があるため、このファイルはBOM展開の"
                        f"対象から除外します。"
                    ),
                }
                raise ValueError(
                    f"{os.path.basename(entry['path'])} のヘッダー行が破損しています"
                    f"（列見出しにデータ値らしき値 {corrupted_cell!r} が含まれています）。"
                )

            alias_map = self._resolve_header_aliases(header)

            for line_no, raw_row in enumerate(reader, start=2):  # 1行目はヘッダー
                row = {}
                for key, value in raw_row.items():
                    if key is None:
                        # DictReaderが列数不一致で拾う余剰値（Noneキー）は無視する
                        continue
                    canonical_key = alias_map.get(key, key)
                    if isinstance(value, str):
                        value = value.strip()
                    row[canonical_key] = value if value not in ("", None) else None

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
