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

## 4. 未対応・将来の検討事項

- `scrap_records`向けの1行単位の修正・削除機能(`update_scrap_record()`/`delete_scrap_record()`)は実装していない(ユーザー決定により、kitting_list_no単位の洗い替え(`replace_scrap_records()`)で運用する方針としたため)。
- NG一覧のフィルタ・ソート機能は、計画一覧のロジックをコピー&適応した実装であり、共通コンポーネントとしては切り出していない(将来、両者の挙動を同時に変更する必要がある場合は両方修正が必要な点に注意)。
- `find_opposite_side_plan()`の複数候補時「最も近いplan_start_datetimeを自動選択」は、業務上本当に正しい組み合わせを保証するものではない(日時が近いというだけの推測)。誤った組み合わせになるケースがないか、実運用で注意が必要。
