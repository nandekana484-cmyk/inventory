# services/backup_service.py
"""
月次DB（config.DB_PATH）・マスタDB（config.MASTER_DB_PATH）を指定フォルダへ
手動バックアップするサービス。

sqlite3.Connection.backup()（SQLite公式のオンラインバックアップAPI）を使う。
単純なファイルコピー（shutil.copy2()等）と異なり、バックアップ対象のDBファイルに
他のプロセス・スレッドが書き込み中であっても、ページ単位で整合性を保ったまま
コピーを完了できる（調査結果、CANONICAL_DESIGN_DECISIONS.md参照）。
"""
import os
import sqlite3
from datetime import datetime

import config


def _generate_unique_path(destination_folder: str, base_name: str, timestamp: str) -> str:
    """
    destination_folder内で、"{base_name}_{timestamp}.db"を基本に、既存ファイルと
    重複しないパスを生成する。重複する場合は"_1"・"_2"...と連番を付与し、
    存在しなくなるまでチェックする（候補B方式）。
    """
    candidate = os.path.join(destination_folder, f"{base_name}_{timestamp}.db")
    if not os.path.exists(candidate):
        return candidate

    n = 1
    while True:
        candidate = os.path.join(destination_folder, f"{base_name}_{timestamp}_{n}.db")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def _backup_one(source_path: str, destination_path: str):
    """sqlite3.Connection.backup()で1つのDBファイルをdestination_pathへバックアップする。"""
    src_con = sqlite3.connect(source_path)
    try:
        dest_con = sqlite3.connect(destination_path)
        try:
            src_con.backup(dest_con)
        finally:
            dest_con.close()
    finally:
        src_con.close()


