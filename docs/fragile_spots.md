# 壊しやすい箇所（根拠）

CLAUDE.md「壊しやすい箇所」の根拠。2026-10-09 時点（HEAD c08a88a）の記録から作成。
コミットメッセージは日付のみ（例 `100917`）のため、「繰り返し修正」は
ファイルの変更回数（全41コミット）と CHANGELOG.md / CANONICAL_DESIGN_DECISIONS.md の記述で判断した。

## 1. 実績CSV取込の照合・自動登録判定

- 場所
  - `inventory_app/ui/production_import_staging_window.py`: `evaluate_auto_register_eligibility()`, `is_auto_confirmable()`, `_load_staged_rows_from_db()`, `_execute_bulk_register()`, `_register_candidate_immediately()`
  - `inventory_app/services/production_import_service.py`: `is_already_registered()`
- 根拠
  - CHANGELOG 1.0.0〜1.0.2 で、同じ領域を立て続けに修正している（D-78 → D-81/D-82 → D-83）。どれも「実績の行が消える」「登録済みと誤って判定する」不具合
  - Shift+Q は D-97 で追加し、同じ日に取り消して（D-100）、条件を変えて再追加した（D-101）
  - D-101 より前は、一覧の状態表示と Shift+S 本体が別々の基準で判定していた（CHANGELOG 1.2.0）
  - production_import_staging_window.py: 変更14コミット、2416行（行数2位）
  - production_import_service.py: 変更9コミット
- 注意: 判定の優先順位は `evaluate_auto_register_eligibility()` の docstring にある。Shift+S と Shift+Q の差は日付の許容日数の定数だけ（同ファイルの定数 `AUTO_REGISTER_MAX_DAYS_SHIFT_S` のコメント）

## 2. ロット完成数の集計キー

- 場所: `inventory_app/services/production_service.py`: `_compute_lot_completion()`, `calculate_lot_completion()`, `_evaluate_lot_status()`
- 根拠
  - CANONICAL_DESIGN_DECISIONS.md §5: 2拠点のマージで `calculate_lot_completion()` のキーが単一キーに2回巻き戻った。単一キーでも例外は出ず、計算結果だけが静かに誤る
  - `_compute_lot_completion()` docstring: 同じ file_no・同じ面にアクティブなバッチが複数ある（実データで222件）。代入にすると実績が消えるため、必ず加算にする
  - CHANGELOG 1.2.1（D-111）: 面1/面2 不整合の判定が誤検出していた（実データで104件すべて）
  - production_service.py: 変更15コミット、1754行（行数3位）
- 正しい形: 2要素 `(setup_file_no, production_side)` で、代入ではなく加算（利用者確認済み、2026-10-09）。3要素（`kitting_list_no` を含む）は2026-09-04（7d2a870）より前の形で、同じファイルNo・同じ面を複数バッチで生産したロットの完成数が少なく出る。CANONICAL §5・BOM_MIGRATION_NOTES.md §4 は2026-10-09にこの形へ修正した

## 3. グローバルな `config.DB_PATH` の切り替え

- 場所
  - `inventory_app/config.py`: `set_db_path()`
  - `inventory_app/services/db_migration_carryover.py`: `carry_over_incomplete_lots()`
  - `inventory_app/ui/main_window.py`: `_try_switch_db_path()`, `on_create_database()`, `_update_current_db_label()` のコメント
- 根拠
  - CANONICAL §10（D-19）: 開いている画面は DB パスを保持せず、操作のたびに `config.DB_PATH` を参照する。画面を開いたまま DB を切り替えると、操作対象が別の DB に変わる。**未解消**
  - `carry_over_incomplete_lots()` は処理中に `config.DB_PATH` を旧DB→新DBへ一時的に切り替える。戻るときは必ず new_db_path にしておく、という契約がある（main_window.py `__init__()` の `_menu_widgets` と `_poll_create_db_queue()` のコメント）
  - 既定DB（未選択）は読み書きしない方針。ただし `init_*_table()`（CREATE TABLE IF NOT EXISTS）を呼ぶと、`sqlite3.connect()` で DB ファイルが作られてしまう（main_window.py `_update_current_db_label()` のコメント）。既定DBへの意図しない書き込みは、CHANGELOG 1.0.0（D-71/D-73）でも修正している
  - CANONICAL §22.6（D-75）: 既知の制約。前月引き継ぎで新規作成するとき、引き継ぎが終わる前に旧DBのロックが解放される

## 4. Tkinter の pack 順序（Treeview とスクロールバー、下部ボタン）

- 場所
  - `inventory_app/ui/kitting_production_entry.py`: `create_widgets()` の計画一覧のコメント
  - `inventory_app/ui/production_import_staging_window.py`: `_create_staging_widgets()`, `_create_candidate_widgets()`
  - `inventory_app/ui/ng_input_window.py`: `_create_ng_list_widgets()`
  - `inventory_app/ui/wip_expansion_window.py`: `_create_wip_list_widgets()`
- 根拠
  - 上の4ファイルのコメントに、同じ不具合パターンが繰り返し記録されている。`expand=True` の Treeview を先に pack すると、スクロールバーやボタン行の領域が残らない。Tk の pack は呼んだ順に領域を確保するため、下部のウィジェットを先に `side=BOTTOM` で pack しておく必要がある
  - `_create_candidate_widgets()` の docstring に「右ペインで発見した既知のバグパターンと同じ構造がここにも存在していた」とある

## 5. スキーマのドリフト（マイグレーションの未適用と巻き戻り）

- 場所
  - `inventory_app/models/bom_master.py`: `init_bom_master_table()`
  - `inventory_app/db/migration_011_*.py`, `inventory_app/db/migration_013_*.py`
  - `production_daily.report_date` の NOT NULL 制約（`models/production.py`: `replace_daily_result()` がこの制約を前提にしている）
- 根拠
  - CANONICAL §6: `bom_master` の列不足（migration_011/013 が未適用）を3回検出した（2026-09-01 に2回、2026-09-03 に再発）。2回目は `no such column: item_type` の実行時エラーとして表に出た
  - CANONICAL §5 の注意: `CREATE TABLE IF NOT EXISTS` で定義されたテーブルは、アプリを起動しても列定義が更新されない
  - マイグレーションはアプリから自動では実行されず、どこまで適用したかを記録する仕組みもない。スクリプトは既定DBにしか適用されない（`docs/db.md`「既知の制約」）。対策は `docs/refactoring_plan.md` の段階7

## 選外（候補として確認したもの）

- `ui/main_window.py`: 変更回数が最多（24コミット）。ただし、同じ不具合を繰り返し直した記録は見つからなかった
- `ui/kitting_production_entry.py`: 変更21コミット、2600行（行数1位）。上の 1・3・4 と重なる
