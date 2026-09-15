# CANONICAL_DESIGN_DECISIONS.md

## 1. 概要

**目的**：複数の調査ドキュメント(BOM_MIGRATION_NOTES.md・PRODUCTION_NG_ENHANCEMENTS_NOTES.md・UI_WORKFLOW_FIXES_NOTES.md)に分散している「確定した設計決定」だけを集約し、正典(canonical)として参照できるようにする。各決定の調査経緯・詳細な検証データは、リンク先の各ノートを参照のこと。本ファイルは決定事項の要約とポインタに徹し、詳細を重複して記載しない。

**対象読者**：`inventory_app`（部品在庫管理アプリ）のコードに触れる開発者。

**作成日**：2026-09-01（本ファイルはこの日付時点でリポジトリに存在しなかったため新規作成した）

---

## 2. 確定した設計決定サマリ

| # | 決定事項 | 詳細 |
|---|---|---|
| D-1 | BOM TSVのヘッダーにはA/B/Cの3表記ゆれが存在し、いずれも実質同一データとして吸収する（列ごとに独立して実在する方の列名を採用）。Dパターン（ヘッダー破損）はエラー扱いとする | BOM_MIGRATION_NOTES.md §9 |
| D-2 | 同一file_noは複数の実装ライン向けに同一BOMを重複記載しているため、`mounting_line`で絞り込まずに合算してはならない | BOM_MIGRATION_NOTES.md §10 |
| D-3 | 丁取り数（`parts_attributes.teitori`）は部品自身の96コードではなく、同一file_no・実装ライン内の「K行」（セットアップ部品種別='K'、基板自身を表す行）の96コードに対して登録されている。K行は生産面に関係なく常に生産面=1で記録される | BOM_MIGRATION_NOTES.md §11 |
| D-4 | `parts`（部品マスタ）・`final_products`（完成品マスタ）・`lots`テーブル、および「4.マスターデータ管理」「11.マスタインポート」画面は、現行のBOM基盤・キッティング計画・生産実績のいずれからも参照されない第一世代設計の名残であり、削除ではなく現状維持（参考情報として残す） | 本ファイル §5 |
| D-5 | `kitting_list_no`単体では計画を一意に識別できない（同一kitting_list_noが複数の異なるlot_noにまたがって存在するのが正常な業務パターン）。`(kitting_list_no, lot_no)`の組み合わせで初めて一意になる | PRODUCTION_NG_ENHANCEMENTS_NOTES.md §5 |
| D-6 | 実績・NG申告は「日付問わず1計画（kitting_list_no, lot_no）1レコード、常に上書き」で統一する（同じロットの別日生産は別のkitting_list_noとして立てる業務運用のため） | PRODUCTION_NG_ENHANCEMENTS_NOTES.md §6 |
| D-7 | 実績CSV取込には`import_production_csv()`（即時登録、後方互換のため維持）と`parse_production_csv_for_staging()`＋`ProductionImportStagingWindow`（ステージング方式、現在の標準フロー）の2系統が**意図的に併存**している。片方が巻き戻った結果ではない | UI_WORKFLOW_FIXES_NOTES.md §3 グループH（H-2） |
| D-8 | 面2が存在する場合、面1は一覧表示（基板別実績・日次実績履歴・日報・月報・仕掛数量抽出）から省略し面2のみ表示する統一ルール。ただし面1の実績が面2を上回る状態（`ActualCorrectionWindow`の片面修正等が原因）は**本来あってはならない不整合**であり、除外した上で別途警告する（黙って消さない） | UI_WORKFLOW_FIXES_NOTES.md §3 グループI（I-3〜I-5） |
| D-9 | `services/unprocessed_check_service.py`は、NG一覧・仕掛一覧の未処理判定ロジックを複製せず、`ui.ng_input_window.NgInputWindow`・`ui.wip_expansion_window.WipExpansionWindow`の該当staticmethodをそのままimportして再利用している。通常避けるべき「services層がui層に依存する」方向の依存関係だが、ロジック複製によるドリフト（`calculate_lot_completion()`と`list_incomplete_lots()`の食い違いが過去に発生した例と同種の問題）を避けるための**意図的な例外**であり、「壊れている」設計ではない | PRODUCTION_NG_ENHANCEMENTS_NOTES.md §11 |
| D-10 | 共有フォルダ上でのSQLite運用改善は、WALモードへの変更ではなく接続タイムアウト延長（30秒）＋`get_connection()`共通化のみを採用する。WALモードはSMB上で補助ファイル（-wal・-shm）へのロックが正しく機能せず、通常のジャーナルモードより状況を悪化させ得るため見送り、実運用で問題が出た場合に改めて検討する | UI_WORKFLOW_FIXES_NOTES.md グループU |
| D-11 | ロックファイル（`.lock`）が破損している（内容を読み取れない）場合、フェイルオープン（無条件に次の利用者が取得可能）ではなくフェイルクローズ（`LockFileCorruptedError`を送出し、ユーザーが明示的に確認した場合のみ`force=True`で上書き取得）を採用する。共有DBの整合性を扱うシステムでは、同時書き込み中の見逃しの方が実害が大きいため | UI_WORKFLOW_FIXES_NOTES.md グループV |
| D-12 | ロックファイルの誤削除（手動削除）への耐性向上（追記型履歴ログ等によるヒューリスティック検知）は見送り、現状維持（ファイルが存在しない＝ロック無しとして取得可能）とする。履歴ログ自体も同じ共有フォルダ上のファイルであり、書き込み競合・肥大化という別の問題を生むため | UI_WORKFLOW_FIXES_NOTES.md グループV |
| D-13 | コード変更後の検証で使う「全パッケージimportループチェック」は、作業ディレクトリ全体ではなく`ui`/`models`/`services`/`config`に限定したスコープ付きで実行する。作業ディレクトリ直下の単発メンテナンススクリプト（`check_*.py`・`delete_*.py`等）まで無差別にimportすると、それらのモジュールトップレベルの処理が実DBに対して実行されてしまうリスクがあるため | 本ファイル §5「検証スクリプト自体の安全性」 |
| D-14 | 開発・検証環境（`.venv`）と実際にアプリを起動する環境（システムPython等）は食い違いうる。`requirements.txt`へのパッケージ追加は、実際に`pip install`を実行した環境にしか反映されない。起動方法を明示的なバッチファイル（`run_app.bat`、常に`.venv`のpython.exeを指定）に統一することで、この環境依存を解消した | 本ファイル §7「実行環境・依存パッケージのドリフト問題」 |

