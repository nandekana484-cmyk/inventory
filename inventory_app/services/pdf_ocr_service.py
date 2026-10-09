"""
PDF を読み取り、行ごとに列に分けて CSV にするサービス。テキスト層があれば pdfplumber で抽出し、
無ければ（全ページ合計が _MIN_TEXT_LENGTH_THRESHOLD 文字未満なら）画像化して Tesseract で OCR する。
列の分け方・前処理・調整値の根拠は docs/domain/pdf_ocr.md。Tesseract 本体と Poppler は OS 側に別途インストールが必要。
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

# PDF 読み取りの開始・完了・エラーを記録する専用ロガー（LOG_DIR/pdf_ocr_YYYYMMDD.log）。ワーカープロセスの異常終了がログに残らなかったため追加した。
# ほかのロガーと違い FileHandler を実際に設定する。propagate=False で、ルートロガーへの二重出力を避ける。
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False

# 直近に FileHandler を設定した日付。起動したまま日付が変わったら、ハンドラを張り替える。
_ocr_log_handler_date = None


def _ocr_log_file_path() -> str:
    return os.path.join(config.LOG_DIR, f"pdf_ocr_{datetime.now().strftime('%Y%m%d')}.log")


def _ensure_ocr_log_handler():
    """当日のログファイルへの FileHandler を設定する（日付が変わっていれば張り替える）。"""
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
    PDF のページ数だけを数える（ログ用の軽い処理）。extract_pdf_rows() が途中で例外を出しても、ページ数をログに残せるようにする。
    """
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def log_ocr_start(pdf_path: str, page_count):
    """PDF 読み取りの開始をログに記録する（page_count が None なら「不明」）。"""
    _ensure_ocr_log_handler()
    logger.info(
        "[開始] file=%s page_count=%s",
        os.path.basename(pdf_path), page_count if page_count is not None else "不明",
    )


def log_ocr_complete(pdf_path: str, page_count, elapsed_seconds: float, method: str, row_count: int):
    """PDF 読み取りの正常完了をログに記録する。"""
    _ensure_ocr_log_handler()
    logger.info(
        "[完了] file=%s page_count=%s method=%s rows=%d elapsed=%.1fs",
        os.path.basename(pdf_path), page_count, method, row_count, elapsed_seconds,
    )


def log_ocr_error(pdf_path: str, page_count, elapsed_seconds: float, error_text: str):
    """PDF 読み取り中の例外（ワーカーの異常終了を含む）をログに記録する。error_text には traceback.format_exc() の文字列を渡す。"""
    _ensure_ocr_log_handler()
    logger.error(
        "[エラー] file=%s page_count=%s elapsed=%.1fs\n%s",
        os.path.basename(pdf_path), page_count if page_count is not None else "不明",
        elapsed_seconds, error_text,
    )

# 全ページ合計がこの文字数未満なら、テキスト層が無いとみなして OCR にする（空白ページなどがあるので0にはしない）。
_MIN_TEXT_LENGTH_THRESHOLD = 20

# 96コードなどの英数字と、部品名などの日本語が混ざるため、両方を認識する。
_OCR_LANGUAGES = "jpn+eng"

# OCR の並列ワーカー数の上限。1ワーカーで CPU を使い切るので、事務用 PC のほかの作業を妨げないよう3に抑える（docs/domain/pdf_ocr.md）。
_MAX_OCR_WORKERS = 3

# バックプレッシャー: 未回収の OCR がワーカー数＋この数に達したら、画像化を止めて1つ終わるのを待つ（画像が積み上がってメモリを圧迫したため）。
# +2 は、ワーカーを待たせないよう、次に渡せる画像を少し持っておくため。
_BACKPRESSURE_EXTRA_FUTURES = 2

# 96コード: "96" で始まる7〜8桁の数字。名称・数量列を誤判定しないよう、セル全体で一致を判定する。
_PART_NO_PATTERN = re.compile(r"^96\d{5,6}$")


def _configure_tesseract():
    """
    pytesseract に、config の Tesseract 本体と tessdata の場所を設定する（何度呼んでもよい）。
    tessdata は環境変数 TESSDATA_PREFIX で渡す。config='--tessdata-dir "<path>"' では、引用符がそのままパスの一部になり読み込みに失敗した。
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
    グレースケール画像の傾きを推定して回転補正する（文字の画素全体の外接矩形の傾きを使う、標準的な方法）。
    0.5度未満なら回転しない（傾いていない画像を、回転・補間で劣化させないため）。
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
    OCR の前の画像処理。グレースケール→傾き補正→ノイズ除去（bilateralFilter）→コントラスト強調（CLAHE）→適応的二値化 の順に行う
    （ノイズを先に除くのは、コントラスト強調でノイズが強調されないようにするため）。手法と数値の根拠は docs/domain/pdf_ocr.md。
    debug_dir を指定すると、前処理の前後の画像を保存する（開発時の比較用）。
    """
    img_bgr = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)

    if debug_dir:
        os.makedirs(debug_dir, exist_ok=True)
        cv2.imwrite(os.path.join(debug_dir, f"{debug_prefix}_00_original.png"), img_bgr)

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = _deskew(gray)

    # bilateralFilter(d=9, sigmaColor=75, sigmaSpace=75)。以前の fastNlMeansDenoising（1画像約1.3秒）と同等の精度で、0.01秒未満。
    # medianBlur は精度が落ちたので不採用。値は OpenCV の公式例（中程度〜強めのノイズ除去）のまま（docs/domain/pdf_ocr.md）。
    denoised = cv2.bilateralFilter(gray, 9, 75, 75)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    contrast_enhanced = clahe.apply(denoised)

    # ノイズ除去の後に残るわずかな濃淡差で誤って二値化しないよう、ブロックサイズを大きく・定数 C を小さくした（以前の値は 31, 15）。
    binarized = cv2.adaptiveThreshold(
        contrast_enhanced, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11,
    )

    if debug_dir:
        cv2.imwrite(os.path.join(debug_dir, f"{debug_prefix}_01_preprocessed.png"), binarized)

    return Image.fromarray(binarized)


