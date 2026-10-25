# services/external_actual_service.py
"""
現場実績入力アプリの実績CSV（services/external_actual_csv.py が解析する）を、人が確認したうえで実績（production_daily）と
NG申告（ng_declarations）に登録する。取り込み（登録待ちにためる）、一覧用の計算、登録の3つ。自動では書き込まない。
GUI には依存しない。業務ルールは docs/domain/external_actual.md。
"""
import logging
import os

import config
from db.migrations import LATEST_VERSION
from models import external_actual_rows as rows_model
from models.kitting_plan import get_latest_plan_by_kitting_no
from models.ng_declarations import list_ng_declarations_latest
from models.operation_log import log_operation
from models.production import get_app_cumulative_qty_bulk, summarize_daily_by_plan
from models.workers import get_all_workers
from services.csv_format_detection import (
    read_csv_header, detect_format_mismatch_warnings,
    EXTERNAL_ACTUAL_CSV_FORMAT_LABEL, EXTERNAL_ACTUAL_CSV_SIGNATURE_COLUMNS,
    PRODUCTION_CSV_FORMAT_LABEL, PRODUCTION_CSV_SIGNATURE_COLUMNS,
    PLAN_CSV_FORMAT_LABEL, PLAN_CSV_SIGNATURE_COLUMNS,
)
from services.external_actual_csv import parse_external_actual_csv, normalize_text
from services.lot_status_service import preview_completed_lot_registration, record_lot_status_snapshot
from services.production_entry_service import compute_ng_save_qty, build_planned_qty_mismatch_lines, register_actual_and_ng
from services.production_import_service import group_active_plan_items_by_lot
from services.production_service import side1_registration_block

logger = logging.getLogger(__name__)

OPERATION_NAME = "現場CSV取込登録"
HISTORY_TRIGGER = "external_csv"

STATE_RED, STATE_YELLOW, STATE_GREEN, STATE_GRAY = "red", "yellow", "green", "gray"
NG_ZERO_REMARK = "NG 0 は既存の申告を変えません"

# register_rows() の結果の status
RESULT_REGISTERED, RESULT_PARTIAL, RESULT_FAILED = "registered", "partial", "failed"
RESULT_REFUSED, RESULT_CHANGED, RESULT_SKIPPED = "refused", "changed", "skipped"
CHANGED_MESSAGE = "プレビュー後に値が変わりました。一覧を更新して確認してください"


class ExternalActualRefused(ValueError):
    """取り込み・登録を受け付けないとき。メッセージはそのまま画面に出せる。"""


def current_db_folder_name() -> str:
    """今の月次DB のフォルダ名（例 2026-10）。バッチの target_db_name と、CSV の「元のDB」の比較に使う。"""
    return os.path.basename(os.path.dirname(config.DB_PATH))


def check_db_ready():
    """
    取り込み・登録を始められない理由（始められるなら None）。取り込み時と登録の直前に呼ぶ。
    既定DB（未選択）では断る。DB の版がアプリの最新と違えば断る（表が無い・形が違う DB に書かないため）。
    """
    if config.is_default_db():
        return "データベースが未選択です。月次データベースを選択してから取り込んでください"
    version = rows_model.get_db_user_version()
    if version != LATEST_VERSION:
        return f"データベースの版（{version}）がこのアプリの版（{LATEST_VERSION}）と違うため、取り込めません"
    return None


# ---------------------------------------------------------------- 取り込み

def detect_wrong_format_warnings(file_path: str) -> list:
    """現場実績CSVではなく、実績CSV・キッティング計画CSVを選んでいないか（ヘッダーの固有列で調べる）。疑いがなければ空。"""
    try:
        header = read_csv_header(file_path)
    except Exception:
        return []          # 読めないときは、取り込みの解析がエラーとして知らせる
    warnings = []
    for other_columns, other_label in ((PRODUCTION_CSV_SIGNATURE_COLUMNS, PRODUCTION_CSV_FORMAT_LABEL),
                                       (PLAN_CSV_SIGNATURE_COLUMNS, PLAN_CSV_FORMAT_LABEL)):
        for message in detect_format_mismatch_warnings(
                header, EXTERNAL_ACTUAL_CSV_SIGNATURE_COLUMNS, EXTERNAL_ACTUAL_CSV_FORMAT_LABEL, other_columns, other_label):
            if message not in warnings:
                warnings.append(message)
    return warnings


