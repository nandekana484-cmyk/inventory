# db/legacy: マイグレーション 001〜013 の記録（実行しない）

段階7b（2026-10-13）で、001〜013 を適用済みの形を基準の版 **13** にした（`db/migrations/__init__.py` の `BASELINE_VERSION`）。
ここにあるスクリプトは、その形に至るまでの記録として残している。**実行しない。** 中身は移したときのまま変えていない。

- どれも単独で実行するスクリプトで、import した時点の `config.DB_PATH`（既定DB）につないでいた。適用したかどうかの記録は無かった
- このフォルダに移したので、スクリプトの中で組み立てている `inventory_app/` へのパスは1階層ずれている（そのままでは動かない）
- 版が記録されていない（`user_version` が 0 の）DB を開くと、アプリが基準の形かどうかを確かめる（`db/schema_version.py` の `BASELINE_REQUIRED_COLUMNS`）。足りなければ「古い形式」として開かない
- 14 以降の変更は `db/migrations/m0NN_<内容>.py` に書く。手順は `docs/db.md`「マイグレーション」

| 番号 | 内容 |
|---|---|
| 001 | `migrate_001.py`（対象の表は D-39 で廃止済み。今は意味がない） |
| 002 | 計画の表（kitting_plan_batches・kitting_plan_items）、production_daily に plan_item_id・kitting_list_no |
| 003 | kitting_plan_batches に delete_flag |
| 004 | kitting_plan_items に version・is_active・previous_plan_item_id・created_by・created_at |
| 005 | kitting_plan_items の kitting_list_no だけの UNIQUE を外す（表の作り直し） |
| 006 | (kitting_list_no, lot_no) のアクティブな行の UNIQUE インデックス |
| 007 | lot_no・kitting_list_no のインデックス |
| 008 | scrap_records に is_unplanned |
| 009 | ng_declarations の表 |
| 010 | scrap_records・ng_declarations に lot_no |
| 011 | bom_master に mounting_line（表の作り直し） |
| 012 | 構成基板数マスタ（今は master.db 側に自動で作られる。今は意味がない） |
| 013 | bom_master に item_type（表の作り直し） |
