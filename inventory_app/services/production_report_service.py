"""
日報・月報（統合レポート）の表示データ。production_daily の実績を、計画・ロット状態と合わせて表示行と警告にする。
段階5で services/production_service.py から移した（関数の中身は変えていない）。production_service.py からも re-export している。
ロット状態の判定は services/lot_status_service.py の evaluate_lot_status() に一本化している。
"""
from models.kitting_plan import (
    find_plan_item_by_kitting_no,
    find_opposite_side_plan,
    list_active_side_plans_for_file,
)
from models.production import (
    get_app_cumulative_qty,
    list_daily_production_today,
    list_daily_production_range,
)
from models.scrap_records import list_scrap_summary_by_kitting_no
from models.ng_declarations import list_ng_declarations_latest
from services.lot_merge_service import load_lot_merge_map
from services.lot_status_service import evaluate_lot_status


def merged_lot_label(lot_eval) -> dict:
    """
    統合したロットの表示用の値（D-152）。統合していなければ空の辞書（行の形を変えない）。
    lot_label は「123456（+123456-）」。行の元のロットNoは、ファイルNoの後ろに添える（file_label_for()）。
    """
    if lot_eval is None or "display_lot_no" not in lot_eval:
        return {}
    return {"lot_label": lot_eval["display_lot_no"]}


def _aligned_board_name(lot_eval, plan):
    """統合したロットの評価で、計画の (ファイルNo, 面) の行に付いている基板名（統合先にそろえた名前）。無ければ None。"""
    for board in lot_eval["boards"]:
        for f in board["files"]:
            if (f["setup_file_no"], f["production_side"]) == (plan["setup_file_no"], plan["production_side"]):
                return board["board_name"]
    return None


def file_label_for(setup_file_no, source_lot_nos, target_lot) -> str:
    """統合したロットのファイルNoの表示。元のロットが統合先だけなら、ファイルNoのまま。例「0553（348588・348588-）」（D-152）。"""
    if not source_lot_nos or list(source_lot_nos) == [target_lot]:
        return setup_file_no
    return f"{setup_file_no}（" + "・".join(source_lot_nos) + "）"


