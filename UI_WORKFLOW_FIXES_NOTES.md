# UI_WORKFLOW_FIXES_NOTES.md

## 1. 概要

**目的**：生産実績入力画面（`ui/kitting_production_entry.py`）を中心に発見された一連のUI・ワークフロー上の問題（計画の削除・表示、実績履歴、ロード画面、多重表示、メインメニュー構成、計画一覧の機能拡張等）について、原因調査・決定事項・実施した修正をまとめ、次にこのプロジェクトを触る人（将来の自分を含む）が同じ調査をやり直さずに済むようにする。

**対象読者**：`inventory_app`（部品在庫管理アプリ）のコードに触れる開発者。

**作成日**：2026-08-29

> **本ドキュメントは2拠点並行開発を前提としている。** セクション3「グループ別の実施内容」の「反映済み/未反映」判定は、**本ドキュメントを作成した時点でこのリポジトリを開いていた環境**でのみ確認したものであり、もう一方の拠点での実装状況は確認していない。実装状況の最終確認は、マージ時に両拠点で改めて突き合わせることを推奨する。

---

## 2. 発見された根本原因

### delete_flagとis_activeの不一致（項目1・2の根本原因）

`create_plan_version()`はCSVの`delete_flag`列の値を一切見ずに、常に`is_active=1`で新版を挿入していた。一方、一覧取得側の関数はフィルタ条件が食い違っていた：
- `list_active_plan_items()`（生産実績入力画面）：`is_active`のみ参照、`delete_flag`は見ない
- `list_plan_items_by_lot()`：`delete_flag`のみ参照、`is_active`は見ない

議論の結果、CSVインポート自体の`delete_flag`の扱いは変更しないことが決定した（数日ごとに更新されるCSVは「載っている行を上書き」で問題なく、CSVに載っていない行を「削除された」と誤判定してはいけないため）。実際に直すべきは以下の2点と判明：

1. バッチ削除ボタンが実データ（`kitting_plan_items`）に反映されていなかった（項目2）
2. 完了済み計画（実績入力済み）を後から見返す手段が一覧UIに無かった（項目8）

### バッチ削除が実データにカスケードしていなかった（項目2）

`mark_batch_deleted()`は`kitting_plan_batches.delete_flag`のみを更新し、`kitting_plan_items`には一切触れていなかった。生産実績入力画面の一覧（`list_active_plan_items()`）は`kitting_plan_batches`をJOINすらしておらず、削除操作の影響が全く反映されない状態だった。

修正：`mark_batch_deleted()`内で、同一トランザクションで対象バッチに属する`kitting_plan_items`行を`is_active=0`に更新するよう修正。復元（`deleted=False`）時は、他に同一`(kitting_list_no, lot_no)`のアクティブ行が既に存在する場合は復元しない（`NOT EXISTS`条件）よう安全策を追加（重複アクティブ行の発生を防止）。

### 完了済み計画の除外は仕様通りだった（項目7）

`list_active_plan_items()`内の判定：
```python
order_qty = item.get("order_qty") or 0
actual_qty = cumulative_by_kitting_no[item["kitting_list_no"]]
if actual_qty >= order_qty:
    continue  # 完了扱いのため一覧から除外
```
実績（production_daily累計）が発注数に到達した時点で一覧から除外される、意図的な設計。ただし除外後に見返す手段が「キッティングリストNo.を直接検索する」ことに限定されており、一覧からの導線が無かった。

### 実績履歴のクリア（項目13）は設計として一貫していたが、UI操作の不整合が原因

`load_history(kitting_no)`は「現在検索中の計画の履歴のみを表示する」設計で一貫していたが、修正前はシングルクリック（検索欄に入力するだけで何もしない）とダブルクリック（計画を開き履歴も入れ替える）で挙動が異なり、これが「クリックしたら消えた」という体感の原因だった。グループBの修正（ワンクリックで計画を開く）により、動作の一貫性が確保され解消された。

---

## 3. グループ別の実施内容

> 「判定」列は、本ドキュメント作成環境（このリポジトリ・このブランチ）で`git status`および該当ファイルの該当箇所を軽く確認した結果。網羅的なgit調査（reflog・他ブランチ探索等）は行っていない。

