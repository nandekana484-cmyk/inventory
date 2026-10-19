# 生産実績入力（ui/kitting/kitting_production_entry.py）の業務ルールと設計メモ

2026-10-09 のコメント軽量化で、`ui/kitting/kitting_production_entry.py` のコメント・docstring から移した内容。
関数名で参照する（行番号は変わりやすいため）。D番号は CANONICAL_DESIGN_DECISIONS.md / CHANGELOG.md。

## 業務ルール

### 実績と NG の日付

- 実績の日付（`production_daily.report_date`）
  - 実績CSV取込から転記した場合は、CSV の払い出し日のまま登録する。手入力の場合は実行日
  - CSV の日付は `"2026/3/18"` のようにスラッシュ区切りでゼロ埋めされていないことがある。`_resolve_csv_report_date()` で必ず `"YYYY-MM-DD"` に正規化する。表記ゆれが残ると、`report_date` の文字列比較による範囲検索（`WHERE report_date >= ? AND report_date <= ?`）が壊れる
  - 解釈できない日付は None として渡し、登録側（`register_daily_result()` / `overwrite_daily_result()`）で実行日を使う
  - 反対側の面への連動登録にも、主たる面と同じ日付を渡す。以前は反対側だけ実行日になる不具合があった（2026-09-29 の調査）
- NG の日付（`ng_declarations.report_date`）は、登録した当日にする（`services/production_entry_service.py` の `register_actual_and_ng()`）

### 1計画＝1レコード

- 実績は「1計画（kitting_list_no・lot_no）＝1レコード、常に上書き」（`models.production.replace_daily_result()`）
- そのため記入欄の初期値は日付を問わずに検索し、複数件あれば最も新しい日付の値を使う（`_load_current_daily_qty()`。複数件あるのは仕様変更前の過去データ）
- NG 申告も「1計画・面＝1レコード」で、日付を問わない現在値を使う（`_setup_ng_side_ui()`）

### NG の保存値と表示（面1・面2）

- 保存値（`services/production_entry_service.py` の `compute_ng_save_qty()`。画面の `_compute_ng_save_qty()` はこれを呼ぶだけ）
  - 面1の保存値 ＝ 面1欄の入力値 ＋ 面2欄の入力値（片方が空でももう片方の値が入る。例: 面1欄=空・面2欄=5 → 面1へ5）
  - 面2の保存値 ＝ 面2欄の入力値のみ（面1欄の値は面2に影響しない）
  - 合計が0の面は保存しない（既存の NG 申告に触れない）
  - 過去の保存値には加算しない。今回の入力だけが正（`_validate_ng_inputs()`）
- 入力欄に表示する値（`_setup_ng_side_ui()`）
  - 面2: 保存値をそのまま表示する
  - 面1: 「面1固有分」＝面1保存値 − 面2保存値 を表示する（0以下なら空欄）。面1の保存値には面2分が含まれているので、そのまま表示すると、何も変えずに再登録したとき面2分が二重に加算される。面2の保存値は常に面2欄だけなので、この引き算で面1固有分を正しく復元できる
- NG を入力せずに実績だけを登録してもよい

### 実績CSVの保留行を消すタイミング

- 実績CSVステージング一覧から転記した行は、**実績の登録が成功したら**一覧から消す。`_perform_registration()` が `register_actual_and_ng()` の戻り値（`actual_registered`）を見て `_pending_csv_row_removal` を呼ぶ
- NG 申告の成否は問わない（CSV 行が表すのは実績数量だけで、NG は別枠のため）
- 書き込みの順序: 実績 → 操作履歴 → NG（ここまで `register_actual_and_ng()`）→ 保留行の削除（画面側）。段階3a（2026-10-13）で、保留行の削除を最後にした。反対側の面への書き込みは 2026-10-19 に削除した（D-169）
- `search_plan()` は `_pending_csv_row_removal`・`_pending_csv_report_date` をクリアする。CSV を経ずに別の計画へ切り替えたとき、古い CSV 行の情報が無関係な登録に使われないようにするため。CSV 経由のときは、ステージング一覧から呼ばれる `load_staged_row()` が `search_plan()` の直後に改めてセットする

### 反対側の面の実績

- 反対側の面には実績を書かない（2026-10-19、D-169）。実績は面2まで終わった完成品として入力し、面2があれば面1の実績は計算に使わない（D-167）。以前の面1への自動入力（D-113）・修正と削除の連動（D-112）は削除した
- 修正・削除時（`ActualCorrectionWindow`）: 選んだ実績だけを変える。確定前に必ず確認ダイアログを出す
- NG の欄（面1・面2）・保存の形・NG展開は変えていない（面2の NG は面1にも加算して保存する）

