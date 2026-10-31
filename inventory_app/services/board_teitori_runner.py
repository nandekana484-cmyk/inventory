# services/board_teitori_runner.py
"""
ふだんの取得（board_teitori_service.refresh_normal）を、画面を出さずに背景で走らせる小さな部品（D-191）。
同時に走るのは1つだけ。走っている間に次の依頼が来たら、終わった後に1回だけ（溜まった依頼をまとめて）走らせる。
失敗は logging に残し、取り込みや前月引き継ぎの流れは止めない。画面に出す失敗は、BOM の共有フォルダを読めない場合だけで、
文を溜めておき（pop_board_teitori_errors()）、メイン画面が自分のスレッドで出す。GUI は import しない。
"""
import logging
import threading

import config
from services import board_teitori_service
from services.board_teitori_service import BomFolderUnavailableError, folder_unavailable_message

logger = logging.getLogger(__name__)


class BoardTeitoriRunner:
    def __init__(self, work=None):
        # work(db_paths) -> 集計。テストで差し替えられるようにしてある。
        self._work = work if work is not None else board_teitori_service.refresh_normal
        self._lock = threading.Lock()
        self._running = False
        self._pending = []                  # 走っている間に来た依頼の DB パス（重複なし、依頼の順）
        self._idle = threading.Event()
        self._idle.set()
        self.runs = 0                       # 実際に work を呼んだ回数（確認用）
        # 画面に出すエラーの文（同じ文は1つにまとめる）。別スレッドからは画面を触れないので、メイン画面が pop_errors() で受け取る。
        self._errors = []

    def request(self, db_path: str) -> bool:
        """
        db_path（依頼した時点の DB）の取得を頼む。戻り値は、すぐ走り始めたら True、走っている最中なので後回しにしたら False。
        db_path は依頼した時点の値を渡すこと（走り始めるまでに DB が切り替わっても取り違えない）。
        """
        with self._lock:
            if self._running:
                if db_path not in self._pending:
                    self._pending.append(db_path)
                return False
            self._running = True
            self._idle.clear()
        threading.Thread(target=self._loop, args=([db_path],), daemon=True).start()
        return True

    def _loop(self, db_paths: list):
        while True:
            try:
                self.runs += 1
                summary = self._work(db_paths)
                logger.info("基板丁数の対応表を更新しました: %s", summary)
            except BomFolderUnavailableError as e:
                # 共有フォルダを読めない失敗だけは、画面に出す（何も書かずに止めている。D-191）。
                logger.warning("基板丁数の対応表を更新できませんでした（共有フォルダを読めません: %s）", e.folder)
                self._add_error(folder_unavailable_message(e.folder))
            except Exception:
                logger.exception("基板丁数の対応表の更新に失敗しました（DB: %s）", ", ".join(db_paths))
            with self._lock:
                if not self._pending:
                    self._running = False
                    self._idle.set()
                    return
                db_paths, self._pending = self._pending, []

    def _add_error(self, message: str):
        with self._lock:
            if message not in self._errors:
                self._errors.append(message)

    def pop_errors(self) -> list:
        """画面に出すエラーの文を取り出す（取り出した分は消える）。メイン画面のスレッドから呼ぶ。"""
        with self._lock:
            errors, self._errors = self._errors, []
        return errors

    def wait_idle(self, timeout: float = None) -> bool:
        """走っている取得がなくなるまで待つ（テスト用）。"""
        return self._idle.wait(timeout)


_runner = BoardTeitoriRunner()


def pop_board_teitori_errors() -> list:
    """背景の取得が共有フォルダを読めずに止まったときの、画面に出す文のリスト（無ければ空）。"""
    return _runner.pop_errors()


def request_board_teitori_refresh() -> bool:
    """
    今の DB（config.DB_PATH を、この呼び出しの時点で控える）について、ふだんの取得を背景で頼む。
    既定DB（未選択）のときは何もしない。戻り値は、取得を始めた（または後回しにした）ら True、何もしなかったら False。
    """
    if config.is_default_db():
        return False
    _runner.request(config.DB_PATH)
    return True