`git status --short`で確認した現在の未コミット変更には、以下のグループが対象とする全ファイルが含まれている（`inventory_app/models/kitting_plan.py`, `ui/kitting_plan_import.py`, `ui/kitting_production_entry.py`, `ui/main_window.py`, `ui/daily_report_window.py`, `ui/monthly_report_window.py`等）。

### グループA：計画表示・削除・実績履歴（項目1, 2, 7, 8, 13, 14）

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| A-1 | `models/kitting_plan.py` | `mark_batch_deleted()`：バッチ削除時に`kitting_plan_items.is_active`も同一トランザクションで連動更新するよう修正。復元時は重複アクティブ行を作らない`NOT EXISTS`条件付き | **反映済み**（`UPDATE kitting_plan_batches SET delete_flag = ...`に続く`kitting_plan_items`側の`is_active`更新コードを確認） |
| A-2 | `ui/kitting_plan_import.py` | 削除確認ダイアログ・完了メッセージに「計画も無効化され、生産実績入力画面の一覧から消えます」の文言を追加 | **反映済み**（該当文言を確認） |
| A-3 | `ui/daily_report_window.py` / `ui/monthly_report_window.py` | Treeviewに`<Double-1>`バインドを追加。選択行から`kitting_list_no`/`lot_no`を`self.report_rows`から逆引きし、`ActualCorrectionWindow`（循環import回避のため関数内import）を開く導線を新設。`on_updated`コールバックには日報・月報の再取得処理（`refresh_report()`）を設定。日報・月報の一覧は`production_daily`（実績）が母体のため、完了済み計画（一覧除外済み）の実績も表示され続けることを確認済み | **反映済み**（`on_row_double_click`・関数内`from ui.kitting_production_entry import ActualCorrectionWindow`を確認） |

**項目14（実績履歴からのクリックで計画呼び出し）について**：調査の結果、この機能は本シリーズでは実装しなかった（調査5で「未設定」と判明したのみで、実装プロンプトの対象には含めなかった）。将来必要になった場合の実装候補として記録する。

### グループB：計画一覧の選択操作改善（項目5, 6, 9）

| # | 実施内容 | 判定 |
|---|---|---|
| B-1 | **ワンクリック選択で計画を開く（項目5）**：`on_select_plan_list()`を拡張し、検索欄への反映に加え`search_plan()`相当の処理（計画情報表示・履歴読み込み）まで実行するように変更 | **反映済み** |
| B-2 | **矢印キー連打時のデバウンス（項目6）**：`<<TreeviewSelect>>`はクリック・矢印キーいずれでも発火するため、同じ経路で実現。`PLAN_SELECT_DEBOUNCE_MS = 200`（ms）。`after_cancel()`で直前の予約をキャンセルしてから再予約する方式 | **反映済み**（`PLAN_SELECT_DEBOUNCE_MS = 200`を確認） |
| B-3 | **ダブルクリックでのテキスト選択（項目9）**：`on_plan_double_click()`（計画を開く処理）を`on_plan_cell_double_click()`に置き換え。クリック位置のセルを特定し、`bbox()`の座標に一時的な`tk.Entry`を重ねてセルテキストを全選択状態で表示。`<FocusOut>`/`<Escape>`/`<Return>`でオーバーレイを閉じる（`<Key>`全般での即時クローズは、Ctrl+Cによるコピー操作を妨げるため採用しなかった） | **反映済み**（`on_plan_cell_double_click`のバインド・定義を確認） |

### グループC：ロード画面の統一（項目3, 4）

**発見された原因（項目3）**：`ui/main_window.py`の`open_kitting_production_entry()`は、`LoadingWindow`を表示した直後に**先に破棄してから**、重い同期処理（`KittingProductionEntryWindow`生成、`load_plan_list()`のDBアクセス）を実行していた。ロード画面が実際に重い処理をしている間は既に破棄済みのため表示されず、「瞬間的にしか表示されない」現象の原因だった。

