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
