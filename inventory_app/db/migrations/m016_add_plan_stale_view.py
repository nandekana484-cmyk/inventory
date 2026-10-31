"""
016: 古い計画の判定のビュー v_plan_stale を足す（D-193、docs/domain/plan_stale.md）。
ビューを足すだけで、既存の表とデータは変えない。kitting_plan_items・kitting_plan_batches が無い DB では何もしない
（その表が作られるとき、init_kitting_plan_tables() が同じビューを作る）。
CREATE 文は models/plan_stale_view.py の CREATE_VIEW_SQL と一字一句同じにする（tests/test_plan_stale_view.py で確かめる）。
2回実行しても安全（作り直す）。
"""
VERSION = 16

CREATE_VIEW = """CREATE VIEW v_plan_stale AS
WITH
latest AS (
    SELECT plan_batch_id, imported_at FROM kitting_plan_batches
    WHERE COALESCE(delete_flag, 0) = 0 AND substr(source_file, 1, 11) <> 'carry_over:'
    ORDER BY imported_at DESC, plan_batch_id DESC LIMIT 1
),
dts AS (
    SELECT plan_item_id, 1 AS kind, plan_start_datetime AS v FROM kitting_plan_items
    UNION ALL
    SELECT plan_item_id, 2, plan_end_datetime FROM kitting_plan_items
),
s1 AS (
    SELECT plan_item_id, kind,
           trim(replace(COALESCE(v, ''), '-', '/'), ' ' || char(9, 10, 13)) AS s
    FROM dts
),
s2 AS (
    SELECT plan_item_id, kind, s,
           CASE WHEN s GLOB '[0-9][0-9][0-9][0-9]/*' THEN substr(s, 6) END AS r1
    FROM s1
),
s3 AS (
    SELECT plan_item_id, kind, CAST(substr(s, 1, 4) AS INTEGER) AS y,
           substr(r1, 1, instr(r1, '/') - 1) AS mm,
           substr(r1, instr(r1, '/') + 1) AS r2
    FROM s2 WHERE r1 IS NOT NULL AND instr(r1, '/') > 0
),
s4 AS (
    SELECT plan_item_id, kind, y, mm,
           substr(r2, 1, MIN(instr(r2 || ' ', ' '), instr(r2 || 'T', 'T')) - 1) AS dd,
           MIN(instr(r2 || ' ', ' '), instr(r2 || 'T', 'T')) <= length(r2) AS has_sep,
           substr(r2, MIN(instr(r2 || ' ', ' '), instr(r2 || 'T', 'T')) + 1) AS tp
    FROM s3
),
s5 AS (
    SELECT plan_item_id, kind, y, mm, dd, tp, has_sep,
           CASE WHEN tp = '' THEN 0 ELSE CAST(substr(tp, 1, instr(tp, ':') - 1) AS INTEGER) END AS h,
           CASE WHEN tp = '' THEN 0 ELSE CAST(substr(tp, instr(tp, ':') + 1, 2) AS INTEGER) END AS mi,
           CASE WHEN length(tp) - instr(tp, ':') = 5 AND tp <> '' THEN CAST(substr(tp, instr(tp, ':') + 4, 2) AS INTEGER) ELSE 0 END AS ss
    FROM s4
    WHERE mm <> '' AND mm NOT GLOB '*[^0-9]*' AND length(mm) <= 2
      AND dd <> '' AND dd NOT GLOB '*[^0-9]*' AND length(dd) <= 2
      AND ((tp = '' AND has_sep = 0)
           OR tp GLOB '[0-9]:[0-9][0-9]' OR tp GLOB '[0-9][0-9]:[0-9][0-9]'
           OR tp GLOB '[0-9]:[0-9][0-9]:[0-9][0-9]' OR tp GLOB '[0-9][0-9]:[0-9][0-9]:[0-9][0-9]')
),
norm AS (
    SELECT plan_item_id, kind,
           printf('%04d/%02d/%02d %02d:%02d:%02d', y, CAST(mm AS INTEGER), CAST(dd AS INTEGER), h, mi, ss) AS dt
    FROM s5
    WHERE h <= 23 AND mi <= 59 AND ss <= 59
      AND CAST(mm AS INTEGER) BETWEEN 1 AND 12 AND CAST(dd AS INTEGER) >= 1
      AND y >= 1
      AND CAST(dd AS INTEGER) <= CAST(strftime('%d', date(printf('%04d-%02d-01', y, CAST(mm AS INTEGER)), '+1 month', '-1 day')) AS INTEGER)
),
items AS (
    SELECT p.plan_batch_id, p.kitting_list_no, COALESCE(p.lot_no, '') AS lot_no,
           p.setup_file_no, p.production_side,
           (SELECT dt FROM norm n WHERE n.plan_item_id = p.plan_item_id AND n.kind = 1) AS start_dt,
           (SELECT dt FROM norm n WHERE n.plan_item_id = p.plan_item_id AND n.kind = 2) AS end_dt,
           COALESCE(p.is_active, 1) AS is_active, COALESCE(p.delete_flag, 0) AS delete_flag
    FROM kitting_plan_items p
),
latest_rows AS (
    SELECT i.* FROM items i JOIN latest l ON l.plan_batch_id = i.plan_batch_id
),
period AS (
    SELECT MIN(start_dt) AS pmin, MAX(end_dt) AS pmax FROM latest_rows
),
judged AS (
    SELECT v.kitting_list_no, v.lot_no, v.setup_file_no, v.production_side,
           CASE WHEN (SELECT plan_batch_id FROM latest) IS NULL THEN 'no_latest_batch'
                WHEN (SELECT pmin FROM period) IS NULL OR (SELECT pmax FROM period) IS NULL THEN 'no_period'
                WHEN v.start_dt IS NULL OR v.end_dt IS NULL THEN 'date_unparsable'
                WHEN v.start_dt < (SELECT pmin FROM period) OR v.end_dt > (SELECT pmax FROM period) THEN 'outside_period'
                WHEN EXISTS (SELECT 1 FROM latest_rows r WHERE r.kitting_list_no = v.kitting_list_no AND r.lot_no = v.lot_no) THEN 'in_latest_batch'
                ELSE 'stale' END AS judge_reason
    FROM items v WHERE v.is_active = 1 AND v.delete_flag = 0
)
SELECT j.kitting_list_no AS kitting_list_no,
       j.lot_no AS lot_no,
       CASE WHEN j.judge_reason = 'stale' THEN 1 ELSE 0 END AS is_stale,
       CASE WHEN (SELECT COALESCE(SUM(d.daily_qty), 0) FROM production_daily d
                  WHERE d.kitting_list_no = j.kitting_list_no AND COALESCE(d.lot_id, '') = j.lot_no) > 0
            THEN 1 ELSE 0 END AS has_legacy_actuals,
       CASE WHEN j.judge_reason = 'stale' THEN
            (SELECT CASE WHEN COUNT(DISTINCT r.kitting_list_no) = 1 THEN MIN(r.kitting_list_no) END
               FROM latest_rows r
              WHERE r.lot_no = j.lot_no AND r.setup_file_no IS j.setup_file_no AND r.production_side IS j.production_side
                AND r.kitting_list_no <> j.kitting_list_no AND r.is_active = 1 AND r.delete_flag = 0)
       END AS replacement_kitting_list_no,
       CASE WHEN j.judge_reason = 'stale' THEN
            (SELECT COUNT(DISTINCT r.kitting_list_no)
               FROM latest_rows r
              WHERE r.lot_no = j.lot_no AND r.setup_file_no IS j.setup_file_no AND r.production_side IS j.production_side
                AND r.kitting_list_no <> j.kitting_list_no AND r.is_active = 1 AND r.delete_flag = 0)
            ELSE 0 END AS replacement_count,
       (SELECT plan_batch_id FROM latest) AS latest_batch_id,
       j.judge_reason AS judge_reason,
       (SELECT imported_at FROM latest) AS judged_at
FROM judged j"""


def _has_table(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def upgrade(con):
    if not _has_table(con, "kitting_plan_items") or not _has_table(con, "kitting_plan_batches"):
        return
    con.execute("DROP VIEW IF EXISTS v_plan_stale")
    con.execute(CREATE_VIEW)
