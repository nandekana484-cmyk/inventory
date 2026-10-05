# CANONICAL_DESIGN_DECISIONS.md

## 1. 概要

**目的**：複数の調査ドキュメント(BOM_MIGRATION_NOTES.md・PRODUCTION_NG_ENHANCEMENTS_NOTES.md・UI_WORKFLOW_FIXES_NOTES.md)に分散している「確定した設計決定」だけを集約し、正典(canonical)として参照できるようにする。各決定の調査経緯・詳細な検証データは、リンク先の各ノートを参照のこと。本ファイルは決定事項の要約とポインタに徹し、詳細を重複して記載しない。

**対象読者**：`inventory_app`（部品在庫管理アプリ）のコードに触れる開発者。

**作成日**：2026-09-01（本ファイルはこの日付時点でリポジトリに存在しなかったため新規作成した）

---

## 2. 確定した設計決定サマリ

| # | 決定事項 | 詳細 |
|---|---|---|
| D-1 | BOM TSVのヘッダーにはA/B/Cの3表記ゆれが存在し、いずれも実質同一データとして吸収する（列ごとに独立して実在する方の列名を採用）。Dパターン（ヘッダー破損）はエラー扱いとする | BOM_MIGRATION_NOTES.md §9 |
| D-2 | 同一file_noは複数の実装ライン向けに同一BOMを重複記載しているため、`mounting_line`で絞り込まずに合算してはならない | BOM_MIGRATION_NOTES.md §10 |
| D-3 | 丁取り数（`parts_attributes.teitori`）は部品自身の96コードではなく、同一file_no・実装ライン内の「K行」（セットアップ部品種別='K'、基板自身を表す行）の96コードに対して登録されている。K行は生産面に関係なく常に生産面=1で記録される | BOM_MIGRATION_NOTES.md §11 |
| D-4 | ~~`parts`（部品マスタ）・`final_products`（完成品マスタ）・`lots`テーブル、および「4.マスターデータ管理」「11.マスタインポート」画面は、現行のBOM基盤・キッティング計画・生産実績のいずれからも参照されない第一世代設計の名残であり、削除ではなく現状維持（参考情報として残す）~~ → **2026-10-01、一部訂正・更新（D-39参照）**。`parts`・`final_products`は、実際には現行の「マスターデータ管理」（`ui/master_management.py`）・「マスターインポート」（`services/master_import_service.py`）画面から現役で読み書きされていることが再調査で判明した（当時の「参照されない」という記述は誤りだった）ため、削除せず現状維持（共通マスタ分離の検討対象、D-38）とする。一方`lots`は、現行コードから本当に一切参照されていないこと（実DB0件、FK宣言は不整合、D-39参照）を確認した上で2026-10-01に削除した → **さらに2026-10-05、メインメニュー整理（D-50）により「マスターデータ管理」（`ui/master_management.py`）・「マスターインポート」（`ui/master_import_window.py`・`services/master_import_service.py`）画面自体を削除した**。削除理由：`parts`・`final_products`を読む機能（`get_all_parts()`・`get_all_products()`）がこの2画面以外に存在しないことを確認済み（他画面からの参照なし）。`parts`・`final_products`テーブル自体・`models/master.py`の`upsert_part`/`upsert_product`/`init_master_tables`（`services/master_merge_service.py`が「不足分のみ取り込み」機能で使用）は削除せず残置した。呼び出し元が無くなった`get_all_parts`/`get_all_products`/`delete_part`/`delete_product`は`models/master.py`から削除した | 本ファイル §5・§18・§20 |
| D-5 | `kitting_list_no`単体では計画を一意に識別できない（同一kitting_list_noが複数の異なるlot_noにまたがって存在するのが正常な業務パターン）。`(kitting_list_no, lot_no)`の組み合わせで初めて一意になる | PRODUCTION_NG_ENHANCEMENTS_NOTES.md §5 |
| D-6 | 実績・NG申告は「日付問わず1計画（kitting_list_no, lot_no）1レコード、常に上書き」で統一する（同じロットの別日生産は別のkitting_list_noとして立てる業務運用のため） | PRODUCTION_NG_ENHANCEMENTS_NOTES.md §6 |
| D-7 | 実績CSV取込には`import_production_csv()`（即時登録、後方互換のため維持）と`parse_production_csv_for_staging()`＋`ProductionImportStagingWindow`（ステージング方式、現在の標準フロー）の2系統が**意図的に併存**している。片方が巻き戻った結果ではない | UI_WORKFLOW_FIXES_NOTES.md §3 グループH（H-2） |
| D-8 | 面2が存在する場合、面1は一覧表示（基板別実績・日次実績履歴・日報・月報・仕掛数量抽出）から省略し面2のみ表示する統一ルール。ただし面1の実績が面2を上回る状態（`ActualCorrectionWindow`の片面修正等が原因）は**本来あってはならない不整合**であり、除外した上で別途警告する（黙って消さない） | UI_WORKFLOW_FIXES_NOTES.md §3 グループI（I-3〜I-5） |
| D-9 | `services/unprocessed_check_service.py`は、NG一覧・仕掛一覧の未処理判定ロジックを複製せず、`ui.ng_input_window.NgInputWindow`・`ui.wip_expansion_window.WipExpansionWindow`の該当staticmethodをそのままimportして再利用している。通常避けるべき「services層がui層に依存する」方向の依存関係だが、ロジック複製によるドリフト（`calculate_lot_completion()`と`list_incomplete_lots()`の食い違いが過去に発生した例と同種の問題）を避けるための**意図的な例外**であり、「壊れている」設計ではない | PRODUCTION_NG_ENHANCEMENTS_NOTES.md §11 |
| D-10 | 共有フォルダ上でのSQLite運用改善は、WALモードへの変更ではなく接続タイムアウト延長（30秒）＋`get_connection()`共通化のみを採用する。WALモードはSMB上で補助ファイル（-wal・-shm）へのロックが正しく機能せず、通常のジャーナルモードより状況を悪化させ得るため見送り、実運用で問題が出た場合に改めて検討する | UI_WORKFLOW_FIXES_NOTES.md グループU |
| D-11 | ロックファイル（`.lock`）が破損している（内容を読み取れない）場合、フェイルオープン（無条件に次の利用者が取得可能）ではなくフェイルクローズ（`LockFileCorruptedError`を送出し、ユーザーが明示的に確認した場合のみ`force=True`で上書き取得）を採用する。共有DBの整合性を扱うシステムでは、同時書き込み中の見逃しの方が実害が大きいため | UI_WORKFLOW_FIXES_NOTES.md グループV |
| D-12 | ロックファイルの誤削除（手動削除）への耐性向上（追記型履歴ログ等によるヒューリスティック検知）は見送り、現状維持（ファイルが存在しない＝ロック無しとして取得可能）とする。履歴ログ自体も同じ共有フォルダ上のファイルであり、書き込み競合・肥大化という別の問題を生むため | UI_WORKFLOW_FIXES_NOTES.md グループV |
| D-13 | コード変更後の検証で使う「全パッケージimportループチェック」は、作業ディレクトリ全体ではなく`ui`/`models`/`services`/`config`に限定したスコープ付きで実行する。作業ディレクトリ直下の単発メンテナンススクリプト（`check_*.py`・`delete_*.py`等）まで無差別にimportすると、それらのモジュールトップレベルの処理が実DBに対して実行されてしまうリスクがあるため | 本ファイル §5「検証スクリプト自体の安全性」 |
| D-14 | 開発・検証環境（`.venv`）と実際にアプリを起動する環境（システムPython等）は食い違いうる。`requirements.txt`へのパッケージ追加は、実際に`pip install`を実行した環境にしか反映されない。起動方法を明示的なバッチファイル（`run_app.bat`、常に`.venv`のpython.exeを指定）に統一することで、この環境依存を解消した | 本ファイル §7「実行環境・依存パッケージのドリフト問題」 |
| D-15 | `ui.main_window.MainWindow`を検証スクリプトから直接インスタンス化する際は、`config.DB_PATH`だけでなく`config.APP_DATA_DIR`も一時ディレクトリへ隔離すること。`MainWindow.__init__()`は起動時に`app_settings.json`（`config.APP_DATA_DIR`配下）から前回選択されていたDBパスを自動復元する仕組み（D-14と同時期に実装）を持つため、`DB_PATH`だけ上書きしても、この復元ロジックが実環境側の設定を読んで実DBへ接続・ロック取得してしまう | 本ファイル §8「検証・テスト時の状態隔離に関する注意点」 |
| D-16 | 一度確定した調査結論（「実装するにはAの前提が必要」等）も、要望の再依頼があれば再検証する価値がある。初回調査で「不要」と判断した前提が、実は既存の参照関係（`parent`への参照保持等）を見落としていただけだった、という事例が実際に発生した | UI_WORKFLOW_FIXES_NOTES.md グループAB（AB-6） |
| D-17 | 日付をDBへ保存する際は、入力の表記（区切り文字・ゼロ埋めの有無等）によらず、必ず統一形式（例："YYYY-MM-DD"）へ正規化してから保存すること。表記ゆれが混在すると、文字列比較による日付範囲検索（`WHERE date_col >= ? AND date_col <= ?`）が誤動作しうる（例："2026-9-5"が文字列として"2026-10-01"より大きいと判定される） | UI_WORKFLOW_FIXES_NOTES.md グループAB（AB-8） |
| D-18 | 調査結果（実際のコード・データ）が確認できない状況では、推測で具体的な原因（存在しない変数名を含む診断等）を断定してはならない。「分からない」と伝え、確認を待つべきである | 本ファイル §9、UI_WORKFLOW_FIXES_NOTES.md グループAB追記2 |
| D-19 | 既に開いている画面（`KittingProductionEntryWindow`等）は開いた時点のDBパスを保持せず、操作のたびに`config.DB_PATH`を都度参照する設計のため、画面を開いたまま別経路でDBが切り替わると、既存画面の操作対象が気づかないうちに新しいDBへ変わる。**未解決のまま記録**（対応方針は本ファイル §10参照） | 本ファイル §10 |
| D-20 | 運用方針を「共有フォルダ上のDBへ複数PCが直接アクセスする」方式から「基本はローカルDBに統一し、共有は今後実装予定のバックアップ機能（ローカル→共有フォルダへの定期コピー）で行う」方式へ転換した。既存のロック機構（D-10・D-11等）は技術的には維持するが、実運用上の位置づけが変わる | 本ファイル §11 |
| D-21 | 実績・完成数（ロットの完成判定・仕掛数・残数）に関わる新しい判定ロジックを実装する際は、必ず`calculate_lot_completion()`（ファイルNo・面単位で複数kitting_list_noの実績を合算する正しいロジック）を経由すること。独自に`order_qty`/`actual_qty`等の行単位比較を実装してはならない。同種のロジック不一致（`calculate_lot_completion()`を経由しない独自ロジックが実績と食い違う）が3箇所で繰り返し発見されたことを踏まえた原則 | 本ファイル §12 |
| D-22 | キッティングNo未確定行の保留・確定マージ（`_merge_from_pending()`）は、識別キー（`lot_no`, `setup_file_no`, `production_side`, `order_qty`）の一致だけでは実データ上一意性が保証されない（同一識別キーで`board_name`が異なる別々の正規計画が実在する）ため、**`board_name`（正規化済み）も一致することを確認してからマージすること**。識別キーは一致するが`board_name`が異なる場合は別の新規計画として扱う | 本ファイル §13 |
| D-23 | CSV取込機能（実績CSV取込・キッティング計画CSV取込）には、想定と異なるフォーマットのファイルが誤って読み込まれることを検知する仕組み（自フォーマット固有列の欠如検知＋他フォーマット固有列の混入検知、両方）を用意すること。警告は強制ブロックではなく確認ダイアログとし、ユーザー判断で続行できるようにする。2種類のCSVフォーマットの取り違え事故が実際に複数回発生したことを踏まえた原則 | 本ファイル §14 |
| D-24 | 構成基板数チェックは、日報・月報（実績の記録）から独立した「ロット進捗チェック」機能として実装する（起点を「実績のある行」から「現在アクティブな全ロット」へ広げる）。**2026-09-28追記：`board_count`列の意味に関する疑義は解消済み（D-25参照）。`check_lot_progress()`は実装完了、UI画面（`ui/lot_progress_window.py`）も実装済み** | 本ファイル §15、`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17 |
| D-25 | 構成基板数（`board_count`）は**ロット単位**の値であり、「そのロットが何枚の基板で構成されるか」を表す（board_name単位の値ではない）。構成基板数との比較はロット内の全board_nameを通したdistinctファイルNo数（面1省略後）の合計と行うこと。マスタ未登録の判定は引き続きboard_name単位（D-24の欠陥修正と混同しないこと、粒度が異なる） | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17.1・§17.2 |
| D-26 | ロットの構成基板数チェック結果に基づく引落確定ルール：不足（shortfall）のロットのみ引落を0とする（構成が揃っていないため）。マスタ未登録・構成基板数の食い違い・超過は、引落を実績ベース（`calculate_lot_completion()`）のまま計算し、警告で登録の修正を求める（判定不能、または自動補正すべきでないため）。`services/production_service.py::DRAWDOWN_ZERO_STATUSES = {"shortfall"}`で管理し、`_evaluate_lot_status()`に一本化して日報・月報・仕掛数量抽出・ロット進捗チェックの4機能で共有する | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17.4 |
| D-27 | 仕掛展開画面（`ui/wip_expansion_window.py`）の一覧は、`wip_board_snapshot`（スナップショット）単独ではなく「スナップショット ∪ 確定登録済み（`wip_scrap_records`）」の和集合とする。確定登録後にスナップショットから消えたロットも「確定済み(スナップショットなし)」として一覧に残し、既存の「実績修正」ボタンから訂正できるようにする（NG側の`ng_declarations` ∪ `scrap_records`という既存の和集合方式（本ファイル参照時は`ui/ng_input_window.py::_fetch_ng_list_rows()`）と揃える設計）。一括展開・登録、`expand_by_identity()`、`services/unprocessed_check_service.py::check_unprocessed_items()`の対象には含めない | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §18 |
| D-28 | `production_daily`の実績を上書き登録する際、`report_date`が呼び出し元から明示的に渡されなかった場合は、削除対象となる既存行の`report_date`をそのまま引き継ぐ（実行日「今日」には書き換えない）。「実際に生産した日」と「修正した日」は別の情報であるべき、というユーザー決定の方針による。既存行自体のreport_dateが空（NULL・空文字列）だった場合、および該当する既存行が無い場合（新規登録）は、今日にフォールバックする | `UI_WORKFLOW_FIXES_NOTES.md` グループAB追記8・追記9 |
| D-29 | 「日々の引落（前日比の増分）」は、`production_daily.report_date`を使った`report_date <= 指定日`の逆算では求めない。`production_daily`は「1計画1行、常に上書き」の設計（D-6）のため、後からの修正・遅延登録でreport_dateが変わると過去の累計が事後的に変わりうる限界が調査で判明したため。代わりに、`models/lot_status_history.py`（新規）に、実績の登録・修正のたびに「その時点のロット状態」（`_evaluate_lot_status()`の結果、ファイルNo単位の内訳を含む）をイベントログとして記録し、「指定日以前で最新の記録」と「前日以前で最新の記録」の差分を取ることで日々の引落を算出する方式を採用した。`recorded_at`は実際に書き込まれた壁時計時刻であり、`report_date`と異なり後から遡って変わらない | 本ファイル §17 |
| D-30 | 実績の登録・修正のたびに行う`lot_status_history`への記録は、`models/operation_log.py`（操作履歴機能）と同じパターンで、共通フックを1か所に一元化せず、実際に書き込みが起きる5箇所（`register_daily_result()`・`overwrite_daily_result()`・`update_daily_result()`・`delete_daily_result()`・`db_migration_carryover.py`）に個別に呼び出しを追加する。記録失敗時は、本来の登録・修正処理自体は失敗させない（ログに残すのみ）。Shift+S一括登録のような多数行の連続処理では、行ごとに記録すると（1回あたり数十ms、主にSQLiteのcommit同期コスト）件数に比例して遅延するため、`record_history`引数で個別記録を抑制し、バッチの最後に影響を受けたdistinctなlot_noだけ1回ずつ記録する方式に変更した | 本ファイル §17 |
| D-31 | 「未完了一覧」は、`lot_status_history`の最新記録からではなく、既存の`check_lot_progress()`（現在の全アクティブロットを都度再計算）を使う。`lot_status_history`は実績の登録・修正が実際に起きたロットにしか記録が無いため、一度も実績登録が無いロット（最も「未完了」らしいロット）が検知されず、D-24で修正した欠陥と同種の見落としを再現してしまうため | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §20 |
| D-32 | `ui/kitting_production_entry.py::_perform_registration()`内で、`search_plan()`呼び出し後に`_setup_ng_side_ui()`・`_load_current_daily_qty()`をもう一度呼んでいる処理は、削除しない。一見重複に見えるが、両者の間にNG申告の保存等、実際のDB書き込みが挟まっており、削除すると登録後のNG入力欄に保存した値が反映されなくなることを実データで確認した | `UI_WORKFLOW_FIXES_NOTES.md` §4 |
| D-33 | 日報・月報は`ui/unified_report_window.py::UnifiedReportWindow`に統合する（旧`DailyReportWindow`・`MonthlyReportWindow`は削除）。一方、ロット進捗チェック（`check_lot_progress()`、現在の全アクティブロットが母集団）・日々の引落一覧（`lot_status_history`、過去の時点を振り返る）は、日報・月報（`production_daily`の期間集計）とは母集団・時間軸の性質が異なるため、統合せず独立した画面として維持する。「実績のあるロット全体を土台にした全4画面統合」は検討の末、見送った | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §21 |
| D-34 | 列ヘッダーソートで「ロット単位のブロックを保つ」実装（`ui.lot_progress_window.py::sort_by_column()`由来）を、行データがロット単位で連続して並ぶ保証のない画面（`ui/unified_report_window.py`）に適用する場合、「現在の並びで連続している区間をブロックとする」方式では同一ロットの行が分断される。`services.production_service._build_report_rows()`は実データ行をproduction_dailyのレコード順（ロット単位にグルーピングされない）で構築し、「未確定」仮想行は全ロット分をまとめて末尾に追加するため、この前提が成立しない（実データで、同一ロットの行が922行中7番目と842・843番目に分断される実例を確認）。この場合は「lot_no値が一致する行を、物理的な位置に関わらず全て1つのブロックに集める」辞書ベースの方式に変更すること | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §21 |
| D-35 | `UnifiedReportWindow`の「登録日」列（`production_daily.report_date`）は、`_build_report_rows()`の各行が元々1つの`production_daily`レコード（＝1つの`kitting_list_no`）に1:1対応しているため、`build_wip_extraction_rows()`の`_pick_representative_plan_item()`のような複数バッチの代表選定ロジックは不要。「未確定」仮想行は元レコードが無いため空欄（`None`）とする | 本ファイル §18.1 |
| D-36 | `list_incomplete_lots()`（DB間引き継ぎの未完了判定）は、構成基板数チェックを含まない`_compute_lot_completion()`直接呼び出しから、`_evaluate_lot_status()`経由の判定に統一する。構成基板数不足（shortfall）のロットは、`remaining_quantity`の値に関わらず無条件で未完了とみなす（引落が0扱いのため）。未着手（旧DB側に実績が1件も無い）計画は、`plan_start_datetime`が引継ぎ日から50日以内のものだけを引き継ぎ対象とする（不要な将来計画の無制限な蓄積を防ぐため）。`plan_start_datetime`が空・パース不能な未着手計画は、安全側（除外せず含める）で扱う。「データを自動的に失わない」という本アプリ全体の既存方針（D-11等）に揃えた判断 | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §22 |
| D-37 | 未完了ロットのDB間引き継ぎは、完了済みファイルNoも含めたロット全体を単位として行う（ファイルNo単位で個別に完了判定して間引かない）。調査の結果、`_fetch_plan_items_for_lot()`・`_fetch_production_daily_for_lot()`は元々lot_no単位で無条件に全件取得する実装になっており、**この要件は既存実装で既に満たされていた**（コード変更は不要、確認のみ） | `PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §22.2 |
| D-38 | `board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`の5テーブルは、月次DB切り替えの対象である`config.DB_PATH`に、月次データ（`kitting_plan_items`・`production_daily`等）と無区別に同居している。月次DBを新規作成するたびに、これら共通マスタも空の状態から始まる設計上の問題が、未完了計画引き継ぎ機能の検証（構成基板数マスタが新DBにコピーされないため、引き継ぎ直後に再評価すると構成基板数マスタが空になりunregistered扱いになる実例）で発見された。この同居は最初のコミット時点からの設計であり、途中の変更で崩れたものではない。`bom_master`は`data_ym`列を持つ設計で月次の概念と矛盾せず、共有フォルダのTSVから再計算可能なキャッシュであるため、分離対象には含めない（月次データ側に残す）。**2026-10-02追記：分離自体はコードとして実装済み（D-42参照）。ただし`inventory.db`側に残る既存データ（`board_structure_master`3119件等）の`master.db`への移行は別課題として未解決のまま残っている** | 本ファイル §18.3・§18.6（D-42） |
| D-39 | レガシーテーブル14個（`production_records`・`lots`・`usage_daily`・`incoming_goods_log`・`stock_manual_adjustment`・`closing_runs`・`closing_wip_adjustment`・`audit_log`・`snapshot_batches`・`stock_snapshot`・`physical_count`・`board_definitions`・`component_bom`・`component_groups`）は、実DB行数0件・現行コードから一切参照されていないことを確認した上で2026-10-01に削除した。`production_daily.lot_id REFERENCES lots(lot_id)`という外部キー宣言が存在したが、アプリの通常接続（`models/db_common.py::get_connection()`）は`PRAGMA foreign_keys`を設定しておらず実際には強制されておらず、かつ`lots`テーブル自体に現行コードからのINSERTが無いため、この宣言は実質的に満たされ得ないものだった。`parts`・`final_products`は削除せず現状維持（D-38の分離検討対象） | 本ファイル §18.4 |
| D-40 | 検証スクリプトから`ui.main_window.MainWindow`を直接インスタンス化した場合、終了時は`mw.destroy()`を直接呼ぶのではなく、`WM_DELETE_WINDOW`プロトコルハンドラ（`_on_app_close()`）を経由する正規の終了経路を使うこと。`destroy()`はロック解放処理（`release_lock()`）をバイパスするため、実DBに対する検証では取得したロックが解放されないまま残留する（D-15で扱った「`APP_DATA_DIR`隔離漏れ」とは別の、`MainWindow`検証固有の落とし穴の2件目） | 本ファイル §8 |
| D-41 | Windows環境のターミナルでウィンドウタイトル等の日本語文字列を確認する際、文字コード不一致による文字化けした出力をそのまま目視で読み取って断定的な報告をしてはならない。`GetWindowTextW`等、Unicode文字列を文字化けさせずに取得できる手段で再確認してから報告すること。D-18（推測で断定しない）の原則を、「自分自身の検証ツールの出力」にも拡張した教訓 | 本ファイル §9 |
| D-42 | マスタDB分離（D-38）は、2026-10-02時点で**コードとしては実装済み**（`config.MASTER_DB_PATH`・`models/db_common.py::get_master_connection()`・`board_structure_master.py`/`parts_attributes.py`/`workers.py`/`master.py`の4モジュールの接続先切り替え・`tests/conftest.py`によるテスト隔離）。ただし`inventory.db`側に残る既存データ（`board_structure_master`3119件・`workers`1件を含む5テーブル）の`master.db`への**移行は未完了**であり、これは「移行するか、初期化して新規に再構築するか」という別の業務判断を要する未解決の課題として残っている。コード実装の完了と、データ移行の完了は別の事柄であることに注意 | 本ファイル §18.3・§18.6 |
| D-43 | 複数拠点・複数セッションでの並行作業では、一方で実装が先行し、記録（本ファイル等のノート）への反映が後追いになることで、**記録と実際のコードの状態が一時的に食い違う**状態が起こりうる（マスタDB分離がD-38で「未着手」と記録された後、別セッションで実装が完了していたにもかかわらず、記録側がそれに気づかないまま「未着手」の記述を維持していた事例、本ファイル§18.6参照）。本ファイル§5「環境間の整合性チェック手順」の定期的な実施（特に、記録上「未着手」「保留」としている項目が、実は既に別の場所で着手・完了していないかの確認）が、巻き戻り検知だけでなく、こうした記録の後追い状態の検知にも有効である | 本ファイル §18.6 |
| D-44 | 手動バックアップ（`services/backup_service.py::backup_databases()`）は、`shutil.copy2()`等の単純なファイルコピーではなく`sqlite3.Connection.backup()`（SQLite公式のオンラインバックアップAPI）を採用する。書き込み中のDBファイルをコピーしても整合性を保って完了できるため。`config.DB_PATH`（月次DB）・`config.MASTER_DB_PATH`（マスタDB）は、マスタDB分離（D-38）後は両方揃って初めて完全な状態になるため、同一タイムスタンプで1回の操作としてまとめてバックアップする。ファイル名重複時は`_1`・`_2`...と連番を付与し、バックアップ対象のDBファイルがまだ存在しない場合（例：`master.db`未作成）はそのDBのみスキップし、空のDBを誤って作成しない | 本ファイル §19.1 |
| D-45 | ~~バックアップファイル（`inventory_backup_<timestamp>.db`等）は、既存の「共有フォルダのDBを開く」機能（`on_open_shared_database()`）でそのまま開いて本番DBとして使い始められることを実証した。この機能・ロック機構（`services/db_lock_service.py`）はいずれもファイル名・パス文字列のみに依存する設計であり、バックアップファイルを特別扱いする新規機能は不要と判断した。複数PCでの同時アクセスという実機・実ネットワーク環境での検証はできていない~~ → **2026-10-05、D-50により改訂**。この方針には3つの問題があった：①バックアップ原本そのものがそのまま本番DBになってしまう（コピーではなく原本を直接使い続けることになる）、②マスタDB（`master.db`）は復元されない（この機能が切り替えるのは`config.DB_PATH`のみ）、③D-20（共有フォルダ直接アクセスからローカル+バックアップ方式への転換）の方針と整合しない（「共有フォルダのDBを開く」機能自体を前提にしているため）。これらを解消するため、「バックアップの呼び出し」機能（新しいローカルフォルダへ`sqlite3.Connection.backup()`でコピーしてから切り替える、原本・元のDBとも不変）に置き換えた | 本ファイル §19.2・§20 |
| D-46 | ログイン画面にパスワード認証が無いことに加え、作業者管理画面（`WorkerManagementWindow`）自体がログイン不要で誰でも開け、`role`（admin/operator）列が存在するにもかかわらずアクセス制御の目的ではどこからも参照されず実質的に無意味だった、というセキュリティ上の空白が判明した。対策として、作業者管理機能を「登録画面」（`ui/worker_registration_window.py::WorkerRegistrationWindow`、常時誰でも開けるが、`models/workers.py::any_admin_exists()`がTrueの場合はadmin選択肢自体を提示しない）と「管理画面」（既存`WorkerManagementWindow`を再編、既存作業者の編集・有効/無効切替専任、admin役割でログイン中の場合のみメインメニューに表示）に分割した。編集・切替の実行自体にも`_require_admin()`による関数レベルのチェックを残し、画面を直接インスタンス化してメニューの表示制御を迂回しても拒否される（ボタンのグレーアウトという見た目の対策と合わせた二重の防御） | 本ファイル §19.3 |
| D-47 | 複数PC間でadmin体制・マスタデータを揃える方法として、「現在のデータを正とし、バックアップ側は不足分のみ追加する」方針を採用する（上書き・完全マージは選ばない）。`services/master_merge_service.py::merge_master_from_backup()`が、5テーブル（`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`）それぞれについて、バックアップ側の主キーが現在の`master.db`に存在しない行だけを、既存のupsert関数を再利用して追加する。`workers`の取り込みでは`role`をバックアップ側の値のまま追加する（既存のadmin・operatorの値には一切影響しない）。この取り込み操作もD-46と同じ基準でadmin限定とした | 本ファイル §19.4 |
| D-48 | .exe化は`--onefile`形式を採用する。`config.py`のBASE_DIR算出部のコメントが最初からonefile形式（`sys.executable`基準）を前提に書かれていたため、既存設計との整合性を優先した。Tesseract OCR・Poppler本体は同梱しない方針を確定した（PDF OCR機能はまだ十分な精度・再現性が得られていないため）。PDF読み取り機能のコード自体は通常通り同梱され、OCRが必要な操作時にTesseractが見つからない場合はエラーダイアログを表示するのみでアプリ全体はクラッシュしない設計であることをコードで確認した | 本ファイル §19.5 |
| D-49 | .exe化の動作確認中、「windowedビルド（`console=False`）が即座に無言で終了する」という事象を一度観測し、`sys.stdout`/`sys.stderr`が`None`になることが原因という仮説のもと`main.py`に防御的な修正を加えたが、後の再検証で、実際の原因は検証スクリプト自身の起動方法（`cmd.exe`経由の`start /B`）の問題であり、ビルド自体に不具合は無かったことが判明した。D-41（文字化けした出力を根拠に断定しない）と同種の「自分の検証結果を疑うべき」教訓が、.exe化の文脈でも再現した事例。修正自体は無害（既知のPyInstaller windowed時の危険への一般的な予防策）のため、コードには残している | 本ファイル §19.6 |
| D-50 | メインメニューを整理した（2026-10-05）。(1) 共有フォルダ3ボタン（共有フォルダのDB一覧／共有フォルダのDBを開く／共有フォルダに新規作成）・`ui/shared_db_list_window.py`・`services/shared_db_scan_service.py`・`services/app_settings_service.py::save_shared_db_root()`/`load_shared_db_root()`を削除し、代わりに「バックアップの呼び出し」ボタン（権限制限なし）を新設した。「共有フォルダのDBを開く」機能をバックアップの復元に転用する旧方針（D-45）が持っていた3つの問題（原本がそのまま本番DBになる・マスタDBが復元されない・D-20の方針と不整合）を解消するため、新しいローカルフォルダへ`sqlite3.Connection.backup()`でコピーしてから切り替える方式（`services/backup_service.py::validate_monthly_db_backup()`で月次DBかどうかをテーブル構成で判定し、マスタDBのバックアップ・SQLiteでないファイルを拒否した上で、`restore_backup_as_new_local_db()`でコピーする。原本・元のDBとも不変。判定方式自体は後日D-53で見直した）に置き換えた。マスタDBはこの機能の対象外のまま（必要な場合は既存の「マスタデータを他PCから取り込む」、D-47を使う）。(2) 「4.マスターデータ管理」（`ui/master_management.py`）・「5.マスターインポート」（`ui/master_import_window.py`・`services/master_import_service.py`）を削除した（`parts`・`final_products`を読む機能がこの2画面以外に存在しないことを確認した上で削除、D-4参照）。テーブル自体・`models/master.py`の一部関数（`upsert_part`/`upsert_product`/`init_master_tables`、`services/master_merge_service.py`の「不足分のみ取り込み」が使用）は残置した。(3) 「PDF読み取り」「操作履歴」を月次データ列（右列）から共通マスタ列（左列）の「ツール」見出し配下へ番号なしで移動した。依存先DB（`config.DB_PATH`、月次DB）は変わらないため、配置は使用頻度の低い補助機能をまとめる目的のレイアウト上の都合であり、依存DBによる列分けの厳密な例外であることをコード上のコメントに明記した | 本ファイル §20 |
| D-51 | `db/schema.sql`（`init_database_at()`が新規の月次DBを作成する際に使う定義）に、マスタDB分離（D-38・D-42）以前の名残である`workers`テーブルの`CREATE TABLE IF NOT EXISTS`定義が今も残っていることが、D-50の検証中に判明した。新規作成した月次DBにはこの未使用の`workers`テーブルが物理的に作成されてしまうため、「`workers`テーブルの有無」を月次DBかマスタDBかの判定材料に使うと誤判定する（実際に検証中、正しい月次DBバックアップがマスタDBと誤判定される事象を確認した）。`schema.sql`自体には`parts`・`final_products`・`lots`（D-39で実DBからは削除済み）の定義も同様に残っており、新規作成した月次DBには引き続きこれらの空テーブルが作られる。`schema.sql`の整理自体は本タスクのスコープ外のため見送り、`services/backup_service.py`の月次DB／マスタDB判定では`board_structure_master`・`parts_attributes`（`schema.sql`に定義が無く、マスタDB分離後は`master.db`側にしか作られない）のみをマスタDBの目印として使うことで回避した。`schema.sql`自体の整理（不要な`CREATE TABLE`定義の削除）は未着手のまま残っている | 本ファイル §20 |
| D-52 | モジュール削除時、「ある関数の呼び出し元」だけをgrepして安全と判断すると、そのモジュール内の**別の関数**が実は他から再利用されている場合を見落とすことがある（`services/master_import_service.py`の削除検討時、`import_parts_csv()`の呼び出し元のみを確認し、同じファイル内の汎用関数`parse_csv_generic()`が`services/production_import_service.py`からも再利用されていることを見落としかけた）。以降、モジュール削除前の安全確認は、特定の関数名ではなく**モジュール名そのもの**でリポジトリ全体をgrepすることを原則とする（テスト・PyInstallerの`.spec`・ドキュメント内のコード例も対象に含める）。再利用されていた汎用部分（`parse_csv_generic`・`_open_csv_with_fallback`・`_resolve_column_map`・`_ENCODINGS_TO_TRY`）は内容を変更せず`services/csv_parsing_common.py`（新規）へ切り出し、`production_import_service.py`の参照先を差し替えた上で`master_import_service.py`を削除した | 本ファイル §20 |
| D-53 | `validate_monthly_db_backup()`（D-50で新設）の判定方式を、「マスタDB固有テーブル（`board_structure_master`・`parts_attributes`等）があれば拒否」という否定的な判定から、「月次DB固有テーブル（`kitting_plan_items`）があれば受理」という肯定的な判定に変更した（2026-10-05）。マスタDB分離（D-38・D-42）より前（2026-10-02より前）に作成された月次DBには、当時の`db/schema.sql`・各モジュールの接続先が`get_connection()`（月次DB）だったため、`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`の5テーブルが物理的に同居している。この同居は分離前の月次DBとして正常な状態だが、旧判定方式ではこれを「マスタDBのバックアップ」と誤って拒否してしまうことを、実在する分離直前の実バックアップファイル（`inventory_app/db/inventory.db.bak_before_master_tables_removal_20261002_084718`、読み取りのみで確認）を使った検証で確認した。`kitting_plan_items`は`db/schema.sql`には定義されず`models.kitting_plan.init_kitting_plan_tables()`（月次DB経由）でのみ作成されるため、分離の前後を問わず月次DBには常に存在し、`master.db`には存在しない、信頼できる肯定的な目印として使える。マスタDB固有テーブルの存在は、拒否条件としてではなく「マスタDBのバックアップのようです」という具体的な拒否理由を出すためのヒントとしてのみ残した | 本ファイル §20.8 |
| D-54 | 全画面（`tk.Toplevel`/`tk.Tk`を生成する29クラス＋関数内ダイアログ11か所）に中央寄せを適用した（2026-10-05）。共通ヘルパー`ui/window_utils.py::center_window(window, parent=None)`を新設し、各画面の`__init__`（または関数内の生成箇所）の最後に1行追加するだけで適用できるようにした。`parent`が無い・現在表示されていない（`withdrawn`/`iconic`）場合はディスプレイ中央、それ以外は`parent`の中央に重ね、画面外にはみ出す場合は画面内に収まるよう補正する。サイズ自体は変更しない：`geometry("WxH")`で明示的にサイズ指定済みの画面はその幅・高さを使い、サイズ未指定（内容に応じた自動サイズ）の画面も、`update_idletasks()`後の`winfo_width()`/`winfo_height()`がどちらの場合も正しい値を返すことを実機確認した上で、分岐せず同じ経路で扱っている。ちらつき防止のため、`center_window()`内部で`withdraw()`→位置決定→`deiconify()`を行う（呼び出し元のコードは変更不要）。既存の`_open_singleton_window()`による再表示（既に開いている画面を前面に出すだけの動作）は、`center_window()`が`__init__`内で1回しか呼ばれない設計のため、位置が動かないことを確認済み | 本ファイル §21 |
| D-55 | D-54の実装着手前の調査で、前回調査（メインメニュー整理時）の「全画面が固定サイズの`geometry("WxH")`を持つ」という前提が誤りだったことが判明した。関数内ダイアログ11か所のうち4か所（`ui/ng_input_window.py`の「対象外にする理由」・「実装ラインの選択」、`ui/kitting_production_entry.py`の「登録内容の確認」、`ui/wip_expansion_window.py`の「対象外にする理由」）は、`geometry()`呼び出しを持たず、パックしたウィジェットの内容に応じた自動サイズのままだった。これらは`center_window()`側の分岐（上記D-54参照）で違和感なく対応できたため、サイズ自体の変更（新たに`geometry("WxH")`を追加する等）は行わず、自動サイズの挙動を維持したまま中央寄せのみ適用した | 本ファイル §21 |
| D-56 | 既定DB（`config.APP_DATA_DIR/db/inventory.db`、`on_switch_database()`等が組み立てるフォルダ名付きパスより1階層浅い、モジュール読み込み時点のデフォルトパス）を、ファイルの有無・データの有無を問わず一律「未選択」として扱い、業務操作（月次データ・共通マスタ・ツール・バックアップ・マスタ取込の各ボタン、「前月から引き継ぐ」チェック）を無効化する方針を確定した（2026-10-05）。**理由**：名前の付いていない既定DBに、利用者が気づかないまま入力・CSV取込を行ってしまうリスクがあったため（どの月の作業か特定できない暗黙のDBが実質的な作業領域になってしまう）。判定は`config.is_default_db()`（`os.path.basename(os.path.dirname(DB_PATH)) == "db"`というパスの深さのみで判定、追加の状態保持は不要）に集約し、`ui/main_window.py::_apply_widget_states()`が、既存の`_set_menu_enabled()`（`carry_over_incomplete_lots()`実行中の一括無効化）とは独立したゲート（`_default_db_locked_widgets`）として合成する。2つのゲートはいずれも「Trueなら制限なし」側で持ち、ANDで合成するため、一方の解除が他方の無効状態を誤って外すことがない。既定DBというフォルダ名（`config.DB_ROOT_FOLDER_NAME = "db"`）は予約語とし、新規作成・バックアップの呼び出しの両方でこの名前のフォルダ作成を拒否する | 本ファイル §21 |
| D-57 | D-56の実装中、既定DBに対して一切のファイル読み書きを行わないという前提を破る実装上の見落としを発見・修正した：在庫値出力済み判定（`models.operation_log.get_inventory_diff_export_status()`）が内部で`init_operation_log_table()`（`CREATE TABLE IF NOT EXISTS`）を呼ぶため、`sqlite3.connect()`が存在しない既定DBファイルを新規作成してしまっていた。隔離環境での検証で、空の環境で起動しただけで既定DBファイルが作られてしまう事象・実データ入りの既定DBファイルのハッシュが起動〜終了で変化してしまう事象の両方を実際に確認した。`ui/main_window.py::_update_current_db_label()`・`_confirm_proceed_despite_exported_db()`の両方に「既定DBの間は在庫値出力済み判定自体を呼ばない」ガードを追加して解消した。**教訓**：「既定DBは読み書きしない」という方針は、既定DB判定（D-56）そのものの実装だけでなく、既定DBの状態に応じて分岐する機能（在庫値出力済み判定を含む）全てに同じ注意が必要になる横断的な制約であり、機能を1つ追加するたびに見落としがちである | 本ファイル §21 |
| D-58 | 在庫値出力（`ui/inventory_diff_window.py::InventoryDiffWindow`の「7. 在庫値出力」）の完了を`operation_log`に記録し（`OPERATION_NAME_INVENTORY_DIFF_EXPORT = "在庫値出力"`、`on_export_pdf()`/`on_export_csv()`の成功時のみ。保存ダイアログのキャンセル・出力失敗時は記録しない）、出力済みのDBを開いている間はメインメニューに目立つ警告（最終出力日時付き）を常時表示し、月次データ1〜6の画面を開く際には確認ダイアログ（「いいえ」なら開かない）を表示する方針を確定した（2026-10-05）。**禁止ではなく警告とした理由**：出力後に誤りが見つかった場合の修正・再出力を妨げないため（在庫差異レポート自体は読み取り専用・何度でも再実行可能な設計であり、D-4未満の過去の決定とも整合する）。警告の対象外：在庫値出力自体・日報月報等の閲覧系・ツール・共通マスタ・DB管理（いずれも月次データへの新規入力を伴わない、または出力完了判定そのものに関わるため）。`operation_log`テーブルが存在しない古いDB（分離前の月次DBをバックアップの呼び出しで取り込んだ場合等）でも、`get_latest_operation_timestamp()`が`init_operation_log_table()`を呼ぶため例外にならないことを確認済み。**前月引き継ぎ**（`carry_over_incomplete_lots()`）は`operation_log`テーブル自体をコピー対象に含めていないため、引き継ぎ先の新DBは出力済みの記録を持ち越さない（未出力の状態で始まる）。**バックアップの呼び出し**（`restore_backup_as_new_local_db()`）は`sqlite3.Connection.backup()`によるファイル全体のページ単位コピーのため、`operation_log`を含め元のDBの出力済み状態がそのまま保持される。いずれも隔離環境で実際に確認済み。本機能より前に出力されたDBは、この記録の仕組み自体が無かったため「出力済み」と判定されない（既知の限界、記録開始時点からの運用で解消する） | 本ファイル §21 |
| D-59 | 既定DB（未選択、D-56）の間は、DBファイル本体だけでなく**ロックファイル（`<DBファイル名>.lock`、`services/db_lock_service.py`）にも一切触れない**方針に拡張した（2026-10-05）。D-56・D-57策定時点では「既定DBに触れない」という方針がDB本体（`sqlite3.connect()`経由のアクセス）のみを対象にしており、ロックファイル（JSON、DB本体とは別ファイル）は対象に含めていなかった。`ui/main_window.py::MainWindow.__init__()`は既定DBであってもなくても無条件に`acquire_lock()`を呼んでいたため、既定DBパスに他者の有効なロックが残っていると「未選択」の状態にすら到達できず起動自体が拒否される、という本来の趣旨（名前の無いDBへの気づかない入力を防ぐだけで、起動そのものは妨げないはずだった）に反する動作になっていた。**理由**：①既定DBに触れない方針との整合、②残留ロックにより「未選択」の状態にすら入れず起動不能になる事態を避けるため。`__init__()`に`if not config.is_default_db():`の分岐を追加し、既定DBの間は`acquire_lock()`自体を呼ばないようにした。既存の`self._lock_acquired`（ロック保持の有無をMainWindow自身が明示的に保持するブール値、D-40参照）が、ハートビート（`_heartbeat()`）・解放（`_release_current_lock()`）双方の呼び出しを既にこのフラグでガードする設計だったため、`__init__()`側の1箇所の変更だけで、ハートビート・終了・ログアウト・切り替え・新規作成・引き継ぎの全経路が「ロック未保持なら何もしない」という振る舞いに安全に追随した（既存コードの事前確認で、ロック保持を前提にした安全に扱えない箇所は見つからなかった） | 本ファイル §22 |
| D-60 | `inventory_app/db/inventory.db.lock`が、2026-09-04〜10-03の約1か月間にAdd→Delete→Add→Delete…を繰り返すパターンで合計9回コミットに混入していたことが判明した（2026-10-05）。`.gitignore`の`*.db`パターンは拡張子が`.lock`のため一致せず、このファイル（DBロック、実行時に作成・削除される runtime ファイル）を除外できていなかった。`.gitignore`に`*.db.lock`を追加し、`git rm --cached`で追跡対象から外した（ファイル自体は削除していない）。本セッション中に実環境の該当ファイルが一度削除される事象があったが、原因は特定できなかった（検証に使った4本のスクリプトを全て読み直し、config3パスの差し替えが常にMainWindow生成・ロック関連関数呼び出しより前に行われていることを確認済みで、隔離漏れは見つからなかった）。該当ロックは3日前（2026-10-02）の古い記録であり、`LOCK_STALE_SECONDS`（30分）を大幅に超過していたため、通常の起動・終了操作でも自然に上書き・削除され得る状態だった。`db/`配下には同様に`.gitignore`で除外できていない`.bak_*`バックアップファイルが他に6件存在するが、これらの扱いは本タスクでは変更していない（別タスク） | 本ファイル §22 |
| D-61 | 画面が画面中央より若干下にずれて表示される（D-54）という報告を受けて調査し、`ui/window_utils.py::center_window()`を書き直した（2026-10-05）。原因は2つ：①`winfo_width/height()`・`winfo_rootx/rooty()`はタイトルバー・枠を除いた「クライアント領域」基準の値を返すのに対し、`geometry("WxH+X+Y")`の`+X+Y`は枠を含む「外枠」の位置を指定する仕様で、旧実装はクライアント領域基準の値をそのまま外枠位置に渡していたため、タイトルバーの高さ（実測約31px）・左右の枠（実測約8px）分だけ右下にずれていた。②`winfo_screenwidth/height()`はタスクバーを含むディスプレイ全体の解像度であり、作業領域ではなかった。対策として、追加の外部ライブラリを使わずctypes経由でWindows APIを直接呼び、`GetWindowRect`でウィンドウの実際の外枠サイズ、`MonitorFromWindow`+`GetMonitorInfoW`の`rcWork`でタスクバーを除いた作業領域を取得し、これらを基準に中央寄せの計算をやり直す設計に変更した。Windows以外の環境・API呼び出し失敗時は従来相当の近似値にフォールバックし、例外は発生させない。合わせて配置基準も「parentの中央」から「parent（省略時はwindow自身）が乗っているモニターの作業領域の中央」に変更し、メインメニューを移動していても開く画面の位置が変わらない（parentの位置そのものには依存しない）設計にした。本アプリはDPI非対応（DPI-unaware）のままで、Tkinter・ctypesのいずれも同じ「見せかけの」96 DPI座標系を使うため、表示倍率（125%・150%等）が変わっても単位変換は不要であることを実機確認済み | 本ファイル §23 |
| D-62 | 構成基板数マスター・基板丁数マスターの両取込画面に、縦スクロールバー（登録済み一覧のTreeview）・登録件数表示（構成基板数マスターは「うち構成基板数なし」件数も併記）を追加した（2026-10-05）。登録件数は専用のCOUNT関数（`models/board_structure_master.py::get_board_structure_count_summary()`・`models/parts_attributes.py::get_parts_attributes_count()`、新設）で取得し、画面を開いたとき・取込完了後の両方で更新する | 本ファイル §24.1 |
| D-63 | 両取込画面の取込処理を「即時反映」から「事前確認→確定」の2段階へ変更した（2026-10-05）。CSV解析・現在のテーブルとの差分計算（追加・更新・変更なし・削除、`compute_board_structure_sync_plan()`/`compute_parts_attributes_sync_plan()`、新設・読み取りのみ）を先に行い、内訳（削除がある場合は対象の先頭10件・CSV内の重複キーの扱い〈値が同じ重複は件数のみ、値が食い違う重複は該当行番号・各値・採用される値を明記〉を含む）を確認ダイアログで提示した上で、「はい」を選んだ場合のみ実際の反映に進む。「いいえ」の場合は一切DBへの書き込みを行わない | 本ファイル §24.2 |
| D-64 | 両取込画面の登録・更新・削除を、1行ごとに個別コミットしていた既存の`upsert_board_structure()`/`upsert_parts_attributes()`（`services/master_merge_service.py`等、他の呼び出し元がそのまま使い続けるため変更していない）とは別に、取込専用の一括関数（`apply_board_structure_sync()`/`apply_parts_attributes_sync()`、新設）を設け、1つのトランザクションにまとめて確定するよう変更した（2026-10-05）。途中で例外が発生した場合、`sqlite3.Connection`の標準コンテキストマネージャ仕様（例外時は自動ロールバック）により、呼び出し前の状態にそのまま戻ることを実機確認済み。副次効果として、3000件規模のCSVの取込時間が約34.7秒（旧実装、行ごとに個別コミット）から約0.09秒（新実装、1トランザクション）に短縮された。部品属性側のBOMキャッシュ無効化（`invalidate_bom_master_by_part_no()`）は、本トランザクションのコミットが成功した後にまとめて行う（bom_masterは月次DB側の別接続のテーブルのため、本来はアトミック性に影響しないが、指示された設計意図に合わせてコミット後に移動した） | 本ファイル §24.3 |
| D-65 | 両取込画面で、CSV解析中・取込確定中に発生した想定外の例外（従来は`ValueError`以外は利用者に表示されず、Tkinterの`after()`コールバック内で無言のまま再発生していた）を、エラーダイアログで利用者に通知するよう修正した（2026-10-05）。ダイアログには「データは取込前の状態のままです」と明記する（D-64の一括トランザクション化により、この文言が常に正しいことが保証されるようになった）。完了メッセージも、キー単位の追加/更新/変更なし/削除・登録されなかった行数（キー空欄）・値が読み取れず空で登録した件数（部品属性側にも新規追加）・取込後の登録件数、の内訳に整理し、ファイル形式についての注意文（notices）は行単位の警告件数（warnings）から分離した。警告が多数ある場合も全件を確認できるよう、専用のスクロール可能な一覧ウィンドウ（`ui/warnings_list_window.py::WarningsListWindow`、新設・両画面で共用）に分離表示する | 本ファイル §24.4 |
| D-66 | 構成基板数マスター・基板丁数マスターの両取込画面に、検索（部分一致・全角半角/大文字小文字を区別しない）・列ソート（昇順/降順の切替、見出しに▲▼記号表示）・構成基板数マスター限定の「構成基板数なしのみ表示」チェックボックスを追加した（2026-10-05）。表示専用の機能であり、DBから取得した全件（`self._all_rows`）を元にTreeview表示のみを絞り込み・並べ替える設計とし、取込処理・差分計算（`compute_..._sync_plan()`/`apply_..._sync()`）は常にDBへ直接アクセスするため、検索・ソートの状態に一切影響されないことをコード構造・実機の両方で確認した | 本ファイル §25.1〜§25.2 |
| D-67 | 列ソートにおいて、数値列（構成基板数・丁取り数・フル数量）は数値として比較し、文字列列は文字列として比較する。値が空のデータは、昇順・降順のいずれでも常に末尾にまとめる方式を採用した（2026-10-05）。既存の`ui/lot_progress_window.py`・`ui/unified_report_window.py`の`sort_by_column()`（数値変換失敗時に`float("-inf")`へフォールバックする方式）を調査したところ、この既存方式では降順ソート時に空値が先頭に来てしまい、今回の要件（常に末尾）を満たさないことが判明したため、意図的に別方式（値が有るデータ・無いデータを分離し、有るデータのみを昇順/降順でソートした後、無いデータを常に末尾に連結する）を採用した。他画面の既存実装は変更していない | 本ファイル §25.2 |
| D-68 | 両画面の登録件数表示に、絞り込み中の表示件数を併記する仕様を追加した（例：「表示: 25件 / 登録件数: 3119件」）。登録件数自体（テーブル全体の件数）は、検索・ソート機能追加前と同じ`get_board_structure_count_summary()`/`get_parts_attributes_count()`（DBへのCOUNT(*)クエリ、D-62で新設）で取得する方式を維持し、Treeviewの表示行数を数える方式には変更していない（画面を開く前の状態でも使える、絞り込み中でもテーブル全体の件数を正しく示せるようにするため）。取込完了後の一覧再読込（`load_board_structure()`/`load_parts_attributes()`）は、検索語・チェックボックス・ソート列・ソート方向をクリアせずそのまま再適用する設計に変更した | 本ファイル §25.3 |

