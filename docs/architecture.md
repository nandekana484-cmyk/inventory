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
| actual_correction_window.py | production_service | operation_log | window_utils |
| async_task.py | — | — | loading_window |
| board_structure_import_window.py | — | board_structure_master, operation_log | loading_window, warnings_list_window, window_utils |
| checkable_treeview.py | — | — | — |
| column_filter.py | — | — | window_utils |
| daily_drawdown_window.py | lot_status_history | — | window_utils |
| daily_report_window.py | — | board_structure_master | window_utils |
| db_delete_helper.py | db_delete_service, db_lifecycle_service | — | — |
| highlight_colors.py | — | — | — |
| inventory_diff_window.py | inventory_diff_service | operation_log | daily_report_window, window_utils |
| inventory_input_window.py | — | inventory, operation_log | loading_window, window_utils |
| kitting_plan_import.py | csv_format_detection, kitting_import_service | kitting_plan, operation_log | loading_window, window_utils |
| kitting_production_entry.py | csv_format_detection, production_entry_service, production_import_service, production_service | board_structure_master, kitting_plan, ng_declarations, production, production_import_staging | actual_correction_window, column_filter, daily_drawdown_window, loading_window, lot_progress_window, production_import_staging_window, unified_report_window, window_utils |
| loading_window.py | — | — | window_utils |
| login_window.py | — | workers | main_window, window_utils, worker_registration_window |
| lot_progress_window.py | production_service | — | daily_report_window, loading_window, window_utils |
| main_window.py | backup_service, db_lifecycle_service, db_lock_service, db_migration_carryover, master_merge_service, unprocessed_check_service | db_lifecycle_log, operation_log | async_task, board_structure_import_window, db_delete_helper, inventory_diff_window, inventory_input_window, kitting_plan_import, kitting_production_entry, login_window※, ng_input_window, operation_log_window, parts_attributes_import_window, pdf_ocr_import_window※, production_side_master_window, theoretical_inventory_import_window, window_utils, wip_expansion_window, worker_management_window, worker_registration_window |
| ng_input_window.py | bom_service, ng_list_service, production_service | ng_declarations, ng_exclusion_list, operation_log, scrap_records | checkable_treeview, column_filter, loading_window, parts_ng_report_window※, plan_candidate_dialog※, product_ng_report_window※, scrap_correction_window, window_utils |
| operation_log_window.py | — | db_lifecycle_log, operation_log | window_utils |
| parts_attributes_import_window.py | — | operation_log, parts_attributes | loading_window, warnings_list_window, window_utils |
| parts_ng_report_window.py | — | scrap_records | daily_report_window, window_utils |
| pdf_ocr_import_window.py | pdf_ocr_service | — | highlight_colors, loading_window, window_utils |
| plan_candidate_dialog.py | date_parsing | — | highlight_colors, window_utils |
| product_ng_report_window.py | — | kitting_plan, scrap_records | daily_report_window, ng_input_window※, window_utils |
| production_import_staging_window.py | production_entry_service, production_import_service, production_service, staging_eligibility | kitting_plan, production, production_import_staging | highlight_colors, kitting_production_entry※, plan_candidate_dialog, window_utils |
| production_side_master_window.py | csv_parsing_common | kitting_plan, operation_log, production_side_master | checkable_treeview, highlight_colors, loading_window, warnings_list_window, window_utils |
| scrap_correction_window.py | — | operation_log, scrap_records | window_utils |
| theoretical_inventory_import_window.py | — | operation_log, theoretical_inventory | loading_window, window_utils |
| unified_report_window.py | production_service | operation_log, wip_board_snapshot | daily_report_window, kitting_production_entry※, lot_progress_window, window_utils |
| unmatched_production_window.py | — | — | window_utils |
| warnings_list_window.py | — | — | window_utils |
| window_utils.py | — | — | — |
| wip_expansion_window.py | bom_service, wip_list_service | operation_log, wip_board_snapshot, wip_exclusion_list, wip_scrap_records | checkable_treeview, column_filter, loading_window, window_utils, wip_parts_report_window※, wip_product_report_window※, wip_scrap_correction_window |
| wip_parts_report_window.py | — | wip_scrap_records | daily_report_window, window_utils |
| wip_product_report_window.py | — | wip_scrap_records | daily_report_window, window_utils, wip_expansion_window※ |
| wip_scrap_correction_window.py | — | operation_log, wip_scrap_records | window_utils |
| worker_management_window.py | — | operation_log, workers | window_utils, worker_registration_window |
| worker_registration_window.py | — | operation_log※, workers | window_utils |

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
| db_migration_carryover.py | kitting_plan, production | date_parsing, production_service | — |
| inventory_diff_service.py | inventory, scrap_records, theoretical_inventory, wip_board_snapshot, wip_scrap_records | — | — |
| kitting_import_service.py | board_structure_master, kitting_plan | — | — |
| lot_status_history.py | — | lot_status_history_service | — |
| lot_status_history_service.py | production | production_service | — |
| lot_status_service.py | board_structure_master, kitting_plan, lot_status_history, production | — | — |
| master_merge_service.py | board_structure_master, db_common, master, parts_attributes, production_side_master, workers | — | — |
| ng_list_service.py | kitting_plan, ng_declarations, ng_exclusion_list, scrap_records | — | — |
| pdf_ocr_service.py | inventory | — | — |
| production_entry_service.py | ng_declarations, operation_log | date_parsing, production_service | — |
| production_import_service.py | kitting_plan, production, production_import_staging | csv_parsing_common, production_service | — |
| production_report_service.py | kitting_plan, ng_declarations, production, scrap_records | lot_status_service | — |
| production_service.py | kitting_plan, production | date_parsing, lot_status_service, production_report_service | — |
| staging_eligibility.py | kitting_plan, production, production_import_staging | date_parsing, production_import_service | — |
| unprocessed_check_service.py | — | ng_list_service, wip_list_service | — |
| wip_list_service.py | wip_board_snapshot, wip_exclusion_list, wip_scrap_records | — | — |

