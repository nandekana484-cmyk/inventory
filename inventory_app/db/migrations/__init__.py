"""
月次DB のマイグレーション（段階7b）。DB を開くとき（起動・切り替え・バックアップからの取り込み）に、
db/schema_version.py の ensure_schema() が、DB の版（PRAGMA user_version）より新しいものを番号の順に適用する。

- 001〜013 は基準（BASELINE_VERSION = 13）に含まれる。それらのスクリプトは db/legacy/ にあり、実行しない
- 新しいマイグレーションは、このパッケージに m0NN_<内容>.py として置く（VERSION = NN と upgrade(con)）。
  表を作り直すものは REBUILDS_TABLES = True も書く。追加したら LATEST_VERSION を上げる。手順は docs/db.md
"""
import importlib
import pkgutil
import re

# 001〜013 を適用済みの形。user_version が 0 の DB は、形を確かめてからこの番号を書く。
BASELINE_VERSION = 13

# このアプリが扱える最新の版。マイグレーションを追加したら上げる（load_migrations() が食い違いを検出する）。
LATEST_VERSION = 14

_MODULE_NAME = re.compile(r"^m(\d{3})_\w+$")


def validate_migrations(migrations, latest_version):
    """番号が BASELINE_VERSION + 1 から抜けなく続き、最後が latest_version であること。違えば ValueError。"""
    expected = BASELINE_VERSION + 1
    for module in migrations:
        if module.VERSION != expected:
            raise ValueError(f"マイグレーションの番号が続いていません（{expected} のところに {module.VERSION}）。")
        expected += 1
    last = migrations[-1].VERSION if migrations else BASELINE_VERSION
    if last != latest_version:
        raise ValueError(f"最新の版（{latest_version}）と、最後のマイグレーションの番号（{last}）が違います。")


def load_migrations():
    """このパッケージの m0NN_*.py を番号の順に import して返す。ファイル名の番号と VERSION が違えば ValueError。"""
    modules = []
    for info in pkgutil.iter_modules(__path__):
        match = _MODULE_NAME.match(info.name)
        if not match:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        if module.VERSION != int(match.group(1)):
            raise ValueError(f"{info.name} の VERSION（{module.VERSION}）がファイル名の番号と違います。")
        modules.append(module)
    modules.sort(key=lambda m: m.VERSION)
    validate_migrations(modules, LATEST_VERSION)
    return modules
