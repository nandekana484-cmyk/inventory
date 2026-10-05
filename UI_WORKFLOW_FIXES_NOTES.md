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

### グループA追記：キッティングNo未確定行の可視化・保留保存・後日紐付け（新機能）

**発見の経緯**：`services/kitting_import_service.py::import_kitting_plan_csv()`は、キッティングリストNo.が空欄（未確定）の行を、エラーにもならず・スキップ件数の画面表示も無いまま**サイレントに`continue`で破棄**していた。後日キッティングNoが付与されて同じ計画が再取込された場合も、それを「同じ計画の更新」として認識する手段が一切無かった。

**段階的な実装**：
- Step1（可視化）：戻り値に`empty_kitting_no_count`を追加し、UIの完了メッセージ・ステータスラベルに表示するようにした。
- Step2（保留保存+後日紐付け）：新テーブル`pending_kitting_plan_items`を追加。識別キーは**(lot_no, setup_file_no, production_side, order_qty)**の組み合わせ（ユーザー決定）。キッティングNo空欄行は破棄せずこのテーブルにupsert保存する。キッティングNo付き行を処理する際、同一識別キーの保留行が存在すれば「未確定期間からの確定」として扱い、保留行を削除した上で正式登録する（登録失敗時は保留行を残し、次回再確定できるようにしている）。現在行で空欄のフィールドは保留側の値で補完するマージ処理（`_merge_from_pending()`）も実装。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| A-4 | `models/kitting_plan.py` | 新テーブル`pending_kitting_plan_items`を追加。`upsert_pending_kitting_plan_item()`・`find_pending_kitting_plan_item()`・`delete_pending_kitting_plan_item()`を新設（`(lot_no, setup_file_no, production_side, order_qty)`のCOALESCE式によるUNIQUE INDEXも追加するが、DBレベルの安全網でありSELECT→UPDATE/INSERTを実際の保存経路とする） | **反映済み** |
| A-5 | `services/kitting_import_service.py` | `import_kitting_plan_csv()`を、空欄行のpending保存・確定時のpending照合＆マージ・削除に対応する形へ全面書き換え。戻り値をタプルからdictに変更（`{"batch_id", "inserted", "pending_saved_count", "confirmed_from_pending_count"}`。他の新しめのインポート関数と同じdict返却パターンに統一） | **反映済み** |
| A-6 | `ui/kitting_plan_import.py` | 完了メッセージ・ステータスラベルに保留保存件数・確定件数を追加表示 | **反映済み** |

**影響を受けたファイル**：`test_kitting_import.py`（手動デバッグ用スクリプト）も、戻り値形式の変更に合わせて更新が必要だった。

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
| G-3 | メインメニューの並び順・ラベル名を変更（呼び出し先クラス自体は変更なし）。共通マスタ：「1. 構成基板数マスター」「2. 基板丁数マスター」「3. 作業者管理」「4. マスターデータ管理」「5. マスターインポート」。月次データ：「1. 生産計画読込」「2. 生産実績入力」「3. NG・仕損展開」「4. 仕掛部品展開」「5. 在庫値入力」「6. 理論値入力」「7. 在庫値出力」 | **反映済み**（全12ボタンを`invoke()`し、対応するクラスが正しく開かれることを実機確認済み） |
| G-4 | 生産実績入力画面（`ui/kitting_production_entry.py`）のデフォルト縦サイズを`1150x850`→`1150x700`に縮小（画面はみ出し対策）。`info_frame`（reqheight 289px）・`entry_frame`（実績+NG統合、147px）は`fill=tk.X`で常に自然サイズを確保し、`hist_frame`のみ`expand=True`で縮小分を吸収する既存の優先順位設計により、履歴欄が265px→234px（約1行分）に縮小する形で対応 | **反映済み**（各frameのreqheight実測により、登録ボタンのクリッピングが無いことを確認） |
| G-5 | ログアウトボタンの横幅を、`fill=tk.X`を外し`width=35`指定に変更することで約半分（実測比率0.49）に縮小 | **反映済み**（実測220px。`width=20`では130px/比率0.29と狭すぎたため`width=35`に調整した） |

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

### グループH：実績CSV取込のステージング化（列名マッピング拡張・確認フロー化・非同期ロード画面）

**背景（列名マッピング）**：実運用のCSV列構成（払い出し日・機種基板名・ロットNo・数量・累計・発注数・基板構成数）が、既存の`COLUMN_MAP_PRODUCTION`の候補列名（「基板名」「qty」等）と一致せず、全行がサイレントにスキップされていた（以前のBOM/部品属性TSVと同種のパターン）。ただし実DBへの書き込みは発生しておらず実害は無かった。「累計」「発注数」「基板構成数」は業務確認の結果、いずれも登録には使わない参考情報と確定した（基板構成数は意味不明のまま「単なる参考情報」として確定。累計・発注数はDB側で別途管理される値のためCSVの値と照合・上書きしない）。「面」（production_side）はCSVに情報が無いが、業務ルール上「製品が生産された＝面1も面2も完了している」ため、既存の面連動ロジック（1行の登録で両面に自動反映）がそのまま正しい設計と確認された。

**背景（ステージング化）**：以前の`import_production_csv()`は「選択→即時全件登録→結果表示」という確認ステップの無い一括処理だった。実績登録が「日付問わず常に1レコードに上書き」というルール（本ファイル§5関連、`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§5とも共通）に変更された後は、CSV再取込によって既存の実績（過去日付含む）が確認なしで一括上書きされるリスクが生じたため、ユーザー判断により自動登録を廃止し、以下のステージング方式に変更した。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| H-1 | `services/production_import_service.py` | `COLUMN_MAP_PRODUCTION`に"product_name"候補として"機種基板名"、"daily_qty"候補として"数量"、"report_date"候補として"払い出し日"を追加。9割以上スキップ時の注意喚起メッセージを追加（部品属性インポートと同様の仕組み） | **反映済み** |
| H-2 | `services/production_import_service.py` | `parse_production_csv_for_staging()`（新規）：DB書き込みを一切行わず、パースと候補解決（`find_matching_plan_items()`）結果のみ返す。既存の`import_production_csv()`（即時登録版）は後方互換のため無変更のまま残す | **反映済み** |
| H-3 | `ui/kitting_production_entry.py`, `ui/production_import_staging_window.py`（新規） | `on_production_csv_import()`を「パース→`ProductionImportStagingWindow`で一覧表示→行ダブルクリックで候補選択ダイアログ→`search_plan()`で計画確定→実績記入欄へ転記→既存の一直線Enterフロー（実績→NG面1→NG面2→登録確認ダイアログ→登録）にそのまま進む」方式に変更。**候補が1件のみでも必ず候補選択ダイアログを経由し、自動確定はしない**。report_dateはCSVの「払い出し日」ではなく登録ボタンを押した日（今日）になる（`register_daily_result()`等のreport_date=None時のデフォルト動作をそのまま使うだけで実現）。登録完了後、その行はステージング一覧から削除される（`remove_callback`） | **反映済み** |
| H-4 | `ui/kitting_production_entry.py` | 面1/面2連動（`register_opposite_side_daily_result()`）は「ユーザーが登録を確定した時点」で呼ばれるよう、タイミングをステージング方式に合わせて移動 | **反映済み** |
| H-5 | `ui/production_import_staging_window.py` | 垂直スクロールバーを追加。ウィンドウを閉じる際、未登録の行が残っていれば確認ダイアログを表示（全て登録済み＝一覧が空の場合は確認なしでそのまま閉じる）。「候補なし」（status='no_candidates'）の行は別途保持し、「登録不可リストをCSV出力」ボタンでutf-8-sig CSVとして出力できる | **反映済み** |
| H-6 | `ui/plan_candidate_dialog.py` | 候補選択ダイアログ（`select_plan_candidate_by_lot()`、新規）に`plan_start_datetime`（実装開始予定日）列を追加 | **反映済み** |
| H-7 | `ui/kitting_production_entry.py` | CSV選択後のパース処理（`parse_production_csv_for_staging()`、DB・ファイルアクセスのみでTkinterに触れない）を、グループCと同じ`LoadingWindow`＋`threading.Thread(daemon=True)`＋`queue.Queue`＋`self.after(200, ...)`ポーリングのパターンで非同期化。パース完了後、UIスレッド上で`ProductionImportStagingWindow`を生成する | **反映済み** |

動作確認（H-7）：`filedialog.askopenfilename`と`parse_production_csv_for_staging()`をモックし、パース処理に1秒の遅延を注入した状態で検証。`on_production_csv_import()`の呼び出し自体は約179msで即座に返り、`LoadingWindow`が表示され取込ボタンが無効化されること、パース中（約1秒間）`root.update()`ループ内の`after`コールバックが継続して実行され続けること（UIスレッドがブロックされていないこと）、パース完了後にロード画面が破棄されボタンが再有効化された上で正しいデータで`ProductionImportStagingWindow`が生成されることを確認済み。

**その後の大規模な機能拡張（2026-09-16、10項目改善）**：本グループHで確立した基本フロー（ステージング方式）を土台に、速度改善・永続化・重複防止・不一致管理・左右ペイン統合・自動確定機能まで拡張した。詳細はグループAB参照。

### グループI：「基板別実績」「日次実績履歴」の面1省略表示

計画情報欄の「基板別実績」表示、および日次実績履歴（本日の全計画分ログ）について、面2が存在する場合は面1を表示しないよう変更した。判定ロジックは`list_active_plan_items()`の既存の「2回目計画があれば1回目除外」ロジックと同じ考え方（同一`(lot_no, setup_file_no)`で`production_side=="2"`かつ`is_active=1`の行があれば1回目を除外）に揃えた。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| I-1 | `ui/kitting_production_entry.py` | `search_plan()`：`plan["lot_file_actuals"]`から、面2が存在する`setup_file_no`については面1エントリを表示から除外 | **反映済み** |
| I-2 | `ui/kitting_production_entry.py` | `load_today_log()`：挿入前に全レコードの計画解決を1回済ませ、`(lot_no, setup_file_no)`単位で面2が存在する組み合わせを集めてから、面1の行をTreeviewへの挿入対象から除外。`self._today_all_rows`自体は変更せず全件保持 | **反映済み** |

**実装中に発見・回避した潜在バグ**：表示行をフィルタすると、Treeviewの表示位置インデックス（`tree.index(row_id)`）と元データ（`self._today_all_rows`）の対応がズレ、履歴行ダブルクリックで誤った計画が開かれるバグが生まれるところだった。挿入時にiid→元レコードを直接対応付ける`self._today_row_by_iid`を新設し、`on_history_row_double_click()`をこちらから逆引きする方式に変更することで回避した。

### グループI追記：日報・月報への面1省略ロジック適用、および実績不整合の検知・警告（重要な発見）

**発見の経緯**：グループIで「基板別実績」「日次実績履歴」には面1省略ロジックを実装済みだったが、**日報・月報画面（`ui/daily_report_window.py`・`ui/monthly_report_window.py`）にはこのロジックが一切適用されていなかった**ことが判明した。面1・面2両方に実績があれば、両方が別々の行として一覧に表示されていた。さらに、月報の「仕掛数量抽出」機能（`on_extract_wip()`）が、面1の`surplus_qty`（仕掛数量）をそのまま抽出してしまうことも判明した。

**面1が面2より多い状態の原因調査**：`ActualCorrectionWindow`（実績修正画面）が**面連動を一切行わない**ことが原因と判明した。`prod_log_id`（片面1件）だけを指定して`update_daily_result()`/`delete_daily_result()`を呼ぶ設計のため、片面だけを修正・削除すると面1・面2の実績が食い違う状態を作れる。CSV取込・手動登録の面連動（`register_opposite_side_daily_result()`）も、反対側への登録が失敗した場合は主行の登録がそのまま成功として扱われるため、失敗時に食い違いが残り得る。

**業務判断の確定**：「面1が面2より多い状態」は**本来あってはならない不整合**であり、**除外した上で別途警告する**方針が確定した（業務上正当な状態ではない。CANONICAL_DESIGN_DECISIONS.md D-8にも記録）。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| I-3 | `services/production_service.py` | `_build_report_rows()`（日報・月報共通関数）に面1省略ロジックを追加（グループIと同じ「1回目除外」の考え方）。除外の際、面1の実績が面2を上回っている場合は`inconsistency_warnings`リストに記録し、黙って消すのではなく事実を残す設計とした。戻り値を`(report_rows, inconsistency_warnings)`のタプルに変更 | **反映済み** |
| I-4 | `ui/monthly_report_window.py` | 集計後、不整合があれば警告ダイアログ（対象lot_no・面1/面2それぞれの数量・「実績修正画面での片面のみの修正が原因の可能性があります」という手がかり）を表示 | **反映済み** |
| I-5 | `ui/monthly_report_window.py` | `on_extract_wip()`：`report_rows`が既にI-3で面1省略済みのため、追加のロジック無しで自動的に面1が除外される（コード変更は無く、docstringで明記するのみ） | **反映済み** |

**副次的な発見（重要）**：戻り値の型変更（タプル化）に伴い、`services/inventory_diff_service.py::_collect_wip_totals()`（在庫差異レポート機能）の呼び出し箇所も連動して修正が必要になった。調査の結果、**この既存機能も同じ「面1二重計上」の問題を抱えていた**ことが判明し、今回の修正で連動して解消された（スコープ外として放置していたら気づかれないまま残っていた可能性が高い）。

### グループJ：計画情報欄「余剰基板」の削除、不一致警告の比較対象変更

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| J-1 | `ui/kitting_production_entry.py` | 計画情報欄の「余剰基板」（`lot_surplus`）表示行（`lbl_lot_surplus`）を削除 | **反映済み** |
| J-2 | `services/production_service.py` | `calculate_lot_completion()`のsurplus計算・戻り値キー（`surplus`）を、他に参照箇所が無いことを確認した上で削除 | **反映済み** |
| J-3 | `ui/kitting_production_entry.py` | `_build_registration_preview()`の実績+NG数量の不一致警告の比較対象を、`order_qty`（発注数）から`planned_qty`（予定生産数）に変更。メッセージ文言も「計画数」→「予定生産数」に修正 | **反映済み** |

> **CANONICAL_DESIGN_DECISIONS.md §5の記載更新が必要**：同ファイルのチェックリスト項目1は「`ui/kitting_production_entry.py`の`lot_file_actuals`/`lot_surplus`表示部分が...」と`lot_surplus`表示の存在を前提にした文言のままだが、J-1により`lot_surplus`表示自体が削除済みのため、このチェック項目は実態と合わなくなっている（要更新、本ドキュメントの更新スコープ外のため付記のみ）。

### グループK：登録完了後の計画一覧の部分更新（大幅な性能改善）

登録完了のたびに計画一覧全体を再取得すると一直線フローの快適さを損なうため、「同一lot_no内の関連行のみ」を部分更新する方式を採用した。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| K-1 | `ui/kitting_production_entry.py` | `self._plan_row_iid_by_kitting_no`（新規、`(kitting_list_no, lot_no)`のタプルキー。478件のkitting_list_no重複問題（本ファイル§4関連、`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§4とも共通）を踏まえ単体キーは使わない）を`_populate_plan_list_tree()`実行時に構築し、Treeviewのiidと対応付け | **反映済み** |
| K-2 | `ui/kitting_production_entry.py` | `_refresh_plan_list_for_lot(lot_no)`（新規）：`list_active_plan_items(lot_no=lot_no, include_completed=True)`でDB側からlot_no絞り込み（LIKE部分一致のため取得後に完全一致で再フィルタ）して対象行のみ再取得し、`calculate_lot_completion(lot_no)`をそのlot_noについて1回だけ呼ぶ。Treeviewの該当行だけを`set()`で更新（削除・再挿入はしない＝ソート順・フィルタ状態・選択状態を保持） | **反映済み** |
| K-3 | `ui/kitting_production_entry.py` | `_perform_registration()`の最後（実績本体・面連動・NG申告の全DB書き込み完了後）で`_refresh_plan_list_for_lot(lot_no)`を呼ぶ | **反映済み** |

動作確認：実測で全件再取得（接続841回・1595件・約1.9秒）→部分更新（接続2回・約6ms）に改善。フィルタ（ロットNo.チェックボックス絞り込み）・ソート（列ソート）を適用した状態での登録でも、表示件数・行順ともに変化しないことを確認済み。面連動（面1にも自動登録される）で更新される行も、同一lot_noに属する限り`list_active_plan_items(lot_no=lot_no)`の結果に自動的に含まれるため、追加対応は不要と確認した。

### グループL：日報・月報の「累計数」列削除

`REPORT_HEADERS`（`ui/daily_report_window.py`）から「累計数」（`app_cumulative_qty`）列を削除。

**発見された潜在的な列ズレリスク**：`DailyReportWindow`/`MonthlyReportWindow`の各`__init__`には、`REPORT_HEADERS`のimportとは別に独自の`cols`/`widths`定義が存在しており、そちらも同時に修正しないと`dict(zip(cols, REPORT_HEADERS))`で列数不一致による表示ズレが発生するところだった。両方修正して解消済み。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| L-1 | `ui/daily_report_window.py` | `REPORT_HEADERS`・`_row_to_values()`・`ReportPreviewWindow.COL_WIDTHS`から「累計数」列を削除 | **反映済み** |
| L-2 | `ui/daily_report_window.py`, `ui/monthly_report_window.py` | 各ウィンドウが独自に持つ`cols`/`widths`のTreeview列定義からも`app_cumulative_qty`を削除（`REPORT_HEADERS`側の修正だけでは自動反映されない、独立した定義であることを確認した上での対応） | **反映済み** |

---

### グループO：親ウィンドウ最小化時のモーダルダイアログ不可視化バグ（重要）

