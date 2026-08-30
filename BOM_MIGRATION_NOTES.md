# BOM基盤 移行・修正メモ

## 1. 概要

**目的**：BOM基盤（共有フォルダTSV → `bom_master`キャッシュ → 在庫差分/NG入力）まわりで行われた調査・修正を記録し、次にこのプロジェクトを触る人（将来の自分を含む）が同じ調査をやり直さずに済むようにする。

**対象読者**：`inventory_app`（部品在庫管理アプリ）のコードに触れる開発者。

**作成/更新日**：2026-08-29

> **本ドキュメントは2拠点並行開発を前提としている。** コードへの反映状況（セクション5の「反映済み/未反映」判定）は、**本ドキュメントを作成/更新した時点でこのリポジトリを開いていた環境**でのみ確認したものであり、もう一方の拠点での実装状況は確認していない。実装状況の最終確認は、マージ時に両拠点で改めて突き合わせることを推奨する。

---

## 2. BOM TSVの実データ構造（重要）

### 実列名一覧（17列）

```
セットアップファイルNo.  実装ライン  生産面  マシン番号  セット位置  96コード  部品員数
セットアップ部品種別  減数種別  マスターCHK員数係数  ボンド打ちフラグ  共通部品グループ
ライン優先順位  タクト時間  後工程  後工程備考  追加出庫リール数
```
（103.tsv・104.tsvのヘッダー行、cp932／タブ区切りで確認。全17列。）

### コード上の定数との対応表（修正前→修正後）

| 定数名 | 修正前（仮置き） | 修正後（実列名） | 備考 |
|---|---|---|---|
| `COL_SIDE` | `"先行面・後行面"` | `"生産面"` | 不一致だったため全行スキップの原因になっていた |
| `COL_QTY_PER_PRODUCT` | `"部品員数"` | `"部品員数"` | 元々一致・変更なし |
| `COL_COEFFICIENT` | `"マスターCHK員数係数"` | `"マスターCHK員数係数"` | 元々一致・変更なし |
| `COL_PART_NO` | `"部品番号"` | `"96コード"` | 不一致 |
| `COL_R_FLAG` | `"Rフラグ"` | `"減数種別"` | 該当列自体が存在しなかった。近い意味の列として「減数種別」を採用 |

対象ファイル：`services/bom_service.py`、`services/bom_file_service.py`（`COL_SIDE`のみ）。

### 「減数種別」列の値の分布

集計対象：519ファイル中518ファイル（file_no=284は文字コードエラーのため除外）。総行数：55,121行。

| 値 | 件数 | 割合 |
|---|---|---|
| （空欄/None） | 54,152 | 98.2% |
| `'R'` | 968 | 1.8% |
| `'M'` | 1 | 0.02%未満 |

- 103.tsv内訳：R=4件、空欄=60件（全64行）
- 104.tsv内訳：R=1件、空欄=61件（全62行）
- `'M'`は1件のみ検出（file_no=427、7行目、96コード=96226360）

### truthy判定を採用した理由と限界

`_calculate_bom()`のロジック（値は変わらず）：
```python
coefficient = row.get(COL_COEFFICIENT)
r_flag = row.get(COL_R_FLAG)

if coefficient and coefficient > 0:
    qty = qty_per_product * coefficient
elif r_flag:
    attrs = get_parts_attributes(part_no)
    teitori = attrs.get("teitori") if attrs else None
    if teitori is not None and teitori >= 1:
        qty = qty_per_product / teitori
    else:
        qty = qty_per_product  # 警告ログ付きの暫定値
else:
    continue
```
`r_flag`は値の中身を問わず「truthyかどうか」だけで分岐に使われている（**単純truthy判定**）。`'M'`のような`'R'`以外の値も無条件に同じ経路（丁取り数割り）に入る。**値の種類（'R'/'M'）による分岐は行っていない。**

理由：サンプル数が極端に偏っており（`'M'`は1件のみ）業務側の定義確認が必要と判断されたが、母数が少なく実装保留は非現実的だったため、まず単純truthy判定を採用する方針をユーザーが選択した（「①ですすめて」の指示）。

### 共有フォルダの実際のディレクトリ構造

パス例：`\\192.168.5.151\みんなの広場\...\◆機種ラインマスタ`

- 直下：753項目、うち752個がサブフォルダ（ファイル番号名）＋`.lnk`が1個。直下に`.tsv`は0件。
- 実TSVは`◆機種ラインマスタ\103\103.tsv`のように1階層下のサブフォルダの中にある（**2階層構成**）。
- 確認方法：PowerShellで`Get-ChildItem`により読み取りアクセスを確認。

