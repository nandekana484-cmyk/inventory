"""
生産実績入力画面（ui/kitting/kitting_production_entry.py）の、画面に依存しない計算・判定。GUI に依存しない純粋関数。

リファクタリング段階1で移した。旧位置には re-export か、ここを呼ぶだけの薄いメソッドを残しているので、
既存の呼び出し側はそのまま動く。業務ルールは docs/domain/production_entry.md。
"""
from datetime import datetime

from models.ng_declarations import save_ng_declaration
from models.operation_log import log_operation
from services.date_parsing import parse_flexible_date
from services.production_service import (
    register_daily_result,
    overwrite_daily_result,
    get_daily_history,
)
from services.lot_merge_service import match_lot_of


def resolve_csv_report_date(raw_value):
    """
    CSV の払い出し日を "YYYY-MM-DD" に正規化する。解釈できなければ None（登録側で実行日を使う）。
    表記ゆれを残すと report_date の文字列範囲検索が壊れるため、必ず正規化する（docs/domain/production_entry.md）。
    """
    parsed = parse_flexible_date(raw_value)
    if parsed is None:
        return None
    return parsed.strftime("%Y-%m-%d")


def compute_ng_save_qty(own_qty_by_side, ng_side_plans):
    """
    NG の保存値を計算する（面2の NG は面1にも連動させる）。
      - 面1の保存値 ＝ 面1欄の入力値 ＋ 面2欄の入力値
      - 面2の保存値 ＝ 面2欄の入力値のみ
    合計が0の面は保存しない（既存の NG 申告に触れない）。docs/domain/production_entry.md 参照。
    ng_side_plans は面（"1"/"2"）-> その面の計画（無ければ None）。旧 KittingProductionEntryWindow._compute_ng_save_qty() の本体。
    """
    own_2 = own_qty_by_side.get("2", 0.0)
    save_qty_by_side = {}
    if ng_side_plans.get("1") is not None:
        total_1 = own_qty_by_side.get("1", 0.0) + own_2
        if total_1 > 0:
            save_qty_by_side["1"] = total_1
    if ng_side_plans.get("2") is not None and own_2 > 0:
        save_qty_by_side["2"] = own_2
    return save_qty_by_side


def is_plan_row_completed(row, lot_info, file_no_index, order_qty_index):
    """
    計画一覧の行が「入力済み」か。行の (file_no, 面) の実績合計が、その行の発注数以上なら True。
    lot_info は calculate_lot_completion() の戻り値。面は行の末尾の隠し要素（production_side）。
    旧 apply_plan_filters() 内の is_row_completed() から、キャッシュと DB 呼び出しを除いた判定部分。
    """
    # 末尾の隠し要素（production_side、_fetch_plan_list_rows()参照）。
    file_no = row[file_no_index]
    production_side = row[-1]
    file_actual = lot_info["file_actuals"].get((file_no, production_side), 0)
    order_qty = float(row[order_qty_index])
    # 統合したロットは、発注数を統合先にそろえた値で比べる（統合されるロットの発注数 170 などで「入力済み」にしない。D-150）。
    if lot_info.get("aligned_order_qty") is not None:
        order_qty = float(lot_info["aligned_order_qty"])
    return file_actual >= order_qty


def plan_row_tag(classification):
    """
    計画一覧の行に付ける背景色のタグ。面1だけの計画の分類が b（面2待ち）・c（生産面マスター未登録）のときだけ付ける（D-9x）。
    旧 _populate_plan_list_tree()・_refresh_plan_list_for_lot() にあった同じ式。
    """
    tag = (
        "needs_side2_wait" if classification == "b"
        else "side_master_unregistered" if classification == "c"
        else ""
    )
    return tag


