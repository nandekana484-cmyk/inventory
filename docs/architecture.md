# アーキテクチャ：画面 → service → model の対応表

import を実測して作った表（`ast` で `inventory_app/` 配下の import 文を抽出）。2026-10-09（HEAD c08a88a）に作り、2026-10-13（リファクタリング段階4の後）に同じ方法で作り直した。
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
| common/async_task.py | — | — | loading_window |
| common/checkable_treeview.py | — | — | — |
| common/color_legend.py | — | — | — |
| common/column_filter.py | — | — | common/window_utils |
| common/db_delete_helper.py | db_delete_service, db_lifecycle_service | — | — |
| common/highlight_colors.py | — | — | — |
| common/lot_row_style.py | — | — | — |
| common/window_utils.py | — | — | — |
| inventory/inventory_diff_window.py | inventory_diff_service | operation_log | common/window_utils, reports/daily_report_window |
| inventory/inventory_input_window.py | — | inventory, operation_log | common/window_utils, loading_window |
| inventory/theoretical_inventory_import_window.py | — | operation_log, theoretical_inventory | common/window_utils, loading_window |
| kitting/actual_correction_window.py | inventory_diff_service, production_service | operation_log | common/window_utils, kitting/cancel_registration_dialog |
| kitting/cancel_registration_dialog.py | — | — | common/window_utils |
| kitting/kitting_plan_import.py | csv_format_detection, kitting_import_service | kitting_plan, operation_log | common/window_utils, loading_window |
| kitting/kitting_production_entry.py | csv_format_detection, lot_merge_service, lot_status_service, production_entry_service, production_import_service, production_service | board_structure_master, kitting_plan, ng_declarations, production, production_import_staging | common/color_legend, common/column_filter, common/highlight_colors, common/window_utils, kitting/actual_correction_window, loading_window, production/daily_drawdown_window, production/lot_progress_window, production/production_import_staging_window, reports/unified_report_window |
| kitting/plan_candidate_dialog.py | date_parsing | — | common/highlight_colors, common/window_utils |
| loading_window.py | — | — | common/window_utils |
| login_window.py | — | workers | common/window_utils, main_window, master/worker_registration_window |
| main_window.py | backup_service, db_lifecycle_service, db_lock_service, db_migration_carryover, master_merge_service, unprocessed_check_service | db_lifecycle_log, operation_log | common/async_task, common/db_delete_helper, common/window_utils, inventory/inventory_diff_window, inventory/inventory_input_window, inventory/theoretical_inventory_import_window, kitting/kitting_plan_import, kitting/kitting_production_entry, login_window※, master/board_structure_import_window, master/parts_attributes_import_window, master/production_side_master_window, master/worker_management_window, master/worker_registration_window, ng_wip/ng_input_window, ng_wip/wip_expansion_window, system/operation_log_window, system/pdf_ocr_import_window※ |
| master/board_structure_import_window.py | — | board_structure_master, operation_log | common/window_utils, loading_window, system/warnings_list_window |
| master/parts_attributes_import_window.py | — | operation_log, parts_attributes | common/window_utils, loading_window, system/warnings_list_window |
| master/production_side_master_window.py | csv_parsing_common | kitting_plan, operation_log, production_side_master | common/checkable_treeview, common/highlight_colors, common/window_utils, loading_window, system/warnings_list_window |
| master/worker_management_window.py | — | operation_log, workers | common/window_utils, master/worker_registration_window |
| master/worker_registration_window.py | — | operation_log※, workers | common/window_utils |
| ng_wip/ng_input_window.py | bom_service, ng_list_service, production_service | ng_declarations, ng_exclusion_list, operation_log, scrap_records | common/checkable_treeview, common/column_filter, common/window_utils, kitting/plan_candidate_dialog※, loading_window, ng_wip/scrap_correction_window, reports/parts_ng_report_window※, reports/product_ng_report_window※ |
| ng_wip/scrap_correction_window.py | — | operation_log, scrap_records | common/window_utils |
| ng_wip/wip_expansion_window.py | bom_service, wip_list_service | operation_log, wip_board_snapshot, wip_exclusion_list, wip_scrap_records | common/checkable_treeview, common/column_filter, common/window_utils, loading_window, ng_wip/wip_scrap_correction_window, reports/wip_parts_report_window※, reports/wip_product_report_window※ |
| ng_wip/wip_scrap_correction_window.py | — | operation_log, wip_scrap_records | common/window_utils |
| production/daily_drawdown_window.py | lot_status_history | — | common/lot_row_style, common/window_utils |
| production/duplicate_plan_dialog.py | — | — | common/window_utils |
| production/lot_merge_window.py | inventory_diff_service, lot_merge_items_service, lot_merge_service | — | common/window_utils, production/lot_progress_window |
| production/lot_progress_window.py | lot_merge_items_service, lot_status_remarks, lot_status_service, production_report_service, production_service | — | common/lot_row_style, common/window_utils, loading_window, production/lot_merge_window※ |
| production/production_import_staging_window.py | lot_merge_service, lot_status_service, production_entry_service, production_import_service, production_service, staging_eligibility | kitting_plan, production, production_import_staging | common/color_legend, common/highlight_colors, common/window_utils, kitting/kitting_production_entry※, kitting/plan_candidate_dialog, production/duplicate_plan_dialog |
| production/unmatched_production_window.py | — | — | common/window_utils |
| reports/daily_report_window.py | — | board_structure_master | common/lot_row_style, common/window_utils |
| reports/parts_ng_report_window.py | — | scrap_records | common/window_utils, reports/daily_report_window |
| reports/product_ng_report_window.py | — | kitting_plan, scrap_records | common/window_utils, ng_wip/ng_input_window※, reports/daily_report_window |
| reports/unified_report_window.py | production_service | operation_log, wip_board_snapshot | common/window_utils, kitting/kitting_production_entry※, production/lot_progress_window, reports/daily_report_window |
| reports/wip_parts_report_window.py | — | wip_scrap_records | common/window_utils, reports/daily_report_window |
| reports/wip_product_report_window.py | — | wip_scrap_records | common/window_utils, ng_wip/wip_expansion_window※, reports/daily_report_window |
| system/operation_log_window.py | — | db_lifecycle_log, operation_log | common/window_utils |
| system/pdf_ocr_import_window.py | pdf_ocr_service | — | common/highlight_colors, common/window_utils, loading_window |
| system/warnings_list_window.py | — | — | common/window_utils |