def import_external_actual_file(file_path: str, imported_by: str) -> dict:
    """
    CSV を読んで検査し、通った行を登録待ちにためる（実績・NG にはまだ書かない）。
    同じ計画の登録待ちがあれば新しい行で置き換える。取り込み済みの書き出しより古い行は、警告して取り込まない。
    今のDB が未選択・版が違うときは ExternalActualRefused。ヘッダーの不備・書式版の不一致は、戻り値の fatal に説明を入れて何も取り込まない。
    戻り値: fatal・file_sha256・total_rows・errors・warnings（external_actual_csv.parse_external_actual_csv() と同じ）に加えて
      imported: 登録待ちにためた行数  replaced: 置き換えた計画 [(キッティングNo., ロットNo.), ...]
      stale: 古い書き出しのため取り込まなかった行  already_imported: 同じ内容のファイルを取り込み済みなら、そのバッチ（なければ None）
    """
    reason = check_db_ready()
    if reason:
        raise ExternalActualRefused(reason)

    result = parse_external_actual_csv(file_path, current_db_folder_name())
    result.update({"imported": 0, "replaced": [], "stale": [], "already_imported": None, "batch_id": None})
    if result["fatal"]:
        return result

    previous = rows_model.find_batch_by_sha256(result["file_sha256"])
    if previous:
        result["already_imported"] = previous
        result["warnings"].append(
            f"同じ内容のファイルを取り込み済みです（{previous['imported_at']}、{previous['source_file']}）")

    latest = rows_model.get_latest_exported_at_by_plan(
        (row["kitting_list_no"], row["lot_no"]) for row in result["rows"])
    accepted = []
    for row in result["rows"]:
        newest = latest.get((row["kitting_list_no"], row["lot_no"]))
        if newest is not None and newest > row["exported_at"]:
            result["stale"].append({
                "csv_row_no": row["csv_row_no"], "kitting_list_no": row["kitting_list_no"], "lot_no": row["lot_no"],
                "messages": [f"取り込み済みの書き出し（{newest}）より古い書き出し（{row['exported_at']}）のため取り込みません"],
            })
        else:
            accepted.append(row)
    if result["stale"]:
        result["warnings"].append(f"古い書き出しの行が {len(result['stale'])} 行あるため、取り込みませんでした")

    if accepted:
        batch = {
            "source_file": os.path.basename(file_path), "file_sha256": result["file_sha256"],
            "exported_at": max(row["exported_at"] for row in accepted),
            "source_db_name": accepted[0]["source_db_name"], "target_db_name": current_db_folder_name(),
        }
        saved = rows_model.import_batch(batch, accepted, imported_by)
        result.update({"imported": saved["inserted"], "replaced": saved["replaced"], "batch_id": saved["batch_id"]})
    return result


# ---------------------------------------------------------------- 登録前の一覧（差の計算と状態の判定）

def _plan_side(plan) -> str:
    return str((plan or {}).get("production_side") or "").strip()


def _load_context() -> dict:
    """行の評価に使う、計画・作業者・今のDB のフォルダ名。計画は1回の取得で全件（完成済みも含む）。面2がある面1は一覧から外れる。"""
    plan_index = {}
    for items in group_active_plan_items_by_lot(include_completed=True).values():
        for item in items:
            plan_index[(normalize_text(item["kitting_list_no"]), normalize_text(item["lot_no"]))] = item
    return {
        "plan_index": plan_index, "worker_ids": {w["worker_id"] for w in get_all_workers()},
        "folder": current_db_folder_name(), "state_cache": {}, "side_block_cache": {},
    }


