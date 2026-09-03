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

## 4. 未対応・将来の検討事項

- 項目14（実績履歴からのクリックで計画呼び出し）：未実装
- 実績入力画面（`ui/kitting_production_entry.py`）に、実績数量の「0以下」を弾くバリデーションが無い（NG入力画面には存在）
- ログイン⇔ログアウトを繰り返すたびに呼び出しスタックが深くなる特性（既存の設計、今回新規導入ではない）
- フィルタ適用後、ソート状態を自動的に再適用する機能は無い（v1として手動で列ヘッダーを押し直す仕様）
- `load_plan_list()`実行のたびにフィルタ・ソート状態がリセットされる（v1仕様）
- ~~`CANONICAL_DESIGN_DECISIONS.md` §5のチェックリスト項目1が、グループJ（余剰基板削除）により実態と合わなくなっている（`lot_surplus`表示の存在を前提にした文言のまま）。次回同ファイルを更新する際に修正すること~~ → 2026-09-01、CANONICAL_DESIGN_DECISIONS.md更新時にあわせて修正済み
- **バックアップ機能**（グループQ関連）：共有フォルダ運用の一環として要望があったが、「機能が確定して安定してから」実装する方針で保留中。具体的な設計・着手はまだ。
- **.exe化**（グループQ関連）：共有フォルダ運用の最終目標（PC各台にインストール）として言及されたのみで、具体的な着手はまだ。`requirements.txt`に`pyinstaller`が依存関係として記載されているのみで、`.spec`ファイル等のビルド設定は存在しない（別途調査済み）。
- ロード画面（`LoadingWindow`＋非同期パターン）未対応の画面、および「対象外」マークの仕組み（NG一覧・仕掛一覧）については、`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §6にまとめて記載した（グループQ・AC（本ファイル・同ファイル参照）に関連する未完了タスクのため、そちらもあわせて参照すること）。
