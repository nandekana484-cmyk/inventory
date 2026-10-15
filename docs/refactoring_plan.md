# リファクタリング計画

## 方針

## 段階表

### 段階7: DB を開くときに形を確かめ、マイグレーションを適用する仕組み

- 時期: 本番運用を始める前、または次のマイグレーション（014）を追加する前
- 背景: 001〜013 のマイグレーションのスクリプトは既定DBにしか適用されず、切替・復元のときに形の確認も補正もない（`docs/db.md`「既知の制約」）
- 段階7a（2026-10-13、済み）: DB の作成・切り替え・復元・削除のうち画面に依存しない処理を `services/db_lifecycle_service.py` に移した
- 段階7b（2026-10-13、済み）: 以下の設計で実装した（2026-10-13 に利用者が了承）。手順は `docs/db.md`「マイグレーション」

#### 前提（利用者が決めたこと、2026-10-13）

- 月別DB は各PCのローカルだけに置く。master.db も各PCのローカルにあり、共有していない
- 今ある DB はすべてテストデータで、古い DB の救済は不要
- 自動バックアップは DB ごとに直近3世代を残し、それより古い `bak_v*` は、適用が成功した後に削除する
- 前月からの引き継ぎの途中では確認しない（取り決めとして `docs/db.md` に書く）

#### 1. 版の記録

- `PRAGMA user_version` に記録する（DB ファイルの中にあり、バックアップ・コピーでもそのまま付いていく）
- 001〜013 を適用済みの今の形を **13**（基準）とする。新しいマイグレーションは 014 から
- 新しく作る DB は、`init_database_at()` の最後に最新の番号を書く
- master.db は別の番号を持たせる（同じ仕組みで、置き場所だけ分ける。今回は月別DB だけを実装する）

#### 2. 確認と適用の流れ

- 入口は `db_lifecycle_service.ensure_db_schema(db_path)` の1つ。ロックを取った後、`config.DB_PATH` を切り替える前に呼ぶ
  - 起動時のつなぎ直し（ロックの取得の後）、切り替え、バックアップからの取り込み（コピーした先の DB。原本は変えない）
  - 新規作成は、作るときに最新の番号を書くので確認だけ。前月からの引き継ぎの途中では確認しない（作る DB は最新、元の DB は今の DB で確認済み）
- 中の流れ
  1. `user_version` を読む。最新より大きければ、アプリが古いので開かない
  2. 0 なら基準の確認（必須の列を `PRAGMA table_info` で確かめる）。そろっていれば 13 を書く。そろっていなければ「古い形式の DB です。作り直してください」として開かない（補正しない）
  3. 最新より小さければ、適用の前に自動でバックアップを取る（同じフォルダの `inventory.db.bak_v<元>_to_v<先>_<日時>`、SQLite のバックアップ API）
  4. 番号の順に1つずつ、`BEGIN IMMEDIATE` のトランザクションで適用し、同じトランザクションの中で `user_version` を書き換える
  5. すべて成功したら、`bak_v*` を直近3世代だけ残して古いものを消す

#### 3. 変更の種類ごとの扱い

- マイグレーションは `db/migrations/m0NN_<内容>.py`（`VERSION` と `upgrade(con)`）。アプリ本体から import するので .exe にも入る
- 列の追加: `PRAGMA table_info` で無いときだけ `ALTER TABLE … ADD COLUMN`（2回実行しても安全）。同じコミットで、その表の `CREATE TABLE`（`schema.sql` か `init_*_table()`）も最新の形に直す
- まだ表が無い DB: 多くの表は初めて使ったときに `init_*_table()` で作られる。表が無ければ何もしない（後で作られるときに最新の形になる）
- 表の作り直し（UNIQUE 制約の変更など）: SQLite の手順に従う
  1. `PRAGMA foreign_keys = OFF`（トランザクションの外で。トランザクションの中では効かない）
  2. トランザクションの中で: 新しい表を作る → 中身を写す → 古い表を消す → 名前を変える → インデックス・トリガーを作り直す → `PRAGMA foreign_key_check` で問題が無いことを確かめる → `user_version` を書く
  3. `PRAGMA foreign_keys` を元の値に戻す（トランザクションの外で）
  - マイグレーションのモジュールに `REBUILDS_TABLES = True` と書くと、1 と 3 は実行する側が行う