def _build_report_rows(records):
    """
    production_daily のレコードから、日報・月報共通の表示行と警告を作る。
    - 引落・仕掛・未完了は _evaluate_lot_status() の結果をそのまま使う（判定は全画面でそこに一本化）。判定できないロットは未完成扱い
    - lot_no は計画を引き直さず、実績の lot_id を使う（kitting_list_no は lot_no をまたいで重複するため）
    - 面2の計画があるロット・ファイルNoの面1の行は表示しない（D-8）。面1合計が面2合計を上回れば inconsistency_warnings に記録する（D-111）
    - 構成基板数が不足するロットには、不足分の「未確定」仮想行を追加する（kitting_list_no=""、数量は None でなく 0）
    戻り値は (report_rows, inconsistency_warnings, order_qty_inconsistency_warnings, unregistered_board_warnings,
    excess_file_no_warnings, board_count_inconsistency_warnings)。各要素の形と経緯は docs/domain/lot_completion.md。
    統合したロット（D-150）: 評価・面1を隠す判定・警告は統合後のロットで行う。行の lot_no は実績の元のロットNoのまま
    （実績修正・NG・仕掛の抽出で使う）で、表示用に lot_label・file_label を足し、統合されるロットの行の発注数は統合先にそろえる。
    """
    merge_map = load_lot_merge_map()

    def group_lots(lot_no):
        target = merge_map.target_of(lot_no)
        members = merge_map.members_of(target)
        return [target] + members if members else [lot_no]

    enriched = []
    for rec in records:
        kitting_list_no = rec["kitting_list_no"]
        lot_no = rec["lot_id"] or ""
        plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no) if lot_no \
            else find_plan_item_by_kitting_no(kitting_list_no)
        enriched.append({
            "kitting_list_no": kitting_list_no,
            "plan": plan,
            "daily_qty": rec["daily_qty"],
            "lot_no": lot_no or (plan["lot_no"] if plan else ""),
            # 実績1件＝1行なので、実績の日付をそのまま使う（代表を選ぶ必要は無い）。
            "report_date": rec["report_date"],
        })

    # 面1の除外は「面2がアクティブな計画として存在するか」だけで判定する（D-8）。find_opposite_side_plan() は複数バッチを
    # 1件に絞る関数なので、ここで使うと誤った絞り込みに引き込まれる（D-111）。
    excluded_indices = set()
    file_keys_with_opposite = set()
    for idx, item in enumerate(enriched):
        plan = item["plan"]
        if plan is None:
            continue
        side = str(plan.get("production_side") or "").strip()
        if side != "1":
            continue

        setup_file_no = plan.get("setup_file_no")
        lot_no = item["lot_no"]
        # 統合したロットでは、統合後のロットのどれかに面2の計画があれば隠す（D-150）。統合していなければ従来どおり lot_no だけ。
        if all(find_opposite_side_plan(lot, setup_file_no, "1") is None for lot in group_lots(lot_no)):
            continue  # 面2計画が無い（片面のみの計画）→ 除外しない

        excluded_indices.add(idx)
        file_keys_with_opposite.add((merge_map.target_of(lot_no), setup_file_no))

    # 不整合は、ロットNo・ファイルNoごとの面1合計と面2合計で比べる（D-111。計画どうしを1対1で対応づけないので、複数バッチでも誤検出しない）。
    inconsistency_warnings = []
    for lot_no, setup_file_no in sorted(file_keys_with_opposite):
        lots = group_lots(lot_no)
        side_plans = [(lot, p) for lot in lots for p in list_active_side_plans_for_file(lot, setup_file_no)]
        side1_plans = [(lot, p) for lot, p in side_plans if str(p.get("production_side") or "").strip() == "1"]
        side2_plans = [(lot, p) for lot, p in side_plans if str(p.get("production_side") or "").strip() == "2"]
        side1_total = sum(get_app_cumulative_qty(p["kitting_list_no"], lot) for lot, p in side1_plans)
        side2_total = sum(get_app_cumulative_qty(p["kitting_list_no"], lot) for lot, p in side2_plans)

        if side1_total > side2_total:
            inconsistency_warnings.append({
                "lot_no": lot_no,
                "setup_file_no": setup_file_no,
                "side1_total": side1_total,
                "side2_total": side2_total,
                "diff": side1_total - side2_total,
                "side1_kitting_list_nos": [p["kitting_list_no"] for _lot, p in side1_plans],
                "side2_kitting_list_nos": [p["kitting_list_no"] for _lot, p in side2_plans],
            })

    # 「NGの有無」の絞り込み用に、NG 実績か NG 申告がある (kitting_list_no, lot_no, production_side) を先にまとめて取得する（N+1 回避）。
    ng_exists_keys = {
        (s["kitting_list_no"], s["lot_no"] or "", str(s["production_side"]))
        for s in list_scrap_summary_by_kitting_no() if s["record_count"] > 0
    } | {
        (d["kitting_list_no"], d["lot_no"] or "", str(d["production_side"]))
        for d in list_ng_declarations_latest() if (d["ng_qty"] or 0) > 0
    }

    report_rows = []
    seq = 1
    lot_status_cache = {}
    for idx, item in enumerate(enriched):
        if idx in excluded_indices:
            continue

        plan = item["plan"]
        kitting_list_no = item["kitting_list_no"]
        lot_no = item["lot_no"]
        daily_qty = item["daily_qty"]
        order_qty = plan["order_qty"] if plan else 0

        eval_lot_no = merge_map.target_of(lot_no)
        if eval_lot_no not in lot_status_cache:
            try:
                lot_status_cache[eval_lot_no] = evaluate_lot_status(lot_no, merge_map=merge_map)
            except ValueError:
                lot_status_cache[eval_lot_no] = None
        lot_eval = lot_status_cache[eval_lot_no]
        merged_fields = merged_lot_label(lot_eval)
        board_name = plan["board_name"] if plan else ""
        if merged_fields and plan is not None:
            # 統合されるロットの行は、発注数・基板名を統合先にそろえた値で出す（D-150）。
            if plan["lot_no"] != lot_eval["lot_no"] and lot_eval.get("aligned_order_qty") is not None:
                order_qty = lot_eval["aligned_order_qty"]
            board_name = _aligned_board_name(lot_eval, plan) or board_name
            merged_fields["file_label"] = file_label_for(plan["setup_file_no"], [plan["lot_no"]], lot_eval["lot_no"])

        file_actual = None
        if lot_eval is not None and plan is not None:
            file_actual = lot_eval["file_actuals"].get(
                (plan["setup_file_no"], plan["production_side"])
            )

        if lot_eval is not None and file_actual is not None:
            completed = lot_eval["lot_completed"]
            surplus_qty = file_actual - completed
            lot_remaining = lot_eval["order_quantity"] - completed
            confirmation_note = lot_eval["status_remarks"]
            status_color_category = lot_eval["status_color_category"]
        else:
            completed = 0
            surplus_qty = daily_qty
            lot_remaining = order_qty
            # 判定できないロットは、誤った理由を出さないよう「確認事項」を空欄・色無しにする。
            confirmation_note = ""
            status_color_category = None

        side_for_ng_key = plan["production_side"] if plan else None
        has_ng = (kitting_list_no, lot_no or "", str(side_for_ng_key)) in ng_exists_keys

        report_rows.append({
            "seq": seq,
            "kitting_list_no": kitting_list_no,
            "file_no": plan["setup_file_no"] if plan else "",
            "board_name": board_name,
            "production_side": plan["production_side"] if plan else None,
            "mounting_line": plan["mounting_line"] if plan else None,
            "lot_no": lot_no,
            "daily_qty": daily_qty,
            "app_cumulative_qty": get_app_cumulative_qty(kitting_list_no, lot_no),
            "order_qty": order_qty,
            "lot_completed": completed,
            "surplus_qty": surplus_qty,
            "lot_remaining": lot_remaining,
            "confirmation_note": confirmation_note,
            "status_color_category": status_color_category,
            "status": lot_eval["status"] if lot_eval is not None else None,
            "has_ng": has_ng,
            "report_date": item["report_date"],
            **merged_fields,
        })
        seq += 1

    # 構成基板数チェックと発注数の不一致を、この帳票に実績があったロットごとに警告・仮想行へ振り分ける（判定は evaluate_lot_status() 側）。
    order_qty_inconsistency_warnings = []
    unregistered_board_warnings = []
    excess_file_no_warnings = []
    board_count_inconsistency_warnings = []
    for lot_no, lot_eval in lot_status_cache.items():
        if lot_eval is None:
            continue  # 計画自体が現存しない（ValueError）→ 判定不能としてスキップ

        if lot_eval["order_qty_inconsistent"]:
            order_qty_inconsistency_warnings.append({
                "lot_no": lot_no, "order_qty_values": lot_eval["order_qty_values"],
            })

        for board_name in lot_eval["unregistered_board_names"]:
            unregistered_board_warnings.append({
                "lot_no": lot_no,
                "board_name": board_name,
                "file_nos": lot_eval["visible_file_nos"],
            })

        status = lot_eval["status"]
        if status == "board_count_inconsistent":
            for board in lot_eval["boards"]:
                if board["board_count"] is None:
                    continue  # 未登録側は上のunregistered_board_warningsで扱い済み
                board_count_inconsistency_warnings.append({
                    "lot_no": lot_no,
                    "board_name": board["board_name"],
                    "board_count": board["board_count"],
                    "board_count_values": lot_eval["board_count_values"],
                    "file_nos": lot_eval["visible_file_nos"],
                })
        elif status == "excess":
            for board in lot_eval["boards"]:
                excess_file_no_warnings.append({
                    "lot_no": lot_no,
                    "board_name": board["board_name"],
                    "board_count": lot_eval["board_count"],
                    "file_nos": lot_eval["excess_file_nos"],
                })
        elif status == "shortfall":
            # 「未確定」仮想行の基板名。空だと画面で「未登録」と誤表示されるので、ロット内の登録済みの基板名の先頭を代表にする。
            registered_board_names = sorted({
                b["board_name"] for b in lot_eval["boards"] if b["board_count"] is not None
            })
            representative_board_name = registered_board_names[0] if registered_board_names else ""

            for entry in lot_eval["missing_file_entries"]:
                report_rows.append({
                    "seq": seq,
                    "kitting_list_no": "",
                    "file_no": entry["setup_file_no"],
                    "board_name": representative_board_name,
                    "production_side": None,
                    "mounting_line": None,
                    "lot_no": lot_no,
                    "daily_qty": 0,
                    "app_cumulative_qty": 0,
                    "order_qty": entry["order_qty"] or 0,
                    "lot_completed": entry["lot_completed"],
                    "surplus_qty": entry["surplus_qty"],
                    "lot_remaining": lot_eval["order_quantity"] - lot_eval["lot_completed"],
                    "confirmation_note": lot_eval["status_remarks"],
                    "status_color_category": lot_eval["status_color_category"],
                    "status": status,
                    # 仮想行には kitting_list_no が無いので、NG は無しとする。
                    "has_ng": False,
                    # 元の実績レコードが無いので、日付も無い。
                    "report_date": None,
                    **merged_lot_label(lot_eval),
                })
                seq += 1

    return (
        report_rows, inconsistency_warnings, order_qty_inconsistency_warnings,
        unregistered_board_warnings, excess_file_no_warnings,
        board_count_inconsistency_warnings,
    )


def build_daily_report():
    """本日入力された実績から、日報の表示データを作る。戻り値は _build_report_rows() と同じ。"""
    records = list_daily_production_today()
    return _build_report_rows(records)


def build_monthly_report(from_date: str, to_date: str):
    """期間（from_date〜to_date、両端を含む）の実績から、月報の表示データを作る。戻り値は _build_report_rows() と同じ。"""
    records = list_daily_production_range(from_date, to_date)
    return _build_report_rows(records)
