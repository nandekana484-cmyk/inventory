# 現場CSV取込（現場実績入力アプリの実績CSV。ui/production/external_actual_window.py）

2026-10-25。現場実績入力アプリ（別アプリ）が書き出した実績CSVを、人が確認したうえで実績（production_daily）とNG申告（ng_declarations）に登録する。
現場アプリは DB に書かない。このアプリも、確認なしには書かない（自動登録は無い）。設計の判断は CANONICAL_DESIGN_DECISIONS.md D-186（§63）。

## 構成

| 層 | ファイル | 役割 |
|---|---|---|
| ui | `ui/production/external_actual_window.py` | 「ファイルを選ぶ → 取り込む → 一覧で確認 → 選んだ行を登録」。入力の受け取りと表示だけ。生産実績入力画面の「現場CSV取込」ボタンから開く |
| services | `services/external_actual_csv.py` | CSV の解析と、DB を見ない検査。GUI・DB に依存しない |
| services | `services/external_actual_service.py` | 取り込み（`import_external_actual_file()`）、登録前の一覧の計算（`evaluate_rows()`・`build_preview()`）、登録（`register_rows()`） |
| services | `services/csv_format_detection.py` | 現場実績CSVの固有列（書式版・書き出し日時）。誤投入の警告 |
| models | `models/external_actual_rows.py` | 登録待ちの表の読み書き（SQL はここだけ） |
| db | `db/migrations/m015_add_external_actual_tables.py`、`db/schema.sql` | 表の作成（版 15） |

## CSV の形式（固定。変えるときは現場アプリ側と先に相談する）

UTF-8（BOM 付き）。ヘッダーは次の12列で、この順。Excel で保存し直して Shift_JIS になっていても読む（utf-8-sig → utf-8 → shift_jis → cp932）。
書式版・書き出し日時・元のDB(フォルダ名)・キッティングNo.・ロットNo.・ファイルNo.・面・日付・生産数(累計)・NG面1・NG面2・担当者ID

- 生産数は、その計画の累計（日ごとの増分ではない）。再送は上書き
- NG面1・NG面2 は、その面だけの値。空は 0。面1の保存値への「面2の分の足し込み」は `compute_ng_save_qty()` に任せる
- 両面のロットは面2の行にまとめて出る（NG面1はその行に入る）。面1の行が出るのは、そのロットに面2の計画が無いときだけ
- 担当者ID は `workers` の worker_id と同じ表記。正規化せず、そのまま比べる

## 取り込みの検査（`external_actual_csv.py`）

- 止めるのは次の2つだけ（ファイル全体を取り込まない）: ヘッダーが12列と違う（足りない列・余分な列・順番）、書式版が 1 でない行がある
- 行ごとの異常は、その行だけ取り込まない（一覧に赤で出す）
  - 元のDB(フォルダ名)が今のDB のフォルダ名と違う（続行の選択肢は無い）
  - キッティングNo.・ロットNo.・ファイルNo.が空、Excel で化けた形（指数表記 1.2E+5、ファイルNo.と、キッティングNo.の先頭の区切りの先頭0落ち）
  - 面が 1・2 以外、日付が `YYYY-MM-DD` でない（`strptime` と形の両方で確かめる）、生産数が空・負・整数でない、NG が負・整数でない（空は 0）
  - 書き出し日時が読めない（`YYYY-MM-DD HH:MM:SS`・ISO 8601・`YYYY/MM/DD HH:MM[:SS]`。内部では前者に直して文字列で比べる）
  - 担当者IDが空、面1の行で NG面2 が 0 より大きい（`compute_ng_save_qty()` が面2の値を面1に足してしまうため）
- キッティングNo.・ロットNo.・ファイルNo.は、前後の空白を除いて NFKC にする。計画との比較は完全一致（末尾「-」の似たロットの候補は出さない）
- 同じ (キッティングNo., ロットNo.) が CSV に複数あれば、書き出し日時が新しい行を使う（同じなら後ろの行）。内容が違えば警告
- 今のDB が未選択の既定DB、または DB の版（`PRAGMA user_version`）が `LATEST_VERSION` と違うときは、取り込みを断る（登録の直前にも確かめる）