def _group_words_into_line_records(data: dict) -> list:
    """
    pytesseract.image_to_data() の結果を (block_num, par_num, line_num) ごとにまとめ、単語の座標付きの行レコードにする。
    conf == -1（構造情報だけの要素）と空の単語は読み飛ばす。行は (block, par, line) の順、行内の単語は left の順。
    line_text は、列の復元に失敗したときのフォールバックと raw_text にだけ使う（日本語は1文字ずつ別の単語になりやすく、空白区切りでは細切れになる）。
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


# 検出した列数がこの範囲の外なら、ヘッダー行の検出に失敗したとみなし、空白区切りにする（1列や、細切れになりすぎた誤検出を弾く）。
_MIN_DETECTED_COLUMNS = 2
_MAX_DETECTED_COLUMNS = 30

# 単語間の隙間の中央値のこの倍を超える隙間を、列の区切りとみなす。実際の表（10列）では列内が数px・列間が250px超で、2〜30倍なら同じ結果だった。
_COLUMN_GAP_MEDIAN_MULTIPLIER = 3


def _detect_column_boundaries(header_words: list) -> list:
    """
    ヘッダー行の単語から、隙間をもとに列の境界を検出する。隣り合う単語の隙間の中央値の _COLUMN_GAP_MEDIAN_MULTIPLIER 倍を超える隙間で列を分ける
    （列幅を事前に知らなくても、ページやフォントの大きさに追従する）。単語が0個なら空、1個ならその範囲を1列として返す。
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
    1行の単語を、列の境界に沿って分ける。単語の中心に最も近い列に割り当てる（範囲内かで判定すると、わずかなはみ出しでどの列にも入らない単語が出る）。
    同じ列の単語はスペースを挟まずにつなぐ（Tesseract が1つのセルを複数の単語に分けることがあり、日本語主体の帳票ではその方が元に近い）。
    """
    n_cols = len(column_boundaries)
    cell_words = [[] for _ in range(n_cols)]
    col_centers = [(left + right) / 2 for left, right in column_boundaries]

    for word in sorted(row_words, key=lambda w: w["left"]):
        word_center = word["left"] + word["width"] / 2
        col_idx = min(range(n_cols), key=lambda i: abs(col_centers[i] - word_center))
        cell_words[col_idx].append(word["text"])

    return ["".join(texts) for texts in cell_words]


# 直前の行との Y 座標の差が「直前の行の文字の高さ×この倍率」を超えたら、別の行とみなす（文字の高さとの比なので、フォントや dpi に依存しない）。
# 実データで 0.6 で正しく分かれることを確認した。
_ROW_GAP_HEIGHT_RATIO = 0.6


def _group_line_records_into_rows(line_records: list) -> list:
    """
    Tesseract の行レコードを、表の「見た目の行」にまとめ直す。罫線付きの表では、1行の各セルが別々の行として認識されるため
    （この処理をしないと、列境界の検出が1セルだけの行で行われてしまう）。Y 座標の順に並べ、差が _ROW_GAP_HEIGHT_RATIO 以内なら同じ行にする。
    罫線の無いタイトル行などは、前後の行と十分離れているので変わらない。
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
    行レコードから表のヘッダー行を探す。検出した列数が許容範囲内の行のうち、列数が最も多い行（同数なら上の行）を選ぶ。無ければ None。
    先頭行をヘッダーとみなすと、表の前のタイトル行の文字間隔から誤った列境界ができた。タイトル行は最大5列程度、表のヘッダーは10列に分かれるので、
    最大値で選ぶのが最も頑健だった（試して失敗した方式は docs/domain/pdf_ocr.md）。
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
    1ページ分の行レコードから、座標で列を復元して (rows, confidences) を作る。行を見た目の行にまとめ直してから、ヘッダー行と列境界を検出する。
    ヘッダー行が無ければ全行を空白区切りにする。ヘッダー行より前の行（タイトルなど）も空白区切りのまま。
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
    OCR の行レコード（全ページ分）から、ページごとに独立して列を復元し、ページ順につなぐ。
    座標はページ画像ごとの相対座標なので、ページをまたいでまとめると別ページの行が混ざる（81ページの PDF が8行に潰れた）。
    ヘッダーは各ページで独立に探すので、ヘッダーが1ページ目にしか無い帳票では、2ページ目以降は空白区切りになる（docs/domain/pdf_ocr.md）。
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
    1ページ分の OCR（前処理＋image_to_data＋行のまとめ）。ProcessPoolExecutor のワーカーで実行する。
    別プロセスには親の _configure_tesseract() の設定が引き継がれない（Windows の spawn）ので、ここで設定し直す。
    引数と戻り値は pickle できる型だけにする。各行レコードに page_no を付ける（ページごとに列を復元するため）。
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
    OCR のワーカー数を決める。CPU 数を基準に、_MAX_OCR_WORKERS（3）とページ数で上限をかける。CPU 数が取れなければ2とみなす。
    """
    return max(1, min(os.cpu_count() or 2, _MAX_OCR_WORKERS, page_count))


def _extract_lines_via_ocr(pdf_path: str, debug_dir: str = None) -> list:
    """
    PDF の各ページを画像化・前処理し、image_to_data() で OCR する（行ごとの信頼度を UI に出すため、image_to_string() ではない）。
    戻り値は全ページ分の行レコード（ページ順）。debug_dir を指定すると、前処理の前後の画像を保存する。
    - ページごとに ProcessPoolExecutor で並列化する（CPU バウンドなので、GIL のあるスレッドではなくプロセス）。1ページだけなら並列化しない
    - 1ページずつ画像化して、終わったページから submit() で OCR に回す（画像化と OCR を重ねるため。map() は入力を先に全部そろえる必要がある）
    - 完了順はばらばらなので、投入順のリスト page_futures の順に result() を呼んでページ順を保つ
    - 未回収の Future がワーカー数＋_BACKPRESSURE_EXTRA_FUTURES に達したら、1つ終わるまで待つ（メモリの圧迫を防ぐ）
    - この関数は全ページが終わるまで戻らない。UI を止めないのは呼び出し元のスレッドの役目
    詳細は docs/domain/pdf_ocr.md。
    """
    _configure_tesseract()

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

    # 300dpi: 150dpi では実際のレイアウト（タイトル4行＋10列の表）で OCR が機能せず、200dpi でも不十分だった。
    # --psm は既定（3）のまま（4 は同じ結果、6 は罫線を文字と誤読した）。経緯は docs/domain/pdf_ocr.md。
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
                # 未回収の Future が上限に達したら、次の画像化の前に1つ終わるまで待つ（バックプレッシャー）。
                done, pending_futures = wait(pending_futures, return_when=FIRST_COMPLETED)

            # 1ページずつ画像化し、終わり次第、ほかのページを待たずに OCR へ回す。
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
    """1行のテキストを、空白・タブの連続で列に分ける（実際のレイアウトが決まったら差し替える前提の素朴な実装）。"""
    return line.split()


def extract_pdf_rows(pdf_path: str, debug_dir: str = None) -> dict:
    """
    PDF を読み取り、行ごとに列に分けた結果を返す（UI のプレビュー用。CSV には書かない）。
    テキスト層が無ければ OCR に切り替える。OCR では座標で列を復元し、テキスト抽出では空白区切りにする。
    信頼度は、テキスト抽出では常に100、OCR では行の単語の平均。
    戻り値: {"method": "text" か "ocr", "rows", "confidences"（rows と同じ件数）, "raw_text", "page_count"}
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
    """rows を CSV に保存する。行ごとに列数が違ってもそのまま書く（空欄で埋めない）。"""
    with open(output_csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)


