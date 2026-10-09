# 実績の登録とロットの完成数・引落（services/production_service.py）

2026-10-09 のコメント軽量化で、コメント・docstring から移した内容。関数名で参照する。
集計キー（2要素・加算）の決定は CANONICAL_DESIGN_DECISIONS.md §5 と BOM_MIGRATION_NOTES.md §4。

## 実績の登録

- 1計画（kitting_list_no・lot_no）＝1レコード、常に上書き。以前は当日分だけを重複・削除の対象にしていたが、日付を問わず対象にした
- `register_daily_result(check_duplicate=True)`: 既存の実績があれば追記せず `DailyResultAlreadyExists` を送出する。CSV 自動取込（`import_production_csv()`）は check_duplicate=False（無条件に追記）のまま。手入力（`KittingProductionEntryWindow`）は確認ダイアログで既存の有無を先に見せているので、例外を使わず register / overwrite を呼び分ける
- 既存レコードが複数ある（仕様変更前の過去データ）ときは、最も新しい日付のものを使う
- `overwrite_daily_result()` の report_date: None を今日にせず、そのまま `replace_daily_result()` に渡して既存行の日付を引き継ぐ（2026-09-29 の調査で、手で数量を直して再登録するたびに CSV 由来の正しい日付が今日に書き換わる問題が見つかったため）。既存行が無ければ今日
- 反対側の面への連動（`register_opposite_side_daily_result()`）: 手入力と CSV 自動取込の共通処理（以前は UI 側にあった）。反対側でも重複チェックは通すが、既存があっても確認せずに上書きする
- 履歴（lot_status_history）の記録
  - 登録・上書き・修正・削除のたびに、そのロットの状態を記録する（2026-09-30）。記録に失敗しても本来の処理は失敗させない（`_record_lot_status_snapshot_safely()`。共通フックにはせず、各関数から個別に呼ぶ方針）
  - record_history=False: 一括登録（Shift+S など）で行ごとに記録すると、1件あたり数十 ms（commit の同期コスト）かかり100件で数秒になる。呼び出し元が影響したロットの集合を持ち、最後にロット単位で1回ずつ記録する。反対側の面も同じ lot_no なので漏れない
  - 修正（`update_daily_result()`）は UPDATE の前に、削除（`delete_daily_result()`）は DELETE の前に対象行を読んで lot_no を特定する（削除後は読めない。修正で前に読むのは、行が既に消えていた場合を検出するため）

## 実績の修正・削除での面の連動（`build_linked_correction_preview()`、D-112）

- 相手の実績の特定
  - a. 同じロットNo・ファイルNoの反対側の面の計画（複数バッチあり得る）の実績のうち、修正・削除前の数量・日付が完全に一致するものが1件だけなら相手にする。複数一致したら決められないので b へ
  - b. 登録時の自動入力・NG 入力欄の相手探しと同じ方法（`find_opposite_side_plan()` に選択中の計画の生産予定日を渡す）で計画を1件に絞る。実績が1件ならそれが相手。2件以上（「1計画＝1レコード」に反する想定外のデータ）なら連動しない（ambiguous=True）
  - c. 相手の計画に実績が無ければ、修正なら新規登録、削除なら何もしない（noop）
- 戻り値
  - `primary`: prod_log_id・kitting_list_no・lot_no・old_daily_qty・new_daily_qty（削除なら None）・report_date・plan
  - `has_opposite_plan`: 反対側の面の計画自体があるか（無ければ secondary は None で、連動しない）
  - `secondary`: None、または method（a/b）・ambiguous・action（update/insert/delete/noop）・kitting_list_no・prod_log_id と old_daily_qty（既存行があるとき）・new_daily_qty・report_date・pre_existing_mismatch（b で、連動前の相手の実績が primary の変更前の値と食い違っていたら True。確認ダイアログで目立たせる）
- `apply_linked_correction()` は1つのトランザクションで確定する

## ロットの完成数（`_compute_lot_completion()`）

- 完成数＝ロット内の (setup_file_no, production_side) ごとの実績合計の最小値
- キーは2要素で、代入ではなく加算する。同じファイルNo・面に、kitting_list_no の違う複数のバッチ（例: 実装予定日違い）が同時にアクティブなことがあり（実データで222件）、代入では片方の実績が消える
- order_qty: 以前は先頭行（順序不定）の値を無条件に使っていた。複数の file_no を持つロット301件のうち3件（166248・516526・516626）で、file_no 間の order_qty が食い違っていた。値が1種類ならそれを使い、複数なら先頭行の値を使いつつ order_qty_inconsistent・order_qty_values で知らせる
- 実績の取得は (kitting_list_no, lot_no) の組でまとめて行う。kitting_list_no だけで集計すると、lot_no をまたいで重複する別ロットの実績まで合算してしまう（実データで完成数の取り違えを確認）
- cutoff_date（2026-10-09、日々の引落一覧の払い出し日ベース化）: この関数は絞り込まず、戻り値に記録するだけ。呼び出し元が、その日付までの実績だけの cumulative_by_pair を渡す

