# リファクタリング計画

## 方針

## 段階表

### 段階7: DB を開くときに不足した列を補う仕組み

- 時期: 本番運用を始める前、または次のマイグレーションを追加する前
- 内容: DB を開くとき（新規作成・切替・バックアップからの復元・起動時の自動再接続）に、不足している列やインデックスを補う
- 背景: 現在のマイグレーションスクリプトは既定DBにしか適用されず、切替・復元時の自動補正もない（`docs/db.md`「既知の制約」）

## 進捗

## 未解決の宿題

### D-9x の仮番号

コメントの「D-9x」は、CANONICAL_DESIGN_DECISIONS.md の D番号が決まる前に書かれた仮番号。内容から D-92〜D-94（生産面マスター）のどれかと思われるが、どれに当たるかは未確認。正しい番号が分かったら置き換える。

- 前回確認した3か所（`ui/kitting_production_entry.py`）
  - `_fetch_plan_list_rows()` の隠し要素1のコメント（生産面マスターの分類）
  - `create_widgets()` の計画一覧のタグ設定のコメント（b・c の背景色）
  - `_build_registration_preview()` の分類のコメント（b・c なら確認ダイアログで理由を示す）
- ほかにも同じ仮番号がある（2026-10-09 時点、計34か所）: `models/kitting_plan.py`（8）、`models/production_side_master.py`（7）、`ui/production_side_master_window.py`（6）、`ui/kitting_plan_import.py`（3）、`ui/production_import_staging_window.py`（3）、`ui/main_window.py`（2）、`services/master_merge_service.py`（1）、`services/production_import_service.py`（1）

## 既知の違反

2026-10-09 時点（HEAD c08a88a）。どれも既存コード。新しく増やさず、そのファイルを触るときに直す（CLAUDE.md「層のルール」）。
行番号は変わりやすいので、関数名で参照する。

### services → ui の import

| ファイル | 内容 |
|---|---|
| `services/unprocessed_check_service.py` | トップレベルで `ui.ng_input_window.NgInputWindow`・`ui.wip_expansion_window.WipExpansionWindow` を import し、`check_unprocessed_items()` から `_fetch_ng_list_rows()`・`_fetch_wip_list_rows()` を呼んでいる |

### models → services の import（すべて関数内での import）

| ファイル | 関数 | import しているもの |
|---|---|---|
| `models/kitting_plan.py` | `find_matching_plan_items()` | `services.production_import_service.normalize_product_name` |
| `models/production_import_staging.py` | `upsert_pending_csv_import_row()` | `services.production_import_service.normalize_product_name` |
| `models/lot_status_history.py` | `record_lot_status_snapshot()` | `services.production_service.evaluate_lot_status` |

### SQL が models/ の外にある（services/ と ui/）

`db/`（init_db・マイグレーション）と単発スクリプトは、ルール上の例外として扱う。

| ファイル | 関数 | SQL の内容 |
|---|---|---|
| `services/backup_service.py` | `validate_monthly_db_backup()` | `PRAGMA integrity_check` と `sqlite_master` の参照（バックアップファイルの検査） |
| `services/db_migration_carryover.py` | `_fetch_plan_items_for_lot()`、`_fetch_existing_plan_start_datetimes_for_lot()`、`_lot_already_migrated()`、`_fetch_production_daily_for_lot()` | kitting_plan_items・production_daily の SELECT（5か所） |
| `services/master_merge_service.py` | `_table_exists()`、`merge_master_from_backup()` | `sqlite_master` の参照と、バックアップ側・現在側テーブルの SELECT（3か所） |
| `services/kitting_import_service.py` | `import_kitting_plan_csv()` | kitting_plan_batches の UPDATE（1か所） |
| `services/lot_status_history.py` | `get_drawdown_date_range()` | production_daily の MIN/MAX（1か所） |
| `ui/kitting_plan_import.py` | `_load_items_for_batch()` | kitting_plan_items の SELECT（1か所）。ui に SQL がある唯一の箇所 |

### 他の画面・モジュールの `_` 始まりの属性やメソッドを使っている

| ファイル | 相手 | 使っているもの |
|---|---|---|
| `ui/production_import_staging_window.py` | 親画面 `ui/kitting_production_entry.py` の `KittingProductionEntryWindow` | `_perform_registration()`、`_pending_csv_row_removal`、`_pending_csv_report_date`、`_build_registration_preview()`、`_show_registration_confirm_dialog()` など |
| `ui/production_import_staging_window.py` | `ui/plan_candidate_dialog.py` | `_sort_candidates_by_closeness`・`_is_large_qty_diff`・`_is_large_date_diff`・`_parse_flexible_date` を import |
| `ui/kitting_production_entry.py` | `ui/plan_candidate_dialog.py` | `_parse_flexible_date` を import |
| `services/unprocessed_check_service.py` | `ui/ng_input_window.py`・`ui/wip_expansion_window.py` | `_fetch_ng_list_rows()`・`_fetch_wip_list_rows()` |

### 層をまたいだ同じファイル名

- `services/lot_status_history.py` と `models/lot_status_history.py`

### 業務ロジックが ui/ にある

- `ui/production_import_staging_window.py`: `is_auto_confirmable()`、`evaluate_auto_register_eligibility()`（自動登録の判定）
