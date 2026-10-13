# DB の切り替え・バックアップ・マイグレーション

2026-10-09 時点（HEAD c08a88a）のコードから読み取れた範囲だけを書く。
パスは `inventory_app/` からの相対パス。
段階7a（2026-10-13）で、DB の作成・切り替え・復元・削除のうち画面に依存しない処理を `services/db_lifecycle_service.py` に移した。ロックの取得・解放と、壊れたロックの確認ダイアログは `ui/main_window.py` に残っている。
コードから読み取れなかった点は「未確認」と書いた。

## DB の種類と置き場所

| DB | パス | 備考 |
|---|---|---|
| 月次DB | `<APP_DATA_DIR>/db/<フォルダ名>/inventory.db` | `config.DB_PATH`。切り替えの対象 |
| 既定DB | `<APP_DATA_DIR>/db/inventory.db` | 一度もDBを選んでいないときの `DB_PATH`。「未選択」として扱い、アプリは読み書きしない（`config.is_default_db()`） |
| マスタDB | `<APP_DATA_DIR>/db/master.db` | `config.MASTER_DB_PATH`。固定パスで、切り替えの対象外。board_structure_master・parts_attributes・workers・parts・final_products・production_side_master・db_lifecycle_log が入る |

- `APP_DATA_DIR`: 開発環境では `inventory_app/`、.exe 実行時は .exe が置かれたフォルダ（`config.py`）
- フォルダ名 `db` は予約済みで、ユーザーは作れない（`config.is_default_db()` の docstring）
- 接続は `models/db_common.py` の `get_connection()`（月次DB）と `get_master_connection()`（master.db）。timeout は30秒

## 切り替え

1. 切り替えの入口は `config.set_db_path(path)` だけ。`config.DB_PATH` に直接代入しない
2. `set_db_path()` は `services/app_settings_service.save_last_db_path()` を呼び、`<APP_DATA_DIR>/app_settings.json` に保存する
3. 次に起動したとき、`ui/main_window.MainWindow.__init__()` が `db_lifecycle_service.restore_last_db_path()`（中で `load_last_db_path()`）で前回のDBにつなぎ直す。ファイルが無ければ既定DB のまま起動し、知らせる
4. 画面からの切り替えは `MainWindow._try_switch_db_path()` で行う。新しいDBのロックを取る → 今のロックを放す → `db_lifecycle_service.set_current_db_path()`（= `set_db_path()`）
   - ロックは `services/db_lock_service.py`。`<DBファイル名>.lock`（JSON）をDBと同じフォルダに置く。30分更新がなければ自動で解除される。ファイルが壊れているときは自動で上書きせず、ユーザーに確認する（`_acquire_lock_with_corruption_handling()`）
   - 既定DB（未選択）の間は、ロックファイルに一切触れない（CHANGELOG 1.0.0、D-59）
5. 注意: 開いている画面は DB パスを持っておらず、操作のたびに `config.DB_PATH` を読む。画面を開いたまま切り替えると、その画面の操作対象も変わる（CANONICAL §10、D-19、未解消）

## 新規作成（`MainWindow.on_create_database()`）

1. フォルダ名の確認（空・予約語・既存）は `db_lifecycle_service.check_new_db_folder()`
2. `db_lifecycle_service.create_db_file()`（= `db/init_db.init_database_at(new_db_path)`）で、`db/schema.sql` のテーブルと production_daily の追加列を作る。その後、画面がロックを取る
3. 引き継ぎなしの場合: `db_lifecycle_service.activate_new_db()`（`set_db_path(new_db_path)` → `models.kitting_plan.init_kitting_plan_tables()`）→ `record_db_created()`
4. 引き継ぎありの場合: `services/db_migration_carryover.carry_over_incomplete_lots(old, new)`（別スレッド。終わった後の記録は `MainWindow._on_create_db_finished()`）
   - 処理中に `config.DB_PATH` を旧DB→新DBへ一時的に切り替える。成功しても失敗しても、戻るときは必ず新DBになっている
   - 実行中はメインメニューの操作をまとめて無効にする（`_set_menu_enabled()`）
   - 引き継ぐもの: 未完了ロットの kitting_plan_items（アクティブな行）と production_daily。まだ着手していない計画は、実装開始予定日が50日以内のものだけ
   - 引き継がないもの: scrap_records・ng_declarations・wip_board_snapshot・wip_scrap_records
   - 既知の制約: 引き継ぎが終わる前に旧DBのロックが解放される（CANONICAL §22.6、D-75）

## 削除（`MainWindow.on_delete_local_database()` → `ui/common/db_delete_helper.py` → `services/db_delete_service.py`・`services/db_lifecycle_service.delete_database()`）

1. `check_delete_safety()`: 今つながっているDBなら削除を拒否する。有効なロックがあれば警告する
2. `db_lifecycle_service.delete_database()` → `delete_database_files()`: DB本体・`.lock`・`.lock.tmp` を消す。フォルダは残す。操作履歴は今の DB に記録する
3. 作成・削除・バックアップ呼び出しの履歴は master.db の db_lifecycle_log に残る（`models/db_lifecycle_log.py`、D-87）