## 構成基板数チェックと引落（`_evaluate_lot_status()`）

- 日報・月報（`_build_report_rows()`）・仕掛数量抽出（`build_wip_extraction_rows()`）・ロット進捗チェック（`check_lot_progress()`）・未完了ロットの抽出（`list_incomplete_lots()`）で共通の、唯一の実装（2026-09-28）
- 一本化の経緯: 以前は機能ごとに判定していた。lot_no=260079（構成基板数2に対し実 file_no 数1の shortfall）で、ロット進捗チェックは引落0・仕掛800、日報・月報は引落800・仕掛0、仕掛数量抽出は対象0件、と食い違った
- 判定の手順
  1. `_compute_lot_completion()` で実績ベースの完成数・代表の order_quantity・file_actuals を得る
  2. 面2があれば対応する面1を除いた、ロット全体の file_no の集合（visible_file_nos）を作る
  3. 基板名ごとに構成基板数マスタを引き、未登録の基板名をすべて集める（以前は代表1件だけを見ていて、ほかの基板名の未登録を見逃した。2026-09-26 修正）
  4. status: 登録済みの基板名が無い→unregistered／登録値が複数種類→board_count_inconsistent（比較はしない）／それ以外は file_no の数と比べて、一致→match、足りない→shortfall（shortfall_count）、多い→excess（excess_file_nos）
  5. 引落: `DRAWDOWN_ZERO_STATUSES`（shortfall だけ）なら0、それ以外は実績ベースの完成数
  6. file_no・面ごとの仕掛（file_actual − 引落）と未生産（order_qty − file_actual）を、基板名ごとにまとめる
  7. shortfall なら、不足分の「未確定」エントリ（引落0・仕掛0・未生産＝代表の order_quantity）を足す
- 引落のルール（利用者の決定）: 不足（shortfall）は構成がそろわないので引落を確定できない→0。未登録・不一致は比べられない、超過は自動で補正しない方針なので、実績ベースの値のまま。ルールを変えるときは `DRAWDOWN_ZERO_STATUSES` だけを変える
- `NEEDS_REVIEW_STATUSES`（unregistered・board_count_inconsistent・excess）: 引落は実績ベースのままだが、構成基板数との整合を確かめていないので「確認・修正が必要」（赤字）とする
- 「確認事項」欄の文言（`_build_lot_status_remarks()`）
  - unregistered:「構成基板数マスタ未登録(引落は未検証)」
  - board_count_inconsistent:「構成基板数がロット内で不一致 4, 5(引落は未検証)」（実際の値を昇順で）
  - excess:「実ファイルNo数がマスタの構成基板数を超過(引落は未検証)」
  - shortfall:「構成基板数不足: 未確定N件(引落0)」
  - match: 主な理由は無し
  - status が unregistered 以外で一部の基板名が未登録なら「一部未登録: 基板名…」を足す（unregistered では二重になるので足さない）
- 文字色（`_lot_status_color_category()`）: needs_review（赤）を優先し、shortfall はオレンジ。一部未登録と不足が両方あれば赤（優先順位の指定は無く、実装時に決めた）
- board_structure_cache（2026-10-09）: 1057ロットで、マスタを毎回引くと1回の表示に5〜10秒かかった。対象日・前日の2回の評価でキャッシュを共有し、1秒未満にした（`evaluate_all_lot_status()`、`services.lot_status_history.get_daily_drawdown()`）
- `evaluate_lot_status(cutoff_date=...)`: 計画と構成基板数マスタは現在の値のままで、日付では絞り込めない（D-115/D-29 改訂の調査で確認した制約）

### `_evaluate_lot_status()` の戻り値

lot_no・status・board_count（match/shortfall/excess のみ）・board_count_values（board_count_inconsistent のみ）・visible_file_nos・shortfall_count・excess_file_nos・unregistered_board_names・boards（基板名ごとの board_count と files: setup_file_no・production_side・order_qty・file_actual・lot_completed・surplus_qty・not_produced_qty）・missing_file_entries（同じ形、setup_file_no="未確定"）・lot_completed・status_remarks・status_color_category・order_quantity・file_actuals・order_qty_inconsistent・order_qty_values

## 日報・月報の行（`_build_report_rows()`）

