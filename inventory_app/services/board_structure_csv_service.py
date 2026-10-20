"""
構成基板数CSVの解析（取込前の差分計算まで。DBへは書かない）と、取込の確認・完了の文言。
ui/master/board_structure_import_window.py から移した（中身は変えていない）。画面のモジュールからも同じ名前で参照できる。
"""
import csv

from models.board_structure_master import compute_board_structure_sync_plan

# エンコーディング自動判定の候補（この順で試す。ui/master/parts_attributes_import_window.py と同一）
_ENCODINGS_TO_TRY = ["utf-8-sig", "utf-8", "cp932"]

# 構成基板数CSVの列名
COL_BOARD_NAME = "基板名"
COL_BOARD_COUNT = "構成基板数"
# 構成基板数列の候補列名（この順で最初に一致したものを使う）
COL_BOARD_COUNT_CANDIDATES = ["構成基板数", "構成数"]

# 確認ダイアログ・完了メッセージで「先頭○件」として表示する最大件数
_PREVIEW_HEAD_COUNT = 10


def open_csv_with_fallback(file_path):
    """utf-8-sig → utf-8 → cp932 の順でエンコーディングを判定して開く。
    ui/master/parts_attributes_import_window.py::_open_csv_with_fallback() と同一実装。"""
    last_error = None
    for encoding in _ENCODINGS_TO_TRY:
        try:
            f = open(file_path, mode="r", encoding=encoding, newline="")
            f.read(2048)
            f.seek(0)
            return f
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError(f"CSVの文字コードを判定できませんでした: {last_error}")


