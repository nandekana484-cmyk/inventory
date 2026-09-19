# ui/plan_candidate_dialog.py
"""
kitting_list_no検索で複数のlot_no候補にあたった場合に、ユーザーに1件選ばせる
共通モーダルダイアログ。

実DBで同一kitting_list_noが複数の異なるlot_noにまたがって存在するケースが
478件確認されており（services.production_service._resolve_plan_item()参照）、
元々はui.kitting_production_entry.KittingProductionEntryWindow.search_plan()と
ui.ng_input_window.NgInputWindow.on_expand()/_expand_from_kitting_no()の
両方から共通のUI部品として使うために切り出した。生産実績入力画面はその後、
キッティングリストNo.検索欄自体を廃止し右ペインの計画一覧からの選択に
一本化した（選択行から常にlot_noも一意に判明するため、本ダイアログを経由する
経路が無くなった）ため、現在使っているのはui.ng_input_window.NgInputWindowの
みである。
"""
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox

_COLS = ("lot_no", "board_name", "setup_file_no", "production_side", "plan_start_datetime",
         "planned_qty", "order_qty")
_HEADERS = {
    "lot_no": "ロットNo.",
    "board_name": "基板名",
    "setup_file_no": "ファイルNo.",
    "production_side": "生産面",
    "plan_start_datetime": "実装開始予定日",
    "planned_qty": "計画数",
    "order_qty": "注文数",
}
_RIGHT_ALIGNED = {"planned_qty", "order_qty"}


def _format_side(side):
    if side in (1, 2):
        return f"面{side}"
    return "" if side is None else str(side)


def _format_qty(value):
    if value is None:
        return ""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


_QTY_DIFF_WARN_RATIO = 0.2  # 数量差が当日実績(daily_qty)の20%を超える候補を視覚的に注意喚起する閾値


def _compute_qty_diff(candidate, daily_qty):
    """candidateのplanned_qtyとdaily_qtyの差の絶対値。比較不能（どちらかがNone・数値化不能）ならNone。"""
    if daily_qty is None:
        return None
    planned = candidate.get("planned_qty")
    if planned is None:
        return None
    try:
        return abs(float(planned) - float(daily_qty))
    except (TypeError, ValueError):
        return None


def _is_large_qty_diff(candidate, daily_qty):
    """候補一覧で背景色を変えて注意喚起すべきほど数量差が大きいか。"""
    diff = _compute_qty_diff(candidate, daily_qty)
    if diff is None or daily_qty is None:
        return False
    threshold = abs(daily_qty) * _QTY_DIFF_WARN_RATIO
    return diff > threshold


def _format_planned_qty_cell(planned_qty, daily_qty):
    """
    候補一覧の「計画数」（planned_qty）セルの表示文字列を組み立てる。

    Tkinterの標準ttk.Treeviewは行単位のtag_configure()による背景色指定にしか
    対応しておらず、特定のセル1つだけを別の色にする（行全体の色とは独立した
    セル単位の色分け）ことはできない。そのため、「数量部分だけを視覚的に
    強調する」という要件は、色ではなく**セルのテキスト自体に差分を埋め込む**
    方式で実現する（行全体のハイライトタグ（large_diff等）とは独立した、
    より細かい粒度の情報として、計画数と実績数の差がある場合は常に併記する。
    行レベルのlarge_diffは_QTY_DIFF_WARN_RATIO（20%）を超えた場合のみ発火する
    のに対し、こちらは差があれば（20%未満のわずかな差でも）常に表示する、
    より高い解像度の指標）。

    表示例：計画数450・実績数400の場合 → "450（差+50）"。差が無い（完全一致）
    場合や、daily_qty自体が指定されない場合（select_plan_candidate()経由、
    CSVとの比較という概念自体が無い）は、従来通り計画数のみを表示する
    （差が無いのに"(差+0)"と表示され続けるのは煩雑なため）。
    """
    formatted = _format_qty(planned_qty)
    if daily_qty is None or planned_qty is None:
        return formatted
    try:
        diff = float(planned_qty) - float(daily_qty)
    except (TypeError, ValueError):
        return formatted
    if diff == 0:
        return formatted
    return f"{formatted}（差{diff:+g}）"