**発見の経緯**：実際に稼働中のプロセスで、「実績CSV取込：登録待ち一覧」の行をダブルクリックしても反応が無く、アプリ全体がフリーズしたように見える現象が発生した。調査の結果、`Toplevel`を`transient(parent)`で生成する際、**親ウィンドウ（生産実績入力画面）が最小化（iconic）状態だと、Tkinter/Windowsの仕様上transientウィンドウが実際には表示されない（`state()=="withdrawn"`のまま）**ことが原因と判明した。ダイアログは`grab_set()`で入力を握ったまま`wait_window()`で待ち続けるため、画面には何も表示されないのにアプリ全体の入力が奪われた状態になる（OSレベルでは`Responding: True`であり、ハングではなく見えないダイアログが応答待ちしているだけ）。応急対処としてタスクバーから親ウィンドウ（最小化されているもの）を元に戻すと、隠れていたダイアログが表示され操作を再開できた。

**恒久修正**：`Toplevel`＋`transient(parent)`＋`grab_set()`パターンを使う箇所すべてに、ダイアログ生成前に`if parent.state() == "iconic": parent.deiconify()`を追加（親ウィンドウを自動的に復元してから表示する）。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| O-1 | `ui/plan_candidate_dialog.py::_show_candidate_list_dialog()`（共通実装。`select_plan_candidate()`・`select_plan_candidate_by_lot()`両方に影響） | iconicチェック＋`deiconify()`を追加 | **反映済み** |
| O-2 | `ui/kitting_production_entry.py::open_plan_checkbox_filter_popup()`（計画一覧のチェックボックス絞り込みポップアップ） | 同上 | **反映済み** |
| O-3 | `ui/kitting_production_entry.py::_show_registration_confirm_dialog()`（登録内容の確認ダイアログ、一直線Enterフローの中核） | 同上 | **反映済み** |
| O-4 | `ui/ng_input_window.py::_select_mounting_line()`（実装ライン選択ダイアログ） | 同上 | **反映済み** |
| O-5 | `ui/ng_input_window.py::open_ng_checkbox_filter_popup()`（NG一覧のチェックボックス絞り込みポップアップ） | 同上 | **反映済み** |

`ActualCorrectionWindow`（実績修正ウィンドウ）は`transient()`・`grab_set()`・`wait_window()`のいずれも使用しない非モーダルウィンドウのため、このバグの対象外であることを確認済み。

**教訓・今後の注意点**：新しくモーダルダイアログ（`Toplevel`＋`transient`＋`grab_set`）を追加する際は、この`iconic`チェック＋`deiconify()`パターンを標準的に含めることを推奨する。

---

### グループP：未完了計画（仕掛）の月次DB間引き継ぎ機能（新機能）

**背景**：月次DB切り替え（`config.DB_PATH`の切り替え、`on_switch_database()`/`on_create_database()`）は、新しい月のDBを完全にまっさらな状態（計画0件・実績0件）から作成する設計だった。未完了（仕掛が残っている）ロットの計画を、次の月のDBに引き継ぎたいという要望から新規実装した。

**確定した設計方針**：
- 引き継ぐのは「未完了の計画行そのもの」を新DBにコピーする方式（同じkitting_list_noで続きから作業できるようにする）。仕掛分だけを新しい番号で再作成する方式は不採用。
- 「未完了」の判定基準は**lot_remaining_quantity > 0**（lot_no単位）。月報の仕掛数量抽出で使われる`surplus_qty`（record単位、別の計算式）とは意味が異なるため区別する。
- 未完了と判定されたlot_noに属する**全kitting_list_no行をまとめてコピー**する（一部だけコピーすると新DB側で正しい完成数計算ができなくなるため）。
- **production_daily（実績）もコピーする**：`lot_remaining_quantity`の計算式自体が累計実績（completed）に依存するため、実績をコピーしないと引き継いだ計画の「未完了数」表示が新DBで意味をなさなくなるという技術的な理由による。
- **scrap_records・ng_declarations（NG履歴）はコピーしない**（ユーザー決定：過去のNG履歴を月をまたいで追跡する必要はない、新DBでは新規のNG申告として扱う）。
- **wip_board_snapshotもコピー不要**（あくまである時点のスナップショットのため）。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| P-1 | `services/production_service.py` | `list_incomplete_lots()`（新規）：全lot_noについて未完了かどうかを効率的に判定する。N+1を避けるため、`list_plan_items_for_all_lots()`（新規、全計画行を1回のSELECTで取得）＋`get_app_cumulative_qty_bulk()`（既存の一括取得）を組み合わせて実装。実測：500 lot_no（計1000計画行）で0.008秒 | **反映済み** |
| P-2 | `services/db_migration_carryover.py`（新規） | `carry_over_incomplete_lots(old_db_path, new_db_path)`：旧DB読み取り→新DB書き込みの2段階。主キー（`plan_batch_id`・`plan_item_id`）は`create_plan_batch()`・`create_plan_version()`を使って新DB側で再採番（旧DBの値はそのまま使わない）。`production_daily`の`plan_item_id`も新DB側の値に付け替える | **反映済み** |
| P-3 | `ui/main_window.py` | `on_create_database()`に「前月から未完了分を引き継ぐ」チェックボックスを追加。既存の非同期パターン（`LoadingWindow`＋スレッド＋キュー＋ポーリング）を適用 | **反映済み** |

**同時操作リスクへの対策**：`config.DB_PATH`はアプリ全体で共有されるグローバル状態のため、引き継ぎ処理中（旧DB読み取り→新DB書き込みの間）に、他の画面（生産実績入力画面等）で操作されるとデータ不整合の恐れがある。対策として：
- 引き継ぎ処理中はメインメニュー全体を操作不可にする（`_set_menu_enabled(False)`）。
- 引き継ぎ開始前、他の子ウィンドウが開いている場合は確認ダイアログを表示し、「いいえ」なら**新DBファイルの作成自体も含めて処理を中断する**（中途半端に新DBだけ作成される状態を避ける）。

### グループP追記：ロットNoの長期重複リスクへの対応（未完了計画DB間引き継ぎ機能の拡張）

**発見の経緯**：「ロットNoは長期的（数年単位）には重複する可能性がある」という業務上の事実が判明。これは、グループPの未完了計画DB間引き継ぎ機能にとって重大なリスクとなる：引き継ぎ後、新DB側で偶然同じlot_noを使う別の（無関係な）計画が通常のCSV取込で登録されると、lot_no単独で集約する`calculate_lot_completion()`等の関数が、**無関係な2つのロットをエラーにも警告にもならず静かに合算してしまう**。

**確定した対応方針**：毎回警告すると近い月同士の自然な再利用（直近であれば前月・当月でのロットNo重複は起こりうる）まで警告してしまい邪魔になるため、**`plan_start_datetime`（業務上の実装開始予定日、ユーザー決定で`created_at`ではなくこちらを採用）が1年以上離れている場合のみ**「重複疑いあり」として警告する設計にした。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| P-4 | `services/db_migration_carryover.py` | `_check_lot_no_duplicate()`（新規）を追加。新DB側に同一lot_noの既存行があれば、旧DB側の`plan_start_datetime`と全組み合わせで比較し、365日以上離れていれば`"suspected_duplicate"`。`plan_start_datetime`がパース不能（None・空欄・形式不正）な組み合わせは「重複でない」と断定せず`"undetermined"`として警告に含める（安全側の判断）。重複検知は引き継ぎ処理自体を止めない（注意喚起のみ） | **反映済み**（1.6年前の重複ケース→検知、45日前の差→検知なし、を実測確認済み） |
| P-5 | `services/db_migration_carryover.py` | `carry_over_incomplete_lots()`の書き込みフェーズで、各lot_noについて`create_plan_batch()`呼び出し前に`_check_lot_no_duplicate()`を実行し、結果を戻り値`summary["duplicate_lot_warnings"]`に集約 | **反映済み** |
| P-6 | `ui/main_window.py` | `on_create_database()`の成功メッセージに`duplicate_lot_warnings`の内容（最大10件＋「...ほかN件」）を表示 | **反映済み** |

**留意点**：現行のUIフロー（`on_create_database()`）は常に新規作成した空DBへ引き継ぐため、実運用でこのチェックが実際に発火する状況（新DBに既に同一lot_noがある状態）は現状は発生しない。将来的に既存の非空DBへ引き継ぐ運用が発生した場合に備えた機能。

---

### グループQ：共有フォルダ運用対応・DBロック機構・共有フォルダ選択UI（新機能）

#### 背景・確定した業務要件

- 基本的には1人の担当者が作業するが、ローカルPCの容量制約からDB本体をローカルに置きたくない。
- 1人しか作業できない状態（その担当者のPCが使えなくなると誰も作業できない）はリスクのため、複数PCのどれからでも（ただし同時には1人だけ）アクセスできるようにしたい。
- バックアップも欲しい（→「機能が確定して安定してから」実装する方針で保留。本ドキュメント§4参照）。
- 最終的に.exe化してPC各台にインストールする計画（→未着手。本ドキュメント§4参照）。

#### 技術調査で判明した前提

- SQLiteは元々「1プロセスがローカルファイルとして扱う」前提の軽量DB。共有フォルダ（SMB）上での複数プロセス同時書き込みは、ファイルロック機構が正しく機能しないリスクがあり、最悪の場合データベースファイルの破損につながる（SQLite公式ドキュメントが明示的に警告している既知の問題）。
- 既存の`get_connection()`は12ファイルに同じ実装が重複しており、毎回新規`sqlite3.connect()`。`journal_mode`は未設定（デフォルトDELETEモード）、タイムアウトも未設定（デフォルト5秒）、ロック競合時のリトライ処理は一切無い。
- `config.set_db_path()`自体はUNCパスを含む任意の文字列を受け付ける実装だが、UI側にはUNCパス・任意パスを指定する手段が元々無かった（ローカル`db/`フォルダ配下の相対フォルダ名のみ）。
- 本アプリは既に`config.BOM_FOLDER_PATH`でUNCパス上のTSVファイルを読み取る処理が動いているが、これは単純な読み取りでありSQLiteの書き込みロックとは別問題（「UNC経由でアクセスできる」ことの傍証にはなっても「SQLiteのロックが安定動作する」ことの証明にはならない）。

#### 確定した方針

「複数PCから同時アクセス」という一番危険なシナリオは業務上基本発生しない（1人しか使わない）ため、本格的なWALモード対応等の大改修ではなく、**軽い排他制御（ロックファイル方式）**で十分安全に運用できると判断した。

#### ロック機構の設計・実装

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| Q-1 | `services/db_lock_service.py`（新規） | `acquire_lock()`・`release_lock()`・`update_heartbeat()`・`get_lock_info()`。ロックファイルはDBファイルと同じ場所に`db_path + ".lock"`（JSON形式）として配置。中身：worker_name・pc_name（`socket.gethostname()`）・acquired_at・last_updated・内部管理用token（uuid4）。ハートビート方式：5分ごとにlast_updatedを更新、**30分間更新が無ければ自動解除**（ユーザー決定。「真のアイドル検知」ではなく「プロセス生存確認」に留める設計判断）。`release_lock()`/`update_heartbeat()`は自分が取得したロックのtoken一致を確認してからのみ操作する（他者のロックを誤って削除・更新しない） | **反映済み** |
| Q-2 | `ui/main_window.py` | 起動時に`config.DB_PATH`に対してロック取得を試み、失敗すれば使用者情報（`get_lock_info()`）を表示して起動を中断する。`self.protocol("WM_DELETE_WINDOW", self._on_app_close)`で終了時にロック解放（**重要な発見**：メインウィンドウには元々このフックが無く、今回新規追加が必須だった）。`self.after(300000, self._heartbeat)`で5分間隔のハートビート。DB切替時（`on_switch_database()`・`on_create_database()`）にもロックの解放・再取得を組み込んだ | **反映済み** |

**実装中に発見・修正した副次的な問題**：`on_logout()`が`WM_DELETE_WINDOW`を経由せず直接`self.destroy()`を呼んでおり、ロック解放漏れになる箇所があった。放置するとログアウトのたびにロックが残留する不具合になるため、`_release_current_lock()`呼び出しを追加して対応した。

**既知の制限**：ロック取得は「読み取り→存在しなければ書き込み」という単純な実装で、読み取りと書き込みの間に理論上わずかな競合窓がある（真の意味でのアトミックな排他ではない）。仕様通りの簡易実装として許容している。

#### 共有フォルダ選択UI

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| Q-3 | `ui/main_window.py` | 「データベース選択」領域を2段構成に再編：1段目（既存）はローカル`db/`フォルダのプルダウン、2段目（新規）に「共有フォルダのDBを開く」（`filedialog.askopenfilename()`で既存`.db`を選択）「共有フォルダに新規作成」（`filedialog.askdirectory()`＋`init_database_at()`）ボタンを追加。共通ヘルパー`_try_switch_db_path()`（ロック取得→成功なら現ロック解放+`set_db_path()`、失敗ならエラーダイアログで使用者情報表示）を新設し、既存の`on_switch_database()`もこのヘルパーにリファクタリング | **反映済み** |

**重要な設計判断**：`on_create_database()`（ローカル新規作成・前月引き継ぎ対応）は今回の共通ヘルパー（Q-3）に統合しなかった。理由：`carry_over_incomplete_lots()`（グループP参照）は「呼び出し時点の`config.DB_PATH`が旧DBであること」を前提とする契約を持っており、`_try_switch_db_path()`が即座に`set_db_path()`してしまうとこの前提を壊すため。

---

### グループQ追記：ロック機構の同一作業者・同一PCからの即時再取得（使い勝手改善）

**発見の経緯**：実運用で「作業者Aが正常終了せずに閉じた直後、同じ作業者Aが同じPCから再ログインしようとしても、30分待たされる」という使いにくさが報告された。調査の結果、これは**意図された仕様通りの動作**だったことが判明した：ロック機構（グループQ）は「誰がロックを持っているか」を記録するだけで、「本人が戻ってきたかどうか」を判定する仕組みが元々無く、ハートビート停止から30分経過するまでは誰であっても一律に拒否される設計だった。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| Q-4 | `services/db_lock_service.py` | `acquire_lock()`に、既存ロックのworker_name・pc_nameが今回の呼び出しと**完全一致する場合のみ**、経過時間の判定を待たずに即座に再取得できる分岐を追加。一致しない場合（別作業者・別PC）は従来通り30分ルールのまま。壊れたロックファイル（`LockFileCorruptedError`）は、同一作業者・同一PCであっても中身を検証できないため対象外（`force=True`による明示的な強制取得のみ） | **反映済み** |

**検証結果**：同一PC・別作業者、別PC・同一作業者、それぞれ単独での不一致ケースも個別に検証し、worker_name・pc_name両方が一致する場合のみ即時再取得が働くことを確認済み。

---

### グループA追記2：キッティング計画CSV取込の高速化（冗長なDDL実行の削減）

**発見された原因**：`models/kitting_plan.py`の`find_pending_kitting_plan_item()`・`upsert_pending_kitting_plan_item()`・`delete_pending_kitting_plan_item()`（グループA追記でキッティングNo.未確定行の保留保存用に新設した3関数）の全てが、関数の先頭で`init_kitting_plan_tables()`（本来アプリ起動時に1回だけで十分なDDL処理、6つのDDL文）を無条件に毎回実行していた。実測では1行あたり新規接続3.00回・SQL文12.00回のうち、半分以上がこの冗長な再実行に費やされていた。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| A-7 | `models/kitting_plan.py` | 上記3関数からの`init_kitting_plan_tables()`呼び出しを削除。テーブル存在保証は呼び出し元（`import_kitting_plan_csv()`が既に呼んでいる`create_plan_batch()`）に一本化。他の呼び出し元がこの3関数を単独で呼んでいないか全数確認し、リスクが無いことを確認した上で実施（呼び出し元は`services/kitting_import_service.py`のみ） | **反映済み** |

**効果（実測）**：

| ケース | 修正前 | 修正後 | 改善率 |
|---|---|---|---|
| 500行 | 3.381秒（6.76ms/行） | 2.632秒（5.26ms/行） | 約22%高速化 |
| 2000行 | 14.989秒（7.49ms/行） | 10.453秒（5.23ms/行） | 約30%高速化 |

DBアクセス削減率（新規接続33%減、SQL文50%減、commit50%減）ほど処理時間が改善していない理由は、残る`create_plan_version()`内の`PRAGMA table_info`や接続オープンコスト自体が支配的なため。さらなる高速化（接続の使い回し等）の余地は残るが今回はスコープ外。

**副次的な発見（リファクタリング候補として記録）**：`models/kitting_plan.py::upsert_plan_item()`（`create_plan_version()`のレガシー互換ラッパー）が定義のみで呼び出し元が存在しない未使用コードであることを確認した（今回は削除せず、§4の未対応・将来の検討事項に記録するのみ）。

---

### グループR：ロード画面追加の完遂・BOMServiceインスタンス共有化（未完了タスクの解消）

**背景**：`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§6で記録されていた「ロード画面（`LoadingWindow`＋非同期パターン）未対応の画面」（NG入力画面・仕掛展開画面、各種CSVインポート5画面）への対応を完了した。

#### NG入力画面・仕掛展開画面
- **BOMServiceインスタンスの共有化**：両画面が独立してモジュールレベルで`BOMService()`を生成していたため、共有フォルダのインデックス構築（`BOMFileIndex.build_index()`）が最大2回発生していた問題を解消。`services/bom_service.py::get_shared_bom_service()`を新設し、アプリ全体で単一インスタンスを共有する形に変更。実測で「1画面目で1回、2画面目では再度呼ばれない」ことを確認。
- 両画面の展開処理（`expand_scrap_to_parts()`/`expand_wip_to_parts()`）を、候補選択ダイアログ・入力検証まではUIスレッド同期のまま、実際のBOM展開部分のみ既存の非同期パターン（LoadingWindow+スレッド+キュー+ポーリング）でラップ。
- 非同期化前後で計算結果（K行丁取り数計算・実装ラインフィルタを含む）が完全一致することを確認。

#### 各種CSVインポート5画面
部品属性・構成基板数・マスタ・96部品在庫・理論在庫の5画面全てに、既存の非同期パターンを追加。`ui/master_import_window.py::_BaseImportTab.on_import_execute()`は共通実装のため1箇所を直すだけで反映されることを確認（将来タブが追加されても`run_import()`さえ実装すれば自動的に同じ非同期パターンの恩恵を受ける設計）。各画面固有の機能（構成基板数の位置ベースフォールバック・5割警告、部品属性の9割スキップ警告）が非同期化後も正しく機能することを、モック無しの実ファイルで確認済み。

#### 在庫差異レポート
`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§9 Step3でBOM展開自体を廃止したため、そもそもロード画面が不要になる形で既に解消済み（本グループの対応対象外）。

