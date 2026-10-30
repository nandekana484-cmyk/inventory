# services/board_teitori_runner.py
"""
ふだんの取得（board_teitori_service.refresh_normal）を、画面を出さずに背景で走らせる小さな部品（D-191）。
同時に走るのは1つだけ。走っている間に次の依頼が来たら、終わった後に1回だけ（溜まった依頼をまとめて）走らせる。
失敗してもダイアログは出さず、logging に残す（取り込みや前月引き継ぎの流れを止めない）。GUI は import しない。
"""
import logging
import threading

import config
from services import board_teitori_service

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
            except Exception:
                logger.exception("基板丁数の対応表の更新に失敗しました（DB: %s）", ", ".join(db_paths))
            with self._lock:
                if not self._pending:
                    self._running = False
                    self._idle.set()
                    return
                db_paths, self._pending = self._pending, []

    def wait_idle(self, timeout: float = None) -> bool:
        """走っている取得がなくなるまで待つ（テスト用）。"""
        return self._idle.wait(timeout)


_runner = BoardTeitoriRunner()


def request_board_teitori_refresh() -> bool:
    """
    今の DB（config.DB_PATH を、この呼び出しの時点で控える）について、ふだんの取得を背景で頼む。
    既定DB（未選択）のときは何もしない。戻り値は、取得を始めた（または後回しにした）ら True、何もしなかったら False。
    """
    if config.is_default_db():
        return False
    _runner.request(config.DB_PATH)
    return True
