# ui/common/highlight_colors.py
"""
アプリ全体で再利用する行ハイライト色の定義（2026-10-08新設）。

同じ意味（警告・不一致・要注意）の赤系ハイライトが、複数の画面にそれぞれ
個別のリテラル値として重複して書かれていた：
  - ui/kitting/plan_candidate_dialog.py："large_diff_both"（数量差・日付差の両方が
    大きい候補）
  - ui/production/production_import_staging_window.py："large_diff_both"（同上）
  - ui/system/pdf_ocr_import_window.py："low_confidence"（OCR読み取りの信頼度が低い行）
  - ui/master/production_side_master_window.py：`_ROW_BG_DELETED`（削除予定の行）

値を1箇所に集約し、今後色を見直す際に1箇所で反映できるようにする
（本モジュール新設自体では、各画面の見た目・既存のタグ名はいずれも
変更していない）。
"""

# 警告・不一致・要注意を表す赤系ハイライト（上記4箇所で使用）。
MISMATCH_RED = "#ffb3b3"

# 実績CSV取込の候補一覧・生産実績入力の計画一覧の行の色（2026-10-20、凡例と行の色付けで同じ値を使うため、ここへ集めた。値は以前のまま。D-181）。
AUTO_CONFIRMABLE_GREEN = "#c8f7c5"      # Shift+S で登録できる（数量一致・日付差1日以内）
QTY_DIFF_YELLOW = "#fff3cd"             # 数量の差が実績数の20%を超える
DATE_DIFF_ORANGE = "#ffd9a0"            # 日付の差が3日以上
SIDE2_WAIT_BLUE = "#cfe2ff"             # 面2待ち（生産面マスターでは面2があるのに、面2の計画が無い面1）
SIDE_MASTER_UNREGISTERED_GRAY = "#e2e3e5"  # 生産面マスター未登録の面1
SIMILAR_LOT_PURPLE = "#e9d8fd"          # 似たロットNo（末尾「-」だけ違うロット）の計画（D-177）
REGISTERED_TEXT_GRAY = "gray"           # 登録済みの候補の文字色