def _resolve_face1_plan(row: dict) -> tuple:
    """
    面2の行の、つながる面1の計画と、赤の理由。面1のキッティングNo.があれば、それとロットNo.の計画が面1・同じファイルNo.で存在すること。
    空なら面1は無いものとして扱う（NG面1 が 0 より大きければ、面1の計画が分からないので赤）。
    """
    face1_no = row["face1_kitting_list_no"]
    if not face1_no:
        return None, ([f"面1のキッティングNo.が空のため、NG面1（{row['ng_side1']}）を保存する面1の計画が分かりません"] if row["ng_side1"] else [])
    plan = get_latest_plan_by_kitting_no(face1_no, row["lot_no"])
    if plan is None:
        return None, [f"面1のキッティングNo.「{face1_no}」（ロットNo. {row['lot_no']}）の計画がありません"]
    reds = []
    if _plan_side(plan) != "1":
        reds.append(f"面1のキッティングNo.「{face1_no}」の計画は面{_plan_side(plan) or '不明'}です")
    if row["file_no"] != normalize_text(plan.get("setup_file_no")):
        reds.append(f"面1のキッティングNo.「{face1_no}」の計画のファイルNo.（{plan.get('setup_file_no')}）がCSVの{row['file_no']}と違います")
    return (None if reds else plan), reds


def _resolve_row(ctx: dict, row: dict) -> dict:
    """行の計画と NG を保存する面の計画を決め、登録できない理由（赤）を集める。DB を引くのは、計画が一覧に無い行と NG を面1に連動させる行だけ。"""
    reds = []
    plan = ctx["plan_index"].get((row["kitting_list_no"], row["lot_no"]))
    if plan is None:
        plan = get_latest_plan_by_kitting_no(row["kitting_list_no"], row["lot_no"])    # 面2がある面1は一覧に出ない。登録しない理由を示すために引く
    ng_side_plans = {"1": None, "2": None}
    if plan is None:
        reds.append("計画がありません（キッティングNo.とロットNo.が一致する有効な計画がありません）")
    else:
        side = _plan_side(plan)
        if side != row["side"]:
            reds.append(f"面が計画（面{side or '不明'}）と違います（CSVは面{row['side']}）")
        if row["file_no"] != normalize_text(plan.get("setup_file_no")):
            reds.append(f"ファイルNo.が計画（{plan.get('setup_file_no')}）と違います（CSVは{row['file_no']}）")
        block_key = (plan["lot_no"], plan.get("setup_file_no"), side)
        if block_key not in ctx["side_block_cache"]:
            ctx["side_block_cache"][block_key] = side1_registration_block(plan)
        if ctx["side_block_cache"][block_key]:
            reds.append(ctx["side_block_cache"][block_key])
        if side in ("1", "2"):
            ng_side_plans[side] = plan
        if side == "2":
            # 面2の NG は面1にも連動して保存される。面1の計画は推測せず、CSV の「面1のキッティングNo.」（現場アプリの管理者が確定）で決める。
            ng_side_plans["1"], face1_reds = _resolve_face1_plan(row)
            reds.extend(face1_reds)
        if side == "1" and row["ng_side2"]:
            reds.append("面1の行に NG面2 が入っています")
    if row["worker_id"] not in ctx["worker_ids"]:
        reds.append(f"担当者ID「{row['worker_id']}」が作業者マスターにありません")
    if row["target_db_name"] != ctx["folder"]:
        reds.append(f"取り込んだDB（{row['target_db_name']}）が今のDB（{ctx['folder']}）と違います")
    own_qty_by_side = {s: float(row[f"ng_side{s}"]) for s in ("1", "2") if ng_side_plans[s] is not None}
    return {"plan": plan, "ng_side_plans": ng_side_plans, "reds": reds,
            "save_qty": compute_ng_save_qty(own_qty_by_side, ng_side_plans)}