**これで、当初の9項目のロード画面調査（`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§6）で発見された箇所は全て対応完了となった。**

---

### グループS：システム全体の堅牢性・セキュリティ調査（実施結果サマリ）

**背景**：共有フォルダ運用開始（グループQ）を踏まえ、①データ処理の確実性、②データの保持性、③最低限のセキュリティ、④全般的な堅牢性、の4観点で調査を実施した。

**調査結果まとめ**

| 観点 | 結果 |
|---|---|
| carry_over_incomplete_lots()のアトミック性 | **重大な問題あり**（複数DBファイルをまたぐため単一トランザクションを持たない。途中失敗時に一部lotだけ引き継がれた中途半端な状態になり得るのに、ユーザーに詳細が伝わらない。→グループTで対応） |
| 個別のdelete-then-insert関数 | 単一トランザクション内で完結しており安全（問題なし） |
| with get_connection()の自動ロールバック | 正しく機能する（ただし接続のclose()は明示的に行われずGCに委ねている） |
| バックアップ＋journal_mode＋接続使い捨てパターンの組み合わせ | SQLite公式が非推奨とするリスクをそのまま抱えている状態だった（→グループUでタイムアウト延長・共通化のみ対応） |
| DBファイルのOSレベルアクセス権限 | アプリ側からの制御なし（共有フォルダのACLに委ねる、対応不要と判断） |
| .lockファイルの耐性 | 平文・無制限アクセス、非原子的書き込み、フェイルオープン設計で誤削除・破損に弱い（→グループVで対応） |
| SQLインジェクション | **リスク無し**（パラメータ化クエリが一貫して使われている、PRAGMA/DDLのf-string化は固定値のみで安全） |
| ログイン機構 | パスワード認証なし、作業者名選択のみ（なりすまし防止機能ではない。対応不要と判断、§4参照） |
| CSVインポートのパス検証 | filedialogのみ経由でリスク低い（対応不要） |
| ネットワーク瞬断へのリトライ | 無し（対応不要と判断） |
| トップレベル例外ハンドラ | 無し（個別画面のtry/exceptとTkinterのデフォルト保護に依存。§4に将来の.exe化時の検討事項として記録） |

この調査結果を受け、carry_over_incomplete_lots()の進捗可視化（グループT）・DB接続の共通化とタイムアウト延長（グループU）・ロックファイルの原子的書き込みとフェイルクローズ化（グループV）の3点を実施した。ログイン機構の性質・トップレベル例外ハンドラの不在は、今回は対応不要と判断し記録のみ残す（§4参照）。

### グループT：未完了計画引き継ぎ機能の進捗可視化・再開可能性

**対応方針の決定**：「ロット単位のアトミック性」「進捗の可視化・再開可能性」の2案を提示し、**実装コストの低い「進捗の可視化のみ」を採用**（ユーザー決定）。真の複数DBファイルをまたぐアトミック性はSQLite標準機能では実現困難なため、この判断は妥当と判断した。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| T-1 | `services/db_migration_carryover.py` | `_lot_already_migrated()`（新規）：新DB側にそのlot_noの計画・実績が「旧DBの対象行すべて」揃って存在するかを確認。1件でも欠けていれば「未完了」とみなす安全側の判定（前回途中で一部だけ書き込まれた不完全な状態を「完了済み」と誤認してスキップする事故を防ぐ） | **反映済み** |
| T-2 | `services/db_migration_carryover.py` | `carry_over_incomplete_lots()`のメインループを1lotごとにtry/exceptで保護。例外発生時は`failed_lot_nos`に記録し、以降の未着手lotも「前段の失敗により未処理」として記録した上でループを打ち切る（ロールバックはしない、成功済み分はそのまま残す）。戻り値に`skipped_lot_nos`（既に引き継ぎ済みでスキップした分）・`failed_lot_nos`（失敗した分、エラー内容付き）を追加 | **反映済み** |
| T-3 | `ui/main_window.py` | `_poll_create_db_queue()`の完了メッセージに、成功・スキップ・失敗の件数と、失敗があれば「もう一度実行すれば失敗分だけ再試行される」旨を案内するよう変更 | **反映済み** |

**検証結果**：5lot構成で、3lot目（LOT003）で意図的に例外発生 → LOT001・002成功、LOT003は実エラー、LOT004・005は前段失敗により未処理、として正しく切り分けられることを確認。再実行すると成功済み2件はスキップされ、残り3件のみ処理されることも確認済み。

### グループU：DB接続の共通化・タイムアウト延長

**対応方針の決定**：journal_mode変更（WALモード）・タイムアウト延長・get_connection()共通化の3案のうち、**WALモードは今回見送り**（SMB共有フォルダ環境ではWALの補助ファイル（-wal・-shm）へのロックが正しく機能しないと、通常のジャーナルモードより状況が悪化する可能性があるため。判断理由は`CANONICAL_DESIGN_DECISIONS.md` D-10参照）。**タイムアウト延長＋get_connection()共通化のみ実施**（ユーザー決定）。WALモードの是非は、実運用で問題が出た場合に改めて検討する方針。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| U-1 | `models/db_common.py`（新規） | 共通の`get_connection()`を実装。`timeout=30.0`（現状のデフォルト5秒から延長、共有フォルダでのロック競合を考慮した値） | **反映済み** |
| U-2 | production.py, kitting_plan.py, master.py, inventory.py, workers.py, bom_master.py, board_structure_master.py, parts_attributes.py, theoretical_inventory.py, ng_declarations.py, scrap_records.py, wip_scrap_records.py, wip_board_snapshot.py, ng_exclusion_list.py, wip_exclusion_list.py（**15ファイル**、当初想定の12ファイルより多かった） | 各ファイルの独自`get_connection()`実装を削除し、`models/db_common.py`からのimportに置換。関数内でのみ使われていた`import sqlite3`・`import config`も併せて削除（未使用import化を防止） | **反映済み** |

**検証結果**：別スレッドで3秒間書き込みロックを保持させた状態で、`timeout=1.0`の接続は約1.2秒で`database is locked`エラー、共通実装（30秒）は約3.1秒待って正常に書き込み成功することを実測確認。

### グループV：ロックファイルの原子的書き込み・フェイルクローズ化

**対応方針の決定**：壊れたロックファイルの扱いについて、**フェイルオープン**（壊れていたら無条件に次の人が取得可能）と**フェイルクローズ**（壊れていたら警告して手動確認を促す）の2つの設計思想を提示し、**共有DBの整合性を扱うシステムという性質上、フェイルクローズ寄りを採用**（判断理由は`CANONICAL_DESIGN_DECISIONS.md` D-11参照）。理由：無条件のフェイルオープンは「実は誰かが同時に書き込み中なのに、壊れたロックのせいで気づかず上書きしてしまう」という最悪のケースを見逃す可能性があるため。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| V-1 | `services/db_lock_service.py` | `_write_lock_atomic()`（新規）：`<ロックファイル>.tmp`へ書き込み・flush()・fsync()した上で`os.replace()`で本ファイルへ置き換える原子的更新方式に変更。`acquire_lock()`・`update_heartbeat()`に適用 | **反映済み** |
| V-2 | `services/db_lock_service.py` | 新規例外`LockFileCorruptedError`：ファイルが存在するのに読み取れない（JSON不正・OSError）場合に送出。ファイル自体が存在しない場合は従来通りNone（「ロック無し」、区別している）。`acquire_lock()`に`force`引数を追加。デフォルトFalseでは破損時に例外を送出、`force=True`は明示的なユーザー操作専用として無条件上書き取得 | **反映済み** |
| V-3 | `ui/main_window.py` | `_acquire_lock_with_corruption_handling()`（新規）で`LockFileCorruptedError`を捕捉した場合、「ロックファイルの状態が不正です。他の利用者が本当に使用中でないか確認してから続行してください」の警告ダイアログを表示し、ユーザーが明示的に選んだ場合のみ`force=True`で再取得（自動解除は一切行わない） | **反映済み** |

**検討したが見送った対応（誤削除への耐性）**：ロックファイルが手動削除された場合と「未使用で正当に存在しない」場合は、ファイルの有無だけでは原理的に区別できない。追記型履歴ログでヒューリスティックに検知する案を検討したが、この履歴ログ自体も同じ共有フォルダ上のファイルであり、書き込み競合・肥大化という別の問題を生むため、今回は見送り、現状維持（存在しない＝ロック無しとして取得可能）とした（判断理由は`CANONICAL_DESIGN_DECISIONS.md` D-12参照）。

**検証結果**：json.dump()を意図的に1回目だけ例外送出させるパッチでハートビート更新中の中断をシミュレートした結果、例外はそのまま伝播しつつ一時ファイルは片付けられ、本来のロックファイルの内容は書き込み前のまま（パース可能な状態）で無事に残ることを確認（原子的更新の効果が実証された）。

---

### グループW：.exe化に向けたパス解決・データ領域の分離（重要な事前対応）

**背景**：「.exe化は機能確定前でもできるか」「再インストール（verUP）時にDBを共有・保持できるか」という質問から調査した結果、**今のまま.exe化すると、再インストールのたびにDBが消える（またはそもそも見えなくなる）可能性が高い**ことが判明した。

**発見された原因**：
- `config.py`の`BASE_DIR`が`__file__`基準。PyInstallerの`onefile`形式では、実行時に一時展開フォルダ（`sys._MEIPASS`）を指してしまい、プロセス終了後に消える。
- アプリのデータ（DB・ログ・設定・OCR言語データ）が、アプリ本体と同じ`BASE_DIR`配下に同居しており、再インストール時にインストーラーがまとめて削除するリスクがある（Windows標準の作法は、この種のユーザーデータを`%LOCALAPPDATA%`等に置くこと）。
- 「現在選択中のDBパス」（共有フォルダ運用時）がどこにも永続化されておらず、次回起動時に必ずローカルのデフォルトDBにリセットされる（.exe化とは独立した既存の運用上の負担）。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| W-1 | `config.py` | `sys.frozen`判定（`IS_FROZEN`）を追加。.exe化時は`sys.executable`基準、開発環境は従来通り`__file__`基準に分岐 | **反映済み**（`IS_FROZEN = getattr(sys, "frozen", False)`を確認） |
| W-2 | `config.py` | `APP_DATA_DIR`を新設。開発環境では`APP_DATA_DIR = BASE_DIR`（従来と同一挙動）、.exe化時は`%LOCALAPPDATA%\InventoryApp`（`LOCALAPPDATA`取得失敗時はBASE_DIRへフォールバック）。`DB_PATH`・`LOG_DIR`・`EXPORT_DIR`・`IMPORT_DIR`・`TESSDATA_DIR`の基準をBASE_DIRからAPP_DATA_DIRに変更 | **反映済み** |
| W-3 | `ui/main_window.py` | `_load_db_folders()`・`on_switch_database()`・`on_create_database()`の3箇所が`config.BASE_DIR`を直接参照していたため、`config.APP_DATA_DIR`に統一（**実装時に気づいた重要な追加対応**。放置すると.exe化後に「ローカルDB切り替え機能が動かない」という新規バグになるところだった） | **反映済み**（3箇所とも`config.APP_DATA_DIR`参照を確認） |
| W-4 | `services/app_settings_service.py`（新規） | 選択中DBパスの永続化：`save_last_db_path()`/`load_last_db_path()`を実装。`config.APP_DATA_DIR/app_settings.json`に`{"last_db_path": "..."}`形式で保存。`config.set_db_path()`が呼ばれるたびに自動永続化（循環import回避のため`set_db_path()`内で遅延import）。`ui/main_window.py.__init__()`が起動時に前回パスへ自動復元、パスが実在しない場合はデフォルトへフォールバックし警告表示 | **反映済み**（`_restore_last_db_failed_path`による起動後の警告表示、`config.set_db_path()`内の遅延importを確認） |

**検証の要点**：
- `sys.frozen`モック＋`importlib.reload()`で実際にフリーズ環境をシミュレートし、`BASE_DIR`・`APP_DATA_DIR`・`DB_PATH`等5項目が正しく`%LOCALAPPDATA%`配下に計算されることを確認。
- `BASE_DIR`と`APP_DATA_DIR`を意図的に分離した状態で、`BASE_DIR`側に「おとり（デコイ）フォルダ」を置いて混入しないことを確認する**ネガティブチェック**を実施し、W-3の3箇所全てが`BASE_DIR`を一切参照しなくなったことを実証。
- 共有フォルダDBを開いた状態でアプリを終了→再起動すると、自動的に前回のDBに再接続されることを確認。

**留意点**：本グループは「.exe化の事前準備（パス解決・データ領域分離）」のみであり、実際のPyInstallerビルド（`.spec`ファイル作成、Tesseract OCR本体の同梱方法決定）はまだ着手していない（引き続き§4参照）。

---

### グループX：現在接続中のDBパス表示機能（新機能）

**背景**：共有フォルダ運用開始後、「今どのDBに接続しているか」を画面上で確認する手段が無かった。`config.py`に`get_current_db_label()`という、まさにこの用途のための関数が既に定義されていたが、**どこからも呼び出されていない未使用コードだった**ことが調査で判明した。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| X-1 | `ui/main_window.py` | ヘッダー行（共有フォルダの2ボタンの直下）に「接続中のデータベース：`<パス>`」の常時表示ラベルを追加 | **反映済み** |
| X-2 | `ui/main_window.py` | `_update_current_db_label()`（新規、共通メソッド）を、DB切り替えの全経路（`_try_switch_db_path()`経由の3操作、`on_create_database()`の引き継ぎ無し分岐、`_poll_create_db_queue()`の引き継ぎ有り分岐、起動時）から呼び出すよう組み込み、更新漏れが無いようにした | **反映済み** |
| X-3 | `ui/main_window.py` | `_truncate_path_for_display()`（新規）：`tkinter.font.Font.measure()`によるピクセル描画幅ベースの省略表示 | **反映済み** |

**設計変更のポイント**：当初は文字数ベースの省略を検討したが、UNCパスに含まれる日本語フォルダ名（全角、半角の約2倍幅）により、固定文字数では実際の描画幅がウィンドウ幅を超えるケースがあることを実測で発見し、ピクセル描画幅ベースの省略に設計変更した。

**検証結果**：起動時・各DB切り替え操作後にラベルが正しく最新パスへ更新されること、長い日本語混じりのUNCパスでも指定ピクセル幅以内に収まるよう省略表示されることを確認済み。

### グループY：月別DB一覧の共有フォルダ対応（新機能）

**背景**：ローカルの`db/`フォルダ配下限定だった既存のDB一覧表示（`_load_db_folders()`）を、共有フォルダ上の月別DBにも対応させたいという要望から着手した。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| Y-1 | `services/shared_db_scan_service.py`（新規） | `scan_shared_db_folders(root_dir)`：任意の親ディレクトリ直下をスキャンし、`inventory.db`が存在するサブフォルダのみを対象に情報（フォルダ名・パス・最終更新日時・サイズ）を返す。既存のローカル版`_load_db_folders()`と同じ設計方針を任意パスに対応できる形で切り出したもの。存在しないディレクトリでも例外にせず空リストを返す（共有フォルダへの一時的なアクセス不可に対する耐性） | **反映済み** |
| Y-2 | `ui/shared_db_list_window.py`（新規） | `SharedDbListWindow`：共有フォルダの親ディレクトリを指定・永続化し、配下の月別DBを一覧表示。一覧から選択してDB切り替え可能 | **反映済み** |
| Y-3 | `services/app_settings_service.py` | `save_shared_db_root()`/`load_shared_db_root()`を追加（既存の`save_last_db_path()`/`load_last_db_path()`と同じread-modify-write方式）。指定した親ディレクトリを永続化し、次回起動時に自動的に同じ場所を再スキャンする | **反映済み** |
| Y-4 | `ui/main_window.py` | `on_open_shared_database()`から`_switch_to_shared_db()`という共通処理を抽出し、ファイル選択ダイアログ経由・一覧画面経由のどちらでも同じ経路（`_try_switch_db_path()`）を通るよう統一（グループXのDBパスラベル更新も自動的に効く設計） | **反映済み** |

**検証結果**：疑似的な共有フォルダで、`inventory.db`が存在するフォルダのみ正しく抽出されること、一覧からの切り替え・親ディレクトリの永続化と再起動後の自動再スキャン・既存のローカルDB一覧機能への影響が無いことを確認済み。

### グループZ：月別DB削除機能（新機能）

**背景**：不要になった月別DBをアプリから削除する手段が無かった。削除は取り返しのつかない操作のため、既存のロック機構（`get_lock_info()`）を活用した安全対策を必須とした。

**安全対策の設計（優先順位）**：
1. 削除対象が現在自分が接続中のDB（`config.DB_PATH`）と一致する場合：確認ダイアログすら出さず拒否。
2. 有効なロック（30分以上更新が無い自動解除対象ではない、本当に有効なもの）が存在する場合：ロック保持者（`worker_name`・`pc_name`）を明記した強い警告ダイアログ。
3. それ以外：通常の削除確認ダイアログ。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| Z-1 | `services/db_delete_service.py`（新規） | `is_current_database()`・`check_delete_safety()`・`delete_database_files()`（DB本体＋関連ファイル`.lock`・`.lock.tmp`のみを削除、**フォルダ自体は削除しない**、判断に迷った場合の安全側の選択） | **反映済み** |
| Z-2 | `services/db_lock_service.py` | `get_active_lock_info()`を新設。**既存の`get_lock_info()`は無変更のまま維持**し（既存呼び出し元の挙動を変えないため）、`_is_stale()`（既存の30分自動解除判定ロジック）と組み合わせた「本当に有効なロックのみ」を返す新関数として追加した | **反映済み** |
| Z-3 | `ui/db_delete_helper.py`（新規） | `confirm_and_delete_database()`：ローカル・共有フォルダ両方の一覧画面から共通で使う削除フロー | **反映済み** |
| Z-4 | `ui/main_window.py`, `ui/shared_db_list_window.py` | ローカルDB一覧・共有フォルダDB一覧の両方に「削除」ボタンを追加。共有フォルダ側は削除後も一覧画面自体は閉じない（続けて他の行も削除できるようにするため） | **反映済み** |

**検証結果**：使用中でない通常のDBは削除できること、現在接続中のDBは拒否されること、他者使用中（有効なロックあり）のDBはロック保持者情報付きの強い警告が表示されること、削除後に一覧が正しく再取得されることを確認済み。

---

### グループAA：操作履歴機能（新機能）

**背景・確定した方針**：「いつ・誰が・どの操作をしたか」を簡単に確認できるようにしたいという要望から着手した。以下の3点をユーザー決定：
- 記録対象：**データに変更を加える重要な操作のみ**（閲覧・画面を開く等は対象外）。
- 記録内容：**大まかな操作名のみで十分**（変更前後の値等の詳細は不要）。
- 保存場所：**現在使用中のDB内**に履歴テーブルを持つ（月次DBごとに履歴も切り替わる）。

**事前調査で判明した重要な事実**：
- `audit_log`テーブルが既にスキーマだけ存在し、書き込みコードがどこにも無い（未接続のまま放置された監査ログ機能の跡）。
- DB接続は`models/db_common.py::get_connection()`に一元化されているが、**書き込み自体をラップする共通関数は存在しない**（各model関数が個別に`con.execute()`を書いている）。そのため、履歴記録を「1箇所に足すだけ」で実現することはできず、70箇所超の書き込み関数を網羅的に洗い出す必要があった。
- 対象操作（データ変更を伴うもの）は10カテゴリに整理された：①キッティング計画CSV取込、②生産実績登録・上書き、③NG申告・展開・登録、④仕掛の展開・確定登録、⑤対象外マーク、⑥個別修正・削除、⑦各種マスタインポート、⑧在庫入力、⑨未完了計画のDB間引き継ぎ、⑩月別DB削除。

**実装方針の決定**：`audit_log`（詳細スキーマ、table_name/record_pk/field_name/old_value/new_value等）を活用する精緻な方式ではなく、**シンプルな新設テーブルに、UI層のボタン押下タイミングで大まかな操作名を記録する**方式を採用（実装コストと要望の粒度のバランスを考慮）。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| AA-1 | `models/operation_log.py`（新規） | `operation_log`テーブル（id, timestamp, worker_name, pc_name, operation_name, detail, created_at）。`log_operation()`はpc_nameを`socket.gethostname()`（既存の`services/db_lock_service.py`と同じ取得方法）で自動取得。`list_operation_log(limit=None)`は新しい順（id降順）で返す | **反映済み** |
| AA-2 | `ui/operation_log_window.py`（新規） | **絞り込み機構は導入せず、列ヘッダークリックでの簡易ソートのみのシンプルな一覧表示**を採用（閲覧専用画面のため、業務画面のような絞り込みは不要と判断） | **反映済み** |
| AA-3 | `ui/main_window.py` | メインメニュー「月次データ」側に「9. 操作履歴」を追加（`operation_log`がDB切り替えのたびに切り替わる＝月をまたいで使い回すものではないため、共通マスタではなく月次データに分類） | **反映済み** |
| AA-4 | 全10カテゴリ・17ファイル | 各操作箇所への組み込み。**方針**：新たなtry/exceptを追加せず、既存の成功/失敗分岐にそのまま乗せる（既存のエラーハンドリングとの二重化を避けるため。DB書き込みが例外で失敗した場合は`log_operation()`に到達しないため、自然に「成功時のみ記録」になる） | **反映済み**（258行追加） |

**カテゴリ⑨（DB間引き継ぎ）の記録タイミング**：`config.DB_PATH`が新DBに切り替わった後のタイミングで記録し、新DB側に正しく記録されることを確認（旧DB側には記録されない）。

**カテゴリ⑩（月別DB削除）の記録先**：削除対象DB自体は削除により消えるため、そこには記録できない。既存の安全対策（`is_current`チェックにより削除対象は常に現在接続中のDBとは異なることが保証されている）を根拠に、**現在接続中のDB側に「何を削除したか」をdetailとして記録する**方針を採用（記録を諦めず実現）。

**検証結果**：全10カテゴリの操作を実際に行い、`operation_log`に正しい内容（操作名・作業者・detail）が記録されることを確認。カテゴリ⑨は新DB側にのみ記録され旧DB側には記録されないこと、カテゴリ⑩は削除対象ではなく現在接続中のDB側に記録されることを、それぞれ実際に検証済み。本機能の動作確認中に発生した検証手法上のインシデントについては、`CANONICAL_DESIGN_DECISIONS.md` §8参照。

---

### グループAB：実績CSV取込の機能拡張（10項目改善、①〜⑧、2026-09-16）

**背景（全体フロー）**：グループHで確立したステージング方式の基本フロー（CSV選択→パースのみ・即時登録しない→ステージング一覧表示→行ダブルクリックで候補選択→候補選択（払い出し日に近い実装開始予定日を上位表示）→計画へ転記→一直線Enterフロー→登録（`report_date`はCSVの払い出し日、`production_daily`は1計画1レコード常に上書き）→面連動→ステージング一覧から該当行削除）を土台に、10項目の改善要望が挙がった。計画特定キーは引き続き`(kitting_list_no, lot_no)`。

**実施順序（依存関係を考慮した決定）**：①速度調査→②ステージング永続化→③④重複登録除外→⑤不一致リスト分離→⑦日付差ハイライト→⑥左右ペイン統合UI→⑧自動確定機能、の順に実施した。**⑨（構成基板数マスタの表記ゆれ対応）は見送り決定**（`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §7参照。原因の大半がmasterへの未登録であり、文字列の近さによる自動照合はリスクの方が大きいと判断）。

