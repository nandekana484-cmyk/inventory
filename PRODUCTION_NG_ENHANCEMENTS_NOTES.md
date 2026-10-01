# PRODUCTION_NG_ENHANCEMENTS_NOTES.md

## 1. 概要

**目的**：生産実績入力画面（`ui/kitting_production_entry.py`）の計画一覧まわりの改善（横スクロール修正・完了済み計画のデフォルト表示化・期間絞り込み・面1/面2のNG入力）と、NG（仕損）入力画面（`ui/ng_input_window.py`）まわりの拡張（計画外登録・NG一覧・再展開上書き・全選択/全解除・レポート出力）について、発見された重要な事実・決定事項・実施した修正をまとめ、次にこのプロジェクトを触る人（将来の自分を含む）が同じ調査・議論をやり直さずに済むようにする。

**対象読者**：`inventory_app`（部品在庫管理アプリ）のコードに触れる開発者。

**作成日**：2026-08-30

> **本ドキュメントは2拠点並行開発を前提としている。** セクション3「グループ別の実施内容」の「反映済み/未反映」判定は、**本ドキュメントを作成した時点でこのリポジトリを開いていた環境**でのみ確認したものであり、もう一方の拠点での実装状況は確認していない。実装状況の最終確認は、マージ時に両拠点で改めて突き合わせることを推奨する。

---

## 2. 発見された重要な事実

### 消費数量(ng_qty)の実態(重要な誤解の解消)

当初、`scrap_records.ng_qty`が「消費数量(NG枚数×員数)」であり「申告NG枚数」とは単位が異なるため、製品NGレポートに表示すべき値が不明という問題提起があった。ユーザーへの確認の結果、以下が判明した:

基板1枚が製品何台分に相当するか(丁数)により、ファイルNo別BOMデータ(基板1枚で使う部品数)を丁数で割ったものが「1製品あたりの部品使用数」になる。完成品数・注文数・NG数の入力は台数(製品数)単位だが、部品はこれを1製品あたりの使用数に変換(展開)しなければならない。これが「NGの展開」の意味であり、**「NG数」とは1製品あたりの使用部品数のことである**。K記号の基板部品は1枚として扱う。

結論:`ng_qty`(消費数量)は最初から求めていた値そのものであり、単位不揃いという当初の懸念は誤りだった。製品NGレポートには`ng_qty`の合計をそのまま表示すればよい。

### list_active_plan_items()の「1回目除外」ロジックがNG一覧にも影響する

`list_active_plan_items()`内の「2回目計画がある場合は1回目を除外する」ロジック(以前のBOM_MIGRATION_NOTES.mdでも報告済み)は、この関数の内部でのみ適用されるビジネスルールで、`find_plan_item_by_kitting_no()`や`get_latest_plan_by_kitting_no()`には適用されない。NG一覧・レポート画面で計画詳細(ロットNo・基板名)を結合する際は、`list_active_plan_items()`を使うと面1が欠落するため、`find_plan_item_by_kitting_no()`を個別に呼ぶ方式を採用した。

### 完了済み計画のデフォルト表示化(P4)は以前の意図的設計を覆す変更

以前(BOM基盤シリーズ)、「実績が発注数に到達した計画は一覧から除外する」ことは意図的な設計と確認されていた。今回、ユーザーの要望によりこれを覆し、デフォルトで完了済みも表示、「入力済みを隠す」チェックボックスでオプトイン的に隠せる仕様に変更した。

重要な制約:`find_matching_plan_items()`(実績CSV自動取込用)は完了済み除外を前提にした一意特定ロジックであり、この関数の挙動は変更していない(`include_completed`引数はデフォルトFalseのまま、CSV自動取込側は明示的に指定せず現状維持)。完了済み表示のON/OFFはUI(計画一覧)側だけの関心事に留めている。

### 横スクロールバーの表示位置バグ

以前(BOM基盤シリーズ4回目)、計画一覧に`hsb_plan`(水平スクロールバー)を追加していたが、`pack`の順序の問題で、Treeviewから視覚的に切り離されたウィンドウ最下端に表示されており、ユーザーからは「スクロールバーが無い」ように見えていた。「更新」ボタンを先にpackしてから`hsb_plan`をTreeviewの直下に配置する順序に修正した。

### 反対側の面(production_side)を一意に特定できないケースが多数存在

同一lot_no・setup_file_noで両面が存在するグループのうち85グループは、片方の面に複数の異なるkitting_list_no(日付違いのバッチ)が存在し、単純な「反対側を1件引く」ロジックでは一意に決まらない。kitting_list_noの命名規則(`{file_no}-{side}-{種別}-{日付}-{連番}`)を使った文字列置換でのペア取得も、side=1の計画1369件中786件(約57%)しか一致しなかった。

対応方針:`find_opposite_side_plan()`で、複数候補がある場合は`plan_start_datetime`が最も近いものを自動選択する。

---

## 3. グループ別の実施内容

### グループP-view: 横スクロール修正 + 完了済み計画のデフォルト表示化

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| P-view-1 | `ui/kitting_production_entry.py` | pack順序を「更新ボタン→hsb_plan→vsb_plan→tree_plan_list」に変更し、水平スクロールバーがTreeview直下に視覚的に配置されるよう修正 | **反映済み**（`hsb_plan`関連のpack順コメント・実装を確認） |
| P-view-2 | `models/kitting_plan.py` | `list_active_plan_items()`に`include_completed: bool = False`引数を追加。`find_matching_plan_items()`は指定なし(デフォルトのまま、完了済み除外を維持) | **反映済み**（`include_completed`引数・`actual_qty >= order_qty`判定を確認） |
| P-view-3 | `ui/kitting_production_entry.py` | `_fetch_plan_list_rows()`は`include_completed=True`で呼ぶよう変更(常に完了済み込みで取得) | **反映済み**（`list_active_plan_items(include_completed=True)`呼び出しを確認） |
| P-view-4 | `ui/kitting_production_entry.py` | 計画一覧の絞り込みエリアに「入力済みを隠す」チェックボックス(デフォルトOFF)を追加。ONの場合のみ、`order_qty`/`actual_qty`を突き合わせて完了済み行を追加除外する処理を、既存の述語ベースのフィルタとは別立てで実装(列をまたいだ判定のため) | **反映済み**（`_hide_completed_var`・「入力済みを隠す」チェックボックスを確認） |

### グループP-date: 計画一覧への期間絞り込み(カレンダーピッカー)

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| P-date-1 | `ui/kitting_production_entry.py` | 「実装開始予定日」フィルタを、テキスト入力から`tkcalendar.DateEntry`×2(開始日・終了日)による期間指定に変更。既存の日報・月報画面と同じ形式(`date_pattern="yyyy-mm-dd", locale="ja_JP"`) | **反映済み**（`_add_plan_date_range_filter()`・`DateEntry`インポートを確認） |
| P-date-2 | `ui/kitting_production_entry.py` | `plan_start_datetime`の実データ形式("YYYY/MM/DD HH:MM:SS"、スラッシュ区切り+時刻付き)とDateEntryの出力形式("YYYY-MM-DD"、ハイフン区切り)の差異を、先頭10文字を取り出しハイフン→スラッシュ変換した上で文字列比較する形で吸収 | **反映済み**（`_plan_date_range_predicate()`を確認） |
| P-date-3 | `ui/kitting_production_entry.py` | 既存の述語ベースのフィルタ機構(`_plan_filter_predicates()`)にそのまま統合。ソート機能とも問題なく共存 | **反映済み** |

### グループP-ng: 実績入力欄への面1/面2 NG入力 + 不一致警告

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| P-ng-1 | `models/kitting_plan.py` | `find_opposite_side_plan(lot_no, setup_file_no, current_side, current_plan_start_datetime)`(新規):`COALESCE(is_active,1)=1`でアクティブな行のみ対象、反対側のproduction_sideを検索。0件→None、1件→そのまま、複数件→`plan_start_datetime`が最も近いものを自動選択(パース不能・未指定時は昇順先頭にフォールバック) | **反映済み**（関数定義・複数候補時の`diff_seconds`ソートを確認） |
| P-ng-2 | `services/production_service.py` | `search_plan_by_kitting_no()`の戻り値に`plan_start_datetime`を追加(反対側検索の基準日時として必要なため) | **反映済み** |
| P-ng-3 | `ui/kitting_production_entry.py` | 「本日の生産実績」欄の下に「本日のNG(仕損)数量」枠を追加、面1・面2固定2行のEntry+「NG登録」ボタン。計画選択時に`find_opposite_side_plan()`を呼び、反対側が無ければEntryを`state=tk.DISABLED`+空欄化 | **反映済み**（`_ng_side_entries`・`_setup_ng_side_ui()`を確認） |
| P-ng-4 | `ui/kitting_production_entry.py` | NG登録時、面ごとに個別に`expand_scrap_to_parts()`でBOM展開。実績+NG数量とorder_qtyの不一致は面ごとに独立して警告(両面不一致なら両方言及)、警告のみで登録continueはブロックしない | **反映済み**（`_warn_ng_quantity_mismatch()`を確認） |
| P-ng-5 | `ui/kitting_production_entry.py`, `ui/checkable_treeview.py` | **追加対応**:確認ステップ(部品確認ダイアログ)を追加。`CheckableTreeview`(新規共通コンポーネント、先頭列に☑/☐、`select_all()`/`deselect_all()`/`get_checked_iids()`/`get_row_values()`を提供)を使い、面1・面2それぞれのセクションを1つのダイアログ内に表示。デフォルト全選択、チェック済み行のみ登録 | **反映済み**（`_open_ng_confirm_dialog()`・`CheckableTreeview`定義を確認） |

**技術的な留意点**：`ttk.Entry`は`state=DISABLED`のまま`insert()`/`delete()`しても例外を出さず黙って無視される(前回内容が残る)ため、内容変更時は必ず一旦NORMALに戻してから操作し、その後必要ならDISABLEDに戻す実装にしている。

### グループN-basic: ファイルNo入力(計画外対応)・NG一覧・再展開上書き

#### N1: ファイルNo入力欄+計画外登録対応

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| N1-1 | `db/migration_008_add_scrap_records_is_unplanned.py` | (新規、実DB適用済み):`scrap_records`に`is_unplanned INTEGER NOT NULL DEFAULT 0`を追加。テーブル未作成環境にも対応(先に有無を確認し、無ければ列込みで新規作成) | **反映済み**（ファイル存在・実DB(`inventory.db`)への適用実績を確認） |
| N1-2 | `models/scrap_records.py` | `save_scrap_record()`に`is_unplanned: bool = False`引数を追加 | **反映済み** |
| N1-3 | `ui/ng_input_window.py` | ファイルNo.入力欄+生産面コンボボックスを追加。モード切替は「キッティングリストNo.欄に値があればそちら優先、空欄ならファイルNo.+生産面を使う」という単純な優先方式 | **反映済み**（`entry_file_no`・`combo_side`・`on_expand()`の分岐を確認） |
| N1-4 | `ui/ng_input_window.py` | 計画外の場合、`kitting_list_no`列には`file_no`をそのまま流用(実在の命名規則`{file_no}-{面}-{種別}-{日付}-{連番}`とは形が異なるため実データと衝突しない、`is_unplanned`フラグで区別できるため値自体に意味を持たせる必要がない、という理由) | **反映済み**（`_expand_from_file_no()`を確認） |
| N1-5 | `services/bom_service.py`（既存コード） | BOM層(`expand_scrap_to_parts()`等)は元々`kitting_list_no`に依存しない設計だったため、計画外でもBOM展開自体は問題なく実行できた | **反映済み**（既存設計の確認のみ、コード変更なし） |

#### N2/N4: NG一覧(右ペイン)

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| N2-1 | `models/scrap_records.py` | `list_scrap_summary_by_kitting_no()`(新規):`scrap_records`を`kitting_list_no`単位でGROUP BY集計。`file_no`・`production_side`・`is_unplanned`・`part_count`(COUNT DISTINCT part_no)・`record_count`・`last_report_date`・`total_ng_qty`を返す。計画あり・計画外どちらも区別なく含む | **反映済み** |
| N2-2 | `ui/ng_input_window.py` | `container→left_frame(既存要素)/right_frame(NG一覧)`の左右分割構造に再構成(720x640→1150x600に拡大) | **反映済み**（`_create_ng_list_widgets()`・ウィンドウサイズを確認） |
| N4-1 | `ui/ng_input_window.py` | NG一覧は計画あり(is_unplanned=0)・計画外(is_unplanned=1)の両方を含める方針で確定(ユーザー決定)。計画あり行のみ`find_plan_item_by_kitting_no()`で個別にロットNo・基板名を補完(`list_active_plan_items()`は使わない、1回目除外ロジックの影響を避けるため) | **反映済み**（`_fetch_ng_list_rows()`を確認） |
| N4-2 | `ui/ng_input_window.py` | フィルタ・ソート・スクロールバーは、`ui/kitting_production_entry.py`の計画一覧と同じ設計を`_ng_`接頭辞のメソッド群としてコピー&適応(共通クラスへの切り出しは行っていない) | **反映済み**（`_add_ng_filter_entry`等`_ng_`接頭辞メソッド群を確認） |

#### N3: 再展開・上書き(delete-then-insert)

業務ルールの確認:同じキッティングNo.が同じ日に複数回に分けて生産されることは無いものとし、後日の入力は前回の訂正として扱う、とユーザーが確認。この結果、**同じkitting_list_noへの再展開・登録は、その計画に紐づく既存レコードを全て(日付問わず)削除してから新しい内容で登録し直す**方式を採用(batch_id等の複雑な仕組みは不要と判断)。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| N3-1 | `models/scrap_records.py` | `replace_scrap_records(kitting_list_no, file_no, side, records, report_date, is_unplanned=False)`(新規):指定`kitting_list_no`の既存`scrap_records`をDELETEしてから`records`をINSERTし直す。1コネクション・1トランザクションで実行(例外時は自動ロールバック)。既存レコード0件でも同じロジックがそのまま動作 | **反映済み** |
| N3-2 | `ui/ng_input_window.py` | NG一覧の行をダブルクリックすると、対応する検索欄(キッティングNo. または ファイルNo.+面)に値が反映され、自動的に展開が実行される | **反映済み**（`on_ng_list_double_click()`を確認） |
| N3-3 | `ui/ng_input_window.py` | 既存レコードがある状態での再登録時は確認ダイアログ(「既存のNG登録内容(N件)を置き換えます。よろしいですか」)を表示。初回登録(既存レコード無し)は確認なしでそのまま登録 | **反映済み**（`on_register()`内の`askyesno`呼び出しを確認） |

### グループN-misc: 全選択/全解除(N6)

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| N6-1 | `ui/checkable_treeview.py` | `CheckableTreeview.clear()`(新規追加):全行削除+内部チェック状態辞書もリセット。「展開」操作のたびに一覧を作り直す`ui/ng_input_window.py`のような用途で必要になった(既存の`ui/kitting_production_entry.py`側の使い方には影響しない) | **反映済み** |
| N6-2 | `ui/ng_input_window.py` | 部品一覧を`ttk.Treeview`(selectmode="extended")から`CheckableTreeview`に置き換え。「全選択」「全解除」ボタンを追加。デフォルト全選択状態 | **反映済み**（`self.tree = CheckableTreeview(...)`・全選択/全解除ボタンを確認） |

