"""
テスト専用の 014（段階7b）: ng_declarations にダミーの列を1つ足すだけ。本番の db/migrations/ には入れない。
本番のマイグレーションの書き方の見本も兼ねる（表が無ければ何もしない・2回実行しても安全）。
"""
VERSION = 14

TABLE = "ng_declarations"
COLUMN = "test_dummy"
COLUMN_DEF = "test_dummy TEXT"


def upgrade(con):
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({TABLE})")}
    if not columns:
        return  # 表がまだ無い DB では何もしない（初めて使うときに最新の形で作られる）
    if COLUMN not in columns:
        con.execute(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN_DEF}")