- ずれを防ぐテスト: 新しく作った DB と、1つ前の版にマイグレーションを当てた DB の形（列・インデックス）が一致することを確かめる

#### 4. 失敗したときの画面の動き

- 失敗したマイグレーションのトランザクションは巻き戻る（それより前の番号は適用済みのまま、その番号が記録に残る）。バックアップは残す
- どの場面でも、切り替え先のロックを放し、今の DB とロックはそのままにする
  - 切り替え: 今の DB を使い続ける
  - 起動時: 既定DB（未選択）で起動し、UI を作り終えてから知らせる
  - バックアップからの取り込み: 取り込んだフォルダを消す（ロックが取れなかったときと同じ）
- アプリより新しい版の DB: 開かない。メッセージに「アプリを最新版に更新してください」を入れる
- 表の作り直しは時間がかかることがある。必要になったら `run_in_background()` で読み込み中の表示を出す（014 時点では列の追加だけを想定し、同期で実行する）

#### 5. 001〜013 の扱い

- 13 を基準にするので、自動で当てる対象にしない。`db/legacy/` に `git mv` し（中身は変えない）、「13 までの記録。実行しない」と README に書く
- 既定DB にだけ手で当てる今の手順は廃止する

#### 見積もりとリスク（設計時）

- 手間: 約2〜2.5日（014 そのものは含まない）
- リスク: 起動時の順序（ロックと既定DB への戻し）、前月からの引き継ぎ中の `config.DB_PATH`、表を初めて使ったときに作る仕組みとのずれ、バックアップの容量（3世代で抑える）

## 進捗

段階1〜8はすべて完了した（2026-10-13）。各段階の後に、利用者が画面で手動確認した（段階1〜7は問題なし。段階8は全メニューを一通り開く確認を依頼した）。
別のPCを移す手順と旧→新の対応表は `docs/migrate_to_refactored.md`、設計判断は CANONICAL_DESIGN_DECISIONS.md §50（D-137〜D-142）と §45（段階7b・8と v1.2.2 のビルド）、変更点の一覧は CHANGELOG.md「1.2.2」。

| 段階 | 日付 | コミット（最初〜最後） | 内容 | テスト |
|---|---|---|---|---|
| 準備 | 2026-10-09 | `5b06e31`〜`1e52b8c` | CLAUDE.md・docs/ の作成、コメントの軽量化（業務ルールを docs/domain/ へ） | 1件 |
| 1 | 2026-10-09 | `3ce2b87`〜`7024939` | 画面に依存しない計算・判定を services へ（date_parsing・staging_eligibility・production_entry_service） | 102件 |
| （環境） | 2026-10-09 | `56f4347`〜`74cc5d5` | requirements.txt のコメントを英数字だけに、requirements.lock.txt、docs/setup.md | ― |
| 2 | 2026-10-13 | `f5167b8`〜`d578452` | ActualCorrectionWindow の分離、日付の解釈の共通化、未使用の import の削除 | 189件 |
| 3a | 2026-10-13 | `4d0a8d2`〜`698fc4c` | 登録の書き込みを register_actual_and_ng() へ（保留行の削除を最後に） | 216件 |
| 3b | 2026-10-13 | `64b7736`〜`562259a` | ステージング画面を親画面の公開メソッドと register_actual_and_ng() に切り替え | 239件 |
| 4 | 2026-10-13 | `42af2e6`〜`a978b17` | 層の違反の解消（services → ui、models → services、models/ の外の SQL）、services の同名ファイルの改名 | 289件 |
| 5 | 2026-10-13 | `c71ed28`〜`a2cdb97` | production_service.py の分割（lot_status_service・production_report_service）、「既知の違反」の残り2件 | 318件 |
| 6 | 2026-10-13 | `190068e`〜`80adfeb` | `_` 始まりの名前の公開名、一覧の絞り込み・並べ替えとポップアップの共通化、run_in_background() | 394件 |
| 7a | 2026-10-13 | `fd2dc34`〜`0832e1a` | DB の作成・切り替え・復元・削除の、画面に依存しない処理を db_lifecycle_service へ | 422件 |
| 7b | 2026-10-13 | `137f994`〜`96dfd52` | DB の版（user_version、基準 13）の確認とマイグレーションの仕組み、001〜013 を db/legacy/ へ | 460件 |
| 8 | 2026-10-13 | `83c7848`〜`5b7572c` | フォルダの整理（ui/ のサブフォルダ化、scripts/・tests/ への移動、古い requirements.txt の削除）、.exe のビルド確認 | 461件 |
| （v1.2.2・v1.3.0） | 2026-10-15 | `8e3a16f` | もう一方のPCで、リファクタリング後のコードの上に機能の修正（D-120〜D-136）。リファクタリングの段階ではない | 501件 |
| 引き継ぎ | 2026-10-15 | （このブランチ） | 開発記録（CHANGELOG「1.2.2」・CANONICAL §50）、他のPCへ移る手順（docs/migrate_to_refactored.md）、check_db_versions.py | 504件 |