### グループN-report: レポート出力画面(N7)

#### 既存パターンの流用

`ui/daily_report_window.py`の`build_daily_report_pdf()`(reportlab使用、headers・row_to_valuesを引数で差し替え可能)、`ReportPreviewWindow`(Tkinter自前描画によるプレビュー)、CSV出力(utf-8-sig)、印刷(`os.startfile(path, "print")`、Windows専用API経由でOSに印刷を委任)を、そのまま関数引数の差し替えだけで転用した。新規ライブラリ導入は不要だった。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| N7-1 | `ui/product_ng_report_window.py` | (新規)製品NGレポート。列:キッティングNo・ファイルNo・ロットNo・NG数量(`total_ng_qty`をそのまま表示、上記「消費数量の実態」の結論通り単位変換は不要)。計画あり・計画外どちらも含む(計画外はロットNo空欄) | **反映済み**（ファイル存在・列構成を確認） |
| N7-2 | `ui/product_ng_report_window.py` | 行のダブルクリックで`NgInputWindow`を開き該当計画を自動展開(数量変更はこの画面では行わず、NG入力画面での再展開に委ねる、というユーザー決定) | **反映済み**（`on_row_double_click()`を確認） |
| N7-3 | `models/scrap_records.py` | `query_scrap_totals_range(from_date, to_date)`(新規):期間指定版。既存の`query_scrap_totals()`(全期間、在庫差異レポート専用)は変更していない | **反映済み**（両関数の共存を確認） |
| N7-4 | `ui/parts_ng_report_window.py` | (新規)96NGレポート。月報画面と同じ形式(DateEntry×2)で期間指定。列:96コード・数量(SUM(ng_qty) GROUP BY part_no) | **反映済み**（ファイル存在・期間指定UIを確認） |

---

## 4. kitting_list_noの一意性問題(重大バグ、実データの478件に影響)

### 発見の経緯

「実績入力で同じキッティングNoだと実績が入力されてしまう」という報告から調査した結果、実は「**同じkitting_list_noが複数の異なるlot_noにまたがって存在する**」ことが原因と判明した。実DBで**478件**のkitting_list_noが複数lot_noにまたがっていた(is_active=1に限定しても同数)。

具体例:`kitting_list_no='0002-1-K-260727-01'`が、`lot_no=277688`(board_name='BL-185SO(7031)')と`lot_no=317564`(board_name='BL-180SO(7030)')の両方に存在する。

**業務ルール確認**:「1つのキッティングリストNo.に複数ロットをまとめて生産することがある」のは正常な業務パターン。`kitting_list_no`単体ではなく**`(kitting_list_no, lot_no)`の組み合わせ**で初めて1つの計画(製品)を一意に識別できる、と確定した。

### 実害の実例

`production_daily`の実績のうち、`kitting_list_no='0002-2-K-260803-03'`(`lot_no=277688`に本日500登録)について、`calculate_lot_completion('277688')`が誤って0を返し、`calculate_lot_completion('317564')`(実績登録していないロット)が誤って500を返す、という**完全な取り違え**が実データで発生していた。

### 修正内容(3段階)

1. **production_dailyの集計をlot_no単位に修正**:`get_app_cumulative_qty()`/`get_app_cumulative_qty_bulk()`/`list_daily_production_by_kitting_no()`/`replace_daily_result()`に`lot_no`条件を追加。`calculate_lot_completion()`も`get_app_cumulative_qty_bulk()`に`lot_no`を渡すよう修正。`list_active_plan_items()`も同様の巻き込みが判明し合わせて修正。CSV取込側の位置引数ズレも発見・修正。`list_plan_items_by_lot()`に`is_active=1`フィルタも追加(呼び出し元が`calculate_lot_completion()`のみと確認済み。BOM_MIGRATION_NOTES.md §4とも関連)。
2. **scrap_records・ng_declarationsにlot_no列を追加**(`db/migration_010_add_lot_no_to_ng_tables.py`、実DB適用済み):両テーブルの関連関数に`lot_no`を条件・保存対象として追加。`query_scrap_totals()`(在庫差異レポート、全期間・全ロット通算)は意図的に`lot_no`条件を加えない(96コード単位の全体消費実数が必要なため)。
3. **lot_noを保持しているのに渡していない4箇所の修正+複数候補選択UI**:計画一覧の行選択・履歴行ダブルクリック・NG一覧の行ダブルクリック・製品NGレポートの行ダブルクリック、いずれも既に判明している`lot_no`を検索処理に渡すよう修正。`ui/plan_candidate_dialog.py`(新規、共通コンポーネント)で、キッティングNo.のみでの検索時に複数候補があればユーザーに選択させるダイアログ(`lot_no`・基板名・`order_qty`等を一覧表示)を実装。

---

## 5. 実績・NG申告の上書きルールの再設計(「日付問わず1計画1レコード」への統一)

### 業務ルール確認

同じロットを数日に分けて生産する場合、各日の生産は**別々のkitting_list_no(別の計画)**として立てられる(同じkitting_list_noを日をまたいで使い回すことはない)。そのため、1つのkitting_list_noの中で日をまたいで実績を積み上げる必要は無く、訂正は上書きで十分。ロット全体が注文数に達しているかは`calculate_lot_completion(lot_no)`が複数のkitting_list_noを横断して(各計画の完成数の最小値を取って)判断する。

CSV取込・手動入力のどちらが優先されるかについては「**後から入力された方が正**」というルールで統一。

### 確定した設計

- `production_daily`:`replace_daily_result_for_today()` → `replace_daily_result()`に改名。`report_date`条件を削除し、`(kitting_list_no, lot_no)`のみで一意に上書き(日付問わず常に1レコード)。
- `ng_declarations`:`save_ng_declaration()`も同様に`report_date`条件を削除、全期間で1レコード化。
- CSV自動取込(`import_production_csv()`)も同じ上書きルールに従う(`check_duplicate=True`)。面1/面2連動ロジックも新設共通関数`register_opposite_side_daily_result()`としてUI・CSV取込両方から呼べる形に。
- `get_ng_declaration()`も全期間検索に変更(以前「当日限定で過去日を拾えない」バグがあり修正)。
- `_current_daily_qty_sum()`(旧`_today_daily_qty_sum()`)も全期間検索に修正。
- `_load_current_daily_qty()`(旧`_load_today_daily_qty()`)も同様に修正(過去日付のレコードでもプリフィルされるように)。

### 面1/面2の連動ルール(業務ルール、重要)

面1(先行面)でNGになった基板は面2の工程に進まない(面2部品は未消費)。そのため:
- **面1NG申告 → 面1のみNG登録**
- **面2NG申告 → 面1・面2両方をNG登録**(面2に到達した時点で面1は完了・消費済みのため)

NG連動計算式:**面1保存値 = 面1欄入力値 + 面2欄入力値、面2保存値 = 面2欄入力値のみ**。過去の保存値には一切加算しない(都度の入力が全てを置き換える、合算ではなく上書き)。

二重加算対策:面1欄のプリフィルは「保存値(面1) − 保存値(面2)」で計算する固定点計算により、画面を開き直して何も変えず再登録しても値が増え続けないことを検証済み。

実績登録も同様に面2登録時、面1にも自動連動登録される(`find_opposite_side_plan()`で反対側を解決)。面1側で当日重複が見つかっても、面2側で既に確認済みのため自動上書き(確認ダイアログなし)。

(NG一覧・製品NGレポートからの再展開時の「delete-then-insert」方式(グループN3、上記§3参照)とは対象が異なる:こちらは**申告・実績(枚数)**の上書きルール、N3は**展開済みscrap_records(96コード単位の部品明細)**の洗い替えルール。)

**関連する重大バグ（2026-09-16発見・修正）**：本節で確立した`calculate_lot_completion()`（ファイルNo×面単位で実績を合算し最小値をロットの完成数とする、正しいロジック）が、生産実績入力画面では使われていた一方、**日報・月報（`_build_report_rows()`）では全く別の独自ロジックが使われており、面1/面2を含む複数ファイルNoの実績が正しく合算されず「揃っていないのに引落されてしまう」不具合があった**。詳細は§14参照。

---

## 6. 未対応・将来の検討事項

- ~~`scrap_records`向けの1行単位の修正・削除機能(`update_scrap_record()`/`delete_scrap_record()`)は実装していない(ユーザー決定により、kitting_list_no単位の洗い替え(`replace_scrap_records()`)で運用する方針としたため)。~~ → **方針転換し実装済み（2026-09-15、§13参照）**。`scrap_records`・`wip_scrap_records`双方に1行単位のUPDATE/DELETE関数と、専用の個別修正画面（`ScrapCorrectionWindow`/`WipScrapCorrectionWindow`）を追加した。グループ単位の洗い替え（`replace_scrap_records()`/`save_wip_scrap_records()`）自体は廃止しておらず、両者は共存する（個別修正は再展開・再登録で上書きされ得る点に注意、§13参照）。
- **NG一覧・仕掛一覧の一括展開・登録機能** → **完了**（NG一覧：2026-09-14、仕掛一覧：2026-09-15、§12参照）。仕掛展開画面にも同じ設計で実装済み（NG入力画面との重要な違い：`wip_board_snapshot`の行は既にfile_no・面・lot_no・mounting_lineを保持しているため計画候補の曖昧さ自体が存在せず、曖昧さが発生し得るのは実装ラインが複数のケースのみ）。
- NG一覧のフィルタ・ソート機能は、計画一覧のロジックをコピー&適応した実装であり、共通コンポーネントとしては切り出していない(将来、両者の挙動を同時に変更する必要がある場合は両方修正が必要な点に注意)。
- `find_opposite_side_plan()`の複数候補時「最も近いplan_start_datetimeを自動選択」は、業務上本当に正しい組み合わせを保証するものではない(日時が近いというだけの推測)。誤った組み合わせになるケースがないか、実運用で注意が必要。
- **ロード画面（`LoadingWindow`＋非同期パターン）の追加** → **完了（2026-09-11）**。CSV/TSV読み込み処理における非同期ロード画面の有無を9画面調査した結果発見された未対応箇所（NG入力画面・仕掛展開画面、各種CSVインポート5画面）は、いずれも対応が完了した。詳細は`UI_WORKFLOW_FIXES_NOTES.md`グループR参照。
- **「対象外」マークの仕組み** → **完了（2026-09-11）**。NG一覧・仕掛一覧それぞれに実装した。詳細は本ファイル§10参照。これにより、「全項目が展開済みまたは対象外になった状態でのみ在庫差異レポート作成を許可する」というゲート機能も実現した（本ファイル§11参照）。
- 上記2項目は共有フォルダ運用・DBロック機構（`UI_WORKFLOW_FIXES_NOTES.md` グループQ）と同時期に整理された未完了タスクの一部だった。バックアップ機能・.exe化（共有フォルダ運用の最終目標）は引き続き保留中。`UI_WORKFLOW_FIXES_NOTES.md` §4を参照。
- **日報・月報への構成基板数チェック組み込み**（「未確定」仮想行・マスタ未登録の別カテゴリ警告・CSV出力・発注数不一致警告の重複呼び出し解消・逆方向不整合(超過)対応・構成基板数列/縞模様表示）→ **完了（2026-09-25〜26）**。§16参照。
- **構成基板数チェックの起点の欠陥2件**（実績のある行のみが起点／代表1件のboard_nameのみ判定）→ **原因判明・`check_lot_progress()`として独立関数の実装は完了、UI画面は保留中（2026-09-26）**。§16参照。マスタの`board_count`値の意味に関する疑義（後述）の確認待ち。
- ~~**構成基板数マスタの`board_count`値の意味に関する疑義**~~ → **2026-09-28、疑義解消・原因判明**。`board_count`は「board_name単位」ではなく「ロット単位」の値であり、(lot_no, board_name)単位で比較していたこと自体が誤りだった。詳細は§17参照。
- ~~**「ロット進捗チェック」のUI画面実装**~~ → **2026-09-28、完了**。`ui/lot_progress_window.py`（`LotProgressWindow`）。§17参照。
- **ロット状態判定・引落ルールの一本化** → **2026-09-28、完了**。`services/production_service.py::_evaluate_lot_status()`に集約し、`check_lot_progress()`・日報・月報・仕掛数量抽出が同じ判定を使うようにした。§17参照。
- **確定登録の後にスナップショットから消えたロットの訂正導線が無い問題** → **2026-09-28、対策実装済み**。§18参照。
- **未解決のまま残っている事項（2026-09-28時点）**：構成基板数がロット内で複数値に分かれる2件（lot_no=271569・344294）の扱い、超過2件（lot_no=262752・468052）のファイルNo統合、生産実績入力画面の情報欄が`_evaluate_lot_status()`に未統一のまま独自に`calculate_lot_completion()`・`get_board_structure()`を呼んでいる点、日報・月報の縞模様・文字色・印刷プレビューの「確認事項」列はみ出しの実機目視確認、仕掛展開画面の赤字表示の実機目視確認。いずれも詳細は§17・§18参照。
- **日々の引落・完了一覧・未完了一覧（Step2・Step3）** → **2026-09-30、完了（方針転換あり）**。`report_date`ベースの逆算ではなく`lot_status_history`（新規、イベントログ方式）を採用した。「日々の引落一覧」画面（`ui/daily_drawdown_window.py`）を実装、「未完了一覧」は新規実装せず既存`check_lot_progress()`を使う方針とした。§19・§20参照。
- **日報・月報の統合画面（`ui/unified_report_window.py::UnifiedReportWindow`）への一本化** → **2026-10-01、完了**。旧`DailyReportWindow`・`MonthlyReportWindow`は削除。期間選択（今日/今週/今月/カスタム範囲）・5種警告のオン/オフ・列ソート・行絞り込み・列の表示/非表示を実装。統合と同時に、日報画面で確認事項欄の色分けが機能していなかったバグも修正した。§21参照。
- **月報の表示対象確認・report_dateの信頼性確保（Step1）** → **完了（2026-09-29）**。§19参照。月報は完了・未完了や仕掛の有無を問わず全行を表示する設計であることを確認した。report_dateの意図せぬ書き換わり問題は`UI_WORKFLOW_FIXES_NOTES.md`グループAB追記8・追記9で修正済み。
- ~~**日々の引落・完了一覧・未完了一覧（Step2・Step3）** → **未着手**。~~ → 上記の通り2026-09-30に方針転換の上完了済み（§20参照）。本行は更新漏れの重複記載だったため削除時に気づき訂正した。
- **月報のソート機能・仕掛のみ表示等の絞り込み機能** → **保留（ユーザー決定、2026-09-29）**。§19参照。具体的な着手時期は未定。
- **もう一方の拠点のDBで`production_daily.report_date`のNOT NULL制約が同じく適用されているか** → **未確認**。この環境からは確認できないため、次にその環境で作業する機会があれば、`CANONICAL_DESIGN_DECISIONS.md` §5の整合性チェック手順に沿って確認することを推奨する（同ファイル§5のチェック項目に追加済み）。
- **UnifiedReportWindowへの「登録日」列の追加** → **2026-10-01、完了**。`production_daily.report_date`をTreeview・CSV・PDF出力すべてに反映。§22.1参照。
- **未完了計画のDB間引き継ぎルールの見直し（`list_incomplete_lots()`の`_evaluate_lot_status()`統一、未着手計画の50日上限）** → **2026-10-01、完了**。§22.2参照。
- **【重要・優先度高・未着手】共通マスタ（`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`）が月次DBに同居している設計上の課題** → **2026-10-01、発見（調査のみ完了、対応未着手）**。月次DBを新規作成するたびに共通マスタも空になる。詳細は`BOM_MIGRATION_NOTES.md` §14・`CANONICAL_DESIGN_DECISIONS.md` §18.3（D-38）参照。
- **レガシーテーブル14個の削除** → **2026-10-01、完了**。`BOM_MIGRATION_NOTES.md` §14・`CANONICAL_DESIGN_DECISIONS.md` §18.4（D-39）参照。