---

## 3. 参照時の注意

本ファイルの内容と各ノートファイルの内容が食い違う場合は、**各ノートファイルに記載された調査日時・検証データを正**とし、本ファイルの要約を更新すること（本ファイルはあくまで索引であり、一次情報ではない）。

---

## 4. 未使用・レガシー資産の一覧（H）

以下は、現行のBOM基盤（TSV → `bom_master` → `parts_attributes`）・キッティング計画（`kitting_plan_items`）・生産実績（`production_daily`/`scrap_records`/`ng_declarations`）のいずれからも参照されていないことを確認済みの、第一世代設計の名残。**削除は行わず、現状維持と決定している**（削除した場合の影響範囲が完全には保証できないため、触らないことが最も安全という判断）。

| 対象 | 現状 | 参照元 |
|---|---|---|
| `parts`テーブル（部品マスタ） | 実データ1件（テストデータのみ） | `models/master.py`（UI: 4.マスターデータ管理「部品マスタ」タブ）、`services/master_import_service.py`（11.マスタインポート）、`db/migrate_001.py`（過去の1回限りの移行スクリプト内で読み取り専用フォールバックとして参照） |
| `final_products`テーブル（完成品マスタ） | 実データ1件（テストデータのみ） | `models/master.py`（UI: 4.マスターデータ管理「完成品マスタ」タブ）のみ |
| `lots`テーブル | 実データ1件（テストデータのみ） | `tests/setup_test_data.py`のみ（本体コードからの参照は無し） |
| 「4.マスターデータ管理」画面（`ui/master_management.py`） | 起動可能・手動でCRUD操作可能 | `parts`・`final_products`のCRUD専用。他画面からは参照されない |
| 「11.マスタインポート」画面（`ui/master_import_window.py`） | 起動可能 | `parts`テーブルへのCSV一括登録専用（`services/master_import_service.py::import_parts_csv()`）。新BOM基盤（`parts_attributes`、13.部品属性インポート）とは別物 |

