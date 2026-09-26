# services/pdf_ocr_service.py
"""
PDFファイルを読み取り、行ごとに列分解してCSV化するサービス。

テキストPDF（文字情報を内部に持つPDF）と画像PDF（スキャンした紙をそのまま
画像として埋め込んだだけのPDF、いわゆる「テキスト層が無い」PDF）の両方に
対応する。判定方法は以下の2段構え：

  1. pdfplumberで各ページの extract_text() を試みる。
     全ページ合計の文字数が_MIN_TEXT_LENGTH_THRESHOLD未満（空、またはノイズ的な
     ごく少量の文字しか取れない）であれば「テキスト層が実質無い」とみなす。
  2. その場合のみ、pdf2imageでページを画像化し、pytesseractでOCR
     （日本語＋数字・英字混在を認識するため lang="jpn+eng"）を行う。

列分解（96コード・名称・数量等への分割）について：
実際のPDFのレイアウト（列の区切り方・並び順）が未確定のため、現時点では
「1行を空白・タブの連続で分割する」という素朴な実装に留めている
（`_split_line_to_columns()`）。実運用のPDFレイアウトが判明した時点で、
固定長カラム・特定の区切り文字・正規表現によるパターンマッチ等への
差し替えが必要になる可能性が高い。

前提となる外部依存（OS側に別途インストールが必要）：
  - Tesseract OCR本体（config.TESSERACT_CMD、日本語データconfig.TESSDATA_DIR/
    jpn.traineddata）
  - Poppler（config.POPPLER_PATH、pdf2imageがPDF→画像変換に使う外部ツール）
"""
import csv
import logging
import os
import re
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime

import cv2
import numpy as np
import pdfplumber
import pytesseract
from PIL import Image
from pdf2image import convert_from_path

import config
from models.inventory import list_inventory_part_nos

# PDF読取り処理（テキスト抽出・OCRいずれも）の開始・完了・エラーを記録する
# 専用ロガー（2026-09-25追加）。config.LOG_DIR配下に日付ごとのログファイル
# （pdf_ocr_YYYYMMDD.log）を出力する。異常終了時（BrokenProcessPool等、
# ProcessPoolExecutorのワーカープロセスが強制終了された場合を含む）は
# ui.pdf_ocr_import_window側のexcept Exceptionでダイアログ表示されるのみで
# ログには何も残らなかった（別途調査済みの問題）ため、その対策として追加した。
#
# logging.getLogger(__name__)を使う（既存のservices/bom_service.py等と同じ
# 命名規約）。ただし他のロガーと異なりFileHandlerを実際に設定する（既存の
# ロガーはハンドラ未設定のままのため、実質どこにも出力されていなかった）。
# propagate = Falseにして、ルートロガー（Tkinter等が設定する可能性がある
# 既定のハンドラ）に二重出力されないようにする。
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False

# 直近にFileHandlerを設定した日付（"YYYYMMDD"）。ログファイル名に日付を
# 含めるため、日付が変わったらハンドラを張り替える必要がある。アプリを
# 起動しっぱなしで日をまたぐケースを想定した最小限の対応（ログ出力の
# たびに日付をチェックするだけで、専用のスケジューラ等は用いない）。
_ocr_log_handler_date = None


def _ocr_log_file_path() -> str:
    return os.path.join(config.LOG_DIR, f"pdf_ocr_{datetime.now().strftime('%Y%m%d')}.log")