def convert_pdf_to_csv(pdf_path: str, output_csv_path: str) -> dict:
    """PDF を読み取って CSV に保存する（extract_pdf_rows() と write_rows_to_csv() をまとめたもの）。戻り値は extract_pdf_rows() と同じ。"""
    result = extract_pdf_rows(pdf_path)
    write_rows_to_csv(result["rows"], output_csv_path)
    return result


def _find_part_no_in_row(row: list):
    """
    行の中で96コードらしい最初のセルを返す（無ければ None）。複数一致しても先頭を使う（名称・数量が同じ形の数字になることはほぼ無い）。
    判定の前に NFKC で正規化する。OCR が数字を「①」のような丸数字に誤認識することがあるため。テキスト抽出と OCR の両方が通る唯一の入口なので、ここで行う。
    """
    for cell in row:
        normalized = unicodedata.normalize("NFKC", cell.strip())
        if _PART_NO_PATTERN.match(normalized):
            return normalized
    return None


def match_against_inventory(csv_rows: list) -> dict:
    """
    抽出した行のうち、inventory_stock に登録済みの96コード（当部署で在庫管理している部品）を含む行だけを残す。
    96コードが無い行と、登録されていない96コードの行は除外する（件数だけを数え、内訳は持たない）。
    戻り値: {"matched_rows", "matched_count", "excluded_count"}
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
