# services/bom_service.py
"""
BOM基盤。

共有フォルダのBOM TSV（services.bom_file_service.BOMFileIndex）と
bom_master テーブル（models.bom_master）を組み合わせ、file_no・面（production_side）
単位の96部品構成（qty_per_product）を計算し、DBへキャッシュ保存する。

係数が0かつRフラグがある行の qty 計算には、models.parts_attributes
（丁取り数マスタ）を使う。
"""
import logging
import math

import config
from services.bom_file_service import BOMFileIndex
from models.bom_master import query_bom_master, save_bom_master, get_current_ym
from models.parts_attributes import get_parts_attributes

logger = logging.getLogger(__name__)

# bom_file_service.read_tsv() が返す行に含まれる列名（拡張ポイント）。
# いずれも実TSV（共有フォルダ上の実ファイル）で確認済みの列名。
# COL_R_FLAG（減数種別）は値の中身を問わず、空欄でなければ真として扱う
# （単純truthy判定。"R"／"M"等の値の種類による分岐は行わない）。
COL_SIDE = "生産面"
COL_QTY_PER_PRODUCT = "部品員数"
COL_COEFFICIENT = "マスターCHK員数係数"
COL_PART_NO = "96コード"
COL_R_FLAG = "減数種別"
COL_MOUNTING_LINE = "実装ライン"
COL_TYPE = "セットアップ部品種別"

# COL_TYPE の値のうち、その行が「部品」ではなく「基板自身」を表すもの。
# 丁取り数（models.parts_attributes.teitori）は部品自身の96コードではなく、
# この「K行」の96コード（基板コード）に対して登録されている
# （実データ検証済み：file_no=723 side=2 の部品96220357はteitoriを持たず、
# 同ファイル・同実装ラインのK行の96コード96254924がteitori=5を持つ）。
TYPE_VALUE_BOARD = "K"