## service → model

| service | model (models/) | 他の services | ui |
|---|---|---|---|
| app_settings_service.py | — | — | — |
| backup_service.py | db_common | — | — |
| bom_file_service.py | — | — | — |
| bom_service.py | bom_master, parts_attributes | bom_file_service | — |
| csv_format_detection.py | — | — | — |
| csv_parsing_common.py | — | — | — |
| date_parsing.py | — | — | — |
| db_delete_service.py | — | db_lock_service | — |
| db_lifecycle_service.py | db_lifecycle_log, kitting_plan, operation_log | app_settings_service, backup_service, db_delete_service | — |
| db_lock_service.py | — | — | — |
| db_migration_carryover.py | kitting_plan, lot_merges, production | date_parsing, production_service | — |
| inventory_diff_service.py | inventory, operation_log, scrap_records, theoretical_inventory, wip_board_snapshot, wip_scrap_records | — | — |
| kitting_import_service.py | board_structure_master, kitting_plan | — | — |
| lot_merge_items_service.py | kitting_plan | — | — |
| lot_merge_service.py | kitting_plan, lot_merges | lot_merge_items_service, lot_status_service※ | — |
| lot_status_history.py | — | lot_status_history_service | — |
| lot_status_history_service.py | production | production_report_service, production_service | — |
| lot_status_remarks.py | — | — | — |
| lot_status_service.py | board_structure_master, kitting_plan, lot_status_history, production | lot_merge_items_service, lot_merge_service, lot_status_remarks | — |
| master_merge_service.py | board_structure_master, db_common, master, parts_attributes, production_side_master, workers | — | — |
| ng_list_service.py | kitting_plan, ng_declarations, ng_exclusion_list, scrap_records | — | — |
| pdf_ocr_service.py | inventory | — | — |
| production_entry_service.py | ng_declarations, operation_log | date_parsing, lot_merge_service, production_service | — |
| production_import_service.py | kitting_plan, production, production_import_staging | csv_parsing_common, lot_merge_service, production_service | — |
| production_report_service.py | kitting_plan, ng_declarations, production, scrap_records | lot_merge_service, lot_status_service | — |
| production_service.py | kitting_plan, ng_declarations, production, scrap_records, wip_scrap_records | date_parsing, lot_merge_service, lot_status_service, production_report_service | — |
| staging_eligibility.py | kitting_plan, production, production_import_staging | date_parsing, lot_merge_service, lot_status_service, production_import_service | — |
| unprocessed_check_service.py | — | ng_list_service, wip_list_service | — |
| wip_list_service.py | wip_board_snapshot, wip_exclusion_list, wip_scrap_records | — | — |

