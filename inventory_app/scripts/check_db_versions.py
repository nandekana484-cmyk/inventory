"""
月次DB の版（PRAGMA user_version）と形を、読み取り専用で調べて一覧にする（DB には書き込まない）。
リファクタリング（段階7b）後のアプリに移る前に、各PCの DB が新しいアプリで開けるかを確かめるために使う。
手順は docs/migrate_to_refactored.md。

使い方（リポジトリ直下で）:
    .venv\\Scripts\\python.exe inventory_app\\scripts\\check_db_versions.py              … inventory_app\\db の下
    .venv\\Scripts\\python.exe inventory_app\\scripts\\check_db_versions.py dist\\db     … .exe の隣の db など、ほかのフォルダ

<フォルダ>/<名前>/inventory.db をすべて調べる（既定DB の <フォルダ>/inventory.db は、アプリが開かないので調べない）。
ほかの単発スクリプトと違い、import しただけでは何もしない。
"""
import os
import sys

# scripts/ から inventory_app/ を import できるようにする。
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.migrations import BASELINE_VERSION, LATEST_VERSION  # noqa: E402
from db.schema_version import check_version_readonly  # noqa: E402


def judge(version, problems, latest=LATEST_VERSION):
    """(開けるか, 説明) を返す。開くときに起きること（db/schema_version.ensure_schema()）と同じ順で判定する。"""
    if version > latest:
        return False, f"開けない: アプリより新しい版（{version}）。アプリを最新版に更新する"
    if version == 0:
        if problems:
            return False, "開けない: 古い形式（足りないもの: " + "、".join(problems) + "）。新しく作り直す"
        if latest > BASELINE_VERSION:
            return True, f"開ける: 版の記録なし。開くと {BASELINE_VERSION} を書き、{latest} までマイグレーションを適用する（自動でバックアップを取る）"
        return True, f"開ける: 版の記録なし。開くと {BASELINE_VERSION} を書く"
    if version < latest:
        return True, f"開ける: 版 {version}。開くと {latest} までマイグレーションを適用する（自動でバックアップを取る）"
    return True, f"開ける: 版 {version}（最新）"


def check_folder(db_root, latest=LATEST_VERSION):
    """db_root/<名前>/inventory.db をすべて調べ、[(パス, 開けるか, 説明)] を返す（名前の順）。"""
    results = []
    if not os.path.isdir(db_root):
        return results
    for name in sorted(os.listdir(db_root)):
        path = os.path.join(db_root, name, "inventory.db")
        if not os.path.isfile(path):
            continue
        try:
            version, problems = check_version_readonly(path)
        except Exception as e:  # 壊れたファイルなど。調べられなかったことだけを示す
            results.append((path, False, f"調べられない: {e}"))
            continue
        ok, message = judge(version, problems, latest)
        results.append((path, ok, message))
    return results


def main(argv):
    if len(argv) > 1:
        roots = argv[1:]
    else:
        import config
        roots = [os.path.join(config.APP_DATA_DIR, "db")]
    print(f"このアプリが扱える版: {LATEST_VERSION}（基準 {BASELINE_VERSION}）")
    unopenable = 0
    for root in roots:
        print(f"\n[{os.path.abspath(root)}]")
        results = check_folder(root)
        if not results:
            print("  （月次DB が見つからない）")
        for path, ok, message in results:
            print(f"  {os.path.basename(os.path.dirname(path))}: {message}")
            unopenable += 0 if ok else 1
    print(f"\n開けない DB: {unopenable} 件")
    return 1 if unopenable else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
