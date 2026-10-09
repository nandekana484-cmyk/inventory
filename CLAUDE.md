# CLAUDE.md

このファイルは毎回読まれる。**80行以内**を保つ。詳細は `docs/` に書き、ここには場所だけを書く。

## 概要
基板実装のキッティング計画・生産実績・NG/仕掛・部品在庫を、月次DB単位で管理する Windows デスクトップアプリ（部品在庫管理アプリ）。
利用者は作業者IDでログインする（admin/operator の2ロール）。
- 環境: Python / Tkinter＋tkcalendar / SQLite（月次DB＋master.db）
- 起動: `run_app.bat`（`.venv\Scripts\python.exe inventory_app\main.py` を実行する）。配布は PyInstaller（`inventory_app.spec`、onefile）
- テスト: `inventory_app` で `python -m pytest tests/`（現状は1件だけ）

## フォルダの役割
- `ui/` — 画面。入力の受け取りと表示だけを行う
- `services/` — 業務ロジック。UIに依存しない
- `models/` — テーブル単位のDBアクセス
- `db/` — スキーマとマイグレーション
- `scripts/` — 単発の調査・保守スクリプト（アプリ本体ではない）
- `tests/` — テスト
- `docs/` — 詳細ドキュメント（必要なときだけ読む）
- 実態: ui/services/models/db/tests は `inventory_app/` の下にある。`docs/` だけリポジトリ直下
- 実態: `scripts/` は `inventory_app/scripts/`（check_code_unchanged.py）。古い単発スクリプトは `inventory_app/` 直下（check_*・delete_*・dump_schema・list_*・verify_*・test_kitting_import.py）とルート（check_db.py・check_env.py）に残っている
- 実態: `db/` には実行時のDB（`db/<フォルダ名>/inventory.db`・`master.db`）とバックアップ（`*.bak_*`）も置かれる
- その他: `inventory_app/imports/`（取込サンプル）、ルートの CANONICAL_DESIGN_DECISIONS.md（設計判断の記録。D番号で grep する）・CHANGELOG.md・*_NOTES.md

## 層のルール
- 依存は `ui → services → models` の一方向。逆向きの import は禁止
- SQL は `models/` にだけ書く
- `services/` は GUI ライブラリを import しない（ダイアログも出さない）。結果は戻り値で返す
- 画面から他の画面の `_` 始まりの属性・メソッドに触らない
- 新規コードでは `ui/` から `models/` を直接呼ばない（既存コードは触るときだけ直す）
- 例外: `db/`（init_db・マイグレーション）と単発スクリプトは SQL を直接書いてよい
- 既存コードには違反が残っている（一覧は docs/refactoring_plan.md）。新しく増やさず、触るときに直す

## 命名
- `ui/*_window.py` / `services/*_service.py` / `models/<テーブル名>.py`
- 層をまたいで同じファイル名を作らない
- マイグレーション: `db/migration_<3桁連番>_<内容>.py`（001 は migrate_001.py、002〜004 は内容名なし）
- バージョン: `inventory_app/version.py` の APP_VERSION / BUILD_DATE が唯一の参照元。再ビルドのたびに更新し、CHANGELOG.md に書く
- 設計判断はコード中で「D-xx 参照」と CANONICAL_DESIGN_DECISIONS.md の D番号を指す

## コメントの方針
- 書くのは「なぜ」と罠だけ。1〜2行にする
- 処理の言い換え、変更履歴、コメントアウトした旧コードは書かない
- 複数ファイルにまたがる業務ルールは `docs/` に書き、コードには参照を1行だけ残す

## 作業ルール
- 開かない・変更しない: `inventory_app/db/` 配下の *.db・バックアップ（*.bak_*）・*.lock、`tessdata_local*/`
- `inventory_app/app_settings.json` は読んでよいが、変更・コミットはしない
- リファクタリングでは挙動を変えない。移動した関数は旧位置に re-export を残す
- 「コメント整理」「ファイル移動」「挙動変更」を同じコミットに混ぜない
- 1ファイルが 500 行を超えたら分割を提案する（既存ファイルには強制しない）
- 文字コード: 新規ファイルは BOM なし UTF-8。既存の BOM 付きファイルは変換しない

## 壊しやすい箇所
- 実績CSV取込の照合・自動登録判定（production_import_staging_window / production_import_service）
- ロット完成数の集計キー。代入ではなく加算する（production_service `_compute_lot_completion()`）
- グローバルな `config.DB_PATH` の切り替え（開いている画面・前月引き継ぎ・既定DB）
- Tk の pack 順序。Treeview を先に pack するとスクロールバーやボタンが潰れる
- スキーマのドリフト（migration の未適用、`CREATE TABLE IF NOT EXISTS` では列が増えない）
- 根拠と場所: `docs/fragile_spots.md`

## 詳細ドキュメント
- `docs/architecture.md` — 画面 → service → model の対応表
- `docs/refactoring_plan.md` — リファクタリングの段階表と進捗
- `docs/db.md` — DB切替・バックアップ・マイグレーションの手順
- `docs/domain/rule_index.md` — 業務ルール（コメントから抽出した一覧と出典。本文はまだ移していない）
- `docs/fragile_spots.md` — 壊しやすい箇所の根拠
- `docs/reports/bloat_survey_20261009.md` — 肥大化ファイル調査（行番号は古い。関数名で参照する）