def _read_existing(resolved: list) -> list:
    """resolved（_resolve_row() の結果）それぞれの、今の実績と NG 申告。まとめて引く。計画が無い行は None。"""
    pairs = [(r["plan"]["kitting_list_no"], r["plan"]["lot_no"]) for r in resolved if r["plan"] is not None]
    totals = get_app_cumulative_qty_bulk(pairs)
    summaries = summarize_daily_by_plan(pairs)
    declared = {(d["kitting_list_no"], int(d["production_side"]), d["lot_no"] or ""): d["ng_qty"]
                for d in list_ng_declarations_latest()}
    existing = []
    for r in resolved:
        if r["plan"] is None:
            existing.append(None)
            continue
        pair = (r["plan"]["kitting_list_no"], r["plan"]["lot_no"])
        summary = summaries.get(pair)
        ng = {s: declared.get((p["kitting_list_no"], int(s), p["lot_no"] or ""))
              for s, p in r["ng_side_plans"].items() if p is not None}
        existing.append({
            "daily_count": summary["count"] if summary else 0, "daily_total": totals[pair] if summary else None,
            "daily_date": summary["last_date"] if summary else None, "ng": ng,
        })
    return existing


def _has_difference(row: dict, save_qty: dict, existing: dict) -> bool:
    """登録すると、今の実績・日付・NG 申告のどれかが変わるか。NG は保存する面（NG 0 の面は保存しない）だけ比べる。"""
    if existing["daily_count"] != 1 or existing["daily_total"] != row["daily_qty"] or existing["daily_date"] != row["report_date"]:
        return True
    return any(existing["ng"].get(s) is None or float(existing["ng"][s]) != float(q) for s, q in save_qty.items())


def _ng_now(existing: dict) -> tuple:
    """NG面1・NG面2の今の値。面1は「面1の保存値 − 面2の保存値」（面1の保存値には面2の分が足されているため）。申告が無ければ None。"""
    saved1, saved2 = existing["ng"].get("1"), existing["ng"].get("2")
    own1 = None if saved1 is None else max(float(saved1) - float(saved2 or 0), 0.0)
    return own1, (None if saved2 is None else float(saved2))


def _completed_info(row: dict, plan: dict, now: dict, has_difference: bool, state_cache: dict = None):
    """差がある行だけ、登録するとロットが完成済みのところへ足すことになるか（preview_completed_lot_registration()。ロットごとに評価する）。"""
    if not has_difference:
        return None
    return preview_completed_lot_registration(
        plan, row["daily_qty"], state_cache, own_existing=now["daily_total"] if now["daily_count"] else 0)


def _yellow_remarks(row: dict, res: dict, now: dict, completed) -> list:
    """差がある行に、確認が要る点（黄）があればその説明。"""
    remarks = []
    if completed is not None:
        remarks.append(f"完成済みロットです（完成数 {completed['lot_completed']:g}／発注数 {completed['order_quantity']:g}、"
                       f"登録後の合計 {completed['new_total']:g}）")
    remarks.extend(build_planned_qty_mismatch_lines(res["ng_side_plans"], row["daily_qty"], res["save_qty"]))
    if now["daily_date"] and row["report_date"] < now["daily_date"]:
        remarks.append(f"既存の実績の日付（{now['daily_date']}）より古い日付です")
    return remarks


