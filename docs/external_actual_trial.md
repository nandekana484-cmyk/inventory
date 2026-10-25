# 現場アプリとの実機つなぎ確認の手順（現場CSV取込）

現場実績入力アプリが書き出したCSVを、このアプリに取り込んで登録するまでを、**本物のDBに触れずに**確かめる手順。
仕様は docs/domain/external_actual.md。CSV は書式版 2（13列）。

## 0. 本物のDBを使わないための注意（先に読む）

- このアプリの保存先（`APP_DATA_DIR`）は、開発環境ではアプリのフォルダ（`inventory_app/`）、.exe ではその .exe のフォルダ。月次DB（`db/<フォルダ名>/inventory.db`）も、作業者などのマスタDB（`db/master.db`）も、その下にある。`db/master.db` は切り替えの対象外で、どの月次DB を開いても同じものを読み書きする
- そのため、**月次DB のファイルだけをコピーして本物のアプリで開くのは避ける**。登録で操作履歴は月次DB に書くが、`master.db` や `app_settings.json`（最後に開いたDB）は本物のものが使われる
- 試すときは、**アプリのフォルダごとコピー**して、コピーの中で起動する（下の手順1）。本物の `inventory_app/db/` と `app_settings.json` は触らない
- 本物の月次DB・`master.db`・バックアップ（`*.bak_*`）は、開かない・変更しない（CLAUDE.md「作業ルール」）。コピーは、アプリを閉じてから（`.lock` が残っていないとき）取る
- 確認が終わったら、コピーのフォルダごと捨てる。コピーのDBに入れた実績・NGを、本物に戻す手順は無い

## 1. 試用のコピーを作る

1. アプリを閉じる。ほかのPCで本物のDBを開いていないことも確かめる
2. 試用のフォルダを作る（例 `C:\trial`）。`inventory_app` フォルダごとコピーする（`C:\trial\inventory_app`）。`.venv` はコピーせず、本物のものを使う
3. コピーの中で、不要なものを消す: `C:\trial\inventory_app\app_settings.json`（あれば。本物の「最後に開いたDB」を引き継がないため）
4. 月次DB のコピーを、**現場アプリが書き出すときの「元のDB(フォルダ名)」と同じ名前のフォルダ**に置く
   - 例: `C:\trial\inventory_app\db\2026-10\inventory.db` に、本物の `2026-10` の `inventory.db` をコピーする
   - CSV の「元のDB(フォルダ名)」と今のDB のフォルダ名が違う行は、取り込めない（続行の選択肢は無い）。名前を変えて試したいときは、現場アプリ側が書き出すフォルダ名も同じにそろえる
   - フォルダ名 `db` は使えない
5. `master.db`（`C:\trial\inventory_app\db\master.db`）も、コピーされたものがそのまま使われる。現場CSV の担当者ID が、この `workers` にあること（無い行は赤で登録できない）

## 2. コピーを開いてアプリを起動する（DB_PATH の指定）

開発環境のアプリは、`config.py` の場所を保存先にする。**コピーの `main.py` を、本物の .venv の Python で起動する**と、`DB_PATH` は `C:\trial\inventory_app\db\...` になる。

```
C:\work\inventory\.venv\Scripts\python.exe C:\trial\inventory_app\main.py
```

（`run_app.bat` は本物の `inventory_app` を起動するので使わない）

月次DB は次のどちらかで選ぶ。

- **画面から**: ログイン後のメインメニューで、データベースの一覧から `2026-10` を選んで「切り替え」。選んだDB は、コピーの `app_settings.json` の `last_db_path` に保存され、次回から自動で開く
- **設定ファイルを先に書く**: `C:\trial\inventory_app\app_settings.json` を作る（UTF-8）。次のとおり

  ```json
  {"last_db_path": "C:\\trial\\inventory_app\\db\\2026-10\\inventory.db"}
  ```

  ファイルが無いと、起動時は既定DB（未選択）になる。未選択の間は、現場CSV取込は断られる

メインメニューの「現在のDB」の表示が `C:\trial\...` であることを、必ず確かめる（`C:\work\inventory\...` なら本物。すぐ閉じる）。
.exe で試すときは、`InventoryApp.exe` を含む `dist` フォルダを、同じようにフォルダごとコピーして、コピーの .exe を起動する（保存先は .exe のフォルダ）。

## 3. 現場CSVの取り込みから登録まで

1. 作業者でログインし、メインメニューから「生産実績入力」を開く
2. 下のボタン行の「現場CSV取込」を押す（開いている間、生産実績入力画面の登録・実績修正ボタンは押せない）
3. 「CSVを選んで取り込む」で、現場アプリが書き出したCSVを選ぶ
   - 実績CSV・計画CSVを選んでいると、確認が出る
   - ヘッダーの不備・書式版が 2 でないときは、ファイル全体が断られる（説明が出る）
   - 取り込み後の案内に、取り込んだ行数・置き換えた行数・取り込まなかった行数が出る。ここではまだ実績・NG に書いていない