---

## 7. 構成基板数マスタ(新機能)

基板名(`board_name`)単位で「構成基板数」(表示のみの参考情報、BOM計算・実績登録等の他の処理には一切使わない)を管理する新マスタを追加した。

**新テーブル**：`board_structure_master`(`board_name TEXT PRIMARY KEY, board_count REAL, board_name_normalized TEXT NOT NULL, imported_at`)。既存の`models/parts_attributes.py`と同じ「CSVをマスタとした差分同期」パターン(delete-then-insert、upsert + `delete_board_structure_not_in()`)を踏襲(`models/board_structure_master.py`、`db/migration_012`)。

**CSVインポート画面**：`ui/board_structure_import_window.py`(新規)。`ui/parts_attributes_import_window.py`と同構成(CSV選択→Treeview表示→インポート実行、タブ区切り、`_open_csv_with_fallback()`をコピー流用)。メインメニュー「共通マスタ」セクションに「15. 構成基板数マスタインポート」ボタンを追加。

**表記ゆれ対策**：`normalize_board_name()`(NFKC正規化＋小文字化＋前後空白除去＋連続空白圧縮。`services/production_import_service.normalize_product_name()`と同一ロジックだが、models層からservices層への依存を避けるため複製)で正規化した値を`board_name_normalized`列として保存し、`get_board_structure()`はこの列で検索する(検索のたびに正規化計算をやり直さない設計)。

**生産実績入力画面への表示**：`ui/kitting_production_entry.py`のinfo_frame、「ロット未完成数」行の直後(row=8。以降の「基板別実績(file_no)」はrow=9へ1つずらした)に「構成基板数」行を追加。`search_plan()`内で`plan["board_name"]`をキーに`get_board_structure()`を検索し、登録が無ければ「未登録」と表示する。

### 「未登録」表示の原因調査と対応方針（2026-09-16、⑨・見送り決定）

**調査結果**：実DBで`kitting_plan_items`のdistinct `board_name`（804件）のうち145件（18.0%）が`board_structure_master`（3,027件）と一致せず「未登録」表示になっていた。`normalize_board_name()`（NFKC正規化＋小文字化＋空白圧縮）を適用しても一致件数は**1件も増えなかった**（正規化の効果がゼロ）ことを確認済み。145件を実際に分類した結果：

- 32件（22%）：`board_name`末尾等に含まれる`***`（ワイルドカード的な記号）を除去すればmasterと一致する、表記ゆれとして解決可能なケース。
- 残り113件（78%）：`board_structure_master`側にそもそも該当データが登録されていない（`GL-VM`・`GL-VZ`系統は0件）、または型番の命名体系自体が異なる（`GL-R`系統、`CC-1010`/`CC-1010K`等）ケース。文字列の近さによる自動照合（部分一致・編集距離等）では本質的に解決できず、誤って別の基板の構成数を引き当てるリスクの方が高いと判断した。

**確定した方針**：`normalize_board_name()`への`***`除去追加は費用対効果が高いが解消できるのは全体の約4%に留まるため、**今回は照合ロジックの強化を見送り**、根本対応（`board_structure_master`側のCSVマスタへの追加登録）はマスタ管理側の運用課題として切り離すことを決定した。詳細な分類・具体例は調査記録（2026-09-16）参照。`UI_WORKFLOW_FIXES_NOTES.md` §4「未対応・将来の検討事項」にも見送り決定として記載。

### 構成基板数の照合機能（色分け表示、2026-09-24追加）

上記「未登録」表示のみだった`ui/kitting_production_entry.py`の「構成基板数」行に、マスタの`board_count`と実際のロットの状況を突き合わせて色分け表示する照合機能を追加した。

- 「基板別実績（file_no）」欄に実際に表示されるfile_no（面2があれば面1を隠す既存ロジック適用後のdistinct集合、`visible_file_nos`）の件数と、`get_board_structure()`で取得した`board_count`を突き合わせる。
- 一致する場合：通常の色（青系、既存の`_add_info_row()`のデフォルト色）で`board_count`をそのまま表示。
- 一致しない場合：`"{board_count}（実際: {distinct_file_no_count}）"`という形式で**赤字**表示し、マスタ登録漏れ・計画データ側の異常等の可能性を視覚的に注意喚起する。
- マスタ未登録の場合：従来通り「未登録」を表示するが、**こちらも赤字**に変更した（一致・不一致・未登録の3状態のうち、対応不要なのは「一致」のみという考え方に統一）。

この照合はあくまで生産実績入力画面での単一計画選択時の参考表示であり、§16で後述する日報・月報側の一括チェック（複数ロットをまとめて処理する必要があるため、判定ロジック自体は再利用しつつUI上は別実装）とは独立している。

---

## 8. 仕掛数量抽出・仕掛展開機能(新機能)

### 仕掛数量抽出(月報)
月報画面(`ui/monthly_report_window.py`)に「仕掛数量抽出」ボタンを追加。押下時、既に集計済みの`self.report_rows`(`services.production_service.build_monthly_report()`の戻り値)から`surplus_qty > 0`の行のみを抽出し、`models.wip_board_snapshot.save_wip_snapshot()`で保存する。

新テーブル：`wip_board_snapshot`(`kitting_list_no, file_no, board_name, production_side, mounting_line, lot_no, surplus_qty, created_at`)。

**上書き単位はテーブル全体差し替え(スナップショット方式)**：行単位のキーによるdelete-then-insertではなく、抽出のたびにテーブル全体をDELETEしてから丸ごと入れ替える。理由：月報の集計期間は実行のたびに任意に変わり得るため、行単位キーで上書きすると、前回の集計対象だったが今回は対象外になった行が削除されずに残り続けてしまうため。

### 仕掛展開画面
`ui/wip_expansion_window.py`(新規)。`ui/ng_input_window.py`の左右ペイン構成(左：対象基板情報＋部品CheckableTreeview、右：一覧＋絞り込み＋ソート)を複製・適応。

- 右ペインのデータソースは`models.wip_board_snapshot.list_wip_snapshot()`(月報で抽出したスナップショット)。列構成：`kitting_list_no・board_name・file_no・生産面・ロットNo.・実装ライン・仕掛数量・抽出日時`。
- 行をダブルクリックすると、`services.bom_service.BOMService.expand_wip_to_parts()`(既存、NG展開の`expand_scrap_to_parts()`と同じ戻り値形式)を呼んでBOM展開する。
- **登録操作は無し(閲覧専用)**。仕掛の部品はまだ消費されていない在庫のため、NG入力画面と異なり登録先のテーブルが無い。
  （※この記述は導入当時の状態。その後、下記「仕掛展開結果の保存・レポート機能への拡張（Step1〜3）」で登録・保存機能が追加されたため、現在は登録先テーブル`wip_scrap_records`が存在する）
- メインメニュー「月次データ」セクションに「16. 仕掛展開」ボタンを追加(`_open_singleton_window()`パターン)。

---

## 9. 仕掛展開結果の保存・レポート機能への拡張（Step1〜3、新機能）

### 背景・確定した業務フロー

NG入力画面（仕損）と同様に、仕掛展開画面でも展開結果を保存・レポート出力できるようにし、最終的には「NG一覧・仕掛一覧の全項目が展開済み（確定）または対象外になった状態で在庫差異レポートを作成する」という業務フローを実現したい、という要望から着手した。

**重要な前提の発見**：在庫差異レポートの仕掛数量は、以前から`wip_board_snapshot`（月報の仕掛数量抽出・仕掛展開画面が参照するもの）を全く見ておらず、「本日の実績」（`services.production_service.build_daily_report()`）から都度独自に再計算していた。同じ「仕掛」という言葉を使いながら、仕掛展開画面と在庫差異レポートが別々のデータソースを見ているという食い違いが存在していた。

### Step1: 仕掛展開結果の保存機能

- 新テーブル`wip_scrap_records`（`models/wip_scrap_records.py`、新規。`scrap_records`と同様の構造：`kitting_list_no, file_no, production_side, part_no, qty, lot_no, mounting_line, created_at`）。
- `save_wip_scrap_records(kitting_list_no, file_no, side, records, lot_no, mounting_line)`（`replace_scrap_records()`と同じdelete-then-insertパターン、`(kitting_list_no, production_side, lot_no)`キー）。
- `ui/wip_expansion_window.py`に「仕掛確定登録」ボタンを追加（NG入力画面の`btn_register`と同じ有効化タイミング：展開成功時にNORMAL）。押下時、`CheckableTreeview.get_checked_iids()`でチェック済み部品を取得し保存する（`NgInputWindow.on_register()`と同じパターン）。
- 右ペインの仕掛一覧に「確定済み/未確定」の状態列を追加（`_fetch_wip_list_rows()`が`wip_board_snapshot`と`wip_scrap_records`を`(kitting_list_no, lot_no, production_side)`キーで突き合わせ、NG一覧の未展開/展開済みと同じ考え方で判定）。

### Step2: 仕掛版レポート2種

- `ui/wip_product_report_window.py`（仕掛製品レポート、`product_ng_report_window.py`ベース）・`ui/wip_parts_report_window.py`（仕掛96レポート、`parts_ng_report_window.py`ベース）を新規実装。列構成・PDF出力・印刷・CSV出力（utf-8-sig）は既存の`ui/daily_report_window.py`の共通実装（`build_daily_report_pdf()`・`ReportPreviewWindow`）をそのまま流用。
- `models/wip_scrap_records.py::query_wip_totals_range()`（期間指定版、96コード単位のSUM）を新設。`wip_scrap_records`には`report_date`列が無く`created_at`（日時文字列）のみのため、`substr(created_at, 1, 10)`で日付部分を切り出して範囲比較する設計にした（既存の`query_scrap_totals_range()`と同じ設計思想を、列構成の違いに合わせて適応）。
- `ui/wip_expansion_window.py`の`on_wip_list_double_click()`を`_expand_row()`として共通処理に切り出し、外部から特定の行を自動展開できる`expand_by_identity(kitting_list_no, lot_no, production_side)`を新設（レポート画面の行ダブルクリックから、新規に`WipExpansionWindow`を開いて自動展開する導線用。`ui.product_ng_report_window.ProductNgReportWindow.on_row_double_click()`が新規`NgInputWindow`を開く既存パターンと同じ考え方）。

### Step3: 在庫差異レポートの仕掛数量参照先の変更

- `services/inventory_diff_service.py::_collect_wip_totals()`を全面書き換え。`BOMService.expand_wip_to_parts()`の都度呼び出し（共有フォルダアクセスを伴う）を廃止し、`models/wip_scrap_records.py::query_wip_totals()`（新規、`query_scrap_totals()`と同じパターン、96コード単位のSUM）から集計する形に変更した。
  **依頼時に指定された関数名（`list_wip_scrap_summary()`）は`(kitting_list_no, production_side, lot_no)`単位の集計であり96コード単位の内訳を持たないため、そのままでは使用できないことが実装時に判明し、正しい集計粒度を持つ新関数`query_wip_totals()`を追加する形に修正された**（集計粒度の違いに気づかず実装していたら誤った集計値になるところだった）。
- `inventory_diff_service.py`から`BOMService`・`build_daily_report`関連のimportを全て削除し、在庫差異レポートがDBアクセスのみで完結する（共有フォルダへ一切アクセスしない）ことを確認した。
- 実行時間の実測：約4.6ms（以前はBOM展開のたびに共有フォルダアクセスが発生する構造で、仕掛のある行数分だけ画面表示前に直列実行される最も深刻な遅延要因だった）。
- `count_unconfirmed_wip_boards()`（`wip_board_snapshot`×`wip_scrap_records`の突き合わせ）を追加し、未確定の仕掛基板がある場合は`ui/inventory_diff_window.py`に警告表示（「※ 未確定の仕掛基板がN件あります。仕掛数量（仕掛列）に反映されていません。」）。0件なら非表示。

---

## 10. NG一覧・仕掛一覧への「対象外」マーク機能（新機能）

### 背景
§6で「展開不要と判断した項目」を示す仕組みが未実装と記録していたが、これを実装した。設計方針（独立した「除外リスト」テーブルが必要、`scrap_records`等の既存3テーブルはdelete-then-insertのため列追加ではフラグが生存しない）は決定済みだった。

### 確定した設計
NG用・仕掛用で**2テーブルに分ける**方針を採用した（ユーザー決定、`target_type`列での1テーブル共用は不採用）。

### 実装（NG用）
- `models/ng_exclusion_list.py`（新規）：`ng_exclusion_list`テーブル。識別キーは`(kitting_list_no, production_side, lot_no)`。delete-then-insertで上書き（`ng_declarations`と同じ設計）。
- `mark_ng_excluded()`・`unmark_ng_excluded()`・`is_ng_excluded()`・`list_ng_exclusions()`を実装。
- NG一覧（`ui/ng_input_window.py`）に「対象外」列を追加。**一覧からは除外せず、区別表示のみ**とする方針を採った（判断理由：一覧から消すと対象外にした事実自体が見えなくなり、解除するための導線も同時に失われるため）。既存のフィルタ・ソート機構にそのまま乗せられる設計。
- 「対象外にする」「対象外解除」ボタン＋理由入力ダイアログを追加。理由入力ダイアログにも既存の最小化対策（`UI_WORKFLOW_FIXES_NOTES.md`グループO参照）を適用済み。

### 実装（仕掛用）
- `models/wip_exclusion_list.py`（新規）：識別キーは`(kitting_list_no, lot_no, file_no, production_side)`（`wip_board_snapshot`に安定した一意キーが無いため4項目）。NG用と全く同じパターンで実装。NG入力画面側の実装・動作には影響が無いことを確認済み。

---

## 11. 在庫差異レポート作成時の未処理項目チェック（ゲート機能）

### 背景
§10の「対象外」マーク機能の完成により、「NG一覧・仕掛一覧の全項目が展開済み（確定）または対象外になった状態で在庫差異レポートを作成する」という業務フロー（§9で着手した目標）を実現するゲート機能を実装した。

### 実装
- `services/unprocessed_check_service.py`（新規）：`check_unprocessed_items()`が、NG一覧の「未展開かつ対象外でない」件数、仕掛一覧の「未確定かつ対象外でない」件数をそれぞれ算出する。
- `ui/main_window.py::open_inventory_diff()`で、レポートを開く前にこのチェックを実行。未処理項目が1件以上あれば確認ダイアログを表示する（**強制ブロックではなく注意喚起**、「はい」を選べば従来通り開ける）。

