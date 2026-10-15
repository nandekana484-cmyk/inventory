"""
ロット統合（D-145）の検証用の合成データ（月次DB と master.db）を作る。実データは含まない（ロットNo. は実データで見つかった形を真似ただけ）。
メインPC・サブPC のどちらでも同じデータを作れるように残している。

使い方（リポジトリ直下で）:
    .venv\\Scripts\\python.exe inventory_app\\scripts\\make_lot_merge_sample_db.py <出力先フォルダ> [--app-dir <inventory_app のフォルダ>]

<出力先フォルダ>\\db\\LOTMERGE\\inventory.db と <出力先フォルダ>\\db\\master.db を作る。どちらかがすでにあれば、何もせずに止まる。
--app-dir: 別の版のコード（git worktree で取り出した v1.3.0 など）でデータを作るとき、その inventory_app を指す。
           v1.3.0 のコードで作ると、版 13 の「v1.3.0 で作った DB」になる（マイグレーション 014 の検証用）。
ほかの単発スクリプトと違い、import しただけでは何もしない。config の DB の場所は、作る間だけ出力先に向ける。
"""
import os
import sys

SAMPLE_FOLDER = "LOTMERGE"
WORKER = ("W1", "作業者A", "admin")

# (計画No., ロットNo., ファイルNo., 基板名, 面, 発注数, 計画数, 実装開始予定)
PLANS = [
    # X と X- の両方に計画（発注数が違う）。X- の側は面2の計画だけ（例 163326- が 170）
    ("K163326-1", "163326", "F1633", "BRD-1633", "1", 1000, 1000, "2026/07/01 08:00:00"),
    ("K163326-2", "163326", "F1633", "BRD-1633", "2", 1000, 1000, "2026/07/01 08:00:00"),
    ("K163326M-2", "163326-", "F1633", "BRD-1633", "2", 170, 170, "2026/07/14 08:00:00"),
    # X と X- の両方に計画（発注数が同じ）
    ("K137599-1", "137599", "F1375", "BRD-1375", "1", 500, 500, "2026/07/02 08:00:00"),
    ("K137599M-1", "137599-", "F1375", "BRD-1375", "1", 500, 50, "2026/07/20 08:00:00"),
    # X と X- が同じ計画No. を共有する
    ("K226793", "226793", "F2267", "BRD-2267", "1", 200, 200, "2026/07/03 08:00:00"),
    ("K226793", "226793-", "F2267", "BRD-2267", "1", 200, 20, "2026/07/21 08:00:00"),
    # X- の側の基板名が表記ゆれ（CA-G160M と CG-160M）
    ("K348588-1", "348588", "F3485", "CA-G160M", "1", 400, 400, "2026/07/04 08:00:00"),
    ("K348588M-1", "348588-", "F3485", "CG-160M", "1", 400, 40, "2026/07/22 08:00:00"),
    # 統合してはいけない組: X と X-1・X-2、X と XK、X と X.1
    ("K410001-1", "410001", "F4100", "BRD-4100", "1", 300, 300, "2026/07/05 08:00:00"),
    ("K4100011-1", "410001-1", "F4100", "BRD-4100", "1", 300, 30, "2026/07/23 08:00:00"),
    ("K4100012-1", "410001-2", "F4100", "BRD-4100", "1", 300, 30, "2026/07/24 08:00:00"),
    ("K420002-1", "420002", "F4200", "BRD-4200", "1", 300, 300, "2026/07/06 08:00:00"),
    ("K420002K-1", "420002K", "F4200", "BRD-4200", "1", 300, 30, "2026/07/25 08:00:00"),
    ("K430003-1", "430003", "F4300", "BRD-4300", "1", 300, 300, "2026/07/07 08:00:00"),
    ("K4300031-1", "430003.1", "F4300", "BRD-4300", "1", 300, 30, "2026/07/26 08:00:00"),
    # 「-」付きしか計画が無いロット
    ("K440004M-1", "440004-", "F4400", "BRD-4400", "1", 100, 100, "2026/07/08 08:00:00"),
    # ふつうのロット
    ("K450005-1", "450005", "F4500", "BRD-4500", "1", 100, 100, "2026/07/09 08:00:00"),
]

# (計画No., ロットNo., 日付, 数量)。163326 は 7/10 に 99 で揃い、163326- の 1 台は 7/15（利用者の例、D-136）。
ACTUALS = [
    ("K163326-1", "163326", "2026-07-10", 99),
    ("K163326-2", "163326", "2026-07-10", 99),
    ("K163326M-2", "163326-", "2026-07-15", 1),
    ("K137599-1", "137599", "2026-07-08", 300),
    ("K226793", "226793", "2026-07-11", 120),
    ("K348588M-1", "348588-", "2026-07-22", 10),
    ("K410001-1", "410001", "2026-07-12", 300),
    ("K4100011-1", "410001-1", "2026-07-23", 30),
    ("K450005-1", "450005", "2026-07-13", 100),
]

