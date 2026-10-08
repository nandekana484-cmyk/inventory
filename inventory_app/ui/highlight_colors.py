# ui/highlight_colors.py
"""
アプリ全体で再利用する行ハイライト色の定義（2026-10-08新設）。

同じ意味（警告・不一致・要注意）の赤系ハイライトが、複数の画面にそれぞれ
個別のリテラル値として重複して書かれていた：
  - ui/plan_candidate_dialog.py："large_diff_both"（数量差・日付差の両方が
    大きい候補）
  - ui/production_import_staging_window.py："large_diff_both"（同上）
  - ui/pdf_ocr_import_window.py："low_confidence"（OCR読み取りの信頼度が低い行）
  - ui/production_side_master_window.py：`_ROW_BG_DELETED`（削除予定の行）

値を1箇所に集約し、今後色を見直す際に1箇所で反映できるようにする
（本モジュール新設自体では、各画面の見た目・既存のタグ名はいずれも
変更していない）。
"""

# 警告・不一致・要注意を表す赤系ハイライト（上記4箇所で使用）。
MISMATCH_RED = "#ffb3b3"
