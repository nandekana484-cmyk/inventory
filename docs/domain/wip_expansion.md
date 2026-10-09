# 仕掛（WIP）展開（ui/wip_expansion_window.py）

2026-10-09 のコメント軽量化で、コメント・docstring から移した内容。関数名で参照する。
NG入力画面（`docs/domain/ng_input.md`）と同じ構成を、仕掛用に複製した画面。

## 画面と流れ

- 実績レポート画面の「仕掛数量抽出」で保存した仕掛基板のスナップショット（`wip_board_snapshot`）を右ペインに表示する
- 行をダブルクリックすると、その行の file_no・生産面・実装ライン・仕掛数量（surplus_qty）で `BOMService.expand_wip_to_parts()` を呼び、左ペインに96コードごとの数量を表示する（既定は全選択）
- チェックした部品は「確定」（`on_confirm()`）で `wip_scrap_records` に登録する。その kitting_list_no・lot_no・面の既存分は削除してから登録し直す（NG入力画面の `on_register()` と同じ）
- 消費数量の列だけ編集できる（96コード列は編集不可）
- 注意: 以前のクラスの docstring には「本画面は確認・閲覧のみで DB への登録は行わない（仕掛の部品はまだ消費されていない在庫なので、NG のように登録して確定する対象ではない）」とあったが、現在は `on_confirm()` と一括展開・登録で wip_scrap_records に登録している。登録を足したときに docstring が更新されなかったと思われる（経緯は未確認）

## 仕掛一覧（`_fetch_wip_list_rows()`）

- スナップショットと確定済みの展開結果（`list_wip_scrap_summary()`）を (kitting_list_no, lot_no, production_side) で合わせ、「確定済み」「未確定」を付ける（NG 一覧の未展開／展開済みと同じ考え方）
- production_side はスナップショットが TEXT、確定済みが INTEGER なので、`str()` にそろえて比べる
- 対象外（`models.wip_exclusion_list`、キーは (kitting_list_no, lot_no, file_no, production_side)）の行も一覧から消さず、「対象外」列で示す（消すと、対象外にした事実と解除の導線が失われる）
- スナップショットに無い確定済み行: 確定登録の後に「仕掛数量抽出」をやり直すなどして、スナップショットからロットが消えた場合。実績修正の対象として見失わないよう、一覧の末尾に足す（NG 一覧の「申告 ∪ 展開済み」と同じ和集合の考え方）。正常な流れではまれ
  - 状態は `STATUS_CONFIRMED_ORPHAN`（「確定済み(スナップショットなし)」）。「確定済み」「未確定」と別の値にして、一括展開・登録の対象や未確定件数（`unprocessed_check_service`）に入らないようにする
  - 基板名・実装ライン・仕掛数量・抽出日時はスナップショット由来なので空欄。`list_wip_scrap_summary()` の total_qty は消費数量で、仕掛数量とは意味が違うので入れない。file_no だけは入れる
  - 展開できないので、ダブルクリックでは「実績修正」を案内して終わる（空欄の仕掛数量で展開して分かりにくいエラーになるのを避ける）。文字色でも区別する
- 列の順番（`WIP_LIST_COLUMNS`）はクラス属性で公開し、`unprocessed_check_service` もこれを参照する

## ほかの画面からの展開（`expand_by_identity()`）

- 仕掛製品レポートなどから、kitting_list_no（と lot_no・production_side）を指定して自動展開する入口。NG入力画面の検索欄を埋めて `on_expand()` を呼ぶのと同じ役割
- 一覧の全件から一致する行を探す（lot_no 等を省略すると、kitting_list_no が一致する最初の行）。見つからなければエラーを出して False（通常は起きない）
- スナップショットの無い確定済み行は展開できないので検索から外す。呼び出し元の動作は変えないため、「見つからない」と同じ扱いにする

## 一括展開・登録（`on_bulk_expand_register()`）

- 未確定で対象外でない行を、すべて一括で展開・登録する。チェックの確認はしない（NG入力画面と同じ）
- スナップショットの行は file_no・生産面・lot_no・実装ライン・仕掛数量を持っているので、NG入力画面のような計画の検索や「候補が複数」は無い
- 実装ラインが空欄の行だけ、`list_mounting_lines()` で TSV の候補を調べる。複数あればバックグラウンドからは選べないのでエラー、1件ならそれを使い、0件なら None（`BOMService._calculate_bom()` の既定）
- 対象の抽出は、表示用の `_all_wip_rows` ではなく `list_wip_snapshot()` の生の値（production_side は int、surplus_qty は float）を使う。スナップショットはキーの一意性が保証されていない（テーブル全体を差し替える方式）ので、dict にまとめずリストのまま走査する

## そのほか

- BOM 展開の非同期化（`_run_bom_expansion_async()`）は NG入力画面と同じ実装を、画面ごとに持っている。NG 一覧・仕掛一覧のフィルタ・ソートも共通部品にしていないのと同じ方針で、共通モジュールには切り出していない
- BOMService は NG入力画面と共有の1インスタンス
