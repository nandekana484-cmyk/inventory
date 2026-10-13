"""
一覧（Treeview）の列ごとの絞り込みと並べ替えの共通部品。生産実績入力の計画一覧・NG 一覧・仕掛一覧で使う。
段階6で3画面の同じ判定を集めた。この部分はウィジェットを使わない（行が条件に合うか、並べ替えのキー）。
画面ごとの違いは揃えずに、引数で渡す（数値として並べる列、期間指定の有無など）。
"""


def build_filter_predicates(text_filters, checkbox_filters):
    """
    列ごとの述語（value: str -> bool）の辞書を作る。指定の無い列は含めない。
    text_filters: {列: 入力された文字列}。前後の空白を除いて空なら絞り込まない。大文字・小文字を区別しない部分一致。
    checkbox_filters: {列: 選ばれた値の集合}。str() した値の完全一致。空の集合ならどの行も通さない。
    同じ列が両方にあれば、チェックボックスの方を使う。
    """
    predicates = {}
    for col_key, text in text_filters.items():
        text = text.strip()
        if not text:
            continue
        needle = text.lower()
        predicates[col_key] = lambda value, needle=needle: needle in value.lower()

    for col_key, selected_values in checkbox_filters.items():
        predicates[col_key] = lambda value, selected=selected_values: value in selected

    return predicates


def row_matches(row, predicates, col_index):
    """行がすべての述語に合うか（AND）。値は str() してから述語に渡す。col_index は {列: 行のタプルの位置}。"""
    for col_key, predicate in predicates.items():
        index = col_index[col_key]
        if not predicate(str(row[index])):
            return False
    return True


def filter_rows(rows, predicates, col_index):
    """述語に合う行のリスト。述語が無ければ rows をそのまま（同じリストを）返す。"""
    if not predicates:
        return rows
    return [row for row in rows if row_matches(row, predicates, col_index)]


def date_range_predicate(from_text, to_text):
    """
    期間（"YYYY-MM-DD" か "YYYY/MM/DD"）から、日時の文字列用の述語を作る。両方空欄なら None。
    値の先頭10文字を「/」区切りにそろえて文字列で比べる（実データは "YYYY/MM/DD HH:MM:SS"）。
    """
    if not from_text and not to_text:
        return None

    from_date = from_text.replace("-", "/") if from_text else None
    to_date = to_text.replace("-", "/") if to_text else None

    def predicate(value):
        date_part = value[:10].replace("-", "/")
        if from_date and date_part < from_date:
            return False
        if to_date and date_part > to_date:
            return False
        return True

    return predicate


def make_sort_key(col, numeric_cols):
    """
    並べ替えのキー関数。col が numeric_cols にあれば float にし、数値にできない値は -inf（昇順で先頭）。
    それ以外の列は文字列のまま比べる。
    """
    def sort_key(value):
        if col in numeric_cols:
            try:
                return float(value)
            except (TypeError, ValueError):
                return float("-inf")
        return value

    return sort_key