### 文字コード（cp932）判明の経緯

バイト列から判定した結果、Shift_JIS（cp932）で正しくデコードできた（UTF-8では文字化け）。`config.BOM_ENCODINGS = ['utf-8-sig', 'utf-8', 'cp932']`（`inventory_app/config.py`）にcp932が含まれているため、読み込み自体は成功しうる状態。

---

## 3. file_no解決の複雑さ（重要）

### 表記の差

- `kitting_plan_items.setup_file_no`：先頭ゼロ埋め4桁（例：`"0103"`, `"0161"`, `"0432"`, `"0449"`）
- `BOMFileIndex`が扱うfile_no：ゼロ埋めなし、サブフォルダ名／ファイル名基準（例：`"103"`, `"104"`, `"161_A8-1"`（サブフォルダ名とファイル名が不一致でファイル名を優先採用したケース）, `"432_A4-1"`, `"432_A6"`（1サブフォルダに複数TSVがあるケース））

サブフォルダ名とTSVファイル名が不一致だった他のフォルダ例（ログ出力対象）：
`162, 163, 164, 165, 166, 22, 226, 355, 360, 361, 366, 368, 370, 372, 374, 382, 384, 385, 404, 416, 428, 430, 432, 434〜440, 503, 504, 507, 510, 55, 56, 57, 580, 599, 601, 671, 672, 680, 681, 686, 687, 694, 695, 700, 701, 708, 709, 723, 94`

### `resolve_file_no()`の実装（`services/bom_file_service.py`の`BOMFileIndex`クラス内）

1. まず完全一致（索引キーそのまま）を試す
2. 一致しなければ先頭ゼロを除去して索引キーと突き合わせる
3. 先頭ゼロ除去後、同一サブフォルダ内に複数TSV候補がある場合は一意に決定せず`None`を返し、`problems`に「複数候補あり」として記録
4. 該当するTSVが見つからない場合も`None`を返し、`problems`に「TSV未整備」として記録

`services/bom_service.py`の`get_parts_for_file_no()`は`index.resolve_file_no(file_no)`で解決してから`read_tsv()`を呼ぶ。

### 432フォルダの複数TSVケース

```
432フォルダの中身:
  432_A4-1.tsv
  432_A4-1.xlsx
  432_A6.tsv
  432_A6.xlsx
```
共有フォルダ配下の全752サブフォルダを走査した結果、複数（2件以上）の`*.tsv`を含むサブフォルダは**432の1件のみ**（他に同様のケースなし）。161フォルダは「サブフォルダ名とファイル名が不一致（単数）」のケースであり、「複数TSV同居」ではない（別事象）。

`setup_file_no='0432'`は正規化（先頭ゼロ除去）後、サブフォルダ432内の`432_A4-1`と`432_A6`のどちらを採用すべきか一意に決まらないため、自動解決不可のケースとして扱われる。

### 実データでの内訳

母数：`kitting_plan_items.setup_file_no`のユニーク値481件のうち、BOMFileIndex（519件）と完全一致しないもの480件。

| 区分 | 件数 | 内容 |
|---|---|---|
| 正規化だけで一意に解決できる | 397件 | 先頭ゼロを外すだけで一致（355件）、または先頭ゼロを外した上でサブフォルダ内の唯一のTSVファイル名と一致（42件） |
| 正規化しても一意に決まらない（複数候補あり） | 1件 | `setup_file_no='0432'` |
| 正規化しても対応TSVが存在しない（真の欠損） | 82件 | サブフォルダ自体は存在するが中身が`.xlsx`のみで`.tsv`が未整備（例：10, 13, 45, 138, 303, 710, 724等） |
| 合計 | 480件 | （残り1件は正規化不要で完全一致済み、481件中） |

### file_no=284の文字コードエラー【未解決】

```
例外種別: ValueError
メッセージ: BOM TSVの文字コードを判定できませんでした: 'cp932' codec can't decode byte 0xeb in position 20: illegal multibyte sequence
```
3種のエンコーディング（utf-8-sig / utf-8 / cp932）全てで失敗し`ValueError`を送出する。**対応方針は未定**（`get_parts_for_file_no()`内で読み込み失敗時に`problems`へ記録する仕組みは実装済みだが、エラー自体の解消・TSVファイル自体の修復は未対応）。

