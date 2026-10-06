# ui/db_delete_helper.py
"""
月別DB削除の確認ダイアログ＋実行の共通処理。ui/main_window.py（ローカルDB一覧）から
使う（削除の安全確認ロジック自体はservices.db_delete_serviceに集約し、本モジュールは
その結果に応じたダイアログの出し分け・実際の削除呼び出しのみを担当する）。
"""
import os
from tkinter import messagebox

from services.db_delete_service import check_delete_safety, delete_database_files
from models.operation_log import log_operation
from models.db_lifecycle_log import record_db_lifecycle_event, OPERATION_TYPE_DELETE


def confirm_and_delete_database(parent, db_path: str, current_worker=None) -> bool:
    """
    db_pathの削除可否をユーザーに確認し、承認されれば実際に削除する。

    安全対策（優先順）：
      1. 削除対象が現在接続中のDB（config.DB_PATH）と同一 → 削除を拒否する
         （確認ダイアログすら出さず、エラーダイアログのみ表示）。
      2. 他者が使用中とみなせる有効なロックが存在する
         （services.db_lock_service.get_active_lock_info()。30分以上更新が
         無い自動解除対象のロックは対象外）→ ロック保持者情報付きの
         強めの警告ダイアログ。
      3. 上記いずれにも該当しない → 通常の削除確認ダイアログ。

    戻り値：実際に削除を実行したかどうか。呼び出し元（一覧画面）はTrueの
    場合のみ一覧を再取得すればよい。

    操作履歴の記録先について：削除対象（db_path）自体は削除により消えてしまう
    ため、そのDB内のoperation_logには記録を残せない。上記安全対策1により
    db_pathは常にconfig.DB_PATH（現在接続中の別DB）と異なることが保証されて
    いるため、代わりに現在接続中のDB（config.DB_PATH）のoperation_logへ、
    「どのDBを削除したか」をdetailに含めて記録する（記録を諦めない方針）。
    """
    safety = check_delete_safety(db_path)

    if safety["is_current"]:
        messagebox.showerror(
            "削除できません",
            "現在使用中のデータベースは削除できません。別のDBに切り替えてから削除してください。",
            parent=parent,
        )
        return False

    active_lock = safety["active_lock"]
    if active_lock:
        worker_name = active_lock.get("worker_name") or "不明"
        pc_name = active_lock.get("pc_name") or "不明"
        confirmed = messagebox.askyesno(
            "警告：使用中の可能性があります",
            "他の利用者が使用中の可能性があります。\n"
            f"ロック保持者：{worker_name}（{pc_name}）\n\n"
            "本当に削除しますか？",
            icon="warning",
            parent=parent,
        )
    else:
        confirmed = messagebox.askyesno(
            "確認",
            "このデータベースを削除します。よろしいですか？この操作は元に戻せません。",
            parent=parent,
        )

    if not confirmed:
        return False

    folder_name = os.path.basename(os.path.dirname(db_path))
    delete_database_files(db_path)
    log_operation(
        (current_worker or {}).get("name", "unknown"),
        "月別DB削除",
        detail=db_path,
    )
    # 削除履歴をmaster.dbへ記録する（2026-10-06追加。上のlog_operation()は
    # 削除対象のDB自体のoperation_logへの記録のため、db_path削除後は失われる。
    # master.db側の記録は削除後も残る）。
    record_db_lifecycle_event(
        OPERATION_TYPE_DELETE, folder_name,
        worker_name=(current_worker or {}).get("name", "unknown"),
    )
    messagebox.showinfo("削除完了", f"データベースを削除しました：\n{db_path}", parent=parent)
    return True
