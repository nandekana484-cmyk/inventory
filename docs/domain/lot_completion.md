# 実績の登録とロットの完成数・引落（services/production_service.py・lot_status_service.py・production_report_service.py）

段階5（2026-10-13）で、ロット状態の評価を `services/lot_status_service.py`、日報・月報の表示データを `services/production_report_service.py` に分けた。登録・修正・仕掛数量の抽出は `services/production_service.py` に残っている（移した関数は production_service.py からも re-export している）。

2026-10-09 のコメント軽量化で、コメント・docstring から移した内容。関数名で参照する。
集計キー（2要素・加算）の決定は CANONICAL_DESIGN_DECISIONS.md §5 と BOM_MIGRATION_NOTES.md §4。

## 実績の登録

- 1計画（kitting_list_no・lot_no）＝1レコード、常に上書き。以前は当日分だけを重複・削除の対象にしていたが、日付を問わず対象にした
- `register_daily_result(check_duplicate=True)`: 既存の実績があれば追記せず `DailyResultAlreadyExists` を送出する。（旧方式の CSV 即時取込 `import_production_csv()` は check_duplicate=False だったが、D-186 で削除した。）手入力（`KittingProductionEntryWindow`）は確認ダイアログで既存の有無を先に見せているので、例外を使わず register / overwrite を呼び分ける
- 既存レコードが複数ある（仕様変更前の過去データ）ときは、最も新しい日付のものを使う
- `overwrite_daily_result()` の report_date: None を今日にせず、そのまま `replace_daily_result()` に渡して既存行の日付を引き継ぐ（2026-09-29 の調査で、手で数量を直して再登録するたびに CSV 由来の正しい日付が今日に書き換わる問題が見つかったため）。既存行が無ければ今日
- 反対側の面には実績を書かない（2026-10-19、D-169）。以前は面2の登録で面1へ同じ数量を自動入力していた（`register_opposite_side_daily_result()`、D-113）が、面2があれば面1の実績を計算に使わなくなった（D-167）ので削除した。置き換え前の確認（D-126、`preview_opposite_side_overwrite(s)()`）も削除した
- 履歴（lot_status_history）の記録
  - 登録・上書き・修正・削除のたびに、そのロットの状態を記録する（2026-09-30）。記録に失敗しても本来の処理は失敗させない（`_record_lot_status_snapshot_safely()`。共通フックにはせず、各関数から個別に呼ぶ方針）
  - record_history=False: 一括登録（Shift+S など）で行ごとに記録すると、1件あたり数十 ms（commit の同期コスト）かかり100件で数秒になる。呼び出し元が影響したロットの集合を持ち、最後にロット単位で1回ずつ記録する
  - 修正（`update_daily_result()`）は UPDATE の前に、削除（`delete_daily_result()`）は DELETE の前に対象行を読んで lot_no を特定する（削除後は読めない。修正で前に読むのは、行が既に消えていた場合を検出するため）

## 実績の取り消し（`build_cancellation_preview()`・`apply_cancellation()`、2026-10-15、D-129）

- 取り消すのはその実績1件だけ（反対側の面の実績へは連動しない。D-169）。NG申告は、取り消す計画の分と、面2なら同じロット・ファイルNoの面1の計画の分を取り消す（面2の NG は面1にも加算して保存されている。面1の計画は NG の欄と同じ `find_opposite_side_plan()` で探す）
- NG申告の計画・面が NG展開（scrap_records）で、または取り消す実績の計画・面が仕掛部品展開（wip_scrap_records）で確定済みなら取り消さない
- 登録待ちに戻す行は1行。日付は実績CSVの書式（`to_csv_date_format()`）、製品名は計画の基板名、作業者は実績を登録した作業者ID、取込のバッチは取り消し専用の新しいバッチ（source_file＝`CANCELLED_REGISTRATION_SOURCE`）、CSVの行番号は NULL
- 確定は `models/production.cancel_daily_production()` の1つのトランザクション

## 実績の修正・削除（`build_correction_preview()`・`apply_correction()`）

