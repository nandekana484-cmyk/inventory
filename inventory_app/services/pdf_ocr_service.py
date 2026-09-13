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
import os
import re

import cv2
import numpy as np
import pdfplumber
import pytesseract
from PIL import Image
from pdf2image import convert_from_path

import config
from models.inventory import list_inventory_part_nos

# 全ページ合計でこの文字数未満しか抽出できなければ、テキスト層が実質無い
# （スキャン画像PDF等）とみなしOCR経路にフォールバックする。テキストPDFでも
# 空白ページ等が混ざるケースを考慮し、0ではなくある程度の閾値を設ける。
_MIN_TEXT_LENGTH_THRESHOLD = 20

# pytesseractに渡す言語コード。96コード等の数字・英字と、部品名称等の日本語
# 文字列が混在するため、日本語＋英数字の両方を認識対象にする。
_OCR_LANGUAGES = "jpn+eng"

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
      3. ノイズ除去：Non-Local Means Denoising（cv2.fastNlMeansDenoising）を
         採用。当初は軽いガウシアンブラーを試したが、粒状ノイズを十分に
         均せないまま二値化すると、ノイズが局所的な明暗差として拾われ
         二値化後に無数の斑点が発生し、かえって認識率が落ちることを実験で
         確認した。fastNlMeansDenoisingは計算コストは上がるが、文字の
         エッジを保ちながら平坦部のノイズをより強力に除去できる。
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

    # Non-Local Means denoising（cv2.fastNlMeansDenoising）を採用。単純な
    # ガウシアンブラーも試したが、スキャン画像にありがちな粒状ノイズ
    # （ランダムノイズ）を十分に均さないまま後段の適応的二値化に渡すと、
    # ノイズが局所的な明暗差として拾われ、二値化後に無数の斑点（黒い砂嵐状の
    # ノイズ）が発生し、かえって文字が読み取りにくくなることを確認した
    # （実験で判明）。fastNlMeansDenoisingは計算コストは上がるものの、
    # エッジ（文字の輪郭）を保ちながら平坦部のノイズをより強力に除去できる
    # ため、これを採用した。
    denoised = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

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


def _group_words_into_lines(data: dict) -> list:
    """
    pytesseract.image_to_data(output_type=Output.DICT)の戻り値
    （"text"・"conf"・"block_num"・"par_num"・"line_num"等の列ごとの
    並列リスト）を、(block_num, par_num, line_num)単位でグルーピングし、
    行ごとの(テキスト, 平均信頼度)のペアのリストに変換する。

    conf == -1 の要素（単語ではない、行・段落・ブロック等の構造情報のみを
    表す行。pytesseractの仕様）は、空文字列の単語同様に読み飛ばす
    （テキスト連結・信頼度集計のいずれにも含めない）。

    グループの並び順は(block_num, par_num, line_num)の昇順とする。単一列の
    通常のレイアウトであれば、この順序はおおむね読み順（上から下）と一致する。

    既知の制限：行内の単語は単純に半角スペースで連結する。日本語（CJK）は
    pytesseract.image_to_data()では文字1つずつが別々の"word"として返って
    くることが多く（スペース区切りの概念が無い言語のため）、image_to_string()
    が内部で行うような「隣接文字は詰め、離れた単語のみ空白を挿入する」
    高度な行復元は行っていない。そのため、日本語部分を含む行は
    _split_line_to_columns()（空白区切り）で意図せず細かく列分割される
    ことがある（単語間の画素ギャップから空白挿入を判定する方式も検討したが、
    文字ごとに検出される高さ・幅が不安定でかえって誤判定を招いたため見送った）。
    ただし96コード等の数字列は1つの連続した"word"として認識されるため、
    match_against_inventory()による96コード列の判定自体への影響は無い。
    列分解ロジック自体の改善は、他の箇所と同様に実際のPDFレイアウトが
    判明した時点でまとめて対応する（モジュールdocstring参照）。
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
        entry["words"].append(word)
        entry["confs"].append(conf)

    lines = []
    for key in sorted(lines_by_key.keys()):
        entry = lines_by_key[key]
        line_text = " ".join(entry["words"])
        avg_confidence = sum(entry["confs"]) / len(entry["confs"])
        lines.append((line_text, avg_confidence))
    return lines


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

    戻り値：[(行テキスト, 平均信頼度0〜100), ...]（全ページ分を連結、
    ページ間の順序はconvert_from_path()が返すページ順のまま）。
    """
    _configure_tesseract()

    images = convert_from_path(pdf_path, poppler_path=config.POPPLER_PATH)

    lines = []
    for i, image in enumerate(images):
        preprocessed = preprocess_image_for_ocr(image, debug_dir=debug_dir, debug_prefix=f"page{i + 1}")
        data = pytesseract.image_to_data(
            preprocessed, lang=_OCR_LANGUAGES, output_type=pytesseract.Output.DICT,
        )
        lines.extend(_group_words_into_lines(data))
    return lines


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
      3. 各行を_split_line_to_columns()で列分解する。

    信頼度（"confidences"）について：テキスト抽出経路（OCRを使わない場合）は
    文字化けのリスクが無いため、常に100（該当なしの意味も兼ねる）として扱う。
    OCR経路は、_group_words_into_lines()が算出したその行に属する単語群の
    信頼度の平均値を使う。

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
        lines_with_confidence = _extract_lines_via_ocr(pdf_path, debug_dir=debug_dir)
    else:
        method = "text"
        lines_with_confidence = [
            (line, 100.0) for line in text.splitlines() if line.strip()
        ]

    rows = []
    confidences = []
    for line, confidence in lines_with_confidence:
        rows.append(_split_line_to_columns(line))
        confidences.append(confidence)

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

    見つからなければNoneを返す（ヘッダー行・96コード列を含まない行等）。
    """
    for cell in row:
        stripped = cell.strip()
        if _PART_NO_PATTERN.match(stripped):
            return stripped
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