#### AB-1（①）：CSV取込速度の劇的改善（N+1解消）

**原因**：`parse_production_csv_for_staging()`が、CSV行ごとに`list_active_plan_items()`（実DBの全アクティブ計画をフルスキャン）を都度呼んでいた。2000行で60.9秒、原因の97%以上がこのフルスキャンの繰り返しだった。

**対応**：`find_matching_plan_items()`・`resolve_plan_by_lot_and_name()`に`plan_items_by_lot`任意引数を追加（渡されれば内部の`list_active_plan_items()`呼び出しをスキップ）。`group_active_plan_items_by_lot()`（新規）で1回だけ全計画を取得しlot_no単位でグルーピングし、各行の照合に渡す。既存呼び出し元は引数省略で後方互換。

**効果（実測）**：100行：2.301秒→0.026秒（約88倍）、500行：11.462秒→0.029秒（約395倍）、2000行：60.882秒→0.051秒（約1194倍）。500行分の新旧ロジックの照合結果を全件突き合わせ、不一致0件を確認。

#### AB-2（②）：ステージングデータの永続化（閉じても続きから登録可能）

**確定した方針（ユーザー決定）**：
- 候補情報（candidates/matched）はキャッシュせず、表示のたびに現在のDB状態から再照合する（既存の他機能と同じ「キャッシュせず毎回計算」の方針に整合、かつAB-1の速度改善により再照合コストが実用上問題にならないため）。
- 登録済み・除外済み行は即座に物理削除する（履歴として残さない）。
- 同一lot_no+product_name等の行が異なるタイミングで取込まれた場合、新しい方が古い保留行を上書きする（delete-then-insert）。
- ウィンドウを閉じる際の確認ダイアログは廃止し、非ブロッキングの通知に格下げする。

**実装**：新テーブル`csv_import_batches`・`pending_csv_import_rows`（`models/production_import_staging.py`、新規）。メインメニューに「実績CSV取込状況を開く」を新設し、新規CSV取込を経由せず既存の未処理データだけで直接開けるようにした。

**既知の仕様（ユーザー確認済み）**：除外（登録不可）判定した実績が、修正せず再度同じ内容のCSVで読み込まれた場合、特別な履歴管理はせず単純に一覧に復活する（**経過観察**、ユーザー決定）。「登録済みの実績が再度CSVで読み込まれた場合の重複防止」はAB-3で対応。

**実運用での発見**：実DBに既に95件の`pending_csv_import_rows`が存在しており、実際の利用者（山田さん）が既にこの機能を実運用で使い始めていることが確認された。

#### AB-3（③④）：既に登録済みの実績の除外（重複取込防止）

**確定した判定基準（ユーザー決定）**：「既に登録済み」とは、**既にproduction_dailyに登録済みの実績値と今回のCSVの数量が一致**していること（重複取込防止が目的。CSVの数量とplanned_qtyの一致ではない）。

**実装**：`is_already_registered(lot_no, product_name, daily_qty, plan_items_by_lot=None)`（新規、`services/production_import_service.py`）。matchedが1件に定まる場合のみ判定し、`get_app_cumulative_qty()`（累計実績）とCSVのdaily_qtyを比較する。

**重大な落とし穴の発見と対処（AB-1と同種の教訓）**：当初`include_completed=False`（デフォルト）の計画一覧で判定していたが、**「既に登録済み」の計画はほぼ必ず「完了済み」でもあるため、デフォルトの完了済み除外フィルタによってcandidatesが0件になり判定不能に落ちる不具合**を発見した。`is_already_registered()`専用に`include_completed=True`を明示的に使う形に修正して解決。通常の候補選択・自動確定判定用の`include_completed=False`は変更なし（既存の使い分けを維持）。

除外タイミングはパース時点を採用（完了メッセージへの件数表示のため）。「登録済みだが数量が異なる（訂正が必要なケース）」は除外されず通常表示されることを確認済み。

#### AB-4（⑤）：不一致と判断した実績の分離・CSV出力フロー

**確定した判定主体（ユーザー決定）**：「一致しない」という最終判断は**人間が行う**（自動判定ロジックは追加しない）。UI上に「不一致として除外」操作を用意し、押されたものを別リスト・CSV出力する。

**実装**：右クリックメニュー「不一致として除外」（ボタンではなく採用）。理由の記録は`simpledialog.askstring()`による自由入力（既存の「登録不可」は機械判定による固定文言1種類で足りるが、「不一致」は人間の個別判断で理由が多様なため使い分け）。空欄・キャンセル時は固定文言にフォールバック。「登録不可」リストとは完全に独立して管理する。

#### AB-5（⑦）：候補一覧への日付差分ハイライト（3日以上）

`_is_large_date_diff(report_date, plan_start_datetime, threshold_days=3)`（新規、`ui/plan_candidate_dialog.py`）。既存の数量差ハイライト（`large_diff`、黄色系）とは別のタグ`large_date_diff`（オレンジ系）を新設。両方に該当する場合は第三の専用タグ`large_diff_both`（赤系）を単独で割り当てる（3タグ併用は不採用、Tkinter Treeviewの複数タグ背景色優先順位が不確実なため）。

`ui/production_import_staging_window.py`の左ペインは独自にTreeview行を構築しており`_show_candidate_list_dialog()`を経由しないため、自動反映されず個別に移植が必要だった（実装時の確認で判明）。

#### AB-6（⑥）：登録待ち一覧と候補選択画面の左右ペイン統合（重要な再検討あり）

**当初の結論とその後の覆り**：初回調査では「候補選択後の登録フローが`KittingProductionEntryWindow`側のメソッドであるため、統合には生産実績入力画面自体への組み込みが前提」という結論だったが、ユーザーの「今のポップアップウィンドウを左右2画面化するだけにしたい、できない理由を調査して」という再依頼を受けて再検証したところ、**`ProductionImportStagingWindow`が既に`parent`（生産実績入力画面のインスタンス）への参照を保持しており、`self.parent.search_plan(...)`を呼ぶだけで既存の登録フローにそのまま接続できる**ことが判明し、初回の結論は不要な前提だったと判明した。この経緯は「一度出した結論も、再検証する価値がある」という教訓として`CANONICAL_DESIGN_DECISIONS.md` D-16に記録した。

**レイアウト（既存規則との整合）**：既存のNG入力画面・仕掛展開画面と同じ配置規則（一覧＝右ペイン、操作・詳細＝左ペイン）に統一。右ペイン＝登録待ち一覧、左ペイン＝選択行の候補一覧（選択に応じてデバウンス200msで再表示）。

**実装**：左ペインの候補ダブルクリックで`self._parent.search_plan(...)`・`entry_daily_qty`転記・`_pending_csv_row_removal`/`_pending_csv_report_date`のセットを直接実行（モーダルダイアログ経由なし）。`ui/plan_candidate_dialog.py`のソート・ハイライトロジックはそのままimportして再利用（ロジックの複製なし、同ファイル自体は無変更）。既存のモーダル版`select_plan_candidate_by_lot()`はNG入力画面等で引き続き使われているため残置（正確には、NG入力画面自体が使うのは`select_plan_candidate()`という別関数だが、同じ`_show_candidate_list_dialog()`を共有しているため`plan_candidate_dialog.py`自体は変更していない）。

#### AB-7（⑧）：自動確定機能（右クリック即時登録＋緑ハイライト）

**確定した操作方式（複数回の要望修正を経て確定）**：
- 左ペイン候補一覧のシングルクリックは通常のTreeview選択動作のまま、特別な処理を挟まない。
- ダブルクリック：従来通り転記のみ（DB書き込みなし）、一直線Enterフローへ。
- 右クリック：確認ダイアログ無しで即座に`_perform_registration()`相当の処理を実行（面連動含む）。要件を満たす・満たさないに関わらず共通の挙動。
- 自動確定要件（ロットNo・製品名一致、生産予定数と実績数の完全一致、生産予定日と払い出し日が24時間以内）を満たす候補は、**あくまで補佐（目印）として緑ハイライトするのみ**（`auto_confirmable`タグ）。実際の登録操作（右クリック）の挙動自体は変えない。

**実装**：`is_auto_confirmable()`は当初services層への配置を指示されたが、日付パース関数（`_compute_date_diff_days()`）がui層（`plan_candidate_dialog.py`）にあり、services→ui依存になってしまうため、**唯一の呼び出し元である`ui/production_import_staging_window.py`自体に配置**した（このコードベースの一貫したui→services→models一方向依存の原則を守るための判断）。

即時登録の確認ダイアログ回避は、`_perform_registration()`自体には確認ダイアログが無く、`_start_registration()`が別途`_show_registration_confirm_dialog()`で確認していた構造を踏まえ、`_start_registration()`を経由せず`_build_registration_preview()`を直接呼んでpreviewを組み立て`_perform_registration()`へ渡すことで実現した（`_perform_registration()`自体は無変更）。

閾値の数学的分析：auto_confirmable（数量差=0・日付差≦1日）とlarge_diff/large_date_diff（数量差>20%・日付差≧3日）は閾値の範囲が重ならないため、両方に同時該当することは原理上起こり得ない。

#### AB-8（⑧付随）：report_date形式の重大な追加修正

実運用データのreport_dateが"YYYY/M/D"形式（月日ゼロ埋め無し）で、既存のパース処理が"%Y-%m-%d"のみに対応しておらずパース不能だったため、`is_auto_confirmable()`が実データに対して常にFalse（安全側）になっていた問題を追加で発見・修正した。

重複していた2つのパース関数（`plan_candidate_dialog.py::_parse_report_date()`、`kitting_production_entry.py::_resolve_csv_report_date()`）を`_parse_flexible_date()`という単一の共通関数（`plan_candidate_dialog.py`）に統合。`("%Y-%m-%d", "%Y/%m/%d")`の2形式を順に試すことで、ハイフン/スラッシュ×ゼロ埋めあり/なしの4パターン全てをカバーする（Pythonの`strptime`が`%m`/`%d`のゼロ埋め有無を問わず解釈できる性質を利用）。

**重要な追加判断**：単にパースできればよいだけでなく、DBの`report_date`列に表記ゆれ（例："2026/3/18" vs "2026-09-05"）が混在すると、`list_daily_production_range()`の文字列比較による日付範囲検索が誤動作する（例："2026-9-5"が文字列として"2026-10-01"より大きいと判定される）ため、`_resolve_csv_report_date()`側でDB書き込み前に必ず`strftime("%Y-%m-%d")`で統一形式へ正規化する処理も追加した（パース修正だけでは見過ごされていた可能性がある副作用への対応）。この「日付はDB保存前に必ず統一形式へ正規化する」という原則は`CANONICAL_DESIGN_DECISIONS.md` D-17に一般原則として記録した。

修正後、実データで95件全てが日付比較不能だった状態から解消され、130件が自動確定要件を満たすと判定されるようになったことを確認済み。

### グループAB追記：追加のUI改善4点（2026-09-18〜19）

グループAB完了後、実運用での使用感を踏まえて以下4点の改善要望が挙がった。

**1. 候補一覧の数量差セル表示**：Tkinter標準`ttk.Treeview`はセル単位の背景色指定に対応していない（`tag_configure()`は行全体にのみ効く）ため、色ではなく「計画数」セルのテキスト自体に差分を埋め込む方式を採用（`_format_planned_qty_cell()`、例："400（差+150）"）。既存の行全体ハイライト（`large_diff`等）とは独立した、20%閾値に関わらず差があれば常に表示するより細かい粒度の指標として併存させる。`ui/plan_candidate_dialog.py`に実装し、`ui/production_import_staging_window.py`はimportして再利用（ロジック複製なし）。

**2. 右ペインの複数選択・一括操作**：`selectmode="extended"`で複数選択に対応。`<Shift-S>`で選択中の全行のうち`is_auto_confirmable()`要件を満たすもののみ即時登録し、満たさないものはスキップする。既存の`_register_candidate_immediately()`が暗黙参照していた`self._current_staging_row`等を明示的な引数渡しにリファクタリングし、単一右クリック・一括処理の両方から安全に再利用できるようにした（一括処理中の状態競合を防止）。完了ダイアログは一括処理中のみ`messagebox.showinfo`を一時的に無効化し（`try/finally`で必ず復元）、最後に件数サマリを1回だけ表示する。右クリックメニューは、クリックした行が既存の複数選択に含まれる場合のみ一括不一致登録メニューを出し、それ以外（未選択行・選択範囲外のクリック）は単一行選択にリセットしてから単一メニューを出す（多くのアプリの慣習に合わせた判定）。