## 層のルールと実態が食い違っている箇所（実測）

CLAUDE.md「層のルール」との食い違い。2026-10-13（段階6の後）の時点。
ui → services → models の一方向以外の import、models/ の外の SQL 実行、他のモジュールの `_` 始まりの名前の import は 0 件（`docs/refactoring_plan.md`「既知の違反」）。

| 種類 | 箇所 | 内容 |
|---|---|---|
| ui → models の直接呼び出し | ui/ のほぼ全画面 | 上の表のとおり。「既存コードは触るときだけ直す」の対象 |

## 共通の UI 部品（段階6）

- `ui/column_filter.py`: 一覧の列ごとの絞り込み（テキストの部分一致・チェックボックスの完全一致・期間指定）と並べ替えの判定、チェックボックス式の絞り込みポップアップ（`CheckboxFilterPopup`）。生産実績入力の計画一覧・NG 一覧・仕掛一覧が使う。画面ごとの違い（数値として並べる列、期間指定と「入力済みを隠す」は計画一覧だけ、など）は揃えずに、画面側に残すか引数で渡す
- `ui/async_task.py`: `run_in_background()`。LoadingWindow＋別スレッド＋after(200) のポーリング。今はメイン画面の4か所だけが使う（同じパターンはほかの画面にも残っている）

## 命名規則と実態が食い違っている箇所

- 層をまたいで同じファイル名: 無し（`services/lot_status_history.py` は段階4で `services/lot_status_history_service.py` に改名した。旧ファイルは re-export だけを残している）
- `ui/*_window.py` に当てはまらないもの: `kitting_plan_import.py`, `kitting_production_entry.py`, `plan_candidate_dialog.py`, `checkable_treeview.py`, `db_delete_helper.py`, `window_utils.py`, `highlight_colors.py`
- `services/*_service.py` に当てはまらないもの: `csv_format_detection.py`, `csv_parsing_common.py`, `date_parsing.py`, `db_migration_carryover.py`, `lot_status_history.py`（re-export だけ）, `staging_eligibility.py`
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

## 補足

上の表と食い違いの一覧は、2026-10-09 の実測で作った。違反の対応一覧は `docs/refactoring_plan.md`「既知の違反」。
