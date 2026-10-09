"""コメントと docstring 以外が変わっていないことを確認する。

使い方（リポジトリのルートで実行）:
    python scripts/check_code_unchanged.py ui/kitting_production_entry.py
    python scripts/check_code_unchanged.py ui/kitting_production_entry.py HEAD~1

第2引数は比較元のコミット（省略時は HEAD）。
作業ツリーのファイルと比較し、構文木が一致すれば OK を表示する。
"""
import ast
import subprocess
import sys

DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def normalized(source: str) -> str:
    """docstring を取り除いた構文木を文字列にする（コメントは構文木に含まれない）。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, DOCSTRING_OWNERS) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            # docstring だけの本体と pass だけの本体を同じものとして扱う
            node.body = node.body[1:] or [ast.Pass()]
    return ast.dump(tree)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = sys.argv[1].replace("\\", "/")
    rev = sys.argv[2] if len(sys.argv) > 2 else "HEAD"

    # "./" を付けると、git がリポジトリ直下ではなく実行中のフォルダからのパスとして解釈する
    git_path = path if path.startswith(("./", "../", "/")) else f"./{path}"
    shown = subprocess.run(["git", "show", f"{rev}:{git_path}"], capture_output=True)
    if shown.returncode != 0:
        print(f"比較元を取得できません: {rev}:{path}")
        print(shown.stderr.decode("utf-8", errors="replace"))
        return 2
    old = shown.stdout.decode("utf-8-sig")
    with open(path, encoding="utf-8-sig", newline="") as f:
        new = f.read()

    old_lines = len(old.splitlines())
    new_lines = len(new.splitlines())
    print(f"行数: {old_lines} -> {new_lines}（{new_lines - old_lines:+d}）")

    if normalized(old) == normalized(new):
        print("OK: コメントと docstring 以外は変わっていません")
        return 0
    print("NG: コードが変わっています。git diff で確認してください")
    return 1


if __name__ == "__main__":
    sys.exit(main())
