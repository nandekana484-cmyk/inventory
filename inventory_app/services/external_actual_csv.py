# services/external_actual_csv.py
"""
現場実績入力アプリが書き出す実績CSV（書式版 1）の解析と、DB を見ずにできる検査。GUI にも DB にも依存しない。
列は固定の12列（変えるときは先に相談）。止めるのは、ヘッダーの不備と書式版の不一致（ファイル全体）、元のDBの違い・形式エラー（その行だけ）。
計画・担当者・NG の面などの検査は DB が要るので services/external_actual_service.py。業務ルールは docs/domain/external_actual.md。
"""
import csv
import hashlib
import re
import unicodedata
from datetime import datetime

from services.csv_parsing_common import open_csv_with_fallback

FORMAT_VERSION = "1"

COLUMNS = (
    "書式版", "書き出し日時", "元のDB(フォルダ名)", "キッティングNo.", "ロットNo.", "ファイルNo.",
    "面", "日付", "生産数(累計)", "NG面1", "NG面2", "担当者ID",
)

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COUNT_PATTERN = re.compile(r"^\d+(\.0+)?$")
_SCIENTIFIC_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?[eE][+-]?\d+$")
_SETUP_FILE_NO_LENGTH = 4          # 実データのファイルNo.は全件4文字（先頭0落ちの検出に使う。models/production_side_master.py）

# CSV 内の重複で、内容が違うかどうかの比較に使う
_CONTENT_KEYS = ("file_no", "side", "report_date", "daily_qty", "ng_side1", "ng_side2", "worker_id")


def normalize_text(value) -> str:
    """前後の空白を除き NFKC にする（キッティングNo.・ロットNo.・ファイルNo.の比較用）。"""
    return unicodedata.normalize("NFKC", "" if value is None else str(value)).strip()


def excel_damage_reason(label: str, text: str):
    """
    Excel で開いて保存したときに化けた形なら、その説明。そうでなければ None。
    指数表記（1.2E+5）と、ファイルNo.の先頭0落ち（0569 → 569）。キッティングNo.は先頭の区切りをファイルNo.として見る。
    """
    if _SCIENTIFIC_PATTERN.match(text):
        return f"{label}「{text}」は指数表記です（Excelで化けた可能性があります）"
    if label in ("ファイルNo.", "キッティングNo."):
        head = text.split("-")[0]
        if head.isdigit() and len(head) < _SETUP_FILE_NO_LENGTH:
            return f"{label}「{text}」は先頭の0が落ちています（Excelで化けた可能性があります）"
    return None


def _parse_count(label: str, text: str, allow_empty: bool, messages: list):
    text = normalize_text(text)
    if not text:
        if allow_empty:
            return 0
        messages.append(f"{label}が空です")
        return None
    if text.startswith("-"):
        messages.append(f"{label}が負の値です（{text}）")
        return None
    if not _COUNT_PATTERN.match(text):
        messages.append(f"{label}が整数ではありません（{text}）")
        return None
    return int(float(text))


def parse_exported_at(text: str):
    """書き出し日時を "YYYY-MM-DD HH:MM:SS" に直す（文字列で大小を比べられる形）。読めなければ None。タイムゾーン付きは今の時刻帯に直す。"""
    text = normalize_text(text)
    if not text:
        return None
    parsed = None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def _parse_report_date(text: str, messages: list):
    text = normalize_text(text)
    try:
        if not _DATE_PATTERN.match(text):
            raise ValueError
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        messages.append(f"日付が YYYY-MM-DD の形ではありません（{text or '空'}）")
        return None
    return text


def _parse_row(cells: list, current_folder_name: str):
    """1行を検査する。戻り値は (行の辞書 or None, メッセージのリスト)。メッセージがあれば、その行は取り込まない。"""
    if len(cells) != len(COLUMNS):
        return None, [f"列の数が {len(COLUMNS)} ではありません（{len(cells)} 列）"]
    messages = []
    kitting_no, lot_no, file_no = (normalize_text(cells[i]) for i in (3, 4, 5))
    for label, value in (("キッティングNo.", kitting_no), ("ロットNo.", lot_no), ("ファイルNo.", file_no)):
        if not value:
            messages.append(f"{label}が空です")
        else:
            reason = excel_damage_reason(label, value)
            if reason:
                messages.append(reason)

    source_db = normalize_text(cells[2])
    if source_db != normalize_text(current_folder_name):
        messages.append(f"元のDB「{source_db or '空'}」が今のDB「{current_folder_name}」と違います")

    exported_at = parse_exported_at(cells[1])
    if exported_at is None:
        messages.append(f"書き出し日時が読めません（{normalize_text(cells[1]) or '空'}）")

    side = normalize_text(cells[6])
    if side not in ("1", "2"):
        messages.append(f"面が 1 か 2 ではありません（{side or '空'}）")

    report_date = _parse_report_date(cells[7], messages)
    daily_qty = _parse_count("生産数", cells[8], False, messages)
    ng_side1 = _parse_count("NG面1", cells[9], True, messages)
    ng_side2 = _parse_count("NG面2", cells[10], True, messages)
    worker_id = cells[11]                      # 正規化せず、そのまま比べる（workers の worker_id と同じ表記が前提）
    if not worker_id.strip():
        messages.append("担当者IDが空です")
    if side == "1" and ng_side2:
        messages.append("面1の行に NG面2 が入っています（面2の NG が面1に足されてしまうため取り込みません）")

    if messages:
        return None, messages
    return {
        "kitting_list_no": kitting_no, "lot_no": lot_no, "file_no": file_no, "side": side, "report_date": report_date,
        "daily_qty": daily_qty, "ng_side1": ng_side1, "ng_side2": ng_side2, "worker_id": worker_id,
        "exported_at": exported_at, "source_db_name": source_db,
    }, []


