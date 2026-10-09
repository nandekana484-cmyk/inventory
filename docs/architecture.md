# アーキテクチャ：画面 → service → model の対応表

2026-10-09 時点（HEAD c08a88a）の import を実測して作った表（`ast` で `inventory_app/` 配下の import 文を抽出）。
パスは `inventory_app/` からの相対パス。`※` は関数内での import（循環 import を避けるための遅延 import）。
import の向きだけを見ているので、実際の呼び出し回数や呼び出し経路は表していない。

## 層の構成（実測）

- 起動経路: `main.py` → `ui/login_window.py` → `ui/main_window.py` → 各画面
- `models/` の DB 接続はすべて `models/db_common.py`（`get_connection()` = 月次DB、`get_master_connection()` = master.db）を経由する
- `db/`（init_db・migration）と単発スクリプトは `config` と `sqlite3` を直接使う
- `config.py` は関数内で `services.app_settings_service` を import する（`set_db_path()` の中）

## 画面 → service / model

| 画面 (ui/) | service (services/) | model (models/) | 他の ui |
|---|---|---|---|
| board_structure_import_window.py | — | board_structure_master, operation_log | loading_window, warnings_list_window, window_utils |
| checkable_treeview.py | — | — | — |
| daily_drawdown_window.py | lot_status_history | — | window_utils |
| daily_report_window.py | — | board_structure_master | window_utils |
| db_delete_helper.py | db_delete_service | db_lifecycle_log, operation_log | — |
| highlight_colors.py | — | — | — |
| inventory_diff_window.py | inventory_diff_service | operation_log | daily_report_window, window_utils |
| inventory_input_window.py | — | inventory, operation_log | loading_window, window_utils |
| kitting_plan_import.py | csv_format_detection, kitting_import_service | kitting_plan, operation_log | loading_window, window_utils |
| kitting_production_entry.py | csv_format_detection, production_import_service, production_service | board_structure_master, kitting_plan, ng_declarations, operation_log, production, production_import_staging | daily_drawdown_window, loading_window, lot_progress_window, plan_candidate_dialog, production_import_staging_window, unified_report_window, window_utils |
| loading_window.py | — | — | window_utils |
| login_window.py | — | workers | main_window, window_utils, worker_registration_window |
| lot_progress_window.py | production_service | — | daily_report_window, loading_window, window_utils |
| main_window.py | app_settings_service, backup_service, db_lock_service, db_migration_carryover, master_merge_service, unprocessed_check_service | db_lifecycle_log, kitting_plan, operation_log | board_structure_import_window, db_delete_helper, inventory_diff_window, inventory_input_window, kitting_plan_import, kitting_production_entry, loading_window, login_window※, ng_input_window, operation_log_window, parts_attributes_import_window, pdf_ocr_import_window※, production_side_master_window, theoretical_inventory_import_window, window_utils, wip_expansion_window, worker_management_window, worker_registration_window |
| ng_input_window.py | bom_service, production_service | kitting_plan, ng_declarations, ng_exclusion_list, operation_log, scrap_records | checkable_treeview, loading_window, parts_ng_report_window※, plan_candidate_dialog※, product_ng_report_window※, scrap_correction_window, window_utils |
| operation_log_window.py | — | db_lifecycle_log, operation_log | window_utils |
| parts_attributes_import_window.py | — | operation_log, parts_attributes | loading_window, warnings_list_window, window_utils |
| parts_ng_report_window.py | — | scrap_records | daily_report_window, window_utils |
| pdf_ocr_import_window.py | pdf_ocr_service | — | highlight_colors, loading_window, window_utils |
| plan_candidate_dialog.py | — | — | highlight_colors, window_utils |
| product_ng_report_window.py | — | kitting_plan, scrap_records | daily_report_window, ng_input_window※, window_utils |
| production_import_staging_window.py | production_import_service | kitting_plan, lot_status_history, production, production_import_staging | highlight_colors, kitting_production_entry※, plan_candidate_dialog, window_utils |
| production_side_master_window.py | csv_parsing_common | kitting_plan, operation_log, production_side_master | checkable_treeview, highlight_colors, loading_window, warnings_list_window, window_utils |
| scrap_correction_window.py | — | operation_log, scrap_records | window_utils |
| theoretical_inventory_import_window.py | — | operation_log, theoretical_inventory | loading_window, window_utils |
| unified_report_window.py | production_service | operation_log, wip_board_snapshot | daily_report_window, kitting_production_entry※, lot_progress_window, window_utils |
| unmatched_production_window.py | — | — | window_utils |
| warnings_list_window.py | — | — | window_utils |
| window_utils.py | — | — | — |
| wip_expansion_window.py | bom_service | operation_log, wip_board_snapshot, wip_exclusion_list, wip_scrap_records | checkable_treeview, loading_window, window_utils, wip_parts_report_window※, wip_product_report_window※, wip_scrap_correction_window |
| wip_parts_report_window.py | — | wip_scrap_records | daily_report_window, window_utils |
| wip_product_report_window.py | — | wip_scrap_records | daily_report_window, window_utils, wip_expansion_window※ |
| wip_scrap_correction_window.py | — | operation_log, wip_scrap_records | window_utils |
| worker_management_window.py | — | operation_log, workers | window_utils, worker_registration_window |
| worker_registration_window.py | — | operation_log※, workers | window_utils |

## service → model

