"""
ロット状態の「確認事項」の文言と文字色の区分（日報・月報・ロット進捗チェック・日々の引落一覧で共通）。
services/lot_status_service.py が500行に近づいたので、そこから移した（中身は変えていない）。lot_status_service.py からも参照できる。
"""

# 引落を0扱いにする構成基板数チェックの status（日報・月報・仕掛数量抽出・ロット進捗チェックで共通）。
# 不足（shortfall）は構成がそろわないので引落を確定できない。未登録・不一致・超過は比べられないので実績ベースのまま（利用者の決定）。
DRAWDOWN_ZERO_STATUSES = {"shortfall"}


# 引落は実績ベースのままでも、構成基板数と比べられないので「確認・修正が必要」（赤字）とする status。
NEEDS_REVIEW_STATUSES = {"unregistered", "board_count_inconsistent", "excess"}

# 発注数の食い違いの文言（ロット進捗チェックの備考に出す。実績レポートの「確認事項」には出さない。D-127）。
ORDER_QTY_REMARK_BETWEEN_FILES = "発注数不一致(ファイル間)"
ORDER_QTY_REMARK_WITHIN_FILE = "発注数不一致(同一ファイル内)"


def _build_lot_status_remarks(status: str, board_count_values, shortfall_count: int,
                                unregistered_board_names: list) -> str:
    """
    「確認事項」欄の理由の文言を組み立てる（日報・月報・ロット進捗チェックで共通。文言はここにだけ書く）。
    複数の理由は " / " でつなぐ。status が unregistered 以外で一部の基板名が未登録なら「一部未登録」を足す。
    """
    parts = []
    if status == "unregistered":
        parts.append("構成基板数マスタ未登録(引落は未検証)")
    elif status == "board_count_inconsistent":
        values_str = ", ".join(f"{v:g}" for v in (board_count_values or []))
        parts.append(f"構成基板数がロット内で不一致 {values_str}(引落は未検証)")
    elif status == "excess":
        parts.append("実ファイルNo数がマスタの構成基板数を超過(引落は未検証)")
    elif status == "shortfall":
        parts.append(f"構成基板数不足: 未確定{shortfall_count}件(引落0)")

    if status != "unregistered" and unregistered_board_names:
        parts.append("一部未登録: " + "、".join(unregistered_board_names))

    return " / ".join(parts)


def _lot_status_color_category(status: str, unregistered_board_names: list):
    """
    「確認事項」欄の文字色の区分を返す: "needs_review"（赤）／"shortfall"（オレンジ）／None。
    一部未登録と不足が両方ある場合は、赤（確認・修正が必要）を優先する（優先順位の指定は無く、実装時に決めた）。
    """
    if status in NEEDS_REVIEW_STATUSES or unregistered_board_names:
        return "needs_review"
    if status in DRAWDOWN_ZERO_STATUSES:
        return "shortfall"
    return None


# 「不足」の備考に出すファイルNoの内訳の最大件数（利用者の決定、D-184）。
SHORTFALL_DETAIL_MAX_FILES = 3


def format_shortfall_detail(lot_eval) -> str:
    """
    「不足」のロットの内訳（D-179。ロット進捗チェックの備考）。計画にあるファイルNo（面2があれば面1を除いた、表示している行）と基板名。
    例「構成基板数 2 に対し、計画のファイルNo は 1 件（0459：FD-Q10C***ﾋｮｳｼﾞ）」。1つのファイルNoに基板名が複数あれば「・」でつなぐ。
    内訳が SHORTFALL_DETAIL_MAX_FILES 件を超えるときは、先頭（ファイルNo順）だけを出し、残りを「ほかN件」とまとめる（D-184）。
    """
    names_by_file = {}
    for board in lot_eval["boards"]:
        for f in board["files"]:
            names_by_file.setdefault(f["setup_file_no"], [])
            if board["board_name"] not in names_by_file[f["setup_file_no"]]:
                names_by_file[f["setup_file_no"]].append(board["board_name"])
    items = [f"{fn}：{'・'.join(names)}" for fn, names in sorted(names_by_file.items())]
    if len(items) > SHORTFALL_DETAIL_MAX_FILES:
        items = items[:SHORTFALL_DETAIL_MAX_FILES] + [f"ほか{len(items) - SHORTFALL_DETAIL_MAX_FILES}件"]
    files = "、".join(items)
    return f"構成基板数 {lot_eval['board_count']:g} に対し、計画のファイルNo は {len(names_by_file)} 件（{files}）"


def format_side2_wait_remark(file_nos) -> str:
    """面2待ちのファイルNoがあるロットの備考（D-180）。例「面2待ち（ファイル0766）」「面2待ち（ファイル0741・0742）」。"""
    return "面2待ち（ファイル" + "・".join(file_nos) + "）"