| # | 実施内容 | 判定 |
|---|---|---|
| C-1 | `open_kitting_production_entry()`：`ui/kitting_plan_import.py`で実績のある非同期パターン（`threading.Thread` + `daemon=True` + `queue.Queue` + `self.after(200, ...)`ポーリング）を移植。`LoadingWindow`表示→別スレッドでDBアクセス（`_fetch_plan_list_rows()`、新設の`@staticmethod`）→ポーリングで完了検知→UIスレッド上で`KittingProductionEntryWindow(..., preloaded_plan_rows=payload)`を生成、の順に変更 | **反映済み**（`_kitting_entry_loading`フラグ・スレッド起動コードを確認） |
| C-2 | `load_plan_list()`を`_fetch_plan_list_rows()`（DBアクセスのみ）と`_populate_plan_list_tree(rows)`（UI更新のみ）に分割。`__init__`に`preloaded_plan_rows`引数を追加し、渡された場合は再DBアクセスせず表示のみ行う（後方互換維持） | **反映済み** |
| C-3 | `ui/kitting_plan_import.py`（項目4）：右上の`ttk.Progressbar`を廃止し、`LoadingWindow`による別画面ロード表示に統一。既存の非同期処理構造（スレッド+キュー+ポーリング）自体は無変更 | **反映済み**（`self.progress`参照が同ファイルから消えていることを確認） |

動作確認：意図的に0.6〜0.8秒の遅延を注入し、`root.update()`の1回あたりの所要時間が最大179ms/50msに収まっていることを実測し、UIスレッドがブロックされていないことを確認済み。

### グループE：画面の多重表示防止（項目12）

**対象画面**：メインメニューから開く10画面（マスターデータ管理・キッティング計画CSV取込・生産実績入力・96部品在庫入力・理論在庫インポート・在庫差異レポート・マスタインポート・NG入力・部品属性インポート・作業者管理）。

`UnmatchedProductionWindow`は対象外（同一クラスで意図的に「未一致行一覧」「エラー行一覧」の2インスタンスを同時に開く既存パターンがあるため）。`DailyReportWindow`/`MonthlyReportWindow`もメインメニュー直接起動ではなくネストした呼び出しのため対象外。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| E-1 | `ui/main_window.py` | `self._open_windows: dict`で開いているウィンドウを管理。共通ヘルパー`_open_singleton_window(key, factory)`を新設：既存ウィンドウがあれば`lift()`/`focus_force()`で前面表示、無ければ新規生成し`protocol("WM_DELETE_WINDOW", ...)`で閉じた際に自動的にレジストリから削除。既存ウィンドウクラス自体は変更していない | **反映済み**（`_open_singleton_window`定義・各`open_*`メソッドからの呼び出しを確認） |
| E-2 | `ui/main_window.py` | `KittingProductionEntryWindow`（非同期のため専用ロジック）：既存ウィンドウがあれば新規スレッドを起こさず、前面表示+既存ウィンドウの`load_plan_list()`（同期版）でデータのみ最新化。連打ガード用に`self._kitting_entry_loading`フラグを追加 | **反映済み** |

### グループF：Enterキーでの実績登録（項目16）

`ui/kitting_production_entry.py`の`entry_daily_qty`に`<Return>`バインドを追加し、`register_result()`（登録ボタンと同じ処理）を呼ぶよう変更。既存の検索欄（`entry_kitting_no`）の`<Return>`→`search_plan()`という既存パターンに倣った。

**判定**：**反映済み**（`self.entry_daily_qty.bind("<Return>", lambda e: self.register_result())`を確認）

**副次的な発見**：この画面には実績数量の「0以下」を明示的に弾くバリデーションが元々存在しない（NG入力画面には存在するが、実績入力画面には無い）。今回のスコープ外のため未対応。今後の検討対象として記録する。

### グループG：メインメニュー再構成（項目17, 18）

**分類の考え方（項目17）**：`config.DB_PATH`は単一ファイルで、月次データも共通マスタも同じDBファイルに同居しており、技術的な「DB切り替え対象かどうか」では分類できないと判明。データの性質（月次で入れ替わる運用データか、月をまたいで使う参照・マスタデータか）で分類した。

- 月次データ：キッティング計画CSV取込・生産実績入力・96部品在庫入力・理論在庫インポート・在庫差異レポート・NG入力
- 共通マスタ：マスターデータ管理・マスタインポート・部品属性インポート・作業者管理