4. 一覧で確認する。色: 緑＝新規または差あり、黄＝要確認、赤＝登録できない、グレー＝変更なし
   - 「生産数（今→新・差）」「NG面1（今→新）」「NG面2（今→新）」と、下の「備考」（行を選ぶと全文）を見る
   - 赤の行は理由を読む（計画が無い・面やファイルNo.の違い・面1のキッティングNo.の不一致・担当者ID不明・形式エラーなど）。直すのは現場アプリ側（CSVを書き出し直す）か、計画・作業者マスターを取り込んでから「一覧を更新」
   - 備考に「NG面2は面1に連動しません」とあれば、面1のキッティングNo.が空なので、面2のNGは面2だけに保存される
5. 登録する行を選ぶ。「緑の行をすべて選ぶ」は緑だけ。黄の行は、1行ずつクリックして選ぶ
6. 「選んだ行を登録」→ 確認の画面（件数と内訳、上書きになる行の今→新）。初めに選ばれているのはキャンセル。登録する場合は「登録する」を押す。上書きの行は、チェックを外すと登録しない
7. 結果の画面で、行ごとの結果を見る。「プレビュー後に値が変わりました」の行は登録されていない（一覧を更新して確認し直す）。NG の部分失敗の行は、実績だけ登録され、登録待ちに残っている
8. 同じCSVを書き出し直して取り込み直すと、同じ計画の登録待ちが新しい行に置き換わる（実績は上書き）

## 4. 登録後の確認（SQL）

確認は、**コピーのDB** を**読み取り専用**で開く。DB Browser for SQLite を使うなら「読み取り専用で開く」を選ぶ。コマンドなら、次のスクリプトを保存して実行する（`DB` を書き換える）。

```python
# check_trial.py   実行: C:\work\inventory\.venv\Scripts\python.exe check_trial.py
import sqlite3
DB = r"C:\trial\inventory_app\db\2026-10\inventory.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)      # 読み取り専用
con.row_factory = sqlite3.Row

QUERIES = {
    "実績（production_daily）": """
        SELECT kitting_list_no, lot_id AS lot_no, report_date, daily_qty, worker_id
        FROM production_daily ORDER BY prod_log_id DESC LIMIT 20""",
    "NG申告（ng_declarations）": """
        SELECT kitting_list_no, lot_no, file_no, production_side, ng_qty, report_date
        FROM ng_declarations ORDER BY id DESC LIMIT 20""",
    "登録待ち・登録済み（external_actual_rows）": """
        SELECT r.row_id, r.status, r.kitting_list_no, r.lot_no, r.side, r.report_date, r.daily_qty,
               r.ng_side1, r.ng_side2, r.face1_kitting_list_no, r.worker_id, r.registered_at, r.registered_by,
               b.source_file, b.target_db_name
        FROM external_actual_rows AS r JOIN external_actual_batches AS b USING (batch_id)
        ORDER BY r.row_id DESC LIMIT 20""",
    "操作履歴（現場CSV取込登録）": """
        SELECT * FROM operation_log WHERE operation_name = '現場CSV取込登録' ORDER BY id DESC LIMIT 10""",
    "ロット履歴（external_csv）": """
        SELECT lot_no, status, trigger_source, recorded_at FROM lot_status_history
        WHERE trigger_source = 'external_csv' ORDER BY rowid DESC LIMIT 10""",
}
for title, sql in QUERIES.items():
    print("\n==", title)
    for row in con.execute(sql):
        print(dict(row))
```

見るところ:

- 登録した計画の `production_daily` が、**その計画に1行**で、`daily_qty` がCSVの生産数（累計）、`report_date` がCSVの日付、`worker_id` がCSVの担当者IDであること（上書きなら、前の行は消えて1行になる）
- `ng_declarations` が、面2の行の NG面2 の値（`production_side` = 2）と、面1の保存値（`production_side` = 1）を持つこと。**面1の保存値は「NG面1 ＋ NG面2」**（面1のキッティングNo.があるときだけ保存される）。NG が 0 の面は変わらない
- `external_actual_rows` の登録した行が `registered` になり、`registered_by` が登録した人の worker_id であること。NG の部分失敗の行は `pending` のまま
- 操作履歴に「現場CSV取込登録」が、行ごとに1件（実績と、保存した面の NG の保存値の前後つき）あること

画面でも確かめられる。生産実績入力画面で、その計画を開くと、アプリ内累計と NG の欄が登録後の値になっている。

## 5. やり直したいとき

コピーのフォルダ（`C:\trial`）を捨てて、手順1からやり直す。本物のDB・`master.db`・`app_settings.json` は、この手順では一度も開いていないので、そのまま。
