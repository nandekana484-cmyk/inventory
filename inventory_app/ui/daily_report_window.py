# ui/daily_report_window.py
import csv
import os
import tempfile
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from tkcalendar import DateEntry

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import registerFont
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

from services.production_service import build_daily_report, build_monthly_report
from models.board_structure_master import get_board_structure

JP_FONT = "HeiseiKakuGo-W5"
registerFont(UnicodeCIDFont(JP_FONT))

REPORT_HEADERS = ["No", "ファイルNo", "基板名", "構成基板数", "ロットNo", "生産数", "注文数",
                   "引落数量", "仕掛数量", "未完了数", "確認事項"]

# PDF出力（build_daily_report_pdf()）用の列幅（ポイント単位）・折り返し対象
# 列インデックス（2026-09-28、「確認事項」列の追加に伴い新設）。A4縦・
# 左右マージン15mmずつ（doc生成時の設定）での利用可能幅は約510pt
# （595pt − 15mm×2枚 ≒ 595 − 85pt）。「確認事項」列（インデックス10、
# REPORT_HEADERSの末尾）に長い文言が入っても他の列を圧迫してページ幅を
# はみ出さないよう、他の列を切り詰めてこの列に幅を多めに配分している。
# ui/monthly_report_window.pyもこの定数をそのままimportして使う（画面ごとに
# 別の値を定義しない）。
PDF_COL_WIDTHS = [18, 42, 55, 38, 38, 30, 30, 34, 34, 34, 117]
PDF_WRAP_COLUMN_INDICES = {10}  # 「確認事項」列のみ折り返す

# ロット単位の縞模様表示（2026-09-26追加）で使うTreeviewタグ名・背景色。
# 既存の警告色（赤・オレンジ・黄・緑等、生産実績入力画面等で使用）と衝突しない
# よう、彩度の無い薄いグレー系の2色のみを使う（判断しやすさを優先し、色数は
# 2色に留めた）。
_LOT_STRIPE_TAG_A = "lot_stripe_a"
_LOT_STRIPE_TAG_B = "lot_stripe_b"
_LOT_STRIPE_COLOR_A = "#ffffff"  # 白（無色、既定の背景と同じ）
_LOT_STRIPE_COLOR_B = "#e6e6e6"  # 薄いグレー

# 「確認事項」欄の文字色タグ（2026-09-28追加）。services.production_service.
# _lot_status_color_category()が返す"needs_review"|"shortfall"に対応する。
# 縞模様タグ（backgroundのみ設定）とは別のタグとし、こちらはforegroundのみを
# 設定する（1行に両方のタグを付けても、設定する属性が重ならないため両方
# 反映される想定。Treeviewの複数タグ適用時の実際の描画は目視確認が必要、
# 詳細は各画面のdocstring・動作確認報告を参照）。
_STATUS_COLOR_TAG_NEEDS_REVIEW = "status_needs_review"
_STATUS_COLOR_TAG_SHORTFALL = "status_shortfall_note"
_STATUS_TEXT_COLOR_NEEDS_REVIEW = "#cc0000"  # 赤（確認・修正が必要）
_STATUS_TEXT_COLOR_SHORTFALL = "#cc6600"  # オレンジ（不足のため引落0）

# services.production_service._lot_status_color_category()の戻り値
# （"needs_review"|"shortfall"|None）から、上記タグ名を引くための辞書。
_STATUS_COLOR_CATEGORY_TO_TAG = {
    "needs_review": _STATUS_COLOR_TAG_NEEDS_REVIEW,
    "shortfall": _STATUS_COLOR_TAG_SHORTFALL,
}


def configure_lot_stripe_tags(tree):
    """
    Treeviewにロット単位の縞模様タグを設定する（2026-09-26追加）。Treeview
    生成直後に1回だけ呼び出す（tag_configure()自体はタグの見た目を定義する
    だけで、行ごとの呼び出しは不要なため）。
    """
    tree.tag_configure(_LOT_STRIPE_TAG_A, background=_LOT_STRIPE_COLOR_A)
    tree.tag_configure(_LOT_STRIPE_TAG_B, background=_LOT_STRIPE_COLOR_B)


