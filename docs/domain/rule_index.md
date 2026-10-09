# 業務ルール一覧（コメント・docstring から抽出）

2026-10-09 時点（HEAD c08a88a）のコメントと docstring から見つけたルールの一覧と出典。
**本文の移動（ここへ集約し、コードには参照を1行だけ残す作業）はまだしていない。**
パスは `inventory_app/` からの相対パス。D番号は CANONICAL_DESIGN_DECISIONS.md / CHANGELOG.md の番号。
抽出に使ったのは、行数上位10ファイルと DB関連の services/models のコメント・docstring。ほかのファイルは網羅していない。
生産実績入力（`ui/kitting_production_entry.py`）のルールと設計メモは、コメントから `docs/domain/production_entry.md` に移した（2026-10-09）。
コメント軽量化でほかに移した先: 実績CSV取込のステージング一覧 → `docs/domain/production_import_staging.md`、実績の登録とロットの完成数・引落 → `docs/domain/lot_completion.md`

## 計画・ロットの特定

| # | ルール | 出典 |
|---|---|---|
| R1 | kitting_list_no は lot_no をまたいで重複する（実データで478件）。計画や実績を特定するときは、必ず lot_no も条件に入れる | `services/production_service.py` `register_daily_result()`、`services/db_migration_carryover.py` `_fetch_production_daily_for_lot()` |
| R2 | 同じ (kitting_list_no, lot_no) でアクティブ（is_active=1）な版は最大1件。新しい版を作るときに旧版を is_active=0 にする | `models/kitting_plan.py` `list_active_plan_items_by_kitting_no()` |
| R3 | 面2の計画があるロット・ファイルNoでは、面1の計画を一覧から除く（D-8） | `models/kitting_plan.py` `classify_side1_only_plan()`、`list_active_plan_items()` |
| R4 | 実績CSVの自動取込で使う計画の検索は、完了済みを除く既定のままで呼ぶ（そうしないと一意に特定できなくなる） | `models/kitting_plan.py` `list_active_plan_items()` |
| R5 | 製品名の照合は完全一致（正規化したうえで）のみ。あいまい一致は実装しないと決まっている | `models/kitting_plan.py` L1143 付近のコメント |

## 生産面（面1・面2）

| # | ルール | 出典 |
|---|---|---|
| R6 | 面1だけの計画は、生産面マスターで「片面の製品／面2待ち／生産面マスター未登録」(a/b/c) に分ける。b と c は確認ダイアログを出すが、登録は禁止しない | `ui/kitting_production_entry.py` L1980 付近、`ui/production_import_staging_window.py` L1365 付近・`_confirm_side1_only_if_applicable()` |
| R7 | 後行面（面2）があるかどうかはセットアップファイルNo単位で判定する（実装ラインは問わない）（D-104、D-107） | `models/kitting_plan.py` L716・L739 付近 |
| R8 | 両面の計画があるロットで実績を修正・削除するときは、反対側の面の実績も連動させる。確定の前に必ず確認ダイアログを出す（D-112） | `ui/kitting_production_entry.py` `ActualCorrectionWindow.on_update()` |
| R9 | 面1/面2 の不整合は、ロットNo・ファイルNoごとの合計で比べる（D-111） | `services/production_service.py` L755 付近のコメント |

## 実績・完成数

| # | ルール | 出典 |
|---|---|---|
| R10 | ロット完成数は (setup_file_no, production_side) ごとに実績を**加算**する（同じファイルNo・同じ面に複数のバッチがある：222件） | `services/production_service.py` `_compute_lot_completion()` |
| R11 | ファイルNoによって order_qty が食い違うロットがある（301件中3件）。食い違ったときの扱いは docstring に書かれている | `services/production_service.py` `_compute_lot_completion()` |
| R12 | 構成基板数のチェックとロット進捗の判定は、`_evaluate_lot_status()` に一本化してある（日報・月報、仕掛数量の抽出、ロット進捗チェック） | `services/production_service.py` `_evaluate_lot_status()` |
| R13 | report_date は必ず `YYYY-MM-DD` に正規化して保存する（DBでは文字列のまま範囲比較するため） | `ui/kitting_production_entry.py` `_resolve_csv_report_date()` |
| R14 | NG を入力せずに実績だけを登録してもよい | `ui/kitting_production_entry.py` `_validate_ng_inputs()` |

## 実績CSV取込

| # | ルール | 出典 |
|---|---|---|
| R15 | 自動登録できるかどうかの判定と、その優先順位（候補なし → 製品名の不一致 → …） | `ui/production_import_staging_window.py` `evaluate_auto_register_eligibility()` |
| R16 | Shift+S と Shift+Q の違いは、日付の許容差（1日と4日）だけ（D-101） | `ui/production_import_staging_window.py` L141 付近のコメント |
| R17 | 「登録済み」は数量と日付の両方が一致したときだけ（D-83） | `services/production_import_service.py` `is_already_registered()`（CHANGELOG 1.0.2） |
| R18 | 同じCSVの中の行は、内容が重複していても別々の保留行として残す（D-78） | CHANGELOG 1.0.0。コード側の出典は未確認（`services/production_import_service.py` L191 付近に D-78 の言及あり） |
| R19 | 候補はDBに保存せず、表示のたびに照合し直す | `ui/production_import_staging_window.py` のモジュール docstring・`_load_staged_rows_from_db()` |
| R20 | 実績がすでにある計画へ登録するときは、経路を問わず確認ダイアログを出す | `ui/production_import_staging_window.py` L1356 付近 |
| R21 | 「登録済み」の候補と、数量が一致しない候補は、右クリックですぐ登録することを禁止する（D-115） | `ui/production_import_staging_window.py` `_on_candidate_right_click()`、`_is_candidate_already_registered()` |

## DB・マスタの運用

| # | ルール | 出典 |
|---|---|---|
| R22 | 既定DB（未選択）は、アプリから一切読み書きしない | `ui/main_window.py` L915 付近、`config.is_default_db()` |
| R23 | 在庫値を出力済みのDBは警告するだけで、入力は妨げない | `ui/main_window.py` L249 付近・`_refresh_inventory_diff_export_warning()` |
| R24 | 新規作成で引き継ぐもの: 未完了ロットの計画と実績。未着手の計画は50日以内のものだけ。NG履歴・仕掛スナップショットは引き継がない | `services/db_migration_carryover.py` のモジュール docstring・`_filter_plan_items_for_migration()` |
| R25 | マスタを他のPCから取り込むときは、足りないキーだけを追加し、既存の行は上書きしない | `services/master_merge_service.py` のモジュール docstring |
| R26 | 基板丁数マスターのCSV取込は、既定では追加と上書きだけ。全件置き換えはチェックボックスを入れたときだけ（D-108） | `ui/parts_attributes_import_window.py` L372 付近 |
| R27 | 作業者IDは、全角/半角・大文字/小文字の違いだけなら同じIDとみなす（保存する値は変換しない）（D-88） | `models/workers.py` `normalize_worker_id()` |
| R28 | 生産面マスターを画面で編集したときは、変わった面だけを upsert/delete する（全件の差分同期はしない） | `ui/production_side_master_window.py` のモジュール docstring |
