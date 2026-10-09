# キッティング計画（models/kitting_plan.py）

2026-10-09 のコメント軽量化で、コメント・docstring から移した内容。関数名で参照する。
コード中の「D-9x」は仮番号（`docs/refactoring_plan.md`「未解決の宿題」）。

## バージョンとアクティブ

- 計画は (kitting_list_no, lot_no) 単位でバージョン管理する（`create_plan_version()`）。新しいバージョンを作るときは、同じ (kitting_list_no, lot_no) の旧アクティブ版を必ず is_active=0 にしてから登録する。そのため lot_no ごとに is_active=1 の行は最大1件
- 部分ユニークインデックス `uq_kitting_plan_items_active_kitting_lot`: 同じ (kitting_list_no, lot_no) で is_active=1 は1件だけ。防げるのはキーが完全一致する場合だけ
- `list_active_plan_items_by_kitting_no()`: kitting_list_no に一致するアクティブな行を lot_no で絞らずに返す。kitting_list_no は lot_no をまたいで重複する（実データで478件）ので、`_resolve_plan_item()` が lot_no 省略で呼ばれたときに「候補が複数あるか（選択ダイアログが要るか）」の判定に使う。is_active 列の無い古い DB では、一致する行をすべて返す（バージョン管理の無い環境なので、それが現在の状態）
- `list_plan_items_by_lot()`: 以前は is_active を条件に含めず、旧バージョンの行も混ざっていた。`calculate_lot_completion()` の kitting_list_no/lot_no の取り違えの修正と合わせて、is_active=1 を足した（呼び出し元がそこだけだと確認できたため）
- `list_plan_items_for_all_lots()`: `list_plan_items_by_lot()` と同じ条件で全ロット分を1回の SELECT で返す。`list_incomplete_lots()`・`check_lot_progress()` が、ロットごとに SELECT する N+1 を避けるために使う。lot_no が NULL・空の行は除く（ロット単位の完成数が成り立たず、`calculate_lot_completion()` では ValueError になる）。board_name は構成基板数チェックのため 2026-09-26 に追加した

## バッチの削除・復元（`mark_batch_deleted()`）

- kitting_plan_batches.delete_flag だけを変えても、`list_active_plan_items()` などは kitting_plan_items.is_active しか見ないので、削除が反映されない。明細の is_active も同じコネクション・1回の commit で更新する（片方だけ成功するのを避ける）
- 削除: このバッチのアクティブな行を is_active=0 にする
- 復元: 単純に is_active=1 に戻さない。同じ (kitting_list_no, lot_no) に、新しいバージョンで既に別のアクティブな行があると、戻した行と重複する。このバッチの行で、今 is_active=0、かつ同じキーにほかのアクティブな行が無いものだけを戻す

## キッティングNo.が未確定の計画（pending_kitting_plan_items）

- キッティングNo.が未確定のまま取り込んだ計画行を一時保存する（`services.kitting_import_service.import_kitting_plan_csv()`）
- 識別キーは (lot_no, setup_file_no, production_side, order_qty)。後日キッティングNo.が付いて再取込されたとき、識別キーが一致すれば「未確定期間からの確定」として正式な kitting_plan_items へ移す
- `upsert_pending_kitting_plan_item()` は SELECT して UPDATE/INSERT を判定する（delete-then-insert ではない）。ユニークインデックス `uq_pending_kitting_plan_items_identity` は ON CONFLICT には使わず、想定外の経路からの重複挿入を DB でも防ぐ保険
- テーブルの作成は呼び出し元に任せる。`import_kitting_plan_csv()` はループの前に `create_plan_batch()`（内部で `init_kitting_plan_tables()`）を呼ぶ。以前は行ごとに `init_kitting_plan_tables()` を呼んでいて、CREATE TABLE/INDEX IF NOT EXISTS の繰り返しが性能のボトルネックになった

## 一覧と面

- `list_active_plan_items()`: 実績入力用。各要素に、完了判定に使った `app_cumulative_qty` を含める（呼び出し元は再計算しないこと）
  - 完了済み（実績が発注数に到達）は除く。include_completed=True なら含める
  - 同じロット・file_no に面2（2回目）の計画があれば、面1（1回目）は完成品ではないので除く（D-8）
  - `find_matching_plan_items()`（実績CSV自動取込）は必ず既定（完了済みを除く）で呼ぶ。完了済みと未完了が同じロット・製品名で複数あると一意に特定できず、自動取込の照合が壊れる
  - 実績は (kitting_list_no, lot_no) の組でまとめて取得する（N+1 回避。kitting_list_no だけで集計すると、別ロットの実績まで合算してしまう。実データで完成数の取り違えを確認）
