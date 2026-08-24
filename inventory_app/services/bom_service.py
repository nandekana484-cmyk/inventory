# services/bom_service.py
"""
新BOM基盤（フェーズ1）。

共有フォルダのBOM TSV（services.bom_file_service.BOMFileIndex）と
bom_master テーブル（models.bom_master）を組み合わせ、file_no・面（production_side）
単位の96部品構成（qty_per_product）を計算し、DBへキャッシュ保存する。

旧BOMロジック（component_bom / board_definitions / component_groups、
旧 services.bom_service の get_parts_for_file_no / expand_wip_to_parts /
expand_scrap_to_parts）とは完全に独立した新規実装であり、それらのテーブル・
関数には一切依存しない。

フェーズ2で services.inventory_diff_service / ui.ng_input_window から
本クラス（BOMService）が直接利用されるようになったため、旧APIとの
互換シム（モジュールレベル関数）は撤去済み。
"""
import logging

import config
from services.bom_file_service import BOMFileIndex
from models.bom_master import query_bom_master, save_bom_master, get_current_ym

logger = logging.getLogger(__name__)

# bom_file_service.read_tsv() が返す行に含まれる列名（拡張ポイント）。
# 先行面・後行面／部品員数／マスターCHK員数係数の3列名はユーザー指定により確定。
# 部品番号列・Rフラグ列は実TSVフォーマット確定後に調整すること。
COL_SIDE = "先行面・後行面"
COL_QTY_PER_PRODUCT = "部品員数"
COL_COEFFICIENT = "マスターCHK員数係数"
COL_PART_NO = "部品番号"
COL_R_FLAG = "Rフラグ"


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

    def get_parts_for_file_no(self, file_no: str, side: int, data_ym: str = None) -> list:
        """
        file_no・side（・data_ym）に対応する96部品構成を返す。

        処理フロー：
          1. 入力検証
          2. DB（bom_master）から取得
          3. 無ければTSV読み込み
          4. BOM計算（係数ロジック）
          5. DB保存
          6. 計算結果を返す

        data_ym を省略した場合は当月（models.bom_master.get_current_ym()）を対象とする。

        戻り値：[{"part_no": "96000123", "qty_per_product": 3.0}, ...]
        """
        # 1. 入力検証
        if not file_no:
            raise ValueError("file_no は必須です。")
        if side not in (1, 2):
            raise ValueError(f"side は1または2である必要があります（受け取った値: {side!r}）。")

        if data_ym is None:
            data_ym = get_current_ym()

        # 2. DBから取得
        cached = query_bom_master(file_no, side, data_ym)
        if cached:
            return cached

        # 3. 無ければTSV読み込み
        rows = self._ensure_index().read_tsv(file_no)

        # 4. BOM計算
        parts = self._calculate_bom(file_no, side, rows)

        # 5. DB保存
        save_bom_master(file_no, side, parts, data_ym)

        # 6. 計算結果を返す
        return parts

    def _calculate_bom(self, file_no: str, side: int, rows: list) -> list:
        """
        TSV行（bom_file_service.read_tsv() の戻り値）から、指定 side のBOMを計算する。

        - side（COL_SIDE）が一致する行のみ対象
        - 部品員数（COL_QTY_PER_PRODUCT）が None の行はスキップ
        - 係数（COL_COEFFICIENT）> 0 → qty = 部品員数 × 係数
        - 係数が 0（またはNone）かつ Rフラグ（COL_R_FLAG）あり
          → qty = 部品員数（丁取り数を用いた計算は後日実装）
        - 係数が 0（またはNone）かつ Rフラグなし → 警告ログを出してスキップ
        - 同一 part_no は qty を合算する
        """
        totals = {}

        for row in rows:
            if row.get(COL_SIDE) != side:
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
                # 丁取り数を用いた計算は後日実装。現時点では部品員数をそのまま採用する。
                qty = qty_per_product
            else:
                logger.warning(
                    "BOM計算スキップ: file_no=%s side=%s part_no=%s "
                    "（係数が0でRフラグも無いため計算できません）",
                    file_no, side, part_no,
                )
                continue

            totals[part_no] = totals.get(part_no, 0) + qty

        return [
            {"part_no": part_no, "qty_per_product": qty}
            for part_no, qty in totals.items()
        ]

    def expand_wip_to_parts(self, wip_record: dict) -> list:
        """
        仕掛（WIP）レコードを96部品に展開する。qty = qty_per_product × wip_qty

        wip_record: {
            "setup_file_no": str, "production_side": int, "wip_qty": float,
            "data_ym": str（省略可）, "lot_no"（省略可・出力にそのまま引き継ぐ）,
        }
        （"file_no" / "side" というキー名も後方互換として受け付ける）

        戻り値：[{"lot_no": ..., "file_no": ..., "part_no": ..., "qty": ...}, ...]
        """
        file_no = wip_record.get("setup_file_no", wip_record.get("file_no"))
        side = wip_record.get("production_side", wip_record.get("side"))
        wip_qty = wip_record["wip_qty"]
        data_ym = wip_record.get("data_ym")

        parts = self.get_parts_for_file_no(file_no, side, data_ym)

        return [
            {
                "lot_no": wip_record.get("lot_no"),
                "file_no": file_no,
                "part_no": part["part_no"],
                "qty": wip_qty * part["qty_per_product"],
            }
            for part in parts
        ]

    def expand_scrap_to_parts(self, scrap_record: dict) -> list:
        """
        仕損（NG）レコードを96部品に展開する。qty = qty_per_product × ng_qty

        scrap_record: {
            "setup_file_no": str, "production_side": int, "ng_qty": float,
            "data_ym": str（省略可）, "lot_no"（省略可・出力にそのまま引き継ぐ）,
        }
        （"file_no" / "side" というキー名も後方互換として受け付ける）

        戻り値：[{"lot_no": ..., "file_no": ..., "part_no": ..., "qty": ...}, ...]
        """
        file_no = scrap_record.get("setup_file_no", scrap_record.get("file_no"))
        side = scrap_record.get("production_side", scrap_record.get("side"))
        ng_qty = scrap_record["ng_qty"]
        data_ym = scrap_record.get("data_ym")

        parts = self.get_parts_for_file_no(file_no, side, data_ym)

        return [
            {
                "lot_no": scrap_record.get("lot_no"),
                "file_no": file_no,
                "part_no": part["part_no"],
                "qty": ng_qty * part["qty_per_product"],
            }
            for part in parts
        ]
