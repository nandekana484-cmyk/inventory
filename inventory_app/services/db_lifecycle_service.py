"""
月次DB の作成・切り替え・バックアップからの復元・削除のうち、画面に依存しない処理（段階7aで ui/main_window.py などから移した）。
DB ファイル・フォルダ・config.DB_PATH・操作履歴・DB の作成削除の履歴（master.db）を扱う。ダイアログは出さない。
ロックの取得と解放（壊れたロックの確認ダイアログを含む）は画面とのやり取りが絡むので、画面側（ui/main_window.py）に残している。
前月からの引き継ぎ（別スレッド）の後の記録も、画面側（_on_create_db_finished()）に残している。
手順と注意点は docs/db.md。
"""
import os
import shutil

import config
from db.init_db import init_database_at
from models.db_lifecycle_log import (
    record_db_lifecycle_event,
    OPERATION_TYPE_CREATE,
    OPERATION_TYPE_DELETE,
    OPERATION_TYPE_RESTORE_FROM_BACKUP,
)
from models.kitting_plan import init_kitting_plan_tables
from models.operation_log import log_operation
from services.app_settings_service import load_last_db_path
from services.backup_service import restore_backup_as_new_local_db
from services.db_delete_service import delete_database_files

# check_new_db_folder() の結果
NEW_FOLDER_EMPTY = "empty"
NEW_FOLDER_RESERVED = "reserved"
NEW_FOLDER_EXISTS = "exists"


def restore_last_db_path():
    """
    起動時に、前回選んでいた DB に config.DB_PATH を切り替える（無ければ既定DB のまま）。
    記憶したパスのファイルが今は無ければ切り替えずに、そのパスを返す（画面が UI を作り終えてから知らせる）。それ以外は None。
    """
    last_db_path = load_last_db_path()
    if last_db_path and last_db_path != config.DB_PATH:
        if os.path.exists(last_db_path):
            config.set_db_path(last_db_path)
        else:
            return last_db_path
    return None


def db_folder_path(folder):
    """月次DB のフォルダ（APP_DATA_DIR/db/<folder>）。"""
    return os.path.join(config.APP_DATA_DIR, "db", folder)


def db_path_for_folder(folder):
    """月次DB のファイル（APP_DATA_DIR/db/<folder>/inventory.db）。"""
    return os.path.join(config.APP_DATA_DIR, "db", folder, "inventory.db")


def list_db_folders():
    """inventory.db があるフォルダの名前を、名前の順で返す（db/ が無ければ空）。"""
    db_root = os.path.join(config.APP_DATA_DIR, "db")
    folders = []
    if os.path.isdir(db_root):
        folders = sorted(
            name for name in os.listdir(db_root)
            if os.path.isfile(os.path.join(db_root, name, "inventory.db"))
        )
    return folders


def check_new_db_folder(folder):
    """
    新しい DB のフォルダ名（前後の空白は呼び出し元で除く）を確かめる。この順に調べ、最初に当たったものを返す。問題なければ None。
      NEW_FOLDER_EMPTY: 空。NEW_FOLDER_RESERVED: "db"（大文字・小文字を問わない）。既定DB の判定（親フォルダ名が "db" か）とぶつかるため。
      NEW_FOLDER_EXISTS: そのフォルダの inventory.db が既にある。
    """
    if not folder:
        return NEW_FOLDER_EMPTY
    if folder.lower() == config.DB_ROOT_FOLDER_NAME:
        return NEW_FOLDER_RESERVED
    if os.path.exists(db_path_for_folder(folder)):
        return NEW_FOLDER_EXISTS
    return None


def set_current_db_path(new_path):
    """config.DB_PATH を切り替える（app_settings.json にも記憶される）。ロックは呼び出し元が先に取っておくこと。"""
    config.set_db_path(new_path)


def create_db_file(new_db_path):
    """新しい月次DB のファイルを作る（schema.sql の表）。計画の表は activate_new_db() で作る。"""
    init_database_at(new_db_path)


def activate_new_db(new_db_path):
    """引き継ぎなしで作った新しい DB に切り替え、計画の表を作る。ロックは呼び出し元が先に取っておくこと。"""
    config.set_db_path(new_db_path)
    init_kitting_plan_tables()


def record_db_created(folder, worker_name):
    """DB の作成（引き継ぎなし）を master.db の履歴に記録する。"""
    record_db_lifecycle_event(OPERATION_TYPE_CREATE, folder, worker_name=worker_name)


def restore_backup_to_new_folder(backup_file_path, folder_name):
    """
    検証済みのバックアップを、新しいフォルダ（APP_DATA_DIR/db/<folder_name>）にコピーする。戻り値は新しい inventory.db のパス。
    途中で失敗したら、作りかけのフォルダを消してから、例外をそのまま送出する（今の DB とロックは変えない）。
    """
    new_folder = db_folder_path(folder_name)
    try:
        return restore_backup_as_new_local_db(backup_file_path, folder_name)
    except Exception:
        if os.path.isdir(new_folder):
            shutil.rmtree(new_folder, ignore_errors=True)
        raise


def discard_restored_folder(folder_name):
    """復元したフォルダを消す（コピーの後で、切り替えのロックが取れなかったとき）。"""
    shutil.rmtree(db_folder_path(folder_name), ignore_errors=True)


def finish_restore(backup_file_path, folder_name, worker_name):
    """
    復元した DB に切り替えた後の処理。計画の表を作り（古いバックアップに無い表を補う）、取り込んだ DB の操作履歴と、
    master.db の履歴に記録する（master.db の記録は、取り込み先の DB を後で消しても残る）。
    """
    init_kitting_plan_tables()
    log_operation(
        worker_name,
        "バックアップからの取り込み",
        detail=f"元ファイル: {backup_file_path} / 取り込み先: {folder_name}",
    )
    record_db_lifecycle_event(
        OPERATION_TYPE_RESTORE_FROM_BACKUP, folder_name,
        worker_name=worker_name,
        source_info=backup_file_path,
    )


def delete_database(db_path, worker_name):
    """
    月次DB のファイル（と .lock・.lock.tmp）を消し、記録する。フォルダ自体は残す。確認は呼び出し元（ui/db_delete_helper.py）が済ませておくこと。
    操作履歴は、消した DB ではなく今の DB（config.DB_PATH。消す DB とは必ず違う）に記録する。master.db の履歴は消した後も残る。
    """
    folder_name = os.path.basename(os.path.dirname(db_path))
    delete_database_files(db_path)
    log_operation(
        worker_name,
        "月別DB削除",
        detail=db_path,
    )
    record_db_lifecycle_event(
        OPERATION_TYPE_DELETE, folder_name,
        worker_name=worker_name,
    )