### 重要な設計判断（意図的な例外）：services層のui層への依存
`services/unprocessed_check_service.py`は、NG一覧・仕掛一覧の判定ロジックを複製せず、`ui.ng_input_window.NgInputWindow`・`ui.wip_expansion_window.WipExpansionWindow`の該当staticmethod（`_fetch_ng_list_rows()`/`_fetch_wip_list_rows()`）を**そのままimportして再利用**した。これは通常避けるべき「services層がui層に依存する」という方向の依存関係だが、このプロジェクトでこれまで繰り返し発生してきた「同じロジックが複数箇所に複製され、片方だけ更新されて食い違う」問題（`calculate_lot_completion()`と`list_incomplete_lots()`の食い違いが過去に発生した例など）を避けるための意図的な選択である。

あわせて、列の並び順を位置決め打ちにしないよう、`NgInputWindow.NG_LIST_COLUMNS`・`WipExpansionWindow.WIP_LIST_COLUMNS`というクラス属性を新設し、各画面の一覧構築コードと本サービスの両方がこれを単一の情報源として参照する設計にした。

**この`services`→`ui`依存は、意図的な例外としてこのまま維持してよい設計判断である（`CANONICAL_DESIGN_DECISIONS.md` D-9参照）。**

---

## 12. NG一覧の一括展開・登録機能（新機能）

### 背景
既存のNG一覧は「1件ずつダブルクリックして展開・登録」する設計だった。在庫差異レポート（`query_scrap_totals()`/`query_wip_totals()`）による96コード単位の集計は、既に登録されている`scrap_records`/`wip_scrap_records`のデータのみを対象とするため、全項目が手動で個別登録されて初めて正しい集計になる、という制約があった。この手動作業を一括処理化したいという要望から着手した。

### 確定した設計
「対象外を除く未展開の項目を、確認ステップなしで一括展開・登録する」方針（ユーザー決定）。既存の「対象外」マーク機能（§10）が、まさにこの一括処理の事前フィルタとして機能する設計になっている。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| AP-1 | `ui/ng_input_window.py` | NG一覧に「一括展開・登録」ボタンを追加 | **反映済み** |
| AP-2 | `ui/ng_input_window.py` | `_get_bulk_expand_targets()`（新規）：生データ（`list_ng_declarations_latest()`・`list_scrap_summary_by_kitting_no()`・`list_ng_exclusions()`）を直接突き合わせ、「未展開（申告はあるが展開集計が無い）かつ対象外でない」行のみを抽出。表示用に整形済みの`_all_ng_rows`（"面1"等の文字列）ではなく生の`production_side`・`ng_qty`を使う | **反映済み** |
| AP-3 | `ui/ng_input_window.py` | `_bulk_expand_and_register_one()`（新規）：既存の`_expand_from_kitting_no()`/`_expand_from_file_no()`と同じ計画解決・`expand_scrap_to_parts()`呼び出しロジックを踏襲。チェック確認のステップは行わず、展開された全部品を`replace_scrap_records()`でそのまま登録する | **反映済み** |
| AP-4 | `ui/ng_input_window.py` | `_run_bulk_expand_worker()`（新規）：対象行を1件ずつtry/exceptで保護し、1件のエラーで処理全体を止めず他の行の処理を継続する（既存のCSV取込系機能と同じ「1行の異常が他行に影響しない」設計）。成功件数・失敗件数・エラー内容を完了時に表示 | **反映済み** |
| AP-5 | `ui/ng_input_window.py` | `on_bulk_expand_register()`：既存の非同期パターン（`LoadingWindow`＋`threading.Thread(daemon=True)`＋`queue.Queue`＋`self.after(200,...)`ポーリング）を適用。処理完了後、NG一覧を再取得し状態（未展開→展開済み）を反映する | **反映済み** |

**重要な設計判断**：計画候補が複数ある場合（`search_plan_by_kitting_no()`がcandidatesを返す）・実装ラインが複数ある場合（`list_mounting_lines()`が2件以上返す）、バックグラウンドスレッドからは選択ダイアログを表示できないため、**その行だけエラーとして扱う**（無理な自動選択はしない、安全側の設計）。

### 仕掛展開画面への同様の機能（実装済み、2026-09-15）
NG入力画面の一括展開・登録機能（AP-1〜AP-5）と同じ設計で、仕掛展開画面（`ui/wip_expansion_window.py`）にも実装した。

**NG入力画面との重要な違い**：`wip_board_snapshot`の行は、月報の「仕掛数量抽出」時点で既にfile_no・生産面・lot_no・mounting_lineを保持しているため、NG入力画面のような「kitting_list_noから計画を検索し、複数候補があれば曖昧」という判定ステップ自体が存在しない（計画あり／計画外の区別も無い）。曖昧さが発生し得るのは、**mounting_lineが未確定（空欄）の行に限り、実装ラインが複数存在するケースのみ**であり、この場合のみNG入力画面と同様にその行だけエラーとして扱う（`list_mounting_lines()`が2件以上返す場合）。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| AR-1 | `ui/wip_expansion_window.py` | 仕掛一覧に「一括展開・登録」ボタンを追加（`更新`／`対象外にする`／`対象外解除`の直後、`実績修正`の前。NG入力画面と同じ並び） | **反映済み** |
| AR-2 | `ui/wip_expansion_window.py` | `_get_bulk_wip_expand_targets()`（新規）：`list_wip_snapshot()`・`list_wip_scrap_summary()`・`list_wip_exclusions()`の生データを突き合わせ、「未確定（`wip_scrap_records`に対応行が無い）かつ対象外でない」行のみ抽出。`wip_board_snapshot`はキーの一意性がDBレベルで保証されないため、dictに丸めずリストのまま走査する | **反映済み** |
| AR-3 | `ui/wip_expansion_window.py` | `_bulk_expand_and_register_one_wip()`（新規）：`expand_wip_to_parts()`（file_no・生産面・mounting_line・仕掛数量を使用）で展開し、チェック確認のステップは行わず`save_wip_scrap_records()`でそのまま登録 | **反映済み** |
| AR-4 | `ui/wip_expansion_window.py` | `_run_bulk_wip_expand_worker()`：対象行を1件ずつtry/exceptで保護し、1件のエラーで処理全体を止めず他の行の処理を継続する（AP-4と同じ設計） | **反映済み** |
| AR-5 | `ui/wip_expansion_window.py` | `on_bulk_expand_register()`：既存の非同期パターン（`LoadingWindow`＋`threading.Thread(daemon=True)`＋`queue.Queue`＋`self.after(200,...)`ポーリング）を適用。処理完了後、仕掛一覧を再取得し状態（未確定→確定済み）を反映する | **反映済み** |

**検証結果**：一時DBで4パターン（未確定・対象外でない2件、対象外1件、既に確定済み1件）を用意し検証。①対象抽出：未確定かつ対象外でない行のみ正しく抽出（対象外・確定済みは除外）。②一括登録：BOM展開をモックし、1件成功（`wip_scrap_records`に正しく登録）・1件はBOM展開エラーで失敗、他行の処理は継続されることを確認。③対象外の除外：対象外指定した行は一括処理の対象にならない。④確定済みの非重複：既に確定済みの行は再登録されず元のレコードのまま維持される。⑤エラー時の継続・報告：1件のエラーでも他行の処理は継続され、完了時に成功/失敗件数とエラー内容が表示される。⑥状態の再反映：処理完了後、成功した行は「確定済み」に、失敗した行は「未確定」のまま正しく表示される。⑦ロード画面：実行直後に`LoadingWindow`が表示され、非同期処理中も`root.update()`が返り続ける（UIスレッドがブロックされない）ことを確認。`python -m pytest tests/`にも影響無し。

### 実装中に発見された重要な問題（検証手法自体のリスク）
これまで慣行としていた「全パッケージimportループチェック」（`pkgutil.walk_packages`で`ui`/`models`/`services`配下を含む作業ディレクトリ全体を対象にimportし、正しくimportできるか確認する手法）が、作業ディレクトリ直下の`check_*.py`・`delete_failed_batches.py`・`delete_test_batches.py`等の単発メンテナンススクリプトまで無差別にimportし、**実DBに対してモジュールトップレベルの処理を走らせてしまう**リスクを抱えていたことが判明した。今回は対象0件で実害は無かったが、`delete_*`という名前のスクリプトが存在する以上、偶然実害が無かっただけというリスクであった。**以降、`ui`/`models`/`services`/`config`に限定したスコープ付きimportチェックに切り替えた**（この安全化の詳細・今後の運用指針は`CANONICAL_DESIGN_DECISIONS.md`§5に記載。このプロジェクトの検証手法そのものの安全性向上として重要なため、単なる本機能の実装メモに留めず正典側にも記録した）。

---

## 13. scrap_records・wip_scrap_recordsの個別行修正画面（新機能）

### 発見された不足
一括展開・仕損NG展開のいずれの経路でも、`scrap_records`/`wip_scrap_records`は**グループ単位（kitting_list_no・lot_no・production_side）での全削除→再登録**という設計（delete-then-insert）のみで、**96コード単位で保存済みデータを見て、個別に1件だけ修正・削除する画面が存在しない**ことが調査で判明した。`production_daily`にはこれに相当する`ActualCorrectionWindow`（個別行のUPDATE/DELETE）があるが、NG・仕掛側には対応する画面が無かった（本ファイル旧§6にも「ユーザー決定により未実装」と記録されていたが、今回方針が変わり実装した。該当記述は§6側で更新済み）。

### 実装
| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| AQ-1 | `models/scrap_records.py` | `update_scrap_record(id, qty)`・`delete_scrap_record(id)`（新規、いずれも`models.production.update_daily_production()`/`delete_daily_production()`と同じ設計・同じ単純さ）。`list_scrap_records_by_kitting_no()`に`production_side`引数を追加（省略時はNoneで従来通り両面分、既存呼び出し元への後方互換を維持） | **反映済み** |
| AQ-2 | `models/wip_scrap_records.py` | 同様に`update_wip_scrap_record()`・`delete_wip_scrap_record()`・`list_wip_scrap_records_by_kitting_no()`（新規、以前の実装時に見送られていたもの）を追加 | **反映済み** |
| AQ-3 | `ui/scrap_correction_window.py`（新規） | `ScrapCorrectionWindow`：`ActualCorrectionWindow`と同じ設計思想（明細一覧→選択→数量修正または削除→即座にUPDATE/DELETE） | **反映済み** |
| AQ-4 | `ui/wip_scrap_correction_window.py`（新規） | `WipScrapCorrectionWindow`：同上のWIP版 | **反映済み** |
| AQ-5 | `ui/ng_input_window.py`, `ui/wip_expansion_window.py` | NG一覧・仕掛一覧それぞれに「実績修正」ボタンを追加し、対応する個別修正画面（選択行の`kitting_list_no`・`lot_no`・`production_side`を渡す）を開ける導線を追加 | **反映済み** |

### 重要な設計上の注意点（既存の設計方針と整合）
個別修正画面での修正は、**その計画が後日NG入力画面/仕掛展開画面で再展開・再登録される（グループ単位の洗い替えが走る）と、消えてしまう**。これは「後からの展開・登録を正として上書きする」という既存の設計方針（§3グループN3、`CANONICAL_DESIGN_DECISIONS.md` D-6等）と整合する挙動であり、想定外の不整合ではないが、個別修正画面を使う人には予想外に映る可能性があるため、**両画面に「この画面での修正は、対象の計画が再展開・再登録されると失われる可能性があります」という注意書きを常時表示**することにした。実際にこの想定通りの挙動（個別修正→再展開で上書きされて消える）を検証済み。

---

## 14. 日報・月報の引落数・仕掛数計算バグ（重大な根本ロジック不一致、修正済み、2026-09-16）

（§5「面1/面2の連動ルール」で確立した実績・NG申告の設計と直接関連する内容のため、本節をここに追加する。）

### 発見の経緯

「同一ロット全ての基板が生産されていないと引落にならず仕掛になるはずができていない」という指摘を受けて調査した結果、**生産実績入力画面は`calculate_lot_completion()`（正しいロジック：ファイルNo×面単位で複数バッチの実績を合算し、その最小値をロットの引落数とする）を使っていたが、日報・月報（`services/production_service.py::_build_report_rows()`）は全く別の独自ロジックを使っていた**ことが判明した。独自ロジックは「その日/期間に実績登録があった行だけ」を対象に最小値を計算するため、**未生産のファイルNoがそもそも計算対象から漏れ、揃っていないのに引落されてしまう**という、業務ルールと直接矛盾する重大な不一致だった。

### 対応

`_build_report_rows()`の独自ロジックを削除し、`calculate_lot_completion(lot_no)`に統一。同一lot_noへの重複呼び出しを避けるため、lot_no単位でキャッシュ（1回だけ計算し、同じレポート内の全該当行で使い回す）。`calculate_lot_completion()`がValueError（対象lot_noの計画が見つからない）を送出した場合は、安全側（未完成扱い：`lot_completed=0`・`surplus_qty=daily_qty`・`lot_remaining=order_qty`）にフォールバックする。

### 動作確認

lot_no=110068（未生産のファイルNoがあるロット）で、修正後の日報結果（`0/120/120.0`）が、生産実績入力画面の`calculate_lot_completion()`直接呼び出し結果と完全一致することを確認。正常系（全ファイルNo生産済み）・同一lot_noに複数行がある場合の重複計算の排除・面1/面2不整合警告（§5参照）・発注数不一致警告への無影響も確認済み。`build_wip_extraction_rows()`（仕掛数量抽出）は元々`calculate_lot_completion()`ベースの正しいロジックだったため無修正。

---

## 15. 計画一覧「入力済みを隠す」フィルタの根本ロジック不一致（同種バグの3件目、修正済み、2026-09-23）

（§14で確立した「実績・完成数に関わる判定は必ず`calculate_lot_completion()`を経由する」という方針が、生産実績入力画面自体の中にも未適用の箇所として残っていたことが判明した事例。`UI_WORKFLOW_FIXES_NOTES.md`のI-3〜I-5（面1省略ロジックが日報・月報に未適用だった件）・本ファイル§14（日報・月報の独自ロジック）に続く、**3件目の同種の発見**。）

### 発見の経緯

計画一覧の「入力済みを隠す」チェック（`P-view-4`、本ファイル§3グループP-view参照）が、複数バッチ（キッティングNo.）に分割されたロットで正しく機能しないケースがあるかを調査した結果、判定ロジック自体が古いままであることが判明した。

### 原因

`ui/kitting_production_entry.py::apply_plan_filters()`内の「入力済みを隠す」判定が、`order_qty`・`actual_qty`という**行単位（単一kitting_list_no単位）**の単純比較（`actual_qty < order_qty`なら表示継続）のままだった。`actual_qty`の実体は`plan_item["app_cumulative_qty"]`（`models/kitting_plan.py::list_active_plan_items()`が計算する`(kitting_list_no, lot_no)`単位の累計）であり、同一lot_no・同一setup_file_noを複数のkitting_list_noが分担するケースで正しく合算しない`calculate_lot_completion()`以前の粒度のままだった。

実データと同じ状況（キッティングNo.A：発注500・実績498、キッティングNo.B：発注2・実績2、合算500=完了）を隔離DBコピー上で再現した結果、キッティングNo.Bの合算後もキッティングNo.A単体では498<500のままのため、「入力済みを隠す」をONにしてもキッティングNo.Aの行が表示され続ける不具合を実機確認した。一方、同じ行の`lot_completed`/`lot_remaining`列（表示のみ、`calculate_lot_completion()`ベース）は既に正しく完了（`remaining_quantity=0`）と表示しており、**同一画面内で新旧2つの完成判定ロジックが矛盾したまま併存していた**。