## 未解決の宿題

### D-9x の仮番号

コメントの「D-9x」は、CANONICAL_DESIGN_DECISIONS.md の D番号が決まる前に書かれた仮番号。内容から D-92〜D-94（生産面マスター）のどれかと思われるが、どれに当たるかは未確認。正しい番号が分かったら置き換える。

- 前回確認した3か所（`ui/kitting/kitting_production_entry.py`）
  - `_fetch_plan_list_rows()` の隠し要素1のコメント（生産面マスターの分類）
  - `create_widgets()` の計画一覧のタグ設定のコメント（b・c の背景色）
  - `_build_registration_preview()` の分類のコメント（b・c なら確認ダイアログで理由を示す）
- ほかにも同じ仮番号がある（2026-10-09 時点、計34か所）: `models/kitting_plan.py`（8）、`models/production_side_master.py`（7）、`ui/master/production_side_master_window.py`（6）、`ui/kitting/kitting_plan_import.py`（3）、`ui/production/production_import_staging_window.py`（3）、`ui/main_window.py`（2）、`services/master_merge_service.py`（1）、`services/production_import_service.py`（1）

### CANONICAL §7 の「C:\python310」と、この PC の状態の食い違い

CANONICAL_DESIGN_DECISIONS.md §7（実行環境・依存パッケージのドリフト問題）と run_app.bat のコメントは、システムの Python を `C:\python310` として書いている（§7 の対応1は `C:\python310\python.exe -m pip install -r requirements.txt`）。CANONICAL は書き換えていない。

- 2026-10-09 時点のこの PC には `C:\python310` が無い。PATH 上の Python は `C:\Users\nande\AppData\Local\Programs\Python\Python310\python.exe`（3.10.11）
- 同じ日の時点で、リポジトリ直下の `.venv` も無く、run_app.bat で起動できなかった。`.venv` を作り直して `inventory_app/requirements.txt` を入れ、起動できることを確認した（`.venv` も上の Python 3.10.11 から作った）
- §7 の記録が別の PC（2拠点のもう一方）のものなのか、この PC で Python を入れ直したのかは未確認。どちらかを確かめて、§7 に「どの PC の話か」を書き足すか、記述を直す

### opencv-python-headless が 5 系になった

2026-10-09 に `.venv` を作り直したとき、`inventory_app\requirements.txt`（`>=4.9.0`）から opencv-python-headless 5.0.0.93 が入った（`requirements.lock.txt` に記録）。それまでの `.venv` に入っていた版は記録が無く、未確認。

- 使っているのは PDF 読み取りの OCR の前処理（`services/pdf_ocr_service.py` の `preprocess_image_for_ocr()`・`_deskew()`。bilateralFilter・CLAHE・adaptiveThreshold・minAreaRect など）
- メニューの「PDF読み取り」は常に無効（D-85）なので、画面から動作を確かめられない。有効にするときに、OCR の前処理が以前と同じ結果になるかを確認する（`docs/domain/pdf_ocr.md` の調整値は 2026-09-25 前後に決めたもので、そのときの版は未確認）