---

## 3. 参照時の注意

本ファイルの内容と各ノートファイルの内容が食い違う場合は、**各ノートファイルに記載された調査日時・検証データを正**とし、本ファイルの要約を更新すること（本ファイルはあくまで索引であり、一次情報ではない）。

---

## 4. 未使用・レガシー資産の一覧（H）（2026-10-02更新、D-38〜D-39反映）

**本節は2026-10-01〜02のマスタDB分離・レガシーテーブル削除作業により、記述の前提が大きく変わっている。以下は現時点（このマシン）で確認できた最新の状態。**

### 削除済み（もう存在しない）

`lots`・`board_definitions`・`component_groups`・`component_bom`は、2026-10-01（D-39、本ファイル§18.4・`BOM_MIGRATION_NOTES.md` §14参照）に**既に削除済み**である。以前の本節は「削除は行わず、現状維持と決定している」としていたが、この決定はその後見直され、実DB行数0件・現行コードから一切参照されないことを確認した上でDROPされた。古い記述は誤りのため訂正する。

### 現役（第一世代設計の名残ではなく、現在も使われている）

`parts`（部品マスタ）・`final_products`（完成品マスタ）は、「現行のBOM基盤・キッティング計画・生産実績のいずれからも参照されない」という以前の本節の説明は、**誤解を招く表現だった**。正確には：