`board_definitions`/`component_groups`/`component_bom`（旧BOM、空テーブル）も同様の位置づけ（BOM_MIGRATION_NOTES.md §7参照）。

---

## 5. 環境間の整合性チェック手順（重要・繰り返し発生している問題）

### 背景：calculate_lot_completion()の巻き戻りが2度発生している

本プロジェクトは2拠点並行開発を前提としており、`git`のマージ操作の際に、**片方の拠点で既に修正済みだった内容が、もう片方の拠点の古い状態で上書きされてしまう**という事故が、少なくとも2回発生している。

- **1回目**：`services/production_service.py::calculate_lot_completion()`の`file_actuals`キーが、3要素タプル`(setup_file_no, production_side, kitting_list_no)`から単一キー`setup_file_no`のみに巻き戻った状態が発見された（BOM_MIGRATION_NOTES.md §4・§5 #4に記録）。同時に`services/bom_service.py`・`services/bom_file_service.py`のBOM列名修正・`build_index()`再帰化・`resolve_file_no()`も同様に巻き戻っていた（同じマージ操作が原因と推測される）。
- **2回目**：1回目の修正・確認から時間を置いた別のタイミングで、`calculate_lot_completion()`が**再び**単一キー（`setup_file_no`のみ）に戻っている状態が発見され、再度3要素タプルキーに修正した。

**この巻き戻りは偶発的な一度きりの事故ではなく、同種のマージ作業のたびに再発するリスクがあるパターンとして扱うべきである。** 特に`calculate_lot_completion()`は「単一キーでも構文的には正しく動作してしまう」（例外を出さない、ただし計算結果が静かに誤る）ため、テストや起動確認だけでは検知できない点が危険性を高めている。

### 次回の環境立ち上げ・マージ時に必ず確認すべき項目

両拠点をマージ、または別環境（別PC・別ブランチ）からコードを持ち込んだ直後は、**必ず**以下を確認すること。

1. **`services/production_service.py::calculate_lot_completion()`のキーが3要素タプルか確認する**：
   ```python
   key = (item["setup_file_no"], item["production_side"], kitting_list_no)
   file_actuals[key] = ...
   ```
   のようになっているか（`file_actuals[file_no] = ...`のような単一キーに戻っていないか）を`grep`等で直接確認する。あわせて`ui/kitting_production_entry.py`の`lot_file_actuals`表示部分が、対応するタプル要素数（3要素）を正しく分解して表示しているかも確認する（`lot_surplus`表示はグループJ（UI_WORKFLOW_FIXES_NOTES.md）で廃止済みのため、2026-09-01時点で本項目の文言から削除した）。
2. **`services/bom_service.py`・`services/bom_file_service.py`のBOM列名定数**（`COL_SIDE="生産面"`・`COL_PART_NO="96コード"`・`COL_R_FLAG="減数種別"`）が、仮置きの値（`"先行面・後行面"`・`"部品番号"`・`"Rフラグ"`）に戻っていないか確認する。
3. **`BOMFileIndex.build_index()`がサブフォルダを再帰的に走査しているか**（`resolve_file_no()`・`problems`機構が存在するか）を確認する（BOM_MIGRATION_NOTES.md §2・§3参照）。
4. **`models/kitting_plan.py::list_plan_items_by_lot()`に`is_active=1`フィルタが含まれているか**を確認する。
5. **`bom_master`テーブルに`item_type`列が存在するか確認する**（`PRAGMA table_info(bom_master)`で直接確認する。基板消費枚数機能（BOM_MIGRATION_NOTES.md §13）のキャッシュ列で、`db/migration_013`で追加される。無ければ`migration_013`が未適用）。
6. 上記いずれかが巻き戻っていた場合は、**該当するノートファイル（BOM_MIGRATION_NOTES.md）の該当セクションを参照し、そこに記載された修正内容をそのまま再適用する**（調査をやり直す必要はない。過去に確定済みの内容であるため）。ただし、そもそも一度も適用されていなかった場合（巻き戻りではない）もあり得る点に注意（2026-09-01の実DB適用時、§6参照）。

