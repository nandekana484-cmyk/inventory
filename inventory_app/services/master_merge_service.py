# services/master_merge_service.py
"""
バックアップされたマスタDB（master_backup_*.db、services/backup_service.py参照）
から、現在のmaster.db（config.MASTER_DB_PATH）に不足しているレコードだけを
追加するサービス。

対象5テーブル：board_structure_master・parts_attributes・workers・parts・
final_products（マスタDB分離の対象として確定した5テーブル、
CANONICAL_DESIGN_DECISIONS.md D-38参照）。

方針（ユーザー確定）：
- 主キー（parts_attributesのみ、実体はUNIQUE制約のpart_no。他4テーブルは
  宣言上のPRIMARY KEY）が現在のmaster.dbに存在しないレコードだけを追加する。
- 既存のレコードは一切上書きしない（バックアップ側の値で変わらない）。
- 各テーブルの既存のupsert関数（新規作成時のINSERTと同じ経路）をそのまま
  再利用する。「不足している行にだけ」呼ぶことで、結果的に新規INSERTとして
  機能させる（既存行は呼び出し対象から除外するため、ON CONFLICT DO UPDATE
  句を持つupsert関数でも上書きは発生しない）。
"""
import logging
import sqlite3

from models.db_common import get_master_connection
from models.board_structure_master import upsert_board_structure
from models.parts_attributes import upsert_parts_attributes
from models.workers import upsert_worker
from models.master import upsert_part, upsert_product

logger = logging.getLogger(__name__)

# テーブルごとの「主キー相当の列」「既存のupsert関数への引き渡し方」の定義。
# upsertはいずれも「無ければ新規登録、あれば上書き」の関数だが、本サービスは
# 不足している行（現在のmaster.dbにキーが存在しない行）にのみ呼び出すため、
# 実質的に新規追加としてのみ働く。
_TABLE_SPECS = [
    {
        "table": "board_structure_master",
        "key_column": "board_name",
        "upsert": lambda row: upsert_board_structure(row["board_name"], row["board_count"]),
    },
    {
        "table": "parts_attributes",
        # 宣言上のPRIMARY KEYは自動採番の"id"だが、業務上の一意キー（UNIQUE制約）
        # はpart_noであり、upsert_parts_attributes()もpart_noを一意キーとして
        # 扱う。バックアップ元と現在のDBでidが一致している保証は無い
        # （別々にINSERTされたautoincrement値のため）ため、必ずpart_noで照合する。
        "key_column": "part_no",
        "upsert": lambda row: upsert_parts_attributes(
            row["part_no"], row["teitori"], row["part_type"], row["supply_type"], row["full_qty"],
        ),
    },
    {
        "table": "workers",
        "key_column": "worker_id",
        # roleはバックアップ側の値をそのまま使う（ユーザー確定方針）。既存の
        # worker_idは対象から除外されるため、既存作業者のroleが書き換わる
        # ことはない。
        "upsert": lambda row: upsert_worker(row["worker_id"], row["name"], row["role"], row["is_active"]),
    },
    {
        "table": "parts",
        "key_column": "part_id",
        "upsert": lambda row: upsert_part(
            row["part_id"], row["code96"], row["part_type"], row["shelf_type"], row["shape_category"],
        ),
    },
    {
        "table": "final_products",
        "key_column": "product_id",
        "upsert": lambda row: upsert_product(row["product_id"], row["product_name"]),
    },
]


def _table_exists(con, table_name: str) -> bool:
    cur = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table_name,),
    )
    return cur.fetchone() is not None


def merge_master_from_backup(backup_file_path: str) -> dict:
    """
    backup_file_path（master_backup_*.db）の5テーブルを読み取り、現在の
    master.db（config.MASTER_DB_PATH）に存在しないキーの行だけを追加する。

    戻り値：{テーブル名: 追加した件数, ...}（5テーブル全てのキーを含む。
    バックアップ側にそのテーブル自体が存在しない場合は0件として扱う）。
    """
    result = {}

    backup_con = sqlite3.connect(backup_file_path)
    backup_con.row_factory = sqlite3.Row
    try:
        for spec in _TABLE_SPECS:
            table = spec["table"]
            key_column = spec["key_column"]

            if not _table_exists(backup_con, table):
                result[table] = 0
                continue

            try:
                with get_master_connection() as con:
                    if _table_exists(con, table):
                        existing_keys = {
                            row[0] for row in con.execute(f"SELECT {key_column} FROM {table}").fetchall()
                        }
                    else:
                        existing_keys = set()

                backup_rows = backup_con.execute(f"SELECT * FROM {table}").fetchall()

                added = 0
                for row in backup_rows:
                    row_dict = dict(row)
                    if row_dict.get(key_column) in existing_keys:
                        continue
                    spec["upsert"](row_dict)
                    added += 1

                result[table] = added
            except Exception:
                # 1テーブルの取り込みに失敗しても、他のテーブルの取り込みは
                # 続行する（部分的な成功を許容する。失敗したテーブルは0件として
                # 報告し、詳細はログに残す）。
                logger.exception(
                    "マスタデータの取り込みに失敗しました（table=%s, backup_file_path=%s）。"
                    "このテーブルはスキップして続行します。",
                    table, backup_file_path,
                )
                result[table] = 0
    finally:
        backup_con.close()

    return result