def _resolve_duplicates(rows: list, warnings: list) -> list:
    """同じ (キッティングNo., ロットNo.) の行は、書き出し日時が新しい方を使う（同じなら後ろの行）。内容が違えば警告する。"""
    groups = {}
    for row in rows:
        groups.setdefault((row["kitting_list_no"], row["lot_no"]), []).append(row)
    kept = []
    for (kitting_no, lot_no), group in groups.items():
        best = max(group, key=lambda r: (r["exported_at"], r["csv_row_no"]))
        kept.append(best)
        if len(group) > 1 and any(any(r[k] != best[k] for k in _CONTENT_KEYS) for r in group):
            numbers = "・".join(str(r["csv_row_no"]) for r in sorted(group, key=lambda r: r["csv_row_no"]))
            warnings.append(
                f"キッティングNo.「{kitting_no}」ロットNo.「{lot_no}」が複数あり、内容が違います（行 {numbers}）。"
                f"書き出し日時が新しい行 {best['csv_row_no']} を使います"
            )
    kept.sort(key=lambda r: r["csv_row_no"])
    return kept


def parse_external_actual_csv(file_path: str, current_folder_name: str) -> dict:
    """
    現場実績CSV を読み、検査を通った行と、止めた行を返す。ファイルの文字コードが判定できないときは ValueError。
    戻り値:
      file_sha256: ファイルの SHA-256（同じファイルを取り込み済みかの確認に使う）
      fatal: ヘッダーの不備か書式版の不一致があれば、その説明（このとき rows は空。ファイル全体を取り込まない）
      rows: 取り込める行（csv_row_no・各列・exported_at・source_db_name を持つ辞書。CSV 内の重複は解決済み）
      errors: 止めた行 [{"csv_row_no", "kitting_list_no", "lot_no", "messages"}]
      warnings: 取り込みは続けるが知らせること（CSV 内の重複で内容が違う、など）
      total_rows: データ行の数（空行を除く）
    """
    with open(file_path, "rb") as f:
        file_sha256 = hashlib.sha256(f.read()).hexdigest()
    result = {"file_sha256": file_sha256, "fatal": None, "rows": [], "errors": [], "warnings": [], "total_rows": 0}

    with open_csv_with_fallback(file_path) as f:
        records = list(csv.reader(f))
    header = [normalize_text(c).lstrip("﻿") for c in (records[0] if records else [])]
    expected = [normalize_text(c) for c in COLUMNS]
    if header != expected:
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        detail = (f"足りない列：{'、'.join(missing)}。" if missing else "") + (f"余分な列：{'、'.join(extra)}。" if extra else "")
        result["fatal"] = "ヘッダーが現場実績CSVの形ではありません。" + (detail or "列の順番が違います。")
        return result

    data = [(number, cells) for number, cells in enumerate(records[1:], start=2) if any(c.strip() for c in cells)]
    result["total_rows"] = len(data)
    bad_versions = [number for number, cells in data if cells and normalize_text(cells[0]) != FORMAT_VERSION]
    if bad_versions:
        shown = "・".join(str(n) for n in bad_versions[:5])
        result["fatal"] = (f"書式版が {FORMAT_VERSION} ではありません（行 {shown}{' ほか' if len(bad_versions) > 5 else ''}）。"
                           "このファイルは取り込みません。")
        return result

    parsed = []
    for number, cells in data:
        row, messages = _parse_row(cells, current_folder_name)
        if messages:
            result["errors"].append({
                "csv_row_no": number,
                "kitting_list_no": normalize_text(cells[3]) if len(cells) > 3 else "",
                "lot_no": normalize_text(cells[4]) if len(cells) > 4 else "",
                "messages": messages,
            })
        else:
            row["csv_row_no"] = number
            parsed.append(row)
    result["rows"] = _resolve_duplicates(parsed, result["warnings"])
    return result
