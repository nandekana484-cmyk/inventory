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

書き込みの原子性について：ロックファイルへの書き込み（acquire_lock()・
release_lock()の削除を除く・update_heartbeat()）は、いずれも一時ファイル
（`<ロックファイル>.tmp`）へ書き込んでから`os.replace()`で本ファイルへ
原子的に置き換える（_write_lock_atomic()参照）。直接本ファイルへ
open()+書き込みする方式だと、書き込み途中でのプロセス強制終了・共有フォルダの
瞬断により、ファイルが半端な内容のまま残る（壊れる）リスクがあるため。

破損検知について：_read_lock()は、ロックファイルが存在するのに内容を正しく
読み取れない場合（JSON不正・OSError）、Noneを返さずLockFileCorruptedErrorを
送出する（「ロックが無い」＝取得可能、と誤認しないための「フェイルクローズ」
設計）。ファイル自体が存在しない場合（初回利用・誤削除等）は、この関数の
責務としては区別できないため、従来通り「ロック無し」としてNoneを返す
（正当な初回利用のケースと誤削除のケースを見分ける手段が無いため。
既存の運用を変えないための現状維持）。
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


class LockFileCorruptedError(Exception):
    """ロックファイルが存在するが、内容を正しく読み取れない（壊れている）ことを示す。"""


def _lock_path(db_path: str) -> str:
    return db_path + ".lock"


def _read_lock(db_path: str):
    """
    ロックファイルの中身を辞書で返す。

    ファイルが存在しない場合はNone（「ロック無し」＝正当な状態）。
    ファイルは存在するが内容を正しく読み取れない場合（JSON不正・OSError）は
    LockFileCorruptedErrorを送出する（Noneを返す「フェイルオープン」にすると、
    破損＝「ロック無し」と誤認して誰でも取得できてしまうため）。
    """
    path = _lock_path(db_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise LockFileCorruptedError(
            f"ロックファイルの内容を読み取れません: {path}"
        ) from e


def _write_lock_atomic(db_path: str, info: dict) -> None:
    """
    ロックファイルへの原子的な書き込み。同じディレクトリ内の一時ファイル
    （`<ロックファイル>.tmp`）へ書き込み・flush・fsyncした上で、os.replace()で
    本ファイルへ置き換える。os.replace()はPOSIX・Windowsいずれでも単一の
    原子的操作であるため、この置き換えの最中にプロセスが強制終了しても、
    本ファイルは「置き換え前の古い内容のまま」か「置き換え後の新しい内容」の
    いずれかであり、中途半端な内容になることはない。
    """
    lock_path = _lock_path(db_path)
    lock_dir = os.path.dirname(lock_path)
    if lock_dir:
        os.makedirs(lock_dir, exist_ok=True)

    tmp_path = lock_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, lock_path)
    except Exception:
        # 一時ファイルへの書き込み・os.replace()自体が失敗した場合、ゴミとして
        # 残った一時ファイルを可能な範囲で片付ける（本ファイルは触っていないため
        # 無事なまま）。削除自体に失敗しても（別プロセスが触っている等）、
        # 元の例外を優先してそのまま送出する。
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _is_stale(info: dict) -> bool:
    last_updated_raw = info.get("last_updated")
    try:
        last_updated = datetime.fromisoformat(last_updated_raw)
    except (TypeError, ValueError):
        # 形式不正で解釈できないロックは、安全側に倒して失効扱いにする。
        return True
    return (datetime.now() - last_updated).total_seconds() >= LOCK_STALE_SECONDS


def acquire_lock(db_path: str, worker_name: str, pc_name: str, force: bool = False) -> bool:
    """
    db_path用のロックファイルの取得を試みる。

    取得できる条件（force=Falseの通常時）：
      - ロックファイルが存在しない、または
      - 既存ロックのworker_name・pc_nameが、今回acquire_lock()を呼んでいる
        worker_name・pc_nameと完全一致する（同一作業者が同一PCから再取得しよう
        としている）、または
      - 存在するが最終更新時刻からLOCK_STALE_SECONDS以上経過している（自動解除対象）。
    それ以外（別の作業者・別のPCが有効なロックを保持中）はFalseを返す。

    「同一作業者・同一PC」を経過時間の判定より優先する理由：アプリが正常終了せず
    （強制終了・PCクラッシュ等で`release_lock()`が呼ばれないまま）ロックファイルが
    残った場合、本人が同じPCからすぐに再ログインしようとしても、他の誰かが
    使用中なわけではないのに30分待たされてしまう不便を解消するため。他者が
    使用中のロックを誤って奪ってしまうリスクは、worker_name・pc_nameの完全一致を
    条件にすることで避けている（別PC・別作業者からの取得は従来通り経過時間判定のまま）。

    ロックファイルが存在するが内容を読み取れない（壊れている）場合、
    force=FalseならLockFileCorruptedErrorをそのまま送出する（自動では解除・
    上書きしない。同一作業者・同一PCであっても、壊れたロックの中身自体を
    信頼できない以上この例外は変わらず送出する。呼び出し元は、他の利用者が
    本当に使用中でないかをユーザーに確認させた上で、force=Trueで再度呼び出すこと）。

    force=Trueの場合、既存ロックの有効・無効・破損の有無を一切確認せず、
    無条件に新しいロックで上書きする（「使用中でないことを確認した上での
    強制取得」という明示的なユーザー操作専用。自動リトライ等から呼ばないこと）。
    """
    if not force:
        existing = _read_lock(db_path)
        if existing is not None:
            same_owner = (
                existing.get("worker_name") == worker_name
                and existing.get("pc_name") == pc_name
            )
            if not same_owner and not _is_stale(existing):
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

    _write_lock_atomic(db_path, new_info)

    _owned_tokens[db_path] = token
    return True