def evaluate_rows(rows: list) -> list:
    """
    登録待ちの行（rows_model.list_pending_rows() の形）それぞれの、今→新の差・状態・備考。rows と同じ順。
    状態: red=登録できない  yellow=差があり、確認が要る（完成済みロット・予定数との不一致・既存より古い日付）  green=新規か差あり  gray=変更なし。
    snapshot は登録の直前に読み直した値と比べる材料（register_rows()）。キーが _ で始まるものはこのモジュールの中で使う。
    """
    ctx = _load_context()
    resolved = [_resolve_row(ctx, row) for row in rows]
    existing = _read_existing(resolved)
    evaluated = []
    for row, res, now in zip(rows, resolved, existing):
        plan, remarks, state = res["plan"], list(res["reds"]), STATE_RED
        item = {
            "row_id": row["row_id"], "csv_row_no": row["csv_row_no"], "source_file": row["source_file"],
            "kitting_list_no": row["kitting_list_no"], "lot_no": row["lot_no"], "side": row["side"], "file_no": row["file_no"],
            "report_date": row["report_date"], "worker_id": row["worker_id"], "qty_new": row["daily_qty"],
            "ng1_new": row["ng_side1"], "ng2_new": row["ng_side2"], "qty_now": None, "ng1_now": None, "ng2_now": None,
            "planned_qty": plan.get("planned_qty") if plan else None, "is_overwrite": False, "snapshot": None,
            "label": "登録不可", "_plan": plan, "_ng_side_plans": res["ng_side_plans"], "_save_qty": res["save_qty"],
        }
        if now is not None:
            item["qty_now"], (item["ng1_now"], item["ng2_now"]) = now["daily_total"], _ng_now(now)
            item["is_overwrite"] = now["daily_count"] > 0
        if not res["reds"]:
            different = _has_difference(row, res["save_qty"], now)
            completed = _completed_info(row, plan, now, different, ctx["state_cache"])
            item["snapshot"] = dict(now, completed=completed is not None)
            if not different:
                state, item["label"] = STATE_GRAY, "変更なし"
            else:
                state, item["label"] = STATE_GREEN, ("上書き" if item["is_overwrite"] else "新規")
                yellow = _yellow_remarks(row, res, now, completed)
                if yellow:
                    state, item["label"] = STATE_YELLOW, "要確認"
                    remarks.extend(yellow)
                if now["daily_count"] > 1:
                    remarks.append("実績が複数行あります（登録すると1行にまとめます）")
                if now["daily_date"] and row["report_date"] > now["daily_date"]:
                    remarks.append(f"日付が {now['daily_date']} から {row['report_date']} に変わります")
            if any(s not in res["save_qty"] for s, p in res["ng_side_plans"].items() if p is not None):
                remarks.append(NG_ZERO_REMARK)
        item.update({"state": state, "remarks": remarks})
        evaluated.append(item)
    return evaluated


def build_preview() -> list:
    """登録待ちの行すべての evaluate_rows()。"""
    return evaluate_rows(rows_model.list_pending_rows())


# ---------------------------------------------------------------- 登録

def _fmt(value) -> str:
    return "なし" if value is None else f"{value:g}"


def _history_detail(row: dict, item: dict, fresh: dict) -> str:
    """操作履歴に残す内容。上書きの前後の値（実績と、保存した面の NG の保存値）を含む。"""
    parts = [f"{row['kitting_list_no']} / ロットNo. {row['lot_no']} / 日付 {row['report_date']}",
             f"実績 {_fmt(fresh['daily_total'])}→{row['daily_qty']:g}"]
    parts += [f"NG面{s}の保存値 {_fmt(fresh['ng'].get(s))}→{qty:g}" for s, qty in sorted(item["_save_qty"].items())]
    parts.append(f"CSVの担当者 {row['worker_id']} / 取込ファイル {row['source_file']}")
    return " / ".join(parts)


