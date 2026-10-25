# 他のPCをリファクタリング後の main に移す手順

2026-10-09〜10-13 のリファクタリング（段階1〜8）で、ファイルの置き場所・モジュール名・関数の置き場所が大きく変わった。
このファイルは、リファクタリングより前の状態（`c08a88a` 以前）で開発していた別のPC（2拠点のもう一方など）を、
今の `main` に移すための手順と、移るときに引っかかる点をまとめたもの。

- 対象のコミット: `c08a88a`（リファクタリング前、v1.2.1）→ `5b7572c`（段階8の docs。リファクタリングの最後）。その上に `8e3a16f`（v1.3.0、2026-10-15）が載っている
- `8e3a16f` はリファクタリング後のコードの上で作られている。そのコミットを作ったPCは、もう移行済み
- アプリの画面・メッセージ・集計結果は、下の「利用者から見て変わったこと」以外は変えていない（各段階で、移す前の動きをテストで固定してから移した）
- 経緯と段階ごとの内容は `docs/refactoring_plan.md`、設計判断は `CANONICAL_DESIGN_DECISIONS.md` §50（D-137〜D-142）と §45（v1.2.2 のビルド）、変更点の一覧は `CHANGELOG.md`「1.2.2」「1.3.0」

## 1. 移る前に確かめること（そのPCで）

1. **未コミット・未 push の変更が無いか**: `git status` と `git log origin/main..HEAD`
   - 変更があれば、先に別のブランチへコミットしておく（例: `git switch -c wip/before-refactoring` → `git commit -a`）。`main` を更新した後に、下の「4. 作業中の変更を持ってくる」で載せ替える
   - ui/ のファイルはすべて別のフォルダへ移った。古い場所のファイルを直接編集していると、`git pull` でぶつかる
2. **DB が新しいアプリで開けるか**（読み取り専用。DB には書き込まない）。`main` に更新した後で、次を実行する
   ```
   .venv\Scripts\python.exe inventory_app\scripts\check_db_versions.py
   .venv\Scripts\python.exe inventory_app\scripts\check_db_versions.py dist\db
   ```
   - 「開けない: 古い形式」と出た DB は、新しいアプリでは開けない（001〜013 のマイグレーションが当たっていない）。今の DB はテストデータで救済は不要という判断なので、新しく作り直す（必要なデータがあるなら、移る前に旧アプリで内容を書き出しておく）
   - 「開けない: アプリより新しい版」は、そのPCのアプリが古い。`main` に更新すれば開ける
   - このPC（`C:\work\inventory`）の `db/2026-07`・`db/TEST` は、2026-10-13 時点で「開ける（版の記録なし。開くと 13 を書く）」だった
3. **`dist/` に利用者のデータがあるか**（`dist\app_settings.json`・`dist\db\`）。.exe を作り直す前に、必ずリポジトリの外へ退避しておく（もう一方のPCでは、2026-10-09〜10-15 の間に `dist/db/` が無くなっていた。原因は記録されていない。CANONICAL §45・D-135）

## 2. main に更新する

```
git fetch origin
git switch main
git pull --ff-only
```

- 古いキャッシュを消す（元の `.py` が無くなった `.pyc` が残るため。消さなくても動くが、混乱の元になる）
  ```
  Get-ChildItem -Path inventory_app -Recurse -Directory -Include __pycache__,.pytest_cache | Remove-Item -Recurse -Force
  ```
- リポジトリ直下の `requirements.txt`（UTF-16 の古い pip freeze）は削除した。使うのは `inventory_app\requirements.txt`（版は `>=`）か `inventory_app\requirements.lock.txt`（版を固定）だけ

## 3. 開発環境をそろえる

`docs/setup.md` の手順と同じ。要点だけ書く。

1. Python 3.10 系（このPCは 3.10.11）で `.venv` を作る。既にある `.venv` をそのまま使ってもよいが、パッケージの版をこのPCとそろえるなら lock を入れる
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r inventory_app\requirements.lock.txt
   ```
   - opencv-python-headless は 5 系になっている（PDF読み取りは常に無効なので、今は影響しない。`docs/refactoring_plan.md`「未解決の宿題」）
   - `CANONICAL_DESIGN_DECISIONS.md` §7 の `C:\python310` は、このPCには無かった（どのPCの記録かは未確認、同じく「未解決の宿題」）。`run_app.bat` は常に `.venv` を使うので、システムの Python の場所には左右されない