def backup_databases(destination_folder: str) -> dict:
    """
    config.DB_PATH（月次DB）・config.MASTER_DB_PATH（マスタDB）を
    destination_folderへバックアップする。

    ファイル名は"inventory_backup_<timestamp>.db"・"master_backup_<timestamp>.db"
    （timestampは両ファイルで共通、本関数の呼び出し時刻"YYYYMMDD_HHMMSS"。
    2つのファイルが対になっていることが名前から分かるようにする）。
    destination_folder内に同名ファイルが既に存在する場合は、_generate_unique_path()
    により"_1"・"_2"...と連番を付けて重複しないファイル名にする。

    DB_PATH・MASTER_DB_PATHのいずれかがまだ存在しない場合（例：マスタDB分離後、
    一度もmaster.dbへアクセスしていないアプリ起動直後等）は、そのファイルの
    バックアップをスキップする（sqlite3.connect()は存在しないパスに対して
    空の新規DBファイルを作成してしまうため、存在しないものを「バックアップした」
    かのように空ファイルを作ってしまうことを避けるため）。

    戻り値：{"inventory_backup_path": strまたはNone, "master_backup_path": strまたはNone,
             "skipped": [スキップしたDBの種類（"inventory"|"master"）, ...]}
    """
    os.makedirs(destination_folder, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    result = {"inventory_backup_path": None, "master_backup_path": None, "skipped": []}

    if os.path.exists(config.DB_PATH):
        inventory_backup_path = _generate_unique_path(destination_folder, "inventory_backup", timestamp)
        _backup_one(config.DB_PATH, inventory_backup_path)
        result["inventory_backup_path"] = inventory_backup_path
    else:
        result["skipped"].append("inventory")

    if os.path.exists(config.MASTER_DB_PATH):
        master_backup_path = _generate_unique_path(destination_folder, "master_backup", timestamp)
        _backup_one(config.MASTER_DB_PATH, master_backup_path)
        result["master_backup_path"] = master_backup_path
    else:
        result["skipped"].append("master")

    return result


# ========== バックアップの呼び出し（復元）==========
#
# 共有フォルダ3ボタン廃止（CANONICAL_DESIGN_DECISIONS.md D-50）に伴い、
# 「既存の『共有フォルダのDBを開く』機能をバックアップの復元に転用する」
# （旧D-45）方針を見直した。旧方針はバックアップ原本そのものが本番DBになって
# しまう・マスタDBは復元されない・D-20（ローカル+バックアップ方針）と
# 整合しないという3点の問題があったため、「新しいローカルフォルダへコピー
# してから切り替える」方式（原本・元のDBともに変更しない）に置き換えた。

# 月次DB（config.DB_PATH）が必ず持つテーブル。バックアップファイルが月次DBか
# どうかを判定する「月次DBである」ことの肯定的な目印として使う（D-53）。
#
# kitting_plan_items は db/schema.sql には含まれず、models.kitting_plan.
# init_kitting_plan_tables()（get_connection()＝月次DB経由）でのみ作成される
# ため、マスタDB分離（D-38・D-42）の前後を問わず、月次DBには常に存在し、
# master.dbには存在しない（空のまま一度も生産計画を取り込んでいない月次DBも
# 含め、init_database_at()直後にinit_kitting_plan_tables()を呼ぶ既存の
# 運用上、テーブル自体は必ず作成される）。
#
# 当初は逆方向（マスタDB固有テーブルがあれば拒否）の判定を採用していたが、
# マスタDB分離前（2026-10-02より前）に作成された月次DBには
# board_structure_master・parts_attributes・workers・parts・final_products
# の5テーブルが（当時のdb/schema.sql・各モジュールの接続先がget_connection()
# だったため）物理的に同居しており、分離後のバックアップ方式に切り替えた
# 今も、古い月次DBのバックアップファイル自体にはこれらのテーブルがそのまま
# 残っている。実際に分離直前の実バックアップファイル
# （db/inventory.db.bak_before_master_tables_removal_20261002_084718、
# 読み取りのみで確認）を検証に使ったところ、この逆方向の判定では正しい
# 月次DBのバックアップが「マスタDBのようだ」と誤って拒否されることを確認した。
# そのため、月次DB固有テーブル（_MONTHLY_DB_MARKER_TABLE）の存在だけを
# 肯定的な受理条件とし、マスタDB固有テーブルの同居は許容する（D-53）。
_MONTHLY_DB_MARKER_TABLE = "kitting_plan_items"

# マスタDB（config.MASTER_DB_PATH）固有のテーブル。受理・拒否の判定条件には
# 使わず、拒否理由をより具体的に案内するためだけに使う（"これはマスタDBの
# バックアップのようです"という文言を出せるのは、月次DB固有テーブルが無く、
# かつこれらのテーブルを持つ場合のみ。db/schema.sqlに定義が無く、
# models/board_structure_master.py・models/parts_attributes.pyが
# get_master_connection()経由でmaster.db側にのみ作成するため、月次DB側に
# 物理的に同居することは無い）。
_MASTER_DB_HINT_TABLES = {"board_structure_master", "parts_attributes"}


def validate_monthly_db_backup(file_path: str) -> dict:
    """
    指定ファイルが「月次DBのバックアップとして取り込み可能な.dbファイル」か
    判定する（読み取りのみ、ファイルへの変更は一切行わない。URIのmode=roで
    読み取り専用接続を強制する）。

    判定内容：
    1. SQLiteとして開けるか（.dbでない・破損している場合はエラー）。
    2. `PRAGMA integrity_check` が "ok" を返すか。
    3. 月次DB固有のテーブル（_MONTHLY_DB_MARKER_TABLE）が存在するか
       （肯定的な受理条件。マスタDB分離前の月次DBのように、マスタDB固有の
       テーブルが同居していても拒否しない。D-53参照）。

    戻り値：{"ok": bool, "reason": str（okがFalseの場合の理由、Trueの場合は空文字列）}
    """
    if not os.path.isfile(file_path):
        return {"ok": False, "reason": "ファイルが見つかりません。"}

    try:
        uri = f"file:{os.path.abspath(file_path)}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        return {"ok": False, "reason": f"SQLiteデータベースとして開けませんでした：{e}"}

    try:
        try:
            cur = con.execute("PRAGMA integrity_check")
            result = cur.fetchone()
        except sqlite3.DatabaseError as e:
            return {"ok": False, "reason": f"SQLiteデータベースとして開けませんでした：{e}"}

        if not result or result[0] != "ok":
            return {"ok": False, "reason": f"データベースの整合性チェックに失敗しました：{result}"}

        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
    finally:
        con.close()

    if _MONTHLY_DB_MARKER_TABLE in tables:
        # 月次DB固有テーブルがあれば受理する。マスタDB固有テーブル
        # （board_structure_master等）が同居していても、マスタDB分離前に
        # 作成された月次DBの正常な状態であるため拒否しない（D-53）。
        return {"ok": True, "reason": ""}

    if tables & _MASTER_DB_HINT_TABLES:
        return {
            "ok": False,
            "reason": "これはマスタDBのバックアップのようです。月次DBのバックアップファイルを選択してください。"
                       "マスタデータを取り込む場合は「マスタデータを他PCから取り込む」（admin限定）を使ってください。",
        }

    return {
        "ok": False,
        "reason": "月次DBのテーブル構成と一致しません（kitting_plan_itemsが見つかりません）。"
                   "無関係なファイル、または対応していない形式のバックアップの可能性があります。",
    }


def restore_backup_as_new_local_db(backup_file_path: str, folder_name: str) -> str:
    """
    検証済みの月次DBバックアップファイルを、config.APP_DATA_DIR/db/<folder_name>/
    inventory.db へ sqlite3.Connection.backup() でコピーする（新規フォルダを
    作成する。既存フォルダへの上書きは呼び出し元が事前に重複チェックする前提）。

    バックアップ原本（backup_file_path）はURIのmode=roで読み取り専用接続を
    強制し、一切変更しない。呼び出し元（ui.main_window.MainWindow）は、
    本関数が例外を送出した場合、作成途中のフォルダを削除した上で現在の
    DB・ロックを変更しないこと（failure時に空の中途半端なDBを残さないため）。

    戻り値：作成したinventory.dbのフルパス。
    """
    new_folder = os.path.join(config.APP_DATA_DIR, "db", folder_name)
    new_db_path = os.path.join(new_folder, "inventory.db")
    os.makedirs(new_folder, exist_ok=True)

    src_uri = f"file:{os.path.abspath(backup_file_path)}?mode=ro"
    src_con = sqlite3.connect(src_uri, uri=True)
    try:
        dest_con = sqlite3.connect(new_db_path)
        try:
            src_con.backup(dest_con)
        finally:
            dest_con.close()
    finally:
        src_con.close()

    return new_db_path