def parse_board_structure_csv(file_path):
    """
    構成基板数CSV（基板名・構成基板数の2列、カンマ区切り）を解析する
    （DBへの書き込みは一切行わない。取込前の確認ダイアログ用の差分計算まで行う）。

    以前はui/master/parts_attributes_import_window.py（タブ区切りTSV）に合わせて
    delimiter="\\t"としていたが、実運用のCSVはカンマ区切りであり、
    区切り文字の不一致によりヘッダーが正しく認識されず全行がスキップされる
    （インポート0件になる）不具合が発覚したため、カンマ区切りに修正した。

    必須列：基板名（欠けている・空の行は警告してスキップ）
    任意列：構成基板数。候補列名（COL_BOARD_COUNT_CANDIDATES＝「構成基板数」
    「構成数」）のいずれかがヘッダーに存在すればそれを使う。いずれも見つからず、
    かつCSVがちょうど2列構成（基板名列＋もう1列）の場合に限り、その「もう1列」
    を構成基板数として位置ベースで読み込むフォールバックを行う（実運用のCSVで
    2列目にヘッダー名自体が付いていない実例が発覚したため）。3列以上ある場合は
    誤った列を拾うリスクを避けるためフォールバックしない（候補列名一致のみ）。
    数値変換できない場合は警告のうえNoneのまま保存する。

    CSV内の重複キー（同じ基板名の行が複数）：最後に出現した行の値を最終的な
    登録値として採用する（既存のON CONFLICT上書きの挙動と一致させるため）。
    値が全て同じ重複は件数のみを、値が食い違う重複は該当行番号・各値・採用される
    値（最後の行）を区別して記録する。

    区切り文字・文字コードの不一致でヘッダーが正しく認識されないと、
    全行が「基板名が空」としてスキップされてしまう（部品属性インポートと同種の
    パターン）。これに気づきやすくするため、基板名空欄によるスキップが読み込み
    行数の9割以上を占める場合・構成基板数が未取得だった行が5割以上を占める場合は、
    行単位の警告（warnings）とは別に、ファイル形式の確認を促す注意喚起メッセージを
    notices に追加する（警告件数の集計を汚さないため）。

    戻り値：{
        "resolved_rows": [(board_name, board_count_or_None), ...]（重複解決後、1キー1件）,
        "plan": compute_board_structure_sync_plan()の戻り値（追加・更新・変更なし・削除）,
        "total_rows": CSVの有効データ行数（ヘッダーを除く全行）,
        "skipped_empty_key_count": 基板名が空でスキップした行数,
        "value_missing_count": 構成基板数が読み取れなかった行数（生の行単位。
            重複キーで最終的に上書きされた行も含めて数える）,
        "duplicates": {
            "same_value": [{"board_name":, "count":, "rows": [row_no,...]}],
            "diff_value": [{"board_name":, "occurrences": [{"row_no":, "value":}],
                             "final_value":}],
        },
        "notices": [...],
        "warnings": [...]（行単位のみ。基板名空欄スキップ・数値変換失敗）,
        "abort_reason": str または None（有効な基板名を含む行が1件も無い場合の中断理由）,
    }
    """
    warnings = []
    notices = []

    with open_csv_with_fallback(file_path) as f:
        reader = csv.DictReader(f, delimiter=",")
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    if not rows:
        return {
            "resolved_rows": [], "plan": None, "total_rows": 0,
            "skipped_empty_key_count": 0, "value_missing_count": 0,
            "duplicates": {"same_value": [], "diff_value": []},
            "notices": [], "warnings": [],
            "abort_reason": "CSVにデータ行が1件も無いため、インポートを中断しました（削除も行っていません）。",
        }

    board_count_col = next((c for c in COL_BOARD_COUNT_CANDIDATES if c in fieldnames), None)
    if board_count_col is None:
        other_columns = [fn for fn in fieldnames if fn != COL_BOARD_NAME]
        if len(fieldnames) == 2 and len(other_columns) == 1:
            board_count_col = other_columns[0]
            notices.append(
                f"列名が想定（{'/'.join(COL_BOARD_COUNT_CANDIDATES)}）と異なるため、"
                "2列目を構成基板数として読み込みました。"
            )

    skipped_count = 0
    value_missing_count = 0
    # board_name -> [(row_no, board_count), ...]（出現順。重複解決・重複検出の両方に使う）
    occurrences_by_name = {}
    order = []  # 初出順のbasic_name一覧（結果の順序を安定させるため）

    for i, row in enumerate(rows, start=2):  # 1行目はヘッダーのためCSV上の行番号に合わせる
        board_name = (row.get(COL_BOARD_NAME) or "").strip()
        if not board_name:
            warnings.append(f"{i}行目: {COL_BOARD_NAME}が空のためスキップしました。")
            skipped_count += 1
            continue

        board_count_raw = (row.get(board_count_col) or "").strip() if board_count_col is not None else ""
        board_count = None
        if board_count_raw:
            try:
                board_count = float(board_count_raw)
            except ValueError:
                warnings.append(
                    f"{i}行目: {COL_BOARD_COUNT}「{board_count_raw}」を数値に変換できないため未設定のまま保存しました。"
                )

        if board_count is None:
            value_missing_count += 1

        if board_name not in occurrences_by_name:
            occurrences_by_name[board_name] = []
            order.append(board_name)
        occurrences_by_name[board_name].append((i, board_count))

    total_rows = len(rows)
    if total_rows > 0 and skipped_count / total_rows >= 0.9:
        notices.insert(
            0,
            f"※ 読み込んだ{total_rows}行中{skipped_count}行"
            f"（{skipped_count / total_rows * 100:.0f}%）が{COL_BOARD_NAME}空欄でスキップされました。"
            "区切り文字（タブ/カンマ）や文字コードがファイルの実際の形式と"
            "一致していない可能性があります。ファイル形式をご確認ください。",
        )

    imported_count = total_rows - skipped_count
    if imported_count > 0 and value_missing_count / imported_count >= 0.5:
        notices.append(
            f"※ 読み込んだ{imported_count}行中{value_missing_count}行"
            f"（{value_missing_count / imported_count * 100:.0f}%）で{COL_BOARD_COUNT}を取得できませんでした。"
            f"区切り文字や列名（{'/'.join(COL_BOARD_COUNT_CANDIDATES)}）が"
            "ファイルの実際の形式と一致していない可能性があります。ファイル形式をご確認ください。"
        )

    if not order:
        return {
            "resolved_rows": [], "plan": None, "total_rows": total_rows,
            "skipped_empty_key_count": skipped_count, "value_missing_count": value_missing_count,
            "duplicates": {"same_value": [], "diff_value": []},
            "notices": notices, "warnings": warnings,
            "abort_reason": "有効な基板名を含む行が1件も無かったため、削除は行っていません。",
        }

    same_value_dups = []
    diff_value_dups = []
    resolved_rows = []
    for board_name in order:
        occurrences = occurrences_by_name[board_name]
        final_value = occurrences[-1][1]  # 最後に出現した行の値を採用
        resolved_rows.append((board_name, final_value))
        if len(occurrences) > 1:
            distinct_values = {value for _row_no, value in occurrences}
            if len(distinct_values) == 1:
                same_value_dups.append({
                    "board_name": board_name, "count": len(occurrences),
                    "rows": [row_no for row_no, _v in occurrences],
                })
            else:
                diff_value_dups.append({
                    "board_name": board_name,
                    "occurrences": [{"row_no": row_no, "value": value} for row_no, value in occurrences],
                    "final_value": final_value,
                })

    plan = compute_board_structure_sync_plan(resolved_rows)

    return {
        "resolved_rows": resolved_rows, "plan": plan, "total_rows": total_rows,
        "skipped_empty_key_count": skipped_count, "value_missing_count": value_missing_count,
        "duplicates": {"same_value": same_value_dups, "diff_value": diff_value_dups},
        "notices": notices, "warnings": warnings, "abort_reason": None,
    }


