"""
014: ロット統合の表 lot_merges を足す（D-145）。表を足すだけで、既存の表とデータは変えない。
新しく作る DB は db/schema.sql で同じ表を作る。2つの CREATE 文は一字一句同じにする（tests/test_lot_merge_schema.py で確かめる）。
"""
VERSION = 14

# 「統合されるロット（末尾に - を1つ付けたもの）→ 統合先のロット」を1行で持つ。統合されるロットは1つの統合先にだけ入る。
CREATE_TABLE = """CREATE TABLE IF NOT EXISTS lot_merges (
    member_lot TEXT PRIMARY KEY,
    target_lot TEXT NOT NULL,
    merged_at TEXT NOT NULL,
    merged_by TEXT,
    note TEXT,
    CHECK (member_lot = target_lot || '-'),
    CHECK (target_lot <> '' AND substr(target_lot, -1) <> '-')
)"""

CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_lot_merges_target_lot ON lot_merges(target_lot)"


def upgrade(con):
    con.execute(CREATE_TABLE)
    con.execute(CREATE_INDEX)