def configure_status_color_tags(tree):
    """
    Treeviewに「確認事項」欄の文字色タグを設定する（2026-09-28追加）。
    configure_lot_stripe_tags()と同様、Treeview生成直後に1回だけ呼び出す。
    縞模様タグ（background）とは別のタグ（foregroundのみ）のため、
    populate_report_tree()で1行に両方のタグを渡して併用する。
    """
    tree.tag_configure(_STATUS_COLOR_TAG_NEEDS_REVIEW, foreground=_STATUS_TEXT_COLOR_NEEDS_REVIEW)
    tree.tag_configure(_STATUS_COLOR_TAG_SHORTFALL, foreground=_STATUS_TEXT_COLOR_SHORTFALL)


def populate_report_tree(tree, report_rows):
    """
    report_rowsをTreeviewへ挿入する（2026-09-26追加、日報・月報で共通化）。
    lot_noごとに縞模様タグ（lot_stripe_a/b）を割り当てる。

    割り当て方法：単純に「直前の行とlot_noが変わったら切り替える」方式では
    なく、**lot_no単位で色を1回だけ確定し、以降その色を使い回す**辞書
    （lot_tag_map）方式を採用した。理由：report_rowsの並び順は
    production_dailyのレコード順であり、同一lot_noの行が必ず連続して
    並ぶ保証が無い（他のlot_noの行が間に挟まることがある）ため、単純な
    「直前行との比較」方式では同じlot_noなのに離れた位置にある行同士が
    別の色になってしまう。また、構成基板数チェックで追加される「未確定」
    仮想行（services.production_service._build_report_rows()参照）は、
    元のlot_noの実データ行とは異なる位置（末尾にまとめて追加される）に
    挿入されるため、この辞書方式でなければ仮想行を元のロットと同じ色に
    揃えることができない。

    色の割り当て順は、report_rows内でそのlot_noが最初に登場した順（＝
    新しいlot_noに出会うたびに交互に切り替え）とする。
    """
    lot_tag_map = {}
    use_tag_b = False
    for row in report_rows:
        lot_no = row["lot_no"]
        if lot_no not in lot_tag_map:
            use_tag_b = not use_tag_b
            lot_tag_map[lot_no] = _LOT_STRIPE_TAG_B if use_tag_b else _LOT_STRIPE_TAG_A

        tags = [lot_tag_map[lot_no]]
        # 「確認事項」欄の文字色タグ（2026-09-28追加）。縞模様タグ（background
        # のみ）とは別に、statusに応じたforegroundのみのタグを追加する。
        # matchかつ未登録board_nameも無いロットはstatus_color_categoryが
        # Noneのため、色タグを追加しない（既定の文字色のまま）。
        color_tag = _STATUS_COLOR_CATEGORY_TO_TAG.get(row.get("status_color_category"))
        if color_tag:
            tags.append(color_tag)

        tree.insert("", tk.END, values=_row_to_values(row), tags=tuple(tags))


def _format_board_count(board_name):
    """
    board_nameから構成基板数マスタ（models.board_structure_master）を検索し、
    表示用の文字列を返す（2026-09-26追加）。未登録の場合は「未登録」を返す
    （ui.kitting_production_entry.py計画情報欄の「未登録」表示と同じ文言に
    揃えた）。「未確定」仮想行のboard_nameには実際の基板名がそのまま入って
    いる（services.production_service._build_report_rows()参照）ため、
    他の行と全く同じ処理で構成基板数がそのまま表示される。
    """
    if not board_name:
        return "未登録"
    board_structure = get_board_structure(board_name)
    if board_structure is None or board_structure.get("board_count") is None:
        return "未登録"
    return f"{board_structure['board_count']:g}"


def _row_to_values(row):
    # 数量項目は生産実績入力画面の計画一覧（load_plan_list()）と同様に整数表示に揃える
    # （DB上はREAL/INTEGER混在のため、無加工だと"100.0"のように小数点が出てしまう）。
    # 「確認事項」（confirmation_note、2026-09-28追加）は、REPORT_HEADERS・
    # 本関数を経由すればCSV・PDF・印刷プレビューにも自動的に反映される
    # （services.production_service._build_report_rows()が全rowに含めている）。
    return [
        row["seq"], row["file_no"], row["board_name"], _format_board_count(row["board_name"]), row["lot_no"],
        f"{row['daily_qty']:.0f}", f"{row['order_qty']:.0f}",
        f"{row['lot_completed']:.0f}", f"{row['surplus_qty']:.0f}", f"{row['lot_remaining']:.0f}",
        row.get("confirmation_note", ""),
    ]