def release_lock(db_path: str) -> None:
    """
    db_pathのロックファイルを削除する。

    このプロセスがacquire_lock()で実際に取得したロック（_owned_tokensに
    記録されたトークンとロックファイルの中身が一致する場合）のみ削除する。
    既に他者が上書き・再取得している場合や、そもそも自分が取得していない
    場合は何もしない。

    ロックファイルが壊れていて読み取れない場合（LockFileCorruptedError）も、
    自分のトークンと一致するか確認できない以上、安全側に倒して削除しない
    （壊れたファイルの後始末はユーザーの明示操作＝次回acquire_lock(force=True)
    に委ねる）。
    """
    token = _owned_tokens.get(db_path)
    if token is None:
        return

    try:
        current = _read_lock(db_path)
    except LockFileCorruptedError:
        _owned_tokens.pop(db_path, None)
        return

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
    何もしない。ロックファイルが壊れていて読み取れない場合
    （LockFileCorruptedError）も同様に何もしない（バックグラウンドの定期処理
    のため、ここではユーザーに確認を求めず静かに諦める。次回のハートビートや、
    ユーザーによる明示的な再取得操作に委ねる）。
    """
    token = _owned_tokens.get(db_path)
    if token is None:
        return

    try:
        current = _read_lock(db_path)
    except LockFileCorruptedError:
        return

    if current is None or current.get("token") != token:
        return

    current["last_updated"] = datetime.now().isoformat()
    _write_lock_atomic(db_path, current)


def get_lock_info(db_path: str):
    """
    現在のロック保持者の情報（worker_name・pc_name・acquired_at・last_updated）を
    辞書で返す。ロックファイルが無い・壊れている場合はNone。

    acquire_lock()が失敗した際に「誰が使用中か」をユーザーに表示する用途。
    内部管理用のtokenは含めない。壊れている場合にNoneを返す（例外を送出しない）
    のは、この関数自体は「表示できる情報が無い」ことを伝えるための補助関数であり、
    破損の検知・ユーザーへの警告はacquire_lock()側の責務とするため。
    """
    try:
        info = _read_lock(db_path)
    except LockFileCorruptedError:
        return None
    if info is None:
        return None
    return {
        "worker_name": info.get("worker_name"),
        "pc_name": info.get("pc_name"),
        "acquired_at": info.get("acquired_at"),
        "last_updated": info.get("last_updated"),
    }


def get_active_lock_info(db_path: str):
    """
    削除等の危険な操作の前に、「他者が今も使用中とみなせる有効なロックが
    存在するか」を確認するための補助関数。

    get_lock_info()と似ているが、LOCK_STALE_SECONDS（30分）以上更新が無い
    自動解除対象のロック（acquire_lock()なら上書きで取得できてしまう状態）を
    「有効なロックではない」として区別する点が異なる。get_lock_info()自体は
    acquire_lock()失敗時に「誰が使用中か」をそのまま表示する用途のため、この
    区別を行わない（既存の呼び出し元の挙動を変えないよう、get_lock_info()
    自体は変更せずこちらを別関数として新設した）。

    戻り値：有効なロックがあれば{"worker_name", "pc_name", "acquired_at",
    "last_updated"}、無ければ（ロック無し・破損・自動解除対象のいずれか）None。
    """
    try:
        info = _read_lock(db_path)
    except LockFileCorruptedError:
        return None
    if info is None or _is_stale(info):
        return None
    return {
        "worker_name": info.get("worker_name"),
        "pc_name": info.get("pc_name"),
        "acquired_at": info.get("acquired_at"),
        "last_updated": info.get("last_updated"),
    }
