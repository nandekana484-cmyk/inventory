# ui/window_utils.py
"""
ウィンドウの中央寄せ共通ヘルパー。

center_window(window, parent=None) は、window（Toplevel/Tk）を、
「タスクバーを除いた作業領域」の中央へ配置する。呼び出し元は、
ウィジェットをすべて配置し終えた後（モーダルダイアログの
transient()/grab_set()/wait_window()より前）に1回呼ぶだけでよい。

2026-10-05の実装（D-54）は「parentの中央（parent省略時はディスプレイ中央）」
を基準にしていたが、「各画面がディスプレイの中央より若干下に表示される」
という報告を受けて調査した結果、2つの問題が判明したため、本モジュールを
書き直した（CANONICAL_DESIGN_DECISIONS.md D-6x参照）。

問題1：winfo_width()/winfo_height()・winfo_rootx()/winfo_rooty()は、
いずれもタイトルバー・枠を除いた「クライアント領域」の大きさ・位置を返す
（Tkinterの一般的な仕様）。一方、geometry("WxH+X+Y")の"+X+Y"部分は
ウィンドウ全体（タイトルバー・枠を含む外枠）の位置を指定する（実機で
GetWindowRect()と比較し確認済み）。旧実装はクライアント領域基準の値を
そのまま外枠位置としてgeometry()に渡していたため、タイトルバーの高さ
（実測約31px）・左右の枠（実測約8px）の分だけ、意図した中央より右下に
ずれていた（特に高さはタイトルバーの影響で目立つ）。

問題2：winfo_screenwidth()/winfo_screenheight()はディスプレイ全体の
解像度であり、タスクバーの分を除いた「作業領域」ではない。

これらを解消するため、Windows APIを ctypes 経由で直接呼び、
①ウィンドウの実際の外枠サイズ（GetWindowRect）、②タスクバーを除いた
作業領域（MonitorFromWindow + GetMonitorInfoW の rcWork）を取得して
中央寄せの計算に使う。追加の外部ライブラリは使わない（標準ライブラリの
ctypesのみ）。Windows以外の環境やAPI呼び出し失敗時は、従来相当の
近似値（winfo_width/height・winfo_screenwidth/height）にフォールバックし、
例外を発生させない。

配置基準の変更（parent中央→作業領域中央）：
旧実装は「parentの中央」に重ねる設計だったが、今回「メインメニューを
移動していても、開く画面は作業領域の中央に表示する」という要件に
変更された。そのため、parent引数は「どのモニターを基準にするか」を
決めるためだけに使う（parentが現在表示中ならそのモニター、それ以外は
window自身の現在位置のモニター、取得失敗時は従来通りwinfo_screenwidth/
height基準）。parentの位置そのものに中央を合わせる処理は行わない。

複数モニター時：parent（無ければwindow自身）が乗っているモニターを
MonitorFromWindow(..., MONITOR_DEFAULTTONEAREST)で求め、そのモニターの
作業領域（GetMonitorInfoWのrcWork）を使う。「最も近いモニター」を採用
するため、ウィンドウが2つのモニターの境界にまたがっている場合は片方に
決定される（Windows標準のMONITOR_DEFAULTTONEARESTの挙動に従う）。

DPIについて：本アプリ・本関数はDPI非対応（DPI-unaware）のまま変更して
いない。DPI非対応プロセスでは、Windowsが実際のモニターDPIに関わらず
一律96 DPI（100%相当）としてプロセスに見せかけ、表示は内部で自動拡大
される（実機確認：GetDpiForWindow()は常に96を返す）。ctypes経由の
座標・サイズも、Tkinter自身が使う座標・サイズと同じこの「見せかけの」
座標系で得られるため、両者の間で単位変換は不要（同一プロセス内なので
自然に一致する）。表示倍率（125%・150%等）が変わっても、この一貫性は
保たれる。DPI対応化（per-monitorクリアな表示）自体は本修正の対象外。

ちらつき防止：位置決定の間、windowをwithdraw()で一旦非表示にし、最終的な
geometry()適用後にdeiconify()で表示する（ウィンドウが withdraw 状態のままでも
GetWindowRect・MonitorFromWindow・GetMonitorInfoWはいずれも正しい値を
返すことを実機確認済み）。

画面（作業領域）より大きいウィンドウの扱い：中央寄せの結果が作業領域から
はみ出す場合は、タイトルバー・左端が必ず作業領域内に収まるよう、
左上を作業領域の左上に揃える（右・下方向にはみ出すことは許容する）。

対象外：production_import_staging_window.py の一時通知（notice、
overrideredirect(True)の非ボーダー通知）。サイズ・自動サイズ4か所の扱い・
再表示時に位置を変更しない動作は変更していない。
"""
import ctypes