- BOM計算（`bom_master`・`parts_attributes`経由）・キッティング計画（`kitting_plan_items`）・生産実績（`production_daily`等）からは、確かに参照されない。
- しかし、`models/master.py`を経由して「4.マスターデータ管理」（`ui/master_management.py`、CRUD）・「11.マスタインポート」（`ui/master_import_window.py`・`services/master_import_service.py::import_parts_csv()`、`parts`へのCSV一括登録）という**独立した現役の画面から、現在も実際に読み書きされている**。「他画面からは参照されない」というのは「他の業務機能（BOM・生産実績等）からは参照されない」という意味であり、「誰からも参照されない」という意味ではなかった。この2つの異なる主張が同じ節に混在していたため、一見矛盾しているように読めていた。

2026-10-01〜02、`board_structure_master`・`parts_attributes`・`workers`とあわせてマスタDB分離の対象として特定され（D-38）、`parts`・`final_products`は削除せず現状維持（分離検討対象）と改めて確定している。

### 行数についての注記（環境依存の可能性、断定を避ける）

以前の本節は`parts`・`final_products`・`lots`の行数をいずれも「実データ1件（テストデータのみ）」としていたが、2026-10-01・10-02にこのマシンの実DB（`inventory.db`）を直接確認したところ、`parts`＝0件、`final_products`＝0件だった（`lots`は前述の通り削除済みのため確認不能）。この差が、過去の記録時点からこのマシン上でデータが変化したためなのか、本プロジェクトが前提とする2拠点並行開発（本ファイル§5参照）の**別の拠点の状態を指して書かれたものだったのか**は、このマシンからは判別できない。他拠点で作業する際は、念のため同様の行数確認を行うことを推奨する。

### 現在もコード上の定義自体が無い旧世代BOM由来のテーブル

上記の通り`board_definitions`/`component_groups`/`component_bom`は2026-10-01に削除済み。`BOM_MIGRATION_NOTES.md` §7・§14も参照。

---

## 5. 環境間の整合性チェック手順（重要・繰り返し発生している問題）

### 背景：calculate_lot_completion()の巻き戻りが2度発生している

本プロジェクトは2拠点並行開発を前提としており、`git`のマージ操作の際に、**片方の拠点で既に修正済みだった内容が、もう片方の拠点の古い状態で上書きされてしまう**という事故が、少なくとも2回発生している。

- **1回目**：`services/production_service.py::calculate_lot_completion()`の`file_actuals`キーが、3要素タプル`(setup_file_no, production_side, kitting_list_no)`から単一キー`setup_file_no`のみに巻き戻った状態が発見された（BOM_MIGRATION_NOTES.md §4・§5 #4に記録）。同時に`services/bom_service.py`・`services/bom_file_service.py`のBOM列名修正・`build_index()`再帰化・`resolve_file_no()`も同様に巻き戻っていた（同じマージ操作が原因と推測される）。
- **2回目**：1回目の修正・確認から時間を置いた別のタイミングで、`calculate_lot_completion()`が**再び**単一キー（`setup_file_no`のみ）に戻っている状態が発見され、再度3要素タプルキーに修正した。

**この巻き戻りは偶発的な一度きりの事故ではなく、同種のマージ作業のたびに再発するリスクがあるパターンとして扱うべきである。** 特に`calculate_lot_completion()`は「単一キーでも構文的には正しく動作してしまう」（例外を出さない、ただし計算結果が静かに誤る）ため、テストや起動確認だけでは検知できない点が危険性を高めている。

### 次回の環境立ち上げ・マージ時に必ず確認すべき項目

両拠点をマージ、または別環境（別PC・別ブランチ）からコードを持ち込んだ直後は、**必ず**以下を確認すること。

1. **`services/production_service.py::calculate_lot_completion()`のキーが3要素タプルか確認する**：
   ```python
   key = (item["setup_file_no"], item["production_side"], kitting_list_no)
   file_actuals[key] = ...
   ```
   のようになっているか（`file_actuals[file_no] = ...`のような単一キーに戻っていないか）を`grep`等で直接確認する。あわせて`ui/kitting_production_entry.py`の`lot_file_actuals`表示部分が、対応するタプル要素数（3要素）を正しく分解して表示しているかも確認する（`lot_surplus`表示はグループJ（UI_WORKFLOW_FIXES_NOTES.md）で廃止済みのため、2026-09-01時点で本項目の文言から削除した）。
2. **`services/bom_service.py`・`services/bom_file_service.py`のBOM列名定数**（`COL_SIDE="生産面"`・`COL_PART_NO="96コード"`・`COL_R_FLAG="減数種別"`）が、仮置きの値（`"先行面・後行面"`・`"部品番号"`・`"Rフラグ"`）に戻っていないか確認する。
3. **`BOMFileIndex.build_index()`がサブフォルダを再帰的に走査しているか**（`resolve_file_no()`・`problems`機構が存在するか）を確認する（BOM_MIGRATION_NOTES.md §2・§3参照）。
4. **`models/kitting_plan.py::list_plan_items_by_lot()`に`is_active=1`フィルタが含まれているか**を確認する。
5. **`bom_master`テーブルに`item_type`列が存在するか確認する**（`PRAGMA table_info(bom_master)`で直接確認する。基板消費枚数機能（BOM_MIGRATION_NOTES.md §13）のキャッシュ列で、`db/migration_013`で追加される。無ければ`migration_013`が未適用）。
6. **`production_daily.report_date`列にNOT NULL制約が存在するか確認する**（`PRAGMA table_info(production_daily)`で`report_date`行の`notnull`が`1`になっているかを直接確認する。2026-09-29、`models/production.py::replace_daily_result()`のreport_date引き継ぎロジック（D-28参照）がこの制約の存在を安全装置として前提にしているため、他方の拠点でこの制約が失われていないかを確認する意味で追加した項目。本項目自体は「巻き戻り」の検知が主目的ではなく、他拠点環境での前提確認のための申し送りとして追加した点が1〜5と異なる）。
7. 上記いずれかが巻き戻っていた場合は、**該当するノートファイル（BOM_MIGRATION_NOTES.md）の該当セクションを参照し、そこに記載された修正内容をそのまま再適用する**（調査をやり直す必要はない。過去に確定済みの内容であるため）。ただし、そもそも一度も適用されていなかった場合（巻き戻りではない）もあり得る点に注意（2026-09-01の実DB適用時、§6参照）。

**重要な留意点（`CREATE TABLE IF NOT EXISTS`パターンの運用上の注意）**：`models/bom_master.py::init_bom_master_table()`のように`CREATE TABLE IF NOT EXISTS`でテーブルを定義しているモジュールは、テーブルが既に存在する環境ではアプリ起動時に何度呼ばれても列定義が更新されない。そのため、`bom_master`のようなテーブルに新しいマイグレーション（`migration_011`・`migration_013`等）が追加された場合、**既存DBには自動適用されず、該当するマイグレーションスクリプトを明示的に実行しない限り古いスキーマのまま残り続ける**。上記チェック項目5（`item_type`列の存在確認）は、この一例にすぎない。同様に`CREATE TABLE IF NOT EXISTS`で定義されている他のテーブルについても、新しいマイグレーションを追加した際は既存DB（特に実DB）への適用を忘れずに行うこと（§6の実施記録、特に2026-09-03付の再発記録も参照）。

### 推奨する運用

- マージ作業を行う際は、上記5項目を機械的にチェックするチェックリストとして扱い、目視確認だけでなく可能であれば簡単なスクリプト（`grep`でのパターンマッチ等）で自動検知することを検討する。
- 本ファイル（CANONICAL_DESIGN_DECISIONS.md）とBOM_MIGRATION_NOTES.md等の各ノートは、マージのたびに「反映済み/未反映」の判定を更新し、巻き戻りが再発していないかをその都度確認する運用とする。

### 検証スクリプト自体の安全性（重要・importループチェックの範囲限定）

**発見の経緯（2026-09-14）**：コード変更後の検証手順として慣行にしていた「全パッケージimportループチェック」（`pkgutil.walk_packages()`で作業ディレクトリ配下の全`.py`ファイルを列挙し、`importlib.import_module()`でimportできるか確認する手法）が、実際には作業ディレクトリ直下に置かれた単発メンテナンス・調査用スクリプト（`check_failed_batch_references.py`・`check_kitting_plan_schema.py`・`delete_failed_batches.py`・`delete_test_batches.py`・`dump_schema.py`等）まで無差別にimportしてしまうことが判明した。これらのスクリプトはモジュールトップレベルに実処理（DB接続・SELECT・場合によってはDELETE）を書いた一回限りの調査用コードであり、importされるだけでその処理がそのまま実行される。実際に一度この手法を実行した際、`delete_failed_batches.py`・`delete_test_batches.py`を含む複数のスクリプトが実DB（`inventory_app/db/inventory.db`）に対して実行され、削除対象0件だったため実害は無かったが、**`delete_*`という名前のスクリプトが存在する以上、対象件数が0件だったのは結果論であり、たまたま実害が無かっただけというリスクだった**。

**確定した対応（D-13）**：以降、importループチェックは対象を`ui`/`models`/`services`/`config`の各パッケージ・モジュールに限定したスコープ付きで実行する（`pkgutil.walk_packages(['ui'], 'ui.')`のように、パッケージごとに明示的に`path`と`prefix`を指定する。作業ディレクトリ直下を無条件に`walk_packages([''], '')`のように列挙しない）。アプリ本体（`ui`/`models`/`services`/`config`）はいずれもモジュールトップレベルに実処理を書かない設計で統一されているため、この範囲に限定してもimportエラーの検知能力は損なわれない。

**今後の注意点**：作業ディレクトリ直下に一時的な調査・修正用スクリプトを置く場合、モジュールトップレベルに実処理（特にDB書き込み系）を書くと、この種の検証手法から不用意に実行されるリスクが常につきまとう。使い終わったスクリプトは早めに削除するか、実処理を`if __name__ == "__main__":`ブロック内に限定することが望ましい。

---

## 6. 整合性チェック実施記録

| 確認日 | 確認内容 | 結果 |
|---|---|---|
| 2026-09-01 | 本ファイル§5のチェック手順（1〜4）に加え、D-1〜D-6由来の関連項目（BOM列名エイリアス、calculate_lot_completion()の3要素タプルキー、replace_daily_result_for_todayの残存有無、save_ng_declaration()のreport_date条件残存有無、bom_masterのUNIQUE制約へのmounting_line包含、K行基準の丁取り数参照、実績CSV取込のステージング化）を計7項目＋is_activeフィルタで確認 | **巻き戻り0件**（全項目一致）。D-7として追記した2系統併存も、巻き戻りではなく意図的な設計と確認済み |
| 2026-09-01（同日、後続） | `db/migration_013`（`bom_master`へのitem_type列追加）を実DBへ適用する前に、`PRAGMA table_info(bom_master)`で現状を確認 | **巻き戻りではなく「そもそも一度も適用されていなかった」項目を発見**：実DBの`bom_master`には`item_type`列だけでなく、本来`migration_011`で追加されているはずの`mounting_line`列も存在しなかった（＝`migration_011`が実DBに未適用のまま放置されていた）。`migration_013`が「テーブルを作り直す」設計だったため、結果的に`migration_011`分（mounting_line）も`migration_013`分（item_type）も同時に反映され、1回の適用で最新スキーマに追いついた（行数は実行前後とも0件でデータ喪失なし）。**教訓**：この種の「巻き戻り検知」チェックリストは、過去に一度は正しく適用されてから巻き戻る場合だけでなく、そもそも一度も適用されていなかった場合の検知にも有効である。また「テーブル作り直し型」のマイグレーションは、複数世代分のスキーマ変更を1回の適用で吸収できるという利点も確認できた |
| 2026-09-03 | NG入力画面でのBOM展開時に`sqlite3.OperationalError: no such column: item_type`が発生。実DB（`inventory_app/db/inventory.db`）の`bom_master`に対し`PRAGMA table_info`で現状を確認 | **同一事象が再発**：実DBの`bom_master`は`item_type`列（および`mounting_line`列）を欠いた状態のまま235行のキャッシュを保持しており、`migration_013`が未適用の状態だった。直上の2026-09-01付エントリでは「migration_013適用済み、行数は実行前後とも0件」と記録されていたが、今回確認したのは235行のキャッシュを保持する別の状態であり、**その確認以降に別のタイミングで再度ドリフトしたか、あるいは2拠点並行開発のもう一方の環境（別のDBインスタンス）に対する確認だった可能性が高い**（どちらであるかは特定できていない）。`migration_013`を実DBに再実行し、235件の既存キャッシュを破棄して`mounting_line`・`item_type`両列込みで再作成した（再計算可能なキャッシュのため実害なし）。修正後、file_no=723・side=2（K行丁取り数バグの実例）でNG展開を実行し、正常動作（`item_type`内訳：part 109件・board 1件、board行はceil(2÷5)=1で正しく計算、`bom_master`への書き込み・再読み込みも成功）を確認。`python -m pytest tests/`にも影響無し（1 passed）。**教訓**：「一度チェックして問題なしと確認しても、その後の作業や別環境での操作により再度ドリフトしうる」。特に`CREATE TABLE IF NOT EXISTS`型のテーブルに対する整合性チェックは、確認した時点・確認した環境（どのDBインスタンスか）を記録に明記しておかないと、後から見て「巻き戻ったのか、そもそも別環境の話だったのか」の切り分けが困難になる（§5の運用上の注意も参照） |

整合性チェック手順（§5）が実際に機能し、巻き戻りを検知（1回目は「無かったこと」、2回目は「そもそも未適用だったこと」、3回目は「一度解消したはずの状態が再発したこと」）できた実績として記録する。

---

## 7. 実行環境・依存パッケージのドリフト問題（重要な運用上の教訓）

### 本章の位置づけ（§5との違い）

§5「環境間の整合性チェック手順」は「**同じ環境内でコードが巻き戻る**」問題（2拠点並行開発でのマージ事故）を扱っている。本章が扱うのはこれとは性質が異なる問題である：**コード自体は同じでも、それを実行するPython環境（インタプリタ・インストール済み依存パッケージ）が、開発・検証時に想定していた環境と、実際にアプリが動く環境とで食い違いうる**、という観点。マージ・巻き戻りの話ではなく、「そもそもどの環境で動かしているか」という前提のズレが原因のトラブルであり、区別して扱う必要がある。

### 発見の経緯（2026-09-15）

PDF読み取り画面を開いた際、以前解消したはずの`ModuleNotFoundError: No module named 'cv2'`が再発した。調査の結果、**実際にアプリを起動している環境（システムPython、`C:\python310`）と、開発・検証作業を行っていた環境（`.venv`仮想環境）が別だった**ことが判明した。`.venv`側に`pip install`していたPDF OCR関連パッケージ（`opencv-python-headless`等）は、`.venv`とは独立した`C:\python310`側には一切反映されておらず、実際にアプリが起動する環境からは見えていなかった。

### 根本原因

- リポジトリ内に明示的な起動スクリプト（.bat等）が一切存在せず、「アプリをどう起動するか」が利用者の環境依存（ショートカットの中身、コマンドの打ち方等）になっていた。
- リポジトリ直下に「用途不明の古い`launch.json`・`settings.json`」（`.vscode/`配下ではない、VSCode自体は読み込まない死んだ設定）が存在し、混乱の一因になりうる状態だった（git履歴調査によりInitial commit以来一度も更新されておらず、他からの参照も無いことを確認した上で削除。`UI_WORKFLOW_FIXES_NOTES.md`§4参照。**削除はまだコミットされていない**）。

### 対応

1. **システムPython側にも依存パッケージを個別にインストール**：`C:\python310\python.exe -m pip install -r requirements.txt`。
2. **明示的な起動用バッチファイル`run_app.bat`を新規作成**：`.venv\Scripts\python.exe`を明示指定して`main.py`を起動する（`UI_WORKFLOW_FIXES_NOTES.md`§4参照）。今後はこのバッチファイル経由での起動に統一することで、起動方法が環境依存になっていたという根本原因そのものを解消した。
3. **リポジトリ直下の死んだ設定ファイルを削除**：`git rm launch.json settings.json`（`.vscode/`配下ではない2ファイルのみ。`.vscode/launch.json`・`.vscode/settings.json`は対象外で、内容変更なく残存していることを確認済み）。

### 今後の教訓（重要・必ず思い出せるように明記）

> **教訓1：`requirements.txt`にライブラリを追加しても、それだけでは使えるようにならない。**
> `requirements.txt`はあくまで「必要なパッケージの一覧」であり、実際に`pip install`を実行した環境にしか反映されない。**開発・検証で使っていた環境（例：`.venv`）と、実際にアプリが起動する環境（例：システムPython）が別である場合、両方に対して個別に`pip install`する必要がある。** 「requirements.txtに書いたから大丈夫」と思い込まないこと。新しいライブラリを追加した際は、必ず「実際にアプリを起動する環境」でも動作確認すること。

> **教訓2：日本語（非ASCII文字）とWindowsのコードページ（文字コード）の組み合わせは落とし穴になりやすい。**
> 本プロジェクトで実際に発生した2つの実例：
> - システムのコンソールコードページ（932、Shift-JIS）とUTF-8保存の`requirements.txt`の不一致により、古いバージョンの`pip`（23.0.1）で`UnicodeDecodeError`が発生した（`.venv`側の新しい`pip`（26.2.1）では問題なかった）。`PYTHONUTF8=1`環境変数を付けて実行することで回避できる。
> - `run_app.bat`に日本語のコメント・メッセージをUTF-8で書いたところ、`cmd.exe`（コードページ932）がバイト列を誤って解釈し、**コマンド解析自体が壊れる**（存在しないコマンドとして次々エラーになる）現象が発生した。**バッチファイル（.bat）は非ASCII文字を含めるとコンソールの既定コードページに左右されやすいため、確実性を優先する場合はASCIIのみで書くのが安全。**
>
> どちらも「ファイルの文字コード（UTF-8）」と「それを読み込む側が想定する文字コード（Windows既定のANSI/OEMコードページ）」の不一致が原因であり、日本語を含むファイルを古いツール・`cmd.exe`等で扱う際は常に注意すること。

### 実運用中のロックファイルに関する注意深い切り分け（参考）

検証中、本番用DBのロックファイルの`acquired_at`が更新されていることに気づいたが、`LoginWindow`はログインボタンを押すまでDBロックを取得しない設計であり、検証作業ではログイン操作を行っていないことを根拠に、検証作業自体の影響ではなく、この共有マシン上で実際の利用者が同時期に使用していたことによるものと判断した（実運用中の共有環境で検証を行う際の切り分けの一例として記録する）。

---

## 8. 検証・テスト時の状態隔離に関する注意点（MainWindow等、永続化された状態を持つコンポーネントの検証）

### 本章の位置づけ（§5・§7との違い）

§5「環境間の整合性チェック手順」は「同じ環境内でコードが巻き戻る」問題（2拠点並行開発でのマージ事故）を、§7「実行環境・依存パッケージのドリフト問題」は「コードを実行するPython環境自体の食い違い」を扱っている。本章が扱うのはこれらとは別の観点：**検証スクリプトが、対象コンポーネント（特にアプリの状態をディスクへ自動的に永続化・復元する機能を持つもの）を安全に隔離できているか**という、検証手法そのものの設計上の注意点である。

### 発生した事故（2026-09-15、操作履歴機能の動作確認中）

`ui.main_window.MainWindow`を検証スクリプトから直接インスタンス化するテストで、意図せず実際の`db/inventory.db`に接続してロックを取得してしまう事故が発生した。