def _show_candidate_list_dialog(parent, title, description, candidates, daily_qty=None, report_date=None):
    """
    候補一覧（kitting_plan_itemsの行の辞書のリスト）をTreeviewで一覧表示し、
    ユーザーに1件選ばせるモーダルダイアログの共通実装。

    一覧から1行選択して「選択」ボタン、またはダブルクリックで即確定する
    （ui.checkable_treeview等、既存のTreeview実装パターンに合わせたシンプルな
    一覧選択UI）。select_plan_candidate()・select_plan_candidate_by_lot()の
    両方から使う（呼び出し元ごとに異なるのはタイトル・説明文のみ）。

    戻り値：選択されたcandidatesの要素（辞書）。ユーザーがキャンセル
    （キャンセルボタン／ウインドウを閉じる）した場合はNone。

    daily_qty：CSVの当日実績数（select_plan_candidate_by_lot()経由の場合のみ
    指定される）。指定された場合、各行のplanned_qtyとの差が_QTY_DIFF_WARN_RATIO
    （20%）を超える候補の背景色を薄く変え（"large_diff"タグ、黄色系）、視覚的に
    注意喚起する。select_plan_candidate()側はdaily_qtyという概念自体が
    無いため、Noneのまま（ハイライト無し）。

    report_date：CSVの払い出し日（同じくselect_plan_candidate_by_lot()経由の
    場合のみ指定される）。指定された場合、各行のplan_start_datetimeとの日数差
    が_DATE_DIFF_WARN_DAYS（3日）以上の候補の背景色を変え（"large_date_diff"
    タグ、オレンジ系）、視覚的に注意喚起する。

    計画数セルの差分表記：daily_qty指定時、各行の「計画数」（planned_qty）
    セルは、実績数との差がある場合に差分を併記した文字列になる（例："450
    （差+50）"、_format_planned_qty_cell()参照）。行全体のハイライトタグ
    （large_diff等、_QTY_DIFF_WARN_RATIO=20%を超えた場合のみ発火）とは独立に、
    差があれば常に（20%未満のわずかな差でも）表示される、より細かい粒度の
    指標。Tkinterの標準Treeviewは行単位の背景色指定にしか対応しておらず
    セル単位の色分けができないため、色ではなくテキストへの差分埋め込みで
    「数量部分だけを強調する」要件を実現した。

    数量差・日付差の両方に該当する候補の扱い：Tkinter Treeviewは1アイテムに
    複数タグを付けた場合の背景色の優先順位が分かりやすく規定されておらず
    （tag_configureの呼び出し順・tags指定順のどちらに依存するかが自明でない）、
    挙動を確実に制御するため、両方に該当する場合は専用の第三のタグ
    "large_diff_both"（黄色でもオレンジでもない、より強い注意を示す赤系）を
    単独で割り当てる（3タグを併用しない）。単なる「両方が同時に目立つ」
    見た目ではなく、「どちらか一方より深刻」という段階（黄色＜オレンジ＜赤、
    という重大度の直感的な序列）を意図した色選定。

    parentが最小化（アイコン化）状態の場合、Tkinter/Windowsの仕様上、
    transient(parent)したダイアログはstate()="withdrawn"のまま実際には
    画面に表示されない（grab_set()は効くため、見えないダイアログが入力を
    握ったままwait_window()で待ち続ける＝アプリ全体がフリーズしたように
    見える）。これを避けるため、transient()の前にparentが最小化されていれば
    deiconify()で元に戻す。呼び出し元（複数のウインドウから共通で使われる
    ダイアログのため、どの呼び出し元のparentが最小化されていても対応できる
    よう、この共通実装側で吸収する）。
    """
    if parent.state() == "iconic":
        parent.deiconify()

    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.geometry("650x320")
    dialog.transient(parent)
    dialog.grab_set()

    result = {"chosen": None}

    ttk.Label(dialog, text=description, padding=10).pack(anchor=tk.W)

    tree_frame = ttk.Frame(dialog, padding=(10, 0))
    tree_frame.pack(expand=True, fill=tk.BOTH)

    tree = ttk.Treeview(tree_frame, columns=_COLS, show="headings", selectmode="browse")
    for col in _COLS:
        tree.heading(col, text=_HEADERS[col])
        # 計画数（planned_qty）は_format_planned_qty_cell()で差分表記
        # （例："450（差+50）"）が付くことがあるため、他列より幅を広めに取る。
        width = 160 if col == "planned_qty" else 100
        tree.column(col, width=width, anchor=tk.E if col in _RIGHT_ALIGNED else tk.W)
    tree.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
    tree.tag_configure("large_diff", background="#fff3cd")
    tree.tag_configure("large_date_diff", background="#ffd9a0")
    tree.tag_configure("large_diff_both", background="#ffb3b3")

    vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)

    for candidate in candidates:
        qty_flag = _is_large_qty_diff(candidate, daily_qty)
        date_flag = _is_large_date_diff(report_date, candidate.get("plan_start_datetime"))
        if qty_flag and date_flag:
            tags = ("large_diff_both",)
        elif qty_flag:
            tags = ("large_diff",)
        elif date_flag:
            tags = ("large_date_diff",)
        else:
            tags = ()
        tree.insert("", tk.END, tags=tags, values=(
            candidate.get("lot_no") or "",
            candidate.get("board_name") or "",
            candidate.get("setup_file_no") or "",
            _format_side(candidate.get("production_side")),
            candidate.get("plan_start_datetime") or "",
            _format_planned_qty_cell(candidate.get("planned_qty"), daily_qty),
            _format_qty(candidate.get("order_qty")),
        ))

    def confirm(event=None):
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("選択エラー", "一覧から選択してください。", parent=dialog)
            return
        index = tree.index(sel[0])
        result["chosen"] = candidates[index]
        dialog.destroy()

    def cancel():
        result["chosen"] = None
        dialog.destroy()

    tree.bind("<Double-1>", confirm)

    btn_frame = ttk.Frame(dialog, padding=10)
    btn_frame.pack(fill=tk.X)
    ttk.Button(btn_frame, text="選択", command=confirm).pack(side=tk.RIGHT, padx=5)
    ttk.Button(btn_frame, text="キャンセル", command=cancel).pack(side=tk.RIGHT)

    dialog.protocol("WM_DELETE_WINDOW", cancel)
    dialog.wait_window()
    return result["chosen"]