- 修正・削除するのは選んだ実績1件だけ（2026-10-19、D-169）。以前は反対側の面の実績も連動させていた（D-112、`build_linked_correction_preview()`・`apply_linked_correction()`・`models/production.apply_daily_production_changes()`）が、面1に実績を書かなくなったので削除した
- 戻り値は `{"primary": {prod_log_id・kitting_list_no・lot_no・old_daily_qty・new_daily_qty（削除なら None）・report_date・plan}}`
- 確定の後、ロットの状態を `correction_update`／`correction_delete` として記録する

## ロットの完成数（`_compute_lot_completion()`）

- 完成数＝ロット内の (setup_file_no, production_side) ごとの実績合計の最小値
- 元のロット・ファイルNoに面2の有効な計画があれば、その面1の計画の実績は使わない（2026-10-19、D-167、案X'）。実績は面2まで終わった完成品として入力するため、面2が正。面1と面2の計画を1対1で組にしない（面2が複数バッチでも面2の合計で数える）。面2の計画が無いファイルNo（片面の製品）は面1の実績を使う
  - 以前は面1のキーも完成数の最小値に入れていた。面2の登録で面1へ自動入力した写し（D-113）が、面2が複数バッチのときに同じ面1の計画へ2回入り、後の値で上書きされて完成数が少なく出た（実データで 616629・424159 の2ロット）
- キーは2要素で、代入ではなく加算する。同じファイルNo・面に、kitting_list_no の違う複数のバッチ（例: 実装予定日違い）が同時にアクティブなことがあり（実データで222件）、代入では片方の実績が消える
- order_qty: 以前は先頭行（順序不定）の値を無条件に使っていた。複数の file_no を持つロット301件のうち3件（166248・516526・516626）で、file_no 間の order_qty が食い違っていた。値が1種類ならそれを使い、複数なら先頭行の値を使いつつ order_qty_inconsistent・order_qty_values で知らせる
- 実績の取得は (kitting_list_no, lot_no) の組でまとめて行う。kitting_list_no だけで集計すると、lot_no をまたいで重複する別ロットの実績まで合算してしまう（実データで完成数の取り違えを確認）
- cutoff_date（2026-10-09、日々の引落一覧の払い出し日ベース化）: この関数は絞り込まず、戻り値に記録するだけ。呼び出し元が、その日付までの実績だけの cumulative_by_pair を渡す
- ロットの統合（2026-10-16、D-150）: 統合したロットは `merge_ctx` を受け取り、実績を計画ごとの**元のロットNo**で引く（計画Noを共有する組で二重に足さない）。上の面2の規則も元のロットごとに当てる（D-167。D-151・D-157 の「面2を面1にも数える」は廃止）。統合していないロットは `merge_ctx=None` で従来と同じ計算。詳細は docs/domain/lot_merge.md

## 構成基板数チェックと引落（`_evaluate_lot_status()`）

- 日報・月報（`_build_report_rows()`）・仕掛数量抽出（`build_wip_extraction_rows()`）・ロット進捗チェック（`check_lot_progress()`）・未完了ロットの抽出（`list_incomplete_lots()`）で共通の、唯一の実装（2026-09-28）
- 一本化の経緯: 以前は機能ごとに判定していた。lot_no=260079（構成基板数2に対し実 file_no 数1の shortfall）で、ロット進捗チェックは引落0・仕掛800、日報・月報は引落800・仕掛0、仕掛数量抽出は対象0件、と食い違った
- 判定の手順
  1. `_compute_lot_completion()` で実績ベースの完成数・代表の order_quantity・file_actuals を得る
  2. 元のロット・ファイルNoに面2があればその面1を除いた、ロット全体の file_no の集合（visible_file_nos）を作る
  3. 基板名ごとに構成基板数マスタを引き、未登録の基板名をすべて集める（以前は代表1件だけを見ていて、ほかの基板名の未登録を見逃した。2026-09-26 修正）
  4. status: 登録済みの基板名が無い→unregistered／登録値が複数種類→board_count_inconsistent（比較はしない）／それ以外は file_no の数と比べて、一致→match、足りない→shortfall（shortfall_count）、多い→excess（excess_file_nos）
  5. 引落: `DRAWDOWN_ZERO_STATUSES`（shortfall だけ）なら0、それ以外は実績ベースの完成数
  6. file_no・面ごとの仕掛（file_actual − 引落）と未生産（order_qty − file_actual）を、基板名ごとにまとめる
     - order_qty は、同じ file_no・面に計画No（バッチ）が複数あっても足さない（2026-10-15、D-121）。発注数はロットの値が各計画に同じ値で入っているため。以前は足していて、ロット進捗チェックの発注数・未生産と日々の引落一覧の未生産が計画Noの数だけ膨らんでいた（実データで54ロット）
     - 同じ file_no・面の中で order_qty が食い違うときは、order_qty・not_produced_qty を None にし、order_qty_inconsistent=True を付ける。ロット進捗チェックは空欄にして備考に「発注数不一致」と出す（実データでは0件）。ファイルNoごとに違う（166248・516526）のは、ロット全体の order_qty_inconsistent の扱いのまま
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

