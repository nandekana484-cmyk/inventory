# services/app_settings_service.py
"""
アプリの永続設定（現在選択中のDBパス等）を、config.APP_DATA_DIR配下の
JSONファイル（app_settings.json）として保存・読み込みするサービス。

config.set_db_path()から呼ばれ、DB切り替えのたびに自動的に永続化される
（ui.main_window.MainWindow.__init__()が起動時にload_last_db_path()を読み、
前回終了時に選択していたDBへ自動的に再接続するために使う）。

config.pyから本モジュールを直接（モジュールトップレベルで）importすると、
本モジュールのimport configとの間で循環importになるため、config.py側では
set_db_path()関数の内部でのみ遅延importする（呼び出し時点では両モジュールの
読み込みが完了しているため問題にならない）。
"""
import json
import os

import config

_SETTINGS_FILENAME = "app_settings.json"


def _settings_path() -> str:
    return os.path.join(config.APP_DATA_DIR, _SETTINGS_FILENAME)


def _read_settings() -> dict:
    """
    設定ファイルの中身を辞書で返す。ファイルが存在しない・JSONとして
    壊れている・トップレベルがオブジェクトでない場合は、いずれも空の辞書を
    返す（例外を送出しない。呼び出し元がデフォルト値にフォールバックしやすい
    ようにするため）。
    """
    path = _settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_settings(settings: dict) -> None:
    os.makedirs(config.APP_DATA_DIR, exist_ok=True)
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def save_last_db_path(path: str) -> None:
    """
    現在選択中のDBパスを永続化する。

    既存の設定内容を読み込んだ上で"last_db_path"キーだけを更新する
    read-modify-write方式にしている（将来、他の設定キーが増えた場合に
    それらを消さずに残すため）。

    書き込み自体に失敗した場合（共有フォルダの瞬断・権限問題等）は、例外を
    送出せずそのまま何もしない。永続化はあくまで利便性のための付加機能であり、
    これが失敗したからといってDB切り替え自体（config.set_db_path()の本来の
    処理）を止めるべきではないため。
    """
    settings = _read_settings()
    settings["last_db_path"] = path
    try:
        _write_settings(settings)
    except OSError:
        pass


def load_last_db_path():
    """
    前回終了時に選択されていたDBパスを返す。設定ファイルが存在しない・
    壊れている・該当キーが無い・値が空文字列等の場合はいずれもNoneを返し、
    呼び出し元（ui.main_window.MainWindow.__init__()）がデフォルト値に
    フォールバックできるようにする。
    """
    value = _read_settings().get("last_db_path")
    return value if isinstance(value, str) and value else None


def save_shared_db_root(path: str) -> None:
    """
    共有フォルダ上の月別DB一覧（ui.shared_db_list_window.SharedDbListWindow）で
    指定した「親ディレクトリ」のパスを永続化する。save_last_db_path()と同じ
    read-modify-write方式・同じ「失敗しても例外を投げない」方針
    （一覧画面の再スキャン自体を止めるべきではないため）。
    """
    settings = _read_settings()
    settings["shared_db_root"] = path
    try:
        _write_settings(settings)
    except OSError:
        pass


def load_shared_db_root():
    """
    前回指定された共有フォルダの親ディレクトリパスを返す。設定ファイルが
    存在しない・壊れている・該当キーが無い・値が空文字列等の場合は
    いずれもNoneを返す（load_last_db_path()と同じ方針）。
    """
    value = _read_settings().get("shared_db_root")
    return value if isinstance(value, str) and value else None