class BOMService:
    """新BOM基盤のエントリーポイント。"""

    def __init__(self):
        self._index: BOMFileIndex = None

    def initialize(self, shared_folder_path: str = None) -> BOMFileIndex:
        """
        BOMFileIndex を初期化する。
        shared_folder_path 省略時は config.BOM_FOLDER_PATH を使う。
        """
        if shared_folder_path is None:
            shared_folder_path = config.BOM_FOLDER_PATH
        self._index = BOMFileIndex(shared_folder_path)
        return self._index

    def _ensure_index(self) -> BOMFileIndex:
        if self._index is None:
            self.initialize()
        return self._index

    def reload_index(self):
        """共有フォルダを再スキャンし、インデックス・読み込みキャッシュを再構築する。"""
        self._ensure_index().build_index()

    def list_available_file_nos(self) -> list:
        """共有フォルダ上で読み込み可能な file_no 一覧を返す。"""
        return self._ensure_index().list_available_file_nos()

    def list_excluded_file_nos(self) -> dict:
        """
        自動解決・読み込みができなかった file_no / setup_file_no の一覧を返す
        （BOMFileIndex.problems の内容をそのまま委譲する）。

        戻り値：{識別子: {"type", "candidates", "message"}, ...}
        type は理由の種類を表し、以下のいずれか：
          - "multiple_tsv_in_subfolder"：サブフォルダ内に複数のTSVがあり、
            サブフォルダ番号だけでは一意に特定できない（build_index()で検知）
          - "index_key_collision"：異なるファイルが同一file_noに解決された
            （build_index()で検知、現状は発生実績なし）
          - "unresolved_multiple_candidates"：setup_file_noの正規化後、
            複数のTSV候補があり一意に決定できない（resolve_file_no()で検知）
          - "tsv_not_found"：setup_file_noに対応するTSVが見つからない
            （共有フォルダにTSVが未整備。resolve_file_no()で検知）
          - "read_error"：TSVは解決できたが読み込みに失敗した
            （文字コードエラー等。get_parts_for_file_no()で検知）
          - "no_k_row_found"：丁取り数の参照元となる基板コード（K行、
            COL_TYPE=='K'の行の96コード）が対象のfile_no・side・実装ライン内に
            見つからない（_calculate_bom()で検知。実データ例：file_no=S722）
          - "multiple_k_rows_found"：同一file_no・side・実装ライン内に、
            異なる96コードを持つK行が複数見つかり、基板コードを一意に
            決定できない（_calculate_bom()で検知。実データでは実装ライン絞り込み
            前のfile_no=339がこれに該当したが、絞り込み後は解消される）
        """
        return dict(self._ensure_index().problems)

    def get_parts_for_file_no(self, file_no: str, side: int, mounting_line: str = None,
                                data_ym: str = None) -> list:
        """
        file_no・side・実装ライン（・data_ym）に対応する96部品構成を返す。

        処理フロー：
          1. 入力検証
          2. DB（bom_master）から取得
          3. 無ければ file_no を解決し、TSV読み込み
          4. BOM計算（係数ロジック、実装ライン絞り込み）
          5. DB保存
          6. 計算結果を返す

        data_ym を省略した場合は当月（models.bom_master.get_current_ym()）を対象とする。

        mounting_line：TSVの「実装ライン」列（COL_MOUNTING_LINE）で絞り込む値。
        同一file_no・sideに複数の実装ラインが存在し、それぞれが同一BOMを
        重複記載しているケースが実データで確認されている（例：file_no=154の
        側面1はD/L/Sの3ラインが同一41部品を記載しており、絞り込まずに合算すると
        3倍に過大計算される）。省略時（None）は _calculate_bom() が
        「最初に見つかったライン1本分」のみを使う安全側のデフォルトとする
        （呼び出し元がラインを特定できない場合の暫定挙動。詳細は
        _calculate_bom() のdocstring参照）。

        bom_master のキャッシュキーは (file_no, side, mounting_line, data_ym)。
        mounting_line=None（未指定）の場合は空文字列("")をキャッシュキーとして扱う
        （SQLiteのUNIQUE制約はNULL同士を区別してしまいON CONFLICTが効かないため）。
        このため、明示的に同じラインを渡した呼び出しとは別のキャッシュ行になる
        （どちらも同じ計算結果になるはずだが、キャッシュ行としては重複しうる）。

        file_no には kitting_plan_items.setup_file_no のような先頭ゼロ付き表記
        （例："0103"）がそのまま渡ってくる可能性があるため、TSV読み込み前に
        BOMFileIndex.resolve_file_no() で実際の索引キーに解決する
        （bom_master のキャッシュキーとしては、従来どおり呼び出し元から
        渡された file_no をそのまま使う）。解決できない／読み込みに失敗した
        場合は BOMFileIndex.problems に記録した上で、従来どおり例外を送出する
        （呼び出し元の FileNotFoundError / ValueError の捕捉方法は変更しない）。

        戻り値：[{"part_no": "96000123", "qty_per_product": 3.0, "item_type": "part"}, ...]
        item_type="board"の行（基板自身、K行の96コード。1件、無い場合もある）が
        通常部品の行に混ざって含まれる（_calculate_bom()参照）。
        """
        # 1. 入力検証
        if not file_no:
            raise ValueError("file_no は必須です。")
        if side not in (1, 2):
            raise ValueError(f"side は1または2である必要があります（受け取った値: {side!r}）。")

        if data_ym is None:
            data_ym = get_current_ym()

        cache_line = mounting_line or ""

        # 2. DBから取得
        cached = query_bom_master(file_no, side, cache_line, data_ym)
        if cached:
            return cached

        # 3. 無ければ file_no を解決してTSV読み込み
        index = self._ensure_index()
        resolved_file_no = index.resolve_file_no(file_no)
        if resolved_file_no is None:
            # resolve_file_no() 内で problems に記録済み
            # （unresolved_multiple_candidates または tsv_not_found）。
            raise FileNotFoundError(
                f"file_no={file_no!r} に対応するBOM TSVが見つからない、"
                f"または一意に解決できません（検索フォルダ: {index.folder_path}）。"
            )

        try:
            rows = index.read_tsv(resolved_file_no)
        except (FileNotFoundError, ValueError) as e:
            # read_tsv() が既により具体的な理由（例："corrupted_header"）を
            # problems に記録済みの場合は上書きしない。
            if file_no in index.problems:
                raise
            index.problems[file_no] = {
                "type": "read_error",
                "candidates": [],
                "message": (
                    f"file_no={file_no!r}（解決後: {resolved_file_no!r}）の"
                    f"読み込みに失敗しました: {e}"
                ),
            }
            raise

        # 4. BOM計算
        parts = self._calculate_bom(file_no, side, rows, mounting_line)

        # 5. DB保存
        save_bom_master(file_no, side, parts, cache_line, data_ym)

        # 6. 計算結果を返す
        return parts

    def list_mounting_lines(self, file_no: str, side: int) -> list:
        """
        file_no・side に対応するTSV上の実装ライン（COL_MOUNTING_LINE）の
        ユニーク値一覧をソートして返す（計画外登録でのライン選択UI用）。

        file_noの解決・TSV読み込みの例外方針は get_parts_for_file_no() と同様
        （FileNotFoundError / ValueError をそのまま送出する。problemsへの
        記録は get_parts_for_file_no() 経由の場合と重複しうるため、ここでは
        行わない）。
        """
        index = self._ensure_index()
        resolved_file_no = index.resolve_file_no(file_no)
        if resolved_file_no is None:
            raise FileNotFoundError(
                f"file_no={file_no!r} に対応するBOM TSVが見つからない、"
                f"または一意に解決できません（検索フォルダ: {index.folder_path}）。"
            )

        rows = index.read_tsv(resolved_file_no)
        lines = {
            row.get(COL_MOUNTING_LINE) for row in rows
            if row.get(COL_SIDE) == side and row.get(COL_MOUNTING_LINE)
        }
        return sorted(lines)

    def _calculate_bom(self, file_no: str, side: int, rows: list, mounting_line: str = None) -> list:
        """
        TSV行（bom_file_service.read_tsv() の戻り値）から、指定 side のBOMを計算する。

        - side（COL_SIDE）が一致する行のみ対象
        - さらに実装ライン（COL_MOUNTING_LINE）が一致する行のみを対象とする。
          mounting_line が省略された場合（None）は、side が一致する最初の行の
          実装ライン値を採用する（＝TSV内で最初に見つかった1ライン分のみを使う）。
          同一file_no・sideに複数の実装ラインが存在する場合、実データでは
          各ラインが同一BOMを重複記載しているケースが大半のため、絞り込まずに
          全ライン分を合算すると部品数量が実装ライン数倍に過大計算されてしまう
          （実例：file_no=154側面1はD/L/Sの3ラインが同一41部品を記載しており、
          絞り込み無しでは3倍になる）。この安全側のデフォルトにより、
          呼び出し元がラインを特定できない場合でも過大計算は防げるが、
          「どのラインの数値を使うか」は保証されない（実データ上は通常
          どのラインも同一BOMのため実害はない）。
        - 部品員数（COL_QTY_PER_PRODUCT）が None の行はスキップ（K行自身もここで
          スキップされる。K行は部品員数が常に空欄のため）
        - 係数（COL_COEFFICIENT）> 0 → qty = 部品員数 × 係数
        - 係数が 0（またはNone）かつ Rフラグ（COL_R_FLAG）あり
          → 基板コード（下記参照）で models.parts_attributes.get_parts_attributes()
            から丁取り数を取得し、
            丁取り数が1以上 → qty = 部品員数 ÷ 丁取り数
            丁取り数が未設定（None）または0以下 → 警告ログを出し、暫定で qty = 部品員数
        - 係数が 0（またはNone）かつ Rフラグなし → 警告ログを出してスキップ
        - 同一 part_no は qty を合算する

        丁取り数の参照元（基板コード）について：
        丁取り数はRフラグが立っている行自身の96コード（部品自身）ではなく、
        同一file_no・実装ライン内の「K行」（COL_TYPE == TYPE_VALUE_BOARD、
        基板自身を表す行。部品員数は常に空欄）が持つ96コードに対して
        models.parts_attributes に登録されている（実データ検証済み：
        file_no=723 side=2 の部品96220357はteitoriを持たず、同ファイル・同
        実装ラインのK行の96コード96254924がteitori=5を持つ。部品自身の
        96コードで検索すると該当データが無くフォールバックが働き、本来1.0で
        あるべき値が5.0になる実害を確認済み）。

        K行探索は side では絞り込まない：K行は生産面（COL_SIDE）に関係なく
        常に生産面=1として記録されている（実データ519ファイル・K行636件全件で
        確認済み、生産面=2のK行は0件）。基板コードは面を問わず共通のため、
        side=2のBOM計算であっても、生産面=1に記録されたK行を参照する
        （実装ラインのみで絞り込む）。

        K行は対象のfile_no・実装ライン内に必ず1件だけ存在する前提
        （実データ519ファイル中517ファイルで確認済み、うち515ファイルは単一、
        実装ライン単位で見ればfile_no=339のような複数ライン混在ファイルも
        ライン絞り込み後は1件になる）。この前提が崩れている場合
        （K行が0件、または絞り込み後もなお複数の異なる96コードが見つかる場合）は
        丁取り数の参照元を一意に決定できないため、BOM展開そのものを中止し、
        BOMFileIndex.problems に理由（"no_k_row_found" / "multiple_k_rows_found"）を
        記録した上で ValueError を送出する（部品員数をそのまま採用するフォール
        バックは行わない。実データではfile_no=S722がK行0件の実例）。
        """
        resolved_line = mounting_line
        if not resolved_line:
            for row in rows:
                if row.get(COL_SIDE) != side:
                    continue
                candidate = row.get(COL_MOUNTING_LINE)
                if candidate:
                    resolved_line = candidate
                    break

        def _row_in_scope(row):
            if row.get(COL_SIDE) != side:
                return False
            if resolved_line and row.get(COL_MOUNTING_LINE) != resolved_line:
                return False
            return True

        def _row_in_line_scope(row):
            # K行探索専用：K行はside（生産面）に関係なく常に生産面=1として
            # 記録されている（実データ519ファイル全636件のK行で確認済み、
            # 生産面=2のK行は0件）。基板コード自体は面を問わず共通のため、
            # sideでは絞り込まず実装ラインのみで絞り込む。
            if resolved_line and row.get(COL_MOUNTING_LINE) != resolved_line:
                return False
            return True

        board_codes = {
            row.get(COL_PART_NO)
            for row in rows
            if _row_in_line_scope(row) and row.get(COL_TYPE) == TYPE_VALUE_BOARD and row.get(COL_PART_NO)
        }

        if len(board_codes) == 0:
            self._ensure_index().problems[file_no] = {
                "type": "no_k_row_found",
                "candidates": [],
                "message": (
                    f"file_no={file_no!r} side={side} mounting_line={resolved_line!r} に"
                    f"基板自身を表すK行（{COL_TYPE}='{TYPE_VALUE_BOARD}'）が見つかりません。"
                    f"丁取り数の参照元となる基板コードを特定できないため、BOM展開を中止します。"
                ),
            }
            raise ValueError(
                f"file_no={file_no!r} side={side} mounting_line={resolved_line!r} に"
                f"K行（基板自身の96コード）が見つからないため、BOM計算を中止しました。"
            )

        if len(board_codes) >= 2:
            candidates = sorted(board_codes)
            self._ensure_index().problems[file_no] = {
                "type": "multiple_k_rows_found",
                "candidates": candidates,
                "message": (
                    f"file_no={file_no!r} side={side} mounting_line={resolved_line!r} に"
                    f"複数の異なるK行（基板コード: {', '.join(candidates)}）が見つかり、"
                    f"丁取り数の参照元を一意に決定できないため、BOM展開を中止します。"
                ),
            }
            raise ValueError(
                f"file_no={file_no!r} side={side} mounting_line={resolved_line!r} に"
                f"複数のK行（{', '.join(candidates)}）が見つかったため、BOM計算を中止しました。"
            )

        board_code = next(iter(board_codes))

        totals = {}

        for row in rows:
            if not _row_in_scope(row):
                continue

            part_no = row.get(COL_PART_NO)
            if not part_no:
                continue

            qty_per_product = row.get(COL_QTY_PER_PRODUCT)
            if qty_per_product is None:
                continue

            coefficient = row.get(COL_COEFFICIENT)
            r_flag = row.get(COL_R_FLAG)

            if coefficient and coefficient > 0:
                qty = qty_per_product * coefficient
            elif r_flag:
                attrs = get_parts_attributes(board_code)
                teitori = attrs.get("teitori") if attrs else None

                if teitori is not None and teitori >= 1:
                    qty = qty_per_product / teitori
                else:
                    logger.warning(
                        "丁取り数が未設定または0以下です: file_no=%s side=%s part_no=%s "
                        "board_code=%s（teitori=%r）。暫定的に部品員数をそのまま採用します。",
                        file_no, side, part_no, board_code, teitori,
                    )
                    qty = qty_per_product
            else:
                logger.warning(
                    "BOM計算スキップ: file_no=%s side=%s part_no=%s "
                    "（係数が0でRフラグも無いため計算できません）",
                    file_no, side, part_no,
                )
                continue

            totals[part_no] = totals.get(part_no, 0) + qty

        # 基板自身（K行の96コード＝board_code）の消費枚数も、通常部品とは別に
        # item_type="board"の1行として結果へ含める。基板の「1台あたり消費比率」は
        # 1÷丁取り数（丁取り数枚の基板から1枚を切り出す、という意味）とし、
        # 通常部品と同じ qty_per_product 形式で表現する（実際のNG数量／仕掛数量を
        # 掛けた後の切り上げ計算は、expand_scrap_to_parts()/expand_wip_to_parts()側で
        # 行う。_calculate_bom()の結果はbom_masterにキャッシュされ、呼び出しごとに
        # 異なるNG数量／仕掛数量で再利用されるため、特定の数量を前提にした
        # 計算結果をここでキャッシュしてはならない）。
        # 丁取り数が未設定・0以下の場合は基板行の消費比率を算出できないため、
        # 警告ログのみ出し基板行自体を結果に含めない（部品員数そのまま採用のような
        # フォールバックは行わない。数量が不明なまま何かを表示する方が誤解を招くため）。
        board_row = None
        board_attrs = get_parts_attributes(board_code)
        board_teitori = board_attrs.get("teitori") if board_attrs else None
        if board_teitori is not None and board_teitori >= 1:
            board_row = {
                "part_no": board_code,
                "qty_per_product": 1.0 / board_teitori,
                "item_type": "board",
            }
        else:
            logger.warning(
                "基板行(K行)の消費数量を計算できません: file_no=%s side=%s "
                "mounting_line=%r board_code=%s（teitori=%r）。基板行は結果に含めません。",
                file_no, side, resolved_line, board_code, board_teitori,
            )

        result = [
            {"part_no": part_no, "qty_per_product": qty, "item_type": "part"}
            for part_no, qty in totals.items()
        ]
        if board_row is not None:
            result.append(board_row)
        return result

    def expand_wip_to_parts(self, wip_record: dict) -> list:
        """
        仕掛（WIP）レコードを96部品に展開する。qty = qty_per_product × wip_qty

        wip_record: {
            "setup_file_no": str, "production_side": int, "wip_qty": float,
            "mounting_line": str（省略可。省略時は get_parts_for_file_no() の
            デフォルト方針＝最初に見つかったライン1本分を使う）,
            "data_ym": str（省略可）, "lot_no"（省略可・出力にそのまま引き継ぐ）,
        }
        （"file_no" / "side" というキー名も後方互換として受け付ける）

        戻り値：[{"lot_no": ..., "file_no": ..., "part_no": ..., "qty": ...,
                   "item_type": "part"|"board"}, ...]
        item_type="board"の行（基板自身、K行の96コード）は、qtyを
        math.ceil()で切り上げる（例：丁取り数5・仕掛数量2 → ceil(2÷5)=1枚）。
        通常部品（item_type="part"）は従来通り切り上げない。
        """
        file_no = wip_record.get("setup_file_no", wip_record.get("file_no"))
        side = wip_record.get("production_side", wip_record.get("side"))
        wip_qty = wip_record["wip_qty"]
        mounting_line = wip_record.get("mounting_line")
        data_ym = wip_record.get("data_ym")

        parts = self.get_parts_for_file_no(file_no, side, mounting_line, data_ym)

        result = []
        for part in parts:
            item_type = part.get("item_type", "part")
            raw_qty = wip_qty * part["qty_per_product"]
            result.append({
                "lot_no": wip_record.get("lot_no"),
                "file_no": file_no,
                "part_no": part["part_no"],
                "qty": math.ceil(raw_qty) if item_type == "board" else raw_qty,
                "item_type": item_type,
            })
        return result

    def expand_scrap_to_parts(self, scrap_record: dict) -> list:
        """
        仕損（NG）レコードを96部品に展開する。qty = qty_per_product × ng_qty

        scrap_record: {
            "setup_file_no": str, "production_side": int, "ng_qty": float,
            "mounting_line": str（省略可。省略時は get_parts_for_file_no() の
            デフォルト方針＝最初に見つかったライン1本分を使う）,
            "data_ym": str（省略可）, "lot_no"（省略可・出力にそのまま引き継ぐ）,
        }
        （"file_no" / "side" というキー名も後方互換として受け付ける）

        戻り値：[{"lot_no": ..., "file_no": ..., "part_no": ..., "qty": ...,
                   "item_type": "part"|"board"}, ...]
        item_type="board"の行（基板自身、K行の96コード）は、qtyを
        math.ceil()で切り上げる（例：丁取り数5・NG数量2 → ceil(2÷5)=1枚）。
        通常部品（item_type="part"）は従来通り切り上げない。
        """
        file_no = scrap_record.get("setup_file_no", scrap_record.get("file_no"))
        side = scrap_record.get("production_side", scrap_record.get("side"))
        ng_qty = scrap_record["ng_qty"]
        mounting_line = scrap_record.get("mounting_line")
        data_ym = scrap_record.get("data_ym")

        parts = self.get_parts_for_file_no(file_no, side, mounting_line, data_ym)

        result = []
        for part in parts:
            item_type = part.get("item_type", "part")
            raw_qty = ng_qty * part["qty_per_product"]
            result.append({
                "lot_no": scrap_record.get("lot_no"),
                "file_no": file_no,
                "part_no": part["part_no"],
                "qty": math.ceil(raw_qty) if item_type == "board" else raw_qty,
                "item_type": item_type,
            })
        return result