def _format_value(value):
    return "" if value is None else value


def build_confirmation_message(parse_result):
    """取込前の確認ダイアログの本文を組み立てる。"""
    plan = parse_result["plan"]
    dup = parse_result["duplicates"]

    lines = [
        f"読み込んだ行数：{parse_result['total_rows']}行",
        "",
        f"新規追加：{len(plan['to_add'])}件",
        f"値が変わる：{len(plan['to_update'])}件",
        f"変更なし：{len(plan['unchanged'])}件",
        f"削除：{len(plan['to_delete'])}件",
    ]

    if plan["to_delete"]:
        lines.append("")
        lines.append("※ このCSVに無いデータは削除されます。")
        head = plan["to_delete"][:_PREVIEW_HEAD_COUNT]
        for item in head:
            lines.append(f"　・{item['board_name']}（現在の値：{_format_value(item['value'])}）")
        remaining = len(plan["to_delete"]) - len(head)
        if remaining > 0:
            lines.append(f"　　...ほか{remaining}件")

    if dup["same_value"]:
        total_dup_rows = sum(d["count"] for d in dup["same_value"])
        lines.append("")
        lines.append(
            f"※ CSV内に同じ基板名が複数回登場する行が{len(dup['same_value'])}件"
            f"（合計{total_dup_rows}行）ありますが、値はいずれも同じでした。"
        )

    if dup["diff_value"]:
        lines.append("")
        lines.append(f"※ CSV内に同じ基板名で値が食い違う重複が{len(dup['diff_value'])}件あります：")
        for d in dup["diff_value"][:_PREVIEW_HEAD_COUNT]:
            occ_text = " / ".join(
                f"{o['row_no']}行目={_format_value(o['value'])}" for o in d["occurrences"]
            )
            lines.append(f"　・{d['board_name']}：{occ_text}　→　採用値：{_format_value(d['final_value'])}（最後の行）")
        remaining = len(dup["diff_value"]) - _PREVIEW_HEAD_COUNT
        if remaining > 0:
            lines.append(f"　　...ほか{remaining}件")

    lines.append("")
    lines.append("この内容で取込を実行しますか？")

    return "\n".join(lines)


def build_completion_message(result, count_summary):
    plan = result["plan"]
    msg = (
        f"読み込んだ行数：{result['total_rows']}行\n"
        f"追加：{len(plan['to_add'])}件 / 更新：{len(plan['to_update'])}件 / "
        f"変更なし：{len(plan['unchanged'])}件 / 削除：{len(plan['to_delete'])}件\n"
        f"登録されなかった行数（{COL_BOARD_NAME}が空欄）：{result['skipped_empty_key_count']}件\n"
        f"値が読み取れず空で登録した件数：{result['value_missing_count']}件\n"
        f"取込後の登録件数：{count_summary['total']}件"
        f"（うち構成基板数なし：{count_summary['board_count_missing']}件）"
    )

    notices = result.get("notices") or []
    if notices:
        msg += "\n\n【ファイル形式についての注意】\n" + "\n".join(notices)

    warnings = result.get("warnings") or []
    if warnings:
        msg += f"\n\n警告：{len(warnings)}件（詳細は別ウィンドウで確認できます）"

    return msg