**原因**：`config.DB_PATH`を一時DBへ直接上書きしただけでは不十分だった。`MainWindow.__init__()`には「前回選択されていたDBパスを`app_settings.json`から復元する」ロジック（D-14と同時期に実装された、選択中DBパスの永続化機能）があり、**`config.APP_DATA_DIR`（`app_settings.json`の探索先）まで隔離していなかったため**、この復元ロジックが実環境側の設定ファイルを参照し、実DBへ切り替わってしまう構造だった。

**実害と対応**：
- 実DBのロックファイルの中身が、テスト用の架空の作業者名で一時的に上書きされたが、`git checkout`で元の内容に復元済み。
- `operation_log`テーブルが実DBに作られていないことを直接SQLiteで確認（スキーマの汚染なし）。
- 以降、`MainWindow`を扱う検証では、`config.DB_PATH`に加えて`config.APP_DATA_DIR`も一時ディレクトリへ隔離することを標準手順とした（D-15参照）。この隔離だけで実環境への漏れが再発しないことを、その後の複数回の`MainWindow`検証（未完了計画DB間引き継ぎ・月別DB削除等の検証を含む）で確認済み。

### 教訓（重要）

**永続化機能（選択中DBパスの自動復元）を実装したこと自体が、検証時の隔離を難しくする新しい落とし穴を生んだ**という事実は重要である。今後、この種の「アプリの状態を裏で自動的に復元する」機能（設定ファイル・キャッシュ・自動保存等）を追加する際は、**検証・テスト時にその復元元（設定ファイルの場所等）も含めて隔離できるか**を、実装と同時に検討する必要がある。「対象コンポーネントが読み書きする状態は、DB_PATHだけとは限らない」という前提でテスト隔離を設計すること。

### 2件目の事故（2026-10-01、レガシーテーブル削除後の実機動作確認中、D-40）

レガシーテーブル削除後の実DBに対する動作確認で、`MainWindow({"worker_id": "diag_tester", ...})`を検証スクリプトから直接インスタンス化し、確認後に`mw.destroy()`で閉じた。

**原因**：`ui/main_window.py::MainWindow`は`self.protocol("WM_DELETE_WINDOW", self._on_app_close)`でウィンドウクローズ時のハンドラを登録しており、ロック解放処理（`release_lock()`）はこの`_on_app_close()`からのみ呼ばれる設計だった。検証スクリプトが`mw.destroy()`を直接呼んだため、このプロトコルハンドラを経由せず、`MainWindow.__init__()`で取得済みだった実DBのロックが解放されないまま残留した。

**実害と対応**：
- 実DBのロックファイルに、テスト用の架空の作業者名（`diag_tester`）が取得者として残った状態になった（この状態を見た別の調査で「別の人物が使用中では」と懸念される事態になった。詳細はD-41・`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §18.5参照）。
- `release_lock()`はプロセス内メモリ（`_owned_tokens`）でしか自分が取得したロックかどうかを判定できない設計のため、既にスクリプトが終了した後の別プロセスから通常の`release_lock()`を呼んでも何も起こらない。後始末には、アプリ自身が提供する「古いロックを強制的に奪取する」経路（`acquire_lock(..., force=True)`）を同一プロセス内で呼んだ上で、正規の`release_lock()`で解放する手順が必要だった。

**教訓（D-40）**：`MainWindow`を検証スクリプトから直接インスタンス化する場合、終了時は`destroy()`ではなく、ウィンドウクローズのプロトコルハンドラを明示的に呼ぶ（`mw.event_generate("<<WM_DELETE_WINDOW>>")`相当、または`_on_app_close()`相当の処理を自前で呼ぶ）必要がある。D-15（`APP_DATA_DIR`隔離漏れ）と合わせて、`MainWindow`は「隔離すべき状態」と「正しい終了手順を踏まないと残留する状態」の両方を持つコンポーネントであることに注意。

---

## 9. 調査結果が確認できない場合の対応原則（重要な教訓、D-18）

### 発生した事象（2026-09-23）

「実績CSV取込のステージング一覧で、払い出し日列が全行空欄なのに自動確定可能と表示される」という矛盾の調査中、添付ファイルの中身が繰り返し空のまま届く事象が発生した。この状況で、実際のコードを確認せずに「`row`変数の誤参照によりreport_dateが上書きされている」という**推測による原因診断**が提示された。この診断は、対象ファイルに実在しない変数名（`mapped_row`）を含んでいた。

### 確定した原則

**調査結果（実際のコード・実際のデータ）が確認できない状況では、推測で具体的な原因を断定してはならない。「分からない」と伝え、確認を待つべきである。**

具体的な変数名・関数名・行番号を含む診断は、それ自体が「実際にコードを読んだ」という強い印象を与えるため、読み手（人間・別のAIエージェント問わず）がそのまま信頼して修正を実行してしまうリスクが特に高い。存在しないコードに対する「修正」は、動いているコードに不要な変更を加え、かえって新しいバグを埋め込む結果になりかねない。

### 実務上の対応指針

- ファイルの中身が確認できない・添付が空である等、事実確認ができない状態のまま原因を尋ねられた場合は、その状態自体を明示した上で、確認可能になるまで具体的な原因の断定を避けること。
- 「〜という可能性がある」という仮説の提示自体は問題ないが、変数名・関数名等の具体的なコード要素を含む診断を行う場合は、必ず実際にそのコードを読んで確認した上で行うこと。
- 詳細な経緯は`UI_WORKFLOW_FIXES_NOTES.md`グループAB追記2参照。

### 原則の拡張：自分自身の検証ツールの出力も疑う（2026-10-01、D-41）

レガシーテーブル削除後の動作確認で、`run_app.bat`相当の起動確認を行った際、Windows環境のターミナル（cp932/UTF-8の不一致）によって日本語のウィンドウタイトルが文字化けして表示された。この文字化けした文字列を正確にバイト単位で確認せずに目視でパターン読み取りし、「ログイン画面がスキップされてメインメニューに直接到達した」という誤った報告を行ってしまった（セキュリティに関わる可能性がある重大な誤報告）。

事後、`ctypes.windll.user32.GetWindowTextW()`でOSから直接Unicode文字列を取得する（文字化けを経由しない）方法で再検証したところ、実際にはログイン画面が正しく表示されており、スキップする経路はコードに存在しないことが判明した。

**確定した原則の拡張**：D-18「調査結果が確認できない状況で推測により断定しない」という原則は、**他者から与えられたコード・データだけでなく、自分自身が実行した検証ツールの出力（特に文字化け・ログの欠落・タイミングのズレ等、不完全な形で返ってくる可能性がある出力）にも適用されるべきである**。文字化けした出力や、一見すると明確に見える結果であっても、断定的な報告を行う前に、より確実な手段（エンコーディングを明示した取得方法、バイト単位の比較等）で再確認する習慣を持つこと。

---

## 10. 複数ウィンドウ開放中のDB切り替えに関する未解決の設計課題（重要、D-19）

### 発見の経緯

払い出し日空欄問題（本ファイル§9・`UI_WORKFLOW_FIXES_NOTES.md`グループAB追記2）の調査の一環として、「各DBフォルダの独立性」を検証する過程で判明した。

### 判明した事実

- `models/db_common.py::get_connection()`は、呼び出しのたびに`config.DB_PATH`をその時点の値で正しく読み取る（キャッシュ無し）ことをコード確認・実機テスト（DB_A→DB_Bの切り替え）の両方で確認した。この点自体は設計通り正しく機能している。
- **しかし、既に開いている画面（`KittingProductionEntryWindow`・`ProductionImportStagingWindow`等）は、開いた時点のDBパスを内部（インスタンス属性）に保持しておらず、操作のたびにグローバル変数`config.DB_PATH`を都度参照する設計になっている。** そのため、**ある画面を開いたまま、別の経路（メインメニュー等）でDBが切り替わると、既に開いている画面の操作対象がいつの間にか別のDBに変わってしまう**。実機検証（DB_A接続中に画面を開き、開いたまま`config.set_db_path(DB_B)`を呼んだ後、既存画面の「更新」相当の操作を実行）で、操作対象が実際にDB_Bに切り替わることを確認済み。

### 業務影響の推測（未確定）

もし実際に、ステージング画面や生産実績入力画面を開いたまま、別の作業（DB切り替え、月次DB作成等）が行われていた場合、画面表示は古いDBのまま止まりつつ、裏で操作対象が新しいDBに切り替わり、中途半端な状態でCSV取込・登録処理が行われる可能性がある。これが本ファイル§9の症状の有力な説明候補として浮上したが、**確証は得られていない**（該当する過去のDBは既に削除済みのため事後検証は不可能）。

### 対応方針（ユーザー決定）

以下2案が検討されたが、**いずれも実装せず、今回は記録に留めることを選択**：
- 案①：画面ごとに、開いた時点のDBパスを固定して保持する（以降そのDBだけを操作対象にする）。
- 案②：DBが切り替わったら、開いている全ての画面を強制的に閉じる（既存の「他画面が開いている場合の警告」をさらに一歩進める）。

**この設計上の弱点は未解消のまま残っている。今後の.exe化・本格運用の前に、対応の要否を改めて検討する価値がある。**

### app_settings.jsonの制約（副次的な発見）

選択中DBパスの永続化（`app_settings.json`）は`config.APP_DATA_DIR`という単一ファイルに保存される設計のため、複数のDBフォルダが存在しても「前回選択したパス」の記録は1つしか持てない。これ自体は上記症状の直接原因ではないが、複数DB運用時の設計上の制約として記録する価値がある。同一PC内で複数DBを行き来する運用がある場合、次回起動時に意図しないDBへ自動接続してしまうリスクがある点に注意。

---

## 11. 共有フォルダ運用方針の転換（ローカル+バックアップ方式へ、重要な方針転換、D-20）

### 本章とD-10・グループQ（UI_WORKFLOW_FIXES_NOTES.md）との関係

D-10（本ファイル§2）およびUI_WORKFLOW_FIXES_NOTES.mdグループQで確定した「共有フォルダ上のDBへ複数PCが直接アクセスする」運用方式・そのためのロック機構（`services/db_lock_service.py`）は、**技術的には無効化・撤回されたわけではなく、引き続き実装として存在する**。本章が記録するのは、その運用方式の**実運用上の位置づけの転換**である。以前の記述と矛盾するものではなく、その後の運用経験を踏まえた方針の発展として読むこと。

### 発見の経緯（2026-09-23）

「ロード画面が固まって更新されない」という報告の調査の過程で判明した。

### 判明した原因

共有サーバ（UNCパス）上のDBに対する操作が、ネットワーク経由のため時間がかかり、結果的に「固まっているように見える」動作になっていた。実際にはロック機構等は正常に機能していたが、体感速度が悪かった。

### 確定した新方針

以前実装した「共有フォルダ運用」（複数PCから同一DBに直接アクセスする設計、D-10・UI_WORKFLOW_FIXES_NOTES.mdグループQ〜グループZ）から方針転換し、**基本的な使用はローカルDBに統一し、共有は今後実装予定のバックアップ機能（定期的にローカルDBを共有フォルダへコピーする形）で行う**方針に変更された。

この転換により、以前実装した共有フォルダの複数PC同時アクセス・ロック機構（D-10・D-11・D-12、UI_WORKFLOW_FIXES_NOTES.mdグループQ・グループQ追記・グループV）は、引き続き技術的には利用可能だが、実運用上は「普段はローカルで使い、共有は非同期のバックアップコピーで行う」という、より単純で速い運用に置き換わる見込みである。バックアップ機能（`UI_WORKFLOW_FIXES_NOTES.md` §4で保留中）の実装方針（タイミング・保存先）の検討時に、この新しい位置づけを踏まえる必要がある。また、.exe化の設計（`UI_WORKFLOW_FIXES_NOTES.md`グループW）も、共有フォルダ経由でのDB配置を前提にしていた部分があれば、この新方針を踏まえて見直しが必要になる。

---

## 12. 実績・完成数の判定ロジックは必ずcalculate_lot_completion()を経由する原則（重要、D-21）

### 背景：同種のロジック不一致が繰り返し発見されている

「ロットの完成数・仕掛数・残数」に関わる判定を、`calculate_lot_completion()`（ファイルNo・面単位で複数kitting_list_noの実績を合算し、その最小値をロットの完成数とする、正しいロジック）を経由せず、独自に実装してしまう不具合が、これまでに**3回**発見されている。

1. **`UI_WORKFLOW_FIXES_NOTES.md` グループI追記**：「基板別実績」「日次実績履歴」には面1省略ロジック（面2があれば面1は表示しない）を実装済みだったが、日報・月報画面にはこのロジックが未適用だった。さらに月報の「仕掛数量抽出」機能が面1の`surplus_qty`をそのまま抽出してしまっていた。
2. **`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §14**：日報・月報（`_build_report_rows()`）が、`calculate_lot_completion()`とは別の独自の完成数計算ロジックを持っており、「その日/期間に実績登録があった行だけ」を対象に最小値を計算するため、未生産のファイルNoが計算対象から漏れ、揃っていないのに引落されてしまう不具合があった。
3. **`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §15**：生産実績入力画面自体の「入力済みを隠す」フィルタ（`apply_plan_filters()`）が、`order_qty`・`actual_qty`という行単位（単一kitting_list_no単位）の単純比較のままで、同じ画面の`lot_completed`/`lot_remaining`列（`calculate_lot_completion()`ベース）と矛盾する判定結果を返していた。

いずれも共通する原因は、**「実績・完成数」を扱う新しい機能を追加する際に、既存の`calculate_lot_completion()`を再利用せず、その場しのぎの独自ロジック（多くは行単位・kitting_list_no単位の単純比較）を実装してしまうこと**である。`calculate_lot_completion()`は「構文的には正しく動作してしまう」（例外を出さない、ただし計算結果が静かに誤る）独自ロジックとの共存を許してしまいやすく、テストや起動確認だけでは検知しにくい点が、この種の不具合を繰り返させている一因と考えられる。

### 確定した原則（D-21）

**実績・完成数（ロットの完成判定・仕掛数・残数）に関わる新しい判定ロジックを実装する際は、必ず`calculate_lot_completion()`を経由すること。独自に`order_qty`/`actual_qty`等の行単位比較を実装してはならない。**

新しい画面・機能で「このロット（またはこの計画行）は完了しているか」を判定する必要が生じた場合、まず`calculate_lot_completion(lot_no)`の`completed_quantity`・`remaining_quantity`・`file_actuals`を使えないか検討すること。行単位（`kitting_list_no`単位）の実績だけで完了を判定すると、同一ロット・同一ファイルNoを複数のkitting_list_noが分担しているケース（実運用で確認済みのパターン、`PRODUCTION_NG_ENHANCEMENTS_NOTES.md`§4参照）を正しく扱えない。

### 実務上の注意点

- 同一画面・同一一覧の中に、`calculate_lot_completion()`ベースの表示列（例：`lot_completed`/`lot_remaining`）と、それを経由しない独自判定（フィルタ・警告等）が併存していないか、既存機能を修正する際にも確認すること。
- パフォーマンス上の懸念がある場合は、`_fetch_plan_list_rows()`・`_build_report_rows()`・`apply_plan_filters()`等で既に確立されている「lot_no単位のキャッシュ（1回の処理内で同一lot_noへの重複呼び出しを避ける）」パターンを適用すること。

---

## 13. 保留・確定マージの識別条件不足による不正データ混入（重要、D-22）

### 発見の経緯

計画一覧に「キッティングNo.が日付形式」「ロットNo.が単純な数値」等の不自然なデータが混入していると報告され、`kitting_plan_items`への全書き込み経路を調査した過程で発見された。混入データ自体の直接原因は別（`UI_WORKFLOW_FIXES_NOTES.md`グループAB追記5・6参照、CSVファイル自体の列内容検証不足）だったが、調査の過程で保留・確定マージ処理（`_merge_from_pending()`）自体にも独立した重大な欠陥が見つかった。

### 判明した事実

キッティングNo未確定行の保留・確定マージは、識別キー（`lot_no`, `setup_file_no`, `production_side`, `order_qty`）が一致することのみを条件にマージを実行していた。しかし実データを調査したところ、**同一ロット・同一ファイルNo・同一面・同一発注数量だが、`board_name`（製品名）が異なる別々の、両方とも`is_active=1`の正規のキッティングバッチが実在する**ことを確認した（例：`lot_no='260066'`・`setup_file_no='0448'`・`production_side='1'`・`order_qty=3000.0`で、`plan_start_datetime`のみが異なる2バッチ）。このため、識別キーだけでマージ可否を判定すると、実際には無関係な別の計画の`board_name`・`plan_start_datetime`等の情報が誤って混入する可能性があった。

### 確定した原則（D-22）

**識別キーの一致だけでは実データ上一意性が保証されないため、`board_name`（正規化済み、`normalize_board_name()`で表記ゆれを吸収）も一致することを確認してからマージすること。** 識別キーは一致するが`board_name`が異なる場合は、偶然の一致（別の計画）とみなし、マージ対象から外して保留行はそのまま残し、現在の行は独立した新規計画としてそのまま登録する。

詳細・実装・動作確認は`UI_WORKFLOW_FIXES_NOTES.md`グループAB追記5参照。

---

## 14. CSVフォーマット取り違え検知の仕組み（重要、D-23）

### 発見の経緯

実績CSV取込・キッティング計画CSV取込という、目的の異なる2種類のCSV取込機能が存在するが、いずれも「選択されたファイルが本当にそのフォーマットのCSVか」を検証する仕組みを持たず、拡張子`.csv`であること以外のチェックが無かった。実際に、キッティング計画CSVが誤って実績CSV取込に読み込まれる事故（2026-09-24、457件の不正データ混入）と、その逆方向のリスク（実績CSVが誤ってキッティング計画CSV取込に読み込まれるケース）の両方が起こり得ることが判明した。

いずれの事故も、2つのCSVフォーマットの一部の列名（`lot_no`候補「ロットNo」・`product_name`候補「機種基板名」等）が偶然共通していたため、エラーにもならず「一見一部だけ正常に見える」中途半端な取込結果になり、発見が遅れるという共通の危険性があった。

### 確定した原則（D-23）

**CSV取込機能には、想定と異なるフォーマットのファイルが誤って読み込まれることを検知する仕組み（案C：自フォーマット固有列の欠如検知＋他フォーマット固有列の混入検知、両方）を用意すること。** 警告は強制ブロックではなく確認ダイアログとし、「はい」でユーザー判断による続行を許容する（列内容の検証までは行わないため、誤検知・見逃しの可能性がゼロではないことを踏まえた設計）。

### 実装

`services/csv_format_detection.py`（新規、両取込サービスが共有する）に、実績CSV固有列（「払い出し日」「基板構成数」）・キッティング計画CSV固有列（「キッティングリストNo」「実装開始日時」「セットアップファイルNo.」）を定義し、ヘッダー1行のみを読んで判定する仕組みを実装した。詳細・動作確認は`UI_WORKFLOW_FIXES_NOTES.md`グループAB追記6参照。

---

## 15. 構成基板数チェックの起点拡大と、マスタ値の意味に関する未解決の疑義（重要・作業一時中断中、D-24）

### 発見の経緯

日報・月報（`services/production_service.py::_build_report_rows()`）に組み込んだ構成基板数チェック（§7・`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §16参照）に、2つの設計上の欠陥が実データで確認された：

1. **起点の欠陥**：チェックは「対象期間に`production_daily`実績があるlot_no」のみを起点にループしていたため、その期間にたまたま実績入力が無かったロットは、構成基板数が実際に不足・超過していても一切検知されない（ロット`260079`で再現確認）。
2. **代表1件方式の欠陥**：同一lot_no内に複数の異なるboard_nameが存在する場合（実データで268ロット確認）、最初に見つかった1件のboard_nameのみでマスタ登録状況を判定していたため、代表に選ばれなかった方のboard_nameが未登録でも警告から漏れる（ロット`425973`で再現確認、表示は「未登録」なのに警告リストには一切現れない状態だった）。

### 確定した方針（D-24前半）

**この2つの欠陥を修正するには、チェックの起点を「その帳票に実績があった行」から「現在アクティブな全ロット」へ、判定単位を「lot_no」から「(lot_no, board_name)」へ、それぞれ広げる必要がある。** ただしこれは、日報・月報という「実績の記録」としての性質と、「現在アクティブな全ロットの構成基板数健全性を継続的に監視する」という別の関心事を1つの帳票機能に混在させることになるため、**構成基板数チェックを日報・月報から切り離し、独立した「ロット進捗チェック」機能として実装する**方針に転換した。配置場所は、日報・月報と同様に生産実績入力画面（`ui/kitting_production_entry.py`）に付随する画面とする（機能の性質が「ロットを構成する全ファイルNoの引落・仕掛・未生産を取りこぼしなく確認する」というロット進捗管理であるため）。

独立関数`services/production_service.py::check_lot_progress()`として実装済み（`models.kitting_plan.list_plan_items_for_all_lots()` + `get_app_cumulative_qty_bulk()`の一括取得パターン、`(lot_no, board_name)`単位のグルーピングで両方の欠陥を解消）。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §16参照。

### 未解決の疑義（D-24後半、作業一時中断の理由）

`check_lot_progress()`を`(lot_no, board_name)`単位で厳密に実行した結果、**約1250件の組のうち約71%（850件）が「不足」判定**という、想定を大きく上回る不一致率が判明した。

原因として、構成基板数マスタの`board_count`列が、本来「そのboard_name（基板）自体が何ファイルNoから構成されるか」を意味すべきところ、実際のマスタ登録内容では**別の概念（ロット・製品全体としての基板種類数等）と混同されて登録されている可能性がある**ことが対話を通じて浮上した。以前（2026-09-24、ロット`287149`：メイン・サブ・ジュコウの3board_nameが同一`board_count=3`を持つ）の検証は「ロット単位で共通の値」という前提を支持していたが、今回の71%という数字はこの前提と実態の食い違いを示唆している。

**この疑義が解消するまで、「ロット進捗チェック」のUI画面実装（`check_lot_progress()`を使う新規画面）は一旦保留とする。** 判定ロジック自体（`check_lot_progress()`関数の実装）は完了済みだが、マスタ値の意味という前提条件が確定していない状態でUIとして公開すると、誤った判定基準に基づく警告を利用者に見せてしまうリスクがあるため。次にこの作業を再開する際は、まずマスタ運用担当者に`board_count`列の正しい意味（board_name単位かロット単位か）を確認することから始めること。

### 追記（2026-09-28）：疑義の解消と、比較粒度の誤りだったことの確定

上記の疑義は解消した。**`board_count`は「board_name単位」ではなく「ロット単位」の値**であり、「そのロットが何枚の基板で構成されるか」を表す。業務側は計画をロット順に並べ、基板名でVLOOKUPした値をロット内の全board_name行に共通で入れる運用をしている。実データでの裏付け（複数board_nameを持つ268ロット中255件・95.1%で、ロット内の全board_nameが同一`board_count`を持つ等）は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17.1参照。

先の「71%が不足」という結果は、データの不備ではなく、**`(lot_no, board_name)`単位で比較するという設計自体の誤りだった**（D-25）。この設計はClaude（本アシスタント）が提案・実装したものであり、§16「代表1件方式の欠陥」（未登録判定はboard_name単位で見るべきだった）への対策を、構成基板数の比較という別の問題（本来はロット単位で見るべきだった）にもそのまま横展開してしまったことが原因。両者は粒度が異なる別々の問題だった。

修正後の判定粒度・693ロットの内訳（一致584・不足73・マスタ未登録32・超過2・構成基板数の食い違い2）、引落ルールの一本化（D-26）、「ロット進捗チェック」画面（`ui/lot_progress_window.py`）の実装は、いずれも`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17に詳細を記録した。

---

## 16. 暫定判断の明示と、実装前の粒度・意味確認の徹底に関する追加の教訓（2026-09-28）

§15（D-24・D-25）の作業を通じて、§9（D-18）の原則を補強する2つの教訓が得られたため記録する。

### 教訓1：実装の前に、値・粒度の意味が業務の実態と合っているかを確認しておくこと

構成基板数（`board_count`）マスタ照合機能で、値の意味（board_name単位かロット単位か）の確認を実装の**後**に行った結果、`(lot_no, board_name)`単位という誤った粒度で厳密な判定ロジックを先に実装してしまい、「約71%が不足」という誤った調査結果を一度報告することになった（§15追記・`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §17.1参照）。値の意味・集計粒度が業務側の運用と一致しているかは、判定ロジックを書き始める前に確認すべき事項である。