### 対応

`apply_plan_filters()`の判定を`calculate_lot_completion(lot_no)`の`file_actuals[(setup_file_no, production_side)]`（そのファイルNo・面の合算実績）と`completed_quantity`の比較に統一した（「行の合算実績がそのファイルNoの完成数以上であれば入力済みとみなす」、`lot_completed`列と同じ判定基準）。`production_side`は表示列（`cols_plan`）に含まれていなかったため、行データの末尾に非表示要素として追加した上でTreeview挿入時にはスライスして画面には出さない形にした。`_fetch_plan_list_rows()`のlot単位キャッシュと同じパターンで、`apply_plan_filters()`呼び出し1回の中でのみ有効な使い捨てキャッシュを設け、同一lot_noへの`calculate_lot_completion()`重複呼び出しを避けている。

なお、この判定基準は**実績数（生産実績の合算）のみで発注数に達しているかを見るものであり、NG数は意図的に考慮しない**（NG込みの完了判定は別の発展的なアイデアとして今回は見送り、ユーザー確認済み）。

### 動作確認

上記の複数バッチ分割シナリオ（キッティングNo.A＋B、合算500=完了）で、ONにすると両方とも正しく非表示になることを確認。単一バッチの通常の完了済み計画も引き続き正しく非表示になることを確認。実DBの計画一覧全553件に対する処理時間は約0.26秒（lot_no単位キャッシュにより`calculate_lot_completion()`の重複呼び出しは発生しない）。`python -m pytest tests/`にも影響無し（1 passed）。

### 教訓

「同種のロジック不一致が複数箇所で繰り返し発見されている」（§14→本節で3件目）という事実を踏まえ、「実績・完成数に関わる新しい判定ロジックを実装する際は、必ず`calculate_lot_completion()`を経由すること、独自の行単位比較を実装してはならない」という原則を`CANONICAL_DESIGN_DECISIONS.md` D-21に記録した。

### 追記：境界条件バグ（未登録の計画も誤って非表示になる、修正済み、2026-09-24）

**発見の経緯**：上記の対応で`calculate_lot_completion()`統一した後、「入力済みを隠す」をONにすると未登録（実績0件）の計画まで全て非表示になってしまうという新たな不具合が報告された。

**原因**：修正後の判定式`file_actuals[key] >= completed_quantity`が、ロット全体が未登録（実績0）の場合の境界ケースを考慮していなかった。`completed_quantity`（`_compute_lot_completion()`が計算する、ロット内の全`file_no`・面の実績の最小値）は、実績が1件も無いロットでは全ての値が0になるため必然的に0になる。このとき`file_actual`（該当行自身の合算実績）も0のため、`0 >= 0`がTrueと評価され、「まだ何も登録されていない計画」が誤って「完了済み」と判定されていた。**「合算実績がそのファイルNoの発注数（`order_qty`）に到達しているか」を見るべきところを、「ロット全体の実績の最小値（`completed_quantity`）に到達しているか」という別の変数と比較してしまっていた、変数の取り違えが根本原因**だった。

**対応**：比較対象を`completed_quantity`から、**その行自身の`order_qty`（発注数）**に変更した（`file_actuals[key] >= order_qty`）。`completed_quantity`はロット全体の最小値を意味するに過ぎず、個々の行の完了判定には本来使うべきでない値だったため、より直接的に「この行のファイルNo・面の合算実績が、この行自身の発注数に到達しているか」を見る形に修正した。

**動作確認**：未登録（実績0件）の計画が正しく表示され続けること、部分的に登録済み（合算実績が発注数未満）の計画も正しく表示され続けること、完全に登録済み・複数バッチ分割で合算完了（498+2=500）のケースは引き続き正しく非表示になることを、いずれも実データ・隔離DBの両方で確認した。`python -m pytest tests/`にも影響無し（1 passed）。

---

## 16. 日報・月報への構成基板数チェック組み込み（新機能、2026-09-25〜26）

§7で追加した構成基板数マスタ・§7追記の色分け照合機能は、生産実績入力画面での**単一計画選択時**の参考表示に留まっていた。日報・月報（`services/production_service.py::_build_report_rows()`）でも、複数ロットをまとめてチェックし、異常を一覧・CSVで確認できるようにしたいという要望から、以下を実装した。

### 「未確定」仮想行の追加

`_build_report_rows()`の実データ行構築ループの後、既に取得済みの`lot_completion_cache`（lot_no単位の`calculate_lot_completion()`結果、重複計算防止のため元々キャッシュされていたもの）を再利用し、lot_no単位で構成基板数チェックを行う。

- `visible_file_nos`（面1省略後のdistinct file_no集合）を`file_actuals`から算出（`ui/kitting_production_entry.py`の照合ロジックと同じ考え方）。
- マスタに登録済みで`board_count > visible_file_nosの数`の場合：不足分（`board_count - visible_file_nosの数`）だけ、「未確定」の仮想行を`report_rows`へ追加する。仮想行は実際の`kitting_plan_items`・`production_daily`のレコードに対応しないため、`kitting_list_no=""`（既存の「ダブルクリック時、kitting_list_noが空ならreturnする」ガードがそのまま機能する）、数量系5フィールド（`daily_qty`・`order_qty`・`lot_completed`・`surplus_qty`・`lot_remaining`）は全て`0`とした。`file_no`列に文字列「未確定」を入れる（`board_name`はそのまま実際の基板名を表示）。
  - **CSV/PDF/印刷プレビューへの影響確認**：これら全ての出力経路が`ui/daily_report_window.py::_row_to_values(row)`という単一関数を経由しており、数量系フィールドを`":.0f"`で書式化する（文字列を入れるとCSV/PDF/Treeview全てで`ValueError`になる）ことを確認した上で、仮想行にも実データ行と同じ9キーの辞書構造・数値型フィールドを持たせることで、既存の出力ロジックを一切変更せずに対応できた。
  - `board_count < visible_file_nosの数`（マスタ側の登録漏れ・計画側の重複等が考えられる、実データで3件確認）の場合は、今回のスコープでは仮想行の追加・警告のいずれも行わない。
- 一致する場合：追加処理なし。
- seqの連番は、実データ行を構築する既存ループの続きとして、仮想行にもそのまま連番を振る（仮想行は実データ行の後にまとめて追加するため、`report_rows`内の同一lot_noの行と隣接しているとは限らない）。

### マスタ未登録ロットの別カテゴリ警告

`board_structure_master`に該当`board_name`が未登録の場合、構成基板数チェック自体が行えないため、仮想行は追加せず、`unregistered_board_warnings`という**別カテゴリ**の戻り値に記録するに留めた。

**判断根拠**（調査結果に基づく）：実データでアクティブな693ロット中226ロット（約33%）がマスタ未登録であり、これを構成基板数不足（64ロット、約9%）と同列に警告すると、真に構成基板数が不足している異常が多数のマスタ整備待ちノイズに埋もれてしまう。§7の「未登録」原因調査（2026-09-16、145件中32件が`***`表記ゆれ、残りはマスタ未登録）とも整合する判断である。

月報画面（`ui/monthly_report_window.py`）に、面1/面2不整合警告・発注数不一致警告と同じパターンの第3の警告ダイアログ（`_show_unregistered_board_warning_if_any()`）を追加した。日報側は、面1/面2不整合警告が月報限定である既存方針に合わせ、この警告も月報限定とした（ダイアログ表示は行わないが、戻り値は日報側でも受け取る）。

### マスタ未登録警告のCSV出力機能、および基板名単位への集約

月報画面に「マスタ未登録リストをCSV出力」ボタンを追加した（`ui/production_import_staging_window.py::on_export_mismatched_csv()`と同じ、utf-8-sig・ファイル選択ダイアログ・成功/失敗messageboxのパターンを踏襲）。警告ダイアログの本文末尾にも、この出力ボタンへの案内文言を追加した。

初期実装は`lot_no, board_name, file_no`（1ロット1行）だったが、後日**board_name単位への集約**に変更した。マスタ未登録の原因はboard_name自体にあり、ロットごとに繰り返し警告されても対応（マスタ登録）は実質同じであるため、対応単位であるboard_name単位の一覧の方が実務上扱いやすいと判断したもの。列構成：`board_name, lot_nos（該当する全ロットNo.、カンマ区切り）, file_nos（該当する全ファイルNo.の重複無しリスト、カンマ区切り）`。同じboard_nameを持つ複数ロットのfile_nosは`set`で合算し重複を除去した上でソートする。

### 発注数不一致警告の重複呼び出し解消

`_compute_lot_completion()`は元々`order_qty_inconsistent`（発注数がファイルNo間で不一致、実データで3件確認済み、§7とは別に月報固有の既知の問題として以前から記録されていた）を算出していたが、`_build_report_rows()`自体はこれを戻り値に含めていなかったため、`ui/monthly_report_window.py::_collect_order_qty_inconsistencies()`が独自に`report_rows`のdistinctなlot_noへ`calculate_lot_completion()`を**再度呼び出して**同じ情報を求める、という二重計算になっていた。

`_build_report_rows()`が既に保持している`lot_completion_cache`をそのまま使って`order_qty_inconsistency_warnings`を算出し戻り値に追加することで、`_collect_order_qty_inconsistencies()`自体を削除し、二重のDB問い合わせを解消した。

`_build_report_rows()`の戻り値は、この一連の変更で`(report_rows, inconsistency_warnings)`の2要素タプルから`(report_rows, inconsistency_warnings, order_qty_inconsistency_warnings, unregistered_board_warnings)`の4要素タプルに変更されており、`build_daily_report()`・`build_monthly_report()`の全ての呼び出し元（`ui/daily_report_window.py`3箇所・`ui/monthly_report_window.py`2箇所）を合わせて更新済み。

### マスタ未登録（226ロット）の表記ゆれ可能性の調査（慎重な事実ベースの報告、自動判定は行わない方針）

§7の調査（2026-09-16、804件中145件未登録、うち32件が`***`表記ゆれ）から日数が経過し、実データの状況が変化した（2026-09-26時点で824件中140件未登録、対応するロット数は226件）ことを踏まえ、改めて表記ゆれの可能性を調査した。

- `normalize_board_name()`を独立した別実装で再計算し、既存の照合ロジックとの不一致（＝正規化ロジック自体のバグ）が無いことを確認した（0件）。
- `***`記号の有無のみが差である候補：140件中28件（該当ロット数では226件中16件）。§7の32件パターンと同種の傾向が今回も確認された形。
- 編集距離1〜2、または包含関係にある候補（`***`パターンとは別）：140件中8件（該当ロット数では226件中5件）。
- 候補が全く見つからない（真に未登録らしい）もの：140件中104件（該当ロット数では226件中205件、約91%）。

**方針の確定**：候補が見つかったものも含め、**どれが実際に表記ゆれでどれが別製品かは自動判定せず、あくまで「人間が確認すべき候補」として提示するに留める**方針を採った。理由：型番中の1文字違い（例："MS2-H150"と"MS2-H100"）等は、単純な誤記の可能性と、実在する別バリエーションを指している可能性の両方があり、業務知識の無いプログラムによる自動判定はリスクの方が大きいと判断したため（§7で既に「文字列の近さによる自動照合は誤って別の基板の構成数を引き当てるリスクの方が高い」と判断された方針を踏襲・再確認する結果となった）。今回の調査結果を機械的な自動照合ロジックへ組み込む変更は行っていない。

### 逆方向の不整合（超過）への対応（2026-09-26追加）

構成基板数チェックはこれまで「不足」（`board_count > visible_file_nosの数`）のみを扱っていたが、その逆（`board_count < visible_file_nosの数`、マスタ側の登録漏れ・計画側の重複等が考えられる）も検知するよう`_build_report_rows()`を拡張した。

- 不足時と異なり、「未確定」仮想行は追加しない（不足ではなく過剰なため、埋めるべき空き行が無い）。
- 代わりに新カテゴリの警告`excess_file_no_warnings`（`{lot_no, board_name, board_count, file_nos}`）に記録する。`unregistered_board_warnings`（マスタ未登録＝判定不能）とは意味が異なるため、別リスト・別ダイアログ・別CSV出力ボタン（「構成基板数超過リストをCSV出力」）として扱い、混同しないようにした。CSV集約は「マスタ未登録リストをCSV出力」と同じboard_name単位（`board_name, lot_nos, file_nos`）に`board_count`列を加えた形。
- 実データの逆方向不整合3件（lot_no=262752・271569・344294、いずれも構成基板数マスタに登録済みだが実際のファイルNo数の方が多い）で、仮想行が追加されないこと・別カテゴリの警告として記録されることを確認済み。

`_build_report_rows()`の戻り値はこの変更で5要素タプル（`report_rows, inconsistency_warnings, order_qty_inconsistency_warnings, unregistered_board_warnings, excess_file_no_warnings`）になり、日報・月報側の全呼び出し元を合わせて更新した。

### 日報・月報一覧への構成基板数列・ロット単位縞模様表示（2026-09-26追加）

一覧の可読性向上のため、以下2点を追加した。

- **構成基板数列**：`REPORT_HEADERS`に「構成基板数」列を「基板名」の直後に追加。`get_board_structure(board_name)`から取得し、未登録の場合は「未登録」と表示する（`ui/kitting_production_entry.py`の表示文言に統一）。CSV・PDF出力（`_row_to_values()`を共通利用）にも自動的に反映される。
- **ロット単位の縞模様表示**：`lot_no`が変わるたびに交互に切り替わる薄いグレー系2色（`lot_stripe_a`＝白、`lot_stripe_b`＝`#e6e6e6`）のTreeviewタグを追加。**単純な「直前行との比較」方式ではなく、「lot_no→タグ」の辞書（`lot_tag_map`）で色を1回だけ確定して使い回す方式**を採用した。理由：`report_rows`は同一lot_noの行が連続して並ぶ保証が無く、また構成基板数チェックが追加する「未確定」仮想行は元のlot_noの実データ行とは離れた末尾に挿入されるため、単純な直前行比較では同じロットなのに色がずれてしまう。辞書方式であれば、仮想行も元のロットと同じ色に自然に揃う。

### 重大な発見1：構成基板数チェックが「実績のある行」のみを起点にしていた欠陥（未解決）

`_build_report_rows()`内の構成基板数チェック（仮想行・`unregistered_board_warnings`・`excess_file_no_warnings`いずれも）は、`lot_completion_cache`（対象期間内に`production_daily`実績があり、かつ面1省略で除外されなかった行のみが登場する辞書）をループの起点にしている。このため、**その日/その期間にたまたま実績入力が無かったロットは、構成基板数が実際に不足・超過していても日報・月報のいずれにも一切現れない**という設計上の欠陥があることが判明した。

実データで再現確認済み：実際に構成基板数(2)がファイルNo数(1)を上回るロット`260079`は、実績が`2026-03-17`の1件のみのため、本日限定の日報では対象0件（仮想行も現れない）。実績が存在する期間を含む月報を実行すると正しく仮想行が1件出現する。

### 重大な発見2：未登録判定が「代表1件のboard_name」のみを見ていた欠陥（未解決）