- `find_opposite_side_plan()`: 同じ lot_no・setup_file_no の反対側の面のアクティブな計画を1件返す。日付違いの複数バッチがアクティブなこともある（実データで確認）。その場合は current_plan_start_datetime に最も近いもの、無いか解釈できなければ生産予定日の昇順で最初のもの
- `list_active_side_plans_for_file()`（2026-10-08）: 同じ (lot_no, setup_file_no) の面1・面2のアクティブな計画を全件返す。面1と面2の実績の合計を比べる不整合判定用。`find_opposite_side_plan()` で1件に絞る方式では、複数バッチで誤った組を選んで誤検出した（`docs/domain/lot_completion.md`）。用途が違うので既存の関数は変えずに新設した

## 面1だけの計画の分類（`classify_side1_only_plan()`、2026-10-07）

- a: 生産面マスターに後行面なしの登録がある → 片面の製品
- b: 後行面ありの登録がある → 面2待ち（まだ計画データに面2が入っていないだけ）
- c: 登録が無い → 生産面マスター未登録
- production_side が "1" でなければ None（分類の対象外）。`list_active_plan_items()` を通った面1には、同じ (lot_no, setup_file_no) に面2が無いことが保証されている（あれば除かれている）ので、計画データを確かめ直さず、マスターだけで分類できる
- 判定の単位（2026-10-08 改訂）: 以前は (setup_file_no, mounting_line) 単位だったが、同じファイルNoで先行面と後行面が別の実装ラインを流れることが分かり、ファイルNo単位（`get_second_side_status_by_file_no()`）にした。実装ラインは kitting_list_no の文字列からではなく mounting_line 列を使う方針（kitting_list_no の3番目の区切りと mounting_line 列が一致しない行が 3526件中20件ある、D-93）は変わらないが、この判定ではもう実装ラインを使わない

## 生産面マスターとの確認用の一覧（ファイルNo単位）

- `find_master_plan_discrepancies()`: マスターでは後行面なし（1回目のみ）だが、計画データにはいずれかの実装ラインで面2の計画があるファイルNo（マスターが古い可能性が高い）。利用者が確認するための一覧で、判定には使わない。面2のある実装ラインとロットNoも参考に返す
- `find_unregistered_production_side_file_nos()`: 計画データにあるが、マスターにどの実装ラインも登録が無いファイルNo。`classify_side1_only_plan()` の c と同じ基準。CSV 出力の元データにも使う
- `find_registered_file_nos_missing_line_combinations()`: 参考情報（判定には使わない）。ファイルNoは登録があるが、計画データ上のこの実装ラインには登録が無い組（例: 先行面を A・B ラインで流すが、マスターには A ラインしか無い → B ラインが出る）
- `list_mounting_lines_by_file_no()`: 未登録のファイルNoの CSV 出力で、どの実装ラインに生産面を登録すればよいかを示すため、計画データにある実装ラインを返す

## 面1の計画が面2で隠れたとき

- 面1に実績を登録した後で面2の計画が追加されると、面1は一覧から隠れる（D-8）。実績の付け替えは自動では行わない（利用者の方針）
- `find_newly_hidden_side1_plans()`: 今回の計画CSV取込で追加した面2によって、実績のある面1が隠れることになった組（取込完了時の通知用）
- `find_all_hidden_side1_plans_with_production()`: 今そうなっている組を全件（いつでも確認できるように）

## 実績CSV自動取込の照合

- `find_matching_plan_items()`: lot_no と正規化済みの製品名から候補を探す。戻り値は (lot_no が一致するアクティブな計画, そのうち製品名も一致するもの)。`resolve_plan_by_lot_and_name()` の判定と、取込側の未一致の理由（計画なし・製品名ゆらぎ・複数候補）の判別の両方で使う
- 一致判定は完全一致だけ。入力が正規化済みなので、board_name を正規化して比べると「完全一致」と「正規化一致」は同じ判定になる。あいまい一致（部分一致など）は実装しないと決まっている（完全一致だけで運用する）
- plan_items_by_lot（{lot_no: [item, ...]}、キーは `str(lot_no or "").strip()`）を渡すと、`list_active_plan_items()` を呼ばずにそこから引く。CSV の行ごとに全計画のスキャンと実績の集計をしていたため、2000行の取込に約61秒かかっていた。1回だけ取得してロットごとにまとめる方式で1秒未満になった。省略時は従来どおり毎回呼ぶ
- `resolve_plan_by_lot_and_name()`: kitting_list_no が1種類に決まればそれを返し、0件か複数なら None
- `normalize_product_name()` は services.production_import_service にあり、そちらがこのモジュールを import しているので、関数内で import する（循環 import の回避。`docs/refactoring_plan.md`「既知の違反」）