### 教訓2：Claudeが置いた暫定的な判断は、暫定であると明示し、影響が広がる前に確認を取ること

`check_lot_progress()`の引落ルールは当初、Claude（本アシスタント）が「一致（match）以外はすべて引落0」という仮の判断で実装していた。これはユーザーが決めた業務ルールではなかったが、明示的に「仮の判断である」と伝えないまま実装に残ったため、後になってマスタ未登録のロットまで一律に引落0になるという問題を引き起こしてから初めて訂正することになった（訂正後のルールはD-26参照）。仮の判断・未確定の前提でコードを書く場合は、その旨をその都度明示し、ユーザーの確認を得る、または影響範囲が広がる前に確認を取ることが望ましい。

### D-18との関係

本章の2つの教訓は、いずれもD-18（「調査結果が確認できない状況では推測で断定しない、分からないと伝えて確認を待つ」）と同じ方向の原則の、別の局面（実装前の前提確認、暫定実装の扱い）への適用である。D-18は「調査結果を待つ」場面を扱っていたのに対し、本章は「実装の前提・仮の判断」の場面を扱っている点で異なるが、根底にある「確認できていないことをそのまま確定事項として扱わない」という姿勢は共通している。

---

## 17. ロット状態履歴（lot_status_history）の新設と、日報・月報統合画面への一本化（2026-09-30〜10-01、D-29〜D-34）

### 背景：日々の引落算出の限界と、方式転換