lot_no・status・board_count（match/shortfall/excess のみ）・board_count_values（board_count_inconsistent のみ）・visible_file_nos・shortfall_count・excess_file_nos・unregistered_board_names・boards（基板名ごとの board_count と files: setup_file_no・production_side・order_qty・order_qty_inconsistent・file_actual・lot_completed・surplus_qty・not_produced_qty）・missing_file_entries（同じ形、setup_file_no="未確定"）・lot_completed・status_remarks・status_color_category・order_quantity・file_actuals・order_qty_inconsistent・order_qty_values

統合したロット（D-150）の結果にだけ、merged_lot_nos・display_lot_no（「123456（+123456-）」）・aligned_order_qty と、files の各行に source_lot_nos（元のロットNo）が付く。統合していないロットの結果の形は変えない。

## 日報・月報の行（`_build_report_rows()`）

- 以前は「その日・期間に実績がある行だけの最小値」を独自に計算していたため、未生産の file_no がある lot_no=110068 などで誤って「引落済み」になっていた。完成数・引落は `_evaluate_lot_status()` の結果を使う
- 計画が見つからない（ValueError）か、行の file_no・面が file_actuals に無い場合は、判定できないので未完成扱い（引落0、仕掛＝その日の実績、未完了＝発注数）にする。実績はあるデータなので、行は消さない
- lot_no は計画を引き直さず、実績の lot_id を使う（kitting_list_no は lot_no をまたいで重複し、kitting_list_no だけで計画を引くとどちらが返るか分からない）
- 面1の除外: 元のロット・ファイルNoに面2の有効な計画がある面1の行は表示しない（D-8・D-167。完成数の計算と同じ単位）
  - 面1・面2の不整合の警告（D-111、inconsistency_warnings）は、面1の実績を計算に使わなくなったので廃止した（2026-10-19、D-169）
- 構成基板数の振り分け: 未登録→unregistered_board_warnings（実際に未登録の基板名すべて）、不一致→board_count_inconsistency_warnings、不足→「未確定」の仮想行、超過→excess_file_no_warnings。仮想行と警告は、本ループの後にロット単位でまとめて追加する（実績の順では同じロットの行が連続する保証が無いため）
- 仮想行: kitting_list_no=""（画面のダブルクリックは「空なら何もしない」）。数量に None や文字列を入れると、画面の `":.0f"` の書式化で ValueError になるので0にする
- 確認事項・文字色（2026-09-28）: 各行に confirmation_note と status_color_category を付ける。文言と色の定義は services 側の2関数にだけ書き、画面ごとに定義しない
- 戻り値: (report_rows, order_qty_inconsistency_warnings, unregistered_board_warnings, excess_file_no_warnings, board_count_inconsistency_warnings)
  - report_rows の主なキー: seq（表示行だけで1から振り直す）・confirmation_note・status_color_category・status（match/shortfall/excess/unregistered/board_count_inconsistent/None）・has_ng（NG 実績か NG 申告があれば True。仮想行は False）・report_date（仮想行は None）
  - unregistered_board_warnings の file_nos は、ロット全体・面1除外後の distinct な file_no（CSV 出力で使う）