### 面1の計画を登録の対象から外す（2026-10-19、D-170）

- 元のロット・ファイルNoに面2の有効な計画がある面1の計画は、無いものとして扱う。判定は `services/production_service.side2_plans_hiding()`、登録を受け付けるかは `side1_registration_block()`（どの経路もこれで判定する）
- 経路ごとの扱い
  - 計画一覧: もともと出さない（`list_active_plan_items()`、D-8）
  - 計画の検索（`search_plan()`）: 面1が当たったら、面2の計画が1件ならそれに切り替えて知らせる。面2が複数なら、どれかを決められないので計画一覧から選んでもらう（未選択に戻す）
  - 本日の実績履歴: 面2の有効な計画がある面1の行は出さない（以前は、同じ日に面2の実績があるときだけ隠していた）。ダブルクリックは `search_plan()` を通るので、面2に切り替わる
  - 登録（`_start_registration()`）: 登録の確認の前に断る
  - サービスの入口（`register_daily_result()`・`overwrite_daily_result()`）: 二重の安全策として `Side1RegistrationRefused` を送出する
- 「面2待ち」（生産面マスターでは面2があるのに、面2の計画がまだ無い。分類 b）の面1: どの経路でも登録を受け付けず、「このロットには面2の計画があるはずです。面2の計画を取り込んでから、面2に登録してください」と案内する（`SIDE2_WAIT_MESSAGE`）。計画一覧には今までどおり色付きで出す
- 片面の製品（a）は今までどおり面1に登録できる。生産面マスター未登録（c）は今までどおり確認のうえで登録できる
- 実績修正の画面（`ActualCorrectionWindow`）: 面2がある面1の計画で開かれたとき（古い版で入った写しなど）は、読み取り専用にして（修正・削除・取り消しを押せない）、面2の計画で開くよう案内する

### 登録確認での不一致チェック

- 「実績＋NG」を、その面の予定生産数（`planned_qty`）と比べる。発注数（`order_qty`、ロット全体の注文数）ではない。1面分の合計と比べる対象として予定生産数の方が実態に合うため（`_build_registration_preview()`）
- 登録後の実績は必ず入力した数量になる（1計画＝1レコードで上書き）ので、DB に書き込む前に判定できる。面1の行も、面2に入力した実績の数量と比べる

### 計画一覧の表示

- 「入力済みを隠す」（`apply_plan_filters()`）: 行の (file_no, 面) の実績合計（`calculate_lot_completion()` の `file_actuals`）が、その行の発注数以上なら隠す。ロット完成数（全 file_no・面の最小値）と比べると、実績0のロットが 0 >= 0 で「完了」と誤判定される（実データで確認、2026-09-24 修正）
- 元のロット・ファイルNoに面2の計画がある場合、面1は無いものとして扱うので、計画情報の「基板別実績」と本日の実績ログに表示しない（D-8・D-170、`list_active_plan_items()` と同じ考え方）
- 構成基板数の照合（`search_plan()`）: 構成基板数マスタの値と、表示中の file_no の数（面2があれば面1を除いた数）が違えば赤字にする。マスタの登録漏れや計画データの異常を疑うため（2026-09-24 追加）。未登録も赤字
- kitting_list_no は lot_no をまたいで重複する（実データで478件）。計画の特定・行の対応づけには常に (kitting_list_no, lot_no) を使う（`docs/domain/rule_index.md` R1）

### 実績CSV取込

- 「確認・選択・転記」方式: CSV を解析して保留行（`pending_csv_import_rows`）に保存するだけで、`production_daily` には書き込まない。登録は候補を選んで記入欄に転記した後、通常の登録フローで行う。以前は即時登録（`import_production_csv()`）だったが、内容を確認せずに登録されるのを避ける方針に変えた
- 計画CSVの誤投入チェック（`_confirm_csv_format_for_production_import()`）: キッティング計画CSVを実績CSV取込に読み込ませ、払い出し日が空欄の行が457件混入した事故（2026-09-24）への対策。自フォーマットの固有列の欠如と、他フォーマットの固有列の混入を検知する。強制的には止めない
- 未処理の保留行が残っているときは、続けて取り込む前に確認する。取込時の上書きルール（同じ lot_no＋製品名は新しい行で置き換える）は変えない

## 画面の設計メモ

### ウインドウサイズ（`KittingProductionEntryWindow.__init__()`）