| # | 実施内容 | 判定 |
|---|---|---|
| G-1 | メインメニューを「月次データ」「共通マスタ」の見出し+`ttk.Separator`で上下2セクションに再配置 | **反映済み**（両見出しラベルを確認） |
| G-2 | 一番下に「ログアウト」ボタンを追加。`on_logout()`は確認ダイアログ→`current_worker`クリア→`MainWindow`を`destroy()`→`LoginWindow`を再起動、という`on_login()`と対称的な実装。子ウィンドウは`MainWindow`の`destroy()`に連動して自動的に閉じるため、個別クローズ処理は不要と判断 | **反映済み**（`on_logout`定義を確認） |

**既知の留意点（今回のスコープ外）**：`LoginWindow`/`MainWindow`は共に独立した`tk.Tk`ルートで、ログイン⇔ログアウトを繰り返すたびにPythonの呼び出しスタックが少しずつ深くなる特性がある（`on_login()`に元々あった特性で、今回新たに導入したものではない）。通常利用では問題にならないが、記録に残しておく。

### グループD：計画一覧の機能拡張（項目10, 11）

**実装開始予定日の列追加（項目10）**：`kitting_plan_items.plan_start_datetime`列（既存、CSVの12列目から既に取り込み済み、"2026/07/21 00:43:00"形式）を、計画一覧Treeviewの「ロットNo.」の直後に追加。ゼロ埋め済みの日時文字列のため、既存のソート機構（`sort_plan_list()`）にそのまま乗せられ、数値列扱いにする必要はなかった。

留意点：値が空文字の行は文字列比較で先頭に来る。将来ゼロ埋めでない日時形式が混入した場合、その行だけ辞書順が時系列と一致しなくなる可能性がある（実データでは未発生）。

**絞り込み機能（項目11）**：列によって方式を分けた：
- **チェックボックス式ポップアップ**（ロットNo./file_no/基板名）：distinct値が719/450/873件と多いため、単純なドロップダウンではなく検索欄付きのポップアップ（Toplevel、Canvas+Scrollbar+Checkbutton群）を実装。
- **テキスト入力式部分一致**（実装開始予定日・キッティングリストNo./発注数/実績累計/差分/ロット完成数/ロット未完成数）

| # | 実施内容 | 判定 |
|---|---|---|
| D-1 | `self._all_plan_rows`（全件データ）を新設して保持。従来はTreeview自体が唯一のデータ保持先だった（全件と表示中の分離が無かった） | **反映済み** |
| D-2 | フィルタ条件は`col_key -> callable(value)->bool`の述語形式で統一管理（`_plan_filter_predicates()`）。テキスト入力・チェックボックス選択のどちらも同じ形式に変換されるため、共存・AND条件適用が自然に実現できた | **反映済み** |
| D-3 | チェックボックスポップアップのdistinct値は、「自列のフィルタを除いた、現在の他の全フィルタ適用結果内でのdistinct値」を採用（エクセルのオートフィルタの一般的な挙動に合わせた、単純な全件distinctより正確な方式） | **反映済み**（`open_plan_checkbox_filter_popup`を確認。上記D1〜D3・列追加も含め計25箇所の関連参照を確認） |
| D-4 | フィルタ・ソートの両立：フィルタ適用は`self._all_plan_rows`から`_populate_plan_list_tree()`で作り直す一方向の変換。ソートは「今表示されている行」に対して働くため、フィルタ→ソートの順で自然に機能する。v1としては、フィルタ適用のたびにソート状態はリセットされる仕様（自動で前回のソートを再適用する機能は今回実装しなかった） | **反映済み** |
| D-5 | `load_plan_list()`（「更新」ボタン等）を呼ぶと、テキスト・チェックボックス両方のフィルタが自動的にリセットされる（v1仕様） | **反映済み** |

---

## 4. 未対応・将来の検討事項

- 項目14（実績履歴からのクリックで計画呼び出し）：未実装
- 実績入力画面（`ui/kitting_production_entry.py`）に、実績数量の「0以下」を弾くバリデーションが無い（NG入力画面には存在）
- ログイン⇔ログアウトを繰り返すたびに呼び出しスタックが深くなる特性（既存の設計、今回新規導入ではない）
- フィルタ適用後、ソート状態を自動的に再適用する機能は無い（v1として手動で列ヘッダーを押し直す仕様）
- `load_plan_list()`実行のたびにフィルタ・ソート状態がリセットされる（v1仕様）