## 層のルールと実態が食い違っている箇所（実測）

CLAUDE.md「層のルール」との食い違い。2026-10-13（段階6の後）の時点。
ui → services → models の一方向以外の import、models/ の外の SQL 実行、他のモジュールの `_` 始まりの名前の import は 0 件（`docs/refactoring_plan.md`「既知の違反」）。

| 種類 | 箇所 | 内容 |
|---|---|---|
| ui → models の直接呼び出し | ui/ のほぼ全画面 | 上の表のとおり。「既存コードは触るときだけ直す」の対象 |

## 共通の UI 部品（段階6）

- `ui/common/column_filter.py`: 一覧の列ごとの絞り込み（テキストの部分一致・チェックボックスの完全一致・期間指定）と並べ替えの判定、チェックボックス式の絞り込みポップアップ（`CheckboxFilterPopup`）。生産実績入力の計画一覧・NG 一覧・仕掛一覧が使う。画面ごとの違い（数値として並べる列、期間指定と「入力済みを隠す」は計画一覧だけ、など）は揃えずに、画面側に残すか引数で渡す
- `ui/common/async_task.py`: `run_in_background()`。LoadingWindow＋別スレッド＋after(200) のポーリング。今はメイン画面の4か所だけが使う（同じパターンはほかの画面にも残っている）
- `ui/common/lot_row_style.py`（2026-10-15、D-125）: ロット単位の一覧の行の色（ロットごとの縞＋状態の文字色）のタグ名・色・設定関数と、ロットごとに縞の色を1回だけ決める `LotRowTagger`。実績レポート・ロット進捗チェック・日々の引落一覧が使う。`reports/daily_report_window.py` からも `configure_lot_stripe_tags`・`configure_status_color_tags` を re-export している

## 命名規則と実態が食い違っている箇所

- 層をまたいで同じファイル名: 無し（`services/lot_status_history.py` は段階4で `services/lot_status_history_service.py` に改名した。旧ファイルは re-export だけを残している）
- `ui/*_window.py` に当てはまらないもの: `kitting_plan_import.py`, `kitting_production_entry.py`, `plan_candidate_dialog.py`, `checkable_treeview.py`, `db_delete_helper.py`, `window_utils.py`, `highlight_colors.py`, `lot_row_style.py`, `color_legend.py`
- `services/*_service.py` に当てはまらないもの: `csv_format_detection.py`, `csv_parsing_common.py`, `date_parsing.py`, `db_migration_carryover.py`, `lot_status_history.py`（re-export だけ）, `staging_eligibility.py`
- `models/<テーブル名>.py` に当てはまらないもの（各ファイルの CREATE TABLE から確認）: `db_common.py`（接続ヘルパー）、`master.py`（parts・final_products）、`inventory.py`（inventory_stock）、`kitting_plan.py`（kitting_plan_batches・kitting_plan_items・pending_kitting_plan_items）、`production_import_staging.py`（csv_import_batches・pending_csv_import_rows）、`production.py`（CREATE TABLE なし。production_daily は `db/schema.sql` で作る）

## 行数上位10ファイル（2026-10-09）

| 行数 | ファイル | 変更コミット数 |
|---|---|---|
| 2600 | ui/kitting/kitting_production_entry.py | 21 |
| 2416 | ui/production/production_import_staging_window.py | 14 |
| 1754 | services/production_service.py | 15 |
| 1580 | ui/main_window.py | 24 |
| 1472 | ui/ng_wip/ng_input_window.py | 12 |
| 1191 | ui/ng_wip/wip_expansion_window.py | 11 |
| 1186 | ui/master/production_side_master_window.py | 未集計 |
| 1176 | models/kitting_plan.py | 12 |
| 1112 | services/pdf_ocr_service.py | 未集計 |
| 1050 | ui/reports/unified_report_window.py | 未集計 |

（変更コミット数は上位15件だけを集計した。「未集計」は15位（7コミット）より少ない）

## 補足

上の表と食い違いの一覧は、2026-10-09 の実測で作った。違反の対応一覧は `docs/refactoring_plan.md`「既知の違反」。