`unregistered_board_warnings`（および構成基板数チェック全体）は、`lot_board_name[lot_no]`という「そのlot_noで最初に見つかったboard_name1件のみ」を代表として`get_board_structure()`に渡す設計だった。同一lot_no内に複数の異なるboard_name（メイン・サブ等）が存在するケースは実データで**268ロット**確認されており、この場合**代表として選ばれなかった方のboard_nameが未登録でも、警告リストからは完全に漏れる**。

実データで再現確認済み：ロット`425973`は`GS-51P5*** Lﾒｲﾝ PNP`（登録済み）・`GS-51P5*** Lｻﾌﾞ`（未登録）の2つのboard_nameを持つ。この場合、一覧の「構成基板数」列にはfile_no=0447の行が正しく「未登録」と表示されるにもかかわらず、`unregistered_board_warnings`は空のままで、このロットは警告に一切現れなかった。

### 設計判断：構成基板数チェックを日報・月報から独立させる方針への転換

上記2つの欠陥（発見1・発見2）を修正するには、構成基板数チェックの起点を「その帳票に実績があった行」から「現在アクティブな全ロット」へ広げる必要がある。しかしこれは、**日報・月報という「実績の記録」としての性質**と、**「現在アクティブな全ロットの構成基板数健全性を継続的に監視する」という別の関心事**を1つの帳票機能に混在させることになるという懸念が生じたため、構成基板数チェックを日報・月報から切り離し、独立した「ロット進捗チェック」機能として実装する方針に転換した。

配置場所：この機能は「あるロットを構成する全ファイルNoが、それぞれ引落・仕掛・未生産のどの状態にあるかを取りこぼしなく確認する」というロット進捗管理の性質を持つため、日報・月報と同様、生産実績入力画面（`ui/kitting_production_entry.py`）に付随する画面として配置する方針とした（詳細な設計検討の経緯は2026-09-26の調査報告、`CANONICAL_DESIGN_DECISIONS.md` D-24参照）。

### `check_lot_progress()`の実装（独立関数、完了。UI画面はまだ未実装）

`services/production_service.py::check_lot_progress()`を新規実装した。

- `models/kitting_plan.py::list_plan_items_for_all_lots()`に`board_name`列を追加した上で、これと`get_app_cumulative_qty_bulk()`による一括取得（`list_incomplete_lots()`と同じ既存の最適化パターン、実績集計を含めても軽量）を組み合わせ、日報・月報の実績データ（`production_daily`のreport_date範囲）には一切依存しない設計にした。
- **`lot_no`単位ではなく`(lot_no, board_name)`単位でグルーピング**し、発見2の欠陥（代表1件方式）を解消した。
- 各`(lot_no, board_name)`について、面1省略後のdistinct file_no数と`get_board_structure()`のboard_countを比較し、`match`/`shortfall`/`excess`/`unregistered`の4状態を判定。
- ファイルNo単位（同一setup_file_no・面の複数バッチは合算）で「引落」「仕掛」「未生産」を算出。**引落は`status=="match"`の場合のみ通常の完成数ロジック（`calculate_lot_completion()`と同じ最小値）を使い、それ以外（不足・超過・未登録）は一律0とする**、構成基板数チェックと連動した仕様。不足分は「未確定」の仮想エントリ（引落0・仕掛0・未生産=ロットの代表発注数）として追加する。
- **動作確認**：693ロット・約1250件の`(lot_no, board_name)`組で全4状態が出現することを確認。`match`ステータスの実例で、算出した引落・仕掛・未生産の値が`calculate_lot_completion()`の結果と完全一致することを確認。処理時間は693ロットで約1.45秒（うちDB問い合わせ自体は計算取得0.007秒＋累計実績バルク取得0.005秒の合計12msと極めて軽量。残りは`get_board_structure()`の個別呼び出し約1250回分で、1回あたり約1.07ms。将来的にマスタを事前に1回だけ辞書化する最適化余地があることも判明したが、現状の絶対時間（1.5秒程度）は画面表示用途として許容範囲と判断）。

### 最重要・未解決：構成基板数マスタの値の意味に関する疑義（UI実装を一時中断中）

`check_lot_progress()`を`(lot_no, board_name)`単位で厳密に実行した結果、**693ロット・約1250件の`(lot_no, board_name)`組のうち約71%（850件）が`shortfall`（不足）と判定される**という、極めて高い不一致率が判明した。

原因調査の結果、「構成基板数（`board_count`）」というマスタ値が、本来「そのboard_name（基板）が実際に何ファイルNoから構成されるか」を意味すべきところ、マスタへの実際の登録内容では**別の概念（例：製品・ロット全体としての基板種類数等）と混同されて登録されている可能性がある**ことが、ユーザーとの対話を通じて判明した。

以前（§7、構成基板数マスタ照合機能の実装時、2026-09-24）の実データ検証では、ロット`287149`のメイン・サブ・ジュコウという3つのboard_nameが同一の`board_count=3`を持っていたことが確認されており、これは「ロット単位で共通の値」という前提（＝`board_count`はロット全体の基板種類数を表し、ロット内の各board_nameに同じ値が複製登録されている）を支持する事例だった。一方、今回の71%という不一致率は、**この前提と実際のマスタ登録内容の大部分が食い違っている**可能性を示唆しており、両者の関係を業務側で改めて確認する必要がある。

このため、**「ロット進捗チェック」のUI画面実装は一旦保留とし、上記のマスタ値の意味に関する確認結果を待つ方針とした**（`check_lot_progress()`関数自体の実装は完了済みだが、業務的に正しい判定基準が確定するまでUIには反映しない）。

---

## 17. 構成基板数マスタの意味の確定、判定ロジックの一本化、ロット進捗チェック画面の実装（2026-09-28、§16・D-24の疑義解消）

### 17.1 構成基板数（board_count）の意味の確定（重要な訂正）

§16末尾で保留となっていた「`board_count`の意味（board_name単位かロット単位か）」の疑義が解消した。

**確定した意味**：`board_count`は「そのboard_name（基板）自体が何ファイルNoから構成されるか」ではなく、**「そのロット（製品）が何枚の基板で構成されるか」を表す、ロット単位の値**である。業務側の運用は、計画をロット順に並べ、基板名でVLOOKUPしてその値をロット内の全board_name行に共通で入れる、というもの（同一ロット内の複数board_nameに同じ`board_count`が複製登録される）。

**実データでの裏付け**（`_diag_doc_verify2.py`、隔離コピー上で確認、読み取りのみ）：
- 複数board_nameを持つロット268件のうち255件（95.1%）で、ロット内の全board_nameが同一の`board_count`を持っていた。
- その255件について、`board_count`とロット内のdistinctファイルNo数（面1省略後）が一致する割合は227件（89.0%）だった。**この89.0%という数値は、後日ユーザーから提示された「約93%」という数値とは一致しなかった**（本ドキュメント作成者による独立再計算の結果。差の原因は未調査・未確認のまま記録する）。
- 同一board_nameが複数ロットに現れるケース（310件）では、いずれもロットが変わってもファイルNo数は変化しなかった（310/310件で一貫）。これは「`board_count`はboard_name自体の固有値ではなく、ロット側の事情で決まる」という上記の結論と整合する。

**先に判明していた「71%が不足」という結果の原因**：`check_lot_progress()`を`(lot_no, board_name)`単位で比較したのは、比較の粒度そのものが誤りだった。ロット単位で本来1つであるべき`board_count`を、ロット内の各board_name（例：メイン・サブ・ジュコウ）それぞれの実ファイルNo数とバラバラに比較していたため、複数board_nameを持つロットのほとんどで「不足」と誤判定されていた。

**この誤りの所在**：この設計（(lot_no, board_name)単位での比較）はClaude（本アシスタント）が提案・実装したものであり、データの不備ではなくClaude側の設計判断の誤りである。§16で発見した「未登録判定が代表1件しか見ていない」欠陥（board_name単位で判定すべきだった）への対策を、構成基板数の比較という別の観点（ロット単位で見るべきだった）にもそのまま横展開してしまったことが原因。両者は「未登録判定はboard_name単位」「構成基板数の比較はロット単位」という、本来別々の粒度で扱うべき問題だった。

### 17.2 修正後の判定粒度と、693ロットの内訳

修正後：
- **構成基板数との比較**：ロット単位（ロット内の全board_nameを通した、面1省略後のdistinctファイルNo数の合計と`board_count`を比較）。
- **マスタ未登録の判定**：引き続きboard_name単位（§16で修正済みの「代表1件方式の欠陥」対策はそのまま維持）。

実データ693ロットでの内訳（`check_lot_progress()`を隔離コピー上で実行し確認）：

| status | 件数 |
|---|---|
| 一致（match） | 584 |
| 不足（shortfall） | 73 |
| マスタ未登録（unregistered） | 32 |
| 超過（excess） | 2（lot_no=262752, 468052） |
| 構成基板数の食い違い（board_count_inconsistent） | 2（lot_no=271569, 344294） |

「食い違い」は、ロット内の複数board_nameの`board_count`自体が一致しない場合（本来1つであるべき値が割れているケース）に新設したstatus。271569・344294はいずれも同じ5つのboard_name構成（CC-1020系）で、うち「カートリッジROM」のみ`board_count=5`、残り4つは`board_count=4`という食い違いだった。

### 17.3 ロット進捗チェック画面（新規、`ui/lot_progress_window.py`）

`LotProgressWindow`（新規）。生産実績入力画面（`ui/kitting_production_entry.py`）の日報出力・月報出力ボタンの隣に「ロット進捗チェック」ボタンを追加（`open_lot_progress()`、`_open_singleton_window()`と同種のシングルトン管理）。

- 日報・月報のような期間・行の指定を必要とせず、`check_lot_progress()`（現在のDBの全アクティブ計画＋日付で区切らない累計実績）から、全ロットの状態（一致・不足・超過・マスタ未登録・構成基板数の食い違い）と、ファイルNo単位の引落・仕掛・未生産をまとめて表示する。
- 不足ロットの「未確定」仮想行、状態・ロットNo.でのテキスト絞り込み、ロット単位の縞模様（`lot_no→タグ`の辞書方式、`ui/daily_report_window.py::populate_report_tree()`と同じ考え方）、CSV出力（utf-8-sig）、列ヘッダークリックでのソート（同一ロットの行が分散しないよう、ロット単位のブロックにまとめてから各ブロック先頭行の値を代表値としてブロック単位でソートする近似方式。`board_name`等ロット内で値が変わる列では代表値が実際のロット全体を表すとは限らない点に注意、`sort_by_column()`のdocstring参照）を実装した。
- **実装中に発見・修正したフィルタ不具合**：「一部未登録」フィルタ（`_apply_filters()`）が、`status=="unregistered"`（全board_name未登録）のロット32件まで誤って含んでしまう不具合があった。`unregistered_board_names`が非空かどうかだけを見ており、「全board_name未登録」と「一部のboard_nameだけ未登録」を区別していなかったため。`row["_status_raw"] == "unregistered"`の場合を除外する条件を追加して修正した（`ui/lot_progress_window.py`のコード上に修正理由のコメントを残してある）。

### 17.4 ロット状態判定・引落ルールの一本化（`_evaluate_lot_status()`）

`services/production_service.py::_evaluate_lot_status()`に、構成基板数チェック（一致/不足/超過/未登録/食い違い）とロット進捗（引落・ファイルNo単位の仕掛/未生産）・「未確定」仮想行の判定を集約した。`check_lot_progress()`・日報・月報（`_build_report_rows()`）・仕掛数量抽出（`build_wip_extraction_rows()`）は、いずれもこの関数（または薄いラッパーの`evaluate_lot_status()`）を経由する。

**確定した引落ルール**（ユーザー確定、コード上は`DRAWDOWN_ZERO_STATUSES = {"shortfall"}`という定数で管理）：
- **不足（shortfall）**：引落は0（構成基板数に対してまだ計画にファイルNoが無いだけであり、実績があっても完成とは認めない）。
- **マスタ未登録・構成基板数の食い違い・超過**：引落は従来通り実績（`calculate_lot_completion()`の`completed_quantity`）ベースでそのまま計算し、代わりに「確認事項」欄で登録の修正を求める警告を出す（判定自体が成立しない、または自動補正すべきでないと判断したため）。
- 超過（ファイルNoの付け替え、例："963"→"S963"のような表記統合）は今回は自動処理しない（17.6「未解決事項」参照）。

**重要な訂正の経緯**：`check_lot_progress()`は当初、Claudeの仮の判断により「一致（match）以外はすべて引落0」として実装されていた。これはユーザーが決めた業務ルールではなく、マスタ未登録のロットまで一律に引落0になってしまう問題があったため、上記のルール（不足のみ引落0）に訂正した。この訂正は「集計期間が違うだけで、本来同じデータとしてそろっているべき」というユーザーの原則に基づく。

**変更前後の比較**（`_evaluate_lot_status()`一本化前の`check_lot_progress()`と、一本化後の結果を693ロットで比較）：差が出たのは不足（shortfall）のロット42件のみ（一致395件・超過2件・食い違い1件は差0。※この比較時点でのstatus内訳は本節17.2の最終結果とは母数が異なる中間状態のもの）。

**仕掛数量抽出（`build_wip_extraction_rows()`）への影響**：`calculate_lot_completion()`の`completed_quantity`をそのまま使っていた旧ロジックと、`_evaluate_lot_status()`経由の新ロジックを、実データ全693ロットに対して隔離コピー上で比較した（実DBへの保存は行っていない、試算のみ）。

| | 行数 | 仕掛数量合計 |
|---|---|---|
| 旧ロジック（再現） | 130 | 30,883 |
| 新ロジック | 188 | 46,759 |
| 差分 | +58 | +15,876 |

新たに抽出結果へ出現した（旧ロジックには全く現れなかった）ロットは39件、既存の抽出対象だったが行数が増えたロットが3件、あわせて58行増加。**この42件はいずれもstatus="shortfall"のロットだった**（実績が発注数に達していても構成基板数が不足しているため、旧ロジックでは仕掛数量が0と算出され抽出結果から漏れていたケース）。現在実DBに保存されている`wip_board_snapshot`（130行、合計30,883）はこの新ロジックでの再抽出がまだ行われていない状態であり、次回「仕掛数量抽出」を実行すると上記の新ロジックの値に置き換わる見込みである。この増加分は、以降の仕掛展開・在庫差異レポートの集計に影響する。

**日報・月報の「未完了数」との違い**：日報・月報の一覧に表示される「未完了数」（ロット単位、`発注数 − 引落`）と、ロット進捗チェックの「未生産」（ファイルNo単位、`order_qty - file_actual`）は、粒度も計算式も異なる別概念であることを明記しておく（同じ「未完了/未生産」という言葉でも指しているものが違う）。

### 17.5 日報・月報への反映

- 一覧に「構成基板数」列（既存、§16参照）に加えて「確認事項」列を追加。
- 確認事項の文言・文字色は`services/production_service.py`の`_build_lot_status_remarks()`・`_lot_status_color_category()`の2関数に一本化し、日報・月報・ロット進捗チェックの3画面が同じ定義を共有する（画面ごとに文言を書かない）。
- 色分け：確認・修正が必要な状態（未登録／食い違い／超過／一部未登録）は赤文字、不足（引落0）はオレンジ文字。ロット単位の縞模様は背景色を使っているため、文字色と背景色は別担当で競合しない（`ui/daily_report_window.py`の`_STATUS_COLOR_CATEGORY_TO_TAG`・縞模様タグ、双方を1行に同時付与できる設計）。
- 月報に、未登録・超過・食い違いの3種の警告ダイアログと、board_name単位に集約したCSV出力（3種）を追加。日報側は、面1/面2不整合警告が月報限定である既存方針（§5）に合わせ、これらの警告ダイアログは出さない。
- 仕掛数量抽出（月報）は、確認・修正が必要なロットを含む場合のみ、`save_wip_snapshot()`実行前に確認ダイアログを表示する（「いいえ」を選ぶと保存しない）。
- 「未確定」仮想行の`board_name`は、ロット内の登録済みboard_name（複数ある場合は先頭の1件）を代表として表示している（表示上の妥協であり、正確な対応関係を表すものではない）。

