"""
月次DB の版（PRAGMA user_version）の確認と、マイグレーションの適用（段階7b）。
ensure_schema() を、ロックを取った後・config.DB_PATH を切り替える前に呼ぶ（呼ぶのは services/db_lifecycle_service.py）。
設計は docs/refactoring_plan.md の段階7、手順は docs/db.md。
"""
import os
import re
import sqlite3
from datetime import datetime

from db.migrations import BASELINE_VERSION, LATEST_VERSION, load_migrations, validate_migrations

# 自動バックアップを DB ごとに何世代残すか（利用者の決定、2026-10-13）。
BACKUP_GENERATIONS = 3

# 自動バックアップの名前: <DB ファイル>.bak_v<元>_to_v<先>_<YYYYMMDD_HHMMSS>[_<連番>]
_BACKUP_SUFFIX = re.compile(r"\.bak_v(\d+)_to_v(\d+)_(\d{8}_\d{6})(?:_(\d+))?$")

# 基準（13）の確認。表があるときに、001〜013 で足した列がそろっていること。production_daily は必ずある（schema.sql）。
# 多くの表は初めて使ったときに作られるので、無い表は確かめない（作られるときに最新の形になる）。
BASELINE_REQUIRED_TABLES = ("production_daily",)
BASELINE_REQUIRED_COLUMNS = {
    "production_daily": ("plan_item_id", "kitting_list_no"),                                       # 002
    "kitting_plan_batches": ("delete_flag",),                                                       # 003
    "kitting_plan_items": ("lot_no", "version", "is_active", "previous_plan_item_id",
                           "created_by", "created_at"),                                             # 004・005
    "scrap_records": ("lot_no", "is_unplanned"),                                                    # 008・010
    "ng_declarations": ("lot_no",),                                                                 # 010
    "bom_master": ("mounting_line", "item_type"),                                                   # 011・013
}


class SchemaError(Exception):
    """DB の形の確認・適用で開けなかったときの基底クラス。"""


class DatabaseTooNewError(SchemaError):
    """DB の版が、このアプリが扱える最新の版より新しい（アプリが古い）。"""

    def __init__(self, db_version, app_version):
        super().__init__(f"データベースの版 {db_version} は、このアプリが扱える版 {app_version} より新しい。")
        self.db_version = db_version
        self.app_version = app_version


class LegacyDatabaseError(SchemaError):
    """版の記録が無く（user_version = 0）、基準（13）の形にもなっていない。problems は足りないものの説明のリスト。"""

    def __init__(self, problems):
        super().__init__("基準の形になっていない: " + "、".join(problems))
        self.problems = problems


class MigrationFailedError(SchemaError):
    """
    version のマイグレーションの適用に失敗した（そのトランザクションは巻き戻した）。
    applied_version: 失敗した時点の DB の版（それより前の番号は適用済み）。backup_path: 適用の前に取ったバックアップ。cause: 元の例外。
    """

    def __init__(self, version, applied_version, backup_path, cause):
        super().__init__(f"版 {version} の適用に失敗した: {cause}")
        self.version = version
        self.applied_version = applied_version
        self.backup_path = backup_path
        self.cause = cause


def read_user_version(con):
    return con.execute("PRAGMA user_version").fetchone()[0]


def write_user_version(con, version):
    # PRAGMA は ? で値を渡せないので、int にしてから埋め込む。
    con.execute(f"PRAGMA user_version = {int(version)}")


def stamp_latest_version(db_path):
    """新しく作った DB に、最新の版を書く（db/init_db.py の init_database_at() から呼ぶ）。"""
    con = sqlite3.connect(db_path)
    try:
        write_user_version(con, LATEST_VERSION)
        con.commit()
    finally:
        con.close()


def _columns(con, table):
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def baseline_problems(con):
    """基準（13）の形に足りないものの説明のリスト（足りていれば空）。"""
    problems = []
    for table in BASELINE_REQUIRED_TABLES:
        if not _columns(con, table):
            problems.append(f"{table}（表が無い）")
    for table, required in BASELINE_REQUIRED_COLUMNS.items():
        columns = _columns(con, table)
        if not columns:
            continue
        problems.extend(f"{table}.{column}" for column in required if column not in columns)
    # 005: kitting_list_no だけの UNIQUE を外した（同じ kitting_list_no が lot_no をまたいで重複するため）。
    if _columns(con, "kitting_plan_items"):
        for index in con.execute("PRAGMA index_list(kitting_plan_items)").fetchall():
            name, unique = index[1], index[2]
            if unique and [r[2] for r in con.execute(f"PRAGMA index_info('{name}')")] == ["kitting_list_no"]:
                problems.append("kitting_plan_items の kitting_list_no だけの UNIQUE（005 が未適用）")
    return problems


def check_version_readonly(db_path):
    """DB を読み取り専用で開き、(版, 基準に足りないもの) を返す（調査用。DB には書き込まない）。"""
    con = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True)
    try:
        return read_user_version(con), baseline_problems(con)
    finally:
        con.close()