**重要な留意点（`CREATE TABLE IF NOT EXISTS`パターンの運用上の注意）**：`models/bom_master.py::init_bom_master_table()`のように`CREATE TABLE IF NOT EXISTS`でテーブルを定義しているモジュールは、テーブルが既に存在する環境ではアプリ起動時に何度呼ばれても列定義が更新されない。そのため、`bom_master`のようなテーブルに新しいマイグレーション（`migration_011`・`migration_013`等）が追加された場合、**既存DBには自動適用されず、該当するマイグレーションスクリプトを明示的に実行しない限り古いスキーマのまま残り続ける**。上記チェック項目5（`item_type`列の存在確認）は、この一例にすぎない。同様に`CREATE TABLE IF NOT EXISTS`で定義されている他のテーブルについても、新しいマイグレーションを追加した際は既存DB（特に実DB）への適用を忘れずに行うこと（§6の実施記録、特に2026-09-03付の再発記録も参照）。

### 推奨する運用

- マージ作業を行う際は、上記5項目を機械的にチェックするチェックリストとして扱い、目視確認だけでなく可能であれば簡単なスクリプト（`grep`でのパターンマッチ等）で自動検知することを検討する。
- 本ファイル（CANONICAL_DESIGN_DECISIONS.md）とBOM_MIGRATION_NOTES.md等の各ノートは、マージのたびに「反映済み/未反映」の判定を更新し、巻き戻りが再発していないかをその都度確認する運用とする。

### 検証スクリプト自体の安全性（重要・importループチェックの範囲限定）

**発見の経緯（2026-09-14）**：コード変更後の検証手順として慣行にしていた「全パッケージimportループチェック」（`pkgutil.walk_packages()`で作業ディレクトリ配下の全`.py`ファイルを列挙し、`importlib.import_module()`でimportできるか確認する手法）が、実際には作業ディレクトリ直下に置かれた単発メンテナンス・調査用スクリプト（`check_failed_batch_references.py`・`check_kitting_plan_schema.py`・`delete_failed_batches.py`・`delete_test_batches.py`・`dump_schema.py`等）まで無差別にimportしてしまうことが判明した。これらのスクリプトはモジュールトップレベルに実処理（DB接続・SELECT・場合によってはDELETE）を書いた一回限りの調査用コードであり、importされるだけでその処理がそのまま実行される。実際に一度この手法を実行した際、`delete_failed_batches.py`・`delete_test_batches.py`を含む複数のスクリプトが実DB（`inventory_app/db/inventory.db`）に対して実行され、削除対象0件だったため実害は無かったが、**`delete_*`という名前のスクリプトが存在する以上、対象件数が0件だったのは結果論であり、たまたま実害が無かっただけというリスクだった**。

**確定した対応（D-13）**：以降、importループチェックは対象を`ui`/`models`/`services`/`config`の各パッケージ・モジュールに限定したスコープ付きで実行する（`pkgutil.walk_packages(['ui'], 'ui.')`のように、パッケージごとに明示的に`path`と`prefix`を指定する。作業ディレクトリ直下を無条件に`walk_packages([''], '')`のように列挙しない）。アプリ本体（`ui`/`models`/`services`/`config`）はいずれもモジュールトップレベルに実処理を書かない設計で統一されているため、この範囲に限定してもimportエラーの検知能力は損なわれない。

**今後の注意点**：作業ディレクトリ直下に一時的な調査・修正用スクリプトを置く場合、モジュールトップレベルに実処理（特にDB書き込み系）を書くと、この種の検証手法から不用意に実行されるリスクが常につきまとう。使い終わったスクリプトは早めに削除するか、実処理を`if __name__ == "__main__":`ブロック内に限定することが望ましい。

---

## 6. 整合性チェック実施記録