def register_actual_and_ng(plan, ng_side_plans, daily_qty, save_qty_by_side, existing_daily_qty,
                           worker_id, worker_name, report_date=None, record_history=True):
    """
    実績→操作履歴→NG の順に DB へ書き込む。段階3aで _perform_registration() から移した書き込み部分。
    反対側の面には実績を書かない（面1への自動入力は廃止。D-169）。NG は ng_side_plans の両面に保存する（変えていない）。
    途中でエラーが出ても、成功した分は残す（ロールバックしない）。実績の登録に失敗したら、それ以降は書き込まない。
    実績の日付は report_date（None なら登録側で実行日。上書きなら既存の日付を引き継ぐ）、NG の日付は当日。
    ng_side_plans は面（"1"/"2"）-> その面の計画。save_qty_by_side は compute_ng_save_qty() の戻り値。
    戻り値の dict:
      actual_registered: 実績を登録できたか
      actual_error: 実績の登録で出た例外（成功時は None）
      new_cumulative: 登録後のアプリ内累計（失敗時は None）
      declared_sides: NG を保存した面（"1"/"2"）の一覧
      errors: NG の保存で出たエラーの文言の一覧
    """
    result = {
        "actual_registered": False,
        "actual_error": None,
        "new_cumulative": None,
        "declared_sides": [],
        "errors": [],
    }
    kitting_no = plan["kitting_list_no"]
    lot_no = plan["lot_no"]

    try:
        if existing_daily_qty is not None:
            new_cumulative = overwrite_daily_result(
                kitting_no, lot_no, daily_qty, worker_id, report_date=report_date,
                record_history=record_history,
            )
        else:
            new_cumulative = register_daily_result(
                kitting_no, lot_no, daily_qty, worker_id, report_date=report_date,
                record_history=record_history,
            )
    except Exception as e:
        result["actual_error"] = e
        return result
    result["actual_registered"] = True
    result["new_cumulative"] = new_cumulative

    log_operation(
        worker_name,
        "生産実績登録",
        detail=f"{kitting_no} / ロットNo. {lot_no} / {daily_qty:g}",
    )

    # NG は当日の日付で保存する（実績は CSV の払い出し日のまま）。
    ng_report_date = datetime.now().strftime("%Y-%m-%d")
    for side in ("1", "2"):
        if side not in save_qty_by_side:
            continue
        side_plan = ng_side_plans[side]
        try:
            save_ng_declaration(
                side_plan["kitting_list_no"], side_plan["setup_file_no"], int(side),
                save_qty_by_side[side], ng_report_date,
                lot_no=side_plan["lot_no"], is_unplanned=False,
            )
            result["declared_sides"].append(side)
        except Exception as e:
            result["errors"].append(f"面{side}のNG登録に失敗しました：{e}")

    return result


def format_registration_message(result, save_qty_by_side):
    """
    register_actual_and_ng() が実績の登録に成功したときの完了文。生産実績入力画面とステージング画面の共通の文言。
    save_qty_by_side は register_actual_and_ng() に渡したもの（申告した NG 数量の表示に使う）。
    """
    lines = [f"実績を登録しました。アプリ入力累計：{result['new_cumulative']:.0f}"]
    for side in result["declared_sides"]:
        lines.append(f"面{side}：NG数量{save_qty_by_side[side]:g}を申告しました")
    if result["errors"]:
        lines.append("")
        lines.append("以下の項目でエラーが発生しました（登録できた分はそのまま残っています）：")
        lines.extend(result["errors"])
    return "\n".join(lines)


def duplicate_registration_info(candidate, csv_lot_no, daily_qty, report_date):
    """
    登録待ちの行（CSV のロットNo csv_lot_no・数量・日付）を candidate の計画へ登録しようとしたとき、その計画にすでに実績があり、
    その実績がこの行と数量・日付とも同じではなければ、重複の確認（D-176）の内容を返す。そうでなければ None（同じ内容の登録は従来どおり）。
    先に登録された実績が実績CSVの別の行から来たのか、手で入れたのかは区別できないので、どちらでも同じ確認にする。
    戻り値: {"kitting_list_no", "lot_no", "existing": [(日付, 数量), ...], "row_date", "row_qty", "total", "combined_date"}。
    合計の日付（combined_date）は、既存の実績とこの行の日付の遅い方。
    """
    lot_no = match_lot_of(candidate, csv_lot_no)
    existing = get_daily_history(candidate["kitting_list_no"], lot_no)
    if not existing:
        return None
    row_date = resolve_csv_report_date(report_date)
    if any(r["daily_qty"] == daily_qty and r["report_date"] == row_date for r in existing):
        return None
    dates = [r["report_date"] for r in existing if r["report_date"]] + ([row_date] if row_date else [])
    return {
        "kitting_list_no": candidate["kitting_list_no"], "lot_no": lot_no,
        "existing": [(r["report_date"], r["daily_qty"]) for r in existing],
        "row_date": row_date, "row_qty": daily_qty,
        "total": sum(r["daily_qty"] for r in existing) + daily_qty,
        "combined_date": max(dates) if dates else None,
    }


def register_combined_with_existing(info, worker_id, worker_name, record_history=True):
    """
    重複の確認の「合計して登録」（D-176）。既存の実績とこの行の数量を合計した数量・遅い方の日付で、その計画の1件の実績にする
    （1計画＝1レコード、D-6）。操作履歴には合計の内訳を残す。更新後のアプリ内累計を返す。
    """
    new_cumulative = overwrite_daily_result(
        info["kitting_list_no"], info["lot_no"], info["total"], worker_id,
        report_date=info["combined_date"], record_history=record_history,
    )
    parts = "＋".join(f"{qty:g}" for _d, qty in info["existing"]) + f"＋{info['row_qty']:g}"
    log_operation(
        worker_name, "生産実績登録",
        detail=f"{info['kitting_list_no']} / ロットNo. {info['lot_no']} / {info['total']:g}（合計：{parts}）",
    )
    return new_cumulative


# 段階6で公開名を付けた。旧名は同じものを指す別名として残す（呼び出し側は新しい名前を使う）。
_resolve_csv_report_date = resolve_csv_report_date
