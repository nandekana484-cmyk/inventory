# 開発環境の構築

## 手順

1. リポジトリ直下に仮想環境を作る（2026-10-09 は Python 3.10.11 で作成）
   ```
   python -m venv .venv
   ```
2. 依存パッケージを入れる
   ```
   .venv\Scripts\python.exe -m pip install -r inventory_app\requirements.txt
   ```
3. 起動は `run_app.bat`（`.venv\Scripts\python.exe` で `inventory_app\main.py` を実行する）
4. テストは `inventory_app` で `..\.venv\Scripts\python.exe -m pytest tests/`

- `inventory_app\requirements.txt` は `>=` 指定なので、入れた日の最新版が入る
- 同じ版を再現したいときは、lock のほう（`.venv\Scripts\python.exe -m pip install -r inventory_app\requirements.lock.txt`）を使う。2026-10-09 に作り直した `.venv` の pip freeze
- `inventory_app\requirements.txt` のコメントは英数字だけにしている。`.venv` に入る pip 23 は requirements.txt を Windows の既定の文字コード（cp932）で読むため、日本語（BOM なしの UTF-8）があると `UnicodeDecodeError` で止まった（2026-10-09）

## OS に別途インストールが必要なもの（PDF 読み取り・OCR）

pdfplumber・pytesseract・pdf2image は Python のパッケージだけでは動かない。メニューの「PDF読み取り」は現在、常に無効（D-85）。

- Tesseract OCR 本体（日本語データ jpn.traineddata を含む）
  - 場所は `config.TESSERACT_CMD`。既定は `C:\Program Files\Tesseract-OCR\tesseract.exe`（Windows 版の公式ビルドの既定のインストール先）。環境変数 `TESSERACT_CMD` で変えられる
  - 言語データは `config.TESSDATA_DIR`（既定は `inventory_app\tessdata_local\`、Git 管理外）。新しい環境では jpn.traineddata・eng.traineddata・osd.traineddata をここに置く。環境変数 `TESSDATA_DIR` で変えられる
- Poppler（pdftoppm など。pdf2image が PDF を画像にするのに使う）
  - bin フォルダを環境変数 `POPPLER_PATH` で指定する。未設定なら PATH 上のものを使う
- 詳細は `config.py` と `docs/domain/pdf_ocr.md`

## パッケージの用途

| パッケージ | 用途 |
|---|---|
| pandas・openpyxl | データ処理（2026-10-09 時点で、inventory_app のコードからは import されていない） |
| reportlab | 帳票出力（PDF） |
| tkcalendar | 日付入力の UI |
| pytest | テスト |
| fastapi・uvicorn[standard]・python-dotenv | 将来の拡張・Web API 化用 |
| pyinstaller | ビルド・配布（.exe、`inventory_app.spec`） |
| pdfplumber・pytesseract・pdf2image | PDF 読み取り・OCR（PDF → CSV 変換） |
| opencv-python-headless | OCR の前処理（傾き補正・コントラスト強調・ノイズ除去）。GUI の機能（imshow など）は使わないので、依存の軽い headless 版にした |
