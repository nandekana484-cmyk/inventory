# services/db_lock_service.py
"""
共有フォルダ上のDBファイルに対する、ロックファイル方式の排他制御。

DBファイルと同じフォルダに `<DBファイル名>.lock` というJSONファイルを置き、
「誰が（worker_name・pc_name）・いつ取得し（acquired_at）・最後にいつ生存確認
したか（last_updated）」を記録する。SQLite自体のファイルロックは共有フォルダ
（SMB等）上では信頼性が低いことが知られているため、それを補うアプリ層の
簡易的な相互排他として使う。

「一定時間ハートビートが更新されなければ自動解除」という仕様のため、厳密な
（TOCTOU競合を完全に排除した）排他制御ではない点に注意。1つのDBを複数PCが
同時に開こうとする瞬間が完全に重ならない、という運用上の前提に立った簡易実装。
"""
import json
import os
import uuid
from datetime import datetime

# この時間（秒）以上ハートビートが更新されていないロックは、
# 保持者が異常終了したとみなして自動解除の対象にする。
LOCK_STALE_SECONDS = 30 * 60  # 30分

# このプロセス内でacquire_lock()が成功したdb_pathごとに、そのとき発行した
# トークンを覚えておく。release_lock()/update_heartbeat()は、ロックファイルの
# 中身がこのトークンと一致する場合のみ操作する（他者のロックを誤って
# 削除・更新しないようにするための確認）。
_owned_tokens = {}


def _lock_path(db_path: str) -> str:
    return db_path + ".lock"


def _read_lock(db_path: str):
    """ロックファイルの中身を辞書で返す。存在しない・壊れている場合はNone。"""
    path = _lock_path(db_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _is_stale(info: dict) -> bool:
    last_updated_raw = info.get("last_updated")
    try:
        last_updated = datetime.fromisoformat(last_updated_raw)
    except (TypeError, ValueError):
        # 形式不正で解釈できないロックは、安全側に倒して失効扱いにする。
        return True
    return (datetime.now() - last_updated).total_seconds() >= LOCK_STALE_SECONDS


def acquire_lock(db_path: str, worker_name: str, pc_name: str) -> bool:
    """
    db_path用のロックファイルの取得を試みる。

    取得できる条件：
      - ロックファイルが存在しない、または
      - 存在するが最終更新時刻からLOCK_STALE_SECONDS以上経過している（自動解除対象）。
    それ以外（他者が有効なロックを保持中）はFalseを返す。
    """
    existing = _read_lock(db_path)
    if existing is not None and not _is_stale(existing):
        return False

    token = uuid.uuid4().hex
    now = datetime.now().isoformat()
    new_info = {
        "worker_name": worker_name,
        "pc_name": pc_name,
        "acquired_at": now,
        "last_updated": now,
        "token": token,
    }

    lock_dir = os.path.dirname(db_path)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)

    with open(_lock_path(db_path), "w", encoding="utf-8") as f:
        json.dump(new_info, f, ensure_ascii=False, indent=2)

    _owned_tokens[db_path] = token
    return True


def release_lock(db_path: str) -> None:
    """
    db_pathのロックファイルを削除する。

    このプロセスがacquire_lock()で実際に取得したロック（_owned_tokensに
    記録されたトークンとロックファイルの中身が一致する場合）のみ削除する。
    既に他者が上書き・再取得している場合や、そもそも自分が取得していない
    場合は何もしない。
    """
    token = _owned_tokens.get(db_path)
    if token is None:
        return

    current = _read_lock(db_path)
    if current is not None and current.get("token") == token:
        try:
            os.remove(_lock_path(db_path))
        except OSError:
            pass

    _owned_tokens.pop(db_path, None)


def update_heartbeat(db_path: str) -> None:
    """
    自分が保持しているロックの最終更新時刻を現在時刻に更新する（生存確認）。

    自分が取得したロックでなくなっている場合（他者が既に上書きした等）は
    何もしない。
    """
    token = _owned_tokens.get(db_path)
    if token is None:
        return

    current = _read_lock(db_path)
    if current is None or current.get("token") != token:
        return

    current["last_updated"] = datetime.now().isoformat()
    with open(_lock_path(db_path), "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)


def get_lock_info(db_path: str):
    """
    現在のロック保持者の情報（worker_name・pc_name・acquired_at・last_updated）を
    辞書で返す。ロックファイルが無い・壊れている場合はNone。

    acquire_lock()が失敗した際に「誰が使用中か」をユーザーに表示する用途。
    内部管理用のtokenは含めない。
    """
    info = _read_lock(db_path)
    if info is None:
        return None
    return {
        "worker_name": info.get("worker_name"),
        "pc_name": info.get("pc_name"),
        "acquired_at": info.get("acquired_at"),
        "last_updated": info.get("last_updated"),
    }