## バックアップ（`MainWindow.on_backup_databases()` → `services/backup_service.backup_databases(dest)`）

- 月次DB と master.db を、SQLite のオンラインバックアップ API（`sqlite3.Connection.backup()`）で書き出す
- ファイル名は `inventory_backup_<YYYYMMDD_HHMMSS>.db` と `master_backup_<YYYYMMDD_HHMMSS>.db`。名前が重複したら `_1`, `_2` を付ける

## バックアップの呼び出し（`MainWindow.on_restore_from_backup()`）

1. `validate_monthly_db_backup()`: 読み取り専用で開き、`integrity_check` と月次DB専用のテーブルがあるかを確認する
2. `db_lifecycle_service.restore_backup_to_new_folder()` → `restore_backup_as_new_local_db()`: 新しいフォルダ `<APP_DATA_DIR>/db/<フォルダ名>/inventory.db` にコピーする。元のファイルは変えない
3. コピーに失敗したら、作りかけのフォルダを消してから例外を送出する（`restore_backup_to_new_folder()`）。切り替えのロックが取れなければ `discard_restored_folder()` でフォルダを消す。どちらも今のDBとロックは変えない
4. 切り替えた後に `db_lifecycle_service.finish_restore()` で `init_kitting_plan_tables()` を呼び、古いバックアップでもテーブルがそろうようにする。操作履歴と master.db の履歴もここで記録する
5. master.db は復元しない。マスタは `on_merge_master_from_backup()` → `services/master_merge_service.merge_master_from_backup()` で、足りない行だけを追加する（既存の行は上書きしない）

## 既知の制約（2026-10-09 確認）

- 現在の DB とバックアップはすべてテストデータで、古い DB を修復する必要はない（利用者の判断）
- （段階7bで解消）マイグレーションのスクリプトは既定DB（`config.DB_PATH` の初期値）にしか適用されなかった。今は DB を開くときに版を確かめて適用する（下の「マイグレーション」）
- （段階7bで解消。版の記録が無い DB は基準の形かを確かめ、足りなければ開かない）切替・バックアップからの復元のときに、不足している列を自動で補う仕組みはなかった。復元時の `init_kitting_plan_tables()` は、表やインデックスが無ければ作るだけで、既存の表に列を足さない
- 新規作成（前月引き継ぎを含む）では、表は最新の形で作られる。例外は migration_005 のインデックス2つ（`idx_kitting_plan_items_batch_id`、`idx_kitting_plan_items_kitting_version`）で、新しいDBには作られない（速度への影響だけで、計測はしていない）
- 列が足りない古いDBを開いたときに起きること（例）
  - NG実績・NG申告に `lot_no`・`is_unplanned` がない（008・010 が未適用）→ 実績レポートの集計、NG入力画面の一覧、在庫差異レポートを開く前の未処理チェックが失敗する
  - BOMキャッシュに `mounting_line`・`item_type` がない（011・013 が未適用）→ NG入力画面・仕掛部品展開画面のBOM展開が失敗する（2026-09-03 に実際に起きた。CANONICAL §6）
- 対策は `docs/refactoring_plan.md` の段階7（段階7bで実装した）

## マイグレーション（段階7b、2026-10-13〜）

### 版と、開くときの確認

- 月次DB の版は `PRAGMA user_version` に記録する。001〜013 を適用済みの形を **13**（基準、`db/migrations/__init__.py` の `BASELINE_VERSION`）とする。このアプリが扱える最新の版は `LATEST_VERSION`
- 新しく作る DB（新規作成。引き継ぎあり・なしとも）は、`db/init_db.init_database_at()` の最後に最新の版を書く
- DB を開くとき（起動時のつなぎ直し・切り替え・バックアップからの取り込み）は、ロックを取った後・`config.DB_PATH` を切り替える前に `db_lifecycle_service.ensure_db_schema()`（`db/schema_version.ensure_schema()`）を呼ぶ
  1. 版がアプリの最新より新しい → 開かない（「アプリを最新版に更新してください」）
  2. 版が 0（記録が無い） → 基準の形かを `PRAGMA table_info` で確かめる（`BASELINE_REQUIRED_COLUMNS`）。そろっていれば 13 を書く。そろっていなければ「古い形式」として開かない（補正はしない。今ある DB はテストデータで、救済は不要という判断）
  3. 版が最新より小さい → 同じフォルダに自動でバックアップ（`inventory.db.bak_v<元>_to_v<先>_<YYYYMMDD_HHMMSS>`）を取ってから、番号の順に1つずつトランザクションで適用する
  4. すべて成功したら、自動バックアップ（`bak_v*`）を DB ごとに直近3世代だけ残し、古いものを消す。手で取ったバックアップ（`bak_migration010_*` など）は消さない