2. テストを流す（全件成功すること）
   ```
   cd inventory_app
   ..\.venv\Scripts\python.exe -m pytest tests/
   ```
   - 2026-10-15 時点で 504 件。Tk の画面を作るテストがあり、画面の外（座標 +3000+3000）に一瞬ウィンドウが出る
   - テストは一時フォルダの DB だけを使い、`inventory_app\db\` には触れない（`tests/conftest.py`）
3. `run_app.bat` で起動し、メニューを一通り開く（`docs/refactoring_plan.md` の各段階の手動確認と同じ）
4. Claude Code を使うなら、リポジトリ直下の `CLAUDE.md` が毎回読まれる（作業ルール・壊しやすい箇所・docs の場所）

## 4. 作業中の変更を持ってくる

リファクタリング前のコードで作った変更（ブランチ）は、そのままでは `main` に載らないことが多い。

- **ファイルの移動だけで済むもの**（ui/ の画面の中身の変更など）: `git rebase main` か `git merge main` で、Git が移動先を追ってくれることが多い（すべて `git mv` で移したため）。ぶつかったら、下の対応表で移動先を探して手で載せる
- **関数が別のファイルへ移ったもの**: Git は追えない。下の「関数の移動先」を見て、移動先のファイルに同じ変更を手で入れる
- 載せ替えた後は、必ずテストを全件流す。新しく業務ロジックを書くなら、`CLAUDE.md`「層のルール」（ui → services → models の一方向、SQL は models/ だけ）に従う

### ui/ の移動先（段階8。旧位置に互換用のファイルは無い）

| 旧 | 新 |
|---|---|
| `ui/checkable_treeview.py`・`highlight_colors.py`・`window_utils.py`・`db_delete_helper.py` | `ui/common/` |
| `ui/kitting_plan_import.py`・`kitting_production_entry.py`・`plan_candidate_dialog.py` | `ui/kitting/` |
| `ui/production_import_staging_window.py`・`unmatched_production_window.py`（2026-10-25 に削除、D-186）・`daily_drawdown_window.py`・`lot_progress_window.py` | `ui/production/` |
| `ui/inventory_input_window.py`・`inventory_diff_window.py`・`theoretical_inventory_import_window.py` | `ui/inventory/` |
| `ui/ng_input_window.py`・`scrap_correction_window.py`・`wip_expansion_window.py`・`wip_scrap_correction_window.py` | `ui/ng_wip/` |
| `ui/daily_report_window.py`・`unified_report_window.py`・`parts_ng_report_window.py`・`product_ng_report_window.py`・`wip_parts_report_window.py`・`wip_product_report_window.py` | `ui/reports/` |
| `ui/board_structure_import_window.py`・`parts_attributes_import_window.py`・`production_side_master_window.py`・`worker_management_window.py`・`worker_registration_window.py` | `ui/master/` |
| `ui/operation_log_window.py`・`warnings_list_window.py`・`pdf_ocr_import_window.py` | `ui/system/` |
| `ui/main_window.py`・`login_window.py`・`loading_window.py` | `ui/` のまま |

import は `from ui.kitting.kitting_production_entry import KittingProductionEntryWindow` のように書く（`from ui.kitting_production_entry import …` は `ModuleNotFoundError` になる）。

### そのほかのファイルの移動

| 旧 | 新 |
|---|---|
| `inventory_app/check_*.py`・`delete_*.py`・`verify_*.py`・`dump_schema.py`・`list_plan_batches_detail.py` | `inventory_app/scripts/`（どれも import しただけで DB につなぐ）。2026-10-25 に10本とも削除した（D-186）ので、旧環境から持ってくる必要は無い |
| `inventory_app/test_kitting_import.py`（単発スクリプト） | `inventory_app/tests/test_kitting_import.py`（pytest のテスト） |
| `inventory_app/db/migrate_001.py`・`migration_002〜013_*.py` | `inventory_app/db/legacy/`（記録。実行しない） |
| `services/lot_status_history.py` | `services/lot_status_history_service.py`（旧ファイルは re-export だけを残している） |

### 関数の移動先（段階1〜7）

旧位置に re-export か別名が残っているものは、古い import のままでも動く。「旧位置に残っていない」ものは書き換えが要る。

| 何を | 旧 | 新 | 旧位置 |
|---|---|---|---|
| 日付の解釈（`_parse_plan_start_datetime`・`_parse_flexible_date`） | `ui/plan_candidate_dialog.py` | `services/date_parsing.py`（公開名 `parse_plan_start_datetime`・`parse_flexible_date`） | re-export・別名あり |
| 自動登録の判定（`evaluate_auto_register_eligibility`・`is_auto_confirmable` など） | `ui/production_import_staging_window.py` | `services/staging_eligibility.py` | re-export あり |
| 生産実績入力の計算（`_resolve_csv_report_date`・NG の保存値・入力済みの判定） | `ui/kitting_production_entry.py` | `services/production_entry_service.py`（公開名 `resolve_csv_report_date`・`compute_ng_save_qty` など） | re-export あり |
| 実績修正画面 `ActualCorrectionWindow` | `ui/kitting_production_entry.py` | `ui/kitting/actual_correction_window.py` | re-export あり |
| 登録の DB への書き込み | `KittingProductionEntryWindow._perform_registration()` の中 | `services/production_entry_service.register_actual_and_ng()`・`format_registration_message()` | `_perform_registration()` は残っている（中で呼ぶ） |
| ステージング画面から親画面への転記・更新 | 親の `_pending_csv_row_removal` などを直接操作 | 親の公開メソッド `load_staged_row()`・`refresh_after_external_registration()` | ― |
| NG 一覧・仕掛一覧の行の組み立て（`_fetch_ng_list_rows`・`_fetch_wip_list_rows`・列の定義） | `ui/ng_input_window.py`・`ui/wip_expansion_window.py` | `services/ng_list_service.py`・`services/wip_list_service.py`（公開名 `fetch_ng_list_rows`・`fetch_wip_list_rows`） | 画面に呼ぶだけの staticmethod あり |
| 製品名の正規化 `normalize_product_name` | `services/production_import_service.py` | `models/kitting_plan.py` | re-export あり |
| ロット履歴の記録 `record_lot_status_snapshot` | `models/lot_status_history.py` | `services/lot_status_service.py`（評価して記録）＋ `models.lot_status_history.insert_lot_status_snapshot()`（INSERT だけ） | **旧位置に残っていない**（models から services を import しないため）。`services.production_service` からは import できる |
| ロット完成数・ロット状態の評価（`calculate_lot_completion`・`evaluate_lot_status`・`list_incomplete_lots`・`check_lot_progress` など） | `services/production_service.py` | `services/lot_status_service.py` | re-export あり |
| 日報・月報の表示データ（`build_daily_report`・`build_monthly_report`） | `services/production_service.py` | `services/production_report_service.py` | re-export あり |
| services・ui にあった SQL | `backup_service`・`db_migration_carryover`・`master_merge_service`・`kitting_import_service`・`lot_status_history`・`ui/kitting_plan_import.py` | `models/db_common.py`・`models/kitting_plan.py`・`models/production.py` の関数 | 旧位置の関数は名前を変えずに残り、models を呼ぶ |
| 一覧の絞り込み・並べ替え・チェックボックスのポップアップ | 3画面にそれぞれ複製 | `ui/common/column_filter.py` | 画面のメソッドは名前を変えずに残り、共通部品を呼ぶ |
| 別スレッドの実行（LoadingWindow＋Thread＋Queue） | メイン画面の4か所 | `ui/common/async_task.run_in_background()` | メイン画面の `_poll_*_queue()` は `_on_*_finished()` に名前を変えた（**旧名は残っていない**） |
| DB の作成・切り替え・復元・削除の、画面に依存しない処理 | `ui/main_window.py` | `services/db_lifecycle_service.py` | ― |
| 起動時の DB のつなぎ直し | `MainWindow.__init__()` の中 | `MainWindow._connect_db_at_startup()`・`_report_startup_db_problems()` | ― |
| 他のモジュールの `_` 始まりの名前（`_sort_candidates_by_closeness`・`_format_qty`・`_open_csv_with_fallback`・`_row_to_values` など） | ― | 公開名を付けた（`sort_candidates_by_closeness`・`format_qty`・`open_csv_with_fallback`・`report_row_to_values` など） | 旧名は同じものを指す別名として残っている |

## 5. 利用者から見て変わったこと

リファクタリングの中で、挙動を変えたのは次だけ（どれも利用者が了承済み）。v1.3.0 で入った画面の変更（取り消しボタン・完成済みロットの確認など）は `CHANGELOG.md`「1.3.0」。

- **登録の書き込みの順序**（段階3a）: 実績CSVの保留行の削除が、実績・操作履歴・反対側の面・NG の書き込みの後になった（以前は操作履歴の直後）。画面の動きは同じ
- **一括登録（Shift+S・Shift+Q）中の親画面の更新**（段階3b）: 行ごとに更新していたのを、最後に1回にした。最後の状態は以前と同じ
- **DB を開くときの版の確認**（段階7b）: 起動・切り替え・バックアップからの取り込みのときに DB の版（`PRAGMA user_version`）を確かめる
  - 版の記録が無い DB は、形を確かめてから 13 を書く（DB に書き込みが起きる。旧アプリでもそのまま開ける。旧アプリは版を見ない）
  - 古い形式の DB・アプリより新しい版の DB は開かず、理由を出す。起動時なら「データベースを選択していない状態で起動しました」になる
  - マイグレーション（014 以降。2026-10-13 時点ではまだ無い）を当てる前に、同じフォルダへ自動でバックアップ（`inventory.db.bak_v<元>_to_v<先>_<日時>`）を取る。DB ごとに直近3世代だけ残す
  - 新しく作った DB には最新の版を書く

## 6. 2つのPCを行き来するときの注意

- 月別DB も master.db も、各PCのローカルだけに置いている（共有していない、2026-10-13 時点）
- 新しいアプリで開いた DB を旧アプリで開いても、2026-10-13 時点では問題ない（版を書くだけで、表の形は変えていない）。014 以降のマイグレーションを足した後は、そのマイグレーションを当てた DB を旧アプリで開くと、足した列を知らないまま動く。**両方のPCを同時に新しいアプリへ更新する**。最初のマイグレーション（014、ロット統合の表）は 2026-10-15 に足した（2.0.0 予定、D-145）
- 月別DB を共有するようになったら、アプリを更新するときは全PCを同時に更新する（`docs/db.md`「月別DB を共有する場合」）
- マイグレーションの追加手順は `docs/db.md`「新しいマイグレーションを追加する手順」。001〜013 のスクリプト（`db/legacy/`）は実行しない

## 7. .exe を作り直すとき

- `inventory_app/version.py` の `APP_VERSION`・`BUILD_DATE` を上げ、`CHANGELOG.md` に版の見出しを足してから、`.venv\Scripts\python.exe -m PyInstaller inventory_app.spec --noconfirm` でビルドする（リポジトリ直下で）。手順と確認の項目は CANONICAL §45・§49（D-120・D-135）
  - 2026-10-15 時点の版は 1.3.0（D-135）
- `inventory_app.spec` は `db.migrations` の中のモジュールを同梱する（`collect_submodules`）。.exe の中に入っているかは、PYZ を `PyInstaller.archive.readers` で開いて確かめる（§45.6）
- `C:\work\inventory` のPCの `dist/InventoryApp.exe` は、2026-10-13 に段階8の確認のためにビルドしたもの。表示は「v1.2.1（2026-10-09）」のままで、v1.3.0 の変更も入っていない。**配布しない**こと。確認のための起動で `dist\db\master.db`（空の作業者マスタ）ができている

## 8. 困ったとき

| 症状 | 原因と対処 |
|---|---|
| `ModuleNotFoundError: No module named 'ui.xxx'` | ui/ のファイルが移った。上の対応表の新しい場所から import する |
| `ImportError: cannot import name 'record_lot_status_snapshot' from 'models.lot_status_history'` | services へ移った。`from services.production_service import record_lot_status_snapshot`（または `services.lot_status_service`） |
| 起動すると「データベースを開けません」「古い形式のため開けません」 | その DB に 001〜013 が当たっていない。`check_db_versions.py` で確かめ、新しく作り直す |
| 「アプリを最新版に更新してください」 | DB が新しいアプリで更新されている。そのPCのアプリ（コードか .exe）を最新にする |
| `pip install -r requirements.txt` が `UnicodeDecodeError` | リポジトリ直下の古い requirements.txt を使っている（削除済み）。`inventory_app\requirements.txt` か `requirements.lock.txt` を使う |
| テストが `tcl_findLibrary` で落ちる・スキップされる | Tk のルートを何度も作り直すと起きることがある。テストは `tests/conftest.py` の `tk_root`（テスト全体で1つ）を使う |
| 単発スクリプトを実行したら DB が変わった | `scripts/` の check_*・delete_* などは import した時点で `config.DB_PATH` の DB に SQL を実行する。対象の DB を確かめてから実行する（`check_db_versions.py` と `check_code_unchanged.py` は書き込まない） |