### `list_excluded_file_nos()`（`services/bom_service.py`の`BOMService`クラス）

`BOMFileIndex.problems`をコピーして返す。除外理由の種類（typeで区別）：

- `multiple_tsv_in_subfolder`（サブフォルダ内に複数TSV、432のケース）
- `index_key_collision`（file_no衝突、現状0件だが将来のための検知機構）
- `unresolved_multiple_candidates`（正規化後も複数候補、0432のケース）
- `tsv_not_found`（TSV未整備、82件のケース）
- `read_error`（読み込みエラー、284のケース）

---

## 4. 同一lot_no内での複数バッチ同時アクティブ問題（重要）

### キー拡張の経緯（3段階）

**段階1（修正前・バグ）**：
```python
file_actuals[file_no] = get_app_cumulative_qty(kitting_list_no)
```
`setup_file_no`のみをキーにしていたため、同一lot_no・同一file_noで面1／面2が両方存在する場合、後から処理された面のデータが上書きし片方の実績が消えていた。

**段階2（1回目修正）**：キーを`(setup_file_no, production_side)`のタプルに変更。面ごとの上書きは解消。ただしこの段階で「同一lot_no・同一file_no・同一面で複数の別バッチ（異なるkitting_list_no）が同時にアクティブ」なケースが**222件**存在することが判明し、このキーでもまだ上書きが起きることが確認された。

**段階3（2回目修正・最終）**：キーを`(setup_file_no, production_side, kitting_list_no)`の3要素タプルに拡張。バッチ単位で完全分離。

各段階でUI側（`ui/kitting_production_entry.py`）の表示ロジックも、タプル要素数の変化に合わせて都度追随修正されている。

### 222件の算出方法・確認内容

実DB全体で`(lot_no, setup_file_no, production_side)`の組み合わせのうち、複数のkitting_list_noが同時にis_active=1になっている件数を確認した結果、**222件**。

原因の実例（lot_no='100075'）：`0568-1-P-260724-01`と`0568-1-P-260731-01`が両方side=1かつ両方is_active=1（バージョンの新旧ではなく、日付違いの別バッチが同時にアクティブになっている状態）。

段階3修正後の検証として、222件から5件をランダムサンプリング（163326, 113035, 232778, 221669, 221608）し、いずれも「is_active行数 == file_actuals件数」（上書きなし）を確認した：

```
lot_no=163326: is_active行数=4, file_actuals件数=4, 一致=True
lot_no=113035: is_active行数=8, file_actuals件数=8, 一致=True
lot_no=232778: is_active行数=8, file_actuals件数=8, 一致=True
lot_no=221669: is_active行数=9, file_actuals件数=9, 一致=True
lot_no=221608: is_active行数=10, file_actuals件数=10, 一致=True
```

---

## 5. これまでに実施した修正一覧（本環境時点での確認結果）

> 以下は**本ドキュメントを作成/更新した環境（このリポジトリ・このブランチ）で`git status`および該当ファイルを軽く確認した結果**であり、網羅的なgit調査（reflog・他ブランチ探索等）は行っていない。**「未反映」と判定した項目については、もう一方の拠点での実装状況は本ドキュメントでは未確認。マージ時に要突き合わせ。**

`git status --short` で確認した現在の未コミット変更：
```
 M inventory_app/db/init_db.py
 M inventory_app/db/migration_002.py
 M inventory_app/db/schema.sql
 M inventory_app/models/bom_master.py
 M inventory_app/models/kitting_plan.py
 D inventory_app/models/parts.py
 M inventory_app/models/parts_attributes.py
 M inventory_app/models/production.py
 M inventory_app/services/production_import_service.py
 M inventory_app/services/production_service.py
 M inventory_app/tests/test_models.py
 M inventory_app/ui/kitting_production_entry.py
 M inventory_app/ui/ng_input_window.py
 M inventory_app/ui/parts_attributes_import_window.py
 M inventory_app/ui/unmatched_production_window.py
 D inventory_app/utils/kitting_csv_importer.py
?? inventory_app/db/migration_007_add_lot_no_and_kitting_list_no_indexes.py
```

