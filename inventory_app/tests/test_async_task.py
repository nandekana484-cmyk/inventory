"""ui/async_task.py の run_in_background()（段階6）。メイン画面での使われ方は test_main_window_background.py で固定している。"""
import threading
import time
import tkinter as tk

from ui.async_task import run_in_background
from ui.loading_window import LoadingWindow


def _pump(root, until, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timeout")


def _label_text(window):
    def walk(w):
        for c in w.winfo_children():
            yield c
            yield from walk(c)
    return [c.cget("text") for c in walk(window) if c.winfo_class() == "TLabel"][0]


def test_success_runs_in_other_thread_and_closes_loading(tk_root):
    calls = []
    main_thread = threading.current_thread()

    def work():
        calls.append(("work", threading.current_thread() is main_thread))
        return 42

    loading = run_in_background(tk_root, "処理中です…", work,
                                lambda result: calls.append(("ok", result, loading.winfo_exists())),
                                lambda e, tb: calls.append(("error", e)))
    assert isinstance(loading, LoadingWindow)
    assert _label_text(loading) == "処理中です…"
    _pump(tk_root, lambda: len(calls) == 2)
    assert calls == [("work", False), ("ok", 42, False)]   # 別スレッドで実行し、結果を渡す前に読み込み中の表示を閉じる


def test_error_passes_exception_and_traceback(tk_root):
    calls = []

    def work():
        raise ValueError("失敗しました")

    loading = run_in_background(tk_root, None, work, lambda r: calls.append(("ok", r)),
                                lambda e, tb: calls.append(("error", e, tb)))
    assert _label_text(loading) == "生産実績画面を読み込んでいます…"   # message=None なら LoadingWindow の既定の文言
    _pump(tk_root, lambda: calls)
    (kind, error, traceback_text), = calls
    assert kind == "error" and isinstance(error, ValueError) and str(error) == "失敗しました"
    assert traceback_text.startswith("Traceback (most recent call last):")
    assert traceback_text.rstrip().endswith("ValueError: 失敗しました")
    assert not loading.winfo_exists()


def test_main_window_uses_run_in_background():
    import inspect
    from ui import main_window
    source = inspect.getsource(main_window)
    assert source.count("run_in_background(") == 4
    assert "threading.Thread" not in source and "queue.Queue" not in source