### リポジトリ直下の requirements.txt（段階8で削除した）

リポジトリ直下の `requirements.txt` は古い pip freeze（UTF-16、版を `==` で固定）で、`inventory_app\requirements.txt` と紛らわしい。

- PDF 読み取りのパッケージ（pdfplumber・pytesseract・pdf2image・opencv-python-headless）が入っていない
- run_app.bat と `docs/setup.md` が使うのは `inventory_app\requirements.txt`。版の固定は `inventory_app\requirements.lock.txt` で行う
- 段階8（2026-10-13）で削除した

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

無し（2026-10-13、段階6の後）。ui → services → models の一方向以外の import、models/ の外の SQL 実行、他のモジュールの `_` 始まりの名前の import は、どれも `ast` による機械的な検査で 0 件（re-export の行は数えていない）。

ui → models の直接呼び出し（ui/ のほぼ全画面）は「既存コードは触るときだけ直す」の対象で、この一覧には数えていない（`docs/architecture.md` の表を参照）。

### 解消したもの（段階5・6、2026-10-13）

他のモジュールの `_` 始まりの名前に公開名を付け、呼び出し側を新しい名前にした（旧名は同じものを指す別名として残している）。

| 段階 | 定義しているところ | 公開名 |
|---|---|---|
| 5 | `ui/kitting/plan_candidate_dialog.py` | `sort_candidates_by_closeness`・`is_large_qty_diff`・`is_large_date_diff`・`format_qty`・`format_planned_qty_cell`・`format_side`・`CANDIDATE_COLS`・`CANDIDATE_HEADERS`・`CANDIDATE_RIGHT_ALIGNED` |
| 5 | `services/ng_list_service.py`・`services/wip_list_service.py` | `fetch_ng_list_rows`・`fetch_wip_list_rows` |
| 6 | `services/date_parsing.py` | `parse_plan_start_datetime`・`parse_flexible_date` |
| 6 | `services/production_entry_service.py` | `resolve_csv_report_date` |
| 6 | `services/csv_parsing_common.py` | `open_csv_with_fallback` |
| 6 | `ui/reports/daily_report_window.py` | `report_row_to_values`（同じファイルで `row_to_values` が引数名に使われているので避けた） |

### 解消したもの（段階4、2026-10-13）

一方向以外の import と models/ の外の SQL 実行は、`ast` による機械的な検査で 0 件。

| 種類 | ファイル | どう解消したか |
|---|---|---|
| services → ui の import | `services/unprocessed_check_service.py` | NG 一覧・仕掛一覧の行の組み立てを `services/ng_list_service.py`・`services/wip_list_service.py` へ移した。画面には呼ぶだけの staticmethod を残した |
| models → services の import | `models/kitting_plan.py`・`models/production_import_staging.py` | `normalize_product_name()` を `models/kitting_plan.py` へ移した（`services/production_import_service.py` は re-export） |
| models → services の import | `models/lot_status_history.py` | `record_lot_status_snapshot()` を、評価して記録する本体（`services/production_service.py`）と INSERT（`models.lot_status_history.insert_lot_status_snapshot()`）に分けた。呼び出し側3ファイルの import を書き換えた |
| SQL が models/ の外にある | `services/backup_service.py`・`db_migration_carryover.py`・`master_merge_service.py`・`kitting_import_service.py`・`lot_status_history_service.py`・`ui/kitting/kitting_plan_import.py` | SQL 文を変えずに `models/db_common.py`（DB ファイル全体の検査・汎用の読み取り）・`models/kitting_plan.py`・`models/production.py` へ移した。旧位置の関数は名前を変えずに残し、models を呼ぶだけにした |
| 層をまたいだ同じファイル名 | `services/lot_status_history.py` | `services/lot_status_history_service.py` に改名した。旧ファイルは re-export だけ |
| 他の画面の `_` 属性・メソッド | `ui/production/production_import_staging_window.py` → 親画面 | 段階3bで公開メソッドに置き換えた |
| 業務ロジックが ui/ にある | `ui/production/production_import_staging_window.py` | 段階1で自動登録の判定を `services/staging_eligibility.py` へ移した（旧位置は re-export） |