| # | 対象ファイル | 修正内容 | 判定 | 関連セクション |
|---|---|---|---|---|
| 1 | `services/bom_service.py`, `services/bom_file_service.py` | BOM列名修正（COL_SIDE/COL_PART_NO/COL_R_FLAG） | **未反映**（現在も`"先行面・後行面"`/`"部品番号"`/`"Rフラグ"`のまま）。別拠点での実装状況は本ドキュメントでは未確認。マージ時に要突き合わせ | §2 |
| 2 | `services/bom_file_service.py` | `BOMFileIndex.build_index()`の再帰化 | **未反映**（`glob.glob(pattern)`で非再帰のまま）。別拠点での実装状況は本ドキュメントでは未確認。マージ時に要突き合わせ | §2 |
| 3 | `services/bom_file_service.py`, `services/bom_service.py` | `resolve_file_no()`によるfile_no正規化・problems記録機構 | **未反映**（該当関数が見当たらない）。別拠点での実装状況は本ドキュメントでは未確認。マージ時に要突き合わせ | §3 |
| 4 | `services/production_service.py` | `calculate_lot_completion()`のキー拡張（段階1→2→3） | **未反映**（`file_actuals[file_no] = ...`と単一キーのまま）。別拠点での実装状況は本ドキュメントでは未確認。マージ時に要突き合わせ | §4 |
| 5 | `services/production_import_service.py` | `register_daily_result()`の呼び出しを行ごとにtry/exceptで囲み、失敗行を`errors`に記録して処理継続 | **反映済み**（未コミット） | - |
| 6 | `models/kitting_plan.py` | `list_plan_items_by_lot()`のis_activeフィルタ追加 | **未反映**（`delete_flag = 0`のみでフィルタ）。別拠点での実装状況は本ドキュメントでは未確認。マージ時に要突き合わせ | §4 |
| 7 | `ui/unmatched_production_window.py` | 表示列追加（report_date/worker_id）、エラー行一覧（`errors`）への流用（title/reason_key/reason_label引数） | **反映済み**（未コミット） | - |
| 8 | `utils/kitting_csv_importer.py` | ファイル削除（`parse_and_import_kitting_csv()`、呼び出し元皆無を確認済み） | **反映済み**（未コミット、git status上で削除済み） | - |
| 9 | `models/parts.py` | ファイル削除（`add_part()`/`get_part_by_id()`）。`tests/test_models.py`からも該当箇所を削除 | **反映済み**（未コミット、git status上で削除済み） | - |
| 10 | `db/migration_002.py` | `DB_PATH`のハードコードを廃し、他migrationスクリプトと同じ`from config import DB_PATH`方式に統一 | **反映済み**（未コミット） | - |
| 11 | `models/bom_master.py`, `models/parts_attributes.py` | `bom_master`キャッシュの無効化機構（`invalidate_bom_master_by_part_no()`） | **反映済み**（未コミット） | - |
| 12 | `models/parts_attributes.py`, `ui/parts_attributes_import_window.py` | `parts_attributes`の差分同期（CSVをマスタとした削除。`delete_parts_attributes_not_in()`新設） | **反映済み**（未コミット） | - |
| 13 | `models/kitting_plan.py` | あいまい一致（`find_matching_plan_items`）は実装しないことが決定（完全一致のみで運用）。TODOコメントを決定内容に書き換え（判定ロジック自体は元々完全一致のみで変更なし） | **反映済み**（未コミット） | §6 |
| 14 | `ui/ng_input_window.py` | NG数量が計画数（`planned_qty`）を超えた場合の警告表示を`on_expand()`内に追加（`messagebox.showwarning`、入力・登録はブロックしない） | **反映済み**（未コミット） | §6 |
| 15 | `db/migration_007_add_lot_no_and_kitting_list_no_indexes.py`, `db/init_db.py`, `models/kitting_plan.py`, `db/schema.sql` | 計画一覧表示のボトルネック調査を受け、`kitting_plan_items.lot_no`・`production_daily.kitting_list_no`にインデックスを追加（`migration_005`/`006`と同じ`sys.path.insert`+`from config import DB_PATH`パターン）。新規DB作成時にも反映されるよう`init_kitting_plan_tables()`/`init_database_at()`にも同内容を追加し、`schema.sql`には実体の所在を示すコメントのみ追記 | **反映済み・実DB適用済み**（`inventory_app/db/inventory.db`に`migration_007`を実行済み。適用前後で`kitting_plan_items`=2251件・`production_daily`=0件のまま変化無しを確認。コード自体は未コミット） | - |
| 16 | `models/production.py`, `models/kitting_plan.py`, `services/production_service.py`, `ui/kitting_production_entry.py` | `get_app_cumulative_qty_bulk()`を新設し、`list_active_plan_items()` / `load_plan_list()` / `calculate_lot_completion()`の3箇所のN+1呼び出し（ループ内で`get_app_cumulative_qty()`を個別呼び出し）を解消。`list_active_plan_items()`の戻り値に計算済みの`app_cumulative_qty`を持たせ、`load_plan_list()`側での重複計算も排除 | **反映済み**（未コミット）。効果：計画一覧表示（`load_plan_list()`相当）が**9.25秒→0.87秒（約10.6倍）**に改善（合成データ11,155件、`migration_007`適用込みで計測） | - |