| service | model (models/) | 他の services | ui |
|---|---|---|---|
| app_settings_service.py | — | — | — |
| backup_service.py | — | — | — |
| bom_file_service.py | — | — | — |
| bom_service.py | bom_master, parts_attributes | bom_file_service | — |
| csv_format_detection.py | — | — | — |
| csv_parsing_common.py | — | — | — |
| db_delete_service.py | — | db_lock_service | — |
| db_lock_service.py | — | — | — |
| db_migration_carryover.py | kitting_plan, lot_status_history, production | production_service | — |
| inventory_diff_service.py | inventory, scrap_records, theoretical_inventory, wip_board_snapshot, wip_scrap_records | — | — |
| kitting_import_service.py | board_structure_master, kitting_plan | — | — |
| lot_status_history.py | db_common※ | production_service | — |
| master_merge_service.py | board_structure_master, db_common, master, parts_attributes, production_side_master, workers | — | — |
| pdf_ocr_service.py | inventory | — | — |
| production_import_service.py | kitting_plan, production, production_import_staging | csv_parsing_common, production_service | — |
| production_service.py | board_structure_master, kitting_plan, lot_status_history, ng_declarations, production, scrap_records | — | — |
| unprocessed_check_service.py | — | — | ng_input_window, wip_expansion_window |

## 層のルールと実態が食い違っている箇所（実測）

CLAUDE.md「層のルール」との食い違い。どれも既存コードで、今回は記録だけしてコードは変えていない。

| 種類 | 箇所 | 内容 |
|---|---|---|
| services → ui | `services/unprocessed_check_service.py` | トップレベルで `ui.ng_input_window.NgInputWindow`・`ui.wip_expansion_window.WipExpansionWindow` を import し、`_fetch_ng_list_rows()`・`_fetch_wip_list_rows()` を呼んでいる。理由はモジュールの docstring に書かれている（集計ロジックを二重に持たないため） |
| models → services | `models/kitting_plan.py` `find_matching_plan_items()` | `services.production_import_service.normalize_product_name` を関数内で import |
| models → services | `models/production_import_staging.py` `upsert_pending_csv_import_row()` | 同上 |
| models → services | `models/lot_status_history.py` `record_lot_status_snapshot()` | `services.production_service.evaluate_lot_status` を関数内で import |
| SQL が models/ の外にある | `services/backup_service.py`, `services/db_migration_carryover.py`, `services/master_merge_service.py`, `services/kitting_import_service.py`, `services/lot_status_history.py`, `ui/kitting_plan_import.py` `_load_items_for_batch()` | `execute()` を直接呼んでいる（それぞれ 2・5・3・1・1・1 か所） |
| 他画面の `_` 属性・メソッド | `ui/production_import_staging_window.py` | 親の `KittingProductionEntryWindow` にある `_perform_registration()`、`_pending_csv_row_removal`、`_pending_csv_report_date`、`_build_registration_preview()`、`_show_registration_confirm_dialog()` などを直接使っている。docstring に「意図的」とある |
| 他モジュールの `_` 関数 | `ui/production_import_staging_window.py`・`ui/kitting_production_entry.py` の import 文 | `ui.plan_candidate_dialog` の `_sort_candidates_by_closeness`・`_is_large_qty_diff`・`_is_large_date_diff`・`_parse_flexible_date` を import |
| 業務ロジックが ui/ にある | `ui/production_import_staging_window.py` | 自動登録の判定（`is_auto_confirmable()`、`evaluate_auto_register_eligibility()`）がモジュールレベルの関数として ui/ にある |
| ui → models の直接呼び出し | ui/ のほぼ全画面 | 上の表のとおり。「既存コードは触るときだけ直す」の対象 |

## 命名規則と実態が食い違っている箇所

- 層をまたいで同じファイル名: `services/lot_status_history.py` と `models/lot_status_history.py`
- `ui/*_window.py` に当てはまらないもの: `kitting_plan_import.py`, `kitting_production_entry.py`, `plan_candidate_dialog.py`, `checkable_treeview.py`, `db_delete_helper.py`, `window_utils.py`, `highlight_colors.py`
- `services/*_service.py` に当てはまらないもの: `csv_format_detection.py`, `csv_parsing_common.py`, `db_migration_carryover.py`, `lot_status_history.py`
- `models/<テーブル名>.py` に当てはまらないもの（各ファイルの CREATE TABLE から確認）: `db_common.py`（接続ヘルパー）、`master.py`（parts・final_products）、`inventory.py`（inventory_stock）、`kitting_plan.py`（kitting_plan_batches・kitting_plan_items・pending_kitting_plan_items）、`production_import_staging.py`（csv_import_batches・pending_csv_import_rows）、`production.py`（CREATE TABLE なし。production_daily は `db/schema.sql` で作る）

## 行数上位10ファイル（2026-10-09）

| 行数 | ファイル | 変更コミット数 |
|---|---|---|
| 2600 | ui/kitting_production_entry.py | 21 |
| 2416 | ui/production_import_staging_window.py | 14 |
| 1754 | services/production_service.py | 15 |
| 1580 | ui/main_window.py | 24 |
| 1472 | ui/ng_input_window.py | 12 |
| 1191 | ui/wip_expansion_window.py | 11 |
| 1186 | ui/production_side_master_window.py | 未集計 |
| 1176 | models/kitting_plan.py | 12 |
| 1112 | services/pdf_ocr_service.py | 未集計 |
| 1050 | ui/unified_report_window.py | 未集計 |

（変更コミット数は上位15件だけを集計した。「未集計」は15位（7コミット）より少ない）

## 肥大化ファイル調査レポートとの関係

肥大化ファイルの調査レポートは `docs/reports/bloat_survey_20261009.md`。行番号は古いので、参照するときは関数名を使う。上の表と食い違いの一覧は、2026-10-09 の実測だけで作った（レポートとは突き合わせていない）。違反の対応一覧は `docs/refactoring_plan.md`「既知の違反」。