「日ごとの引落（前日比の増分）・完了一覧・未完了一覧を見たい」という要望（`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §19のStep1で着手）に対し、当初は`production_daily.report_date`を使って`report_date <= 指定日`のSUMで過去の累計を逆算する案を検討していた。しかし調査の結果、`production_daily`が「1計画1行、常に上書き」（D-6）の設計であるため、**後からの修正・遅延登録によってreport_dateが変わると、過去に計算した累計が事後的に変わりうる**という限界が判明した。

ユーザーの判断により、過去の状態を逆算で再現するのではなく、**「実績が登録・修正されるたびに、その時点の状態を記録として積み上げる」**方式（D-29）に転換した。`models/lot_status_history.py`（新規）が、実績の登録・修正のたびに`_evaluate_lot_status()`の結果（ロットの状態・ファイルNo単位の引落/仕掛/未生産の内訳を全てJSON配列で保持、代表1件への省略はしない）を1行の履歴として記録する。`recorded_at`は実際に書き込まれた壁時計時刻であり、`report_date`と異なり後から遡って変わらないため、「指定日以前で最新の記録」と「前日以前で最新の記録」の差分を取るだけで、事後的な変化に影響されない日々の引落を算出できる。

### 記録フックの入れ方（D-30）

`models/operation_log.py`（操作履歴機能）と同じ考え方で、共通フックを1か所に一元化せず、実際に書き込みが起きる5箇所（`register_daily_result()`・`overwrite_daily_result()`・`update_daily_result()`・`delete_daily_result()`・`db_migration_carryover.py`）に個別に呼び出しを追加した。記録失敗時は本来の登録・修正処理自体を失敗させない（ログに残すのみ）設計とした。

Shift+S一括登録では、当初は選択行ごとに履歴記録していたため100件規模で約4〜5秒の追加遅延が見積もられた。`record_history`引数を追加して行ごとの記録を抑制し、バッチ処理の最後に、影響を受けたdistinctなlot_noだけ1回ずつ記録する方式に変更した結果、重複の多いロットでは約64%の処理時間短縮を確認した。

### 教訓：誤った計測結果を安易に信じない（D-18の再確認事例）

検証の過程で「実UI経由で5行の一括登録に約50秒かかり、原因不明」という事象が一時的に報告された。原因を調査した結果、**検証スクリプト側がバッチ処理末尾の完了通知ダイアログ（`messagebox.showinfo`）をモックし忘れていたことによる誤計測**であり、実装自体には性能上の問題が無かったことが後日の再検証（ダイアログを正しくモックした上で再計測）で判明した。本来の処理時間は0.2秒程度だった。

この一件は、D-18（「確認できない・怪しい結果を安易に確定事項として扱わない」）の精神を、**自分自身が作成した検証スクリプトの結果に対しても適用すべき**ことを示す事例として記録する。遅い・おかしいという測定結果が出た場合、実装を疑う前に、まず計測方法自体に見落とし（特にGUIの未モック・ブロッキング呼び出し）が無いかを確認すべきである。

### `_perform_registration()`の「重複」呼び出しは削除しなかった（D-32）

`ui/kitting_production_entry.py::_perform_registration()`で、`search_plan()`呼び出し後に`_setup_ng_side_ui()`・`_load_current_daily_qty()`をもう一度呼んでいる処理は、一見冗長に見えたため削除を検討したが、調査の結果、両者の間にNG申告の保存等、実際のDB書き込みが挟まっており、削除すると登録後のNG入力欄に保存した値が反映されなくなることを実データで確認したため、削除せず現状維持とした。

### 日報・月報の統合（D-33）、ロット進捗チェック画面のソート方式の限界（D-34）

個別の画面（日報・月報・ロット進捗チェック・日々の引落一覧）が増えてきたことを受け、全画面統合も検討したが、「実績のあるロット全体を土台にした全画面統合」は見送り、**日報・月報のみを`ui/unified_report_window.py::UnifiedReportWindow`に統合**し、ロット進捗チェック・日々の引落一覧は母集団・時間軸の性質が異なるため独立画面として維持する方針で確定した（D-33）。

統合の過程で、`DailyReportWindow`が実質的に「1日だけの月報」として動いていたこと（`on_display()`・`refresh_report()`が`build_monthly_report(date, date)`を呼んでいた）、および`DailyReportWindow`が`configure_status_color_tags()`を一度も呼んでおらず日報画面で確認事項欄の色分け警告が機能していなかったバグを発見し、統合画面の実装と同時に修正した。`MonthlyReportWindow`・`DailyReportWindow`は、全メソッドの移植漏れが無いことを確認した上で削除した（`daily_report_window.py`モジュール自体は、他画面が再利用する共通関数の置き場所として存続）。

統合作業の途中で、月報限定だった「仕掛数量抽出」「3種のCSV出力」への導線が一時的に失われる見落としが発生したが、自己点検により発覚し追加実装で解消した。

さらに、統合画面に列ソート機能（ロット進捗チェック画面の`sort_by_column()`を踏襲）を実装する際、**既存の`_build_report_rows()`が同一ロットの行の連続性を保証しない設計**であることが判明した（実データで、同一ロットの行が922行中7番目と842・843番目に分断される実例を確認）。ロット進捗チェック画面の実装が前提としていた「現在の並びで連続している区間をブロックとする」方式ではこれに対応できないため、「lot_no値が一致する行を、物理的な位置に関わらず全て1つのブロックに集める」辞書ベースの方式に設計変更した（D-34）。442ロット全件で分断が無いことを確認済み。

### 未確認・申し送り事項

「今週」の起算日（月曜起算とした）、列の表示/非表示のレイアウトの見た目、チェックボックスの状態を次回起動時に保持するか、大量データ（922行）でのフィルタ操作の体感速度は、いずれも業務側の意向・実機での目視確認が済んでいない。詳細は`PRODUCTION_NG_ENHANCEMENTS_NOTES.md` §21参照。

---

## 18. 登録日列の追加・未完了ロット引き継ぎルールの見直し・共通マスタの月次DB同居問題の発見・レガシーテーブル削除（2026-10-01〜02、D-35〜D-43）

（本章の内容は変更なし。2026-10-02〜03の続報は本ファイル §19 参照）

### 18.1 「登録日」列の追加（D-35）

`UnifiedReportWindow`に`production_daily.report_date`を「登録日」として表示する列を追加した（Treeview・CSV・PDF出力すべてに反映）。調査の結果、`_build_report_rows()`の各行は元々1つの`production_daily`レコード（＝1つの`kitting_list_no`）に1:1対応しており、`build_wip_extraction_rows()`が必要とするような複数バッチの代表選定ロジック（`_pick_representative_plan_item()`）は不要だった。「未確定」仮想行は元レコードが無いため空欄とする。

実装時、`ui/daily_report_window.py::ReportPreviewWindow.COL_WIDTHS`（印刷プレビュー用の列幅、`COL_HEADERS = REPORT_HEADERS`と同じインデックス対応の仕組み）への追加が当初漏れていたことに気づき、合わせて修正した。放置するとプレビューのヘッダーと列幅が1列ずれる実害があった。

「登録日は修正登録等で事後的に書き換えられうる、現在その行に保存されている最新値に過ぎない」という既知の限界（D-6関連）は、今回も意図的にスコープ外とした。

### 18.2 未完了計画のDB間引き継ぎルールの見直し（D-36、D-37）

`list_incomplete_lots()`を、構成基板数チェックを含む`_evaluate_lot_status()`ベースの判定に統一した（D-36）。以前は構成基板数チェックを行わない古い`_compute_lot_completion()`直接呼び出しのままで、2026-09-28の判定ロジック一本化（D-26）の対象から漏れていた。これにより、構成基板数不足のロットが無条件で未完了として引き継ぎ対象になるよう修正された。実データでの変化：390件中26件が新規に未完了として追加判定され、42件のshortfallロットが「引落0扱い」で正しく評価されるようになった。matchロット283件は退行なし。

ユーザー確定の新ルール：
1. 未着手（実績が1件もない）の計画は、実装開始予定日が引継ぎ日から50日以内のものだけを引き継ぐ（データの蓄積による肥大化防止）。`plan_start_datetime`が空・パース不能な場合は安全側（除外せず含める）とした。
2. 完了しているロットは引き継がない（レポート出力の有無は問わない）。仕掛・未完了が残っているロットは、そのロット全体（全ファイルNo、完了済みのファイルNoも含む）をまとめて引き継ぐ。調査の結果、ロット単位の引き継ぎは既存実装（`_fetch_plan_items_for_lot()`・`_fetch_production_daily_for_lot()`がlot_no単位で無条件に全件取得する設計）で既に満たされており、コード変更は不要だった（D-37）。

`wip_scrap_records`が「コピーしない」対象であることを、`services/db_migration_carryover.py`のモジュールdocstringに明記した（動作は従来通りコピーしない設計だったが、ドキュメントへの記載漏れがあった）。

### 18.3 「共通マスタ」が実は月次DBに同居していたという設計上の発見（D-38、実装状況は18.6参照）

`board_structure_master`・`parts_attributes`・`workers`等の「共通マスタ」が、実際には`config.DB_PATH`（月次DB切り替えの対象そのもの）に格納されており、月次DBを新規作成するたびに空の状態から始まる設計だったことが判明した。

この性質は最初のコミット時点からの設計であり、途中の変更で崩れたものではない。2026-08-30に追加された既存コメント（`ui/main_window.py`、「現状は月次・共通いずれのテーブルも同一のDBファイルに同居しており…」）は、この事実に気づいて明文化しただけで、設計自体を変更したものではなかった。

この発見のきっかけは、「未完了計画の引き継ぎ機能で、構成基板数マスタが新DBにコピーされないため、引き継いだ直後に再評価すると構成基板数マスタが空でunregistered扱いになり、元の問題（構成基板数不足ロットが見落とされる）が再発する」という実例だった。

分離すべき共通マスタの候補として、`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`の5テーブルを特定した（D-4の訂正も参照）。`bom_master`は、`data_ym`（年月）列を持つ設計であり月次の概念と矛盾しないこと、共有フォルダのTSVから再計算可能なキャッシュであることから、月次データ側に残すのが妥当と判断し、分離対象には含めない。

分離の実現には、`config.py`への第2のDBパス新設、各マスタ系モジュールの接続先振り分けという設計変更が必要で、影響範囲は複数モジュールに及ぶ。**2026-10-02追記：その後このマシンで実装済みであることを確認した。詳細・データ移行の未解決課題は18.6参照。**

### 18.4 レガシーテーブル14個の削除（D-39）

調査の結果、以下14テーブルがいずれも実DBで0件、現行コードから一切参照されていないことを確認した上で削除した：`production_records`・`lots`・`usage_daily`・`incoming_goods_log`・`stock_manual_adjustment`・`closing_runs`・`closing_wip_adjustment`・`audit_log`・`snapshot_batches`・`stock_snapshot`・`physical_count`・`board_definitions`・`component_bom`・`component_groups`。

`production_records`はdocstringに「廃止予定」と明記されていた。`board_definitions`・`component_bom`・`component_groups`は旧世代のBOM機能の名残で、現行コードにCREATE TABLE定義自体が存在しなかった。

重要な発見：`production_daily.lot_id REFERENCES lots(lot_id)`という外部キー宣言が存在したが、アプリの通常動作では`PRAGMA foreign_keys = ON`が設定されておらず、実際には強制されていなかった。`lots`テーブル自体に現行コードからのINSERTが無いため、この宣言は実質的に満たされ得ないものだった。`lots`削除によりこの宣言自体も解消された。

削除前にバックアップ（`inventory_app/db/inventory.db.bak_before_legacy_table_removal_<日時>`）を作成し、削除後に主要10画面（メインメニュー、生産実績入力、共通マスタ5画面、ロット進捗チェック、日々の引落一覧、実績レポート）が正常に動作することを確認した。`parts`・`final_products`は削除せず、マスタDB分離（18.3）の検討対象として維持している。

### 18.5 検証作業自体の重要な教訓

本作業の検証過程で2つの誤った報告が発生し、両方とも事後的に自分で誤りに気づいて訂正した。詳細はD-40（ロック解放漏れ、本ファイル §8）・D-41（文字化け出力の誤読、本ファイル §9）参照。

教訓：`MainWindow`を直接インスタンス化する検証では、`destroy()`ではなく正規の終了経路（`_on_app_close()`相当）を使うか、検証後に確実にロック解放の後始末を行うこと。また、文字化けした出力を根拠に断定的な報告をせず、正確な手段（エンコーディングを意識した取得方法等）で再確認してから報告すること。この教訓は、本ファイル §9「調査結果が確認できない場合の対応原則（D-18）」の「自分の検証結果も疑う」という原則の拡張として位置づけられる。

### 18.6 マスタDB分離の実装判明と、記録の後追い状態（2026-10-02、D-42〜D-43）

18.3でD-38として記録した「共通マスタの月次DB同居問題」は、記録時点（2026-10-01）では「調査のみ完了、分離は未着手」としていた。しかしその後、このマシン上で以下がコードとして実装済みであることが2026-10-02の調査で判明した：

- `config.py`に`MASTER_DB_PATH`（`APP_DATA_DIR/db/master.db`）が新設されている。
- `models/db_common.py`に、月次DB用`get_connection()`とは別の`get_master_connection()`が新設されている。
- `models/board_structure_master.py`・`models/parts_attributes.py`・`models/workers.py`・`models/master.py`の4モジュールが、全て`get_master_connection()`経由に切り替わっている（`workers.py`に`init_workers_table()`、`master.py`に`init_master_tables()`という遅延初期化関数も新設されている）。
- `tests/conftest.py`（新規）が、`autouse=True`のpytestフィクスチャで`config.APP_DATA_DIR`・`config.DB_PATH`・`config.MASTER_DB_PATH`の3つを一時ディレクトリへ隔離する仕組みを提供している。

隔離コピー上での動作確認（2026-10-02）：`master.db`が初回アクセス時に正しく自動作成されること、ログイン画面の作業者一覧が0件（`master.db`新規作成直後のため）で例外なく表示されること、作業者登録・構成基板数マスタCSVインポートが`master.db`へ正しく保存されること、`inventory.db`側が一切書き込まれないことを確認した。

**データ移行は未完了（D-42、未解決の課題）**：コードの切り替えが完了した一方で、`inventory.db`側に残っている既存データ（`board_structure_master`3119件・`workers`1件、他`parts_attributes`・`parts`・`final_products`は0件）は、`master.db`側へまだ移行されていない。このままアプリを実運用で起動すると、ログイン画面の作業者一覧・構成基板数マスタが実質的に空の状態から始まることになる。**「`inventory.db`側の3119件・1件を`master.db`へ移行するか、それとも初期化して新規に登録し直すか」は、技術的な判断ではなく業務上の判断を要する、別の未解決課題として記録する。** `inventory.db`側に残る5テーブル自体の削除（孤立データの後始末）も、このデータ移行の判断が決まるまでは実行していない。

**2026-10-02〜03追記（進捗）**：`inventory.db`側に残っていた孤立データ（本節の5テーブル）自体は、実DB行数0件・現行コードから一切参照されていないことを確認した上で削除済み（本ファイル §18.4・D-39参照。`board_structure_master`等の実データ自体は、削除前にバックアップへ退避済み）。`master.db`には、ログイン動作確認用のテスト作業者（`worker_id=W001`、氏名「テスト作業者」）を登録済み。**ただし、`board_structure_master`の実データ（3119件）そのものをCSV再インポート等で`master.db`側に再構築する作業は、依然として未着手のまま残っている（優先度の高い未対応タスク、本ファイル §19.7参照）。** データ移行の要否・方法自体の業務判断が必要という、本節で記録した論点は解消していない。

**記録が後追いになった経緯（D-43）**：D-38を記録した時点（2026-10-01）では「分離は未着手」が事実だったと考えられるが、その後、別セッション（または別のタイミング）でこのマシン上に分離の実装が加えられ、記録側（本ファイル）がこれに気づかないまま「未着手」の記述を維持し続けていた。2026-10-02の別件調査（レガシーテーブル削除後の動作確認）の過程で、このコードの食い違いに気づいた。

**教訓**：複数拠点・複数セッションでの並行作業では、一方の作業で実装が先行し、記録（本ファイル等のノート）への反映が後追いになることで、**記録と実際のコードの状態が一時的に食い違う状態が起こりうる**。本ファイル§5「環境間の整合性チェック手順」は、これまで「コードが古い状態に巻き戻っていないか」の検知を主眼に書かれていたが、今回の事例はその逆方向（**記録が古いまま、コードは先に進んでいた**）であり、同じ§5の定期的な整合性チェックの運用が、この種の「記録の後追い状態」の検知にも有効であることを再確認した。記録上「未着手」「保留」としている項目ほど、実際には既に別の場所で着手・完了していないかを、作業開始前に一度疑ってみる価値がある。

---

## 19. 手動バックアップ・マスタデータ取り込み機能、作業者管理のセキュリティ分離、.exe化（2026-10-02〜03、D-44〜D-49）

### 19.1 手動バックアップ機能（D-44）

`services/backup_service.py::backup_databases(destination_folder)`を新規実装した。`sqlite3.Connection.backup()`（SQLite公式のオンラインバックアップAPI）を採用し、`shutil.copy2()`等の単純なファイルコピーは使わない（書き込み中のDBファイルをコピーしても、ページ単位で整合性を保って完了できるため）。

マスタDB分離（D-38）後は、`config.DB_PATH`（月次DB）・`config.MASTER_DB_PATH`（マスタDB）の両方が揃って初めて完全な状態になるため、同一タイムスタンプ（バックアップ開始時刻）で1回の操作としてまとめてバックアップする。ファイル名（`inventory_backup_<timestamp>.db`・`master_backup_<timestamp>.db`）が保存先フォルダに既に存在する場合は、`_1`・`_2`...と連番を付与して重複しないファイル名にする。バックアップ対象のDBファイルがまだ存在しない場合（例：`master.db`を一度も作成していない）は、そのDBのみスキップし、空のDBを誤って作成しないようにした。

メインメニューのヘッダー行に「バックアップ」ボタンを配置し、既存の非同期パターン（`LoadingWindow`＋スレッド＋`queue.Queue`＋ポーリング、`on_create_database()`と同じ構造）でバックグラウンド実行する。

実装中、`_poll_backup_queue()`が`on_backup_databases()`のローカル変数（`destination_folder`）を参照しようとして`NameError`になる不具合を自己点検で発見し、インスタンス属性（`self._backup_destination_folder`）に保持する形に修正した。

### 19.2 バックアップ経由での月次DB共有の実証（新機能は不要と判明、D-45）

「バックアップファイルを他PCで取得・利用できるか」という問いに対し、既存の「共有フォルダのDBを開く」機能（`on_open_shared_database()`）を調査したところ、ファイル名に一切制約を持たない（`os.path.abspath(path)`をそのまま`config.set_db_path()`に渡すのみ）ことを確認した。隔離コピー上で、実際にバックアップファイル（`inventory_backup_<timestamp>.db`）をこの機能で開き、計画・実績データが正しく読み込めること、さらに実際の登録処理（`overwrite_daily_result()`）で書き込んだ値がバックアップファイル自身に反映され、元の（バックアップ前の）DBには影響しないことを実証した。ロック機構（`services/db_lock_service.py`）もファイルパス文字列のみに依存する設計であることをコードで確認した。

**これにより、バックアップファイルをそのまま本番DBとして使い始められることが分かり、新規機能の追加は不要と判断した。** ただし、複数PCでの同時アクセスという実機・実ネットワーク環境での検証はできていない（同一プロセス内での`acquire_lock()`の2回呼び出しによるシミュレーションに留まる）。

### 19.3 作業者管理のセキュリティ上の空白の発見と、登録画面・管理画面への分割（重要、D-46）

既存の`WorkerManagementWindow`を調査したところ、以下の空白が判明した：
- ログイン画面自体にパスワード認証が無い（以前から既知、`UI_WORKFLOW_FIXES_NOTES.md`グループS参照）。
- 加えて、**作業者管理画面自体がログイン不要で誰でも開け**（`ui/login_window.py`から直接開ける導線が存在）、画面内の全操作（新規登録・役割変更・有効/無効切替）に`role`（admin/operator）による制限が一切無かった。
- `role`列はデータとして存在するが、アクセス制御の目的では現行コードのどこからも参照されておらず、実質的に無意味な値だった。

対策として、作業者管理機能を2画面に分割した：
- **登録画面**（`ui/worker_registration_window.py::WorkerRegistrationWindow`、新設）：常時誰でも開ける（ログイン前・ログイン後どちらからも）。`models/workers.py::any_admin_exists()`でadmin役割の作業者が1人もいなければadmin選択肢を含め、1人以上いれば"operator"のみに制限する（readonlyコンボボックスのため選択自体が不可能）。
- **管理画面**（既存`WorkerManagementWindow`を再編）：admin役割でログイン中の場合のみメインメニューに表示する（operatorの場合はボタン自体を生成しない）。新規登録フォームを撤去し、既存作業者の編集・有効/無効切替専任とした。「有効」チェックボックスを廃止し、「選択行の有効/無効を切り替え」ボタンのみに統一した。

編集・切替の実行自体にも、`_require_admin()`による関数レベルのチェックを追加した。メインメニュー側のボタン非表示を迂回して画面を直接インスタンス化した場合でも、`current_worker.get("role") != "admin"`であれば処理を中断する（実際に隔離コピー上でこのバイパスシナリオを再現し、拒否されることを確認済み）。さらに、operator役割でこの画面を開いた場合はボタンをグレーアウト（`state="disabled"`）し、「この画面の操作にはadmin権限が必要です」という案内ラベルを表示する見た目の対策も追加した（ボタン無効化・関数内チェックの二重防御、どちらか一方がバイパスされても他方が機能する設計）。

### 19.4 マスタデータの「不足分のみ取り込み」機能（D-47）

複数PC間でadmin体制・マスタデータを揃える方法として、「現在のデータを正とし、バックアップ側は不足分のみ追加する」方針を採用した（上書き・完全マージは選ばない。既存データを保護する方向での決定）。

`services/master_merge_service.py::merge_master_from_backup(backup_file_path)`を新規実装した。5テーブル（`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`）それぞれについて、バックアップ側の主キー（`parts_attributes`のみ、宣言上のPRIMARY KEY`id`ではなく実質的な一意キーである`part_no`で照合）が現在の`master.db`に存在しない行だけを、既存のupsert関数（`upsert_board_structure()`・`upsert_parts_attributes()`・`upsert_worker()`・`upsert_part()`・`upsert_product()`）を再利用して追加する。既存の行は呼び出し自体をスキップするため、上書きは発生しない。

`workers`の取り込みでは、`role`をバックアップ側の値のまま追加する（これにより、他PCで既にadmin体制が整っていれば、そのadminをこのPCにも反映できる。既存のadmin・operatorの値には一切影響しない）。この取り込み操作もD-46と同じ基準でadmin限定とした（メニュー非表示＋関数内の二重チェック）。

隔離コピー上で、2つの独立したmaster.db（「現在」「バックアップ相当」）を用意し、片方にのみ存在する作業者（admin・operator各1名）・構成基板数マスタ1件が正しく追加されること、両方に同じ主キーがあり値が異なる場合に現在のDB側の値が維持されること（上書きされない）、同じバックアップファイルで再実行すると追加件数が0件になる冪等性を確認した。

### 19.5 .exe化のビルド実施（D-48）

`inventory_app.spec`（PyInstaller用設定ファイル）を新規作成し、実際に`dist/InventoryApp.exe`（約124MB）のビルドに成功した。`--onefile`形式を採用した（`config.py`のBASE_DIR算出部のコメントが最初からonefile形式、すなわち`sys.executable`基準を前提に書かれていたため、既存設計との整合性を優先した）。

Tesseract OCR・Poppler本体は同梱しない方針を確定した（PDF OCR機能はまだ十分な精度・再現性が得られていないため、今回のパッケージには含めない）。PDF読み取り機能のコード自体は通常通り同梱され、OCRが必要な操作時にTesseractが見つからない場合は、既存の例外処理（`except Exception`で捕捉し`messagebox.showerror()`で表示するのみ）によりアプリ全体はクラッシュしない設計であることをコードで確認した。

ログイン画面・メインメニューの起動、`config.APP_DATA_DIR`（`%LOCALAPPDATA%\InventoryApp\`）配下へのDBファイル（`master.db`・`inventory.db.lock`）の作成は実機（`pywinauto`での実クリック操作を含む）で確認済み。**一方、生産実績入力画面・共通マスタ5画面・バックアップ機能の実際のボタンクリックによる動作確認は、ttk製ウィジェットがWindowsの標準アクセシビリティAPI上で名前を持たないため自動化が技術的に困難で、完了していない。** これらの画面クラスが`main_window.py`のモジュール読み込み時にimportされており、exeが正常に起動した時点でimportの成功（依存関係が揃っていること）は確認できているが、実際の画面構築・動作そのものの確認は手動での確認待ちとして申し送る（本ファイル §19.7参照）。

`.gitignore`に`build/`・`dist/`を追加した（ビルド成果物であり、100MB超のバイナリをGit管理対象から除外するため）。

### 19.6 D-41の教訓が.exe化の検証でも再現した事例（D-49）

.exe化の動作確認中、「windowedビルド（`console=False`）が即座に無言で終了する」という事象を一度観測した。`sys.stdout`/`sys.stderr`がNoneになることが原因という仮説のもと、`main.py`に防御的な修正（Noneならダミーの書き込み先に差し替える）を加えた。

しかし後の再検証で、同じ無修正のビルドを正しい起動方法（直接のプロセス起動）で再テストしたところ、修正の有無に関わらず正常に起動することが判明した。**つまり、最初に観測した「クラッシュ」は実際のアプリの不具合ではなく、検証スクリプト自身の起動方法（`cmd.exe`経由の`start /B`）の問題だった。**

D-41（文字化けした出力を根拠に断定しない、自分自身の検証ツールの出力も疑う）と同種の教訓が、.exe化という全く別の作業文脈でも再現した事例として記録する。修正自体は無害（既知のPyInstaller windowed時の危険への一般的な予防策）であり、かつ良い防御的プログラミングであるため、コードには残しているが、「このクラッシュを修正した」という当初の自己評価は誤りだった。

### 19.7 未確認・申し送り事項（優先度の高い未着手タスク）

- **`board_structure_master`の実データ（3119件）の`master.db`への再構築**：CSV再インポート等による再構築は未着手のまま（本ファイル §18.6参照）。
- **.exeの手動動作確認**：生産実績入力画面・共通マスタ5画面・バックアップ機能・マスタデータ取り込み機能の実際のボタンクリックによる動作確認（§19.5参照）。本節§20のメインメニュー整理により画面構成が変わったため、.exeを再ビルドする際はこの変更後の構成で改めて確認が必要。
- `multiprocessing.freeze_support()`の実際の効果（OCRの並列処理を実際に実行する場面）は、exe化検証では一度も実行しておらず未確認。
- バックアップ・マスタデータ取り込み機能の複数PC間での実地検証（実際の共有フォルダ・ネットワーク越しのロック競合を含む）は未実施。

---

## 20. メインメニューの整理：共有フォルダ3ボタン廃止・「バックアップの呼び出し」新設・マスターデータ管理/マスターインポート削除・PDF読み取り/操作履歴のツール移動（2026-10-05、D-50〜D-52）

### 背景

共有フォルダ運用からローカル+バックアップ方式への転換（D-20）後も、メインメニューには転換前の設計を前提とした共有フォルダ直接アクセス用の3ボタンが残っていた。また、「4.マスターデータ管理」「5.マスターインポート」画面（`parts`・`final_products`テーブルのCRUD・CSV取込）が、D-4の訂正で「現役」と確認されていたものの、実際にこの2テーブルを読む機能がこの2画面以外に存在するかどうかは未確認のまま残っていた。これらを整理する作業の一環で実施した。

### 20.1 事前調査で発見した想定外の依存（D-52の教訓の実例）

削除対象として指示された`services/master_import_service.py`には、`import_parts_csv()`（マスタインポート専用）だけでなく、汎用CSVパーサ`parse_csv_generic()`（列名ゆらぎ対応）も定義されていた。この関数は`services/production_import_service.py`（実績CSV取込）からも`from services.master_import_service import parse_csv_generic`の形で再利用されていたため、ファイルを丸ごと削除すると実績CSV取込機能が壊れる状態だった。

事前の参照調査が`import_parts_csv()`という**特定の関数名**のgrepに留まっていたため、この依存を一度見落とした。対応として、`parse_csv_generic`・`_open_csv_with_fallback`・`_resolve_column_map`・`_ENCODINGS_TO_TRY`（内容は一切変更せず）を新規モジュール`services/csv_parsing_common.py`へ切り出し、`production_import_service.py`の`import`文・docstringの参照先を差し替えた上で、`master_import_service.py`を削除した。

以降、モジュール削除時の安全確認は「削除対象として指示された個々の関数」ではなく「モジュール名そのもの」でリポジトリ全体（テスト・`.spec`ファイル・ドキュメント内のコード例を含む）をgrepすることを原則とする（D-52）。

### 20.2 共有フォルダ3ボタンの廃止と「バックアップの呼び出し」新設（D-50）

**削除したもの**：
- `ui/main_window.py`のヘッダー3ボタン（共有フォルダのDB一覧／共有フォルダのDBを開く／共有フォルダに新規作成）と対応するハンドラ（`open_shared_db_list`・`on_open_shared_database`・`on_create_shared_database`・`_switch_to_shared_db`・`_shared_dialog_initial_dir`）。
- `ui/shared_db_list_window.py`（`SharedDbListWindow`）・`services/shared_db_scan_service.py`（`scan_shared_db_folders()`）・`services/app_settings_service.py`の`save_shared_db_root()`/`load_shared_db_root()`。いずれも他から参照されていないことをモジュール名でのgrepで確認した上で削除した。
- ロック機構（`services/db_lock_service.py`）・`_try_switch_db_path()`・ローカルDBの切り替え・新規作成（前月引き継ぎ含む）・削除機能は変更していない。

**旧D-45方針の問題点**：「共有フォルダのDBを開く」機能をバックアップの復元に転用する運用（D-45）には、①バックアップ原本そのものがそのまま本番DBになってしまう（コピーではなく原本を直接使い続ける）、②マスタDB（`master.db`）は復元されない（この機能が切り替えるのは`config.DB_PATH`のみ）、③D-20（ローカル+バックアップ方針への転換）と整合しない（共有フォルダへの直接アクセスを前提にした機能のため）、という3つの問題があった。

**新設した「バックアップの呼び出し」機能**：ヘッダーの「バックアップ」ボタンの隣に、権限制限なしで配置した。処理は`services/backup_service.py`に集約し、UIから分離した：

- `validate_monthly_db_backup(file_path)`：SQLiteとして開けるか・`PRAGMA integrity_check`が`"ok"`か・月次DB固有のテーブル（`kitting_plan_items`）を持ち、マスタDB固有のテーブル（`board_structure_master`・`parts_attributes`）を持たないか、を判定する（読み取り専用接続、`file:...?mode=ro`のURI指定で原本への書き込みを構造的に防ぐ）。マスタDBのバックアップ・SQLiteでないファイルはここで理由付きで拒否する。
- `restore_backup_as_new_local_db(backup_file_path, folder_name)`：`config.APP_DATA_DIR/db/<folder_name>/inventory.db`へ`sqlite3.Connection.backup()`でコピーする（原本は読み取り専用で開く）。
- `ui/main_window.py::on_restore_from_backup()`：ファイル選択→妥当性チェック→取り込み先フォルダ名の入力（既定値はバックアップ選択時刻から生成、`on_create_database()`と同じ非空・重複禁止の規則）→コピー→`_try_switch_db_path()`で切り替え→`init_kitting_plan_tables()`でテーブル構成を最新化、という流れ。失敗時（妥当性チェック不合格・コピー中の例外・ロック取得失敗）は作成途中のフォルダを削除し、現在のDB・ロックは変更しない。完了メッセージには「元のDBは『切り替え』で戻せる」「マスタデータは対象外（必要なら『マスタデータを他PCから取り込む』を使う）」を明記する。

マスタDB（`master.db`）はこの機能の対象外のまま（D-47の「不足分のみ取り込み」機能で別途対応する）。共有フォルダ上のDBを直接開く・削除する手段は、この整理により廃止された（ローカルDB一覧からの切り替え・削除は従来通り利用できる）。

### 20.3 「4.マスターデータ管理」「5.マスターインポート」の削除（D-4更新）

`ui/master_management.py`（`MasterManagementWindow`）・`ui/master_import_window.py`（`MasterImportWindow`）・`services/master_import_service.py`（`parse_csv_generic`切り出し後の残り＝`import_parts_csv()`等）を削除した。

**削除理由**：`models/master.py::get_all_parts()`/`get_all_products()`（`parts`・`final_products`を読む唯一の関数）の呼び出し元が、この2画面以外に存在しないことをリポジトリ全体のgrepで確認した。`parts`・`final_products`テーブル自体、および`models/master.py`のうち`services/master_merge_service.py`（「不足分のみ取り込み」機能、D-47）が使う関数（`upsert_part`・`upsert_product`・`init_master_tables`）は残置した。呼び出し元が無くなった`get_all_parts`・`get_all_products`・`delete_part`・`delete_product`・`upsert_part_master`（`master_import_service.py`専用）は`models/master.py`から削除した。

### 20.4 PDF読み取り・操作履歴のツール移動

「8. PDF読み取り（在庫照合）」「9. 操作履歴」を、月次データ列（右列）から共通マスタ列（左列）の末尾に新設した「ツール」見出し配下へ、番号を外して移動した（以降この2つに番号は付けない。右列「月次データ」の1〜7は変更なし。共通マスタ側の1〜3は4・5削除後も連番のまま変化なし）。

依存先DB（`config.DB_PATH`、`inventory_stock`・`operation_log`いずれも月次DB側のテーブル）は変わっておらず、月をまたいで使い回す共通マスタになったわけではない。配置は使用頻度の低い補助機能をまとめるという、レイアウト上の都合による例外であることを、`ui/main_window.py`のコード上のコメントに明記した。

### 20.5 schema.sqlに残っていた未使用テーブル定義の発見（D-51）

検証の過程で、`db/schema.sql`（`init_database_at()`が新規の月次DBを作る際に使う定義）に、マスタDB分離（D-38・D-42）以前の名残である`workers`テーブルの`CREATE TABLE IF NOT EXISTS`定義が今も残っていることが判明した。`models/workers.py`は既に`get_master_connection()`経由（`master.db`側）に切り替わっているため、このテーブルは使われないが、新規作成した月次DBには物理的に作成され続ける。

この事実は、`validate_monthly_db_backup()`の実装中に実際に問題を引き起こした：「`workers`テーブルの有無」を月次DB／マスタDBの判定材料に使うと、正しい月次DBのバックアップが誤ってマスタDBと判定され拒否される事象を検証で確認した。`schema.sql`には同様に`parts`・`final_products`・`lots`（`lots`はD-39で実DBからは削除済みだが、`schema.sql`側の定義は残っている）の定義も残っており、新規作成した月次DBには引き続きこれらの空テーブルが作られる。

`schema.sql`自体の整理（不要な`CREATE TABLE`定義の削除）は本タスクのスコープ外のため見送った。`validate_monthly_db_backup()`の判定では、`schema.sql`に定義が無く、マスタDB分離後は`master.db`側にしか作られない`board_structure_master`・`parts_attributes`のみをマスタDBの目印として使うことで、この問題を回避した。**`schema.sql`の整理自体は、優先度の高い未着手タスクとして別途記録する。**

### 20.6 動作確認

隔離環境（`config.DB_PATH`・`config.APP_DATA_DIR`・`config.MASTER_DB_PATH`の3つを一時ディレクトリへ隔離）で以下を確認した：

- バックアップの呼び出し：作成→検証→取り込み→切り替えの一連の流れで、取り込み先の`kitting_plan_items`件数が元と一致すること、呼び出し前後でバックアップファイルのハッシュが一致すること（原本不変）、元のDBファイルがそのまま残ること。
- 拒否ケース3種（マスタDBのバックアップ・SQLiteでないファイル・重複フォルダ名）がいずれも理由付きで拒否され、フォルダが新規作成されないこと。
- 失敗時（存在しないソースファイルを指定）に例外が発生し、呼び出し元の後始末ロジックでフォルダが残らないこと、現在のDBが変更されないこと。
- ロック：新DBのロックを取得できること、旧DBのロックが解放されること、失敗時（他者使用中）は元のロックが維持されること。
- 「不足分のみ取り込み」機能（D-47）が従来通り動作し、既存レコードが上書きされないこと。
- 実績CSV取込が使う`parse_csv_generic()`（`csv_parsing_common.py`切り出し後）が、cp932エンコーディング・列名ゆらぎ（「ロットNo」「機種基板名」「数量」「払い出し日」）を含むCSVを移動前と同じ結果で解析できること。
- admin・operatorの両ロールで`MainWindow`が例外なく起動し、想定する全ハンドラ（`open_*`・`on_*`）が存在すること。正規の終了経路（`_on_app_close()`、D-40）で後始末した。
- ウィンドウの高さ（`winfo_reqheight()`、`update_idletasks()`後）が`940x600`の600px以内に収まること（admin:564px、operator:557px）。幅（`reqwidth=986px`）は本タスクの変更前から既に940pxを超えていたことをgit上の変更前コードとの比較で確認済みであり、本タスクが対象としていない「データベース選択」行に起因する**既存の問題**であるため、本タスクでは変更していない（高さのみを対象とする指示だったため）。
- `pkgutil.walk_packages()`による`ui`/`models`/`services`限定のスコープ付きimportチェック（D-13）・既存の`pytest`（1 passed）に影響が無いこと。
- 削除したモジュール名・関数名（`master_import_service`・`master_management`・`master_import_window`・`shared_db_scan_service`・`shared_db_list_window`・`save_shared_db_root`・`load_shared_db_root`・`get_all_parts`・`get_all_products`・`delete_part`・`delete_product`・`upsert_part_master`等）への参照がリポジトリ全体に残っていないことをgrepで確認した。

### 20.7 未確認・申し送り事項

- `schema.sql`に残る未使用テーブル定義（`workers`・`parts`・`final_products`・`lots`）の整理（§20.5、§20.8でも再度言及）。
- .exeは未再ビルド。本変更（メインメニューの画面構成変化）を反映するには再ビルドが必要（D-48関連）。
- ウィンドウ幅（`reqwidth=986px`）が`940px`を超える既存の問題（「データベース選択」行起因、本タスクでは未対応。§20.9で再確認）。

### 20.8 バックアップ呼び出しの月次DB判定を、分離前の月次DBも受理できるよう修正（2026-10-05追記、D-53）

#### 事実確認：分離前の月次DBにも5テーブルが同居していることをgit履歴・実ファイルで確認

`models/board_structure_master.py`・`models/parts_attributes.py`・`models/workers.py`・`models/master.py`のgit履歴を確認したところ、マスタDB分離（D-38・D-42、2026-10-02頃）より前のコミットでは、いずれも`models.db_common.get_connection()`（月次DB、`config.DB_PATH`）を使っていた（分離後は`get_master_connection()`、`config.MASTER_DB_PATH`）。

さらに、リポジトリに実在する分離直前の実バックアップファイル（`inventory_app/db/inventory.db.bak_before_master_tables_removal_20261002_084718`）を読み取り専用で確認したところ、`kitting_plan_items`・`production_daily`等の月次データと、`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`の5テーブルが**同一ファイルに同居**していることを直接確認した。さらに古い実バックアップ（`inventory_backup_before_migration_005_20260820_144033.db`、2026-08-20）でも同様に`workers`・`parts`・`final_products`・`lots`（当時の第一世代設計の名残、D-39参照）が月次データと同居していた。

D-50で実装した`validate_monthly_db_backup()`（マスタDB固有テーブルがあれば拒否する否定的な判定）に、この分離前の実バックアップファイルを渡すと、実際に「これはマスタDBのバックアップのようです」と誤判定され拒否されることを確認した。

#### 修正：肯定的な判定（月次DB固有テーブルの存在）へ変更

判定方式を、「マスタDB固有テーブルがあれば拒否」から「月次DB固有テーブル（`kitting_plan_items`）があれば受理」に変更した（D-53）。

マーカーに`kitting_plan_items`を選んだ根拠：
- `db/schema.sql`には定義が無く、`models.kitting_plan.init_kitting_plan_tables()`（`get_connection()`＝月次DB経由）でのみ作成されるため、`master.db`には絶対に存在しない。
- `init_kitting_plan_tables()`は、`init_database_at()`で新規の月次DBを作成した直後に必ず呼ばれる運用（`ui/main_window.py::on_create_database()`・`on_restore_from_backup()`等）になっているため、分離の前後を問わず、月次DBとして運用されたことのあるファイルには常にこのテーブルが存在する。

マスタDB固有テーブル（`board_structure_master`・`parts_attributes`）の存在は、拒否条件からは外したが、「月次DB固有テーブルが無く、かつこれらを持つ」場合に限り「マスタDBのバックアップのようです」という具体的な拒否理由を出すためのヒントとして残した。

#### 分離前の月次DBを取り込んだ場合の挙動（重要）

分離前の月次DBのバックアップを「バックアップの呼び出し」で取り込んでも、同居している旧マスタテーブル（`board_structure_master`・`workers`等）の中身は、現行コードからは一切読まれない。理由：`models/board_structure_master.py`等は既に`get_master_connection()`（ローカル固定の`config.MASTER_DB_PATH`）経由に切り替わっており、取り込んだ月次DBファイル（`config.DB_PATH`側）内の同名テーブルを参照する経路はコード上存在しないため。

検証（隔離環境、実バックアップファイルを使用）：取り込んだ旧DB内の`workers`・`board_structure_master`に判別可能な値（`OLD_W001`・`OLD-EMBEDDED-BOARD`）を仕込んだ上で取り込み、`models.workers.get_all_workers()`・`models.board_structure_master.get_board_structure()`のいずれからもこれらの値が一切見えないこと、取り込み後もローカルの`master.db`の`workers`・`board_structure_master`テーブルが空のまま（旧DBのデータが紛れ込んでいない）ことを確認した。

旧DBに同居していたマスタ的なデータを実際に活用したい場合（例：旧DBの`workers`に他では登録されていない作業者がいた等）、本機能では対応しない。その場合は、旧DBのファイルから別途バックアップ相当のファイルを作り、既存の「マスタデータを他PCから取り込む」（`merge_master_from_backup()`、D-47）機能に渡すことで、`board_structure_master`・`parts_attributes`・`workers`・`parts`・`final_products`の不足分のみ`master.db`へ追加できる可能性がある（`merge_master_from_backup()`は引数のファイルから直接これら5テーブルをSELECTするだけで、月次DBかマスタDBかを判定しないため、月次DBファイルを渡しても技術的には動作する）。ただし、この用途は今回の指示・検証の対象外であり、動作確認はしていない。

#### 動作確認（隔離環境、実バックアップファイルを使用）

- 分離前スキーマ（5テーブル同居）の実バックアップファイルを月次DBとして正しく受理すること。
- 呼び出し（`restore_backup_as_new_local_db()`）が例外なく完了し、原本（分離前バックアップファイル）が変更されないこと。
- 取り込み後、主要画面（生産計画読込・生産実績入力・ロット進捗チェック・日報/月報（`UnifiedReportWindow`）・操作履歴）がいずれもエラーなく開くこと。
- 取り込み直後の月次DBに存在しなかったテーブル（`operation_log`・`lot_status_history`等、分離前バックアップの時点ではまだ実装されていなかった機能のテーブル）が、各画面を開いた際の遅延初期化（`CREATE TABLE IF NOT EXISTS`）により補完されること。
- 同居している旧マスタテーブルの中身（仕込んだ`OLD_W001`・`OLD-EMBEDDED-BOARD`）が現行コードから一切見えないこと、取り込み後もローカルの`master.db`の該当テーブルが空のままであること（バイト単位の完全一致は、`master.db`自身の遅延初期化＝新しいテーブルの追加により変化するため参考情報に留め、実際のレコード内容を判定基準とした）。
- マスタDBのバックアップ・SQLiteでないファイルの拒否が、判定方式の変更後も引き続き成立すること（回帰確認）。
- 既存の`pytest`（1 passed）・スコープ付きimportチェック（`ui`/`models`/`services`）・D-50の検証26項目に影響が無いこと。

#### 教訓

D-50実装時、新規作成した月次DB（schema.sqlの未整理に起因する`workers`テーブルの残存）だけを使って検証していたため、「分離前に実際に運用されていた、5テーブルが本当に同居する月次DB」という、より重要なケースでの動作確認が漏れていた。否定的な判定（「〜があれば拒否」）は、想定していなかった正常な状態（分離前の月次DB）まで誤って拒否してしまうリスクを持ちやすく、肯定的な判定（「〜があれば受理」）の方が、このアプリの運用実態（古い月次DBのバックアップも将来使われうる）に対して安全側に働くことを確認した事例として記録する。

### 20.9 未確認・申し送り事項（追記）

- `schema.sql`に残る未使用テーブル定義（`workers`・`parts`・`final_products`・`lots`）の整理は、本追記時点でも引き続き未着手（§20.5）。
- ウィンドウ幅（`reqwidth=986px`）が`940px`を超える件は、実機での目視確認がまだ行われていない（「データベース選択」行のレイアウトが、940px幅のウィンドウで実際にどう見えるか・操作に支障が無いかの確認待ち）。

---

## 21. 画面の中央表示・DB未選択（既定DB）状態の操作禁止・在庫値出力済みDBの警告（2026-10-05、D-54〜D-58）

### 21.1 画面の中央表示（D-54・D-55）

事前調査（前回セッション）で「全29クラス＋関数内ダイアログ11か所、計40か所のうち`notice`トースト（`ui/production_import_staging_window.py`、対象外）を除く39か所が全て`geometry("WxH")`で固定サイズ指定済み」という前提を確認したつもりだったが、実装着手前に全箇所のコードを直接確認したところ、**関数内ダイアログ11か所のうち4か所（`ui/ng_input_window.py`の「対象外にする理由」・「実装ラインの選択」、`ui/kitting_production_entry.py`の「登録内容の確認」、`ui/wip_expansion_window.py`の「対象外にする理由」）には`geometry()`呼び出しが無く、パックしたウィジェットの内容に応じた自動サイズのままだった**ことが判明した（D-55）。前回調査の前提が誤りだったことになる。

この発見を受けて着手を一度停止し、`center_window(window, parent=None)`（`ui/window_utils.py`、新規）の仕様を「サイズ指定済みの画面はその幅・高さを使う、未指定の画面は`update_idletasks()`後の自動計算サイズを使う（サイズ自体は変更しない）」という形に確定した上で実装した（D-54）。

**実装の要点**：
- `window.withdraw()`→（`parent`が無い・現在表示されていない`withdrawn`/`iconic`状態でなければ`parent`の中央、それ以外はディスプレイ中央の座標を計算）→`window.geometry(f"{w}x{h}+{x}+{y}")`→`window.deiconify()`、という順で実行する。ちらつき防止（配置完了まで非表示にし、位置決定後に表示する）を`center_window()`内部で完結させているため、呼び出し元のコードは「ウィジェットを配置し終えた後に1行呼ぶだけ」で済む。
- 当初は「サイズ指定済みなら`winfo_width()`、未指定なら`winfo_reqwidth()`」という分岐を想定していたが、実機検証の結果、`update_idletasks()`後は`winfo_width()`がどちらの場合も正しい値（指定済みならその値、未指定なら内容に応じた自動計算値と同じ値）を返すことを確認したため、分岐を設けずに済んだ。
- 画面外にはみ出す場合は、親ではなく**画面全体（`winfo_screenwidth()`/`winfo_screenheight()`）基準でクランプする**（親より大きいウィンドウを親の中心に配置しようとすると負の座標になりうるケースへの対応）。
- 全40か所中、`notice`トースト（一時的な非ボーダー通知、3秒で自動`destroy()`）のみ対象外とし、残り39か所（29クラス＋関数内10か所）全てに適用した。
- 既存の`_open_singleton_window()`（既に開いている画面を`lift()`/`focus_force()`で前面に出すだけの多重表示防止の仕組み）は、`center_window()`が各画面の`__init__`内で1回しか呼ばれない設計のため、再表示時に位置が動かないことを確認済み。

### 21.2 DB未選択（既定DB）状態の操作禁止（D-56・D-57）

#### 確定した方針

既定DB（`config.APP_DATA_DIR/db/inventory.db`、`on_switch_database()`・`on_create_database()`・`on_restore_from_backup()`が組み立てる`APP_DATA_DIR/db/<フォルダ名>/inventory.db`より1階層浅い、モジュール読み込み時点のデフォルトパス）を、**ファイルの有無・データの有無を問わず一律「未選択」として扱う**方針を確定した。

**理由**：名前の付いていない既定DBに、利用者が気づかないまま入力・CSV取込を行ってしまうリスクがあった（どの月の作業データか特定できない暗黙のDBが、意図せず実質的な作業領域になってしまう）。

**既定DBに残っていたデータの扱い**：開発環境でこのマシンの既定DB（`inventory_app/db/inventory.db`）には実際にテストデータ（`kitting_plan_items`2740件等）が入っていることを確認したが、**これはテストデータであり、既定DB専用の移行機能は実装しない**（ユーザー判断）。既定DBのファイル自体はアプリからは一切読み書きされなくなるため、**今後アプリから到達不能な状態のまま残る**（削除する場合は手動で行う。実環境の`db/inventory.db`は本タスクでは削除・変更していない）。

#### 判定方法（D-56）

```python
# config.py
def is_default_db() -> bool:
    return os.path.basename(os.path.dirname(DB_PATH)) == DB_ROOT_FOLDER_NAME  # "db"