### 17.6 未解決・未確認の事項

- **構成基板数の食い違い2件**（lot_no=271569, 344294）：カートリッジROMのみ`board_count=5`、他4board_nameは`board_count=4`。マスタ登録者の意図の確認待ち（画面上は「構成基板数不一致」として区別表示される）。
- **超過2件**（lot_no=262752, 468052）：ファイルNoの表記統合（例："963"と"S963"のような別表記の候補）を試みたが、統合の根拠となる候補が見つからなかった。ファイルNoが途中で変わる場合の扱いは今後の検討課題。
- lot_no=344294は実績が1件も登録されていないため、日報・月報には一切表示されない（既存の制約：日報・月報の構成基板数チェックは、その期間に実績があるロットにしか現れない。§16「重大な発見1」参照）。ロット進捗チェック画面はこの制約を持たないため、こちらでは表示される。
- **生産実績入力画面の情報欄が未統一のまま**：`ui/kitting_production_entry.py`の構成基板数の色分け照合（1236行目付近）は、独自に`calculate_lot_completion()`と`get_board_structure()`を呼んでおり、`_evaluate_lot_status()`（今回の一本化）の対象に含まれていない。同一ロットについて、この画面と日報・月報・ロット進捗チェックとで異なる説明・異なる引落の見え方をする可能性がある（未確認、実機での突き合わせは行っていない）。
- **実機目視確認が未実施の表示項目**：日報・月報の縞模様（背景色）と確認事項の文字色を同時表示した際の見え方、印刷プレビュー（`ReportPreviewWindow`、固定ピクセル幅のCanvas描画で折り返し機構を持たない）での「確認事項」列のはみ出し（PDF出力側は`Paragraph`による自動折り返し対応済みだが、印刷プレビューは別経路のため未対応。この制約は`ui/daily_report_window.py`のコード上のコメントに既に記載されている）、仕掛展開画面の「確定済み(スナップショットなし)」行の赤字表示、の3点はいずれも実機での目視確認が済んでいない。
- 日報・月報の構成基板数比較を、ロット進捗チェックと同様の「期間の実績に依存しない」仕組みへ全面的に寄せるかどうかは未決定。
- 構成基板数マスタの未登録（実データで表記ゆれの可能性がある候補は約9%）について、自動判定は行わない方針（§16参照）は今回も変更していない。

### 17.7 運用上の反省（記録）

- **実装の前に、その値・粒度の意味が業務の実態と合っているかを確認しておくこと**：今回は構成基板数の意味の確認が実装の後になり、(lot_no, board_name)単位での厳密な比較を先に実装した結果、約71%という誤った不一致率を報告することになった。
- **Claudeが置いた暫定的な判断（「一致以外はすべて引落0」）が、ユーザーの決めたルールであるかのように実装へ残ってしまった**：暫定の判断は暫定であると明示し、影響が広がる前に確認を取るべきだった（`CANONICAL_DESIGN_DECISIONS.md` D-18と同種の教訓、詳細は同ファイルの追記箇所参照）。
- 「調査結果が届いていないのに結論を言わない、待つよう言われたら待つ」という既存の原則（D-18）が、今回も当てはまる事例だった。

---

## 18. 確定登録の後にスナップショットから消えたロットの訂正導線問題（発見・対策、2026-09-28）

### 発見の経緯

仕掛の確定登録（`wip_scrap_records`）の後で仕掛が解消し、スナップショット（`wip_board_snapshot`）から該当ロットが消えた場合に、その確定行を人が見つけて訂正（数量修正・削除）する手段があるかを、隔離コピー上の再現を交えて調査した。

### 判明した事実

- `wip_board_snapshot`は月報の「仕掛数量抽出」のたびにテーブル全体が置き換わるスナップショット、`wip_scrap_records`は独立したキーを持つ確定済みの消費数量の台帳であり、両者の間に外部キーや自動同期の仕組みは無い。
- 確定登録後にスナップショットから当該ロットが消えても、確定行は自動的に削除・更新されない。**仕掛製品レポート**（`list_wip_scrap_summary()`ベース）と**在庫差異レポート**（`query_wip_totals()`ベース）はいずれも`wip_scrap_records`のみを参照するため、この確定行はそのまま残り続ける。
- 一方、**仕掛展開画面の一覧**（`ui/wip_expansion_window.py::_fetch_wip_list_rows()`）は`wip_board_snapshot`の走査が起点だったため、この確定行は一覧に出ず、既存の「実績修正」ボタン・`WipScrapCorrectionWindow`を開く唯一の入口（`on_open_wip_scrap_correction()`）にも到達できなかった。仕掛製品レポートのダブルクリック（`expand_by_identity()`）も同じ一覧データを検索するため同様。
- 比較として、仕損（NG）側の一覧（`ui/ng_input_window.py::_fetch_ng_list_rows()`）は「申告（`ng_declarations`）∪ 展開済み（`scrap_records`）」の**和集合**で作られており、申告側に対応が無い展開済み行も「展開済み（申告記録なし）」として一覧に残り、`ScrapCorrectionWindow`で訂正できる。仕掛側だけがこの非対称な設計になっていた。

### 運用の前提（ユーザー確定）

仕掛数量抽出は日報・月報のデータが確定した後に行うものであり、抽出後のスナップショットの変化を追いかける仕組みは持たない。間違いが分かった場合は、仕掛または仕損の画面で人が訂正する。確定登録の自動削除・自動更新は行わない。

### 対策（実装済み）

`ui/wip_expansion_window.py::_fetch_wip_list_rows()`を、NG側と同じ「スナップショット ∪ 確定登録済み（`wip_scrap_records`）」の和集合方式に変更した。

- スナップショットに対応行が無い確定済み行は、状態列を`STATUS_CONFIRMED_ORPHAN = "確定済み(スナップショットなし)"`として一覧の末尾に追加し、Treeviewタグ（`confirmed_orphan`、赤文字）で区別する。
- `file_no`・`lot_no`・`production_side`は`list_wip_scrap_summary()`から取得する（実際にfile_noを返すことをコード・隔離コピー実行の両方で確認済み）。基板名・実装ライン・仕掛数量・抽出日時はスナップショット由来の情報のため空欄のままとする（`list_wip_scrap_summary()`は`mounting_line`も実際には返すが、指示に従いこれも空欄にしている）。
- 既存の「実績修正」ボタン（`on_open_wip_scrap_correction()`）・`_get_selected_wip_row_identity()`は無改修のまま、この追加行に対しても正しく機能する（Treeview選択から`(kitting_list_no, lot_no, file_no, side)`を取るだけの既存ロジックがそのまま使えたため）。
- この行のダブルクリックは展開を実行せず、「スナップショットに存在しないため展開できません。『実績修正』で確認・訂正してください」というメッセージを表示するよう変更した。
- `expand_by_identity()`（仕掛製品レポートのダブルクリックから呼ばれる、今回は動作を据え置く方針）・`_get_bulk_wip_expand_targets()`（一括展開・登録）・`services/unprocessed_check_service.py::check_unprocessed_items()`（在庫差異レポートを開く前の未処理項目チェック）は、いずれもこの追加行を対象に含めないことを、コード確認と隔離コピー上の実行の両方で確認済み。

### 動作確認（隔離コピー、config.DB_PATH・config.APP_DATA_DIR双方を隔離）

スナップショットに1行あるロットを確定登録し、そのロットを含まない内容でスナップショットを置き換えた状態で確認：追加行が一覧に出現、「実績修正」ボタンから`WipScrapCorrectionWindow`が正しい`kitting_list_no`・`lot_no`・`production_side`で開く（数量修正・削除が`wip_scrap_records`に反映されることも確認）、ダブルクリックでは展開が実行されずメッセージが出る、`check_unprocessed_items()`の未確定件数は追加行の有無で変化しない、一括展開・登録の対象にも含まれない、ソート・絞り込みでもエラーは出ない、をいずれも確認した。

実データの隔離コピーでは、この状況（確定登録後にスナップショットから消えたロット）に該当するものは現時点で0件であり、既存の「確定済み」2件・「未確定」128件は変更前後で同じだった。**実際の画面上での見え方（赤字表示等）は未確認**。

### 対象外とした点

仕掛製品レポートのダブルクリックの動作（スナップショットに無い行では従来通りのエラーメッセージのまま）、`wip_scrap_records`の自動削除・自動更新、`save_wip_snapshot()`自体の変更、在庫差異レポートの集計方法は、いずれも今回は変更していない（ユーザーの指示通り）。

---

## 19. 日々の引落算出に向けた調査：月報の表示対象確認とreport_dateの信頼性確保（Step1、2026-09-29）

### 背景・新しい要望

「日ごとの引落（前日比の増分）・完了一覧・未完了一覧を見たい」という新しい要望が挙がり、その実現に向けた調査・準備（Step1）に着手した。

対話で確定した点：
- 現状の日報（`build_daily_report()`）は「その日（`report_date`）に実績登録があった行」を表示するのみで、**日ごとの引落（前日からの増分）を出す仕組みは無い**（`_evaluate_lot_status()`・`calculate_lot_completion()`はいずれも日付条件を持たない「現在の累計」のみを返す。§14〜17参照）。
- ユーザーが求める「引落」は、`calculate_lot_completion()`が返す**完成数（累計の最小値）そのものではなく、前日までの累計との差分（日々の増分）**であることが対話で確定した。
- 目的は2つ：①日ごとの引落・仕掛・未生産が、日をまたいで正しく連続しているかを確認できること、②日報を「その日に引落があったものだけの一覧」と「まだ完了していないものの進捗一覧」に分けて見られるようにすること。
- 「完了報告」自体はこのアプリの外で行われる記録であり、対応不要（ユーザー確定）。

### 月報の表示対象の確認（調査結果）

`build_monthly_report()`・`_build_report_rows()`（`services/production_service.py`）を実際に読み、実データ（隔離コピー、`db/inventory.db`のコピー）に対して`build_monthly_report()`を広い期間で実行して確認した。

- 抽出条件は`list_daily_production_range()`の`report_date`範囲（`WHERE report_date >= ? AND report_date <= ?`）のみで、**完了・未完了や仕掛の有無による除外条件は無い**。
- **完了済み（`lot_remaining=0`）ロットの行**も、**仕掛が0の行**も、実データで実際に月報の集計結果に含まれることを確認した（`lot_no='317114-'`・`'389672'`・`'317564'`がいずれも完了済みかつ`surplus_qty=0`の状態で表示された）。
- `ui/monthly_report_window.py::on_row_double_click()`のdocstringにも「完了済み（生産実績入力画面の一覧からは除外済み）の計画でも、production_dailyに実績が残っている限りここから修正できる」との明記があり、月報が完了済み行を含む前提で作られていることが裏付けられた。

**ユーザー確認**：月報にソート機能を追加し、仕掛のみ表示等ができるようにする案は、**今後の対応として保留**（本ファイル§6の未対応事項に記録）。

### report_dateの信頼性に関する重大な発見と対応（完了）

日々の引落（前日比）を計算するには、`production_daily.report_date`を使って「指定日時点の累計」を再構成する必要があるが、調査の結果、**手動で既存の実績を再登録（上書き）すると、report_dateが実行日（今日）に書き換わってしまう複数の経路**が見つかった。CSV経由の登録（自動取込・ステージング・右クリック主経路・一括登録）はいずれも正しくCSVの払い出し日を使っており、壊れていなかった。

**ユーザー決定の方針**：「実際に生産した日」と「修正した日」は別の情報であるべきなので、`report_date`が明示的に渡されない場合は、既存行の`report_date`をそのまま保つ（今日には書き換えない）方針で統一した。

調査・対応の詳細（発見された3つの経路・修正内容・6シナリオでの動作確認、および検証過程で追加で見つかったreport_dateの空値（NULL/空文字列）に関する堅牢化）は、`UI_WORKFLOW_FIXES_NOTES.md`グループAB追記8・追記9に記録した（対象コード`models/production.py`・`services/production_service.py`・`ui/kitting_production_entry.py`が生産実績入力画面の既存のグループABの系列に属するため、実施記録は同ファイルに集約し、本ファイルからはポインタのみとする）。確定した設計方針は`CANONICAL_DESIGN_DECISIONS.md` D-28にも記録した。

### Step2・Step3の方針転換（完了）

当初はStep2・Step3を、本節で確保したreport_dateの信頼性を前提に「`report_date <= 指定日`のSUMで過去の累計を逆算する」方式で実装する想定だったが、調査の結果、`production_daily`が「1計画1行、常に上書き」の設計である以上、**後からの修正・遅延登録によってreport_date自体が変わり、過去に計算した累計が事後的に変わりうる**という、report_dateの信頼性確保だけでは解消できない限界があることが分かった。ユーザーの判断により、逆算方式ではなく「実績の登録・修正のたびに、その時点の状態を記録として積み上げる」方式（`lot_status_history`）に転換した。詳細は§20参照。

---

## 20. ロット状態履歴（lot_status_history）の新設と、日々の引落一覧（2026-09-30、D-29〜D-31）

### 新テーブルとその位置づけ

`models/lot_status_history.py`（新規）に`lot_status_history`テーブルを新設した。実績の登録・修正のたびに、そのロットの状態（`_evaluate_lot_status()`の結果）を1行の履歴として自動で記録する「イベントログ」である。既存の`production_daily`（最新状態のみを保持する上書き型）・`wip_board_snapshot`（最後の抽出のみを保持するスナップショット型）とは異なり、**過去の時点の状態を後から振り返れる**という性質を持つ唯一のテーブルになる。

列構成：`id, recorded_at, lot_no, status, board_count, visible_file_no_count, files_detail, total_surplus_qty, total_not_produced_qty, trigger_source`。`files_detail`には、ロットを構成する全board_nameのファイルNo単位の内訳（引落・仕掛・未生産を含む）を、代表1件への省略をせず全てJSON配列として保持する（1ロットに複数board_nameが存在するケースが実データで268件確認されている、D-25参照）。`total_surplus_qty`・`total_not_produced_qty`は`files_detail`の集計値を非正規化して保持する列で、「そのロットが仕掛0・未生産0になった最初の記録」をJSON解析なしで検索できるようにするためのもの。

採用した設計の理由（D-29）は`CANONICAL_DESIGN_DECISIONS.md` §17参照：`production_daily.report_date`ベースの逆算では、後からの修正・遅延登録によって過去の累計が事後的に変わりうるという限界があったため、`recorded_at`（実際に書き込まれた壁時計時刻、後から変わらない）を基準にする方式へ転換した。

### 記録フックの入れ方（D-30）