**3. 不一致リスト出力忘れ防止**：不一致リスト（`self._mismatched_rows`）は除外時点で対応する`pending_csv_import_rows`のDB行が既に物理削除され、CSV出力せずに閉じると理由（自由記述）を含め完全に失われるという性質を踏まえ、`_on_close()`に「不一致リストが未出力です（N件）。CSV出力しますか？」というブロッキングの確認ダイアログを追加した（件数が0件の場合は表示しない）。ファイル選択キャンセル時は閉じる操作自体を中止する（出力を促したのに何も出力されないまま閉じてしまう事故を防止）。登録不可リスト（`self._unregistrable_rows`）は対応するDB行が削除されず（CSV出力時に初めて削除される）再現可能なため、同様の確認は追加不要と判断した（データ喪失リスクの非対称性に基づく判断）。

**4. Undo機能**：要望として挙がったが、「元に戻す」の定義（レコード削除か、訂正前の値への復元か）の確認が必要となり、確認の間に他機能を優先する方針をユーザーが選択したため保留となった。**未実装**（§4参照）。

### グループAB追記2：払い出し日空欄+自動確定可能の矛盾表示問題の調査（原因不明のままクリーン化で解消、重要な教訓あり）

**発見の経緯**：実際の画面スクリーンショットで、ステージング一覧の「払い出し日（参考）」列が全行空欄になっているにもかかわらず、多くの行が「自動確定可能（要確認）」と表示されるという矛盾が報告された。`is_auto_confirmable()`は`date_diff is None`の場合を明示的にFalseとして扱うため、これは論理的に「現在のコードでは同時に起こり得ない組み合わせ」であることが確認された。

**徹底調査の経緯**：以下の経路を全て調査したが、いずれも既知のバグは見つからなかった。
- CSVパース直後の`report_date`取得（実物ファイル"4-1.csv"で検証、空欄0件）
- `is_auto_confirmable()`・`_compute_date_diff_days()`のNone処理ロジック（いずれも正しくNoneを「要件不成立」として扱っていることを確認）
- DB保存（`upsert_pending_csv_import_row()`）〜画面表示までの経路
- 実行中コードのバージョン一致確認（`git status`クリーン、修正が該当コミットに反映済みであることを確認）
- `is_already_registered()`判定経路・重複キーによるupsertの上書き・複数候補ケース

**重要な反省事項（教訓、必ず思い出せるように明記）**：調査の途中で、添付ファイルの中身が繰り返し空のまま届く事象があり、Claude側がその状況で「`row`変数の誤参照」という**実際のコードを確認せずに推測で作り上げた原因診断**を提示してしまった。この診断は実在しない変数名（`mapped_row`）を含んでおり、コード側の確認により「現状のコードに存在しないバグを修正すると、動いているコードに不要な変更を加え、かえって新しいバグを埋め込むリスクがある」という指摘とともに事実確認を求め返された。

> **教訓：調査結果が確認できない状況では、推測で具体的な原因を断定してはならない。「分からない」と伝え、確認を待つべきである。** `CANONICAL_DESIGN_DECISIONS.md` D-18に一般原則として記録した。

**結末**：原因はコード上では特定できなかったが、ユーザーがローカルDB（`inventory_app/db/inventory.db`）を一度クリーンな状態にリセット（テーブル構造は維持しデータのみ全削除、バックアップは取らずに実行）した後、症状は解消した。「テストでDBが点在的にあったこと、データ自体がテストデータであったこと」が背景として言及された。この調査の副産物として、`models/db_common.py::get_connection()`のDBパス即時参照性・複数DB独立性の検証を実施し、`CANONICAL_DESIGN_DECISIONS.md` D-19（複数ウィンドウ開放中のDB切り替えに関する未解決の設計課題）を発見した。

### グループAB追記3：実績CSV取込画面（ProductionImportStagingWindow）への追加改善（2026-09-23）

グループAB完了後、実運用での使用感を踏まえてさらに以下の改善を実施した。

**一覧UIの機能拡張（左右ペイン共通）**：右ペイン（登録待ち一覧）に、既存のNG一覧・仕掛一覧と同じパターンで縦スクロールバー・列ソート（`sort_staging_list()`、`daily_qty`は数値ソート）・件数表示（「表示中：N件　登録不可：N件　不一致として除外：N件　登録済み：N件」）を追加した。

**pack順序バグの2箇所目の発見・修正**：右ペインのスクロールバーが画面に表示されない不具合を調査したところ、以前のBOM基盤シリーズ・生産実績入力画面で確立済みの「`pack()`はボタン等の固定サイズ枠より先に`expand=True`のTreeviewをpackすると、後からpackした固定枠側の表示領域が奪われる」という既知のバグパターンと同一の原因（ボタン類より先にTreeview＋スクロールバーのフレームをpackしていた）と判明した。ボタン類を`side=tk.BOTTOM`で先にpackしてから、Treeview＋スクロールバーのフレームを`expand=True, fill=tk.BOTH`で最後にpackする順序に修正し解消した。**左ペイン（候補一覧）にも構造上全く同じ脆弱性があることを追加調査で発見**し、同じパターンで修正した。左ペインは現在のウィンドウ既定サイズ（1150x520）では偶然表示領域が足りており症状が表面化していなかっただけで、今回の修正は将来のレイアウト変更（列追加・ウィンドウ縮小等）で顕在化しうる潜在バグへの予防的対応という位置づけになる。

**メインメニューボタンの移設**：メインメニューの「実績CSV取込状況」ボタンを削除し、生産実績入力画面（`ui/kitting_production_entry.py`）の「実績CSV取込」ボタンの隣に移設した。ステージング画面（`ProductionImportStagingWindow`）は生産実績入力画面のインスタンス（`self.parent`）へ直接アクセスする設計上、そもそも生産実績入力画面が開いている状態でしか意味を持たない機能であり、その画面自体に配置する方が自然と判断した。処理の入口自体（`open_pending_csv_staging_window()`）は無変更で、呼び出し元をメインメニューから移しただけ。

**前回データ残存時の確認ダイアログ**：新しいCSVを取り込もうとした時点で`pending_csv_import_rows`に前回分の未処理行が残っていれば、「前回の取込データがN件残っています。続けて取り込みますか？」という確認ダイアログを表示するようにした。「はい」を選んだ場合の取込自体の挙動（同一lot_no+製品名の行は新しい方が古い保留行を上書きする既存ルール、グループAB AB-2参照）は変更していない。

**「候補なしとする」機能の追加**：登録待ち一覧の右クリックメニューに「候補なしとする」（単一・複数選択どちらにも対応）を追加した。既存の「不一致として除外」（AB-4、人間判断による理由自由記述）とは別に、既存の「登録不可」リスト（機械判定、固定文言）と同じ状態として扱う、人間判断による分類を新設した形になる。

**「登録済みスキップ」行の第三リスト化（既存の欠陥も同時に解消）**：これまで`is_already_registered()`（AB-3）でTrueと判定された行は、判定に使った詳細情報（一致した`kitting_list_no`・既存実績値）を一切保持せずそのまま握りつぶしていた。`is_already_registered()`の戻り値を判定結果＋詳細情報のdictに拡張し、`parse_production_csv_for_staging()`の戻り値に`already_registered_rows`として含めるよう変更。`ProductionImportStagingWindow`側に`self._already_registered_rows`（既存の「登録不可」「不一致」リストと同じ位置づけの第三のリスト）を新設し、「登録済みリストを表示」ボタン（別ウインドウ、lot_no・製品名・CSVの数量・払い出し日・一致した計画・既存実績値の一覧、CSV出力対応）から確認できるようにした。この一覧からは右クリック「通常の一覧に戻す（訂正する）」で、通常のステージング一覧（候補未確定状態）へ差し戻すことができる。この変更により、**「全件が登録済み判定のCSVを取り込むと、詳細情報が完全に失われたまま何も表示されない」という既存の欠陥も同時に解消された**（`imported_count == 0`でも`already_registered_rows`があればウインドウを開くよう、`_poll_csv_import_queue()`・`open_or_notify()`双方の判定条件も合わせて修正）。

動作確認：`python -m pytest tests/`への影響なし。各機能とも実データ・隔離DBコピーでの動作確認を実施済み（詳細は各実装時の報告参照）。

### グループAB追記4：複数バッチ分割ロットの業務シナリオ検証（調査のみ、修正不要と判明）

**検証シナリオ**：同一ロットNo・同一製品名で、後日追加のキッティングNo（バッチ）により計画が分割されるケース（例：キッティングNo.A 計画500・実績498〈NGにより2不足〉＋後日追加のキッティングNo.B 計画2・実績未登録）。この状態でキッティングNo.Bの実績が登録された場合に、既存のCSV自動取込・完成数計算・NG警告の各ロジックが正しく機能するかを実データ・テストデータで検証した。

**調査結果（いずれも修正不要）**：
- `is_already_registered()`（AB-3）の重複判定：`find_matching_plan_items()`はこのシナリオで2件の候補（キッティングNo.A・B）を返すが、`is_already_registered()`は「候補が1件に定まらない場合は常に『未登録』側に倒す」という既存の安全設計（`matched`が2件ならば早期return）により、誤ってキッティングNo.Aと取り違えたり誤判定したりするリスクは無いことを確認した。
- `calculate_lot_completion()`での合算：キッティングNo.A（498）・B（2）の両方を登録後、同一`(setup_file_no, production_side)`単位での合算により`completed_quantity`が正しく500（498+2）になることを確認した。
- NG登録の促進：手動登録の確認ダイアログ（`_build_registration_preview()`経由）では、キッティングNo.Aの498（予定500に対し2不足）に対して正しく不一致警告が表示されることを確認した。一括登録（Shift+S、AB追記2参照）は`is_auto_confirmable()`（数量完全一致が要件）でこのような不一致候補を自動的に除外するため対象にならない。

**副次的な発見（別途対応済み）**：この検証の過程で、`calculate_lot_completion()`内の`order_quantity`（および`remaining_quantity`）の代表値選択が、同一lot_no内で`order_qty`が食い違う場合に`plan_items`の取得順序（`list_plan_items_by_lot()`に`ORDER BY`が無い）に依存し非決定的になりうることを発見した。この点は既にコード側のdocstringで「月報側での警告表示を想定した既知の制約」として言及されていたが、実際に符号が反転しうる（0 vs -498）レベルの非決定性であることを具体的に確認した。**この件は別途ユーザーへの報告のみに留め、今回はコード修正の対象にしていない**（対応要否は今後の検討事項、必要であれば別途対応する）。

---

### グループAB追記5：保留マージロジックの識別条件不足による不正データ混入（重大、修正済み、2026-09-24）

**発見の経緯**：計画一覧に「キッティングNo.が日付形式」「ロットNo.が10や100といった単純な数値」「実装開始予定日が空欄」「ファイルNo.にロットNo.らしき値」という不自然なデータが混入していると報告され、`kitting_plan_items`に書き込む全経路（通常のCSV取込・保留マージ・DB間引き継ぎ・過去のテストスクリプト）を調査した。

**直接原因**：実データ調査の結果、混入した673件（`plan_batch_id=2, 3`、`4-2.csv`・`4-3.csv`）自体は、キッティングNo.が空欄でなかったため保留マージ経路を一切経由しておらず、**CSVファイル自体の列内容が本来のキッティング計画CSVの形式と一致していなかった**（列内容の検証が無いまま位置だけで機械的に取り込まれた）ことが直接の原因と判明した。この調査自体は保留マージロジックのバグではなかったが、その過程で**保留マージロジック自体にも独立した重大な欠陥**があることを発見した。

**発見した保留マージロジックの欠陥**：`_merge_from_pending()`（キッティングNo未確定行の保留・確定処理）が、識別キー（`lot_no`, `setup_file_no`, `production_side`, `order_qty`）の一致のみでマージ可否を判定しており、`board_name`（製品名）が一致するかを確認していなかった。実データで、**同一ロット・同一ファイルNo・同一面・同一発注数量だが、`board_name`が異なる別々の正規のキッティングバッチが実在する**ことを確認した（例：`lot_no='260066'`・`setup_file_no='0448'`・`production_side='1'`・`order_qty=3000.0`で、`plan_start_datetime`だけが異なる2つの独立した`is_active=1`バッチ）。このような場合、識別キーだけで一致と判定してマージすると、実際には別の計画の`board_name`・`plan_start_datetime`等が誤って混入する。

**確定した業務原則**：業務確認の結果、「**識別キーが一致してもboard_nameが違えば別の新規計画として扱う**」という原則が確定した。

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| AB-9 | `services/kitting_import_service.py` | `import_kitting_plan_csv()`内、保留行とのマージ判定に`normalize_board_name()`（表記ゆれ吸収、既存の`models/board_structure_master.py`を再利用）による正規化済み`board_name`一致チェックを追加。一致する場合のみ`_merge_from_pending()`を実行し、一致しない場合は保留行を削除せずそのまま残し、現在のCSV行は通常の新規計画としてそのまま`create_plan_version()`で登録する（`pending_match = None`にリセットすることで、既存の削除・カウント処理を自然にスキップする実装） | **反映済み**（実データ相当のシナリオ4パターン（一致してマージ・不一致でマージされない・表記ゆれ吸収・実データ衝突パターンの再現）を隔離DBで実機検証済み） |

**混入データの削除**：発見した673件（`plan_batch_id=2, 3`）について、`production_daily`・`scrap_records`・`ng_declarations`・`wip_board_snapshot`・`wip_scrap_records`等いずれからも参照されていないこと（実害無し）を確認した上で、事前に実DBのバックアップを取得し、`kitting_plan_items`からのみ削除した（`kitting_plan_batches`のバッチ記録自体は維持、削除しない方針とした）。削除後、`kitting_plan_items`は1114件→441件（正常データのみ）になり、計画一覧の実機確認でも不自然な行が0件になったことを確認した。

### グループAB追記6：実績CSV取込への誤フォーマットファイル混入問題（2回発生、恒久対策実施、2026-09-24）

**背景**：グループAB追記5の調査（計画一覧への不正データ混入）とは別に、実績CSV取込側（`pending_csv_import_rows`）でも「`report_date`（払い出し日）が全行空欄になる」という類似の症状が報告され、調査した。

**1回目（過去）**：グループAB追記2「払い出し日空欄+自動確定可能の矛盾表示問題」として既に記録済み。徹底調査したが真因は特定できず、ローカルDBの全消去（データのみ初期化）により症状が解消した経緯があり、原因不明のままだった。

**2回目（今回）**：実績CSV取込ボタンから、誤ってキッティング計画CSV（`Desktop/sample/計画/`フォルダの`4-1.csv`・`4-4.csv`）を選択して取り込んでいたことが直接原因と判明した。`COLUMN_MAP_PRODUCTION`の`report_date`候補列名（「払い出し日」等）が計画CSVのヘッダーと一致せず全行空欄になった一方、`lot_no`（候補「ロットNo」）・`product_name`（候補「機種基板名」）・`daily_qty`（候補「数量」、計画CSV側では実際には「計画数」の意味）の列名が**両フォーマットで偶然共通していた**ため、エラーにもならず「一見一部だけ正常に見える」中途半端な取込結果になっていた。

**関連性の確認（重要）**：この問題がグループAB追記5の保留マージロジック修正（`board_name`一致チェック）の副作用ではないか、また`_revert_already_registered_rows()`（登録済みリストから戻す機能）に実装ミスがあるのではないかという仮説を検証したが、いずれも**該当しないことをコード確認で確定した**：
- 保留マージロジック（`kitting_import_service.py`、計画データ用）と実績CSV取込（`production_import_service.py`、実績データ用）はコードパスが完全に分離しており、呼び出し関係が無い。`plan_start_datetime`（計画側）と`report_date`（実績側）は概念として完全に別物であり、混同するコードは存在しない。
- `_revert_already_registered_rows()`・`self._already_registered_rows`の構築（`services/production_import_service.py`）はいずれも`report_date`を正しく引き継いでおり、実装上のバグは無い。パース時点で既に`report_date`が`None`だった値を、そのまま忠実に伝播しているだけだった。

**恒久対策（案C：自フォーマット固有列の欠如検知＋他フォーマット固有列の混入検知、両方）**：

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| AB-10 | `services/csv_format_detection.py`（新規） | 実績CSV固有列（「払い出し日」「基板構成数」）・キッティング計画CSV固有列（「キッティングリストNo」「実装開始日時」「セットアップファイルNo.」）を定義。`read_csv_header()`（各取込サービスと同じエンコーディング自動判定でヘッダー1行のみ読む）・`detect_format_mismatch_warnings()`（自フォーマット固有列の欠如／他フォーマット固有列の混入を独立に検知）を実装 | **反映済み** |
| AB-11 | `ui/kitting_production_entry.py` | `on_production_csv_import()`のファイル選択直後に`_confirm_csv_format_for_production_import()`を追加。警告があれば確認ダイアログ（「はい」で続行・「いいえ」で中止、強制ブロックはしない） | **反映済み** |
| AB-12 | `ui/kitting_plan_import.py` | `on_start_import()`のファイル存在チェック直後に`_confirm_csv_format()`を追加（逆方向、実績CSVを計画CSV取込に読み込ませた場合の検知） | **反映済み** |

**動作確認**：4パターン（計画CSV→計画CSV取込、実績CSV→計画CSV取込、実績CSV→実績CSV取込、計画CSV→実績CSV取込）全てを、実物のサンプルCSVファイルを用いて隔離DB上で検証。正しい組み合わせでは警告が出ず、誤った組み合わせでは明確な警告文言（「このファイルは○○CSVのようです。△△取込ではなく○○取込をご利用ください」）が表示され、「はい」で続行・「いいえ」で中止できることを確認した。

**誤取込された457件の削除**：`import_batch_id=4, 5`に該当する`pending_csv_import_rows`457件が、`production_daily`への登録が一切発生していないこと（登録済みの行は必ず`pending_csv_import_rows`から削除される設計のため、削除直前まで存在していたこと自体が未登録の証明になる）を確認した上で、事前バックアップを取得し削除した。対応する`csv_import_batches`（`import_batch_id=4, 5`）も、どのコードからも参照されない書き込み専用メタデータであることを確認した上であわせて削除した（グループAB追記5の`kitting_plan_batches`とは異なり、こちらは削除する判断とした）。