# (計画No., ファイルNo., 面, NG数, 日付, ロットNo.)
NG_DECLARATIONS = [
    ("K163326-1", "F1633", 1, 2, "2026-07-10", "163326"),
    ("K348588M-1", "F3485", 1, 1, "2026-07-22", "348588-"),
]

# 登録待ち（実績CSVの行）: (ロットNo., 製品名, 数量, 日付)
PENDING_ROWS = [
    ("163326-", "BRD-1633", 5, "2026/07/16"),
    ("440004-", "BRD-4400", 3, "2026/07/17"),
]

# 構成基板数マスタ。CG-160M（表記ゆれ）は登録しない（実データと同じく「マスタ未登録」になる）。
BOARD_COUNTS = {"BRD-1633": 1, "BRD-1375": 1, "BRD-2267": 1, "CA-G160M": 1, "BRD-4100": 1,
                "BRD-4200": 1, "BRD-4300": 1, "BRD-4400": 1, "BRD-4500": 1}

# 候補に出るはずの組（統合されるロット → 統合先）と、出てはいけないロット
EXPECTED_CANDIDATES = {"137599-": "137599", "163326-": "163326", "226793-": "226793", "348588-": "348588"}
EXPECTED_NOT_CANDIDATES = ("410001-1", "410001-2", "420002K", "430003.1", "440004-")


def build(out_dir):
    """out_dir の下に月次DB と master.db を作り、(月次DB のパス, master.db のパス) を返す。config の DB の場所は出力先に向けたまま戻る。"""
    import config
    from db.init_db import init_database_at
    from models.board_structure_master import upsert_board_structure
    from models.kitting_plan import create_plan_batch, create_plan_version, find_plan_item_by_kitting_no, init_kitting_plan_tables
    from models.ng_declarations import save_ng_declaration
    from models.production import insert_daily_production
    from models.production_import_staging import create_csv_import_batch, upsert_pending_csv_import_row
    from models.production_side_master import upsert_production_side
    from models.workers import create_worker

    db_path = os.path.join(out_dir, "db", SAMPLE_FOLDER, "inventory.db")
    master_path = os.path.join(out_dir, "db", "master.db")
    for path in (db_path, master_path):
        if os.path.exists(path):
            raise FileExistsError(f"すでにあります（上書きしない）: {path}")

    config.APP_DATA_DIR = out_dir
    config.MASTER_DB_PATH = master_path
    config.DB_PATH = db_path
    init_database_at(db_path)
    init_kitting_plan_tables()

    create_worker(*WORKER)
    batch = create_plan_batch("lot_merge_sample.csv", imported_by=WORKER[0], row_count=len(PLANS))
    for kitting_list_no, lot_no, file_no, board_name, side, order_qty, planned_qty, start in PLANS:
        create_plan_version(batch, kitting_list_no, {
            "lot_no": lot_no, "setup_file_no": file_no, "board_name": board_name, "production_side": side,
            "order_qty": order_qty, "planned_qty": planned_qty, "plan_start_datetime": start, "mounting_line": "A",
        }, created_by=WORKER[0])
        upsert_production_side(file_no, "A", side)
    for board_name, count in BOARD_COUNTS.items():
        upsert_board_structure(board_name, count)
    for kitting_list_no, lot_no, report_date, qty in ACTUALS:
        plan = find_plan_item_by_kitting_no(kitting_list_no, lot_no)
        insert_daily_production(plan["plan_item_id"], kitting_list_no, lot_no, plan["board_name"], report_date, qty, WORKER[0])
    for kitting_list_no, file_no, side, ng_qty, report_date, lot_no in NG_DECLARATIONS:
        save_ng_declaration(kitting_list_no, file_no, side, ng_qty, report_date, lot_no=lot_no)
    csv_batch = create_csv_import_batch("lot_merge_sample_actual.csv", imported_by=WORKER[0])
    for i, (lot_no, product_name, qty, report_date) in enumerate(PENDING_ROWS, start=2):
        upsert_pending_csv_import_row({"csv_row_no": i, "lot_no": lot_no, "product_name": product_name,
                                       "daily_qty": qty, "report_date": report_date, "worker_id": WORKER[0]},
                                      import_batch_id=csv_batch)
    return db_path, master_path


def main(argv):
    args = list(argv[1:])
    app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if "--app-dir" in args:
        i = args.index("--app-dir")
        app_dir = os.path.abspath(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    sys.path.insert(0, app_dir)
    out_dir = os.path.abspath(args[0])
    db_path, master_path = build(out_dir)
    import sqlite3
    import config
    with sqlite3.connect(db_path) as con:
        version = con.execute("PRAGMA user_version").fetchone()[0]
    print(f"コード: {os.path.dirname(os.path.abspath(config.__file__))}")
    print(f"月次DB: {db_path}（版 {version}）")
    print(f"master.db: {master_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
