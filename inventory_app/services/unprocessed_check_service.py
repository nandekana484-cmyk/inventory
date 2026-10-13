# services/unprocessed_check_service.py
"""
在庫差異レポート（ui/inventory_diff_window.py）を開く前の注意喚起用チェック。

NG一覧・仕掛一覧の行の組み立て（services/ng_list_service.py・services/wip_list_service.py の
fetch_ng_list_rows()/fetch_wip_list_rows()、DB読み取りのみ）をそのまま再利用し、
「未処理」（NG一覧：status="未展開"、仕掛一覧：status="未確定"、いずれも対象外指定されていないもの）の件数を数える。
集計ロジックを複製しないのは、片方だけを直して定義がずれるのを防ぐため。列の位置は NG_LIST_COLUMNS / WIP_LIST_COLUMNS で引く。
以前は画面（ui/ng_input_window.py・ui/wip_expansion_window.py）から import していた（services → ui の逆転）。段階4で services へ移した。
"""
from services import ng_list_service, wip_list_service


def check_unprocessed_items() -> dict:
    """
    NG一覧・仕掛一覧それぞれの未処理件数を数える。

    戻り値：{"ng_unprocessed_count": int, "wip_unprocessed_count": int}
    """
    ng_col_index = {key: i for i, key in enumerate(ng_list_service.NG_LIST_COLUMNS)}
    ng_rows = ng_list_service.fetch_ng_list_rows()
    ng_unprocessed_count = sum(
        1 for row in ng_rows
        if row[ng_col_index["status"]] == "未展開" and row[ng_col_index["excluded"]] != "対象外"
    )

    wip_col_index = {key: i for i, key in enumerate(wip_list_service.WIP_LIST_COLUMNS)}
    wip_rows = wip_list_service.fetch_wip_list_rows()
    wip_unprocessed_count = sum(
        1 for row in wip_rows
        if row[wip_col_index["status"]] == "未確定" and row[wip_col_index["excluded"]] != "対象外"
    )

    return {
        "ng_unprocessed_count": ng_unprocessed_count,
        "wip_unprocessed_count": wip_unprocessed_count,
    }