---

## 6. 未対応・保留中の項目

- **`list_excluded_file_nos()`の派生対応**（§3参照）：除外理由（`multiple_tsv_in_subfolder`/`unresolved_multiple_candidates`/`tsv_not_found`/`read_error`等）の一覧化はできているが、それぞれをUI上でどう扱う・通知するかは未対応。
- **file_no=284の文字コードエラー対応方針**（§3参照）：`ValueError`（cp932でもデコード不可）が発生する既知の未解決事項。対応方針は未定。
- **`models/production.py`の`init_production_table()`/`get_daily_production()`**：呼び出し元がリポジトリ全体を検索しても見つからない。削除候補として報告済みだが未対応のまま残存。
- **`production_records`テーブル**（実DB0件）：上記2関数が対象とする旧テーブル。DROPするかどうか未定。
- **`services/production_service.py`の`_build_report_rows()`（日報・月報画面、`build_daily_report()`/`build_monthly_report()`が利用）に同様のN+1構造が残存**：ループ内で`get_app_cumulative_qty()`を1レコードずつ個別呼び出ししている（§5 #16の対応スコープ外）。同じく`get_app_cumulative_qty_bulk()`を流用して一括化できる見込み。

---

## 7. 実DBに関する注意事項

- `board_definitions`/`component_groups`/`component_bom`（旧BOM）は空テーブルとして残存。DROP不要と判断。
- `parts_attributes`/`bom_master`テーブルは開発中の動作確認の副作用で実DBに新規作成済み（0件、DROP不要と判断）。
- `kitting_plan_items`件数について、開発環境と実環境で値が異なる場合がある（実環境の値を正とする方針で運用中）。2251件 vs 6949件という食い違いが確認されたが、リポジトリ内に比較対象となる`.db`ファイルが1つしか存在しないこと、`sqlite_sequence`の最高到達値が2255であり過去に6949件へ達したことがないことから、6949件は別環境固有の事情によるものと判断し、実環境側の値（2251件）を正として扱う方針とした。

---

## 8. 参考：呼び出し経路の全体像

```
BOMService.initialize() / _ensure_index()
  └─ BOMFileIndex(shared_folder_path)  ※ config.BOM_FOLDER_PATH がデフォルト
       └─ build_index()  … 共有フォルダを走査し file_no → {path, mtime} を索引化

BOMService.get_parts_for_file_no(file_no, side, data_ym)
  ├─ 1. models.bom_master.query_bom_master() … キャッシュ確認（1件でもあればヒットとして返す）
  ├─ 2. （キャッシュミス時）BOMFileIndex.resolve_file_no(file_no) → read_tsv() … TSV読み込み
  ├─ 3. _calculate_bom() … 係数 or 丁取り数（models.parts_attributes.get_parts_attributes）でqty算出
  └─ 4. models.bom_master.save_bom_master() … 計算結果をキャッシュ保存

BOMService.expand_wip_to_parts(wip_record)   ── get_parts_for_file_no() を利用
  └─ 呼び出し元: services/inventory_diff_service.py（_bom_service = BOMService()）
       └─ 呼び出し元: ui/inventory_diff_window.py（build_inventory_diff_report() 経由）

BOMService.expand_scrap_to_parts(scrap_record) ── get_parts_for_file_no() を利用
  └─ 呼び出し元: ui/ng_input_window.py（_bom_service = BOMService()、NG入力画面）

【丁取り数マスタの更新経路】
ui/parts_attributes_import_window.py（CSVインポート、行ごとにupsert）
  └─ models.parts_attributes.upsert_parts_attributes()
       └─ models.bom_master.invalidate_bom_master_by_part_no()  … 該当part_noを含むキャッシュキーを無効化
  └─ （全行upsert後）models.parts_attributes.delete_parts_attributes_not_in()  … CSVに無いpart_noを削除・キャッシュも連動無効化
```
