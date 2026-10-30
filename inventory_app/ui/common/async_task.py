"""
重い処理を別スレッドで実行し、終わったら UI スレッドで結果を受け取る部品（段階6）。
LoadingWindow を出す → threading.Thread(daemon=True) で実行 → queue.Queue に結果を入れる → after(200) でポーリング、という
各画面で同じだったパターンをまとめた。今は ui/main_window.py の5か所だけが使う（ほかの画面は以前のまま）。
"""
import queue
import threading
import traceback

from ui.loading_window import LoadingWindow

POLL_INTERVAL_MS = 200


def run_in_background(parent, message, work_fn, on_success, on_error):
    """
    work_fn() を別スレッドで実行する。実行中は LoadingWindow（message が None なら既定の文言）を出す。
    終わったら UI スレッドで LoadingWindow を閉じてから、成功なら on_success(work_fn の戻り値)、
    例外なら on_error(例外オブジェクト, traceback の文字列) を呼ぶ。traceback は別スレッドの except の中で取ったもの。
    戻り値は LoadingWindow（閉じるのはこの関数に任せること）。
    """
    loading = LoadingWindow(parent) if message is None else LoadingWindow(parent, message=message)
    result_queue = queue.Queue()

    def _work():
        try:
            result = work_fn()
        except Exception as e:
            result_queue.put((False, (e, traceback.format_exc())))
            return
        result_queue.put((True, result))

    threading.Thread(target=_work, daemon=True).start()

    def _poll():
        try:
            success, payload = result_queue.get_nowait()
        except queue.Empty:
            parent.after(POLL_INTERVAL_MS, _poll)
            return

        loading.destroy()
        if success:
            on_success(payload)
        else:
            error, traceback_text = payload
            on_error(error, traceback_text)

    parent.after(POLL_INTERVAL_MS, _poll)
    return loading