### グループAB追記7：右クリック即時登録の使い勝手改善2点（数量差の修正導線・登録後の自動次行選択、2026-09-24）

AB-7（右クリック即時登録＋緑ハイライト）・AB追記の複数選択一括登録（Shift+S）の運用を踏まえ、以下2点を追加した。いずれも`ui/production_import_staging_window.py::_register_candidate_immediately()`（単一行の右クリック即時登録の実処理）・`_on_bulk_register()`（Shift+S一括登録）が対象。

**1. 即時登録後、数量差がある場合の修正導線**：`_register_candidate_immediately()`は、`_perform_registration()`が表示する完了メッセージ（`messagebox.showinfo`）をいったん捕捉し（一括登録時と同じ「一時的に無効化→`try/finally`で復元」の手法）、実績数（`daily_qty`）と計画数（`planned_qty`）が一致するかを判定する。

- 一致する場合：従来通りの完了メッセージのみ表示。
- 不一致の場合：完了メッセージに続けて「実績数（{daily_qty}）が計画数（{planned_qty}、差{diff:+g}）と異なります。数量を修正しますか？」という`messagebox.askyesno`を表示する。「はい」を選ぶと、該当計画を再度開き（`search_plan()`）、登録済みの実績数を実績記入欄へ転記した上でフォーカスを移す。ユーザーがそこから値を訂正し、既存の一直線Enterフロー（Enter→確認ダイアログ→登録）で再登録すれば、`overwrite_daily_result()`により上書きされる。

**2. 登録後の自動次行選択**：右クリック即時登録・Shift+S一括登録のいずれでも、登録により右ペイン（登録待ち一覧）から消えた行の次に、自動的に次の行を選択状態にする。

- 単一行の右クリック登録（`_register_candidate_immediately()`）：削除前に`self.tree.next(staging_iid)`で次のiidを記録しておき（削除後は消えたiidを起点に辿れないため）、登録成功後（`staging_iid`が`self._row_by_iid`から消えている＝登録成功の判定）にその行を選択・`see()`でスクロール表示する。前述の数量差修正ダイアログで「はい」を選んだ場合も、生産実績入力画面側の訂正作業とは独立に、ステージング一覧側のカーソルは次の行へ進めておく（並行して作業できるようにするため）。
- 複数選択一括登録（`_on_bulk_register()`）：複数行が同時に削除されるため、「対象行のうちTreeview上で最後だった行の次の行（対象行以外で最初に来る後続行）」を選択する方針とした（削除が進むと`tree.next()`等で辿れなくなるため、削除開始前に対象範囲の最後の位置を特定し記録しておく）。個々の`_register_candidate_immediately()`呼び出しによる次の行選択も内部的には行われるが、本メソッドの末尾で改めて`selection_set()`により上書きするため、最終的な選択状態はこの一括処理向けの方針に従う。

---

### グループAB追記8：report_dateが手動修正のたびに意図せず「今日」へ書き換わる問題の発見と対応（重要、2026-09-29）