| 確認日 | 確認内容 | 結果 |
|---|---|---|
| 2026-09-01 | 本ファイル§5のチェック手順（1〜4）に加え、D-1〜D-6由来の関連項目（BOM列名エイリアス、calculate_lot_completion()の3要素タプルキー、replace_daily_result_for_todayの残存有無、save_ng_declaration()のreport_date条件残存有無、bom_masterのUNIQUE制約へのmounting_line包含、K行基準の丁取り数参照、実績CSV取込のステージング化）を計7項目＋is_activeフィルタで確認 | **巻き戻り0件**（全項目一致）。D-7として追記した2系統併存も、巻き戻りではなく意図的な設計と確認済み |
| 2026-09-01（同日、後続） | `db/migration_013`（`bom_master`へのitem_type列追加）を実DBへ適用する前に、`PRAGMA table_info(bom_master)`で現状を確認 | **巻き戻りではなく「そもそも一度も適用されていなかった」項目を発見**：実DBの`bom_master`には`item_type`列だけでなく、本来`migration_011`で追加されているはずの`mounting_line`列も存在しなかった（＝`migration_011`が実DBに未適用のまま放置されていた）。`migration_013`が「テーブルを作り直す」設計だったため、結果的に`migration_011`分（mounting_line）も`migration_013`分（item_type）も同時に反映され、1回の適用で最新スキーマに追いついた（行数は実行前後とも0件でデータ喪失なし）。**教訓**：この種の「巻き戻り検知」チェックリストは、過去に一度は正しく適用されてから巻き戻る場合だけでなく、そもそも一度も適用されていなかった場合の検知にも有効である。また「テーブル作り直し型」のマイグレーションは、複数世代分のスキーマ変更を1回の適用で吸収できるという利点も確認できた |
| 2026-09-03 | NG入力画面でのBOM展開時に`sqlite3.OperationalError: no such column: item_type`が発生。実DB（`inventory_app/db/inventory.db`）の`bom_master`に対し`PRAGMA table_info`で現状を確認 | **同一事象が再発**：実DBの`bom_master`は`item_type`列（および`mounting_line`列）を欠いた状態のまま235行のキャッシュを保持しており、`migration_013`が未適用の状態だった。直上の2026-09-01付エントリでは「migration_013適用済み、行数は実行前後とも0件」と記録されていたが、今回確認したのは235行のキャッシュを保持する別の状態であり、**その確認以降に別のタイミングで再度ドリフトしたか、あるいは2拠点並行開発のもう一方の環境（別のDBインスタンス）に対する確認だった可能性が高い**（どちらであるかは特定できていない）。`migration_013`を実DBに再実行し、235件の既存キャッシュを破棄して`mounting_line`・`item_type`両列込みで再作成した（再計算可能なキャッシュのため実害なし）。修正後、file_no=723・side=2（K行丁取り数バグの実例）でNG展開を実行し、正常動作（`item_type`内訳：part 109件・board 1件、board行はceil(2÷5)=1で正しく計算、`bom_master`への書き込み・再読み込みも成功）を確認。`python -m pytest tests/`にも影響無し（1 passed）。**教訓**：「一度チェックして問題なしと確認しても、その後の作業や別環境での操作により再度ドリフトしうる」。特に`CREATE TABLE IF NOT EXISTS`型のテーブルに対する整合性チェックは、確認した時点・確認した環境（どのDBインスタンスか）を記録に明記しておかないと、後から見て「巻き戻ったのか、そもそも別環境の話だったのか」の切り分けが困難になる（§5の運用上の注意も参照） |

整合性チェック手順（§5）が実際に機能し、巻き戻りを検知（1回目は「無かったこと」、2回目は「そもそも未適用だったこと」、3回目は「一度解消したはずの状態が再発したこと」）できた実績として記録する。

---

## 7. 実行環境・依存パッケージのドリフト問題（重要な運用上の教訓）

### 本章の位置づけ（§5との違い）

§5「環境間の整合性チェック手順」は「**同じ環境内でコードが巻き戻る**」問題（2拠点並行開発でのマージ事故）を扱っている。本章が扱うのはこれとは性質が異なる問題である：**コード自体は同じでも、それを実行するPython環境（インタプリタ・インストール済み依存パッケージ）が、開発・検証時に想定していた環境と、実際にアプリが動く環境とで食い違いうる**、という観点。マージ・巻き戻りの話ではなく、「そもそもどの環境で動かしているか」という前提のズレが原因のトラブルであり、区別して扱う必要がある。

### 発見の経緯（2026-09-15）