- 以前は「その日・期間に実績がある行だけの最小値」を独自に計算していたため、未生産の file_no がある lot_no=110068 などで誤って「引落済み」になっていた。完成数・引落は `_evaluate_lot_status()` の結果を使う
- 計画が見つからない（ValueError）か、行の file_no・面が file_actuals に無い場合は、判定できないので未完成扱い（引落0、仕掛＝その日の実績、未完了＝発注数）にする。実績はあるデータなので、行は消さない
- lot_no は計画を引き直さず、実績の lot_id を使う（kitting_list_no は lot_no をまたいで重複し、kitting_list_no だけで計画を引くとどちらが返るか分からない）
- 面1の除外と不整合（D-111、2026-10-08）
  - 面2の計画がアクティブに存在するロット・ファイルNoの面1の行は表示しない（D-8）
  - 面1合計が面2合計を上回れば inconsistency_warnings に記録する（片面だけの修正で食い違う既存データがあり得るため）
  - 以前は面1の実績ごとに `find_opposite_side_plan()` で相手の面2を1件に絞って比べていた。複数バッチがあると別のバッチどうしを比べてしまい、実データで104件すべてが誤検出だった。ロットNo・ファイルNoごとの合計どうしで比べる方式に変えた
- 構成基板数の振り分け: 未登録→unregistered_board_warnings（実際に未登録の基板名すべて）、不一致→board_count_inconsistency_warnings、不足→「未確定」の仮想行、超過→excess_file_no_warnings。仮想行と警告は、本ループの後にロット単位でまとめて追加する（実績の順では同じロットの行が連続する保証が無いため）
- 仮想行: kitting_list_no=""（画面のダブルクリックは「空なら何もしない」）。数量に None や文字列を入れると、画面の `":.0f"` の書式化で ValueError になるので0にする
- 確認事項・文字色（2026-09-28）: 各行に confirmation_note と status_color_category を付ける。文言と色の定義は services 側の2関数にだけ書き、画面ごとに定義しない
- 戻り値: (report_rows, inconsistency_warnings, order_qty_inconsistency_warnings, unregistered_board_warnings, excess_file_no_warnings, board_count_inconsistency_warnings)
  - report_rows の主なキー: seq（表示行だけで1から振り直す）・confirmation_note・status_color_category・status（match/shortfall/excess/unregistered/board_count_inconsistent/None）・has_ng（NG 実績か NG 申告があれば True。仮想行は False）・report_date（仮想行は None）
  - inconsistency_warnings: lot_no・setup_file_no・side1_total・side2_total・diff・side1_kitting_list_nos・side2_kitting_list_nos
  - unregistered_board_warnings の file_nos は、ロット全体・面1除外後の distinct な file_no（CSV 出力で使う）

## 未完了ロットの抽出（`list_incomplete_lots()`）

- DB 新規作成時の引き継ぎ用。2026-10-01 に `_evaluate_lot_status()` を通すよう変えた。以前は構成基板数を見ない完成数で判定していたため、shortfall のロットを「完了」として引き継ぎから外すおそれがあった（理論上。実データでは未確認）
- 未完了の条件（利用者の決定）: shortfall は常に未完了。match・unregistered・board_count_inconsistent・excess は remaining_quantity > 0 なら未完了
- 計画は `list_plan_items_for_all_lots()` で一括取得する。`list_active_plan_items()` は面1や完了済みを除くので、完成数の計算に必要な組がそろわない

## ロット進捗チェック（`check_lot_progress()`、2026-09-26）

- 日報・月報は対象期間に実績があるロットしか見ないので、(a) 実績の無いロットの不足・未登録を検知できず、(b) 1つのロットに基板名が複数あると代表1件しか見なかった。計画を起点に全アクティブロットを評価する
- `evaluate_all_lot_status()` は、これに cutoff_date と board_structure_cache を足した版（日々の引落一覧用）。check_lot_progress() は変えないため別関数にした

## 仕掛数量の抽出（`build_wip_extraction_rows()`）

- 以前は日報の行（バッチ単位）の値を抽出していて、複数バッチを持つ file_no の仕掛が正しく合算されなかった。file_actuals を土台にする
- 2026-09-28 から引落は `evaluate_lot_status()` の値を使う。以前は shortfall のロットの仕掛が0と計算され、抽出から漏れていた（lot_no=260079）
- 面2の実績がある file_no の面1は除く。面1・面2の実績は連動で一致するので、両方を数えると1枚の基板の仕掛が二重になる
- 代表のバッチ（`_pick_representative_plan_item()`）: 生産予定日が最も新しいもの（手元に残る仕掛の実体に近い）。スナップショットは1行1 kitting_list_no のスキーマなので代表が必要
- shortfall の「未確定」は対象外（実在の kitting_list_no が無い）