```

フォルダ名付きDBは必ず`APP_DATA_DIR/db/<folder>/inventory.db`という1階層深い形でパスが組み立てられるため、親フォルダ名が`"db"`そのものになるのは既定DBのみ、という**パスの深さの違いだけで判定でき、追加の状態保持は不要**。この判定と衝突しないよう、`"db"`という名前のフォルダは新規作成（`on_create_database()`）・バックアップの呼び出し（`on_restore_from_backup()`）の両方で予約語として拒否する。

#### 無効化の仕組み（既存の引き継ぎ中無効化との独立性）

`ui/main_window.py`に、既存の`_menu_widgets`（`_set_menu_enabled()`が管理、`carry_over_incomplete_lots()`実行中の一括無効化に使う）とは**別に**`_default_db_locked_widgets`（既定DBの間に無効化する業務ボタン：月次データ1〜7・共通マスタ・ツール・バックアップ・マスタ取込）を新設した。DB選択・切り替え・新規作成・ローカルDB削除・バックアップの呼び出し・ログアウトは対象外（常に操作できる）。「前月から未完了分を引き継ぐ」チェックボックスは、既定DBの間は無効化し、チェック済みの状態も強制的に解除する（既定DBを引き継ぎ元にした不定な動作を防ぐため）。

2つのゲート（`self._menu_enabled`・`config.is_default_db()`）は、いずれも「Trueなら制限なし」側で統一して持ち、`_apply_widget_states()`が両方をANDで合成して最終的な`state`を決める。これにより、一方の解除（例：`carry_over_incomplete_lots()`完了による`_menu_enabled=True`化）が、既定DBロックで無効のままにすべきボタンまで誤って有効化してしまうことがない（逆方向も同様）ことを、隔離環境での検証（引き継ぎ中の無効化→解除、既定DBロック→解除の4パターン全ての組み合わせ）で確認した。

#### 発見・修正した実装上の見落とし（D-57）

実装中、「既定DBは一切読み書きしない」という前提を破る見落としを発見した。在庫値出力済み判定（§21.3、`models.operation_log.get_inventory_diff_export_status()`）は内部で`init_operation_log_table()`（`CREATE TABLE IF NOT EXISTS`）を呼ぶため、`sqlite3.connect()`が**存在しない既定DBファイルを新規作成してしまう**ことが判明した。隔離環境での検証で、空の環境で起動しただけで既定DBファイルが作られてしまう事象、実データ入りの既定DBファイルのハッシュが起動〜正規終了で変化してしまう事象の両方を実際に確認した。

`ui/main_window.py::_update_current_db_label()`・`_confirm_proceed_despite_exported_db()`の両方に「既定DBの間は在庫値出力済み判定自体を呼ばない」ガードを追加して解消した。**教訓**：「既定DBは読み書きしない」という方針は、既定DB判定そのものの実装だけでなく、既定DBの状態に応じて分岐する機能全てに同じ注意が必要になる横断的な制約であり、機能を1つ追加するたびに見落としがちである。D-18（確認できない状況で推測で断定しない）とは別の種類の教訓だが、「既定DBに関する変更は、新しいDB依存機能を追加するたびに再確認が必要」という点で、今後の開発でも意識すべき事項として記録する。

### 21.3 在庫値出力済みDBの警告（D-58）

#### 確定した方針

在庫値出力（`ui/inventory_diff_window.py::InventoryDiffWindow`、メインメニュー「7. 在庫値出力」）の完了を`operation_log`に記録し、出力済みのDBに対する以降の操作に警告を出す。**禁止ではなく警告とした**理由は、出力後に誤りが見つかった場合の修正・再出力を妨げないため（在庫差異レポート自体が読み取り専用・何度でも再実行可能な設計であることと整合する）。

#### 実装

- `models/operation_log.py`に`OPERATION_NAME_INVENTORY_DIFF_EXPORT = "在庫値出力"`・`get_latest_operation_timestamp(operation_name)`・`get_inventory_diff_export_status()`を新設。`get_latest_operation_timestamp()`は`init_operation_log_table()`を呼んでからSELECTするため、`operation_log`テーブルが存在しない古いDB（分離前の月次DBを「バックアップの呼び出し」で取り込んだ場合等）でも例外にならない。
- `InventoryDiffWindow.on_export_pdf()`/`on_export_csv()`の**成功時のみ**（保存ダイアログのキャンセル・出力失敗時は記録しない）`log_operation()`を呼ぶ。完了メッセージにも「次月分は新しいデータベースを作成して入力してください」という案内を追加した。
- メインメニューのヘッダー直下（現在DBラベルの近く）に、出力済みの場合のみ目立つ色（赤系）で「在庫値出力済み（最終出力：…）」を常時表示する警告ラベルを新設。`pack(before=self._body_frame)`で、表示/非表示を繰り返しても常に同じ位置（現在DBラベルの直下）に挿入されるようにしている。
- 月次データ1〜6（7の在庫値出力自体は対象外）の各画面を開く直前に`_confirm_proceed_despite_exported_db()`を呼び、出力済みなら確認ダイアログ（「いいえ」なら画面を開かない）を表示する。日報・月報等の閲覧系・ツール・共通マスタ・DB管理はいずれも対象外。

#### 引き継ぎ・呼び出しとの関係（確認済み）

- **前月引き継ぎ**（`services/db_migration_carryover.py::carry_over_incomplete_lots()`）は`operation_log`テーブル自体をコピー対象に含めていない（計画・実績データのみをコピーする既存の設計、本機能のための変更は不要だった）ため、引き継ぎ先の新DBは出力済みの記録を持ち越さず、未出力の状態で始まることを隔離環境で確認した。
- **バックアップの呼び出し**（`services/backup_service.py::restore_backup_as_new_local_db()`）は`sqlite3.Connection.backup()`によるファイル全体のページ単位コピーのため、`operation_log`を含め元のDBの状態がそのまま保持される。出力済みDBをバックアップ→呼び出しで取り込んだ場合、取り込んだ新DBも出力済みのまま（最終出力日時も保持）であることを隔離環境で確認した。

#### 既知の限界

本機能より前に在庫値出力を行っていたDBは、記録の仕組み自体が無かったため「出力済み」と判定されない。今後、この機能の導入以降の運用で解消される。

### 21.4 動作確認

隔離環境（`config.DB_PATH`・`config.APP_DATA_DIR`・`config.MASTER_DB_PATH`の3つを一時ディレクトリへ隔離）で、以下を確認した（計54項目、全て合格）。

- **中央表示**：サイズ指定済み・未指定それぞれの画面が親（または画面、`parent`省略時）の中央に配置されること（許容誤差3px）、画面より大きいウィンドウは画面内（0,0起点）にクランプされること、再表示（`lift()`/`focus_force()`）で位置が動かないこと、自動サイズの4ダイアログが幅・高さを変えずに中央寄せされ内容が切れないこと、実画面（`OperationLogWindow`・`LotProgressWindow`、および自動サイズの実ダイアログ3種）で実際に確認できたこと。
- **未選択**：空の環境・実データ入りの既定DBいずれでも起動すると「未選択」と判定され業務ボタンが無効になること、DB管理ボタン・ログアウトは有効のままであること、起動〜正規終了を通じて既定DBファイルが作成・変更されないこと（ハッシュ一致で確認）、フォルダ名付きDBへの切り替え・新規作成後に業務ボタンが有効化されること、現在接続中のDBの削除は従来通り拒否されること、前回DBのフォルダが外部で削除・移動された状態でも例外なく起動し「未選択」になること、"db"という名前のフォルダが新規作成・バックアップの呼び出しの両方で拒否されること、引き継ぎ中の無効化と既定DBロックの無効化が相互に干渉しないこと（4パターンの組み合わせ）。
- **出力済み**：出力前は警告が無いこと、PDF・CSVそれぞれの出力成功後に記録とメインメニュー表示が行われること、保存ダイアログのキャンセル時は記録されないこと、月次データ1〜6を開く際の確認ダイアログで「いいえ」なら開かず「はい」なら開くこと、在庫値出力自体にはこの確認ダイアログが出ないこと、`operation_log`テーブルの無い古いDBでも例外にならないこと、前月引き継ぎ先のDBは未出力のまま・バックアップの呼び出しで取り込んだDBは出力済み状態を保つこと。
- **回帰**：admin・operatorの両ロールで例外なく起動し高さが600px以内に収まること、前回セッションのD-50〜D-53検証（26項目）・D-53追加検証（15項目）がいずれも引き続き合格すること、既存`pytest`（1 passed）・スコープ付きimportチェック（`ui`/`models`/`services`）に影響が無いこと、実環境の`inventory_app/db/inventory.db`（開発環境の既定DB）が本タスクの検証を通じて一切変更されていないこと（行数・ファイル更新時刻で確認）。

### 21.5 未対応・申し送り事項

- D-19（画面を開いたままDBを切り替えた場合、既存画面の操作対象が気づかないうちに新しいDBに変わる未解決の設計課題）は、本タスクのスコープ外のため従来通り未対応のまま。
- .exeは未再ビルド。本変更（画面中央寄せ・既定DB無効化・在庫値出力警告）を反映するには再ビルドが必要（D-48関連）。
- §20.5（`schema.sql`の未使用テーブル定義整理）・ウィンドウ幅986pxの実機目視確認（§20.9）は、本タスクでも引き続き未対応。

---

## 22. 未選択状態でのロック処理の停止と、ロックファイルのgit除外（2026-10-05、D-59〜D-60）

### 22.1 背景：実環境のロックファイルが消えた事故

D-56〜D-58（画面の中央表示・既定DB無効化・在庫値出力警告）の実装・検証作業中に、実環境の`inventory_app/db/inventory.db.lock`（git管理下、`worker_name: "テスト作業者"`、2026-10-02取得の古い記録）が意図せず削除される事故が発生した。`services/db_lock_service.py::release_lock()`は、そのプロセス内メモリ（`_owned_tokens`）が記憶するトークンと一致する場合のみファイルを削除する設計のため、本セッション中のどこかで実環境の既定DBパスに対して`acquire_lock()`→`release_lock()`が実行された形跡だった。

事後調査の結果、**原因は特定できなかった**。本セッションで保存していた検証スクリプト4本（`verify_menu_reorg.py`・`verify_pre_separation_restore.py`・`verify_phase123.py`・`verify_phase3.py`）を全て読み直し、`config.APP_DATA_DIR`・`config.DB_PATH`・`config.MASTER_DB_PATH`の3パス差し替えが、`MainWindow`の生成・`db_lock_service`の関数呼び出しより常に先行していることを確認したが、隔離漏れは見つからなかった。該当ロックは発見時点で3日前（2026-10-02）の記録であり、`LOCK_STALE_SECONDS`（30分）を大幅に超過していたため、`acquire_lock()`の経過時間判定により、誰が・どの`worker_name`/`pc_name`で呼んでも確認なしに即座に成功し得る状態だった。実環境の`git log`を調べたところ、この`.lock`ファイルは2026-09-04〜10-03の約1か月間にAdd/Delete（上書き・削除）を繰り返すパターンで**合計9回**コミットに混入していたことが判明し、本セッションでの削除はこの既存パターンの最新の1回に過ぎなかったとみられる（人間の利用者や他セッションによる通常のログイン/ログアウト操作でも同じ現象が起こり得るため、原因を本セッションの操作だけに限定する根拠は無い）。

事後、`git checkout -- inventory_app/db/inventory.db.lock`で復元済み。

### 22.2 未選択（既定DB）の間はロックにも触れない（D-59）

D-56・D-57で確定した「既定DBには一切のファイル読み書きを行わない」方針は、策定当初**DB本体（`sqlite3.connect()`経由のアクセス）のみ**を対象にしており、ロックファイル（JSON、`services/db_lock_service.py`が読み書きするDB本体とは別ファイル）は対象に含めていなかった。

`ui/main_window.py::MainWindow.__init__()`は、既定DBかどうかを問わず無条件に`acquire_lock()`を呼んでいたため、既定DBパスに他者の有効なロックが残っていると、「未選択」の状態にすら到達できず起動自体が拒否されてしまっていた。これは、既定DBは「名前の無いDBへの気づかない入力を防ぐ」ためのものであり、起動そのものを妨げる意図は無かったという方針の趣旨に反する動作だった。

**事前確認（§0相当）**：ロックを保持していることを前提にした既存処理（ハートビート`_heartbeat()`・切り替え`_try_switch_db_path()`・新規作成`on_create_database()`・引き継ぎ（lock取得は`on_create_database()`内で新DBに対して同期的に行われ、非同期の引き継ぎスレッド自体はロックに触れない）・終了`_on_app_close()`・ログアウト`on_logout()`）を全て確認したところ、いずれも`self._lock_acquired`（ロック保持の有無をMainWindow自身が明示的に保持するブール値、D-40で既に導入済み）でガードされた`_release_current_lock()`・`_heartbeat()`経由で呼ばれており、「ロック未保持」を安全に扱えない箇所は見つからなかった。

**実装**：`MainWindow.__init__()`に`if not config.is_default_db():`の分岐を追加し、既定DBの間は`acquire_lock()`自体を呼ばないようにした（`self._lock_acquired`は`__init__()`冒頭で元々`False`に初期化されているため、この分岐をスキップするだけで済む）。既存の`self._lock_acquired`ガードにより、ハートビート・終了・ログアウト・切り替え・新規作成・引き継ぎの全経路が「ロック未保持なら何もしない」という振る舞いに自然に追随した。`services/db_lock_service.py`自体は変更していない。フォルダ名付きDBでのロックの挙動（取得・ハートビート・切り替え時の付け替え・失敗時の維持・破損時の処理）も変更していない。

### 22.3 ロックファイルのgit除外（D-60）

`.gitignore`の`*.db`パターンは拡張子が`.lock`のファイル（`<DBファイル名>.lock`）には一致しないため、このファイルを除外できていなかった。`.gitignore`に`*.db.lock`を追加し、`git rm --cached inventory_app/db/inventory.db.lock`で追跡対象から外した（ファイル自体は削除していない）。

`db/`配下には同様に`.gitignore`で除外できていない`.bak_*`バックアップファイル（`inventory.db.bak_before_delete_20260924_110042`等、計6件）が存在するが、これらの追跡状態・ファイルとも本タスクでは一切変更していない（扱いは別タスクとして判断する）。

### 22.4 動作確認

隔離環境（`config.DB_PATH`・`config.APP_DATA_DIR`・`config.MASTER_DB_PATH`の3つを一時ディレクトリへ隔離）で、以下を確認した（計40項目、全て合格）。

- 未選択で起動→ハートビート相当の実行→正規終了の間、既定DBの`.lock`が一度も作成されないこと。
- 既定DBパスに別の作業者・別PC名の新しい（30分以内）ロックファイルが存在する状態でも、未選択として例外なく起動できること。そのロックファイルの内容が起動・ハートビート・正規終了の前後で一切変更・削除されないこと。
- 未選択→新規作成・切り替え（ローカルDB一覧経由）・バックアップの呼び出しのそれぞれで、新DBのロックを正しく取得し（`self._lock_acquired is True`）、既定DBの`.lock`には一切触れないこと。
- 未選択→切り替えに失敗（新DBが他者にロック中）した場合、`config.DB_PATH`・`self._lock_acquired`とも変化せず、既定DBの`.lock`も作られないまま、未選択の状態に安全に残ること。
- フォルダ名付きDB同士の切り替え・他者使用中DBへの切り替え失敗時のロック維持・終了時の解放が、いずれも従来どおり機能すること（回帰確認）。
- 前回DBのフォルダが外部で消えていて未選択で起動する場合も、同様にロックへ一切触れないこと。
- 既存の検証（D-50〜D-58、計95項目：26+15+37+17）・既存`pytest`（1 passed）・スコープ付きimportチェック（`ui`/`models`/`services`）に影響が無いこと。
- `git status`で`inventory_app/db/inventory.db.lock`が追跡対象から外れ（ステージ上は削除、ファイルは存在、再度未追跡としても現れない）、実環境の`db/inventory.db`本体・ロックファイルの中身がいずれも本タスクの作業前後で変化していないこと。

### 22.5 未対応・申し送り事項

- `db/`配下の`.bak_*`ファイル6件のgit管理の扱いは未着手（別タスク）。
- 実環境のロックファイル消失の根本原因は特定できなかった（§22.1参照）。今後同様の事象が発生した場合に備え、`.gitignore`への`*.db.lock`追加（D-60）により、少なくとも「誤ってコミットに混入する」副作用は解消された。

## 23. 中央表示の基準と計算方法の修正：クライアント領域と外枠の取り違え、作業領域基準への変更（2026-10-05、D-61）

### 23.1 報告された症状と調査方針

D-54で全画面に適用した中央寄せについて、「画面がディスプレイ中央より若干下に表示される」という報告を受けた。調査は、(1) 旧`center_window()`が幅・高さ・位置の算出にどの値を使っていたか、(2) タイトルバー・枠が計算に含まれているか、(3) Windowsの表示拡大（125%・150%等）の影響、(4) 実際の余白の差の実測、の4点で行った。

### 23.2 原因1：クライアント領域と外枠の取り違え

旧実装（D-54）は`winfo_width()`/`winfo_height()`で幅・高さを、`winfo_screenwidth()`/`winfo_screenheight()`（またはparent中央の場合は`winfo_rootx()`/`winfo_rooty()`+`winfo_width()`/`winfo_height()`）で中央座標を算出し、そのまま`geometry("WxH+X+Y")`に渡していた。

実機で`ctypes.windll.user32.GetWindowRect()`と比較したところ、`winfo_width()`/`winfo_height()`・`winfo_rootx()`/`winfo_rooty()`はいずれもタイトルバー・枠を除いた「クライアント領域」の大きさ・位置を返すのに対し、`geometry()`の`+X+Y`部分はタイトルバー・枠を含む「外枠」の位置を指定する仕様であることを確認した。すなわち、クライアント領域基準で計算した値をそのまま外枠位置として渡していたため、タイトルバーの高さ（実測約31px）・左右の枠（実測約8px）の分だけ、意図した中央より右下にずれていた（特に高さはタイトルバーの影響で目立つ）。

### 23.3 原因2：ディスプレイ全体と作業領域の取り違え

`winfo_screenwidth()`/`winfo_screenheight()`はタスクバーを含むディスプレイ全体の解像度であり、タスクバー分を除いた「作業領域」ではなかった。タスクバーの高さだけ、実際に使える領域の中央とディスプレイ全体の中央がずれる。

### 23.4 対策：ctypes経由のWindows API直接呼び出し

追加の外部ライブラリを使わず、標準ライブラリの`ctypes`のみで以下を取得する設計に変更した（`ui/window_utils.py`を全面的に書き直し）。

- **外枠サイズ**：`GetAncestor(hwnd, GA_ROOT)`でTkの`winfo_id()`から実際のOSトップレベルウィンドウのHWNDを求め、`GetWindowRect()`で外枠の矩形を取得する。
- **作業領域**：`MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)`でモニターハンドルを求め、`GetMonitorInfoW()`の`rcWork`（タスクバー等を除いた作業領域）を使う。

いずれもAPI呼び出しが失敗した場合（Windows以外の環境等）は、従来相当の近似値（`winfo_width/height()`・`winfo_screenwidth/height()`）にフォールパックし、例外を発生させない。`withdraw()`で非表示状態のウィンドウに対しても、これらのAPIがいずれも正しい値を返すことを実機確認済みであり、既存のちらつき防止設計（`withdraw()`→位置決定→`deiconify()`）はそのまま維持した。

### 23.5 配置基準の変更：「parentの中央」から「作業領域の中央」へ

今回の修正に合わせて、配置基準も変更した。旧実装は「parentの中央に重ねる」設計だったが、新実装は「parent（またはparent省略時・非表示時はwindow自身）が乗っているモニターの作業領域の中央」を基準にする。これにより、メインメニューを画面の端へ移動していても、そこから開く画面は作業領域の中央に表示される（parentの位置そのものには依存しない）。parent引数は「どのモニターを基準にするか」を決めるためだけに使われる。

複数モニター時は、`MonitorFromWindow(..., MONITOR_DEFAULTTONEAREST)`により「最も近いモニター」が採用される。ウィンドウが2モニターの境界にまたがっている場合は、Windows標準の同API挙動に従って片方のモニターに決定される（本修正ではこの既定動作をそのまま採用し、独自のまたがり判定は行っていない）。

画面（作業領域）よりウィンドウが大きい場合は、タイトルバー・左端が必ず作業領域内に収まるよう左上を作業領域の左上に揃える（右・下方向へのはみ出しは許容する）設計を維持した。

### 23.6 DPIについて

本アプリ・本関数はDPI非対応（DPI-unaware）のまま変更していない。実機確認では`GetDpiForWindow()`が常に96（100%相当）を返し、DPI非対応プロセスではWindowsが実際のモニターDPIに関わらず一律96 DPIとしてプロセスに見せかけることを確認した。ctypes経由の座標・サイズも、Tkinter自身が使う座標・サイズと同じこの「見せかけの」座標系で得られるため、両者の間で単位変換は不要であり、表示拡大（125%・150%等）が変わってもこの一貫性は保たれる。DPI対応化（per-monitorでの精密な表示）自体は本修正の対象外のまま。

### 23.7 動作確認

隔離環境（`config.APP_DATA_DIR`・`config.DB_PATH`・`config.MASTER_DB_PATH`を一時ディレクトリに差し替え）で、以下を確認した。

- 単体での`center_window()`（10項目）：サイズ指定済み・自動サイズ両方の画面で、作業領域基準・外枠サイズ基準での左右／上下の余白の差が数px以内に収まること。parentを作業領域の左上端へ移動しても子の表示位置が変わらないこと（parent位置非依存の確認）。作業領域より大きいウィンドウでタイトルバー・左端が作業領域内に収まること。parentがwithdrawn状態でも例外が出ないこと。再表示（lift/focus_force）で位置が変わらないこと。
- **実際のボタン起動経路での確認（29項目、全画面クラスを実際にインスタンス化）**：メインメニューを作業領域の端へ移動した状態から、別スレッドでデータを事前読み込みしてから開く非同期画面（生産実績入力`KittingProductionEntryWindow`・ロット進捗`LotProgressWindow`・日々の引落`DailyDrawdownWindow`）を含め、`center_window()`呼び出しを持つ29クラス全てを実際に生成し、作業領域基準での左右／上下の余白の差を実測した。結果は全て左右の差0px、上下の差1px以内（ウィンドウ高さの偶奇に由来する丸め、許容範囲内）で、29/29が合格した。
- **関数内の一時ダイアログ・ポップアップ（10か所）**：「登録済みリストを表示」ダイアログ（`production_import_staging_window.py`）を実際に開いて実測し合格したことに加え、残り9か所についてはソースコードを直接確認し、いずれも「ウィジェットを配置し終えた後・`grab_set()`/`wait_window()`より前に`center_window()`を1回呼ぶ」という、既に実機確認済みの画面群と同一の呼び出しパターンであることを確認した。固定サイズ（`geometry("WxH")`）の画面では、`center_window()`呼び出し後に追加でウィジェットをpackしている箇所（チェックボックス式絞り込みポップアップ等）があるが、Tkinterの仕様上、明示的に`geometry("WxH")`で両方の寸法を指定済みのトップレベルウィンドウは、その後の`pack()`だけでは外枠サイズが変化しない（スクロール可能なCanvas等で内容の増加を吸収する設計になっている）ため、中央寄せ後にサイズ・位置がずれ直すことはない。
- 既存の検証スイート（D-50〜D-60、計95＋40＋29項目）を再実行し、中央寄せの基準変更に直接関係する5項目（旧D-54仕様の「parentの中央に重ねる」ことを検証していたテスト）のみ、意図した仕様変更どおりに失敗することを確認した（リグレッションではなく、変更の反映）。それ以外の項目（既定DB無効化・在庫値出力警告・ロック制御等）は全て合格のままで、本修正による影響が無いことを確認した。
- 既存`pytest`（1 passed）に影響が無いこと。
- 実環境の`db/inventory.db`・`db/inventory.db.lock`のハッシュ・git状態が、本タスクの作業前後で変化していないこと（検証は全て`config.DB_PATH`等を一時ディレクトリへ差し替えた上で実行した）。

### 23.8 未対応・申し送り事項

- 関数内ダイアログ10か所のうち9か所は、実際に起動してのライブ実測ではなくソースコード上のパターン一致確認（§23.7参照）で代替した。いずれもトリガー条件が複雑な業務データを要するため、本タスクではこの確認範囲を妥当と判断したが、仮に将来これらのダイアログだけに固有の表示崩れが報告された場合は、個別にライブ実測を追加する余地がある。
- DPI対応化（per-monitorでの精密表示）自体は本修正の対象外のまま（§23.6参照）。

## 24. 構成基板数マスター・基板丁数マスター画面の改善：スクロールバー・登録件数表示・取込前確認・一括トランザクション化（2026-10-05、D-62〜D-65）

前段の調査タスク（§0に記載した投資結果、CANONICAL_DESIGN_DECISIONS.md本節策定前の調査）で判明した、2画面共通の3つの課題（登録済み一覧にスクロールバーが無い・登録件数の常時表示が無い・取込が即時反映かつ1行ごとの個別コミットで途中失敗時に部分的な反映が残る）に対応した。`ui/board_structure_import_window.py`・`ui/parts_attributes_import_window.py`・対応する`models/board_structure_master.py`・`models/parts_attributes.py`に同じ内容の改善を適用している。

### 24.0 着手前の確認（§0、いずれも停止要件に該当しなかった）

- **既存の`upsert_board_structure()`/`upsert_parts_attributes()`の呼び出し元への影響**：`services/master_merge_service.py::merge_master_from_backup()`がこれら2関数を直接呼んでいることを確認した（`services/master_merge_service.py:24-25,39,48`）。1行ごとの個別コミットという既存の挙動を変更すると影響が及ぶため、**既存の2関数は変更せず**、取込専用の一括関数（`apply_board_structure_sync()`/`apply_parts_attributes_sync()`）を新設する方針とした。実機検証（§24.5）で、`merge_master_from_backup()`が従来通り動作することを確認済み。
- **BOMキャッシュ無効化をコミット後にまとめて行えるか**：`invalidate_bom_master_by_part_no()`（`models/bom_master.py:74`）は`models.db_common.get_connection()`（月次DB、`config.DB_PATH`）を使う独立した接続・テーブルであり、`parts_attributes`が属する`master.db`（`get_master_connection()`）とは別のDBファイルであることを確認した（`models/parts_attributes.py:9-10`、`models/bom_master.py:10`）。このため、master.db側のトランザクションのコミット成功後にBOMキャッシュ無効化をまとめて呼んでも、アトミック性に影響しない（元々別DBのため、両方を1つのトランザクションにまとめることはできない関係にあった）。

いずれも実装を妨げる要因ではなかったため、停止せずに実装した。

### 24.1 スクロールバー・登録件数表示（D-62）

- 縦スクロールバーは`ui/operation_log_window.py:63-69`の既存パターン（`vsb.pack(side=tk.RIGHT, fill=tk.Y)`を`self.tree.pack(...)`より先に呼ぶ）をそのまま踏襲した。マウスホイールでのスクロールは、ttk.Treeview（Windows）が標準で対応しているため追加のバインド処理は行っていない（実機確認済み、§24.5参照）。
- 登録件数表示は、画面を開いたとき（`load_board_structure()`/`load_parts_attributes()`の末尾）・取込完了後（同じ関数を再度呼ぶ既存の経路）の両方で`_update_count_label()`が呼ばれるようにした。件数の取得は専用のCOUNT関数（`get_board_structure_count_summary()`・`get_parts_attributes_count()`、いずれも新設）を使い、Treeviewの行数を数える方式は採用していない（画面を開く前の状態でも使える、SQL側で集計する方が効率的という理由）。
- 構成基板数マスターのみ「うち構成基板数なし」の件数を併記する（部品属性側は仕様上この追加表示を求められていないため、総件数のみ）。

### 24.2 取込前の確認ダイアログ（D-63）

取込を「CSVを解析→DBへ書き込み」という単純な即時実行から、「①CSV解析＋現在のテーブルとの差分計算（読み取りのみ）→②確認ダイアログ→③「はい」の場合のみ実際の反映」という2段階に変更した。

- 差分計算（`compute_board_structure_sync_plan()`・`compute_parts_attributes_sync_plan()`、いずれも新設・読み取りのみ）は、CSVの重複キー解決後（後述）の1キー1件のリストと、現在のテーブル内容を比較し、追加・更新（値が変わる）・変更なし・削除の4カテゴリに分類する。
- 確認ダイアログには、各カテゴリの件数に加え、削除がある場合はその旨を明記した上で対象の先頭10件を表示する。「いいえ」を選んだ場合はDBへの書き込みを一切行わない（実機確認済み、§24.5参照）。
- CSV内の重複キー（同じ基板名／96コードの行が複数）は、解析時点で検出する。値が完全に同じ重複は件数のみを通知し、値が食い違う重複は該当キー・各行番号・各値・採用される値（最後の行の値、既存のON CONFLICT上書きの挙動と一致させるため）を確認ダイアログに明記する。続行するかどうかは、この重複の情報を含む同じ確認ダイアログで判断できるようにした（重複専用の別ダイアログは設けていない）。

### 24.3 取込の一括トランザクション化（D-64）

- 新設した`apply_board_structure_sync()`・`apply_parts_attributes_sync()`は、重複解決後の1キー1件のリストを受け取り、登録（INSERT ... ON CONFLICT DO UPDATE、既存のSQLと同一）・削除（差分同期で特定した対象）を**1つの`with get_master_connection() as con: ... con.commit()`ブロック内**で実行する。
- 途中で例外が発生した場合、Pythonの`sqlite3.Connection`が標準で提供するコンテキストマネージャ仕様（`__exit__`で、例外が無ければ`commit()`、例外があれば`rollback()`）により、呼び出し前の状態にそのまま戻ることを、実際に3行目で例外を注入する実機検証で確認した（§24.5参照）。
- 既存の`upsert_board_structure()`・`upsert_parts_attributes()`・`delete_board_structure_not_in()`・`delete_parts_attributes_not_in()`（1行・1処理ごとに個別コミット）はそのまま残し、`services/master_merge_service.py`等の既存の呼び出し元の動作は変更していない。
- **副次的な効果（性能）**：3000件規模のCSVで実測したところ、旧実装（1行ごとに個別コミット）は約34.7秒を要したのに対し、新実装（1トランザクション）は解析＋差分計算＋確定の合計で約0.09秒だった（§24.5参照）。1行ごとの個別コミットがSQLiteのディスク同期（fsync相当）のオーバーヘッドを件数分発生させていたことが原因と考えられる。
- 部品属性側のBOMキャッシュ無効化（`invalidate_bom_master_by_part_no()`）は、§24.0で確認した通り別DBの操作のため、本トランザクションのコミット成功後に、影響を受けた全part_no（追加・更新・削除）についてまとめて呼ぶよう変更した。

### 24.4 エラー表示・結果表示の整理（D-65）

- 従来、CSV解析・取込確定中の例外は`ValueError`のみエラーダイアログで通知し、それ以外の例外は`on_import_execute()`内の`self.after()`コールバック内でそのまま再発生させており、利用者には何も表示されなかった（調査タスクで判明した既存の問題）。両画面とも、解析段階・確定段階それぞれで`ValueError`以外の例外も捕捉し、エラーダイアログで「データは取込前の状態のままです」と明記して通知するよう修正した。D-64の一括トランザクション化により、確定段階で例外が発生した場合にこの文言が常に正しいことが保証される。
- 完了メッセージを、読み込んだ行数（CSVの有効行）・追加/更新/変更なし/削除（キー単位）・登録されなかった行数（キー空欄）・値が読み取れず空で登録した件数（部品属性側にも新規に追加。丁取り数またはフル数量の数値変換に失敗した行数）・取込後の登録件数、の内訳に整理した。
- ファイル形式についての注意文（区切り文字・文字コード不一致の疑いがある場合のヒント）は、`notices`という別カテゴリに分離し、行単位の警告件数（`warnings`、基板名／96コード空欄スキップ・数値変換失敗のみ）には含めない。
- 警告が多数ある場合も全件を確認できるよう、専用のスクロール可能な一覧ウィンドウ（`ui/warnings_list_window.py::WarningsListWindow`、新設）を両画面で共用し、警告が1件以上ある場合は完了メッセージの直後に自動的に開く。完了メッセージ自体には件数のみを表示する。
- `operation_log`に記録する内訳も、新しい分類（追加/更新/変更なし/削除の件数）に合わせて変更した。

### 24.5 動作確認

隔離環境（`config.APP_DATA_DIR`・`config.DB_PATH`・`config.MASTER_DB_PATH`を一時ディレクトリへ差し替え）で、以下を確認した。

- 前回調査の実測5パターン（初回・再取込・値変更・行削除・CSV内重複〈値同じ/値違い〉）について、差分計算の結果（追加・更新・変更なし・削除の件数）と、実際にトランザクションを確定した後のテーブル内容が、全パターンで一致すること（board_structure側11項目・parts_attributes側7項目、全て合格）。
- 確認ダイアログで「いいえ」相当（`apply_..._sync()`を呼ばない）の操作をした場合、テーブルが一切変化しないこと（合格）。
- 途中の行で例外を注入した場合、例外が発生し、テーブルが例外発生前と完全に同じ内容に戻ること（board_structure側：`normalize_board_name()`の2件目呼び出しで例外注入、parts_attributes側：NOT NULL制約違反を注入、いずれも合格）。
- 登録件数の表示が、画面を開いたとき・取込完了後の両方でテーブルの実際の行数と一致すること（合格）。
- 3000件規模のCSVの取込時間：旧実装（HEADコミット時点のモジュールを直接ロードして計測）約34.7秒 → 新実装（解析＋確定の合計）約0.09秒（合格、性能劣化なし）。
- 実際の`BoardStructureImportWindow`を生成し、スクロールバーの存在・マウスホイールイベントでのTreeviewスクロール（`<MouseWheel>`イベント生成後に`yview()`が変化すること）・「はい」選択での反映・登録件数ラベルの更新・「いいえ」選択での無変化・想定外の例外（`RuntimeError`を注入）でのエラーダイアログ表示（「データは取込前の状態のままです」を含む）とテーブル無変化、の計8項目を実機相当の経路で確認した（全て合格）。
- 「不足分のみ取り込み」（`services/master_merge_service.py::merge_master_from_backup()`）・BOM計算（`invalidate_bom_master_by_part_no()`経由のキャッシュ無効化が、丁取り数更新後にコミット後まとめて正しく働くこと）が従来どおり動作すること（合格）。
- TSV（タブ区切り）・cp932エンコーディング（全角文字を含む）の読み取りが、部品属性側・構成基板数マスター側のいずれも従来どおり動作すること（合格）。
- 既存の検証スイート（D-50〜D-61、計26+15+38+17+40+29項目）・既存`pytest`（1 passed）に影響が無いこと（全て合格）。
- 実環境の`db/inventory.db`・`db/inventory.db.lock`が、本タスクの作業前後で変化していないこと（確認済み。全ての検証は`config.DB_PATH`等を一時ディレクトリへ差し替えた上で実行した）。

### 24.6 未対応・申し送り事項

- ~~`ui/warnings_list_window.py::WarningsListWindow`も内部で`center_window()`を使うため...39か所の表に正式に追加する作業は本タスクのスコープ外として見送った。~~ → **後続タスクで反映済み（§25.4参照）**。
- `board_structure_master`の実データ（3119件）の`master.db`への再構築自体は、本タスクの対象外のまま未着手（D-42他、既知の別課題）。

## 25. 構成基板数マスター・基板丁数マスター画面への検索・ソート追加（2026-10-05、D-66〜D-68）

§24で改善した両取込画面の登録済み一覧に、検索（絞り込み）・列ソート機能を追加した。

### 25.0 着手前の確認（§0）

アプリ内の他画面における検索・絞り込み・列ソートの既存実装を調査した。

- **列ソート**：`ui/operation_log_window.py::sort_by_column()`が最も単純な実装（文字列比較のみ、クリックごとに昇順/降順をトグル、`_sort_reverse`辞書で列ごとに状態を保持）。`ui/lot_progress_window.py`・`ui/unified_report_window.py`の`sort_by_column()`は、数値列を`float()`変換して比較する`_NUMERIC_COLUMNS`方式を採用しているが、いずれも「ロット単位のブロックでまとめてソートする」という本件には無関係な業務要件（同一ロットの行が離れないようにする）のための設計が主目的であり、かつ数値変換に失敗した値（未登録等）を`float("-inf")`にフォールバックする方式のため、**降順ソート時に空値が先頭に来てしまい**、今回の「値が空のデータは昇順・降順どちらでも末尾」という要件を満たさない（D-67参照）。列ヘッダーにソート方向を記号で表示する既存実装は、調査した範囲では見つからなかった（`▼`は全画面で「チェックボックス式絞り込みポップアップを開くボタン」の意味で使われており、ソート方向の記号としては流用せず、本件で新たに`▲`/`▼`をヘッダーテキストへ直接追記する方式を採用した）。
- **検索（テキスト絞り込み）**：`ui/ng_input_window.py`等のチェックボックス式絞り込みポップアップ内の検索欄（`search_var.trace_add("write", rebuild_visible)`）が、入力のたびに絞り込む設計の既存例。ただし`.lower()`のみで全角/半角は吸収しない。今回は「全角/半角を区別しない」という追加要件があるため、`models/board_structure_master.py::normalize_board_name()`と同じNFKC正規化を検索語・検索対象値の両方に適用する方式を新設した（UI層のため、既存のmodels層との依存を避ける方針に従い、models側の関数を呼ばず同一ロジックを複製した。D-52の教訓・既存の設計判断を踏襲）。
- **採用方針**：列ソートの基本構造（列ごとの状態辞書、クリックでトグル、`Treeview.move()`での並べ替え）は`operation_log_window.py`に、数値列の判定方式（列名の集合で判定）は`lot_progress_window.py`/`unified_report_window.py`に合わせた。検索欄の「入力のたびに絞り込む」操作性は`ng_input_window.py`の検索欄に合わせたが、正規化方式（NFKC追加）とヘッダーの矢印表示は本タスクで新設した（既存のどの画面にも無い要件のため）。**既存画面（`operation_log_window.py`・`lot_progress_window.py`・`unified_report_window.py`・`ng_input_window.py`等）のコードは一切変更していない。**

### 25.1 検索・絞り込み（D-66）

- 一覧の上に検索欄（`ttk.Entry`＋`tk.StringVar`）と「クリア」ボタンを追加した。`search_var.trace_add("write", ...)`により、1文字入力するたびに絞り込みを再計算する。
- 検索対象列：構成基板数マスターは基板名・構成基板数の2列、基板丁数マスターは96コード・丁取り数・部品種別・部品支給区分・フル数量の5列（いずれもTreeviewに表示している全列）。数値列もTreeviewに表示される文字列表現（`str(value)`）に変換した上で検索対象にする。
- 正規化（`_normalize_for_search()`、両ファイルに同一ロジックを複製）：NFKC正規化＋小文字化＋前後空白除去により、全角/半角・大文字/小文字の違いを区別しない部分一致を実現した。
- 該当が0件の場合：Treeviewを空にし、`self.lbl_empty`（「検索条件に一致するデータがありません。」）をTreeview直前に`pack()`して表示する。該当が1件以上に戻った場合は`pack_forget()`で隠す。
- 構成基板数マスターのみ、「構成基板数なしのみ表示」チェックボックス（`self.missing_only_var`）を追加し、`board_count is None`の行だけに絞り込む機能を加えた（検索語との併用可、AND条件）。

### 25.2 列ソート（D-67）

- 列見出しをクリックすると、`sort_by_column(col)`が呼ばれる。同じ列を連続でクリックすると昇順/降順がトグルし、別の列をクリックした場合は常に昇順から始まる（`self._sort_column`・`self._sort_ascending`で状態を保持）。
- 現在のソート列・方向は、見出しテキストに`" ▲"`（昇順）/`" ▼"`（降順）を追記して表示する（`_update_column_headers()`、ソート対象外の列は元の見出しテキストのまま）。
- 数値列（構成基板数マスターは`board_count`、基板丁数マスターは`teitori`・`full_qty`）は`float()`変換して数値として比較し、それ以外の列は文字列として比較する（`_NUMERIC_COLUMNS`集合で判定、既存画面と同じ判定方式）。
- **値が空のデータの扱い（既存画面との重要な違い）**：ソート対象列の値が空（数値列は`None`、文字列列は`None`または空文字列）の行を、ソート前に別リストへ分離し、値が有る行だけを指定方向でソートした後、値が空の行を常に末尾へ連結する方式を採用した。既存の`lot_progress_window.py`等が使う「`float("-inf")`へのフォールバック」方式では、降順ソート時に空値が先頭に来てしまい、今回の「昇順・降順どちらでも末尾」という要件を満たせないため、意図的に別方式にした（§25.0参照）。
- 初期状態（画面を開いた直後、何もクリックしていない状態）は、キー列（構成基板数マスターは`board_name`、基板丁数マスターは`part_no`）の昇順。`list_board_structure()`/`list_parts_attributes()`のSQL自体が`ORDER BY`でキー昇順を返すことに加え、`self._sort_column`の初期値をキー列に設定しているため、ヘッダーの矢印表示も開いた直後から正しくキー列に付く。

### 25.3 件数表示・既存機能との独立性（D-68）

- 絞り込み中は、登録件数表示に「表示: N件 / 」を前置する（例：「表示: 25件 / 登録件数: 3119件（うち構成基板数なし 12件）」）。絞り込み無しの場合は従来と同じ表示のまま変化しない。
- 登録件数自体（「/」の後ろの値）は、検索・ソート機能の追加前と同じCOUNT関数（D-62で新設の`get_board_structure_count_summary()`/`get_parts_attributes_count()`）から取得する。Treeviewの行数を数える方式には変更していない。
- 検索・ソートは`self._all_rows`（DBから取得した全件のキャッシュ）に対してのみ適用し、Treeviewへの表示だけに影響する。取込処理（CSV解析・差分計算・確認ダイアログ・一括トランザクションでの確定）は、いずれも`self._all_rows`を参照せず、常にDBへ直接アクセスする設計（§24で確立済み）のままであるため、検索・ソートの状態は取込結果に一切影響しない。
- 取込完了後の一覧再読込（`load_board_structure()`/`load_parts_attributes()`）は、`self._all_rows`を再取得した後、現在の検索語・チェックボックス・ソート状態をそのまま使って再描画する（`_apply_filter_and_render()`を呼ぶのみで、検索語等をクリアする処理は無い）ため、取込前に見ていた絞り込み結果が、取込後のデータで自然に更新される。

### 25.4 center_window()適用箇所の反映（前回の見送り分）

D-65で新設した`ui/warnings_list_window.py::WarningsListWindow`も`center_window()`を使用するため、D-54・D-61で「39か所（29クラス＋関数内ダイアログ10か所）」として整理していた適用箇所が、実際には40か所（30クラス＋関数内ダイアログ10か所）になっていたことを、今回あらためて確認・反映した（前回はスコープ外として反映を見送っていた、§24.6参照）。実機確認（作業領域中央への配置、§25.5参照）は合格している。

### 25.5 動作確認

隔離環境（`config.APP_DATA_DIR`・`config.DB_PATH`・`config.MASTER_DB_PATH`を一時ディレクトリへ差し替え）で、以下を確認した。

- 検索：部分一致、全角/半角・大文字/小文字を区別しない、0件時の空表示・専用メッセージ、クリアでの全件復帰（構成基板数マスター：15項目、基板丁数マスター：3項目、全て合格）。
- ソート：数値列の昇順・降順が数値順になること（2, 10, 100の順。文字列順の10, 100, 2にならないことを明示的に確認）、値が空のデータが昇順・降順いずれでも末尾にまとまること、見出しの▲▼記号表示、初期状態がキー列昇順であること（全て合格）。
- 絞り込み中にソートしても絞り込みが維持されること（合格）。
- 絞り込み中に取込を実行しても、差分同期がテーブル全体に正しく反映され（CSVに無い行の削除・新規行の追加を含む）、取込後の件数表示（COUNT関数）がテーブルの実件数と一致すること。取込完了後も検索語が保持されたまま一覧が更新されることも確認した（合格）。
- 3000件規模での応答時間：画面を開く（読込＋初期表示）約0.12秒、検索（1回分の絞り込み）約0.03秒、ソート（数値列の昇順・降順切替）約0.03〜0.04秒。いずれも対話操作として遅延を感じない水準であることを確認した。
- 既存の検証スイート（D-50〜D-65、計11項目×2画面＋8項目＋4項目＋既存164項目）・既存`pytest`（1 passed）に影響が無いこと（全て合格）。
- `WarningsListWindow`が実際に作業領域の中央に表示されること（§25.4、合格）。
- 実環境の`db/inventory.db`・`db/inventory.db.lock`が、本タスクの作業前後で変化していないこと（確認済み）。

### 25.6 未対応・申し送り事項

- 検索・ソートの導入により一覧上の見た目は変わるが、CSVインポート自体の仕様（差分同期・CSVに無いデータの削除）はD-53・D-63から変更していない。
- `board_structure_master`の実データ（3119件）の`master.db`への再構築は、本タスクでも対象外のまま未着手（既知の別課題、D-42他）。