## 登録待ち（`external_actual_batches`・`external_actual_rows`）

- 取り込んだ行は、実績・NG に書かず、登録待ち（status = `pending`）にためる。既存の登録待ち（`pending_csv_import_rows`）とは別の表
- 同じ計画の登録待ちは1行だけ（部分一意インデックス）。再送は、古い登録待ちを消して新しい行を入れる。登録済み（`registered`）の行は消さずに残す
- 取り込み済みの書き出し日時（登録待ち・登録済みの両方）より古い行は、警告して取り込まない。同じ書き出し日時は取り込む
- 行に `exported_at` を持つ（設計の表に足した列）。CSV の書き出し日時は行ごとに違いうるため
- 登録待ちは月次DB ごとに持つ。別の月のDB には見えず、前月からの引き継ぎでも引き継がない（引き継ぎのときに残りの行数を知らせるだけ。`summary["external_pending_rows_not_copied"]`）

## 登録前の一覧（`evaluate_rows()`）

状態の色: **緑**（新規、または今の実績・日付・NG と差がある）、**黄**（差があり、確認が要る）、**赤**（登録できない）、**グレー**（変更なし）。

- 赤: 計画が無い／面が計画の `production_side` と違う／ファイルNo.が計画の `setup_file_no` と違う／面1の拒否・面2待ち（`side1_registration_block()` に任せる）／NG面1 を保存する面1の計画が無い／担当者IDが `workers` に無い／取り込み先のDB が今のDB と違う
- 黄（差がある行だけ）: 完成済みロット（`preview_completed_lot_registration()`）／「実績＋NG」が予定生産数と合わない（`build_planned_qty_mismatch_lines()`）／既存の実績より古い日付
- 黄の行は、画面で1行ずつ選ばないと登録しない（「緑の行をすべて選ぶ」は緑だけ）。赤・グレーの行は選べない
- 面1の NG は、一覧では「面1の保存値 − 面2の保存値」の固有分で見せる（保存値には面2の分が足されているため）。NG が 0 の面は既存の申告を変えないので、備考に「NG 0 は既存の申告を変えません」と出す
- 面1の計画は CSV に無い。面2の行で NG があれば、生産実績入力画面と同じ探し方（`find_opposite_side_plan()`）で決める
- 速度: 計画は `group_active_plan_items_by_lot(include_completed=True)` を1回、今の実績は `get_app_cumulative_qty_bulk()`・`summarize_daily_by_plan()`、NG は `list_ng_declarations_latest()` で、それぞれまとめて引く。行ごとに DB を引くのは、計画が一覧に無い行・NG を面1に連動させる行・面1の行の拒否の判定（ファイルNoごとに1回）だけ。完成済みの判定はロットごと（300ロットで約1秒）

## 登録（`register_rows()`）

1. 登録の直前に DB の版と、選んだ行のバッチの取り込み先DB（`target_db_name`）を確かめる。今のDB と違う行が1つでもあれば、何も登録しない
2. 行ごとに今の計画・今の実績・NG・完成済みかを読み直し、一覧を作ったときの `snapshot` と違えば、その行は登録せず「プレビュー後に値が変わりました」と返す
3. 行ごとに `register_actual_and_ng()` を呼ぶ。作業者はCSVの担当者ID、日付はCSVの日付（NG の日付は登録した日。既存の動き）。既存の実績があれば上書き（1計画＝1レコード）
4. NG の部分失敗（実績の登録のあと NG が失敗）は、その行を登録待ちのまま残し、結果に出す（もう一度登録しても同じ結果になる）
5. 成功した行は `registered` にする。操作履歴に「現場CSV取込登録」を、登録した人の名前で残す（実績と、保存した面の NG の保存値の前後を含む）
6. `lot_status_history` は行ごとには記録せず、最後にロットごとに1回（`trigger_source = external_csv`）

画面を開いている間は、生産実績入力画面の登録・実績修正のボタンを押せない。登録後は `refresh_after_external_registration()` で、表示中の計画と計画一覧を更新する。
