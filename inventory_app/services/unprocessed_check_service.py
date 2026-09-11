# services/unprocessed_check_service.py
"""
在庫差異レポート（ui/inventory_diff_window.py）を開く前の注意喚起用チェック。

NG一覧（ui/ng_input_window.py）・仕掛一覧（ui/wip_expansion_window.py）の
一覧表示ロジック（_fetch_ng_list_rows()/_fetch_wip_list_rows()、いずれも
DB読み取りのみでTkinterウィジェットには一切触れない@staticmethod）をそのまま
再利用し、「未処理」（NG一覧：status="未展開"、仕掛一覧：status="未確定"、
いずれも対象外指定されていないもの）の件数を数える。

集計ロジック自体をこのモジュールに複製するのではなく、画面側の関数を
そのままimportして使う方針を採った（services層からui層をimportする形には
なるが、以下の理由により実害が無いと判断した）：
  - 両関数ともDB読み取りのみでTkinterに一切依存しないため、import自体に
    副作用は無い（ウィンドウを開く必要も無い）。
  - 集計ロジック（申告・展開済みデータのマージ、状態判定等）を複製すると、
    将来どちらか一方だけを修正した場合に定義がずれてしまうリスクがある。
    単一の実装を再利用する方が正確性を保ちやすい。
  - 列の並び順は各画面クラスの NG_LIST_COLUMNS / WIP_LIST_COLUMNS
    （クラス属性として公開）を参照するため、位置の決め打ちにもならない。
"""
from ui.ng_input_window import NgInputWindow
from ui.wip_expansion_window import WipExpansionWindow


def check_unprocessed_items() -> dict:
    """
    NG一覧・仕掛一覧それぞれの未処理件数を数える。

    戻り値：{"ng_unprocessed_count": int, "wip_unprocessed_count": int}
    """
    ng_col_index = {key: i for i, key in enumerate(NgInputWindow.NG_LIST_COLUMNS)}
    ng_rows = NgInputWindow._fetch_ng_list_rows()
    ng_unprocessed_count = sum(
        1 for row in ng_rows
        if row[ng_col_index["status"]] == "未展開" and row[ng_col_index["excluded"]] != "対象外"
    )

    wip_col_index = {key: i for i, key in enumerate(WipExpansionWindow.WIP_LIST_COLUMNS)}
    wip_rows = WipExpansionWindow._fetch_wip_list_rows()
    wip_unprocessed_count = sum(
        1 for row in wip_rows
        if row[wip_col_index["status"]] == "未確定" and row[wip_col_index["excluded"]] != "対象外"
    )

    return {
        "ng_unprocessed_count": ng_unprocessed_count,
        "wip_unprocessed_count": wip_unprocessed_count,
    }