- 高さ 700px: 以前の 850px では画面からはみ出す環境があった。1150x850 時点の実測は info_frame 289px・entry_frame 147px・hist_frame 265px（pack の pady 上下10pxを含む）。1150x700 で確認したところ、info_frame・entry_frame は変わらず、hist_frame だけが 265px→234px（約1行分）に縮み、登録ボタンなどはウインドウ内に収まった。hist_frame が left_frame で唯一の expand=True 要素なので、縮小分を吸収する
- 幅 1350px（2026-10-07）: 以前の 1150px では、右ペイン（計画一覧・絞り込み）の必要幅 965px に対し 484px しか確保できず、481px 不足していた。左ペインの初期幅 300px＋右ペインの必要幅 985px（余裕込み）＋分割バーと余白から決めた
- 開いた直後に最大化する。計画一覧を別スレッドで読み込んだ後に生成されるので、「小さく出てから広がる」動きにはならない

### 左右のペイン（`create_widgets()`）

- 以前は pack(side=LEFT/RIGHT, expand=True) で、両ペインの自然要求幅の比率で幅が決まり、最大化しても右ペインがほとんど広がらなかった（実測: 左1037px・右484px）。PanedWindow にして初期サッシュ位置を明示した
- `_RIGHT_PANE_MIN_WIDTH_PX = 985`: plan_filter_frame.winfo_reqwidth() の実測 965px に、文言追加やフォント差の余裕を加えた値
- `_LEFT_PANE_INITIAL_WIDTH_PX = 300`: info_frame・entry_frame の実測 reqwidth（214px/198px）が切れない最小限の値

### pack の順序

- Tk の pack は、side に関係なく pack を呼んだ順に領域（cavity）を取る。計画一覧は ボタン行→水平スクロールバー→垂直スクロールバー→Treeview の順に pack する。逆にすると水平スクロールバーがボタン行の下に離れて表示される（以前の実装で起きていた）
- 同じパターンは ui/production/production_import_staging_window.py・ui/ng_wip/ng_input_window.py・ui/ng_wip/wip_expansion_window.py にもある（`docs/fragile_spots.md` 4）

### 実績CSVステージング一覧との連携

- ステージング一覧（`ProductionImportStagingWindow`）は、本画面の公開メソッド `load_staged_row()`（転記）・`refresh_after_external_registration()`（外部で登録した後の画面更新）・`confirm_overwrite_if_existing()` を使う（段階3b。以前は `search_plan()`・`entry_daily_qty`・`_pending_csv_row_removal` などを直接使い、さらに前は候補選択ダイアログ `select_plan_candidate_by_lot()` とコールバック経由だった）。そのため「実績CSV取込状況」ボタンはメインメニューから本画面へ移した
- 登録後のフォーカス（D-84、2026-10-06）: lift() は重ね順を変えるだけで、キーボードフォーカスは移らない。以前は登録後に実績記入欄へフォーカスを戻していたため、一覧が手前に見えても一覧で押した Shift+S が記入欄への「s」の入力になっていた。登録完了時に一覧を lift()・focus_force() し、一覧の Treeview にフォーカスを渡す。常に最前面（topmost）は、後に開くダイアログを隠すので使わない。最小化されていれば deiconify() してから戻す
- 一覧を開くとき、既に開いていれば多重に開かない。直前の取込の「登録済み」行は `add_already_registered_rows()` で追記する

### その他

- 計画一覧の選択はデバウンスする（200ms）。矢印キー連打で DB 検索が積み重なるのを避けるため。記入欄へのフォーカス移動はデバウンス確定後に行う（選択イベントの時点で移すと、一覧の矢印キー操作が記入欄に取られる）
- 実績CSVの解析は別スレッドで行う（LoadingWindow → threading.Thread(daemon=True) → queue.Queue → after(200) でポーリング）。ui/kitting/kitting_plan_import.py・ui/main_window.py と同じパターン
- 登録直後の計画一覧の部分更新（`_refresh_plan_list_for_lot()`）: `list_active_plan_items(lot_no=...)` は部分一致（LIKE）なので、lot_no の完全一致で絞り直す（例: "100075" で "1100075" も取れてしまう）。絞り込みで非表示の行も `_all_plan_rows` は更新する。既存 iid への set() だけなので、並び順と選択状態は変わらない
- 最小化中のウインドウに transient のポップアップ・ダイアログを出すと、表示されないまま入力を握る。出す前に deiconify() する（UI_WORKFLOW_FIXES_NOTES.md）