def build_daily_report_pdf(report_rows, report_date, output_path, title_prefix="日報",
                             headers=None, row_to_values=None,
                             col_widths=None, wrap_column_indices=None):
    """
    日報データを A4縦PDFとして出力する。
    行数が1ページに収まらない場合は reportlab の Table により自動改ページされる。

    title_prefix はタイトル先頭の帳票名（日報／月報など）。省略時は日報用の表記になる。
    headers / row_to_values を指定すると、日報以外の列構成のレポート
    （在庫差異レポート等）でもこの関数をそのまま再利用できる。
    省略時は日報・月報用の REPORT_HEADERS / _row_to_values を使う。

    col_widths / wrap_column_indices（2026-09-28追加、「確認事項」列対応）：
    省略時（None）は以前と全く同じ挙動（reportlabのTableが内容の自然な
    幅で自動サイズする）のまま変更しない（本関数は日報・月報以外にも
    NG一覧・仕掛一覧・在庫差異レポート等、複数の呼び出し元が異なる列構成で
    共有しているため、それらへの影響を避けるためデフォルトは無変更とした）。
    col_widths（headersと同じ要素数のポイント単位の幅リスト）を指定すると、
    Tableの各列幅を固定する。wrap_column_indices（折り返しを行う列の
    0始まりインデックスの集合）を指定すると、その列のセル値を
    reportlab.platypus.Paragraphで包み、col_widthsで指定した幅の中で
    自動的に折り返す（「確認事項」列のように長い文言が入り得る列が、
    ページ幅からはみ出さないようにするため。プレーン文字列のセルは
    Tableの自然な幅で描画されるため、長い文言は折り返されずページ幅を
    超えて描画されるリスクがある）。
    """
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Title"]
    title_style.fontName = JP_FONT

    elements = [Paragraph(f"{title_prefix} {report_date}", title_style), Spacer(1, 10)]

    headers = headers if headers is not None else REPORT_HEADERS
    row_to_values = row_to_values if row_to_values is not None else _row_to_values
    wrap_column_indices = wrap_column_indices or set()

    cell_style = styles["Normal"]
    cell_style.fontName = JP_FONT
    cell_style.fontSize = 8
    cell_style.leading = 10

    def _build_cell(col_index, value):
        if col_index in wrap_column_indices:
            return Paragraph(str(value), cell_style)
        return value

    data = [headers] + [
        [_build_cell(i, v) for i, v in enumerate(row_to_values(row))]
        for row in report_rows
    ]
    table = Table(data, repeatRows=1, colWidths=col_widths)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), JP_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(table)

    def draw_page_number(canvas_obj, doc_obj):
        canvas_obj.setFont(JP_FONT, 8)
        canvas_obj.drawCentredString(A4[0] / 2, 10 * mm, f"ページ {doc_obj.page}")

    doc.build(elements, onFirstPage=draw_page_number, onLaterPages=draw_page_number)


