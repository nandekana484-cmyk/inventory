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

### CANONICAL §7 の「C:\python310」と、この PC の状態の食い違い

CANONICAL_DESIGN_DECISIONS.md §7（実行環境・依存パッケージのドリフト問題）と run_app.bat のコメントは、システムの Python を `C:\python310` として書いている（§7 の対応1は `C:\python310\python.exe -m pip install -r requirements.txt`）。CANONICAL は書き換えていない。

- 2026-10-09 時点のこの PC には `C:\python310` が無い。PATH 上の Python は `C:\Users\nande\AppData\Local\Programs\Python\Python310\python.exe`（3.10.11）
- 同じ日の時点で、リポジトリ直下の `.venv` も無く、run_app.bat で起動できなかった。`.venv` を作り直して `inventory_app/requirements.txt` を入れ、起動できることを確認した（`.venv` も上の Python 3.10.11 から作った）
- §7 の記録が別の PC（2拠点のもう一方）のものなのか、この PC で Python を入れ直したのかは未確認。どちらかを確かめて、§7 に「どの PC の話か」を書き足すか、記述を直す

### opencv-python-headless が 5 系になった

2026-10-09 に `.venv` を作り直したとき、`inventory_app\requirements.txt`（`>=4.9.0`）から opencv-python-headless 5.0.0.93 が入った（`requirements.lock.txt` に記録）。それまでは 4 系で動かしていたと思われる。

- 使っているのは PDF 読み取りの OCR の前処理（`services/pdf_ocr_service.py` の `preprocess_image_for_ocr()`・`_deskew()`。bilateralFilter・CLAHE・adaptiveThreshold・minAreaRect など）
- メニューの「PDF読み取り」は常に無効（D-85）なので、画面から動作を確かめられない。有効にするときに、OCR の前処理が以前と同じ結果になるかを確認する（`docs/domain/pdf_ocr.md` の調整値は 4 系で決めたもの）

### リポジトリ直下の requirements.txt

リポジトリ直下の `requirements.txt` は古い pip freeze（UTF-16、版を `==` で固定）で、`inventory_app\requirements.txt` と紛らわしい。

- PDF 読み取りのパッケージ（pdfplumber・pytesseract・pdf2image・opencv-python-headless）が入っていない
- run_app.bat と `docs/setup.md` が使うのは `inventory_app\requirements.txt`。版の固定は `inventory_app\requirements.lock.txt` で行う
- 削除するか、整理する（どちらにするかは未決定）

### D-5x・D-8x の仮番号

D-9x と同じく、D番号が決まる前に書かれた仮番号。どれに当たるかは未確認（2026-10-09 時点、計11か所）。

- D-5x（8か所）: 既定DB（未選択）と在庫値出力済みの警告に関するもの。内容から D-56〜D-59 のどれかと思われる
  - `ui/main_window.py`: `__init__()`（2か所）、`_set_menu_enabled()`、`_confirm_proceed_despite_exported_db()`、`_update_current_db_label()`（2か所）、`_refresh_inventory_diff_export_warning()`
  - `models/operation_log.py`: `list_operation_log()` 付近（1か所）
- D-8x（3か所）: 内容から D-81〜D-85 のどれかと思われる
  - `ui/main_window.py`: `__init__()` の PDF読み取りボタンを常に無効にするコメント（CHANGELOG では D-85）
  - `services/production_import_service.py`: `is_already_registered()`（2か所。CHANGELOG では D-83 の内容）

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