## 未完了ロットの抽出（`list_incomplete_lots()`）

- 未完了の条件は `is_lot_incomplete()` に切り出し、完成済みロットの自動登録の除外（`lot_completion_state()`、D-130）と共有する。`lot_completion_state()` は同じ (ファイルNo, 面) の中で発注数が食い違うロットを完成済みにしない
- `preview_completed_lot_registration()`（登録前の確認の内容）は、その計画に既に実績がある（数量を直す）場合、直した後の合計が発注数を超えて今より増えるときだけ返す（D-133）。生産実績入力の手入力・右クリック・ダブルクリックのどれも、この関数で判定する

- DB 新規作成時の引き継ぎ用。2026-10-01 に `_evaluate_lot_status()` を通すよう変えた。以前は構成基板数を見ない完成数で判定していたため、shortfall のロットを「完了」として引き継ぎから外すおそれがあった（理論上。実データでは未確認）
- 未完了の条件（利用者の決定）: shortfall は常に未完了。match・unregistered・board_count_inconsistent・excess は remaining_quantity > 0 なら未完了
- 計画は `list_plan_items_for_all_lots()` で一括取得する。`list_active_plan_items()` は面1や完了済みを除くので、完成数の計算に必要な組がそろわない

## ロット進捗チェック（`check_lot_progress()`、2026-09-26）

- 日報・月報は対象期間に実績があるロットしか見ないので、(a) 実績の無いロットの不足・未登録を検知できず、(b) 1つのロットに基板名が複数あると代表1件しか見なかった。計画を起点に全アクティブロットを評価する
- `evaluate_all_lot_status()` は、これに cutoff_date と board_structure_cache を足した版（日々の引落一覧用）。check_lot_progress() は変えないため別関数にした
- 発注数の食い違い（2026-10-15、D-127）: ファイルNoによって発注数が違うロットは、全行の備考に「発注数不一致(ファイル間)」、同じファイルNo・面の中で違う行は「発注数不一致(同一ファイル内)」（D-121）。どちらも文字色は赤。文言は `ORDER_QTY_REMARK_*`、色は `progress_color_category`（日々の引落一覧と共有）。実績レポートは `status_remarks`・`status_color_category` を使い続け、発注数の食い違いを出さない
- 不足の内訳（2026-10-20、D-179）: 「不足」のロットの備考に「構成基板数 2 に対し、計画のファイルNo は 1 件（0459：FD-Q10C***ﾋｮｳｼﾞ）」と足す（`format_shortfall_detail()`）。数えるファイルNoは画面に出している行と同じ（面2があれば面1を除く、D-167）。内訳が3件を超えるときは先頭（ファイルNo順）の3件だけを出し、残りを「ほかN件」とまとめる（D-184）。件数（「12 件」）は全件の数のまま
- 面2待ち（2026-10-20、D-180）: 面2待ち（分類 b）の面1があるロットは、全行の備考に「面2待ち（ファイル0766）」と足す（`lot_eval["side2_wait_file_nos"]`、`format_side2_wait_remark()`）。状態の絞り込みに「面2待ち」を足した。状態そのもの（一致・不足など）は変えない

## 仕掛数量の抽出（`build_wip_extraction_rows()`）

- 以前は日報の行（バッチ単位）の値を抽出していて、複数バッチを持つ file_no の仕掛が正しく合算されなかった。file_actuals を土台にする
- 2026-09-28 から引落は `evaluate_lot_status()` の値を使う。以前は shortfall のロットの仕掛が0と計算され、抽出から漏れていた（lot_no=260079）
- 面2の計画がある file_no の面1は、完成数の計算（file_actuals）に入らないので出てこない（D-167。以前はここで除いていた）。両面の製品の仕掛は面2の行だけで、面2の部品表で展開する（面1の部品は入らない。今後の課題 D-171）
- 代表のバッチ（`_pick_representative_plan_item()`）: 生産予定日が最も新しいもの（手元に残る仕掛の実体に近い）。スナップショットは1行1 kitting_list_no のスキーマなので代表が必要
- shortfall の「未確定」は対象外（実在の kitting_list_no が無い）
