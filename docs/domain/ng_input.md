# NG入力・BOM展開（ui/ng_input_window.py）

2026-10-09 のコメント軽量化で、コメント・docstring から移した内容。関数名で参照する。

## 入力の流れ（`NgInputWindow`）

1. 次のどちらかで対象を指定する
   - 1a. キッティングリストNo.（計画あり）
   - 1b. ファイルNo.＋生産面（計画外。対応する計画が無い場合。キッティングリストNo.欄が空のときに使う）
   - キッティングリストNo.が入っていれば、ファイルNo.欄に何があっても計画ありとして扱う
2. NG 数量（枚数）を入れる。欄が空なら、生産実績入力画面で申告済みの NG（日付を問わない「1計画・面＝1レコード」の現在値）を自動で入れる。欄に値があればそちらを優先する（申告と違う枚数で展開したいときのため）（`_resolve_ng_qty()`）
3. 「展開」で BOM を展開する
   - 1a: 計画から file_no・生産面を特定して展開する。lot_no が分かっていれば一意に特定し、分からず候補が複数なら `ui.plan_candidate_dialog.select_plan_candidate()` で選ばせる（`plan_candidate_dialog` は生産実績入力画面と共用するために切り出したが、今この経路を使うのはこの画面だけ）
   - 1b: 計画を介さず `BOMService.expand_scrap_to_parts()` を直接呼ぶ。計画が無いので予定生産数の超過警告は出さない。TSV に実装ラインが複数あれば選ばせ、1件ならそのまま、0件なら None（`BOMService._calculate_bom()` の既定＝最初に見つかったライン1本分）
4. 使用部品（96コードごとの消費数量）をチェック付きの一覧で表示する（既定は全選択）。消費数量の列だけ編集できる（96コードを誤って書き換えて保存する事故を防ぐ）
5. 仕損にしない部品のチェックを外す
6. 「仕損登録」で、チェックした行だけを `replace_scrap_records()` で保存する。その kitting_list_no・面の既存レコードは全部削除してから登録し直す（後からの展開・登録を正とする）。上書きになる場合だけ事前に確認する。1b は is_unplanned=True
7. 保存した scrap_records は、在庫差異レポートで96コードごとに集計される（is_unplanned に関わらず part_no 単位の合計に入る）
8. 右ペインの NG 一覧で、行をダブルクリックするとその計画（計画外なら file_no＋生産面）を再展開する。NG 数量は変えない

## 計画外の登録

- scrap_records.kitting_list_no は NOT NULL なので、計画外では file_no をそのまま入れる。実在の kitting_list_no の命名規則（`{file_no}-{side}-{種別}-{日付}-{連番}`）と形が違うので、実データとぶつからない
- 計画外の行は lot_no を持たない（NG 申告も lot_no=NULL のものだけを使う）

## NG 一覧（`_fetch_ng_list_rows()`）

- NG 申告（`list_ng_declarations_latest()`）と展開済み（`list_scrap_summary_by_kitting_no()`）を、(kitting_list_no, lot_no, production_side) で合わせる。lot_no を含めないと、別ロットの申告と展開済みが1行に混ざる（kitting_list_no は lot_no をまたいで重複する。実データで478件）
- 状態: 未展開（申告だけ）／展開済み（両方）／展開済み（申告記録なし）（展開済みだけ。この機能の導入前に登録されたデータなど）
- 基板名は、計画ありの行だけ、その lot_no を使って `find_plan_item_by_kitting_no()` で補う（`list_active_plan_items()` は面1を除くので使わない）。計画外は空欄
- 対象外（`models.ng_exclusion_list`）: 未展開の行を対象外にしても、一覧から消さず「対象外」列で示す。消すと、対象外にした事実が見えなくなり、解除もできなくなるため（絞り込みで隠すことはできる）
- 列の順番（`NG_LIST_COLUMNS`）はクラス属性で公開し、`services.unprocessed_check_service` もこれを参照する（位置を決め打ちにしない）

## 一括展開・登録（`on_bulk_expand_register()`）

- 未展開で対象外でない行を、すべて一括で展開・登録する。チェックの確認はせず、展開した全部品を登録する。対象は scrap_records が無い行なので、実質は新規追加。上書きの確認も出さない
- バックグラウンドのスレッドではダイアログを出せないので、候補が複数ある行（計画ありで lot_no の候補が複数、計画外で実装ラインが複数）はエラーにして、ほかの行は続ける
- 1件のエラーで全体を止めない（CSV 取込と同じ「1行の異常がほかの行に影響しない」設計）
- `_run_bom_expansion_async()`（1件用）は使わず、行ごとの成否を追う。ワーカーの想定外の例外もキューで UI スレッドに渡し、エラーダイアログにする（アプリを落とさない）
- 対象の抽出（`_get_bulk_expand_targets()`）は、表示用に整形済みの `_all_ng_rows` ではなく、`list_ng_declarations_latest()` の生の値（production_side は int、ng_qty は float）を使う

## そのほか

- BOM 展開は共有フォルダを読むので時間が読めない。別スレッドで実行する（LoadingWindow＋threading.Thread＋queue.Queue＋after(200)）。計画の特定・入力検証・候補や実装ラインの選択ダイアログは、Tkinter の操作なので UI スレッドで先に済ませる。実装ラインの一覧取得（`list_mounting_lines()`）も TSV を読むが、非同期化の対象外にした
- BOM 展開の既知のエラー（FileNotFoundError・ValueError。file_no の未整備、K行が複数など）は、従来の文言でダイアログに出す。それ以外は Tkinter の通常の例外報告に任せる（以前から捕まえていなかったので挙動を変えない）
- 基板自身の行（item_type="board"、K行の96コード）は、区分列に「基板」と出して区別する。96コード列に文字を足すと、保存時に96コードを書き換えてしまう
- 部品の値は列名で取る（`get_row_value(iid, "part_no")`）。以前は位置で3要素を取り出していて、列を足したときに例外になった
- 開いた直後に最大化する（2026-10-07）。直前の `geometry("1150x600")` と中央の座標が、最大化を解除したときの大きさと位置になる。`center_window()` は非表示のまま位置を決めるので、「小さく出てから広がる」動きにはならない
- BOMService は仕掛展開画面と共有の1インスタンス（`get_shared_bom_service()`）。どちらの画面を先に開いても、共有フォルダのインデックス作成は1回で済む