**背景**：「日々の引落（前日比）を出したい」という新しい要望（`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§19参照）を受け、`production_daily.report_date`を使って「指定日時点の累計」を再構成できるか調査したところ、report_dateの信頼性に問題が見つかった。

**発見の経緯**：`overwrite_daily_result()`/`replace_daily_result()`（`models/production.py`）の全呼び出し元をリポジトリ全体から洗い出し、それぞれがreport_dateにどんな値を渡しているかを1件ずつ追跡した。CSV経由の登録（自動取込・ステージング画面の主経路・右クリック即時登録・Shift+S一括登録）はいずれもCSVの払い出し日を正しく使っていたが、以下3つの経路で、意図せずreport_dateが実行日（今日）に書き換わることが判明した：

1. **生産実績入力画面で既存のキッティングNo.を選び直し、通常の登録ボタンで再登録した場合**：`search_plan()`が`self._pending_csv_report_date`をNoneにクリアするため、`_perform_registration()`の`_resolve_csv_report_date(None)`がNoneを返し、`overwrite_daily_result()`側のデフォルト動作（実行日）にフォールバックしていた。
2. **画面からの手動登録で、面1/面2の連動登録（反対側の面）を行った場合**：`ui/kitting_production_entry.py::_register_opposite_side_daily_result()`が`report_date`引数を一切渡していなかった（今回新しく見つかった問題）。主たる面がCSV由来の正しい日付で登録されていても、反対側の面は常に今日になっていた。
3. **右クリック即時登録で数量不一致になり「修正しますか」から通常フローで再登録した場合**：`ui/production_import_staging_window.py::_register_candidate_immediately()`の数量差修正ダイアログで「はい」を選ぶと、再度の`search_plan()`により1と同じ経路に合流する。

**隔離コピー（config.DB_PATH・config.APP_DATA_DIR双方を隔離）での再現**：過去日付（2026-09-01）で登録済みの実績を、計画一覧から選び直して数量だけ変更し通常の登録ボタンで再登録したところ、report_dateが実行日（2026-09-29）へ書き換わることを実際に確認した。

**ユーザー決定の方針**：「実際に生産した日」と「修正した日」は別の情報であるべきなので、report_dateが明示的に渡されない場合は、既存行のreport_dateをそのまま保つ（今日には書き換えない）方針で統一した（`CANONICAL_DESIGN_DECISIONS.md` D-28参照）。

**対応（完了）**：

| # | 対象ファイル | 実施内容 | 判定 |
|---|---|---|---|
| AB8-1 | `models/production.py` | `replace_daily_result()`を修正。report_dateがNoneの場合、DELETEの直前・同一コネクション内で削除対象の既存行のreport_dateをSELECTして引き継ぐ。該当する既存行が無い場合のみ実行日（今日）にフォールバックする | **反映済み**（隔離コピーで動作確認済み） |
| AB8-2 | `services/production_service.py` | `overwrite_daily_result()`が従来ここで行っていた「Noneなら今日にする」処理を削除し、Noneのまま`replace_daily_result()`へ渡すよう変更（既存行引き継ぎの判断をmodels層に一本化） | **反映済み** |
| AB8-3 | `ui/kitting_production_entry.py` | `_register_opposite_side_daily_result()`に`report_date`引数を新設。`_perform_registration()`が主たる面に使ったreport_date（CSV由来の値、またはNone）を、反対側の面にも同じように渡すよう変更 | **反映済み** |

**動作確認（隔離コピー）**：①手動再登録での日付保持、②新規登録（既存行なし）は従来通り今日になる、③CSV由来の明示的な値は従来通り使われる（退行なし）、④面連動で反対側にも同じ日付が渡る、⑤右クリック即時登録の数量不一致修正フローでも日付が保持される、⑥`ActualCorrectionWindow`の「修正」ボタンへの影響なし（そもそもreport_dateに触れない設計のため）、の6シナリオすべてで想定通りの結果を確認した。`python -m pytest tests/`への影響なし。

---

### グループAB追記9：report_dateの空値（NULL・空文字列）に関する追加の堅牢化（完了、2026-09-29）

**発見の経緯**：グループAB追記8の修正を検証する過程で、「既存行はあるが、その既存行自体のreport_dateがNULLまたは空文字列だった場合、NULLがそのまま連鎖して新しい行に書き込まれてしまう」というロジックの隙間が見つかった。追記8の修正コードは「削除対象となる行が存在するかどうか」だけを見ており、「その行のreport_date列に実際に有効な値が入っているかどうか」を区別していなかったことが原因。

**現状のスキーマでの実害の有無**：`production_daily.report_date`列には元々`NOT NULL`制約が付いている（`db/schema.sql`、実DBの`PRAGMA table_info`でも`notnull: 1`を確認済み）ため、この状況（既存行のreport_date自体がNULL）は通常発生しない。実DBの19行を直接確認したが、`report_date`がNULL・空文字列の行は0件だった。ただし、これは「今のスキーマの制約に守られているから実害が出ていないだけ」であり、コード自体の脆弱性として修正した。

**重要な副次的発見**：もしreport_dateがNULLのまま登録される行があると、日報・月報（report_dateの範囲・等価比較で絞り込む`list_daily_production_range()`/`list_daily_production_today()`）からは**一切見えなくなる**一方、引落・累計の計算（`get_app_cumulative_qty()`等、日付条件を持たないSUM集計）には**含まれ続けてしまう**。「日報・月報には出ないのに集計には反映される」という気づきにくい不整合になり得ることを、隔離コピーでの実際のクエリ実行で確認した。

**対応（完了）**：`models/production.py::replace_daily_result()`をさらに修正。

- 既存行のreport_date自体が空（NULLまたは空文字列）の場合も、「既存行なし」と同様に今日へフォールバックするよう変更（「行の存在」と「report_date列に有効な値があるか」を区別）。
- 呼び出し元から渡されたreport_dateが空文字列(`''`)の場合もNoneと同じ扱いにした（`if report_date is None:` → `if not report_date:`）。
- DELETE・INSERTの直前にもう一段、`report_date`が空でないことを確認するガードを追加した（将来同種の抜け道が生まれても、NOT NULL制約の列へ空の値を書き込まないための多重の防御）。

**動作確認（隔離コピー）**：NOT NULL制約を持たない別ファイルのテスト用DBを用意し、既存行のreport_dateがNULL・空文字列それぞれのケースで、修正後は今日の日付へフォールバックすることを確認。呼び出し元からreport_date=''が渡された場合も、既存行があればその日付を引き継ぎ、無ければ今日になることを確認。グループAB追記8の6シナリオを再実行し、退行が無いことも確認した。`python -m pytest tests/`への影響なし。

**未確認事項（申し送り）**：もう一方の拠点のDBで`production_daily.report_date`のNOT NULL制約が同じく適用されているかは、この環境からは確認できていない。次にその環境で作業する機会があれば、`CANONICAL_DESIGN_DECISIONS.md` §5の整合性チェック手順に沿って確認することを推奨する（同ファイル§5のチェック項目に追加済み）。

---

### グループAC：手動バックアップ機能の実装（新機能、2026-10-02）

`services/backup_service.py::backup_databases(destination_folder)`を新規実装した。単純なファイルコピー（`shutil.copy2()`等）ではなく、`sqlite3.Connection.backup()`（SQLite公式のオンラインバックアップAPI）を採用した。書き込み中のDBファイルをコピーしても、ページ単位で整合性を保って完了できるため。

マスタDB分離（グループAB以降、`CANONICAL_DESIGN_DECISIONS.md` D-38参照）後は、`config.DB_PATH`（月次DB）・`config.MASTER_DB_PATH`（マスタDB）の両方が揃って初めて完全な状態になるため、同一タイムスタンプで1回の操作としてまとめてバックアップする。ファイル名（`inventory_backup_<timestamp>.db`・`master_backup_<timestamp>.db`）が保存先フォルダに既に存在する場合は`_1`・`_2`...と連番を付与し、バックアップ対象のDBファイルがまだ存在しない場合（`master.db`未作成等）はそのDBのみスキップする。

メインメニューのヘッダー行に「バックアップ」ボタンを配置し、既存の非同期パターン（`LoadingWindow`＋スレッド＋`queue.Queue`＋ポーリング、`on_create_database()`と同じ構造）でバックグラウンド実行する。

**自己点検で発見・修正したバグ**：実装直後の検証で、`_poll_backup_queue()`が`on_backup_databases()`のローカル変数`destination_folder`をそのまま参照しており`NameError`になる不具合（バックアップ自体は完了するが、完了メッセージ表示・操作履歴記録の段階で例外になる）を発見した。インスタンス属性（`self._backup_destination_folder`）に保持する形に修正した。

詳細は`CANONICAL_DESIGN_DECISIONS.md` §19.1（D-44）参照。

### グループAC追記：バックアップファイルの共有フォルダ機能での再利用実証（調査のみ、新機能は不要と判明）

「バックアップファイルを他PCで取得・利用できるか」という問いに対し、既存の「共有フォルダのDBを開く」機能（`on_open_shared_database()`）を調査したところ、ファイル名に一切制約を持たないことを確認した。隔離コピー上で、実際にバックアップファイルをこの機能で開き、計画・実績データの読み込み・新規の実績登録がバックアップファイル自身に正しく反映されること、ロック機構（`services/db_lock_service.py`）もファイルパス文字列のみに依存する設計であることを確認した。**これにより新規機能の追加は不要と判断した。** 複数PCでの同時アクセスという実機・実ネットワーク環境での検証はできていない。詳細は`CANONICAL_DESIGN_DECISIONS.md` §19.2（D-45）参照。

### グループAD：.exe化のビルド実施（PyInstaller、2026-10-02〜03）

`inventory_app.spec`を新規作成し、`--onefile`形式で実際に`dist/InventoryApp.exe`（約124MB）のビルドに成功した。Tesseract OCR・Poppler本体は同梱しない方針を確定した（PDF OCR機能はまだ十分な精度・再現性が得られていないため）。OCRが必要な操作時にTesseractが見つからない場合はエラーダイアログを表示するのみでアプリ全体はクラッシュしない設計であることをコードで確認した。

ログイン画面・メインメニューの起動、`config.APP_DATA_DIR`（`%LOCALAPPDATA%\InventoryApp\`）へのDB作成は実機確認済み。**生産実績入力画面・共通マスタ5画面・バックアップ機能の実際のボタンクリックによる動作確認は、ttk製ウィジェットの自動化が技術的に困難だったため完了していない**（importの成功＝依存関係が揃っていることは確認済み。優先度の高い申し送り事項）。

**教訓の再現**：検証の過程で「windowedビルドが即座に終了する」という事象を一度観測し修正を加えたが、後の再検証で実際の原因は検証スクリプト自身の起動方法の問題であり、ビルド自体に不具合は無かったことが判明した。D-41（自分の検証結果を疑う）の教訓が.exe化の文脈でも再現した事例として記録する。

`.gitignore`に`build/`・`dist/`を追加した（100MB超のバイナリをGit管理対象から除外するため）。詳細は`CANONICAL_DESIGN_DECISIONS.md` §19.5・§19.6（D-48・D-49）参照。

### グループAE：作業者管理のセキュリティ上の空白の発見と、登録画面・管理画面への分割（重要、セキュリティ対応、2026-10-03）

作業者管理画面（`WorkerManagementWindow`）の仕様確認を行ったところ、ログイン画面自体にパスワード認証が無い（グループS、以前から既知）ことに加え、**作業者管理画面自体がログイン不要で誰でも開け**、画面内の全操作（新規登録・役割変更・有効/無効切替）に`role`（admin/operator）による制限が一切無いことが判明した。`role`列はデータとして存在するが、アクセス制御の目的では現行コードのどこからも参照されておらず実質的に無意味だった。

対策として、作業者管理機能を「登録画面」（`ui/worker_registration_window.py::WorkerRegistrationWindow`、新設、常時誰でも開けるがadmin不在時のみadmin選択肢を提示）と「管理画面」（既存`WorkerManagementWindow`を再編、既存作業者の編集・有効/無効切替専任、admin役割でログイン中の場合のみメインメニューに表示）に分割した。編集・切替の実行自体にも`_require_admin()`による関数レベルのチェックを追加し、画面を直接インスタンス化してメニューの表示制御を迂回しても拒否されることを実証した。operator役割でこの画面を開いた場合は、保存・切替ボタンをグレーアウトし「この画面の操作にはadmin権限が必要です」という案内ラベルを表示する見た目の対策も追加した（ボタン無効化・関数内チェックの二重防御）。

詳細は`CANONICAL_DESIGN_DECISIONS.md` §19.3（D-46）参照。

### グループAE追記：マスタデータの「不足分のみ取り込み」機能（2026-10-03）

複数PC間でadmin体制・マスタデータを揃える方法として、「現在のデータを正とし、バックアップ側は不足分のみ追加する」方針を採用した（上書き・完全マージは選ばない）。`services/master_merge_service.py::merge_master_from_backup(backup_file_path)`を新規実装し、5テーブル（`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`）それぞれについて、バックアップ側の主キーが現在の`master.db`に存在しない行だけを、既存のupsert関数を再利用して追加する。`workers`の取り込みでは`role`をバックアップ側の値のまま追加する。この取り込み操作もグループAEと同じ基準でadmin限定とした（メニュー非表示＋関数内の二重チェック）。

隔離コピー上で、既存レコードが上書きされないこと、同じバックアップファイルでの再実行が冪等（追加件数0件）であることを確認した。詳細は`CANONICAL_DESIGN_DECISIONS.md` §19.4（D-47）参照。

### グループAF：メインメニューの整理（共有フォルダ3ボタン廃止・バックアップの呼び出し新設・マスターデータ管理/マスターインポート削除・PDF読み取り/操作履歴のツール移動、2026-10-05）

共有フォルダ運用からローカル+バックアップ方式への転換（D-20）後も残っていた共有フォルダ直接アクセス用の3ボタン（グループY・Z等で実装）を削除し、「バックアップの呼び出し」（新しいローカルフォルダへコピーしてから切り替える、原本・元のDBとも不変）に置き換えた。あわせて「4.マスターデータ管理」「5.マスターインポート」（`parts`・`final_products`を読む機能がこの2画面以外に無いことを確認した上で削除）、PDF読み取り・操作履歴のツール移動（月次データ列から共通マスタ列の「ツール」見出し配下へ、番号を外して移動）も実施した。

削除にあたり、`services/master_import_service.py`に汎用CSVパーサ`parse_csv_generic()`（`services/production_import_service.py`も再利用していた）が同居しており、特定の関数名（`import_parts_csv`）だけのgrepでは見落としていたことが判明した。再利用部分は`services/csv_parsing_common.py`（新規）へ切り出し、内容を変更せずに移動した上で削除した（モジュール削除時はモジュール名でgrepすべき、という教訓）。

検証中、`db/schema.sql`に`workers`テーブルの未使用定義（マスタDB分離前の名残）が残っていたため、新規作成した月次DBが誤ってマスタDBと判定される事象を発見・回避した（`schema.sql`自体の整理は今回のスコープ外、未着手のまま残っている）。

**追記（2026-10-05）**：新規作成した月次DBだけでなく、**マスタDB分離（D-38・D-42）より前に実際に運用されていた月次DB**（`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`の5テーブルが同居する実データ）でも、同様に「マスタDBのバックアップ」と誤判定され拒否されることが分かった。判定方式を「マスタDB固有テーブルがあれば拒否」から「月次DB固有テーブル（`kitting_plan_items`）があれば受理」という肯定的な判定に変更し、分離前の月次DBも正しく取り込めるようにした。同居する旧マスタテーブルの中身は現行コードから読まれないこと・取り込み後もローカルの`master.db`が汚染されないことを、実在する分離直前の実バックアップファイルを使って確認した。詳細は`CANONICAL_DESIGN_DECISIONS.md` §20.8（D-53）参照。

詳細・削除/変更したファイルの一覧・検証結果は`CANONICAL_DESIGN_DECISIONS.md` §20（D-50〜D-53）参照。

### グループAG：画面の中央表示・DB未選択（既定DB）状態の操作禁止・在庫値出力済みDBの警告（2026-10-05）

全29クラス＋関数内ダイアログ11か所に中央寄せ（`ui/window_utils.py::center_window()`、新規）を適用した。調査の結果、関数内ダイアログ4か所は`geometry()`未指定の自動サイズだったことが判明し（前回調査の「全画面固定サイズ」という前提は誤り）、サイズ自体は変えずに中央寄せのみ適用した。

既定DB（`config.APP_DATA_DIR/db/inventory.db`、フォルダ名なし）を、ファイル・データの有無を問わず一律「未選択」として扱い、業務ボタン（月次データ・共通マスタ・ツール・バックアップ・マスタ取込）を無効化する方針を導入した（名前の付いていないDBへの気づかない入力・取込を防ぐため）。既定DBに残っていた実データ（テストデータ）は移行機能を実装せず、アプリからは今後到達不能なまま残す（削除は手動）。

在庫値出力（「7. 在庫値出力」）の完了を`operation_log`に記録し、出力済みDBを開いている間はメインメニューに警告（最終出力日時付き）を表示、月次データ1〜6を開く際には確認ダイアログを出す方針を導入した（禁止ではなく警告：出力後の修正・再出力を妨げないため）。

詳細・検証結果（54項目）は`CANONICAL_DESIGN_DECISIONS.md` §21（D-54〜D-58）参照。

### グループAH：未選択状態でのロック処理の停止・ロックファイルのgit除外（2026-10-05）

グループAGの検証中、実環境の`inventory_app/db/inventory.db.lock`（git管理下）が意図せず削除される事故が発生した。調査の結果、原因は特定できなかったが、このファイルは`.gitignore`の`*.db`パターンに一致しないため約1か月の間に9回コミットに混入しており、本セッションの削除はその繰り返しパターンの一例とみられる。`.gitignore`に`*.db.lock`を追加し追跡対象から外した（ファイル自体は`git checkout`で復元済み）。

あわせて、既定DB（未選択）の間は`MainWindow`がロックファイルにも一切触れないよう修正した（`acquire_lock()`を既定DBでは呼ばない）。従来は既定DBであってもロックを無条件に取得しようとしており、既定DBパスに他者の残留ロックがあると「未選択」の状態にすら入れず起動自体が拒否されてしまっていた。

詳細・検証結果（40項目）は`CANONICAL_DESIGN_DECISIONS.md` §22（D-59〜D-60）参照。

### グループAI：中央表示の基準と計算方法の修正（2026-10-05）

グループAGで導入した中央寄せについて「ディスプレイ中央より若干下に表示される」という報告を受け、`ui/window_utils.py::center_window()`を書き直した。原因は2つ：①`winfo_width/height()`等が返すのはタイトルバー・枠を除いた「クライアント領域」基準の値なのに、`geometry()`の位置指定は枠を含む「外枠」基準という取り違え（タイトルバー約31px・左右の枠約8px分、右下にずれていた）、②`winfo_screenwidth/height()`がタスクバーを含むディスプレイ全体の解像度で、タスクバー分を除いた「作業領域」ではなかったこと。

対策として、ctypes経由でWindows APIを直接呼び（追加の外部ライブラリなし）、`GetWindowRect()`で実際の外枠サイズ、`MonitorFromWindow()`+`GetMonitorInfoW()`の`rcWork`で作業領域を取得する設計に変更した。API呼び出し失敗時は従来相当の近似値にフォールバックし例外を出さない。配置基準も「parentの中央」から「parent（省略時はwindow自身）が乗っているモニターの作業領域の中央」に変更し、メインメニューを移動していても子画面の表示位置が変わらないようにした。

全29クラスを実際にボタン起動経路でインスタンス化して実測し29/29合格（非同期読み込みを伴う生産実績入力・ロット進捗・日々の引落も含む）。関数内ダイアログ10か所は1か所をライブ実測、残り9か所はソースコード上のパターン一致確認で代替した。

詳細・検証結果は`CANONICAL_DESIGN_DECISIONS.md` §23（D-61）参照。

### グループAJ：構成基板数マスター・基板丁数マスター画面の改善（2026-10-05）

事前調査（登録済み一覧にスクロールバーが無い・登録件数の常時表示が無い・取込が即時反映＋1行ごと個別コミットで途中失敗時に部分的な反映が残る、という3つの課題を特定）を受け、`ui/board_structure_import_window.py`・`ui/parts_attributes_import_window.py`・対応する`models`の両方に同じ改善を適用した。

縦スクロールバー（`ui/operation_log_window.py`と同じ実装パターン）・登録件数表示（専用のCOUNT関数を新設、画面を開いたとき・取込後に更新）を追加。取込を「CSV解析＋差分計算（追加/更新/変更なし/削除）→確認ダイアログ→「はい」の場合のみ確定」の2段階にし、削除がある場合は対象の先頭10件を明示、CSV内の重複キーは値が同じなら件数のみ、値が食い違う場合は行番号・各値・採用値（最後の行）を確認ダイアログに明記するようにした。

登録・更新・削除を取込専用の一括関数（既存の1行ごと個別コミットの関数はほかの呼び出し元〈`master_merge_service.py`等〉のためそのまま残した）で1トランザクションに統一し、途中で例外が発生しても取込前の状態に完全に戻るようにした。副次効果として3000件規模のCSVの取込時間が約34.7秒→約0.09秒に改善。想定外の例外（従来は`ValueError`以外が画面に表示されなかった既知の問題）もエラーダイアログで通知するよう修正し、完了メッセージの内訳を整理、警告が多い場合も全件確認できる専用ウィンドウ（`ui/warnings_list_window.py`、新設・両画面で共用）を追加した。

詳細・検証結果は`CANONICAL_DESIGN_DECISIONS.md` §24（D-62〜D-65）参照。

### グループAK：構成基板数マスター・基板丁数マスター画面への検索・ソート追加（2026-10-05）

グループAJの2画面に、検索（部分一致、全角/半角・大文字/小文字を区別しない）・列ソート（クリックで昇順/降順切替、見出しに▲▼表示）を追加した。事前調査で、既存の`ui/operation_log_window.py`（ソートの基本構造）・`ui/lot_progress_window.py`/`ui/unified_report_window.py`（数値列判定）・`ui/ng_input_window.py`（入力ごとの絞り込み）を参考にしたが、いずれも今回の「値が空のデータは昇順・降順どちらでも末尾」という要件には対応していなかったため、その部分のみ新しい方式（値が有るデータ・無いデータを分離してソートし、無いデータを常に末尾に連結）を採用した。既存画面のコードは変更していない。

構成基板数マスターには「構成基板数なしのみ表示」チェックボックスも追加。検索・ソートは表示専用で、取込処理（差分計算・一括トランザクション確定）は常にDBへ直接アクセスするため影響されない。絞り込み中は「表示: N件 / 登録件数: M件」の形式で両方表示し、取込完了後も検索語・ソート状態を保ったまま一覧が更新される。3000件規模でも検索・ソートともに0.1秒未満で応答することを確認済み。

あわせて、前回（グループAJ）導入した`ui/warnings_list_window.py::WarningsListWindow`が`center_window()`を使うために生じていた「39か所→40か所」のずれ（前回はスコープ外として見送っていた）を、今回反映した。

詳細・検証結果は`CANONICAL_DESIGN_DECISIONS.md` §25（D-66〜D-68）参照。

---

## 4. 未対応・将来の検討事項

- 項目14（実績履歴からのクリックで計画呼び出し）：未実装
- 実績入力画面（`ui/kitting_production_entry.py`）に、実績数量の「0以下」を弾くバリデーションが無い（NG入力画面には存在）
- ログイン⇔ログアウトを繰り返すたびに呼び出しスタックが深くなる特性（既存の設計、今回新規導入ではない）
- フィルタ適用後、ソート状態を自動的に再適用する機能は無い（v1として手動で列ヘッダーを押し直す仕様）
- `load_plan_list()`実行のたびにフィルタ・ソート状態がリセットされる（v1仕様）
- ~~`CANONICAL_DESIGN_DECISIONS.md` §5のチェックリスト項目1が、グループJ（余剰基板削除）により実態と合わなくなっている（`lot_surplus`表示の存在を前提にした文言のまま）。次回同ファイルを更新する際に修正すること~~ → 2026-09-01、CANONICAL_DESIGN_DECISIONS.md更新時にあわせて修正済み
- **バックアップ機能**（グループQ関連）：共有フォルダ運用・ロック機構・「対象外」マーク・在庫差異レポート連携が一通り完成したタイミングで着手を検討したが、具体的な方針が未決定のまま引き続き保留であることが再確認された（2026-09-11）。**2026-09-23、方針転換（`CANONICAL_DESIGN_DECISIONS.md` D-20参照）により「共有フォルダ運用の代替手段」という新しい重要性を持つようになったため、優先度を上げて検討する価値がある。** 着手する際は以下2点を先に決定する必要がある：
  - **タイミング**：月次DB切替時に自動／手動ボタン／定期自動、のいずれにするか。
  - **保存先**：共有フォルダ内の別フォルダのみとするか、ローカルPCとの併用にするか（方針転換後は「ローカルDB→共有フォルダへの定期コピー」が基本形になる見込み）。
- ~~**.exe化**（グループQ関連）：共有フォルダ運用の最終目標（PC各台にインストール）として言及されたのみで、具体的な着手はまだ~~ → **パス解決・データ領域分離の事前対応は完了（グループW参照）**。`BASE_DIR`/`APP_DATA_DIR`の分離、選択中DBパスの永続化（`services/app_settings_service.py`）まで実施済み。ただし実際のPyInstallerビルド本体（`.spec`ファイル作成、`requirements.txt`に`pyinstaller`が依存関係として記載されているのみでビルド設定は未着手）はまだ。**PDF OCR機能（`PDF_OCR_FEATURE_NOTES.md`参照）を導入する場合、Tesseract OCR本体（OS側の外部実行ファイル、pytesseractは同梱しない）を各PCの.exeに同梱するか個別インストールしてもらうかの方針も、この.exe化のタイミングで合わせて決定する必要がある。** **2026-09-23、`CANONICAL_DESIGN_DECISIONS.md` D-20の方針転換（共有フォルダ運用からローカル+バックアップ方式へ）により、.exe化の前提設計（共有フォルダ経由でのDB配置を想定していた）自体を見直す必要が生じた。着手時はこの新方針を踏まえること。**
- **複数ウィンドウ開放中のDB切り替えに関する未解決の設計課題**（2026-09-23発見）：既に開いている画面（`KittingProductionEntryWindow`・`ProductionImportStagingWindow`等）は、開いた時点のDBパスを内部に保持せず操作のたびに`config.DB_PATH`を都度参照するため、画面を開いたまま別経路でDBが切り替わると、既存画面の操作対象が気づかないうちに新しいDBへ変わってしまう。対応案（①画面ごとにDBパスを固定、②DB切替時に開いている全画面を強制的に閉じる）はいずれも未実装のまま記録に留めることをユーザーが選択した。詳細・未解決である理由は`CANONICAL_DESIGN_DECISIONS.md` D-19（新設の章）参照。**.exe化・本格運用の前に対応要否を再検討すべき重要な設計課題。**
- **Undo機能（実績登録の取り消し）**（2026-09-19発見）：「元に戻す」の定義（レコード削除か訂正前の値への復元か）が未確定のまま保留。グループAB追記参照。
- ~~ロード画面（`LoadingWindow`＋非同期パターン）未対応の画面、および「対象外」マークの仕組み（NG一覧・仕掛一覧）については、`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §6にまとめて記載した（グループQ・AC（本ファイル・同ファイル参照）に関連する未完了タスクのため、そちらもあわせて参照すること）。~~ → 2026-09-11、両方とも完了。ロード画面追加は本ファイルのグループR、「対象外」マークの仕組み・在庫差異レポートのゲート機能は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§10・§11を参照。
- ~~carry_over_incomplete_lots()のアトミック性（複数DBファイルをまたぐため単一トランザクションを持たない問題）~~ → 2026-09-11、進捗の可視化のみ対応完了（グループT参照）。真のロット単位・複数DBファイルをまたぐアトミック性はSQLite標準機能では実現困難なため、今回はスコープ外とすることが決定した。
- ~~DB接続の堅牢性（タイムアウト延長・get_connection()共通化）~~ → 2026-09-11、完了（グループU参照）。journal_mode（WALモード）は今回見送り、実運用で問題が出た場合に再検討する。
- ~~ロックファイルの耐性向上（原子的書き込み・フェイルクローズ化）~~ → 2026-09-11、完了（グループV参照）。誤削除への耐性向上（履歴ログ等によるヒューリスティック検知）は検討の上見送った。
- **（新規、対応不要と判断されたが記録）** ログイン機構がパスワード認証ではなく作業者名選択のみである点、トップレベル例外ハンドラが存在しない点は、堅牢性・セキュリティ調査（グループS、2026-09-11）で判明したが、特に対応の指示は無く現状維持とした。将来.exe化する際は、トップレベル例外ハンドラの追加（コンソールが無くなるため、未捕捉例外がどこにも表示されなくなる問題への対策）を検討する必要がある。
- ~~現在接続中のDBパス表示機能~~ → 2026-09-15、完了（グループX参照）。
- ~~月別DB一覧の共有フォルダ対応~~ → 2026-09-15、完了（グループY参照）。
- ~~月別DB削除機能~~ → 2026-09-15、完了（グループZ参照）。既存のロック機構（`get_lock_info()`）を活用した安全対策込み。
- **ロック機構の同一作業者・同一PCからの即時再取得** → 2026-09-16、完了（グループQ追記参照）。正常終了せずに閉じた直後の再ログインが30分待たされる使いにくさを解消。
- **キッティング計画CSV取込の高速化** → 2026-09-16、完了（グループA追記2参照）。冗長な`init_kitting_plan_tables()`呼び出しを削除し、500行で約22%・2000行で約30%高速化。
- **操作履歴機能** → 2026-09-16、完了（グループAA参照）。「いつ・誰が・どの操作をしたか」を`operation_log`テーブルに記録する新機能。
- **（新規、リファクタリング候補）** `models/kitting_plan.py::upsert_plan_item()`（`create_plan_version()`のレガシー互換ラッパー）に呼び出し元が存在しないことを、キッティング計画CSV取込の高速化作業（グループA追記2、2026-09-16）の際に確認した。削除はまだ行っていない（.exe化・バックアップ機能の完了後に着手する方針のリファクタリング候補の1つとして記録）。
- **明示的な起動用バッチファイル`run_app.bat`を新規作成済み**（2026-09-15）：`.venv\Scripts\python.exe`を明示指定して`main.py`を起動する。実際にアプリを起動する環境（システムPython）と開発・検証環境（`.venv`）が食い違っていた問題への対応の一環。詳細な経緯・教訓は`CANONICAL_DESIGN_DECISIONS.md`§7参照。
- ~~**リポジトリ直下の`launch.json`・`settings.json`削除**：用途不明の古い設定ファイル（`.vscode/`配下ではない、VSCode自体は読み込まない死んだ設定）と判明し`git rm`でステージング済みだが、2026-09-15時点でまだコミットされていない（ユーザーが意図的に保留、準備ができたタイミングでコミット判断）。~~ → **2026-09-16時点で確認したところ、既にコミット済み**（`git log --diff-filter=D`で確認、コミット`94be9da`）。上記の「まだコミットされていない」という記述は古い状態のままだったため本更新で修正した。`.vscode/launch.json`・`.vscode/settings.json`（実際にVSCodeが使う設定）は無事残存・内容変更なしを確認済み。
- **日報・月報の引落数・仕掛数計算バグ（重大な根本ロジック不一致）** → 2026-09-16、完了。`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §14参照。
- **実績CSV取込の機能拡張（①速度改善・②ステージング永続化・③④重複登録防止・⑤不一致リスト分離・⑦日付差ハイライト・⑥左右ペイン統合・⑧自動確定機能）** → 2026-09-16、完了。グループAB参照。
- **⑨構成基板数マスタの表記ゆれ対応** → 2026-09-16、**見送り決定**。原因（マスタ側への未登録が大半、一部は`***`記号の表記ゆれ）は判明済み。`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §7参照。将来再検討する場合、`***`除去等の安全な範囲の対応と、編集距離等による人間確認前提の対応の2段階が考えられる。
- **実績CSV取込画面（ProductionImportStagingWindow）への追加改善（右ペインのスクロールバー・列ソート・件数表示、左ペインのpack順序バグ修正、メインメニューボタンの移設、重複取込確認ダイアログ、「候補なしとする」機能、「登録済みスキップ」行の第三リスト化）** → 2026-09-23、完了。グループAB追記3参照。
- **複数バッチ分割ロットの業務シナリオ検証（同一ロット・同一製品名で後日追加のキッティングNo.が存在するケース）** → 2026-09-23、調査完了・修正不要と判明。グループAB追記4参照。
- **計画一覧「入力済みを隠す」フィルタの根本ロジック不一致（日報・月報バグ（本ファイル上記参照）・仕掛数量抽出に続く3件目の同種バグ）** → 2026-09-23、完了。`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §15参照。
- **`calculate_lot_completion()`の`order_quantity`/`remaining_quantity`代表値選択の非決定性**（2026-09-23、グループAB追記4の検証中に副次的に発見）：同一lot_no内で`order_qty`が複数kitting_list_noにまたがって食い違う場合（`order_qty_inconsistent=True`）、代表値として`plan_items[0]["order_qty"]`を採用しているが、取得元`list_plan_items_by_lot()`に`ORDER BY`が無いため行の順序がSQLiteのクエリプランに依存し保証されない。実際に`plan_items`の順序を入れ替えるだけで`remaining_quantity`が0にも-498にもなることを実機確認した。既存のdocstringには「月報側での警告表示を想定した既知の制約」と記載済みだが、符号が反転しうるレベルの非決定性であることまでは記載されていなかった。**未対応（報告のみ、コード修正はまだ行っていない）**。対応要否は今後検討する。
- **保留マージロジックの識別条件不足による不正データ混入（重大）** → 2026-09-24、完了。グループAB追記5参照。`_merge_from_pending()`に`board_name`一致チェックを追加し、混入していた673件（`plan_batch_id=2, 3`）は実害無しを確認の上、事前バックアップを取って削除した。
- **「入力済みを隠す」フィルタの境界条件バグ（未登録の計画が誤って非表示になる）** → 2026-09-24、完了。`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §15追記部分参照。グループAB追記3・4で確立した`calculate_lot_completion()`統一実装（本ファイル上記参照）自体に内在していた境界条件バグ（`completed_quantity`との比較を`order_qty`との比較に修正）。
- **実績CSV取込への誤フォーマットファイル混入問題（2回発生）・恒久対策** → 2026-09-24、完了。グループAB追記6参照。誤って取り込まれた457件（`pending_csv_import_rows`）は実害無しを確認の上、事前バックアップを取って削除。恒久対策として`services/csv_format_detection.py`（新規）を実績CSV取込・キッティング計画CSV取込の両方に組み込み、フォーマット取り違えを警告する仕組み（強制ブロックはしない）を導入した。
- **右クリック即時登録の使い勝手改善2点（数量差の修正導線・登録後の自動次行選択）** → 2026-09-24、完了。グループAB追記7参照。
- **PDF OCR機能の大規模改修（高速化・精度改善・複数ページ対応）** → 2026-09-25〜26、完了。`PDF_OCR_FEATURE_NOTES.md` §8参照。実際の81ページPDFでの検証により、複数ページ列復元ロジックの重大バグ（ページ境界を考慮しない座標処理による行の誤結合）を含む複数の問題を発見・修正した。
- **構成基板数マスタ関連の日報・月報組み込み**（「未確定」仮想行・マスタ未登録警告・CSV出力・逆方向不整合(超過)への対応・構成基板数列/ロット単位縞模様表示） → 2026-09-25〜26、完了。`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §16参照。§7・本ファイル上記「⑨構成基板数マスタの表記ゆれ対応」の見送り決定と整合する形で、表記ゆれの自動判定は今回も見送り、候補提示のみに留めている。
- ~~**構成基板数チェックの設計上の欠陥2件の発見と、独立機能への切り出し**~~ → 2026-09-26に原因判明・独立関数`check_lot_progress()`実装完了、**2026-09-28、UI画面（`ui/lot_progress_window.py`、「ロット進捗チェック」ボタン）も実装完了**。保留理由だった構成基板数マスタ`board_count`列の意味に関する疑義も解消済み（`board_count`はロット単位の値であり、(lot_no, board_name)単位での比較自体が誤りだった）。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17・`CANONICAL_DESIGN_DECISIONS.md` D-24〜D-26参照。
- **ロット状態判定・引落ルールの一本化** → 2026-09-28、完了。`services/production_service.py::_evaluate_lot_status()`に構成基板数チェック・引落・仕掛/未生産・「未確定」判定を集約し、`check_lot_progress()`・日報・月報・仕掛数量抽出が同じ関数を使うようにした。不足（shortfall）ロットのみ引落0とするルールに訂正（`DRAWDOWN_ZERO_STATUSES`）。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17.4・`CANONICAL_DESIGN_DECISIONS.md` D-26参照。
- **確定登録の後にスナップショットから消えたロットの訂正導線が無い問題** → 2026-09-28、対策実装完了。`ui/wip_expansion_window.py`の一覧を「スナップショット∪確定登録済み」の和集合方式に変更し、既存の「実績修正」ボタンから訂正できるようにした（NG側と同じ設計に統一）。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §18・`CANONICAL_DESIGN_DECISIONS.md` D-27参照。
- **未解決のまま残っている事項（2026-09-28時点）**：構成基板数がロット内で複数値に分かれる2件（lot_no=271569・344294）・超過2件（lot_no=262752・468052、ファイルNo表記統合の候補なし）の業務側確認待ち、生産実績入力画面の情報欄（`ui/kitting_production_entry.py`）が`_evaluate_lot_status()`に未統一のまま独自に`calculate_lot_completion()`・`get_board_structure()`を呼んでいる点、日報・月報の縞模様・確認事項列の文字色・印刷プレビューでのはみ出し・仕掛展開画面の赤字表示、いずれも実機目視確認が未実施。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17.6参照。
- **production_daily.report_dateが手動修正のたびに意図せず「今日」へ書き換わる問題** → 2026-09-29、完了。グループAB追記8参照。CSV経由の登録は元々壊れていなかったが、①既存計画を選び直しての手動再登録、②面連動登録（反対側の面）、③右クリック即時登録の数量不一致修正フロー、の3経路で発生していた。「実際に生産した日」と「修正した日」は別の情報であるべき、というユーザー決定の方針に基づき、report_dateが明示的に渡されない場合は既存行の値を引き継ぐよう修正した（`CANONICAL_DESIGN_DECISIONS.md` D-28参照）。
- **report_dateの空値（NULL・空文字列）に関する堅牢化** → 2026-09-29、完了。グループAB追記9参照。上記の修正過程で見つかった「既存行のreport_date自体が空だった場合にNULLが連鎖する」ロジックの隙間に対応した。現行スキーマのNOT NULL制約下では実害が出ない（実DBの19行でも該当0件）ことを確認済みだが、コード自体の脆弱性として修正した。もう一方の拠点でも同じNOT NULL制約が適用されているかは未確認（`CANONICAL_DESIGN_DECISIONS.md` §5のチェック項目に追加済み）。
- ~~**日々の引落・完了一覧・未完了一覧（Step2・Step3）**~~ → **2026-09-30、完了（方針転換あり）**。上記2件（グループAB追記8・9）はその前提となるreport_dateの信頼性確保（Step1）だったが、`report_date`ベースの逆算では後からの修正・遅延登録で過去の累計が事後的に変わりうる限界が判明したため、`models/lot_status_history.py`（新規、実績の登録・修正のたびに状態を記録するイベントログ）を使う方式に転換した。「日々の引落一覧」画面（`ui/daily_drawdown_window.py`）を実装、「未完了一覧」は既存の`check_lot_progress()`を使う方針とした。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §19・§20、`CANONICAL_DESIGN_DECISIONS.md` §17（D-29〜D-31）参照。
- **日報・月報の統合画面（`UnifiedReportWindow`）への一本化** → **2026-10-01、完了（重要・大規模な変更）**。旧`DailyReportWindow`・`MonthlyReportWindow`を削除し、`ui/unified_report_window.py::UnifiedReportWindow`に一本化した。ロット進捗チェック・日々の引落一覧は母集団・時間軸の性質が異なるため独立画面として維持する方針（「実績のあるロット全体を土台にした全4画面統合」案は見送り）。統合の過程で、日報画面が確認事項欄の色分け警告（`configure_status_color_tags()`）を一度も呼んでいなかったバグを発見・修正した。期間選択（今日/今週/今月/カスタム範囲）・5種警告のオン/オフ切り替え・列ヘッダーソート（ロット単位のブロックを保つ、既存の`sort_by_column()`では対応できない非連続データ向けに新しいブロック検出方式を採用）・行の絞り込み（状態・NG有無・ロットNo.部分一致）・列の表示/非表示（CSV/PDF出力には影響しない）を実装。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §21、`CANONICAL_DESIGN_DECISIONS.md` §17（D-32〜D-34）参照。
- **`_perform_registration()`の「重複」呼び出しの調査** → **2026-09-30、調査完了・現状維持**。`search_plan()`後の`_setup_ng_side_ui()`・`_load_current_daily_qty()`の再呼び出しは、間にNG申告の保存等の実際のDB書き込みが挟まっているため、削除すると登録後のNG入力欄の表示が崩れることを確認し、削除しない判断で確定した。`CANONICAL_DESIGN_DECISIONS.md` D-32参照。
- **未確認・申し送り事項（2026-10-01時点）**：「今週」の起算日（月曜起算とした）が業務慣習と合っているか、統合画面の列表示/非表示のレイアウトの見た目、チェックボックスの状態を次回起動時に保持するか、大量データ（922行）でのフィルタ操作の体感速度。いずれも業務側の意向確認・実機目視確認が未実施。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §21参照。
- **UnifiedReportWindowへの「登録日」列追加・未完了計画のDB間引き継ぎルールの見直し** → **2026-10-01、完了**。`production_daily.report_date`を登録日列としてTreeview・CSV・PDF出力に追加（代表選定ロジックは不要と判明）。`list_incomplete_lots()`を`_evaluate_lot_status()`ベースの判定に統一し、構成基板数不足ロットが無条件で未完了引き継ぎ対象になるよう修正。未着手計画は実装予定日が引継ぎ日から50日以内のものだけを引き継ぐ新ルールを追加（パース不能な日付は安全側で含める）。ロット単位（完了済みファイルNo込み）での引き継ぎは既存実装で既に満たされていたと確認済み（コード変更不要）。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §22、`CANONICAL_DESIGN_DECISIONS.md` §18.1・§18.2（D-35〜D-37）参照。
- **共通マスタが月次DBに同居している設計上の課題** → **2026-10-01、発見。2026-10-02、分離自体はコードとして実装済みであることが判明した**（`config.MASTER_DB_PATH`・`get_master_connection()`・`board_structure_master.py`/`parts_attributes.py`/`workers.py`/`master.py`の接続先切り替え・`tests/conftest.py`）。隔離コピー上で、ログイン画面の作業者一覧0件表示・作業者登録・構成基板数マスタCSVインポートがいずれも`master.db`へ正しく反映されることを確認済み。`inventory.db`側に残っていた孤立データ（5テーブル）は2026-10-02に削除済み（下記参照）、`master.db`にはログイン動作確認用のテスト作業者（`worker_id=W001`）を登録済み。**【重要・未解決・優先度高】`board_structure_master`の実データ（3119件）そのものをCSV再インポート等で`master.db`側に再構築する作業は、依然として未着手のまま残っている。** `bom_master`は月次の概念と矛盾しないキャッシュのため分離対象に含めない。詳細は`BOM_MIGRATION_NOTES.md` §14、`CANONICAL_DESIGN_DECISIONS.md` §18.3・§18.6（D-38・D-42〜D-43）参照。
- **レガシーテーブル14個の削除**（`production_records`・`lots`・`usage_daily`・`incoming_goods_log`・`stock_manual_adjustment`・`closing_runs`・`closing_wip_adjustment`・`audit_log`・`snapshot_batches`・`stock_snapshot`・`physical_count`・`board_definitions`・`component_bom`・`component_groups`） → **2026-10-01、完了**。実DB0件・現行コードから一切参照されていないことを確認した上でバックアップを取って削除。`production_daily.lot_id REFERENCES lots(lot_id)`という外部キー宣言は、アプリが`PRAGMA foreign_keys`を設定しないため実際には強制されておらず、かつ`lots`自体に現行コードからのINSERTが無いため実質的に満たされ得ないものだったと判明。`parts`・`final_products`は分離検討対象として削除せず維持。詳細は`BOM_MIGRATION_NOTES.md` §14、`CANONICAL_DESIGN_DECISIONS.md` §18.4（D-39）参照。
- **【重要・必ず記録】検証作業自体の教訓（2026-10-01）**：レガシーテーブル削除後の動作確認の過程で、2件の誤った報告が発生し、いずれも事後的に自分で誤りに気づいて訂正した。(a)文字コード不一致で文字化けしたウィンドウタイトルを正確に確認せず「ログイン画面がスキップされた」と誤報告（実際はログイン画面は正しく表示されていた。`GetWindowTextW`でのUnicode直接取得により訂正）。(b)検証スクリプトが`MainWindow`を`destroy()`で直接閉じ、`WM_DELETE_WINDOW`プロトコル経由のロック解放処理が実行されなかったため、実DBのロックファイルに検証用の架空の作業者名が残留し、「別の人物が使用中では」という懸念を招いた（`force=True`での奪取→正規の`release_lock()`で後始末済み）。**教訓**：`MainWindow`を直接インスタンス化する検証では、`destroy()`ではなく正規の終了経路を使うか確実にロック解放の後始末を行うこと。文字化けした出力を根拠に断定的な報告をしないこと。詳細・`CANONICAL_DESIGN_DECISIONS.md`の既存原則（D-18「調査結果が確認できない場合は推測で断定しない」）との関連付けは同ファイル §8（D-40）・§9（D-41）参照。
- **手動バックアップ機能（`services/backup_service.py::backup_databases()`）** → **2026-10-02、完了**。月次DB・マスタDBを同一タイムスタンプでまとめてバックアップ、`sqlite3.Connection.backup()`採用、ファイル名重複時の連番付与。グループAC参照。
- **バックアップ経由での月次DB共有の実証** → **2026-10-02、調査完了・新機能は不要と判明**。既存の「共有フォルダのDBを開く」機能でバックアップファイルをそのまま開けることを実証した。複数PCでの実機・実ネットワーク環境での検証は未実施。グループAC追記参照。
- **【重要・優先度高】board_structure_masterの実データ（3119件）のmaster.dbへの再構築** → **未着手**。CSV再インポート等による再構築が必要。`BOM_MIGRATION_NOTES.md` §14参照。
- **.exe化のビルド実施**（`inventory_app.spec`、`--onefile`形式） → **2026-10-02〜03、ビルド完了**。ログイン画面・メインメニュー起動・`%LOCALAPPDATA%`へのDB作成は実機確認済み。**【重要・優先度高】生産実績入力画面・共通マスタ5画面・バックアップ機能・マスタデータ取り込み機能の実際のボタンクリックによる動作確認は未完了**（ttk製ウィジェットの自動化が技術的に困難だったため、手動確認待ち）。Tesseract・Popplerは同梱しない方針を確定。検証中に観測した「windowedビルドが即座に終了する」事象は、後の再検証で検証スクリプト自身の起動方法の問題と判明し、ビルド自体に不具合は無かった（D-41の教訓の再現）。グループAD参照。
- **作業者管理のセキュリティ上の空白の発見と対策（登録画面・管理画面への分割）** → **2026-10-03、完了（重要・セキュリティ対応）**。ログイン画面にパスワード認証が無いことに加え、作業者管理画面自体がログイン不要で誰でも開け、role列が実質無視されていたことが判明。登録画面（`WorkerRegistrationWindow`、新設）と管理画面（既存`WorkerManagementWindow`を再編、admin限定）に分割し、ボタングレーアウト＋関数内チェックの二重防御を実装。グループAE参照。
- **マスタデータの「不足分のみ取り込み」機能（`services/master_merge_service.py::merge_master_from_backup()`）** → **2026-10-03、完了**。現在のデータを正とし、バックアップ側は不足分のみ追加する方針（上書きしない）。admin限定操作。グループAE追記参照。
- **メインメニューの整理（共有フォルダ3ボタン廃止・バックアップの呼び出し新設・マスターデータ管理/マスターインポート削除・PDF読み取り/操作履歴のツール移動）** → **2026-10-05、完了**（判定ロジックの追加修正含む）。グループAF参照。`CANONICAL_DESIGN_DECISIONS.md` D-50〜D-53も参照。
- **【新規・優先度高】`db/schema.sql`に残る未使用テーブル定義（`workers`・`parts`・`final_products`・`lots`）の整理** → **未着手**。マスタDB分離（D-38・D-42）・レガシーテーブル削除（D-39）がいずれも実DB側・`models`層のコード側の対応に留まり、新規DB作成時のテーブル定義（`schema.sql`）側は更新されていなかったことが、メインメニュー整理（グループAF）の検証中に判明した。詳細は`CANONICAL_DESIGN_DECISIONS.md` §20.5（D-51）参照。
- **画面の中央表示・DB未選択（既定DB）状態の操作禁止・在庫値出力済みDBの警告** → **2026-10-05、完了**。グループAG参照。`CANONICAL_DESIGN_DECISIONS.md` D-54〜D-58も参照。
- **未選択状態でのロック処理の停止・ロックファイルのgit除外** → **2026-10-05、完了**。グループAH参照。`CANONICAL_DESIGN_DECISIONS.md` D-59〜D-60も参照。
- **中央表示の基準と計算方法の修正（クライアント領域/外枠の取り違え、作業領域基準への変更）** → **2026-10-05、完了**。グループAI参照。`CANONICAL_DESIGN_DECISIONS.md` D-61も参照。
- **構成基板数マスター・基板丁数マスター画面の改善（スクロールバー・登録件数表示・取込前確認・一括トランザクション化）** → **2026-10-05、完了**。グループAJ参照。`CANONICAL_DESIGN_DECISIONS.md` D-62〜D-65も参照。
- **構成基板数マスター・基板丁数マスター画面への検索・ソート追加** → **2026-10-05、完了**。グループAK参照。`CANONICAL_DESIGN_DECISIONS.md` D-66〜D-68も参照。
- **ウィンドウ幅（`reqwidth=986px`）が`940px`を超える件の実機目視確認** → **未着手**。`CANONICAL_DESIGN_DECISIONS.md` §20.9参照。
- **D-19（画面を開いたままDBを切り替えた場合の課題）** → 本タスク（グループAG）でも対応せず、未解決のまま。