`models/operation_log.py`（操作履歴機能）と同じパターンを踏襲し、共通フックを1か所に一元化せず、実際に書き込みが起きる5箇所に個別に`record_lot_status_snapshot()`呼び出しを追加した：`services/production_service.py`の`register_daily_result()`・`overwrite_daily_result()`・`update_daily_result()`・`delete_daily_result()`、`services/db_migration_carryover.py`（月次DB引き継ぎ）。記録失敗時は、本来の登録・修正処理自体は失敗させない（ログに残すのみ）設計とした。`update_daily_result()`・`delete_daily_result()`は`prod_log_id`しか受け取らないため、UPDATE/DELETE実行前に対象行をSELECTしてlot_noを特定する必要がある点に注意（特にDELETEは実行後に対象行が参照できなくなるため、必ず実行前に行う）。

### 一括登録時の性能最適化

Shift+S一括登録（`ui/production_import_staging_window.py::_on_bulk_register()`）で、当初は選択行ごとに履歴記録していたため、1件あたり数十ms（主にSQLiteのcommit同期コスト）が積み重なり、100件規模で約4〜5秒の追加遅延が見積もられた。`register_daily_result()`/`overwrite_daily_result()`に`record_history`引数（デフォルトTrue、既存呼び出し元には影響しない）を追加し、一括登録時は`False`を渡して行ごとの記録を抑制、バッチ処理の最後に影響を受けたdistinctなlot_noだけ1回ずつ記録する方式に変更した。実データでの計測：重複の少ない100ロット（1ロット1行）では短縮効果はほぼ無し、重複の多い実データ（100行・distinctなlot_noは5件）では2.29秒→0.83秒と約64%短縮した。

**誤った計測結果の教訓**：検証の過程で「実UI経由で5行の一括登録に約50秒かかり原因不明」という事象が一時的に報告されたが、原因は検証スクリプト側が完了通知ダイアログ（`messagebox.showinfo`）をモックし忘れていたことによる誤計測であり、実装自体には性能上の問題が無かったことが後日の再検証で判明した（実際は0.2秒程度）。詳細は`CANONICAL_DESIGN_DECISIONS.md` §17参照。

### 日々の引落一覧（新規画面）

`services/lot_status_history.py::get_daily_drawdown(target_date)`・`ui/daily_drawdown_window.py::DailyDrawdownWindow`を新規実装した。「指定日以前で最新の記録」と「前日以前で最新の記録」の、ファイルNo単位の`lot_completed`の差分を計算して表示する。生産実績入力画面に「日々の引落一覧」ボタンを追加した。

**重要な性質（バグではなく仕様として記録）**：複数バッチで構成されるロットは、引落が「各バッチの登録ごとに段階的に増える」のではなく、「全バッチが揃った瞬間に一括で増える」。これは`calculate_lot_completion()`の「全ファイルNo・面の実績の最小値」ロジック（D-21）に起因する：一部のバッチだけ実績を積み増しても、他のファイルNoの実績が追いついていなければ最小値（＝引落）は変わらないため、日々の引落一覧上では0のまま推移し、最後の1件が揃った日に一気に増える形で表示される。この画面を実際に使う際の注意点として記録する。

### 未完了一覧についての判断（D-31）

「未完了一覧」は、`lot_status_history`から新規に実装するのではなく、既存の`check_lot_progress()`（`ui/lot_progress_window.py`、現在の全アクティブロットを都度再計算）をそのまま使う方針とした。理由：`lot_status_history`は実績の登録・修正が実際に起きたロットにしか記録が無いイベントログであるため、「一度も実績登録が無いロット」（最も「未完了」らしいロット）がそもそも集計対象に含まれず、静かに一覧から漏れる。これは§16・D-24で修正した「対象期間に実績が無いロットが検知されない」という欠陥と本質的に同じ種類の見落としであり、繰り返すべきではないと判断した。

---

## 21. 日報・月報の統合画面（UnifiedReportWindow）への一本化（2026-09-30〜10-01、D-32〜D-34）

### 統合の経緯と範囲

個別の画面（日報・月報・ロット進捗チェック・日々の引落一覧）が増えてきたことを受け、設計の見直しを検討した。「実績のあるロット全体を土台にした全4画面統合」案も検討したが、ロット進捗チェック（現在の全アクティブロットが母集団、期間の概念なし）・日々の引落一覧（過去の時点を振り返る、`lot_status_history`依存）は、日報・月報（`production_daily`の期間集計）とは母集団・時間軸の性質が異なるため統合を見送り、**日報・月報のみを`ui/unified_report_window.py::UnifiedReportWindow`に統合**する方針で確定した（D-33）。

### 統合を後押しした発見

調査の結果、`DailyReportWindow`は起動直後（`build_daily_report()`）を除けば、日付を変更するたびに`build_monthly_report(selected_date, selected_date)`を呼んでおり、**実質的に「1日だけの月報」として動いていた**ことが判明し、統合が技術的に無理のない変更であることを裏付けた。

さらに、`DailyReportWindow`が`configure_status_color_tags()`を一度も呼んでおらず、**日報画面では「確認事項」欄の赤字・オレンジ字による警告表示が機能していなかった**（月報では機能していた）という重大なバグを発見した。統合画面の実装と同時にこのバグを修正した（両方のタグ設定関数を必ず呼ぶようにした）。

### UnifiedReportWindowの機能一覧

- 期間選択：今日/今週（月曜起算）/今月/カスタム範囲。選択に応じて開始日・終了日を自動計算し、`build_monthly_report(from_date, to_date)`を呼ぶ（「今週」の起算日が業務慣習と合っているかは未確認）。
- ロット単位の縞模様・状態による文字色分け（上記バグ修正込み）。
- 仕掛数量抽出（`build_wip_extraction_rows()`→`save_wip_snapshot()`）とその保存前確認ダイアログ、マスタ未登録・構成基板数超過・構成基板数不一致の3種のCSV出力：いずれも旧`MonthlyReportWindow`限定機能だったものを移植した。
- 5種の警告（面1/面2不整合・発注数不一致・マスタ未登録・構成基板数超過・構成基板数不一致）のオン/オフ切り替え：オフにしても裏側の計算自体は省略せず（`_build_report_rows()`が5種まとめて1回で計算する設計のため、かつ計算コスト自体軽いため）、ダイアログ表示のみ抑制する。CSV出力ボタンは警告のオン/オフと独立して常に有効（「ダイアログで毎回知らされたくない」と「その異常を一切気にしない」は別の意味、という判断による）。
- 列ヘッダーでのソート（ロット単位のブロックを保つ、後述）。
- 行の絞り込み：状態（一致/不足/超過/未登録/構成基板数不一致）・NGの有無・ロットNo.部分一致の3軸（`ui/wip_expansion_window.py`・`ui/ng_input_window.py`の「テキスト部分一致＋チェックボックス式ポップアップ」パターンを踏襲）。状態・NGの有無を判定するため、`_build_report_rows()`の各行に`status`・`has_ng`キーを新設した（`has_ng`は`scrap_records`・`ng_declarations`を事前一括取得して判定）。
- 列の表示/非表示：列ヘッダーの右クリックメニュー（Treeviewの`displaycolumns`で制御）。CSV/PDF出力は表示設定とは独立に常に全列を出力する。

### 統合作業中の見落としと自己点検

統合作業の途中で、月報限定だった「仕掛数量抽出」「3種のCSV出力」への導線が、ボタン置き換えに伴い一時的に失われる見落としが発生したが、自己点検（旧`MonthlyReportWindow`の全メソッドと新画面のメソッド一覧を突き合わせる確認）により発覚し、追加実装で解消した。

### クラスの削除

`MonthlyReportWindow`・`DailyReportWindow`は、全メソッドの移植漏れが無いことを確認した上で削除した。`daily_report_window.py`モジュール自体は削除せず、他画面（`ui/unified_report_window.py`・`ui/wip_expansion_window.py`等）が再利用する共通関数・共通クラス（`populate_report_tree`・`configure_lot_stripe_tags`・`configure_status_color_tags`・`_row_to_values`・`build_daily_report_pdf`・`REPORT_HEADERS`・`ReportPreviewWindow`等）の置き場所として維持している。

### 重要な設計上の発見：ソートのブロック検出方式の変更（D-34）

ロット進捗チェック画面の`sort_by_column()`（「現在の並びで連続している区間をブロックとする」方式）をそのまま統合画面に適用しようとしたところ、既存の`_build_report_rows()`が**同一ロットの行の連続性を保証しない設計**であることが判明した。実データで、同一ロット（lot_no=298055）の実データ行が922行中7番目、「未確定」仮想行2件が842・843番目という、大きく離れた位置に分断される実例を確認した。原因は、実データ行が`production_daily`のレコード順（ロット単位にグルーピングされない）で構築されること、「未確定」仮想行が全ロット分まとめて末尾に追加されることの2点。

対応として、ブロックの検出方式を「lot_no値が一致する行を、物理的な位置に関わらず全て1つのブロックに集める」辞書ベースの方式に変更した。実データ全442ロットで、ソート後に分断されるロットが0件であることを確認済み。

### 未確認・申し送り事項

- 「今週」の起算日（月曜起算とした）が業務慣習と合っているか。
- 列の表示/非表示のレイアウトの見た目、チェックボックスの状態を次回起動時に保持するか。
- 大量データ（922行）でのフィルタ操作の体感速度。

いずれも業務側の意向確認・実機での目視確認が済んでいない。

---

## 22. 「登録日」列の追加と、未完了計画のDB間引き継ぎルールの見直し（2026-10-01、D-35〜D-37）

### 22.1 UnifiedReportWindowへの「登録日」列の追加（D-35）

`UnifiedReportWindow`に`production_daily.report_date`を「登録日」として表示する列を追加した。Treeview表示・CSV出力・PDF出力すべてに反映している。

調査の結果、`_build_report_rows()`の各行はもともと1つの`production_daily`レコード（＝1つの`kitting_list_no`）に1:1対応しているため、`build_wip_extraction_rows()`が使うような複数バッチの代表選定ロジック（`_pick_representative_plan_item()`、plan_start_datetimeが最も新しいものを採用する方式）は不要だった。「未確定」仮想行は元になる`production_daily`レコードが存在しないため、登録日は空欄になる。

実装時、`ui/daily_report_window.py::ReportPreviewWindow.COL_WIDTHS`（印刷プレビュー用の列幅定数、`COL_HEADERS = REPORT_HEADERS`と同じインデックス対応の仕組みで結合されている）への列追加が当初漏れていたことに気づき、合わせて修正した。これを放置すると、プレビュー画面のヘッダーと列幅が1列分ずれて表示されるという実害があった（`REPORT_HEADERS`・`PDF_COL_WIDTHS`・`_row_to_values()`等、同じインデックス対応で結合された定数を変更する際は、すべての結合先を洗い出す必要があるという教訓）。

「登録日は修正登録等で事後的に書き換えられうる、現在その行に保存されている最新値に過ぎない」という既知の限界（§19で既出）は、今回も解消していない（意図的にスコープ外とした）。

### 22.2 未完了計画のDB間引き継ぎルールの見直し（D-36、D-37）

`services/production_service.py::list_incomplete_lots()`（`db_migration_carryover.py::carry_over_incomplete_lots()`が使う未完了ロット抽出関数）を、構成基板数チェックを含まない`_compute_lot_completion()`直接呼び出しから、`_evaluate_lot_status()`ベースの判定に統一した。

**発見の経緯**：2026-09-28に日報・月報・仕掛数量抽出・ロット進捗チェックの4機能の判定ロジックを`_evaluate_lot_status()`へ一本化した（D-26）際、DB間引き継ぎの未完了判定（`list_incomplete_lots()`）はこの一本化の対象に含まれておらず、構成基板数チェックを考慮しない旧ロジックのまま取り残されていたことが、別件の調査で判明した。

**未完了とみなす新ルール**：
- `status=="shortfall"`（構成基板数不足）：引落が0扱いのため、`remaining_quantity`の値に関わらず無条件で未完了とする。
- `status=="match"`：従来通り`remaining_quantity<=0`で完了判定する（退行なし）。
- `status in ("unregistered", "board_count_inconsistent", "excess")`：構成基板数との整合性チェック自体が成立しない、または自動補正を行わないため、`_evaluate_lot_status()`が従来通り実績ベースの値をそのまま`lot_completed`として返す（D-26で確定済みの方針）。その値で完了判定する。

**実データでの変化**：390件中26件が新規に未完了として追加判定された（いずれも`status=shortfall`）。除外されたロットは0件（shortfallの0扱いは「未完了側へ寄せる」方向にしか働かないため、理論上も妥当）。別途16件は元々未完了扱いだったが、`completed_quantity`／`remaining_quantity`の値が実績ベースの値から0扱いに訂正された。matchロット283件全件で旧ロジックと完全一致（退行なし）を確認済み。

**ユーザー確定の新ルール（未着手計画の50日上限、D-36）**：
1. 未着手（旧DB側に実績が1件も無い）の計画は、`plan_start_datetime`（実装開始予定日）が引継ぎ日（引き継ぎ処理の実行日）から50日以内のものだけを引き継ぎ対象とする。50日より先の計画は引き継がない（データの蓄積による肥大化防止）。
2. `plan_start_datetime`が空・パース不能（形式不正）な未着手計画は、安全側（除外せず含める）で扱う。理由：50日上限の目的は「先の話でまだ不要な計画データが無制限に積み上がるのを防ぐ」ことであり、判定不能なデータを誤って除外すると、実は近日中に着手予定だった計画が新DBから跡形もなく失われ、後から気づいて復旧する手段が無いリスクがある。誤って含めてしまっても「本来除外したい計画が1件余分に残る」という软らかい失敗で済むため、「データを自動的に失わない」という既存方針（D-11等）に揃えた。該当した計画は`carry_over_incomplete_lots()`の戻り値`undetermined_plan_start_datetime_items`に記録し、可視化する。
3. 完了しているロットは引き継がない（レポート出力の有無は問わない）。仕掛・未完了が残っているロットは、そのロット全体（全ファイルNo、完了済みのファイルNoも含む）をまとめて引き継ぐ（D-37）。**調査の結果、この要件は既存実装（`_fetch_plan_items_for_lot()`・`_fetch_production_daily_for_lot()`がlot_no単位で無条件に全件取得する設計）で既に満たされていた**。実データのlot_no=106661（4ファイルNo中2件完了・2件未着手）で、完了済みファイルも含めてロット全体が正しく引き継がれることを確認し、コード変更は行わなかった。

**ドキュメントの記載漏れの解消**：`wip_scrap_records`が「コピーしない」対象であることを、`services/db_migration_carryover.py`のモジュールdocstringに明記した（動作自体は従来通りコピーしない設計だったが、ドキュメントへの記載漏れがあった）。

### 22.3 未確認・申し送り事項

- `plan_start_datetime`が空・パース不能な未着手計画は、実データ上0件だった。そのため「安全側で含める」分岐の実データでの動作は未検証（ロジックのみ確認）。
- 50日という上限値自体の妥当性（実際の生産サイクルに対して適切か）は、業務知識が必要な判断のため確認していない。
- 「共通マスタがDB間引き継ぎ機能の検証中にコピーされず再評価結果が変わる」という、より大きな設計上の課題を発見した。詳細は`BOM_MIGRATION_NOTES.md` §14・`CANONICAL_DESIGN_DECISIONS.md` §18.3（D-38）参照。
