# version.py
"""
アプリのバージョン番号・ビルド日付の唯一の参照元。

バージョン番号の付け方（3桁、ピリオド区切り）：
    1桁目：データの互換性が変わるような大きな変更
    2桁目：機能の追加・変更を含む再ビルド
    3桁目：不具合修正や小さな調整での再ビルド

再ビルドのたびに、本ファイルのAPP_VERSION・BUILD_DATEを更新してから
ビルドすること（CANONICAL_DESIGN_DECISIONS.md・CHANGELOG.md参照）。
ログイン画面・メインメニューのウィンドウタイトル（ui/login_window.py・
ui/main_window.py）、.exeのファイルバージョン情報（inventory_app.spec、
version_info.txt経由）は、いずれも本ファイルの値から導出する
（バージョン番号を複数箇所で二重管理しない）。
"""
APP_VERSION = "1.2.0"
BUILD_DATE = "2026-10-07"


def get_version_label() -> str:
    """
    ウィンドウタイトル表示用の文字列を返す（例："v1.0.0（2026-10-06）"）。
    開発環境（config.IS_FROZEN が False）では、.exeと見分けられるよう
    末尾に「開発版」を付ける。
    """
    import config

    label = f"v{APP_VERSION}（{BUILD_DATE}）"
    if not config.IS_FROZEN:
        label += " 開発版"
    return label