PDF読み取り画面を開いた際、以前解消したはずの`ModuleNotFoundError: No module named 'cv2'`が再発した。調査の結果、**実際にアプリを起動している環境（システムPython、`C:\python310`）と、開発・検証作業を行っていた環境（`.venv`仮想環境）が別だった**ことが判明した。`.venv`側に`pip install`していたPDF OCR関連パッケージ（`opencv-python-headless`等）は、`.venv`とは独立した`C:\python310`側には一切反映されておらず、実際にアプリが起動する環境からは見えていなかった。

### 根本原因

- リポジトリ内に明示的な起動スクリプト（.bat等）が一切存在せず、「アプリをどう起動するか」が利用者の環境依存（ショートカットの中身、コマンドの打ち方等）になっていた。
- リポジトリ直下に「用途不明の古い`launch.json`・`settings.json`」（`.vscode/`配下ではない、VSCode自体は読み込まない死んだ設定）が存在し、混乱の一因になりうる状態だった（git履歴調査によりInitial commit以来一度も更新されておらず、他からの参照も無いことを確認した上で削除。`UI_WORKFLOW_FIXES_NOTES.md`§4参照。**削除はまだコミットされていない**）。

### 対応

1. **システムPython側にも依存パッケージを個別にインストール**：`C:\python310\python.exe -m pip install -r requirements.txt`。
2. **明示的な起動用バッチファイル`run_app.bat`を新規作成**：`.venv\Scripts\python.exe`を明示指定して`main.py`を起動する（`UI_WORKFLOW_FIXES_NOTES.md`§4参照）。今後はこのバッチファイル経由での起動に統一することで、起動方法が環境依存になっていたという根本原因そのものを解消した。
3. **リポジトリ直下の死んだ設定ファイルを削除**：`git rm launch.json settings.json`（`.vscode/`配下ではない2ファイルのみ。`.vscode/launch.json`・`.vscode/settings.json`は対象外で、内容変更なく残存していることを確認済み）。

### 今後の教訓（重要・必ず思い出せるように明記）

> **教訓1：`requirements.txt`にライブラリを追加しても、それだけでは使えるようにならない。**
> `requirements.txt`はあくまで「必要なパッケージの一覧」であり、実際に`pip install`を実行した環境にしか反映されない。**開発・検証で使っていた環境（例：`.venv`）と、実際にアプリが起動する環境（例：システムPython）が別である場合、両方に対して個別に`pip install`する必要がある。** 「requirements.txtに書いたから大丈夫」と思い込まないこと。新しいライブラリを追加した際は、必ず「実際にアプリを起動する環境」でも動作確認すること。

> **教訓2：日本語（非ASCII文字）とWindowsのコードページ（文字コード）の組み合わせは落とし穴になりやすい。**
> 本プロジェクトで実際に発生した2つの実例：
> - システムのコンソールコードページ（932、Shift-JIS）とUTF-8保存の`requirements.txt`の不一致により、古いバージョンの`pip`（23.0.1）で`UnicodeDecodeError`が発生した（`.venv`側の新しい`pip`（26.2.1）では問題なかった）。`PYTHONUTF8=1`環境変数を付けて実行することで回避できる。
> - `run_app.bat`に日本語のコメント・メッセージをUTF-8で書いたところ、`cmd.exe`（コードページ932）がバイト列を誤って解釈し、**コマンド解析自体が壊れる**（存在しないコマンドとして次々エラーになる）現象が発生した。**バッチファイル（.bat）は非ASCII文字を含めるとコンソールの既定コードページに左右されやすいため、確実性を優先する場合はASCIIのみで書くのが安全。**
>
> どちらも「ファイルの文字コード（UTF-8）」と「それを読み込む側が想定する文字コード（Windows既定のANSI/OEMコードページ）」の不一致が原因であり、日本語を含むファイルを古いツール・`cmd.exe`等で扱う際は常に注意すること。

### 実運用中のロックファイルに関する注意深い切り分け（参考）

検証中、本番用DBのロックファイルの`acquired_at`が更新されていることに気づいたが、`LoginWindow`はログインボタンを押すまでDBロックを取得しない設計であり、検証作業ではログイン操作を行っていないことを根拠に、検証作業自体の影響ではなく、この共有マシン上で実際の利用者が同時期に使用していたことによるものと判断した（実運用中の共有環境で検証を行う際の切り分けの一例として記録する）。
