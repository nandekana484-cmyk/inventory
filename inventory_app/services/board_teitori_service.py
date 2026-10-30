# services/board_teitori_service.py
"""
計画ごとの丁取り数の対応表（master.db の board_teitori_map、ビュー v_board_teitori）の取得（D-191、docs/domain/board_teitori.md）。

月次DB の対象の計画から (ファイルNo., 実装ライン) の組を集め、BOM TSV の K行の96コードを引いて表に書く。
GUI は import しない。結果は戻り値で返し、失敗は例外のまま呼び出し元へ（背景の呼び出しは board_teitori_runner.py がログに残す）。
ふだんの取得（取り込み・引き継ぎ・復元の後）は差分だけ、手動の再取得は全部引き直す。取得は1つずつ（_RUN_LOCK）。
"""
import logging
import os
import threading
import time
from datetime import datetime

import config
from models.board_teitori_map import (
    MAP_STATUSES, VIEW_STATUSES, STATUS_AMBIGUOUS_TSV, STATUS_MULTI_K, STATUS_NO_K_ROW, STATUS_OK, STATUS_READ_ERROR,
    STATUS_TSV_NOT_FOUND, list_view_rows, load_map_state, upsert_map_rows,
)
from models.kitting_plan_setup_pairs import count_active_plan_rows_by_pair, has_plan_items_table
from models.monthly_db_readonly import open_monthly_db_readonly
from models.production_side_master import normalize_mounting_line
from services.bom_file_service import COL_PART_NO, COL_TYPE, BOMFileIndex
from services.bom_service import COL_MOUNTING_LINE, TYPE_VALUE_BOARD
from services.db_lifecycle_service import db_path_for_folder, list_db_folders

logger = logging.getLogger(__name__)

# 1回のトランザクションで書く組の上限。現場アプリの読み取りを長く待たせないための短い書き込み（ジャーナルモードは delete のまま）。
WRITE_CHUNK_SIZE = 50

# 取得は同時に1つだけ（背景の取得と手動の再取得の両方がこれを通る）。
_RUN_LOCK = threading.Lock()


class BomFolderUnavailableError(RuntimeError):
    """BOM の共有フォルダを読めない（無い、またはTSVが1つも見えない）。全組を tsv_not_found で上書きしないために取得を止める。"""


def _is_default_db_path(db_path: str) -> bool:
    """config.is_default_db() と同じ判定（親フォルダ名が db のもの＝既定DB＝未選択）を、任意のパスに対して行う。"""
    return os.path.basename(os.path.dirname(db_path)) == config.DB_ROOT_FOLDER_NAME


def classify_pair(index: BOMFileIndex, setup_file_no: str, mounting_line: str) -> dict:
    """
    1組（正規化済み）の取得結果。part_no, status, resolved_file_no, tsv_mtime の dict（fetched_at は呼び出し元が付ける）。
    TSV はファイルNo. だけで探す（resolve_file_no）。K行の抽出と実装ラインでの絞り込みは BOMService._calculate_bom と同じ規則
    （K行は生産面で絞らない。実装ラインだけで絞る）。ただし実装ラインは、組のキーと同じ正規化をして比べる。
    """
    result = {"part_no": None, "status": None, "resolved_file_no": None, "tsv_mtime": None}

    resolved = index.resolve_file_no(setup_file_no)
    if resolved is None:
        problem = index.problems.get(setup_file_no) or {}
        result["status"] = (
            STATUS_AMBIGUOUS_TSV if problem.get("type") == "unresolved_multiple_candidates" else STATUS_TSV_NOT_FOUND
        )
        return result

    result["resolved_file_no"] = resolved
    result["tsv_mtime"] = index.get_mtime(resolved)
    try:
        rows = index.read_tsv(resolved)
    except Exception:
        logger.warning("BOM TSV を読めませんでした: file_no=%s", resolved, exc_info=True)
        result["status"] = STATUS_READ_ERROR
        return result

    codes = {
        row.get(COL_PART_NO) for row in rows
        if normalize_mounting_line(row.get(COL_MOUNTING_LINE)) == mounting_line
        and row.get(COL_TYPE) == TYPE_VALUE_BOARD and row.get(COL_PART_NO)
    }
    if not codes:
        result["status"] = STATUS_NO_K_ROW
    elif len(codes) >= 2:
        result["status"] = STATUS_MULTI_K
    else:
        result["status"] = STATUS_OK
        result["part_no"] = next(iter(codes))
    return result