# GA_ROOT：GetAncestor()で「所有者を遡った最上位のウィンドウ」を得るための定数。
_GA_ROOT = 2
# MONITOR_DEFAULTTONEAREST：対象ウィンドウが乗っていない場合でも、
# 最も近いモニターをフォールバックとして返すMonitorFromWindow()の定数。
_MONITOR_DEFAULTTONEAREST = 2


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _root_hwnd(window):
    """windowのTk内部ID（winfo_id()）から、実際のOSトップレベルウィンドウの
    HWNDを得る。GetAncestor()が失敗・0を返した場合はwinfo_id()自体を使う
    （Toplevel自体が既にトップレベルの場合等への保険）。"""
    hwnd = window.winfo_id()
    user32 = ctypes.windll.user32
    root = user32.GetAncestor(hwnd, _GA_ROOT)
    return root or hwnd


def _get_outer_size(window):
    """
    windowの現在の外枠サイズ（タイトルバー・枠を含む、GetWindowRect基準）を
    (width, height) で返す。Windows以外の環境・API呼び出し失敗時はNoneを返す
    （呼び出し元はwinfo_width()/winfo_height()にフォールバックすること）。
    """
    try:
        user32 = ctypes.windll.user32
        hwnd = _root_hwnd(window)
        rect = _RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        return None


def _get_work_area(window, parent):
    """
    中央寄せの基準とする「タスクバーを除いた作業領域」を
    (left, top, width, height) で返す。

    モニターの選び方：parentが存在し現在表示中（withdrawn/iconicでない）なら
    parentが乗っているモニター、それ以外はwindow自身の（この時点でまだ
    withdraw中・位置決定前の）現在位置が乗っているモニターを使う
    （MonitorFromWindow(..., MONITOR_DEFAULTTONEAREST)、「メインメニューが
    表示されているモニター」の実務上の近似：直接の親がメインメニューでなく
    ても、親子関係を遡れば最終的にメインメニューが開かれたモニターに一致する）。

    Windows以外の環境・API呼び出し失敗時は、ディスプレイ全体
    （winfo_screenwidth()/winfo_screenheight()、タスクバーを除かない近似値）
    にフォールバックする。例外は発生させない。
    """
    try:
        parent_visible = (
            parent is not None
            and parent.winfo_exists()
            and parent.state() not in ("withdrawn", "iconic")
        )
        ref = parent if parent_visible else window

        user32 = ctypes.windll.user32
        hwnd = _root_hwnd(ref)
        monitor = user32.MonitorFromWindow(hwnd, _MONITOR_DEFAULTTONEAREST)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError("GetMonitorInfoW failed")

        r = info.rcWork
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return (0, 0, window.winfo_screenwidth(), window.winfo_screenheight())


def center_window(window, parent=None):
    window.withdraw()
    window.update_idletasks()

    client_w = window.winfo_width()
    client_h = window.winfo_height()

    outer_size = _get_outer_size(window)
    if outer_size is None:
        # ctypes経由での外枠サイズ取得に失敗した場合（Windows以外の環境等）、
        # クライアント領域サイズを外枠サイズの近似値として使う（旧実装相当の
        # フォールバック、タイトルバー分のずれは残るが例外は出さない）。
        outer_w, outer_h = client_w, client_h
    else:
        outer_w, outer_h = outer_size

    work_left, work_top, work_w, work_h = _get_work_area(window, parent)

    x = work_left + (work_w - outer_w) // 2
    y = work_top + (work_h - outer_h) // 2

    # 作業領域よりウィンドウが大きい場合、タイトルバー・左端が必ず作業領域内に
    # 収まるよう左上を作業領域の左上に揃える（右・下へのはみ出しは許容する）。
    x = max(work_left, min(x, work_left + max(0, work_w - outer_w)))
    y = max(work_top, min(y, work_top + max(0, work_h - outer_h)))

    window.geometry(f"{client_w}x{client_h}+{x}+{y}")
    window.deiconify()