def select_plan_candidate(parent, kitting_list_no, candidates):
    """
    候補一覧（models.kitting_plan.list_active_plan_items_by_kitting_no()の戻り値）
    から、kitting_list_no検索で複数のlot_no候補にあたった場合に1件選ばせる。

    戻り値：選択されたcandidatesの要素（辞書）。キャンセル時はNone。
    """
    description = (
        f"キッティングリストNo. {kitting_list_no} には複数のロットが該当します。\n"
        "対象のロットを選択してください。"
    )
    return _show_candidate_list_dialog(parent, "ロットNo.の選択", description, candidates)


def _parse_plan_start_datetime(value):
    try:
        return datetime.strptime(str(value).strip(), "%Y/%m/%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


# 払い出し日（report_date）として許容する形式。上から順に試す。
# "%Y-%m-%d"：以前からの標準形式（例："2026-09-05"）。
# "%Y/%m/%d"：実運用のCSVで確認された形式（例："2026/9/5"）。strptimeの
# %m・%dはゼロ埋めの有無を問わず解釈できるため（"9"でも"09"でも可）、
# 区切り文字（"/"か"-"か）さえ合えばこの2つのフォーマット文字列で
# ゼロ埋めあり・なしの両方をカバーできる。
_REPORT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")


def _parse_flexible_date(value):
    """
    払い出し日（report_date）のパースを1箇所に集約した共通関数。
    _REPORT_DATE_FORMATSを順に試し、最初に成功した結果を返す。
    どの形式でもパースできない・値が無い場合はNoneを返す。

    以前は"%Y-%m-%d"のみに対応する_parse_report_date()という名前の
    関数だったが、実運用のCSVでは"%Y/%m/%d"形式（かつ月日がゼロ埋め
    されていない、例："2026/3/18"）が使われていることが判明し
    （PRODUCTION_NG_ENHANCEMENTS_NOTES.md等の調査記録参照）、
    "%Y-%m-%d"のみでは実データに対して常にパース不能になっていた。
    そのため複数形式に対応させ、関数名も実態に合わせて改称した。

    本関数はui.kitting_production_entry._resolve_csv_report_date()からも
    importして使う（同じ日付形式の解釈をここに集約し、重複実装を避ける
    ため）。呼び出し元ごとに戻り値の扱いが異なる点に注意：
      - 本モジュール内（_sort_candidates_by_closeness()・
        _compute_date_diff_days()）は、日数差の計算にそのままdatetime
        オブジェクトとして使う。
      - _resolve_csv_report_date()は、DBのreport_date列（"YYYY-MM-DD"
        形式で統一的に保存する必要がある）へ書き込む前提のため、本関数の
        戻り値（datetime）をさらにstrftime("%Y-%m-%d")で正規化してから
        使う（入力が"2026/3/18"のような非ゼロ埋め・スラッシュ区切りで
        あっても、DBには常にゼロ埋め済みのハイフン区切りで保存され、
        文字列としての日付範囲比較（例：models.production.
        list_daily_production_range()のWHERE report_date >= ? AND <= ?）
        が正しく機能するようにするため）。
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in _REPORT_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
    return None


_DATE_DIFF_WARN_DAYS = 3  # 払い出し日と生産予定日の差がこの日数以上の候補を視覚的に注意喚起する閾値


def _compute_date_diff_days(report_date, plan_start_datetime):
    """
    report_date（CSVの払い出し日）とplan_start_datetime（候補の生産予定日）の
    差の日数（絶対値）。パース不能・どちらか欠落なら比較不能としてNone。
    パースロジックは_sort_candidates_by_closeness()内のdate_diff()と同じ
    _parse_flexible_date()・_parse_plan_start_datetime()をそのまま流用する
    （日付形式の解釈を1箇所に集約し、ずれが生じないようにするため）。
    """
    reference = _parse_flexible_date(report_date)
    if reference is None:
        return None
    dt = _parse_plan_start_datetime(plan_start_datetime)
    if dt is None:
        return None
    return abs((dt - reference).days)


def _is_large_date_diff(report_date, plan_start_datetime, threshold_days=_DATE_DIFF_WARN_DAYS):
    """候補一覧で背景色を変えて注意喚起すべきほど、払い出し日と生産予定日の差が大きいか。"""
    diff = _compute_date_diff_days(report_date, plan_start_datetime)
    if diff is None:
        return False
    return diff >= threshold_days


_DATE_DIFF_WEIGHT = 0.7
_QTY_DIFF_WEIGHT = 0.3


def _normalize(value, lo, hi):
    """min-max正規化。値が無い（None）場合や、候補間で差が無い（hi<=lo）場合は0.0（中立）扱い。"""
    if value is None or hi <= lo:
        return 0.0
    return (value - lo) / (hi - lo)


def _sort_candidates_by_closeness(candidates, report_date, daily_qty=None):
    """
    候補一覧を、(1) report_date（CSVの払い出し日）とplan_start_datetimeの日数差、
    (2) daily_qty（CSVの当日実績数）とplanned_qty（計画数）の差、の2軸を
    組み合わせた複合スコアの小さい順に安定ソートする。

    方式の選定理由：日数差（単位：日）と数量差（単位：枚等）はスケールが
    全く異なるため、単純に足し合わせることができない。そこで候補集合内で
    それぞれをmin-max正規化（0〜1に変換）した上で、日数差を主軸としつつ
    （重み0.7）数量差もタイブレークとして反映する（重み0.3）よう
    重み付け合算する。日数差だけでは優劣が付かない・僅差のケースで
    数量差の大きい候補が後ろに回る一方、数量差だけでは大差が付いても
    日数差が支配的な限りは日付の近さが優先される（0.7 vs 0.3という重みの
    非対称性により、日付の近さが「主」・数量の近さが「従」という
    要件を反映）。

    以前の実装（_sort_candidates_by_report_date_closenessという名前だった、
    日数差のみによる単純ソート）とのスケール互換性：daily_qty未指定
    （report_date比較のみ）の場合、正規化後の値に一律で重み0.7を掛けても
    ソート順（大小関係）自体は変わらないため、既存の呼び出し・挙動を
    後方互換のまま保つ。

    - report_dateがNone・パース不能：日数差は使わず、数量差のみで判定する
      （daily_qty未指定なら結果的にソートせず元の順序のまま）。
    - plan_start_datetimeがパース不能な候補：report_dateが使える場合に限り、
      （数量差の大小に関わらず）末尾に回す。それら同士の相対順序は元のまま
      （sortの安定性による）。
    - planned_qtyが無い・数値化不能な候補：数量差は「不明」として中立
      （ペナルティ無し＝0.0）に扱う。候補一覧からは除外しない。
    """
    reference = _parse_flexible_date(report_date)
    has_date_ref = reference is not None
    has_qty_ref = daily_qty is not None

    if not has_date_ref and not has_qty_ref:
        return list(candidates)

    def date_diff(candidate):
        if not has_date_ref:
            return None
        dt = _parse_plan_start_datetime(candidate.get("plan_start_datetime"))
        if dt is None:
            return None
        return abs((dt - reference).days)

    date_diffs = [date_diff(c) for c in candidates]
    qty_diffs = [_compute_qty_diff(c, daily_qty) for c in candidates]

    known_dates = [d for d in date_diffs if d is not None]
    known_qtys = [q for q in qty_diffs if q is not None]
    date_lo, date_hi = (min(known_dates), max(known_dates)) if known_dates else (0, 0)
    qty_lo, qty_hi = (min(known_qtys), max(known_qtys)) if known_qtys else (0, 0)

    def sort_key(item):
        _, d, q = item
        if has_date_ref and d is None:
            return (1, 0.0)
        score = 0.0
        if has_date_ref:
            score += _DATE_DIFF_WEIGHT * _normalize(d, date_lo, date_hi)
        if has_qty_ref:
            qty_weight = _QTY_DIFF_WEIGHT if has_date_ref else 1.0
            score += qty_weight * _normalize(q, qty_lo, qty_hi)
        return (0, score)

    combined = sorted(zip(candidates, date_diffs, qty_diffs), key=sort_key)
    return [candidate for candidate, _, _ in combined]


def select_plan_candidate_by_lot(parent, lot_no, product_name, candidates, matched, report_date=None, daily_qty=None):
    """
    候補一覧から、実績CSV取込のステージング一覧（ui.production_import_staging_window）
    向けに1件選ばせる。select_plan_candidate()とは絞り込みの軸
    （kitting_list_no+lot_no vs lot_no+製品名）が異なるため別関数として
    新設したが、Treeview表示パターン（_show_candidate_list_dialog()）は共通で
    流用している。

    candidates：models.kitting_plan.find_matching_plan_items()の戻り値のうち、
    lot_noに属する現在アクティブな計画一覧（製品名の一致・不一致は問わない）。
    matched：同じくfind_matching_plan_items()の戻り値のうち、正規化済み製品名
    まで一致した計画一覧。

    製品名（board_name）による絞り込み：以前はcandidates（lot_no一致のみ）を
    そのまま表示しており、製品名による絞り込みが一切適用されていなかった
    （調査により判明）。matchedを優先して表示するよう変更し、matchedが0件
    （製品名が完全一致する候補が無い）の場合のみ、フォールバックとして
    candidates（lot_no一致の全件）を表示し、その旨をダイアログの説明文に
    注記する（誤って有効な候補まで絞り込みすぎてしまうことを避けるため）。

    候補が1件のみであっても、このダイアログを必ず経由させ、自動確定はしない
    （呼び出し元の方針：登録前に必ず人間の確認を挟む）。

    report_date：CSVの払い出し日（"YYYY-MM-DD"形式を期待、ui.kitting_
    production_entry._resolve_csv_report_date()と同じ形式）。

    daily_qty：CSVの当日実績数。report_dateとあわせて、
    _sort_candidates_by_closeness()による複合的な優先順位付け（日数差を
    主軸に、数量差をタイブレークとして反映）に使う。指定した場合は
    候補一覧にも当日実績数を表示し、計画数（planned_qty列）と見比べ
    やすくする。省略時（None）は数量差を考慮せず、従来通り日数差のみで
    ソートする。いずれも省略・パース不能な場合はソートせず元の順序の
    まま表示する。数量差が大きい候補も一覧から除外はしない（優先順位を
    下げる、および一覧上でハイライトするのみ）。

    戻り値：選択されたcandidatesまたはmatchedの要素（辞書）。キャンセル時はNone。
    """
    using_fallback = not matched
    effective_candidates = candidates if using_fallback else matched
    effective_candidates = _sort_candidates_by_closeness(effective_candidates, report_date, daily_qty)

    description = (
        f"ロットNo. {lot_no}（製品名: {product_name}）に該当する計画候補です。\n"
    )
    if daily_qty is not None:
        description += f"当日実績数：{_format_qty(daily_qty)}（計画数と見比べてご確認ください）\n"
    description += "登録する計画を選択してください。"
    if using_fallback:
        description += (
            "\n※ 製品名が完全一致する候補がありませんでした。"
            "ロットNo.が一致する全ての候補を表示しています。"
        )

    return _show_candidate_list_dialog(
        parent, "計画の選択", description, effective_candidates,
        daily_qty=daily_qty, report_date=report_date,
    )
