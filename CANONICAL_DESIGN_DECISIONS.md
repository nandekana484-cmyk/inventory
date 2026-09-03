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

---

## 6. 整合性チェック実施記録

| 確認日 | 確認内容 | 結果 |
|---|---|---|
| 2026-09-01 | 本ファイル§5のチェック手順（1〜4）に加え、D-1〜D-6由来の関連項目（BOM列名エイリアス、calculate_lot_completion()の3要素タプルキー、replace_daily_result_for_todayの残存有無、save_ng_declaration()のreport_date条件残存有無、bom_masterのUNIQUE制約へのmounting_line包含、K行基準の丁取り数参照、実績CSV取込のステージング化）を計7項目＋is_activeフィルタで確認 | **巻き戻り0件**（全項目一致）。D-7として追記した2系統併存も、巻き戻りではなく意図的な設計と確認済み |
| 2026-09-01（同日、後続） | `db/migration_013`（`bom_master`へのitem_type列追加）を実DBへ適用する前に、`PRAGMA table_info(bom_master)`で現状を確認 | **巻き戻りではなく「そもそも一度も適用されていなかった」項目を発見**：実DBの`bom_master`には`item_type`列だけでなく、本来`migration_011`で追加されているはずの`mounting_line`列も存在しなかった（＝`migration_011`が実DBに未適用のまま放置されていた）。`migration_013`が「テーブルを作り直す」設計だったため、結果的に`migration_011`分（mounting_line）も`migration_013`分（item_type）も同時に反映され、1回の適用で最新スキーマに追いついた（行数は実行前後とも0件でデータ喪失なし）。**教訓**：この種の「巻き戻り検知」チェックリストは、過去に一度は正しく適用されてから巻き戻る場合だけでなく、そもそも一度も適用されていなかった場合の検知にも有効である。また「テーブル作り直し型」のマイグレーションは、複数世代分のスキーマ変更を1回の適用で吸収できるという利点も確認できた |
| 2026-09-03 | NG入力画面でのBOM展開時に`sqlite3.OperationalError: no such column: item_type`が発生。実DB（`inventory_app/db/inventory.db`）の`bom_master`に対し`PRAGMA table_info`で現状を確認 | **同一事象が再発**：実DBの`bom_master`は`item_type`列（および`mounting_line`列）を欠いた状態のまま235行のキャッシュを保持しており、`migration_013`が未適用の状態だった。直上の2026-09-01付エントリでは「migration_013適用済み、行数は実行前後とも0件」と記録されていたが、今回確認したのは235行のキャッシュを保持する別の状態であり、**その確認以降に別のタイミングで再度ドリフトしたか、あるいは2拠点並行開発のもう一方の環境（別のDBインスタンス）に対する確認だった可能性が高い**（どちらであるかは特定できていない）。`migration_013`を実DBに再実行し、235件の既存キャッシュを破棄して`mounting_line`・`item_type`両列込みで再作成した（再計算可能なキャッシュのため実害なし）。修正後、file_no=723・side=2（K行丁取り数バグの実例）でNG展開を実行し、正常動作（`item_type`内訳：part 109件・board 1件、board行はceil(2÷5)=1で正しく計算、`bom_master`への書き込み・再読み込みも成功）を確認。`python -m pytest tests/`にも影響無し（1 passed）。**教訓**：「一度チェックして問題なしと確認しても、その後の作業や別環境での操作により再度ドリフトしうる」。特に`CREATE TABLE IF NOT EXISTS`型のテーブルに対する整合性チェックは、確認した時点・確認した環境（どのDBインスタンスか）を記録に明記しておかないと、後から見て「巻き戻ったのか、そもそも別環境の話だったのか」の切り分けが困難になる（§5の運用上の注意も参照） |

整合性チェック手順（§5）が実際に機能し、巻き戻りを検知（1回目は「無かったこと」、2回目は「そもそも未適用だったこと」、3回目は「一度解消したはずの状態が再発したこと」）できた実績として記録する。