def _collect_pairs(db_paths: list) -> tuple:
    """月次DB を読み取り専用で開き、{組: 計画の行数} を足し合わせる。開けない DB・計画の表が無い DB は飛ばす。戻り値は (組の辞書, 飛ばした DB のパスのリスト)。"""
    pair_rows = {}
    skipped = []
    for db_path in db_paths:
        try:
            con = open_monthly_db_readonly(db_path)
            try:
                if not has_plan_items_table(con):
                    skipped.append(db_path)
                    continue
                counts = count_active_plan_rows_by_pair(con)
            finally:
                con.close()
        except Exception:
            logger.warning("月次DB から組を集められませんでした（飛ばします）: %s", db_path, exc_info=True)
            skipped.append(db_path)
            continue
        for pair, n in counts.items():
            pair_rows[pair] = pair_rows.get(pair, 0) + n
    return pair_rows, skipped


def _select_targets(pairs, state: dict, index: BOMFileIndex, full: bool) -> list:
    """
    引く組。全部（full）か、表に無い組・status が ok 以外の組・TSV の更新日時が変わった組（ふだん）。
    ok の組は、表に残っている resolved_file_no の今の更新日時が tsv_mtime と同じなら読み直さない。
    """
    if full:
        return sorted(pairs)
    targets = []
    for pair in sorted(pairs):
        known = state.get(pair)
        if known is None or known["status"] != STATUS_OK:
            targets.append(pair)
        elif known["resolved_file_no"] is None or index.get_mtime(known["resolved_file_no"]) != known["tsv_mtime"]:
            targets.append(pair)
    return targets


def _empty_summary(full: bool) -> dict:
    return {
        "full": full, "dbs": 0, "skipped_dbs": [], "pairs": 0, "targets": 0, "written": 0,
        "by_status": {s: 0 for s in MAP_STATUSES}, "rows_by_status": {s: 0 for s in MAP_STATUSES},
        "index_seconds": 0.0, "seconds": 0.0,
    }


def _refresh(db_paths: list, full: bool, folder: str = None) -> dict:
    started = time.monotonic()
    summary = _empty_summary(full)
    summary["dbs"] = len(db_paths)

    pair_rows, skipped = _collect_pairs(db_paths)
    summary["skipped_dbs"] = skipped
    summary["pairs"] = len(pair_rows)
    if not pair_rows:
        summary["seconds"] = round(time.monotonic() - started, 3)
        return summary

    folder = folder if folder is not None else config.BOM_FOLDER_PATH
    t = time.monotonic()
    index = BOMFileIndex(folder)            # フォルダの走査は1回の取得で1回だけ
    summary["index_seconds"] = round(time.monotonic() - t, 3)
    if not index.list_available_file_nos():
        raise BomFolderUnavailableError(f"BOM フォルダを読めません（TSV が1つも見えません）: {folder}")

    targets = _select_targets(pair_rows, load_map_state(), index, full)
    summary["targets"] = len(targets)

    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch = []

    def flush():
        upsert_map_rows(batch)
        summary["written"] += len(batch)
        batch.clear()

    for setup_file_no, mounting_line in targets:
        found = classify_pair(index, setup_file_no, mounting_line)
        summary["by_status"][found["status"]] += 1
        summary["rows_by_status"][found["status"]] += pair_rows[(setup_file_no, mounting_line)]
        batch.append({"setup_file_no": setup_file_no, "mounting_line": mounting_line, "fetched_at": fetched_at, **found})
        if len(batch) >= WRITE_CHUNK_SIZE:
            flush()
    if batch:
        flush()

    summary["seconds"] = round(time.monotonic() - started, 3)
    return summary


def refresh_normal(db_paths: list, folder: str = None) -> dict:
    """
    ふだんの取得。db_paths（呼び出した時点の config.DB_PATH など）の対象の計画の組のうち、差分だけを引く。
    既定DB のパスは飛ばす。戻り値は取得の集計（pairs=集めた組、targets=引いた組、by_status=引いた組の status ごとの数）。
    """
    paths = [p for p in dict.fromkeys(db_paths) if not _is_default_db_path(p)]
    with _RUN_LOCK:
        return _refresh(paths, full=False, folder=folder)


def refresh_all_monthly(folder: str = None) -> dict:
    """
    手動の再取得。db フォルダの下のすべての月次DB（既定DB を除く）から組を集め、全部引き直す。
    開けない DB や計画の表が無い DB は飛ばして、skipped_dbs に入れる。
    """
    paths = [db_path_for_folder(name) for name in list_db_folders()]
    with _RUN_LOCK:
        summary = _refresh(paths, full=True, folder=folder)
    # 画面に出す status ごとの組の数。表の全行をビュー（マスター側の2つの status を含む8種類）で数える。
    view_by_status = {s: 0 for s in VIEW_STATUSES}
    for row in list_view_rows():
        view_by_status[row["status"]] += 1
    summary["view_by_status"] = view_by_status
    return summary