def register_rows(row_ids, expected_snapshots: dict, registered_by: dict) -> dict:
    """
    選ばれた登録待ちの行を、行ごとに register_actual_and_ng() で実績・NG に登録する。
    registered_by は登録した人 {"worker_id", "name"}。実績の作業者はCSVの担当者ID、日付はCSVの日付。
    expected_snapshots は行ごとの、一覧を作ったときの snapshot（evaluate_rows()）。登録の直前に今の値を読み直し、違う行は登録しない。
    バッチの取り込み先DB が今のDB と違う行があれば、何も登録せず ExternalActualRefused。
    NG の部分失敗（実績は登録できたあとの NG の失敗）は、その行を登録待ちのまま残し、結果に出す（もう一度登録しても同じ結果になる）。
    履歴（lot_status_history）は行ごとには記録せず、最後にロットごとに1回記録する。
    戻り値: {"results": [{"row_id", "status", "message", "kitting_list_no", "lot_no", "new_cumulative", "declared_sides", "errors"}, ...],
             "lot_nos": 実績を登録したロット}。status は RESULT_* の定数。
    """
    reason = check_db_ready()
    if reason:
        raise ExternalActualRefused(reason)
    rows_by_id = {row_id: rows_model.get_pending_row(row_id) for row_id in dict.fromkeys(row_ids)}
    folder = current_db_folder_name()
    wrong_dbs = sorted({r["target_db_name"] for r in rows_by_id.values() if r and r["target_db_name"] != folder})
    if wrong_dbs:
        raise ExternalActualRefused(f"取り込んだDB（{'、'.join(wrong_dbs)}）が今のDB（{folder}）と違うため、登録を中止しました")

    evaluated = {e["row_id"]: e for e in evaluate_rows([r for r in rows_by_id.values() if r and r["status"] == "pending"])}
    results, lot_nos = [], []
    for row_id, row in rows_by_id.items():
        item = evaluated.get(row_id)
        out = {"row_id": row_id, "kitting_list_no": (row or {}).get("kitting_list_no"), "lot_no": (row or {}).get("lot_no"),
               "new_cumulative": None, "declared_sides": [], "errors": []}
        results.append(out)
        if item is None:
            out.update(status=RESULT_SKIPPED, message="すでに登録済みか、登録待ちにありません")
            continue
        if item["state"] == STATE_RED:
            out.update(status=RESULT_REFUSED, message="登録できません：" + "／".join(item["remarks"]))
            continue
        plan = item["_plan"]
        out["plan_key"] = (plan["kitting_list_no"], plan["lot_no"])
        fresh = _read_existing([{"plan": plan, "ng_side_plans": item["_ng_side_plans"]}])[0]
        fresh["completed"] = _completed_info(row, plan, fresh, _has_difference(row, item["_save_qty"], fresh)) is not None
        expected = expected_snapshots.get(row_id)
        if expected is not None and fresh != expected:
            out.update(status=RESULT_CHANGED, message=CHANGED_MESSAGE)
            continue

        result = register_actual_and_ng(
            plan, item["_ng_side_plans"], row["daily_qty"], item["_save_qty"],
            fresh["daily_total"] if fresh["daily_count"] else None,
            row["worker_id"], registered_by["name"], report_date=row["report_date"], record_history=False)
        if not result["actual_registered"]:
            out.update(status=RESULT_FAILED, message=f"実績の登録に失敗しました：{result['actual_error']}")
            continue
        if plan["lot_no"] not in lot_nos:
            lot_nos.append(plan["lot_no"])
        detail = _history_detail(row, item, fresh)
        out.update(new_cumulative=result["new_cumulative"], declared_sides=result["declared_sides"], errors=result["errors"])
        if result["errors"]:
            log_operation(registered_by["name"], OPERATION_NAME, detail=detail + "（NGの登録でエラー：" + "、".join(result["errors"]) + "）")
            out.update(status=RESULT_PARTIAL, message="実績は登録しましたが、NGの登録でエラーが出ました：" + "、".join(result["errors"]))
            continue
        rows_model.mark_row_registered(row_id, registered_by["worker_id"])
        log_operation(registered_by["name"], OPERATION_NAME, detail=detail)
        out.update(status=RESULT_REGISTERED, message="登録しました")

    for lot_no in lot_nos:
        try:
            record_lot_status_snapshot(lot_no, HISTORY_TRIGGER)
        except Exception:
            logger.exception("lot_status_historyの記録に失敗しました（lot_no=%s, trigger_source=%s）。登録自体はそのまま続行します。",
                             lot_no, HISTORY_TRIGGER)
    return {"results": results, "lot_nos": lot_nos}