class ReportPreviewWindow(tk.Toplevel):
    """
    日報データを A4縦レイアウトのページとしてプレビュー表示するウィンドウ。
    1ページに収まらない行は自動で2ページ目以降に送られる。
    """
    A4_WIDTH = 595
    A4_HEIGHT = 842
    ROWS_PER_PAGE = 35

    COL_HEADERS = REPORT_HEADERS
    # 2026-09-28、「確認事項」列の追加に伴い、他の列幅を詰めて確保した
    # （プレビューは固定ピクセル幅のキャンバスに単純にテキスト描画するのみで
    # 折り返し機構が無いため、「確認事項」に長い文言が入ると列の右側へ
    # はみ出す可能性がある。この点はPDF出力（Paragraphによる自動折り返し
    # 対応済み、build_daily_report_pdf()参照）と異なり、プレビュー画面固有の
    # 制約として残る。実際の見え方は目視確認が必要）。
    COL_WIDTHS = [20, 50, 65, 50, 50, 35, 35, 40, 40, 40, 120]

    def __init__(self, parent, report_rows, report_date, title_prefix="日報",
                 headers=None, col_widths=None, row_to_values=None):
        super().__init__(parent)
        self.report_rows = report_rows
        self.report_date = report_date
        self.title_prefix = title_prefix
        self.col_headers = headers if headers is not None else self.COL_HEADERS
        self.col_widths = col_widths if col_widths is not None else self.COL_WIDTHS
        self.row_to_values = row_to_values if row_to_values is not None else _row_to_values

        self.title("印刷プレビュー")
        self.geometry("660x760")

        outer = ttk.Frame(self)
        outer.pack(expand=True, fill=tk.BOTH)

        self.canvas_scroll = tk.Canvas(outer, bg="#808080")
        vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas_scroll.yview)
        self.canvas_scroll.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas_scroll.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        self.pages_frame = ttk.Frame(self.canvas_scroll)
        self.canvas_scroll.create_window((0, 0), window=self.pages_frame, anchor="nw")
        self.pages_frame.bind(
            "<Configure>",
            lambda e: self.canvas_scroll.configure(scrollregion=self.canvas_scroll.bbox("all")),
        )

        self._render_pages()

    def _paginate(self):
        rows = self.report_rows
        if not rows:
            return [[]]
        return [rows[i:i + self.ROWS_PER_PAGE] for i in range(0, len(rows), self.ROWS_PER_PAGE)]

    def _render_pages(self):
        pages = self._paginate()
        total_pages = len(pages)
        for page_index, page_rows in enumerate(pages, start=1):
            page_canvas = tk.Canvas(
                self.pages_frame, width=self.A4_WIDTH, height=self.A4_HEIGHT,
                bg="white", highlightthickness=1, highlightbackground="black",
            )
            page_canvas.pack(pady=15)
            self._draw_page(page_canvas, page_rows, page_index, total_pages)

    def _draw_page(self, canvas, rows, page_no, total_pages):
        margin = 30

        canvas.create_text(
            self.A4_WIDTH / 2, margin, text=f"{self.title_prefix} {self.report_date}",
            font=("Helvetica", 14, "bold"),
        )

        col_x = [margin]
        for w in self.col_widths:
            col_x.append(col_x[-1] + w)

        header_y = margin + 30
        for i, h in enumerate(self.col_headers):
            canvas.create_text(col_x[i] + 3, header_y, text=h, anchor="nw", font=("Helvetica", 9, "bold"))
        canvas.create_line(margin, header_y + 16, col_x[-1], header_y + 16)

        row_height = 18
        y = header_y + 20
        for row in rows:
            for i, v in enumerate(self.row_to_values(row)):
                canvas.create_text(col_x[i] + 3, y, text=str(v), anchor="nw", font=("Helvetica", 8))
            y += row_height

        canvas.create_text(
            self.A4_WIDTH / 2, self.A4_HEIGHT - margin,
            text=f"ページ {page_no} / {total_pages}", font=("Helvetica", 9),
        )


