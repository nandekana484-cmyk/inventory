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

2026-10-09 に `.venv` を作り直したとき、`inventory_app\requirements.txt`（`>=4.9.0`）から opencv-python-headless 5.0.0.93 が入った（`requirements.lock.txt` に記録）。それまでの `.venv` に入っていた版は記録が無く、未確認。

- 使っているのは PDF 読み取りの OCR の前処理（`services/pdf_ocr_service.py` の `preprocess_image_for_ocr()`・`_deskew()`。bilateralFilter・CLAHE・adaptiveThreshold・minAreaRect など）
- メニューの「PDF読み取り」は常に無効（D-85）なので、画面から動作を確かめられない。有効にするときに、OCR の前処理が以前と同じ結果になるかを確認する（`docs/domain/pdf_ocr.md` の調整値は 2026-09-25 前後に決めたもので、そのときの版は未確認）

### リポジトリ直下の requirements.txt

リポジトリ直下の `requirements.txt` は古い pip freeze（UTF-16、版を `==` で固定）で、`inventory_app\requirements.txt` と紛らわしい。

- PDF 読み取りのパッケージ（pdfplumber・pytesseract・pdf2image・opencv-python-headless）が入っていない
- run_app.bat と `docs/setup.md` が使うのは `inventory_app\requirements.txt`。版の固定は `inventory_app\requirements.lock.txt` で行う
- 削除するか、整理する（どちらにするかは未決定）

### 発注数0・実績0の行が「入力済み」になる

`services/production_entry_service.py` の `is_plan_row_completed()` は「実績合計 >= 発注数」で判定するため、発注数0・実績0の行は 0 >= 0 で「入力済み」と判定され、「入力済みを隠す」で隠れる。段階1で移す前からの挙動（テストでは現在の挙動として固定している）。業務上これで正しいかは未確認。

### find_opposite_side_plan の parse_dt だけ前後の空白を除かない

`models/kitting_plan.py` の `find_opposite_side_plan()` 内の `parse_dt` は `datetime.strptime(value, ...)` を直接呼ぶので、前後に空白がある plan_start_datetime は解釈できず None になる。ほかの複製（`services/date_parsing.py` の `_parse_plan_start_datetime()` など）は `str(value).strip()` してから解釈する。どちらに揃えるべきかは未確認（揃えると挙動が変わるので、リファクタリングとは別に扱う）。

### D-5x・D-8x の仮番号

D-9x と同じく、D番号が決まる前に書かれた仮番号。どれに当たるかは未確認（2026-10-09 時点、計11か所）。

- D-5x（8か所）: 既定DB（未選択）と在庫値出力済みの警告に関するもの。内容から D-56〜D-59 のどれかと思われる
  - `ui/main_window.py`: `__init__()`（2か所）、`_set_menu_enabled()`、`_confirm_proceed_despite_exported_db()`、`_update_current_db_label()`（2か所）、`_refresh_inventory_diff_export_warning()`
  - `models/operation_log.py`: `list_operation_log()` 付近（1か所）
- D-8x（3か所）: 内容から D-81〜D-85 のどれかと思われる
  - `ui/main_window.py`: `__init__()` の PDF読み取りボタンを常に無効にするコメント（CHANGELOG では D-85）
  - `services/production_import_service.py`: `is_already_registered()`（2か所。CHANGELOG では D-83 の内容）

## 既知の違反

2026-10-13（段階4の後）の時点。新しく増やさず、そのファイルを触るときに直す（CLAUDE.md「層のルール」）。
行番号は変わりやすいので、関数名で参照する。

### 残っているもの

| 種類 | ファイル | 内容 |
|---|---|---|
| 他モジュールの `_` 始まりの関数 | `ui/production_import_staging_window.py` | `ui/plan_candidate_dialog.py` の `_sort_candidates_by_closeness`・`_is_large_qty_diff`・`_is_large_date_diff` などを import |
| 他モジュールの `_` 始まりの関数 | `services/unprocessed_check_service.py` | `services/ng_list_service.py` の `_fetch_ng_list_rows()`・`services/wip_list_service.py` の `_fetch_wip_list_rows()`。段階4で ui から移したとき「名前を変えない」ルールに従ったため。公開名への改名は未決定 |

ui → models の直接呼び出し（ui/ のほぼ全画面）は「既存コードは触るときだけ直す」の対象で、この一覧には数えていない（`docs/architecture.md` の表を参照）。

### 解消したもの（段階4、2026-10-13）

一方向以外の import と models/ の外の SQL 実行は、`ast` による機械的な検査で 0 件。

| 種類 | ファイル | どう解消したか |
|---|---|---|
| services → ui の import | `services/unprocessed_check_service.py` | NG 一覧・仕掛一覧の行の組み立てを `services/ng_list_service.py`・`services/wip_list_service.py` へ移した。画面には呼ぶだけの staticmethod を残した |
| models → services の import | `models/kitting_plan.py`・`models/production_import_staging.py` | `normalize_product_name()` を `models/kitting_plan.py` へ移した（`services/production_import_service.py` は re-export） |
| models → services の import | `models/lot_status_history.py` | `record_lot_status_snapshot()` を、評価して記録する本体（`services/production_service.py`）と INSERT（`models.lot_status_history.insert_lot_status_snapshot()`）に分けた。呼び出し側3ファイルの import を書き換えた |
| SQL が models/ の外にある | `services/backup_service.py`・`db_migration_carryover.py`・`master_merge_service.py`・`kitting_import_service.py`・`lot_status_history_service.py`・`ui/kitting_plan_import.py` | SQL 文を変えずに `models/db_common.py`（DB ファイル全体の検査・汎用の読み取り）・`models/kitting_plan.py`・`models/production.py` へ移した。旧位置の関数は名前を変えずに残し、models を呼ぶだけにした |
| 層をまたいだ同じファイル名 | `services/lot_status_history.py` | `services/lot_status_history_service.py` に改名した。旧ファイルは re-export だけ |
| 他の画面の `_` 属性・メソッド | `ui/production_import_staging_window.py` → 親画面 | 段階3bで公開メソッドに置き換えた |
| 業務ロジックが ui/ にある | `ui/production_import_staging_window.py` | 段階1で自動登録の判定を `services/staging_eligibility.py` へ移した（旧位置は re-export） |
