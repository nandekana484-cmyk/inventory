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

---

## 6. 未対応・将来の検討事項

- ~~`scrap_records`向けの1行単位の修正・削除機能(`update_scrap_record()`/`delete_scrap_record()`)は実装していない(ユーザー決定により、kitting_list_no単位の洗い替え(`replace_scrap_records()`)で運用する方針としたため)。~~ → **方針転換し実装済み（2026-09-15、§13参照）**。`scrap_records`・`wip_scrap_records`双方に1行単位のUPDATE/DELETE関数と、専用の個別修正画面（`ScrapCorrectionWindow`/`WipScrapCorrectionWindow`）を追加した。グループ単位の洗い替え（`replace_scrap_records()`/`save_wip_scrap_records()`）自体は廃止しておらず、両者は共存する（個別修正は再展開・再登録で上書きされ得る点に注意、§13参照）。
- **NG一覧・仕掛一覧の一括展開・登録機能** → **完了**（NG一覧：2026-09-14、仕掛一覧：2026-09-15、§12参照）。仕掛展開画面にも同じ設計で実装済み（NG入力画面との重要な違い：`wip_board_snapshot`の行は既にfile_no・面・lot_no・mounting_lineを保持しているため計画候補の曖昧さ自体が存在せず、曖昧さが発生し得るのは実装ラインが複数のケースのみ）。
- NG一覧のフィルタ・ソート機能は、計画一覧のロジックをコピー&適応した実装であり、共通コンポーネントとしては切り出していない(将来、両者の挙動を同時に変更する必要がある場合は両方修正が必要な点に注意)。
- `find_opposite_side_plan()`の複数候補時「最も近いplan_start_datetimeを自動選択」は、業務上本当に正しい組み合わせを保証するものではない(日時が近いというだけの推測)。誤った組み合わせになるケースがないか、実運用で注意が必要。
- **ロード画面（`LoadingWindow`＋非同期パターン）の追加** → **完了（2026-09-11）**。CSV/TSV読み込み処理における非同期ロード画面の有無を9画面調査した結果発見された未対応箇所（NG入力画面・仕掛展開画面、各種CSVインポート5画面）は、いずれも対応が完了した。詳細は`UI_WORKFLOW_FIXES_NOTES.md`グループR参照。
- **「対象外」マークの仕組み** → **完了（2026-09-11）**。NG一覧・仕掛一覧それぞれに実装した。詳細は本ファイル§10参照。これにより、「全項目が展開済みまたは対象外になった状態でのみ在庫差異レポート作成を許可する」というゲート機能も実現した（本ファイル§11参照）。
- 上記2項目は共有フォルダ運用・DBロック機構（`UI_WORKFLOW_FIXES_NOTES.md` グループQ）と同時期に整理された未完了タスクの一部だった。バックアップ機能・.exe化（共有フォルダ運用の最終目標）は引き続き保留中。`UI_WORKFLOW_FIXES_NOTES.md` §4を参照。

---

## 7. 構成基板数マスタ(新機能)

基板名(`board_name`)単位で「構成基板数」(表示のみの参考情報、BOM計算・実績登録等の他の処理には一切使わない)を管理する新マスタを追加した。

**新テーブル**：`board_structure_master`(`board_name TEXT PRIMARY KEY, board_count REAL, board_name_normalized TEXT NOT NULL, imported_at`)。既存の`models/parts_attributes.py`と同じ「CSVをマスタとした差分同期」パターン(delete-then-insert、upsert + `delete_board_structure_not_in()`)を踏襲(`models/board_structure_master.py`、`db/migration_012`)。

**CSVインポート画面**：`ui/board_structure_import_window.py`(新規)。`ui/parts_attributes_import_window.py`と同構成(CSV選択→Treeview表示→インポート実行、タブ区切り、`_open_csv_with_fallback()`をコピー流用)。メインメニュー「共通マスタ」セクションに「15. 構成基板数マスタインポート」ボタンを追加。

**表記ゆれ対策**：`normalize_board_name()`(NFKC正規化＋小文字化＋前後空白除去＋連続空白圧縮。`services/production_import_service.normalize_product_name()`と同一ロジックだが、models層からservices層への依存を避けるため複製)で正規化した値を`board_name_normalized`列として保存し、`get_board_structure()`はこの列で検索する(検索のたびに正規化計算をやり直さない設計)。

**生産実績入力画面への表示**：`ui/kitting_production_entry.py`のinfo_frame、「ロット未完成数」行の直後(row=8。以降の「基板別実績(file_no)」はrow=9へ1つずらした)に「構成基板数」行を追加。`search_plan()`内で`plan["board_name"]`をキーに`get_board_structure()`を検索し、登録が無ければ「未登録」と表示する。

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