def _ensure_ocr_log_handler():
    """
    loggerに、当日分のログファイルへのFileHandlerが設定されていることを
    保証する。日付が変わっていれば、古いハンドラを閉じて新しい日付の
    ファイル用ハンドラに張り替える。
    """
    global _ocr_log_handler_date
    today = datetime.now().strftime('%Y%m%d')
    if _ocr_log_handler_date == today and logger.handlers:
        return

    os.makedirs(config.LOG_DIR, exist_ok=True)
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    handler = logging.FileHandler(_ocr_log_file_path(), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    _ocr_log_handler_date = today


def get_pdf_page_count(pdf_path: str) -> int:
    """
    PDFのページ数のみを取得する（ログ出力用の軽量ヘルパー、2026-09-25追加）。
    extract_pdf_rows()自体もページ数を内部で取得するが、extract_pdf_rows()が
    テキスト抽出・OCRの途中で例外を送出した場合、呼び出し元（UI側）は
    ページ数を知る手段が無くエラーログにページ数を残せない。そのため、
    UI側で処理開始前・例外発生時にこの関数を個別に呼び、ページ数を
    ログに残せるようにする（pdfplumber.open()でページ数を数えるだけの
    処理のため、OCRやテキスト抽出本体に比べて呼び出しコストは軽微）。
    """
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def log_ocr_start(pdf_path: str, page_count):
    """
    PDF読取り処理の開始をログに記録する（2026-09-25追加）。page_countは
    Noneの場合「不明」と記録する（呼び出し元がページ数取得自体に失敗した
    場合を想定）。
    """
    _ensure_ocr_log_handler()
    logger.info(
        "[開始] file=%s page_count=%s",
        os.path.basename(pdf_path), page_count if page_count is not None else "不明",
    )


def log_ocr_complete(pdf_path: str, page_count, elapsed_seconds: float, method: str, row_count: int):
    """PDF読取り処理の正常完了をログに記録する（2026-09-25追加）。"""
    _ensure_ocr_log_handler()
    logger.info(
        "[完了] file=%s page_count=%s method=%s rows=%d elapsed=%.1fs",
        os.path.basename(pdf_path), page_count, method, row_count, elapsed_seconds,
    )


def log_ocr_error(pdf_path: str, page_count, elapsed_seconds: float, error_text: str):
    """
    PDF読取り処理中の例外（BrokenProcessPool等、ProcessPoolExecutorの
    ワーカープロセスが異常終了した場合を含む）をログに記録する（2026-09-25
    追加）。error_textには呼び出し元でtraceback.format_exc()により取得した
    スタックトレースを含む文字列を渡す想定。
    """
    _ensure_ocr_log_handler()
    logger.error(
        "[エラー] file=%s page_count=%s elapsed=%.1fs\n%s",
        os.path.basename(pdf_path), page_count if page_count is not None else "不明",
        elapsed_seconds, error_text,
    )

# 全ページ合計でこの文字数未満しか抽出できなければ、テキスト層が実質無い
# （スキャン画像PDF等）とみなしOCR経路にフォールバックする。テキストPDFでも
# 空白ページ等が混ざるケースを考慮し、0ではなくある程度の閾値を設ける。
_MIN_TEXT_LENGTH_THRESHOLD = 20

# pytesseractに渡す言語コード。96コード等の数字・英字と、部品名称等の日本語
# 文字列が混在するため、日本語＋英数字の両方を認識対象にする。
_OCR_LANGUAGES = "jpn+eng"

# 複数ページOCRの並列ワーカー数の上限（2026-09-25追加）。事務用PC
# （コア数が限られる想定、Core i5相当）での過剰な負荷を避けるため、
# os.cpu_count()を基準にしつつ最大3に抑える。実測では1ワーカーあたりの
# OCR処理自体がCPUを使い切る重い処理（前処理のOpenCV演算＋Tesseractの
# 認識処理）のため、コア数以上に並列化しても速度向上は見込めない一方、
# 事務PCで他の作業を妨げないよう、コア数を使い切らない余裕を残す。
_MAX_OCR_WORKERS = 3

# バックプレッシャー制御（2026-09-25追加）：画像化（convert_from_path()）と
# OCR投入（ProcessPoolExecutor.submit()）のパイプライン化で、画像化の方が
# OCRより速い場合（実データで想定されるノイズの多いスキャン画像等でOCRが
# 遅くなるケース）、未処理（投入済みだが結果未回収）のFutureに紐づく画像が
# 際限なく積み上がりメモリを圧迫することが調査により判明した（詳細は
# 2026-09-25の調査報告参照）。そのため、未処理Futureの数がこの値
# （ワーカー数＋_BACKPRESSURE_EXTRA_FUTURES）を超えたら、画像化を一時停止し
# いずれか1つが完了するまで待つ。ワーカー数に対して余裕（+2）を持たせるのは、
# ワーカーが遊ばないよう「今処理中のページ」に加えて「次にすぐ渡せる
# 画像化済みページ」を多少確保しておくため（余裕が無さすぎるとワーカーの
# 手待ちが発生し、速度低下につながる）。
_BACKPRESSURE_EXTRA_FUTURES = 2

# 96コードのパターン："96"で始まる7〜8桁の数字（既存のBOM/在庫関連コードと
# 同じ想定。例："96000123"）。名称・数量列を誤って96コードと判定しないよう、
# 行内の各セルをこのパターンで厳密一致（fullmatch相当）判定する。
_PART_NO_PATTERN = re.compile(r"^96\d{5,6}$")


def _configure_tesseract():
    """
    pytesseractに、config.pyで指定されたTesseract本体・tessdataディレクトリを
    設定する。呼び出しのたびに設定し直しても副作用は無い（単純な属性代入・
    環境変数代入のため）。

    tessdataディレクトリの指定は環境変数TESSDATA_PREFIXで行う
    （pytesseractのimage_to_string()にconfig='--tessdata-dir "<path>"'を
    渡す方式も試したが、pytesseractが内部でconfig文字列をトークン分割して
    tesseract.exeへ渡す際に手動で付けた引用符がそのままリテラル文字として
    扱われてしまい、パスの末尾に引用符が付いた不正なパスとしてTesseract側で
    解釈され読み込みに失敗した。環境変数経由であればこの問題を回避できる）。
    """
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
    os.environ["TESSDATA_PREFIX"] = config.TESSDATA_DIR


def _extract_text_via_pdfplumber(pdf_path: str) -> str:
    """
    pdfplumberで各ページのテキストを抽出し、ページ区切りを改行2つで連結して返す。
    抽出できるテキストが無いページは空文字列として扱う（例外にしない）。
    """
    page_texts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            page_texts.append(text)
    return "\n\n".join(page_texts)


def _deskew(gray: np.ndarray) -> np.ndarray:
    """
    グレースケール画像の傾きを推定し、水平になるよう回転補正する。

    手法：Otsu二値化で文字らしき画素を抽出し、その全画素の外接矩形
    （cv2.minAreaRect）の傾きを画像全体の傾きとみなして回転する
    （pyimagesearch等で広く紹介されている標準的なdeskewレシピ）。
    ハフ変換による直線検出も検討したが、罫線が明確でない一般的な文書では
    直線検出が不安定になりやすく、テキスト塊全体の外接矩形を使う本方式の
    方が実装が単純で頑健なためこちらを採用した。

    角度がごく小さい（0.5度未満）場合は回転をスキップする。ほぼ傾いて
    いないクリーンな画像に不要な回転・補間をかけて画質を落とさないための
    安全策（合成テスト画像等、元々傾きが無い画像の認識精度を維持するため）。
    """
    thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    coords = cv2.findNonZero(thresh)
    if coords is None:
        # 真っ白（文字が無い）画像等、判定材料が無ければ何もしない。
        return gray

    angle = cv2.minAreaRect(coords)[-1]
    # cv2.minAreaRect()が返す角度は[-90, 0)の範囲。矩形の長辺・短辺の
    # 取り方により符号の意味が変わるため、-45度を境に補正方向を調整する
    # （標準的なdeskewレシピの調整式と同じ）。
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.5:
        return gray

    (h, w) = gray.shape[:2]
    center = (w // 2, h // 2)
    rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        gray, rotation_matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def preprocess_image_for_ocr(image: Image.Image, debug_dir: str = None, debug_prefix: str = "page") -> Image.Image:
    """
    OCR実行前の画像前処理。pdf2imageが返すPIL Imageを受け取り、前処理後の
    PIL Image（二値化済み、モード"L"相当のグレースケール配列から生成）を返す。

    採用した手法と理由：
      1. グレースケール化：OCRに色情報は不要なため、以降の処理を単純化する。
      2. 傾き補正：_deskew()参照。スキャン時のわずかな回転はTesseractの
         認識精度を大きく下げるため、傾きが一定以上ある場合のみ回転補正する。
      3. ノイズ除去：cv2.bilateralFilter（バイラテラルフィルタ）を採用
         （2026-09-25、高速化のため見直し。以前はNon-Local Means Denoising
         （cv2.fastNlMeansDenoising）を使っていたが、実測で前処理全体の
         約97%（1画像あたり約1.3秒）を占める最大のボトルネックだった）。
         まずcv2.medianBlur（中央値フィルタ、カーネルサイズ3・5・7）を
         試したが、傾き・低コントラスト・ノイズを加えた劣化画像テストで
         現状（fastNlMeansDenoising）より明らかにOCR精度が落ちる
         （行の脱落・文字の誤認識が増える）ことを実験で確認したため採用を
         見送った。bilateralFilterは、エッジ（文字の輪郭）を保ちながら
         平坦部を平滑化する点でfastNlMeansDenoisingと似た性質を持ちつつ
         計算コストが大幅に低く、同じ劣化画像テストでfastNlMeansDenoisingと
         同等の認識結果を維持できることを確認した上で採用した。
      4. コントラスト強調：CLAHE（Contrast Limited Adaptive Histogram
         Equalization）を採用。単純なヒストグラム均等化（cv2.equalizeHist）は
         画像全体を一様に引き伸ばすため、スキャン画像にありがちな「用紙の
         一部だけ影で暗い」といった局所的な明暗差には効果が薄い。CLAHEは
         画像を小領域（タイル）に分けて局所的に均等化するため、こうした
         局所的な低コントラストに対してより効果的である。
      5. 二値化：適応的二値化（cv2.adaptiveThreshold、ガウシアン重み付き）で
         最終的に黒文字・白背景の二値画像にする。Tesseract自身も内部で
         二値化を行うが、照明ムラのある画像では事前に適応的二値化を
         施しておいた方が安定した結果が得られやすいとされる。

    処理順序（グレースケール→傾き補正→ノイズ除去→コントラスト強調→二値化）は、
    先に地の傾き・ノイズを整えてからコントラスト強調・二値化を行うことで、
    ノイズがコントラスト強調によって不必要に強調されるのを避ける意図がある。

    debug_dir（既定None、オフ）を指定すると、前処理前後の画像を
    "<debug_prefix>_00_original.png"・"<debug_prefix>_01_preprocessed.png"
    としてこのフォルダに保存する（開発時に前処理の効果を目視比較する用途）。
    """
    img_bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        cv2.imwrite(os.path.join(debug_dir, f"{debug_prefix}_00_original.png"), img_bgr)

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = _deskew(gray)

    # バイラテラルフィルタ（cv2.bilateralFilter）を採用（2026-09-25見直し）。
    # cv2.medianBlur（カーネルサイズ3・5・7）も検討したが、劣化画像テスト
    # （傾き・低コントラスト・ノイズを加えたテスト画像）でfastNlMeansDenoising
    # （旧手法）より明らかに認識精度が落ちることを確認したため見送った。
    # bilateralFilterはエッジを保ちながら平坦部を平滑化する点でfastNlMeans
    # Denoisingに近い性質を持ちつつ計算コストが大幅に低く（実測で1画像あたり
    # 1.3秒→0.01秒未満）、同じ劣化画像テストで旧手法と同等の認識結果を
    # 維持できることを確認した。d=9（近傍直径）・sigmaColor=sigmaSpace=75は
    # OpenCV公式ドキュメントが「中程度〜強めのノイズ除去用」として例示する
    # 値をそのまま採用した。
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast_enhanced = clahe.apply(denoised)

    # ブロックサイズ・定数Cは、上記denoisingを経てもなお残るわずかな濃淡差で
    # 誤って二値化されないよう、単純なガウシアンブラー前提だった値
    # （31, 15）よりもブロックサイズを大きく・定数Cを小さくする方向に調整した。
    binarized = cv2.adaptiveThreshold(
        contrast_enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11,
    )

    if debug_dir:
        cv2.imwrite(os.path.join(debug_dir, f"{debug_prefix}_01_preprocessed.png"), binarized)

    return Image.fromarray(binarized)


def _group_words_into_line_records(data: dict) -> list:
    """
    pytesseract.image_to_data(output_type=Output.DICT)の戻り値
    （"text"・"conf"・"left"・"top"・"width"・"height"・"block_num"・
    "par_num"・"line_num"等の列ごとの並列リスト）を、(block_num, par_num,
    line_num)単位でグルーピングし、行ごとのレコードのリストに変換する
    （2026-09-25、座標ベースの列復元（_detect_column_boundaries()・
    _assign_words_to_columns()）のため、単語の座標（left/top/width/height）を
    保持する形に拡張。以前は(テキスト, 平均信頼度)のペアのみを返す
    _group_words_into_lines()という名前だったが、座標を返すようになった
    実態に合わせて改名した）。

    conf == -1 の要素（単語ではない、行・段落・ブロック等の構造情報のみを
    表す行。pytesseractの仕様）は、空文字列の単語同様に読み飛ばす
    （テキスト連結・信頼度集計のいずれにも含めない）。

    グループの並び順は(block_num, par_num, line_num)の昇順とする。単一列の
    通常のレイアウトであれば、この順序はおおむね読み順（上から下）と一致する。
    各行内の単語はleft昇順（左から右）で並べる。

    line_text（半角スペース区切りで単語を連結したもの）は、座標ベースの
    列復元が失敗した場合のフォールバック（_build_rows_from_line_records()
    参照）・raw_textの構築にのみ使う。日本語（CJK）はpytesseract.
    image_to_data()では文字1つずつが別々の"word"として返ってくることが
    多いため、line_text単体では列復元の主経路として不十分である
    （_split_line_to_columns()による空白区切りが日本語部分を意図せず
    細かく分割してしまう問題が過去に判明済み。座標ベースの列復元を
    新設した動機そのもの）。

    戻り値：[{"words": [{"text","left","top","width","height"}, ...]
              （left昇順）, "line_text": str, "avg_confidence": float}, ...]
    """
    lines_by_key = {}
    word_count = len(data["text"])
    for i in range(word_count):
        word = data["text"][i].strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < 0:
            continue

        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        entry = lines_by_key.setdefault(key, {"words": [], "confs": []})
        entry["words"].append({
            "text": word, "left": data["left"][i], "top": data["top"][i],
            "width": data["width"][i], "height": data["height"][i],
        })
        entry["confs"].append(conf)

    records = []
    for key in sorted(lines_by_key.keys()):
        entry = lines_by_key[key]
        words_sorted = sorted(entry["words"], key=lambda w: w["left"])
        line_text = " ".join(w["text"] for w in words_sorted)
        avg_confidence = sum(entry["confs"]) / len(entry["confs"])
        records.append({"words": words_sorted, "line_text": line_text, "avg_confidence": avg_confidence})
    return records


# 座標ベースの列復元（案A、2026-09-25追加）で検出された列数がこの範囲外の
# 場合、ヘッダー行の検出自体に失敗した（表形式ではない・単純すぎる／複雑
# すぎるレイアウト）とみなし、_split_line_to_columns()（空白区切り）に
# フォールバックする。1列（実質的に列分割になっていない）や、極端に多い
# 列数（隙間ベースのクラスタリングが細切れになりすぎた誤検出）を弾く
# ための安全弁。
_MIN_DETECTED_COLUMNS = 2
_MAX_DETECTED_COLUMNS = 30

# 列境界とみなす「大きい隙間」の判定に使う倍率。ヘッダー行の単語間の隙間の
# 中央値（＝同一列内の文字間隔の典型値とみなす）に対して、この倍率を超える
# 隙間があれば列の区切りと判定する。実際の表形式テストPDF（10列）で、
# 列内の隙間（数px）と列間の隙間（250px超）の差が非常に大きく、2〜30倍の
# 範囲であれば同じ結果になることを確認済み（余裕を持たせた値として3を採用）。
_COLUMN_GAP_MEDIAN_MULTIPLIER = 3


def _detect_column_boundaries(header_words: list) -> list:
    """
    ヘッダー行（先頭の行と推定）の単語群（_group_words_into_line_records()の
    1行分の"words"、left昇順である必要はない）から、隙間ベースのクラスタ
    リングで列境界を検出する（案A：ヘッダー行から列境界を検出し、以降の
    データ行をその境界に沿って列分けする方式）。

    アルゴリズム：単語をleft座標順に並べ、隣接する単語間の隙間（前の単語の
    右端から次の単語の左端までの距離）を全て求める。列内の文字間隔（小さい
    隙間）と列と列の間の隙間（大きい隙間）は実データ上ほぼ1桁以上の差が
    あることを実際の表形式テストPDFで確認済みのため、全隙間の中央値の
    _COLUMN_GAP_MEDIAN_MULTIPLIER倍を閾値とし、これを超える隙間の直後で
    新しい列（クラスタ）を開始する。列幅を事前に知っている必要はない
    （ページ・フォントサイズが変わっても自動的に追従する）。

    単語が0個の場合は空リストを返す（列境界を検出できない＝呼び出し元で
    フォールバック対象と判断させる）。単語が1個のみの場合は、その単語の
    範囲をそのまま唯一の列として返す。

    戻り値：[(col_left, col_right), ...]（左端座標の昇順）。
    """
    if not header_words:
        return []

    sorted_words = sorted(header_words, key=lambda w: w["left"])
    if len(sorted_words) == 1:
        w = sorted_words[0]
        return [(w["left"], w["left"] + w["width"])]

    gaps = []
    for i in range(1, len(sorted_words)):
        prev_word = sorted_words[i - 1]
        cur_word = sorted_words[i]
        gap = cur_word["left"] - (prev_word["left"] + prev_word["width"])
        gaps.append(gap)

    sorted_gaps = sorted(gaps)
    median_gap = sorted_gaps[len(sorted_gaps) // 2]
    # 中央値が0以下（文字同士が密着・重複している等）の場合のゼロ（以下）
    # 閾値による誤分割を避けるため、最低でも1pxは確保する。
    threshold = max(median_gap * _COLUMN_GAP_MEDIAN_MULTIPLIER, 1)

    clusters = [[sorted_words[0]]]
    for i in range(1, len(sorted_words)):
        if gaps[i - 1] > threshold:
            clusters.append([])
        clusters[-1].append(sorted_words[i])

    boundaries = []
    for cluster in clusters:
        left = min(w["left"] for w in cluster)
        right = max(w["left"] + w["width"] for w in cluster)
        boundaries.append((left, right))
    return boundaries


def _assign_words_to_columns(row_words: list, column_boundaries: list) -> list:
    """
    1行分の単語群（_group_words_into_line_records()の1行分の"words"）を、
    _detect_column_boundaries()が返した列境界に沿って列分けする。

    各単語の中心x座標（left + width/2）と、各列の中心座標（column_boundariesの
    (left+right)/2）との距離が最も近い列に割り当てる（単語が列境界の範囲を
    わずかにはみ出す場合でも、最も近い列に自然に救済されるようにするため。
    範囲内かどうかで厳密に判定する方式は、文字の描画位置のわずかなズレで
    どの列にも属さない単語が出てしまうリスクがあるため採用しなかった）。

    同一列に複数の単語が割り当てられた場合は、間にスペースを挟まず直接
    連結する（例："図面"+"001" → "図面001"）。実際の表形式テストPDFで、
    Tesseractが1つのセルの内容（日本語＋数字の組み合わせ等）を複数の
    "word"に分裂させるケースを確認しており、いずれもスペース無しで連結
    した方が元のセル内容に近い結果になることを確認済み（英語の複数単語
    から成る値（例："PART NAME"）を1セルに収める場合はこの限りではないが、
    本アプリが対象とする日本語主体の帳票（96コード・図名・型名等）では
    スペース無し連結が実データの傾向に合致するとの判断）。

    戻り値：[cell1, cell2, ...]（len(column_boundaries)件、該当する単語が
    無い列は空文字列""）。
    """
    n_cols = len(column_boundaries)
    cell_words = [[] for _ in range(n_cols)]
    col_centers = [(left + right) / 2 for left, right in column_boundaries]

    for word in sorted(row_words, key=lambda w: w["left"]):
        word_center = word["left"] + word["width"] / 2
        col_idx = min(range(n_cols), key=lambda i: abs(col_centers[i] - word_center))
        cell_words[col_idx].append(word["text"])

    return ["".join(texts) for texts in cell_words]


# 罫線付きの表の行を再構成する際、直前の行との代表Y座標の差が「直前の行の
# 文字高さの典型値×この倍率」を超えたら別の行とみなす。文字高さに対する
# 比率にする理由：フォントサイズ・dpiが変わっても、同じ行内の複数セルは
# ほぼ同じY座標に並ぶ（差は文字高さよりずっと小さい）のに対し、行が変われば
# 差は少なくとも文字高さ程度は生じるはずだという前提に基づく（実データで
# 0.6という値により正しく行が分離されることを確認済み）。
_ROW_GAP_HEIGHT_RATIO = 0.6


def _group_line_records_into_rows(line_records: list) -> list:
    """
    _group_words_into_line_records()が返す行レコード（Tesseractの
    block_num/par_num/line_numによるグルーピング結果）を、実際の表の
    「視覚的な行」単位に再グルーピングする（2026-09-25、列復元機能の実装中に
    発見した問題への対処として追加）。

    発見の経緯：罫線付きの表画像（本アプリが主な対象とする帳票の想定
    レイアウト）に対し、tessdata_fast採用後のTesseractでOCRしたところ、
    ページ分割が罫線をブロック境界と解釈し、表の1つの視覚的な行に含まれる
    複数のセルが、それぞれ別々の(block_num, par_num, line_num)としてバラバラに
    認識されることが実データ検証で判明した（例：ヘッダー行の「シリーズ」
    「コード」「型名」...が、それぞれ独立した行として扱われる）。そのため、
    _group_words_into_line_records()の「行」は表の視覚的な行と必ずしも
    一致せず、この関数による事前の再グルーピングを経ないと、_detect_column_
    boundaries()・_assign_words_to_columns()が正しく機能しない
    （1つのセルしか含まない「行」から列境界を検出しようとしてしまう等）。

    アルゴリズム：各行レコードの代表Y座標（先頭の単語のtop、既にleft昇順の
    ため実質的に行内で最も左の単語のtop）でソートし、直前の行との差が
    「直前の行の文字高さの平均×_ROW_GAP_HEIGHT_RATIO」以内であれば同じ行と
    みなして単語をまとめる。まとめる際、行レコード同士はそれぞれの先頭
    単語のleft座標順に並べる（表の列順＝左から右の順序を保つため）。

    タイトル行等、罫線が無く元々1つの(block_num, par_num, line_num)に
    正しくまとまっている行は、この関数を通しても実質的に変化しない
    （前後の行との代表Y座標の差が文字高さよりずっと大きいため、単独の
    グループのまま残る）。

    戻り値：_group_words_into_line_records()と同じ形式（[{"words",
    "line_text", "avg_confidence"}, ...]）の、行単位に統合済みのリスト。
    """
    if not line_records:
        return []

    def representative_top(record):
        return record["words"][0]["top"] if record["words"] else 0

    def average_height(record):
        heights = [w["height"] for w in record["words"]]
        return sum(heights) / len(heights) if heights else 20

    sorted_records = sorted(line_records, key=representative_top)

    row_groups = [[sorted_records[0]]]
    for record in sorted_records[1:]:
        prev_record = row_groups[-1][-1]
        gap = representative_top(record) - representative_top(prev_record)
        if gap > average_height(prev_record) * _ROW_GAP_HEIGHT_RATIO:
            row_groups.append([record])
        else:
            row_groups[-1].append(record)

    merged_rows = []
    for group in row_groups:
        group_sorted = sorted(group, key=lambda r: r["words"][0]["left"] if r["words"] else 0)
        words = []
        texts = []
        confs = []
        for record in group_sorted:
            words.extend(record["words"])
            texts.append(record["line_text"])
            confs.append(record["avg_confidence"])
        merged_rows.append({
            "words": words,
            "line_text": " ".join(texts),
            "avg_confidence": sum(confs) / len(confs) if confs else 0.0,
        })
    return merged_rows


def _find_header_row_index(line_records: list):
    """
    line_recordsの中から「表のヘッダー行」らしい行のインデックスを探す
    （2026-09-25、実データ検証中に発見した問題への対処として追加）。

    当初は単純に先頭行（line_records[0]）をヘッダー行とみなす設計だったが、
    実際の想定レイアウト（タイトル4行＋10列の表）で検証したところ、
    ページ先頭の「タイトル行」（表の前にある見出し文）が誤ってヘッダー行と
    判定され、そのタイトル行自体の（本来列とは無関係な）文字間の隙間から
    誤った列境界が検出され、表の実際のヘッダー行・データ行すべてがその
    誤った境界で分割されてしまう不具合が判明した。

    改善の経緯（複数回の試行錯誤、実データで判明した問題点）：
    1. 「次の1行だけと検出列数が近ければ採用」という安定性チェックを試みたが、
       タイトル行同士がたまたま近い列数に分裂するケースがあり不十分だった。
    2. 「後続の行それぞれが“自分自身の単語の隙間”から独立に検出する列数が
       近いか」で連続run長を測る方式も試したが、データ行（値が短く、各セルの
       間隔がほぼ均等に大きい）は_detect_column_boundaries()を単独で適用
       すると中央値ベースの閾値がうまく機能せず、ほぼ常に1列に潰れてしまい
       （ヘッダー行は項目名の長さが不揃いなため隙間の大小差が明確に出るが、
       データ行の短い値では出ない）、本来のヘッダー行が誤って「後続と
       一致しない」と判定されてしまった。
    3. 「候補行の境界を後続行に適用した際、一定数以上の列が埋まるか」という
       安定性チェックも試みたが、埋まったとみなす閾値（列数の一定割合）を
       低く設定するとタイトル行由来の（本来無関係な）境界でも大半の行が
       閾値を満たしてしまい、かつ判定対象がページの手前にあるほど後続行の
       母数が多く有利になる（先頭に近いほどrun長が伸びやすい）という
       構造的な偏りがあり、依然としてタイトル行を誤って選んでしまった。

    現在の方式：_MIN_DETECTED_COLUMNS〜_MAX_DETECTED_COLUMNSの範囲に収まる
    候補行の中から、**検出された列数が最大の行**を採用する、という単純な
    基準に変更した。実データで、タイトル行（文章の文字間隔のばらつきに
    起因する偶発的な分裂）が検出する列数は最大でも5程度に留まる一方、
    実際の表のヘッダー行（項目名の境界が明確な罫線付きの表）は列数分
    （10列）にきれいに分裂することを確認済みであり、両者の間には十分な
    差があるため、複雑な安定性チェックより単純な最大値判定の方が頑健で
    あることが分かった。同数の場合は、より手前（ページの上）にある行を
    優先する（タイトルより後、表より前に別の候補が無い限り、実質的に
    表のヘッダー行が選ばれる）。

    候補が1つも無ければNoneを返す（呼び出し元は全行を_split_line_to_
    columns()にフォールバックさせる）。

    戻り値：ヘッダー行と判定したインデックス、またはNone。
    """
    best_index = None
    best_n_cols = 0
    for i, record in enumerate(line_records):
        n_cols = len(_detect_column_boundaries(record["words"]))
        if not (_MIN_DETECTED_COLUMNS <= n_cols <= _MAX_DETECTED_COLUMNS):
            continue
        if n_cols > best_n_cols:
            best_n_cols = n_cols
            best_index = i

    return best_index


def _build_rows_for_single_page(line_records: list) -> tuple:
    """
    _build_rows_from_line_records()の実処理本体。**1ページ分**の行レコード
    （全て同じpage_noを持つ前提）のみを受け取り、座標ベースの列復元（案A）
    でrows・confidencesを組み立てる。

    _find_header_row_index()で「ヘッダー行」を探し、_detect_column_
    boundaries()で列境界を検出する。ヘッダー行が見つからない場合（表形式
    ではない単純なレイアウト等）は、全行について従来通り
    _split_line_to_columns()（空白区切り）にフォールバックする。

    ヘッダー行より前にある行（タイトル行等、表の一部ではないと判断される
    行）は、列境界の対象外として従来通り_split_line_to_columns()のままにする
    （表とは無関係な行に無理に列境界を当てはめないため）。ヘッダー行以降
    （ヘッダー行自身を含む）の全行に、検出した列境界を適用する。

    事前に_group_line_records_into_rows()で表の視覚的な行単位に再グルーピング
    してから処理する（罫線付きの表でTesseractのブロック分割がセル単位に
    細分化されてしまう問題への対処、同関数のdocstring参照）。

    戻り値：(rows, confidences) のタプル。
    """
    if not line_records:
        return [], []

    line_records = _group_line_records_into_rows(line_records)
    header_index = _find_header_row_index(line_records)

    if header_index is None:
        rows = [_split_line_to_columns(r["line_text"]) for r in line_records]
        confidences = [r["avg_confidence"] for r in line_records]
        return rows, confidences

    column_boundaries = _detect_column_boundaries(line_records[header_index]["words"])

    rows = []
    confidences = []
    for i, record in enumerate(line_records):
        if i < header_index:
            rows.append(_split_line_to_columns(record["line_text"]))
        else:
            rows.append(_assign_words_to_columns(record["words"], column_boundaries))
        confidences.append(record["avg_confidence"])
    return rows, confidences


def _build_rows_from_line_records(line_records: list) -> tuple:
    """
    OCR経路の行レコード（_group_words_into_line_records()の戻り値に
    "page_no"（1始まりのページ番号）を付与したもの、複数ページ分を連結した
    もの）から、座標ベースの列復元（案A）でrows・confidencesを組み立てる。
    extract_pdf_rows()のOCR経路から呼ばれる（テキスト抽出経路は座標情報を
    持たないため、従来通り_split_line_to_columns()による空白区切りのままと
    する、対象外）。

    ページ単位での分離処理（2026-09-25、複数ページPDFでの列復元バグ修正で
    追加）：行レコードの座標（left/top）はページ画像原点からの相対座標で
    あり、ページをまたいで比較できる値ではない。以前は全ページ分の行
    レコードを1つのリストとして_group_line_records_into_rows()・
    _find_header_row_index()・_detect_column_boundaries()にまとめて渡して
    いたため、別ページの同じような座標にある行・セルが誤って1つに統合
    されてしまう不具合があった（実データの81ページPDFで81ページ分の内容が
    8行に潰れる形で発現、2026-09-25の調査報告参照）。

    修正方針：line_recordsを"page_no"でページ単位にグルーピングし、各
    ページの行レコードだけを_build_rows_for_single_page()に渡して独立に
    処理する（行のグルーピング・ヘッダー検出・列境界検出・列復元の一連の
    処理を、ページごとに完全に独立させる）。各ページの結果（rows・
    confidences）は、ページ番号の昇順で結合して最終的な戻り値とする。

    ヘッダー行検出の方針（ページ単位で独立に検出）：帳票によってはヘッダー
    行が全ページに繰り返し印字される場合と、1ページ目にしか無い場合の
    両方が考えられる。今回は実装をシンプルに保つため、各ページが「自分
    自身の行レコードの中だけ」でヘッダー行を検出する方式を採用した
    （_build_rows_for_single_page()をページごとに個別呼び出しするだけで
    実現でき、ページをまたいだ状態受け渡しが不要）。この方針による制約：
    ヘッダー行が1ページ目にしか印字されない帳票の場合、2ページ目以降は
    そのページ単独では有効なヘッダー候補が見つからず、_split_line_to_
    columns()（空白区切り）にフォールバックする（＝2ページ目以降は列復元
    の精度が1ページ目より下がる）。ただし帳票の性質上、複数ページに
    またがる表はページごとにヘッダーが再掲されるレイアウトが一般的であり
    （実際の想定レイアウトである「タイトル4行＋10列の罫線付き表」も、
    ページをまたぐ際は各ページの先頭に表のヘッダー行が再掲される想定）、
    その場合は各ページで正しく独立にヘッダーが検出され、案Aの列復元が
    ページごとに正しく機能する。1ページ目の列境界を全ページへ使い回す
    案（ヘッダーが繰り返されない帳票により頑健）も検討したが、ページに
    よって画像内のわずかな伸縮・余白差が生じ得るため、まずは各ページが
    自身の実測座標から列境界を検出する今回の方式を採用し、実運用で
    ヘッダー非repeat型の帳票が問題になった場合に再検討する。

    戻り値：(rows, confidences) のタプル（全ページ分、ページ順に結合）。
    """
    if not line_records:
        return [], []

    records_by_page = {}
    for record in line_records:
        records_by_page.setdefault(record.get("page_no", 1), []).append(record)

    rows = []
    confidences = []
    for page_no in sorted(records_by_page.keys()):
        page_rows, page_confidences = _build_rows_for_single_page(records_by_page[page_no])
        rows.extend(page_rows)
        confidences.extend(page_confidences)

    return rows, confidences


def _ocr_one_page(image: Image.Image, debug_dir: str, debug_prefix: str, page_no: int) -> list:
    """
    1ページ分のOCR処理（前処理＋pytesseract呼び出し＋行グルーピング）。
    ProcessPoolExecutorのワーカーから呼ばれる想定のモジュールレベル関数
    （2026-09-25追加、複数ページ並列化）。

    別プロセスで実行されるため、親プロセス側で_configure_tesseract()を
    呼んでいても、その効果（pytesseract.pytesseract.tesseract_cmdへの
    代入・os.environ["TESSDATA_PREFIX"]の設定）はこのプロセスには
    引き継がれない（Windowsのspawn方式では新しいインタプリタが本モジュールを
    再importするだけで、親プロセスのPythonオブジェクトの状態は共有されない）。
    そのため、ここで改めて_configure_tesseract()を呼ぶ必要がある。

    引数・戻り値は全てpickle可能な単純な型のみで構成する（ProcessPoolExecutorは
    ワーカーとの間の引数・戻り値をpickleでやり取りするため）。PIL Imageは
    標準でpickle可能（内部でtobytes()相当の変換を行う）。戻り値は
    _group_words_into_line_records()が返す行レコードのリスト（辞書のリスト、
    座標情報を含む。以前は(str, float)のタプルのリストだったが、2026-09-25の
    座標ベース列復元（案A）追加に伴い、単語座標を保持する形に変更した）。

    各行レコードに"page_no"（1始まりのページ番号）を付与する（2026-09-25、
    複数ページPDFでの列復元バグ修正に伴い追加）。経緯：座標ベースの列復元
    （_group_line_records_into_rows()のY座標クラスタリング・
    _find_header_row_index()・_detect_column_boundaries()）は、各行レコードの
    座標がページ画像原点（左上）からの相対座標であることを前提にしており、
    複数ページ分の行レコードを単純に連結すると、別ページの同じような座標に
    ある行・セルが誤って統合されてしまう不具合が実データ（81ページPDF）で
    確認された（詳細は2026-09-25の調査報告参照）。この"page_no"により、
    _build_rows_from_line_records()がページ単位で処理を分離できるようにする。
    """
    _configure_tesseract()
    preprocessed = preprocess_image_for_ocr(image, debug_dir=debug_dir, debug_prefix=debug_prefix)
    data = pytesseract.image_to_data(
        preprocessed, lang=_OCR_LANGUAGES, output_type=pytesseract.Output.DICT,
    )
    records = _group_words_into_line_records(data)
    for record in records:
        record["page_no"] = page_no
    return records


def _resolve_ocr_worker_count(page_count: int) -> int:
    """
    OCR並列処理のワーカー数を決定する。os.cpu_count()を基準にしつつ、
    事務用PC（コア数が限られる想定）での過剰な負荷を避けるため
    _MAX_OCR_WORKERS（3）を上限とする。対象ページ数より多いワーカーを
    起動しても無駄なため、page_countも上限に含める。cpu_count()が
    取得できない環境（稀）はNoneを返しうるため、その場合は2を仮定する
    （既存のCPU数取得失敗時のフォールバックと同じ考え方）。
    """
    return max(1, min(os.cpu_count() or 2, _MAX_OCR_WORKERS, page_count))


def _extract_lines_via_ocr(pdf_path: str, debug_dir: str = None) -> list:
    """
    pdf2imageでPDFの各ページを画像化し、preprocess_image_for_ocr()で前処理
    した上で、pytesseract.image_to_data()（image_to_string()ではなく、
    単語ごとの信頼度スコア付きの詳細データを返す関数）でOCRする。
    テキスト層を持たない（スキャン画像由来の）PDFに対するフォールバック経路。

    image_to_string()ではなくimage_to_data()を使う理由：認識した文字列
    だけでなく、各単語の信頼度（0〜100）を取得し、行単位の平均信頼度として
    extract_pdf_rows()の戻り値に含め、UI側で信頼度の低い行を可視化できる
    ようにするため。

    debug_dir（既定None）を指定すると、各ページの前処理前後の画像を
    "page1_00_original.png"のようなファイル名でそのフォルダに保存する
    （開発時の比較用。preprocess_image_for_ocr()にそのまま委譲する）。

    戻り値：_group_words_into_line_records()が返す行レコード（辞書）の
    リスト（全ページ分を連結、ページ間の順序はconvert_from_path()が返す
    ページ順のまま）。以前は(行テキスト, 平均信頼度0〜100)のタプルのリスト
    だったが、2026-09-25の座標ベース列復元（案A）追加に伴い、単語座標
    （"words"）を保持する行レコードのリストに変更した。

    複数ページの並列処理（2026-09-25追加、後にパイプライン化）：ページごとの
    前処理・OCRは互いに独立した計算のため、ProcessPoolExecutorでページ単位に
    並列化する。ワーカー数は_resolve_ocr_worker_count()で事務用PC向けに
    控えめに抑える（最大3）。ThreadPoolExecutorではなくProcessPoolExecutorを
    使う理由：OpenCVの前処理・Tesseractの認識処理はいずれもCPUバウンドな
    計算であり、PythonのGIL（Global Interpreter Lock）下ではスレッド並列では
    並列に実行されない（cv2・pytesseractの呼び出し自体がGILを解放する実装で
    ない限り効果が薄い）ため、プロセス単位の並列化を選んだ。

    画像化とOCRのパイプライン化（2026-09-25追加）：以前はconvert_from_path()
    で全ページを一括画像化してからOCRを開始していたが、ページ数が多い
    PDF（実データで81ページ確認済み）では、この画像化フェーズ全体が
    OCRの並列処理と重ならない直列コストとして積み上がってしまう問題が
    あった。そのため、convert_from_path()にfirst_page/last_pageを指定して
    1ページずつ画像化し、画像化が終わったページから順にProcessPoolExecutor.
    submit()でOCRへ投入する方式に変更した。これにより、あるページの画像化
    処理中に、既に画像化済みの別ページのOCRをワーカープロセスが並行して
    進められるようになる（画像化とOCRが完全なパイプラインとして重なる）。

    map()ではなくsubmit()を使う理由：map()は渡された入力（画像のリスト）を
    事前に全て確定させる必要があり、画像化を1ページずつ順次行いながら
    投入する今回の方式とは相性が悪い。submit()はFutureをその場で返すため、
    1ページ画像化するたびに即座に投入し、次のページの画像化に進める。

    順序の保証：submit()はワーカーへの投入順とOCRの完了順が一致するとは
    限らない（並列実行のため、後から投入したページが先に終わることが
    ある）ため、Future自体をpage_futures（投入順＝ページ番号順のリスト）に
    保持しておき、全ページ投入後にこのリストの順番通りに.result()を呼ぶ
    ことで、mapを使っていた以前の実装と同じくページ順（1ページ目→2ページ目
    →…）を保証する。バックプレッシャー制御（下記）はpage_futuresとは別の
    pending_futures集合で管理するため、この順序保証には影響しない。

    バックプレッシャー制御（2026-09-25追加）：画像化がOCRより大幅に速い
    状況（実データでのノイズの多いスキャン画像等でOCRが遅くなるケースを
    想定）では、投入済みだが結果未回収のFutureが際限なく積み上がり、
    それぞれが保持する画像データ分メモリを圧迫することが調査で判明した。
    そのため、pending_futures（投入済みだが未回収のFutureの集合）の要素数が
    _BACKPRESSURE_EXTRA_FUTURES分の余裕を持たせたワーカー数
    （worker_count + _BACKPRESSURE_EXTRA_FUTURES）に達したら、次のページの
    画像化に進む前にconcurrent.futures.wait(..., return_when=FIRST_COMPLETED)
    でいずれか1つが完了するまでブロックする。これにより、未処理の画像化
    済みページ数は常にこの上限以下に抑えられる。

    wait()の戻り値（done, not_done）でpending_futuresをnot_doneに置き換える
    実装のため、しきい値判定のタイミングによっては実際にはすでに完了して
    いるFutureがpending_futuresに残ったまま次のページに進むことがある
    （wait()を呼ぶまでpending_futuresを能動的に更新しないため）。この場合、
    次にしきい値に達してwait()を呼んだ際、それらの完了済みFutureは即座に
    doneとして返るため、実害はない（安全側に倒れるだけで、不要な待ちが
    増えることはない）。

    1ページのみのPDFはプロセスプール起動のオーバーヘッド（新規プロセスの
    起動・cv2/numpy/pytesseract等の重いモジュールの再import）の方が
    処理時間そのものより大きくなり得るため、並列化せず現在のプロセス内で
    直接処理する。

    既存の非同期パターン（ui.pdf_ocr_import_window.PdfOcrImportWindow.
    _run_convert_in_thread()がthreading.Threadでこの関数を呼ぶ）との関係：
    バックグラウンドスレッドの中からProcessPoolExecutorで別プロセスを
    起動すること自体は問題ない（threading.Threadは「UIスレッドをブロック
    しない」ためのものであり、ProcessPoolExecutorは「CPUバウンドな処理を
    複数コアに分散する」ためのもので、目的が異なり両立する）。この関数自体は
    従来通り「全ページの処理が終わるまで戻らない」同期関数のままであり、
    それを非同期化する責務は引き続き呼び出し元（バックグラウンドスレッド側）
    が担う。
    """
    _configure_tesseract()

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

    # dpi=300（2026-09-25、精度優先のため150から引き上げ）。
    #
    # 経緯：当初、高速化のためdpiを150に引き下げた（pdf2imageのデフォルト
    # 200dpiより低い値、単純な1ブロックの部品リスト風テストデータでは
    # 認識行数に変化が無かったため）。しかし、実際の運用に近いレイアウト
    # （タイトル4行＋10列の罫線付き表）で改めて検証したところ、150dpiでは
    # 文字が小さすぎてOCRが機能不全に陥り、タイトル・データ行のいずれも
    # バラバラな1文字・意味不明な文字列断片にしかならないことが判明した
    # （例："月次基板構成数チェックリスト"が"awigemserx ャ ク リ ス ト"に
    # なる等）。200dpiでも改善は限定的だった。300dpiまで引き上げたところ、
    # タイトル・96コード・数量・日付等がほぼ完全に近い精度で認識できる
    # （実用レベルの）結果が得られたため、精度を優先し300dpiを採用する。
    # --psmオプションは指定しない（デフォルトの--psm 3のまま）：同じ検証で
    # --psm 4は--psm 3と同一結果、--psm 6は罫線を文字として誤読しデータ行が
    # ほぼ全滅する結果になったため、変更しない判断とした。
    _DPI = 300

    if page_count <= 1:
        images = convert_from_path(pdf_path, poppler_path=config.POPPLER_PATH, dpi=_DPI)
        line_records = []
        for i, image in enumerate(images):
            line_records.extend(_ocr_one_page(image, debug_dir, f"page{i + 1}", i + 1))
        return line_records

    worker_count = _resolve_ocr_worker_count(page_count)
    max_pending_futures = worker_count + _BACKPRESSURE_EXTRA_FUTURES
    line_records = []
    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        page_futures = []  # 投入順（＝ページ番号順）。順序保証専用、完了しても要素を取り除かない。
        pending_futures = set()  # バックプレッシャー判定用。しきい値到達時のみwait()で更新する。

        for page_no in range(1, page_count + 1):
            if len(pending_futures) >= max_pending_futures:
                # 未処理（投入済みだが結果未回収）のFutureが上限に達した場合、
                # 次のページの画像化に進む前にいずれか1つが完了するまで待つ
                # （バックプレッシャー制御。docstring参照）。
                done, pending_futures = wait(pending_futures, return_when=FIRST_COMPLETED)

            # 1ページずつ画像化する（first_page=last_page=page_noで対象を
            # 1ページに絞る）。画像化が終わり次第、他ページの画像化を待たず
            # 即座にOCRへ投入する。
            page_images = convert_from_path(
                pdf_path, poppler_path=config.POPPLER_PATH, dpi=_DPI,
                first_page=page_no, last_page=page_no,
            )
            future = executor.submit(_ocr_one_page, page_images[0], debug_dir, f"page{page_no}", page_no)
            page_futures.append(future)
            pending_futures.add(future)

        # 投入順（＝ページ番号順）にresult()を呼ぶことで、完了順に関わらず
        # ページの順序を保証する。
        for future in page_futures:
            line_records.extend(future.result())

    return line_records


def _split_line_to_columns(line: str) -> list:
    """
    1行のテキストを列に分解する（現時点では空白・タブの連続を区切りとする
    素朴な実装。モジュールdocstring参照。実際のPDFレイアウトが判明した際は
    ここを差し替える）。
    """
    return line.split()


def extract_pdf_rows(pdf_path: str, debug_dir: str = None) -> dict:
    """
    PDFを読み取り、行ごとに列分解した結果を返す（CSVへの書き出しは行わない、
    UIでのプレビュー表示用）。

    処理フロー：
      1. pdfplumberでテキスト抽出を試みる。
      2. 抽出できた文字数が_MIN_TEXT_LENGTH_THRESHOLD未満であれば、
         pdf2image + preprocess_image_for_ocr() + pytesseract.image_to_data()
         によるOCR抽出にフォールバックする（_extract_lines_via_ocr()）。
      3. 列分解：
         - OCR経路：_build_rows_from_line_records()による座標ベースの列
           復元（案A、2026-09-25追加）。先頭行をヘッダー行とみなして列境界を
           検出し、以降の全行をその境界に沿って列分けする。ヘッダー行の
           検出に失敗した場合（表形式でない単純なレイアウト等）は、従来
           通り_split_line_to_columns()（空白区切り）にフォールバックする。
         - テキスト抽出経路：座標情報を持たないため、従来通り
           _split_line_to_columns()（空白区切り）のみを使う。

    信頼度（"confidences"）について：テキスト抽出経路（OCRを使わない場合）は
    文字化けのリスクが無いため、常に100（該当なしの意味も兼ねる）として扱う。
    OCR経路は、_group_words_into_line_records()が算出したその行に属する
    単語群の信頼度の平均値を使う。

    debug_dir（既定None）を指定すると、OCR経路使用時に限り、各ページの
    前処理前後の画像をこのフォルダへ保存する（開発時の比較用、
    _extract_lines_via_ocr()参照）。

    戻り値：{
        "method": "text" または "ocr"（どちらの経路で抽出したか）,
        "rows": [[col1, col2, ...], ...]（空行は除外済み）,
        "confidences": [信頼度(0〜100), ...]（rowsと同じ順序・同じ件数）,
        "raw_text": 抽出した生テキスト（全行を改行で連結したもの）,
        "page_count": ページ数,
    }
    """
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

    text = _extract_text_via_pdfplumber(pdf_path)

    if len(text.strip()) < _MIN_TEXT_LENGTH_THRESHOLD:
        method = "ocr"
        line_records = _extract_lines_via_ocr(pdf_path, debug_dir=debug_dir)
        rows, confidences = _build_rows_from_line_records(line_records)
        raw_text = "\n".join(r["line_text"] for r in line_records)
    else:
        method = "text"
        lines_with_confidence = [
            (line, 100.0) for line in text.splitlines() if line.strip()
        ]
        rows = [_split_line_to_columns(line) for line, _ in lines_with_confidence]
        confidences = [confidence for _, confidence in lines_with_confidence]
        raw_text = "\n".join(line for line, _ in lines_with_confidence)

    return {
        "method": method,
        "rows": rows,
        "confidences": confidences,
        "raw_text": raw_text,
        "page_count": page_count,
    }


def write_rows_to_csv(rows: list, output_csv_path: str):
    """
    extract_pdf_rows()が返すrows（可変長のリストのリスト）をCSVとして保存する。
    行ごとに列数が異なり得るため、csv.writerでそのまま書き出す
    （欠けている列を空欄で埋める整形は行わない）。
    """
    with open(output_csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def convert_pdf_to_csv(pdf_path: str, output_csv_path: str) -> dict:
    """
    PDFを読み取り、行ごとに列分解した結果をCSVファイルとして保存する。
    extract_pdf_rows() + write_rows_to_csv() をまとめて行う便利関数。

    戻り値はextract_pdf_rows()と同じ形式の辞書（"method"・"rows"・"raw_text"・
    "page_count"）。
    """
    result = extract_pdf_rows(pdf_path)
    write_rows_to_csv(result["rows"], output_csv_path)
    return result


def _find_part_no_in_row(row: list):
    """
    行（セルのリスト）の中から96コードらしきセルを探す。_PART_NO_PATTERN
    （"96"で始まる7〜8桁の数字）に一致する最初のセルをその行の96コードとみなす。

    複数セルが一致する場合も先頭の1件を採用する（名称・数量列がたまたま
    同じ桁数の"96"始まりの数字になることは現実的にはほぼ想定されないため、
    複数一致時の警告・エラー処理は今回は行わない）。

    判定前にunicodedata.normalize("NFKC", ...)で正規化する（2026-09-25追加）。
    OCR経路（_extract_lines_via_ocr()）で、Tesseractのjpn+eng混在モードが
    数字を円囲み文字（例："①"=U+2460 CIRCLED DIGIT ONE）に誤認識するケースが
    実データで確認されており、NFKCはこの種の文字を対応する通常の数字（"1"）へ
    機械的に変換する（既存のmodels.board_structure_master.normalize_board_
    name()・services.production_import_service.normalize_product_name()と
    同じ標準ライブラリ関数）。_split_line_to_columns()側ではなくこちらで
    正規化する理由：本関数はテキスト抽出経路・OCR経路のどちらの行に対しても
    共通で呼ばれる唯一の入口のため、ここに1箇所追加するだけで両経路に
    自動的に反映される（テキスト抽出経路でも、PDF自体に全角数字で96コードが
    埋め込まれているケースへの耐性にもなる）。_PART_NO_PATTERN自体の定義は
    変更していない（正規化後の文字列に対してそのまま判定させる）。

    見つからなければNoneを返す（ヘッダー行・96コード列を含まない行等）。
    """
    for cell in row:
        normalized = unicodedata.normalize("NFKC", cell.strip())
        if _PART_NO_PATTERN.match(normalized):
            return normalized
    return None


def match_against_inventory(csv_rows: list) -> dict:
    """
    PDFから抽出したCSV行（extract_pdf_rows()の"rows"、_split_line_to_columns()
    による素朴な列分解結果）を、inventory_stockに登録済みの96コード一覧
    （models.inventory.list_inventory_part_nos()）と照合し、一致する行
    （＝当部署で在庫管理している部品）だけを抽出する。

    各行から_find_part_no_in_row()で96コードらしきセルを特定し、
    inventory_stockに存在すれば一致行として残す。以下はいずれも「除外」
    として扱う（区別はexcluded_countに合算するのみで、内訳は持たない）：
      - 96コードらしきセルが1つも無い行（見出し行・ノイズ行等）
      - 96コードは特定できたが、inventory_stockに存在しない行（他部署の部品と想定）

    戻り値：{
        "matched_rows": [[...], ...]（一致した行。元の列構成のまま）,
        "matched_count": int,
        "excluded_count": int,
    }
    """
    known_part_nos = list_inventory_part_nos()

    matched_rows = []
    excluded_count = 0
    for row in csv_rows:
        part_no = _find_part_no_in_row(row)
        if part_no is not None and part_no in known_part_nos:
            matched_rows.append(row)
        else:
            excluded_count += 1

    return {
        "matched_rows": matched_rows,
        "matched_count": len(matched_rows),
        "excluded_count": excluded_count,
    }