def _backup_path(db_path, from_version, to_version, now):
    base = f"{db_path}.bak_v{from_version}_to_v{to_version}_{now.strftime('%Y%m%d_%H%M%S')}"
    path, n = base, 1
    while os.path.exists(path):
        path = f"{base}_{n}"
        n += 1
    return path


def backup_before_migration(con, db_path, from_version, to_version, now=None):
    """適用の前に、DB を同じフォルダへコピーする（SQLite のバックアップ API）。戻り値はバックアップのパス。"""
    path = _backup_path(db_path, from_version, to_version, now or datetime.now())
    dest = sqlite3.connect(path)
    try:
        con.backup(dest)
    finally:
        dest.close()
    return path


def list_migration_backups(db_path):
    """自動バックアップ（bak_v*）のパスを、古い順に返す。手で取ったバックアップ（bak_migration010_* など）は含めない。"""
    folder = os.path.dirname(os.path.abspath(db_path))
    prefix = os.path.basename(db_path)
    found = []
    for name in os.listdir(folder):
        if not name.startswith(prefix):
            continue
        match = _BACKUP_SUFFIX.search(name[len(prefix):])
        if match and name[len(prefix):].startswith(".bak_v"):
            found.append(((match.group(3), int(match.group(4) or 0)), os.path.join(folder, name)))
    return [path for _, path in sorted(found)]


def prune_migration_backups(db_path, keep=BACKUP_GENERATIONS):
    """自動バックアップを新しい方から keep 世代だけ残し、古いものを消す。戻り値は消したパスのリスト。"""
    backups = list_migration_backups(db_path)
    to_delete = backups[:-keep] if keep > 0 else backups
    deleted = []
    for path in to_delete:
        try:
            os.remove(path)
            deleted.append(path)
        except OSError:
            pass
    return deleted


def ensure_schema(db_path, migrations=None, latest_version=None, now=None):
    """
    DB の版を確かめ、足りないマイグレーションを適用する。開けないときは SchemaError の子クラスを送出する。
    migrations・latest_version はテスト用（省略すると db/migrations の本番のもの）。
    戻り値: {"from_version", "to_version", "applied"（適用した番号のリスト）, "stamped_baseline"（0 → 13 を書いたか）,
             "backup_path"（適用しなければ None）, "deleted_backups"}
    """
    if migrations is None:
        migrations = load_migrations()
        latest_version = LATEST_VERSION
    else:
        migrations = sorted(migrations, key=lambda m: m.VERSION)
        if latest_version is None:
            latest_version = migrations[-1].VERSION if migrations else BASELINE_VERSION
        validate_migrations(migrations, latest_version)

    # 自分でトランザクションを始めるため、自動の BEGIN を使わない（isolation_level=None）。
    con = sqlite3.connect(db_path, isolation_level=None, timeout=30.0)
    try:
        version = read_user_version(con)
        if version > latest_version:
            raise DatabaseTooNewError(version, latest_version)

        stamped_baseline = False
        if version == 0:
            problems = baseline_problems(con)
            if problems:
                raise LegacyDatabaseError(problems)
            write_user_version(con, BASELINE_VERSION)
            version = BASELINE_VERSION
            stamped_baseline = True

        result = {
            "from_version": version, "to_version": version, "applied": [],
            "stamped_baseline": stamped_baseline, "backup_path": None, "deleted_backups": [],
        }
        pending = [m for m in migrations if m.VERSION > version]
        if not pending:
            return result

        backup_path = backup_before_migration(con, db_path, version, latest_version, now)
        result["backup_path"] = backup_path
        for module in pending:
            _apply_one(con, module, backup_path)
            result["applied"].append(module.VERSION)
            result["to_version"] = module.VERSION

        result["deleted_backups"] = prune_migration_backups(db_path)
        return result
    finally:
        con.close()


def _apply_one(con, module, backup_path):
    """
    1つのマイグレーションを1つのトランザクションで適用し、同じトランザクションの中で user_version を書く。
    REBUILDS_TABLES = True なら、トランザクションの外で foreign_keys を OFF にし、終わったら元に戻す
    （foreign_keys はトランザクションの中では切り替わらない）。コミットの前に foreign_key_check で確かめる。
    """
    rebuilds = getattr(module, "REBUILDS_TABLES", False)
    applied_version = read_user_version(con)
    foreign_keys = None
    if rebuilds:
        foreign_keys = con.execute("PRAGMA foreign_keys").fetchone()[0]
        con.execute("PRAGMA foreign_keys = OFF")
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            module.upgrade(con)
            if rebuilds:
                violations = con.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise sqlite3.IntegrityError(f"外部キーの不整合が {len(violations)} 件あります: {violations[:5]}")
            write_user_version(con, module.VERSION)
            con.execute("COMMIT")
        except Exception as e:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise MigrationFailedError(module.VERSION, applied_version, backup_path, e) from e
    finally:
        if rebuilds:
            con.execute(f"PRAGMA foreign_keys = {int(foreign_keys)}")
