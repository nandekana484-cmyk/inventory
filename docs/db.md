# DB の切り替え・バックアップ・マイグレーション

2026-10-09 時点（HEAD c08a88a）のコードから読み取れた範囲だけを書く。
パスは `inventory_app/` からの相対パス。
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
3. 次に起動したとき、`ui/main_window.MainWindow.__init__()` が `load_last_db_path()` で前回のDBにつなぎ直す
4. 画面からの切り替えは `MainWindow._try_switch_db_path()` で行う。新しいDBのロックを取る → 今のロックを放す → `set_db_path()`
   - ロックは `services/db_lock_service.py`。`<DBファイル名>.lock`（JSON）をDBと同じフォルダに置く。30分更新がなければ自動で解除される。ファイルが壊れているときは自動で上書きせず、ユーザーに確認する（`_acquire_lock_with_corruption_handling()`）
   - 既定DB（未選択）の間は、ロックファイルに一切触れない（CHANGELOG 1.0.0、D-59）
5. 注意: 開いている画面は DB パスを持っておらず、操作のたびに `config.DB_PATH` を読む。画面を開いたまま切り替えると、その画面の操作対象も変わる（CANONICAL §10、D-19、未解消）

## 新規作成（`MainWindow.on_create_database()`）

1. `db/init_db.init_database_at(new_db_path)` で、`db/schema.sql` のテーブルと production_daily の追加列を作る
2. 引き継ぎなしの場合: `set_db_path(new_db_path)` → `models.kitting_plan.init_kitting_plan_tables()`
3. 引き継ぎありの場合: `services/db_migration_carryover.carry_over_incomplete_lots(old, new)`
   - 処理中に `config.DB_PATH` を旧DB→新DBへ一時的に切り替える。成功しても失敗しても、戻るときは必ず新DBになっている
   - 実行中はメインメニューの操作をまとめて無効にする（`_set_menu_enabled()`）
   - 引き継ぐもの: 未完了ロットの kitting_plan_items（アクティブな行）と production_daily。まだ着手していない計画は、実装開始予定日が50日以内のものだけ
   - 引き継がないもの: scrap_records・ng_declarations・wip_board_snapshot・wip_scrap_records
   - 既知の制約: 引き継ぎが終わる前に旧DBのロックが解放される（CANONICAL §22.6、D-75）

## 削除（`MainWindow.on_delete_local_database()` → `ui/db_delete_helper.py` → `services/db_delete_service.py`）

1. `check_delete_safety()`: 今つながっているDBなら削除を拒否する。有効なロックがあれば警告する
2. `delete_database_files()`: DB本体・`.lock`・`.lock.tmp` を消す。フォルダは残す
3. 作成・削除・バックアップ呼び出しの履歴は master.db の db_lifecycle_log に残る（`models/db_lifecycle_log.py`、D-87）

## バックアップ（`MainWindow.on_backup_databases()` → `services/backup_service.backup_databases(dest)`）

- 月次DB と master.db を、SQLite のオンラインバックアップ API（`sqlite3.Connection.backup()`）で書き出す
- ファイル名は `inventory_backup_<YYYYMMDD_HHMMSS>.db` と `master_backup_<YYYYMMDD_HHMMSS>.db`。名前が重複したら `_1`, `_2` を付ける

## バックアップの呼び出し（`MainWindow.on_restore_from_backup()`）

1. `validate_monthly_db_backup()`: 読み取り専用で開き、`integrity_check` と月次DB専用のテーブルがあるかを確認する
2. `restore_backup_as_new_local_db()`: 新しいフォルダ `<APP_DATA_DIR>/db/<フォルダ名>/inventory.db` にコピーする。元のファイルは変えない
3. 失敗したら、呼び出し側が作りかけのフォルダを消す。今のDBとロックは変えない
4. 切り替えた後に `init_kitting_plan_tables()` を呼び、古いバックアップでもテーブルがそろうようにする（main_window.py `on_restore_from_backup()`）
5. master.db は復元しない。マスタは `on_merge_master_from_backup()` → `services/master_merge_service.merge_master_from_backup()` で、足りない行だけを追加する（既存の行は上書きしない）

## 既知の制約（2026-10-09 確認）

- 現在の DB とバックアップはすべてテストデータで、古い DB を修復する必要はない（利用者の判断）
- マイグレーションのスクリプトは既定DB（`config.DB_PATH` の初期値）にしか適用されない。選択中の月次DBに適用する方法はない
- 切替・バックアップからの復元のときに、不足している列を自動で補う仕組みはない。復元時の `init_kitting_plan_tables()` は、表やインデックスが無ければ作るだけで、既存の表に列を足さない
- 新規作成（前月引き継ぎを含む）では、表は最新の形で作られる。例外は migration_005 のインデックス2つ（`idx_kitting_plan_items_batch_id`、`idx_kitting_plan_items_kitting_version`）で、新しいDBには作られない（速度への影響だけで、計測はしていない）
- 列が足りない古いDBを開いたときに起きること（例）
  - NG実績・NG申告に `lot_no`・`is_unplanned` がない（008・010 が未適用）→ 実績レポートの集計、NG入力画面の一覧、在庫差異レポートを開く前の未処理チェックが失敗する
  - BOMキャッシュに `mounting_line`・`item_type` がない（011・013 が未適用）→ NG入力画面・仕掛部品展開画面のBOM展開が失敗する（2026-09-03 に実際に起きた。CANONICAL §6）
- 対策は `docs/refactoring_plan.md` の段階7（本番運用の開始前、または次のマイグレーションを追加する前に実施する）

## マイグレーション

- スクリプト: `db/migrate_001.py`、`db/migration_002.py` 〜 `db/migration_013_*.py`。どれも単独で実行するスクリプト（`if __name__ == "__main__"`）
- アプリ本体（ui/・services/・models/・main.py・config.py）から import されているものはない。.exe（`inventory_app.spec`）にも同梱されていない。アプリ本体が import する db/ のモジュールは `db/init_db.py`（`init_database_at()`、main_window.py から）だけ
- 現在は意味のないもの: migrate_001（対象の表は D-39 で廃止済み）、migration_012（構成基板数マスタは今は master.db 側に自動で作られる）
- **対象DB**: 各スクリプトは import した時点の `config.DB_PATH`（= `config.py` の初期値）につなぐ。開発環境では既定DB `inventory_app/db/inventory.db` が対象になる。`app_settings.json` に保存された選択中のDBは使わない。引数で対象DBを指定する仕組みもない
- どこまで適用したかを記録する仕組み（バージョンのテーブルなど）はない
- 実行のしかた（コマンドとカレントディレクトリ）を書いた記録: 未確認。スクリプトは自分で `sys.path` に `inventory_app/` を足すので、どこから実行しても import は通るはず
- 実行前のバックアップ: スクリプトは自動ではバックアップを取らない。`db/` 配下に `inventory.db.bak_migration010_*` のようなファイルがあるので、手でバックアップを取っていたと思われる（手順としての記録は未確認）
- `CREATE TABLE IF NOT EXISTS` で定義したテーブルは、アプリを起動しても列が増えない。列の有無は `PRAGMA table_info(<テーブル>)` で直接確かめる（CANONICAL §5 の 5・6 項目）
- 新しい月次DBは `init_database_at()` で作る。migration で追加された列が `schema.sql` に入っているかどうかはテーブルごとに違い、未確認（`models/scrap_records.py` には「schema.sql への直接追記は行わない」とある）

## Git 管理について

- `.gitignore` は `*.db` と `*.db.lock` を除外している。一方、`db/inventory.db.bak_*`（6ファイル）は追跡されている