class DailyReportWindow(tk.Toplevel):
    """
    本日入力された生産実績を一覧表示し、印刷プレビュー・印刷・PDF出力・CSV出力を行うウィンドウ。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.report_date = datetime.now().strftime("%Y-%m-%d")
        # inconsistency_warnings（面1・面2の実績不整合）は月報画面（ui/monthly_report_window.py）
        # 側でのみ警告表示する（要求スコープ）。日報側はタプルを正しく受け取り
        # 保持するに留める。
        # order_qty_inconsistency_warnings・unregistered_board_warnings
        # （2026-09-26追加）は月報限定の警告のため（面1/面2不整合警告と同じ
        # 既存方針）、日報側では受け取るのみでダイアログ表示等は行わない。
        self.report_rows, self.inconsistency_warnings, _, _, _, _ = build_daily_report()

        self.title(f"日報出力（{self.report_date}）")
        self.geometry("1020x500")

        date_frame = ttk.Frame(self, padding=10)
        date_frame.pack(fill=tk.X)

        ttk.Label(date_frame, text="対象日：").pack(side=tk.LEFT, padx=5)
        self.date_entry = DateEntry(date_frame, date_pattern="yyyy-mm-dd", width=12, locale="ja_JP")
        self.date_entry.pack(side=tk.LEFT, padx=5)

        ttk.Button(date_frame, text="表示", command=self.on_display).pack(side=tk.LEFT, padx=10)

        tree_frame = ttk.Frame(self, padding=10)
        tree_frame.pack(expand=True, fill=tk.BOTH)

        cols = ("seq", "file_no", "board_name", "board_count", "lot_no", "daily_qty", "order_qty",
                "lot_completed", "surplus_qty", "lot_remaining")
        headers = dict(zip(cols, REPORT_HEADERS))
        widths = {
            "seq": 50, "file_no": 100, "board_name": 160, "board_count": 80, "lot_no": 110,
            "daily_qty": 80, "order_qty": 80,
            "lot_completed": 80, "surplus_qty": 80, "lot_remaining": 80,
        }
        left_aligned = {"file_no", "board_name", "board_count", "lot_no"}

        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings")
        for c in cols:
            self.tree.heading(c, text=headers[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c in left_aligned else tk.E)
        self.tree.pack(expand=True, fill=tk.BOTH)
        self.tree.bind("<Double-1>", self.on_row_double_click)
        configure_lot_stripe_tags(self.tree)

        populate_report_tree(self.tree, self.report_rows)

        btn_frame = ttk.Frame(self, padding=10)
        btn_frame.pack(fill=tk.X)

        ttk.Button(btn_frame, text="印刷プレビュー", command=self.on_preview).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="印刷", command=self.on_print).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="PDF出力", command=self.on_export_pdf).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="CSV出力", command=self.on_export_csv).pack(side=tk.LEFT, padx=5)

    def on_display(self):
        selected_date = self.date_entry.get()

        try:
            self.report_rows, self.inconsistency_warnings, _, _, _, _ = build_monthly_report(selected_date, selected_date)
        except Exception as e:
            messagebox.showerror("エラー", f"集計に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        self.report_date = selected_date
        self.title(f"日報出力（{self.report_date}）")

        for item in self.tree.get_children():
            self.tree.delete(item)
        populate_report_tree(self.tree, self.report_rows)

    def on_row_double_click(self, event):
        """
        選択行に対応する実績（kitting_list_no・lot_no）を実績修正ウインドウ
        （ui.kitting_production_entry.ActualCorrectionWindow）で開く。
        完了済み（生産実績入力画面の一覧からは除外済み）の計画でも、
        production_daily に実績が残っている限りここから修正できる。
        循環import回避のため、ここで都度importする。
        """
        sel = self.tree.selection()
        if not sel:
            return
        index = self.tree.index(sel[0])
        if index >= len(self.report_rows):
            return
        row = self.report_rows[index]
        kitting_list_no = row["kitting_list_no"]
        if not kitting_list_no:
            return

        from ui.kitting_production_entry import ActualCorrectionWindow
        ActualCorrectionWindow(
            self,
            kitting_list_no=kitting_list_no,
            lot_no=row["lot_no"],
            on_updated=self.refresh_report,
        )

    def refresh_report(self):
        """実績修正後に日報の一覧を再取得して表示を更新する。"""
        self.report_rows, self.inconsistency_warnings, _, _, _, _ = build_monthly_report(self.report_date, self.report_date)
        for item in self.tree.get_children():
            self.tree.delete(item)
        populate_report_tree(self.tree, self.report_rows)

    def on_preview(self):
        ReportPreviewWindow(self, self.report_rows, self.report_date)

    def on_print(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "本日の実績データがありません。", parent=self.winfo_toplevel())
            return

        tmp_path = os.path.join(
            tempfile.gettempdir(), f"daily_report_{self.report_date.replace('-', '')}_print.pdf"
        )
        try:
            build_daily_report_pdf(
                self.report_rows, self.report_date, tmp_path,
                col_widths=PDF_COL_WIDTHS, wrap_column_indices=PDF_WRAP_COLUMN_INDICES,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF生成に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        try:
            os.startfile(tmp_path, "print")
        except Exception as e:
            messagebox.showerror("エラー", f"印刷の起動に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("印刷", "OS標準の印刷ダイアログを開きました。", parent=self.winfo_toplevel())

    def on_export_pdf(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "本日の実績データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"daily_report_{self.report_date.replace('-', '')}.pdf"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            build_daily_report_pdf(
                self.report_rows, self.report_date, save_path,
                col_widths=PDF_COL_WIDTHS, wrap_column_indices=PDF_WRAP_COLUMN_INDICES,
            )
        except Exception as e:
            messagebox.showerror("エラー", f"PDF出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"PDFを保存しました：\n{save_path}", parent=self.winfo_toplevel())

    def on_export_csv(self):
        if not self.report_rows:
            messagebox.showwarning("警告", "本日の実績データがありません。", parent=self.winfo_toplevel())
            return

        default_name = f"daily_report_{self.report_date.replace('-', '')}.csv"
        save_path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile=default_name,
            filetypes=[("CSV files", "*.csv")],
        parent=self.winfo_toplevel())
        if not save_path:
            return

        try:
            with open(save_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(REPORT_HEADERS)
                for row in self.report_rows:
                    writer.writerow(_row_to_values(row))
        except Exception as e:
            messagebox.showerror("エラー", f"CSV出力に失敗しました：{e}", parent=self.winfo_toplevel())
            return

        messagebox.showinfo("完了", f"CSVを保存しました：\n{save_path}", parent=self.winfo_toplevel())