- 取り込みでは、確かめて適用するのはコピーした先の DB だけで、バックアップの原本は変えない
- **前月からの引き継ぎの途中では確認しない（取り決め）**。作る DB は `init_database_at()` で最新の形・版になり、引き継ぎ元は今の DB（開いたときに確認済み）。`carry_over_incomplete_lots()` の中で `config.DB_PATH` を切り替えるときに、確認や適用を足さないこと

### 開けなかったとき・適用に失敗したとき

- 失敗したマイグレーションのトランザクションは巻き戻る。それより前の番号は適用済みのまま、その版が記録に残る。自動バックアップは残す
- どの場面でも、開こうとした DB のロックを放し、今の DB とロックは変えない
  - 切り替え: 切り替えずに、今の DB のまま続ける
  - 起動時: 既定DB（未選択）で起動し、UI を作り終えてから理由を出す（次の起動も未選択から）
  - バックアップからの取り込み: 取り込んだフォルダを消す（その中の自動バックアップも消える。原本は変わらない）
- メッセージ: 古い形式 → 「新しいデータベースを作成し直してください」と足りない列の一覧。新しすぎる版 → 「アプリを最新版に更新してください」。適用の失敗 → 失敗した番号・適用済みの版・更新前のバックアップの場所・詳細

### 月別DB を共有する場合（今はしていない）

- 2026-10-13 時点で、月別DB も master.db も各PCのローカルだけに置き、共有していない
- もし月別DB を共有する（共有フォルダに置くなど）場合は、**アプリを更新するときは全PCを同時に更新する。古いアプリでは新しい形の DB を開けない**（新しい版を書いた DB は、古いアプリでは「アプリを最新版に更新してください」と出て開けない）
- master.db の版の仕組みはまだ無い（月別DB だけ）。master.db の表を変えるときは、同じ仕組みを master.db 用に作ってから行う

### 新しいマイグレーションを追加する手順

1. `db/migrations/m0NN_<内容>.py` を作る。番号は今の `LATEST_VERSION` ＋1。中身は `VERSION = NN` と `upgrade(con)`
   - `upgrade(con)` は2回実行しても安全に書く（列は `PRAGMA table_info` で無いときだけ足す）
   - 表がまだ無い DB では何もしない（多くの表は初めて使ったときに `init_*_table()` で作られ、そのとき最新の形になる）
   - `con` はトランザクションの中で渡される。`COMMIT`・`ROLLBACK`・`PRAGMA foreign_keys` は書かない（実行する側が行う）
   - 見本: `tests/schema_migrations/m014_add_test_dummy_column.py`（テスト専用。本番には入れない）
2. 同じコミットで、その表の `CREATE TABLE`（`db/schema.sql` か `models/*.py` の `init_*_table()`）を最新の形に直す
3. `db/migrations/__init__.py` の `LATEST_VERSION` を上げる（番号が抜けていたり食い違っていたりすると、`load_migrations()` が ValueError を出す）
4. テストを足す: 1つ前の版の DB に当てて目的の形になること、新しく作った DB と形（列・インデックス）が一致すること（`tests/test_schema_version.py` の `test_new_db_and_migrated_db_have_the_same_shape` と同じ方法）、2回当てても変わらないこと
5. CHANGELOG.md とこのファイルに、番号と内容を書く

### 表を作り直す変更（列の削除・名前の変更・制約の変更）

SQLite の `ALTER TABLE` でできない変更は、表を作り直す。マイグレーションのモジュールに `REBUILDS_TABLES = True` と書く。

1. `PRAGMA foreign_keys = OFF` — トランザクションの外で行う（トランザクションの中では切り替わらない）。`REBUILDS_TABLES = True` なら実行する側が行う
2. トランザクションの中で（`upgrade(con)` が書く部分）:
   1. 新しい形の表を別の名前で作る（`CREATE TABLE <表>_new (...)`）
   2. 中身を写す（`INSERT INTO <表>_new (...) SELECT ... FROM <表>`）
   3. 古い表を消す（`DROP TABLE <表>`）
   4. 名前を変える（`ALTER TABLE <表>_new RENAME TO <表>`）
   5. インデックス・トリガーを作り直す（消した表のものは一緒に消えている）
3. `PRAGMA foreign_key_check` で問題が無いことを確かめる（実行する側が行い、問題があれば巻き戻す）→ 版を書いてコミット
4. `PRAGMA foreign_keys` を元の値に戻す — トランザクションの外で（実行する側が行う）

- 作り直しは時間がかかることがある。DB を開く処理は同期で実行しているので、大きな表を作り直すマイグレーションを足すときは、`ui/common/async_task.run_in_background()` で読み込み中の表示を出すことを検討する

### 001〜013（db/legacy/）

- 13 を基準にしたので、自動では当てない。`db/legacy/` に記録として残している（実行しない）。既定DB にだけ手で当てていた以前の手順は廃止した
- 以前の記録: どれも単独で実行するスクリプトで、import した時点の `config.DB_PATH`（既定DB）につないでいた。適用したかどうかの記録は無かった

## Git 管理について

- `.gitignore` は `*.db` と `*.db.lock` を除外している。一方、`db/inventory.db.bak_*`（6ファイル）は追跡されている
