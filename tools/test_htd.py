"""tools/htd.py の検査（DOCS_SCHEMA.md §2・§4〜§6）。ネットワークは使わない。

実行: python3 -B -m unittest discover -s tools
見本の PDF はこの検査の中で作る（pdftotext・pdfinfo が要る。無ければ PDF の検査は飛ばす）。
"""
import os
import shutil
import subprocess
import sys
import datetime as dt
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402

HAS_POPPLER = bool(shutil.which("pdftotext") and shutil.which("pdfinfo") and shutil.which("pdftoppm"))
HAS_GIT = bool(shutil.which("git"))
try:
    import openpyxl  # noqa: F401
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# NDLOCR-Lite の代わり（ocr.py と同じ引数で動き、画像1枚ごとに <名前>.txt と .json を書く。
# 環境変数 FAKE_EMPTY の名前は .json だけ（字の無いページ）、FAKE_SKIP の名前は何も書かない）
FAKE_OCR = """import json, os, sys
a = sys.argv
src, out = a[a.index("--sourcedir") + 1], a[a.index("--output") + 1]
for name in sorted(os.listdir(src), reverse=True):
    stem = os.path.splitext(name)[0]
    if stem in os.environ.get("FAKE_SKIP", "").split(","):
        continue
    with open(os.path.join(out, stem + ".json"), "w") as f:
        json.dump({"stem": stem}, f)
    if stem not in os.environ.get("FAKE_EMPTY", "").split(","):
        with open(os.path.join(out, stem + ".txt"), "w", encoding="utf-8") as f:
            f.write("読んだ " + stem)
"""
LINE = "The town budget overview for the fiscal year lists the main projects and their costs in detail."


def make_pdf(pages):
    """各ページに、渡した文字列（ASCII）を1行ずつ置いた PDF のバイト列。空文字のページは白紙。"""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        content = f"BT /F1 9 Tf 20 700 Td ({text}) Tj ET" if text else ""
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offs = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


class Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.saved = (htd.SOURCES, htd.TEXT_DIR, htd.RAW_DIR, htd.WAIT, htd.OCR_DIR, htd.OCR_RAW_DIR,
                      htd.NDLOCR_DIR, htd.NDLOCR_PYTHON, htd.TEXT_RAW_DIR)
        htd.SOURCES = os.path.join(self.d, "data", "sources.tsv")
        htd.TEXT_DIR = os.path.join(self.d, "data", "text")
        htd.TEXT_RAW_DIR = os.path.join(self.d, "data", "text_raw")
        htd.RAW_DIR = os.path.join(self.d, "sources_raw")
        htd.OCR_DIR = os.path.join(self.d, "data", "ocr")
        htd.OCR_RAW_DIR = os.path.join(self.d, "ocr_raw")
        htd.WAIT = 0

    def tearDown(self):
        (htd.SOURCES, htd.TEXT_DIR, htd.RAW_DIR, htd.WAIT, htd.OCR_DIR, htd.OCR_RAW_DIR,
         htd.NDLOCR_DIR, htd.NDLOCR_PYTHON, htd.TEXT_RAW_DIR) = self.saved
        for k in ("FAKE_EMPTY", "FAKE_SKIP"):
            os.environ.pop(k, None)
        shutil.rmtree(self.d)

    def request(self, rows):
        p = os.path.join(self.d, "req.tsv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("\t".join(htd.REQUEST_COLUMNS) + "\n")
            for r in rows:
                f.write("\t".join(r.get(c, "") for c in htd.REQUEST_COLUMNS) + "\n")
        return p


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def req(i, url=None, **kw):
    r = {"id": i, "category": i.split("-")[0], "title": f"見本 {i}", "fiscal_year": "2026", "date": "",
         "page_url": "https://www.town.shiga-hino.lg.jp/0000008534.html",
         "url": url or f"https://www.town.shiga-hino.lg.jp/cmsfiles/{i}.pdf", "size_label": ""}
    r.update(kw)
    return r


class TestClassify(unittest.TestCase):
    def test_rates(self):
        self.assertEqual(htd.classify([60, 60, 60, 60, 0]), "ok")        # 8割
        self.assertEqual(htd.classify([60, 60, 60, 0, 0]), "partial")    # 6割
        self.assertEqual(htd.classify([60, 0, 0, 0, 0]), "partial")      # ちょうど2割は partial
        self.assertEqual(htd.classify([60, 0, 0, 0, 0, 0]), "image")     # 2割未満
        self.assertEqual(htd.classify([50, 49]), "partial")              # 50字ちょうどは文字のあるページ
        self.assertEqual(htd.classify([]), "ng")

    def test_size(self):
        self.assertEqual(htd.size_bytes("1.23MB"), 1.23 * 1024 ** 2)
        self.assertEqual(htd.size_bytes("サイズ：443.03KB"), 443.03 * 1024)
        self.assertIsNone(htd.size_bytes(""))


class TestRequest(Tmp):
    def test_errors(self):
        rows = [req("yosan-r8-gaiyou"), req("yosan-r8-gaiyou"), req("Yosan-r8"), req("kessan-r6-gaiyou", category="yosan"),
                req("yosan-r7-gaiyou", url="https://example.com/a.pdf"), req("yosan-r6-gaiyou", fiscal_year="R6")]
        with self.assertRaises(htd.Stop) as cm:
            htd.check_request(rows, {"yosan-r5-gaiyou"})
        msg = str(cm.exception)
        for needle in ("重なっている", "id の形が違う", "区分で始まらない", "町のサイトでない", "西暦の年度でない"):
            self.assertIn(needle, msg)

    def test_existing_id_stops(self):
        with self.assertRaises(htd.Stop) as cm:
            htd.check_request([req("yosan-r8-gaiyou")], {"yosan-r8-gaiyou"})
        self.assertIn("台帳にすでにある", str(cm.exception))

    def test_shingi_is_a_category(self):
        htd.check_request([req("shingi-youho-teigen"), req("shingi-youho-gijiroku-01", fiscal_year="2022")], set())

    def test_bosai_is_a_category(self):
        htd.check_request([req("bosai-h30-hinanjo", fiscal_year="2018")], set())

    def test_jigyo_is_a_category(self):
        htd.check_request([req("jigyo-shinkodomoen-kousou"), req("jigyo-shinkodomoen-pabu")], set())

    def test_koho_date_is_year_month(self):
        htd.check_request([req("koho-no792-04", date="2026-07")], set())
        with self.assertRaises(htd.Stop) as cm:
            htd.check_request([req("koho-no792-05", date="2026-07-01"), req("yosan-r8-gaiyou", date="2026-03")], set())
        msg = str(cm.exception)
        self.assertIn("広報ひのの date が YYYY-MM でない", msg)
        self.assertIn("date が YYYY-MM-DD でない", msg)


class TestLedgerV1(Tmp):
    def test_old_header_is_read_and_rewritten(self):
        # 0c64af5 より前の台帳（ocr_method・ocr_date が無い）は、2列を空で足して読み、書くときは新しい見出しにする
        os.makedirs(os.path.dirname(htd.SOURCES))
        row = {c: "" for c in htd.COLUMNS_V1}
        row.update(id="yosan-r8-gaiyou", category="yosan", format="pdf", pages="1", check="ok")
        with open(htd.SOURCES, "w", encoding="utf-8", newline="") as f:
            f.write("\t".join(htd.COLUMNS_V1) + "\n" + "\t".join(row[c] for c in htd.COLUMNS_V1) + "\n")
        rows = htd.read_sources()
        self.assertEqual((rows[0]["ocr_method"], rows[0]["ocr_date"]), ("", ""))
        htd.write_sources(rows)
        self.assertEqual(read_text(htd.SOURCES).splitlines()[0].split("\t"), htd.COLUMNS)
        self.assertEqual(len(htd.COLUMNS), 21)

    def test_v2_header_gets_removed_columns(self):
        # 2026-11-10 より前の台帳（removed・removed_judgment・archive_url が無い）は、3列を空で足して読む（§4）
        os.makedirs(os.path.dirname(htd.SOURCES))
        row = {c: "" for c in htd.COLUMNS_V2}
        row.update(id="yosan-r8-gaiyou", category="yosan", format="pdf", pages="1", check="ok")
        with open(htd.SOURCES, "w", encoding="utf-8", newline="") as f:
            f.write("\t".join(htd.COLUMNS_V2) + "\n" + "\t".join(row[c] for c in htd.COLUMNS_V2) + "\n")
        rows = htd.read_sources()
        self.assertEqual([rows[0][c] for c in htd.REMOVED_COLUMNS], ["", "", ""])
        htd.write_sources(rows)
        self.assertEqual(read_text(htd.SOURCES).splitlines()[0].split("\t"), htd.COLUMNS)


class TestRemoved(Tmp):
    URL = "https://www.town.shiga-hino.lg.jp/cmsfiles/contents/0000008/8428/kousou.pdf"

    def write(self, **kw):
        os.makedirs(os.path.dirname(htd.SOURCES), exist_ok=True)
        row = {c: "" for c in htd.COLUMNS}
        row.update(id="jigyo-shinkodomoen-kousou", category="jigyo", format="pdf", pages="1", check="ok", url=self.URL)
        row.update(kw)
        with open(htd.SOURCES, "w", encoding="utf-8", newline="") as f:
            f.write("\t".join(htd.COLUMNS) + "\n" + "\t".join(row[c] for c in htd.COLUMNS) + "\n")

    def test_good_values(self):
        self.write(removed="2026-10-23", removed_judgment="replaced",
                   archive_url="https://web.archive.org/web/20260308000000id_/" + self.URL)
        self.assertEqual(htd.read_sources()[0]["removed_judgment"], "replaced")

    def test_bad_values_stop(self):
        bad = [dict(removed="2026/10/23"), dict(removed="2026-10-23", removed_judgment="ok"),
               dict(removed_judgment="withheld"),
               dict(removed="2026-10-23", archive_url="https://web.archive.org/web/20260308000000id_/https://example.org/x.pdf")]
        for kw in bad:
            self.write(**kw)
            with self.assertRaises(htd.Stop):
                htd.read_sources()


class TestWriteAtomic(Tmp):
    def test_failure_keeps_old_ledger(self):
        # 書いている途中で止まっても、前の台帳がそのまま残り、一時ファイルも残らない
        row = {c: "" for c in htd.COLUMNS}
        row.update(id="yosan-r8-gaiyou", category="yosan", format="pdf", pages="1", check="ok")
        htd.write_sources([row])
        before = read_text(htd.SOURCES)

        def broken(f):
            f.write("途中まで")
            raise OSError("書けない（見本）")
        with self.assertRaises(OSError):
            htd.write_atomic(htd.SOURCES, broken)
        self.assertEqual(read_text(htd.SOURCES), before)
        self.assertEqual(os.listdir(os.path.dirname(htd.SOURCES)), ["sources.tsv"])


class TestUnexpected(unittest.TestCase):
    def test_count(self):
        self.assertEqual(htd.unexpected("令和3年度 ①㎡※℃ CO₂ ➢ 備︑被害︒"), 0)     # 縦書きの句読点（U+FE11・U+FE12）も使う字
        self.assertEqual(htd.unexpected("物価⾼騰 ⺠"), 0)                            # 部首の字は数えない
        self.assertEqual(htd.unexpected("\u0a5e\u202b97\u202c ¾ \x1a\x08"), 6)          # 化けた字・制御文字

    def test_note(self):
        self.assertEqual(htd.unexpected_note("ふつう\f\u0a5e\u0a5e\fふつう\f\x1a"), "想定外の字のあるページ：2（2字）・4（1字）")
        self.assertEqual(htd.unexpected_note("ふつう\fふつう"), "")


@unittest.skipUnless(HAS_POPPLER, "pdftotext・pdfinfo が無い")
class TestFetch(Tmp):
    def test_fetch_writes_ledger_and_text(self):
        pdfs = {"yosan-r8-gaiyou": make_pdf([LINE] * 4 + [""]), "yosan-r8-hoshin": make_pdf([LINE] + [""] * 5),
                "kessan-r6-gaiyou": make_pdf([LINE, LINE, "", ""])}
        opener = lambda url: pdfs[url.rsplit("/", 1)[1][:-4]]
        p = self.request([req(i, size_label="1.00MB" if i == "yosan-r8-hoshin" else "") for i in pdfs])
        done, stop, total = htd.fetch(p, opener=opener, today=date(2026, 10, 21))
        self.assertEqual((len(done), stop, total), (3, None, 3))
        rows = {r["id"]: r for r in htd.read_sources()}
        self.assertEqual([r["id"] for r in htd.read_sources()], sorted(pdfs))   # id の順
        self.assertEqual(rows["yosan-r8-gaiyou"]["check"], "ok")
        self.assertEqual(rows["kessan-r6-gaiyou"]["check"], "partial")
        self.assertIn("文字の少ないページ：3・4", rows["kessan-r6-gaiyou"]["note"])
        self.assertEqual(rows["yosan-r8-hoshin"]["check"], "image")
        self.assertIn("掲載ページの大きさ 1.00MB", rows["yosan-r8-hoshin"]["note"])
        self.assertEqual((rows["yosan-r8-gaiyou"]["pages"], rows["yosan-r8-gaiyou"]["fetched"]), ("5", "2026-10-21"))
        self.assertTrue(os.path.exists(os.path.join(htd.TEXT_DIR, "yosan-r8-gaiyou.txt")))
        self.assertFalse(os.path.exists(os.path.join(htd.TEXT_DIR, "yosan-r8-hoshin.txt")))   # image はテキストを置かない
        text = read_text(os.path.join(htd.TEXT_DIR, "yosan-r8-gaiyou.txt"))
        self.assertEqual(text.count("\f"), 5)                                            # 改ページを残す
        # 同じ id をもう一度頼むと止める
        with self.assertRaises(htd.Stop):
            htd.fetch(p, opener=opener)

    def test_fetch_from_archive(self):
        url = "https://www.town.shiga-hino.lg.jp/cmsfiles/contents/0000008/8428/kousou.pdf"
        arc = "https://web.archive.org/web/20250830131330id_/" + url
        p = os.path.join(self.d, "req.tsv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("\t".join(htd.REQUEST_COLUMNS_ARCHIVE) + "\n")
            r = req("jigyo-shinkodomoen-kousou", url=url)
            r["archive_url"] = arc
            f.write("\t".join(r.get(c, "") for c in htd.REQUEST_COLUMNS_ARCHIVE) + "\n")
        asked = []
        htd.fetch(p, opener=lambda u: asked.append(u) or make_pdf([LINE] * 2))
        self.assertEqual(asked, [arc])                                   # 保存版から取る
        row = htd.read_sources()[0]
        self.assertEqual(row["url"], url)                                # 台帳の url は町の元の URL
        self.assertIn("取得元：Webアーカイブの保存版（2025-08-30 13:13:30 UTC、" + arc + "）", row["note"])
        self.assertEqual((row["archive_url"], row["removed"], row["removed_judgment"]), (arc, row["fetched"], ""))   # §6
        htd.extract()                                                    # やり直しても取得元の注記は残る
        self.assertIn("取得元：Webアーカイブの保存版", htd.read_sources()[0]["note"])

    def test_archive_url_must_match_url(self):
        url = "https://www.town.shiga-hino.lg.jp/cmsfiles/contents/0000008/8428/kousou.pdf"
        for bad in ["https://web.archive.org/web/20250830131330id_/https://www.town.shiga-hino.lg.jp/x.pdf",
                    "https://web.archive.org/web/20250830131330/" + url, "https://example.org/" + url]:
            r = req("jigyo-shinkodomoen-kousou", url=url)
            r["archive_url"] = bad
            with self.assertRaises(htd.Stop) as cm:
                htd.check_request([r], set())
            self.assertIn("archive_url", str(cm.exception))

    def test_stop_keeps_what_was_fetched(self):
        def opener(url):
            if "kessan" in url:
                raise htd.Stop("取得できない（見本）：HTTP Error 403")
            return make_pdf([LINE] * 3)
        p = self.request([req("yosan-r8-gaiyou"), req("kessan-r6-gaiyou"), req("yosan-r7-gaiyou")])
        done, stop, total = htd.fetch(p, opener=opener)
        self.assertEqual(([r["id"] for r in done], total), (["yosan-r8-gaiyou"], 3))
        self.assertIn("403", stop)
        self.assertEqual([r["id"] for r in htd.read_sources()], ["yosan-r8-gaiyou"])

    def test_extract_again(self):
        p = self.request([req("yosan-r8-gaiyou")])
        htd.fetch(p, opener=lambda url: make_pdf([LINE] * 2))
        before = read_text(htd.SOURCES)
        htd.extract()
        self.assertEqual(read_text(htd.SOURCES), before)
        # 原典が差し替わっていたら止める
        with open(os.path.join(htd.RAW_DIR, "yosan-r8-gaiyou.pdf"), "wb") as f:
            f.write(make_pdf([LINE]))
        with self.assertRaises(htd.Stop) as cm:
            htd.extract(["yosan-r8-gaiyou"])
        self.assertIn("sha256 が台帳と違う", str(cm.exception))

    def test_extract_keeps_hand_note_first(self):
        # 手で書いた注記は前に残り、道具の注記は末尾に書き直される（§4）
        p = self.request([req("kessan-r6-gaiyou")])
        htd.fetch(p, opener=lambda url: make_pdf([LINE, LINE, "", ""]))
        rows = htd.read_sources()
        self.assertEqual(rows[0]["note"], "文字の少ないページ：3・4")
        rows[0]["note"] = "抜き取り（2026-10-21）：1ページ。欠けと誤りなし。文字の少ないページ：3・4"
        htd.write_sources(rows)
        before = read_text(htd.SOURCES)
        htd.extract()
        htd.extract()
        self.assertEqual(read_text(htd.SOURCES), before)

    def test_raw_text(self):
        # §5.1：テキストを置く資料には -raw のテキストも置き、image には置かない
        pdfs = {"yosan-r8-gaiyou": make_pdf([LINE] * 3), "yosan-r8-hoshin": make_pdf([LINE] + [""] * 5)}
        p = self.request([req(i) for i in pdfs])
        htd.fetch(p, opener=lambda url: pdfs[url.rsplit("/", 1)[1][:-4]])
        rp = os.path.join(htd.TEXT_RAW_DIR, "yosan-r8-gaiyou.txt")
        raw = read_text(rp)
        self.assertEqual(raw.count("\f"), 3)                                             # 改ページを残す
        self.assertIn("town budget", raw)
        self.assertFalse(os.path.exists(os.path.join(htd.TEXT_RAW_DIR, "yosan-r8-hoshin.txt")))
        before = read_text(htd.SOURCES)
        os.remove(rp)
        self.assertEqual([r["id"] for r in htd.raw()], ["yosan-r8-gaiyou"])             # raw は作り直すだけ
        self.assertEqual(read_text(rp), raw)
        self.assertEqual(read_text(htd.SOURCES), before)                                 # 台帳は変えない
        htd.extract()
        self.assertEqual(read_text(rp), raw)
        with open(os.path.join(htd.RAW_DIR, "yosan-r8-gaiyou.pdf"), "wb") as f:
            f.write(make_pdf([LINE]))
        with self.assertRaises(htd.Stop) as cm:
            htd.raw(["yosan-r8-gaiyou"])
        self.assertIn("sha256 が台帳と違う", str(cm.exception))
        with self.assertRaises(htd.Stop):
            htd.raw(["no-such-id"])

    def test_non_pdf(self):
        p = self.request([req("zaisei-r6-zaimu", url="https://www.town.shiga-hino.lg.jp/cmsfiles/a.docx")])
        htd.fetch(p, opener=lambda url: b"PK\x03\x04dummy")
        r = htd.read_sources()[0]
        self.assertEqual((r["format"], r["check"], r["text_method"], r["pages"]), ("docx", "notext", "none", ""))

    def test_broken_xlsx_is_ng(self):
        p = self.request([req("zaisei-r6-zaimu", url="https://www.town.shiga-hino.lg.jp/cmsfiles/a.xlsx")])
        htd.fetch(p, opener=lambda url: b"PK\x03\x04dummy")
        r = htd.read_sources()[0]
        self.assertEqual((r["format"], r["check"], r["text_method"]), ("xlsx", "ng", "openpyxl"))
        self.assertFalse(os.path.exists(os.path.join(htd.TEXT_DIR, "zaisei-r6-zaimu")))

    def test_new_categories(self):
        htd.check_request([req("kaiken-20260225-shiryo-03", date="2026-02-25"), req("hyoka-r6-senryaku-kensho"),
                           req("chiiki-jichikai-chosa"), req("tokei-r8-jidoseito"),
                           req("shingi-sogokyoiku-gijiroku-r6-01")], set())


def make_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "歳出"
    ws["A1"] = "区分"
    ws["B1"] = "金額"
    ws["A2"] = "議会費\n（款）"
    ws["B2"] = 1234.0
    ws["C2"] = 0.125
    ws["B4"] = "=B2*2"           # 計算結果が保存されていない数式
    ws["D5"] = dt.datetime(2026, 3, 31)
    ws["E5"] = True
    ws.merge_cells("A6:B6")
    ws["A6"] = "合計"
    h = wb.create_sheet("メモ")
    h.sheet_state = "hidden"
    h["A1"] = "隠れたシート"
    wb.save(path)


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl が無い")
class TestXlsx(Tmp):
    def test_fetch_xlsx(self):
        src = os.path.join(self.d, "src.xlsx")
        make_xlsx(src)
        with open(src, "rb") as f:
            data = f.read()
        p = self.request([req("zaisei-r6-shiryoshu", url="https://www.town.shiga-hino.lg.jp/cmsfiles/a.xlsx")])
        htd.fetch(p, opener=lambda url: data)
        r = htd.read_sources()[0]
        self.assertEqual((r["format"], r["check"], r["text_method"], r["pages"]), ("xlsx", "ok", "openpyxl", "2"))
        d = os.path.join(htd.TEXT_DIR, "zaisei-r6-shiryoshu")
        self.assertEqual(sorted(os.listdir(d)), ["01.tsv", "02.tsv", "sheets.tsv"])
        rows = read_text(os.path.join(d, "01.tsv")).split("\n")
        self.assertEqual(rows[0], "区分\t金額")
        self.assertEqual(rows[1], "議会費 （款）\t1234\t0.125")   # 改行は空白1つ。整数は小数点を付けない
        self.assertEqual(rows[2], "")                                # 値の無い行も残す（行の番号をそろえる）
        self.assertEqual(rows[3], "")                                # 計算結果の無い数式は空
        self.assertEqual(rows[4], "\t\t\t2026-03-31\tTRUE")
        self.assertEqual(rows[5], "合計")                            # 結合したセルは左上だけ
        self.assertEqual(len(rows), 7)                               # 6行と末尾の改行
        idx = read_text(os.path.join(d, "sheets.tsv")).splitlines()
        self.assertEqual(idx[1], "01\t歳出\tvisible\t6\t5\t1\t0")
        self.assertEqual(idx[2], "02\tメモ\thidden\t1\t1\t0\t0")
        self.assertFalse(os.path.exists(os.path.join(htd.TEXT_DIR, "zaisei-r6-shiryoshu.txt")))
        self.assertFalse(os.path.exists(os.path.join(htd.TEXT_RAW_DIR, "zaisei-r6-shiryoshu.txt")))
        # extract でやり直しても同じ
        htd.extract(["zaisei-r6-shiryoshu"])
        self.assertEqual(read_text(os.path.join(d, "01.tsv")).split("\n")[1], "議会費 （款）\t1234\t0.125")


def make_text_pdf(lines):
    """1ページに、渡した行（ASCII）を上から順に置いた PDF。"""
    content = "BT /F1 10 Tf 20 700 Td 12 TL " + " ".join(f"({x}) Tj T*" for x in lines) + " ET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
            f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"]
    out, offs = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode("latin-1")
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return out


@unittest.skipUnless(HAS_POPPLER, "pdftotext が無い")
class TestHyphen(Tmp):
    def test_restore(self):
        # 既定の取り出しは「ABCDEF」「5,927,077,723」「17,974,743,700」になる（「-」が消える）
        lines = ["ABC-", "DEF", "-", "5,927,077,723", "-", "-", "17,974,743,700", "Tel 0748-", "52-6550"]
        pdf = make_text_pdf(lines)
        p = self.request([req("zaisei-r6-zaimu")])
        htd.fetch(p, opener=lambda url: pdf)
        r = htd.read_sources()[0]
        t = read_text(os.path.join(htd.TEXT_DIR, "zaisei-r6-zaimu.txt"))
        self.assertEqual(t.split("\f")[0].strip().split("\n"), lines)
        self.assertEqual(r["text_method"], "pdftotext+hyphen")
        self.assertIn("行の終わりの「-」を戻した：5", r["note"])
        # extract でやり直しても、注記は重ならない
        htd.extract(["zaisei-r6-zaimu"])
        self.assertEqual(htd.read_sources()[0]["note"].count("行の終わりの「-」を戻した"), 1)

    def test_no_hyphen_no_note(self):
        p = self.request([req("yosan-r8-gaiyou")])
        htd.fetch(p, opener=lambda url: make_pdf([LINE]))
        r = htd.read_sources()[0]
        self.assertEqual(r["text_method"], "pdftotext+hyphen")
        self.assertNotIn("戻した", r["note"])

    def test_restore_page_mismatch(self):
        self.assertIsNone(htd.restore_page("ABCDEF\n", [("ABC-", True), ("DEX", False)]))
        self.assertEqual(htd.restore_page("ABC\n", [("ABC", False)]), "ABC\n")


DRAWING = """<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">
<xdr:twoCellAnchor><xdr:from><xdr:col>3</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>9</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
<xdr:to><xdr:col>5</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>12</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
<xdr:sp><xdr:txBody><a:p><a:r><a:t>人口</a:t></a:r></a:p><a:p><a:r><a:t>22,074</a:t></a:r></a:p></xdr:txBody></xdr:sp>
<xdr:clientData/></xdr:twoCellAnchor>
<xdr:twoCellAnchor><xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>1</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>
<xdr:to><xdr:col>2</xdr:col><xdr:colOff>0</xdr:colOff><xdr:row>2</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>
<xdr:grpSp><xdr:sp><xdr:txBody><a:p><a:r><a:t>題</a:t></a:r></a:p></xdr:txBody></xdr:sp>
<mc:AlternateContent><mc:Choice Requires="a14"><xdr:sp><xdr:txBody><a:p><a:r><a:t>選ばれる</a:t></a:r></a:p></xdr:txBody></xdr:sp></mc:Choice>
<mc:Fallback><xdr:sp><xdr:txBody><a:p><a:r><a:t>代わり</a:t></a:r></a:p></xdr:txBody></xdr:sp></mc:Fallback></mc:AlternateContent>
<xdr:sp><xdr:txBody><a:p/></xdr:txBody></xdr:sp></xdr:grpSp>
<xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>"""


def add_drawing(path, drawing_xml):
    """openpyxl で作った xlsx の1つめのシートに、図形の部品を足す（openpyxl は図形を書けないため）。"""
    import zipfile as zf
    tmp = path + ".tmp"
    with zf.ZipFile(path) as zin, zf.ZipFile(tmp, "w") as zout:
        names = zin.namelist()
        for n in names:
            data = zin.read(n)
            if n == "xl/worksheets/sheet1.xml":
                data = data.replace(b"</worksheet>", b'<drawing xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rIdZ1"/></worksheet>')
            if n == "[Content_Types].xml":
                data = data.replace(b"</Types>", b'<Override PartName="/xl/drawings/drawing99.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>')
            zout.writestr(n, data)
        rels = "xl/worksheets/_rels/sheet1.xml.rels"
        body = b'<Relationship Id="rIdZ1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing99.xml"/>'
        assert rels not in names   # この見本では、1つめのシートに関係ファイルは無い
        zout.writestr(rels, b'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + body + b"</Relationships>")
        zout.writestr("xl/drawings/drawing99.xml", drawing_xml.encode("utf-8"))
    os.replace(tmp, path)


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl が無い")
class TestZukei(Tmp):
    def test_shapes_and_chart(self):
        import openpyxl
        from openpyxl.chart import BarChart, Reference
        src = os.path.join(self.d, "z.xlsx")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["年度", "金額"])
        for y, v in (("H26", 100), ("H27", 120)):
            ws.append([y, v])
        ch = wb.create_sheet("グラフ")
        bar = BarChart()
        bar.title = "歳出の推移"
        bar.y_axis.title = "千円"
        bar.add_data(Reference(ws, min_col=2, min_row=1, max_row=3), titles_from_data=True)
        bar.set_categories(Reference(ws, min_col=1, min_row=2, max_row=3))
        ch.add_chart(bar, "C5")
        wb.save(src)
        add_drawing(src, DRAWING)
        d = htd.xlsx_drawings(src)
        self.assertEqual(d[0], [(2, 1, "図形", "", "", "題"), (2, 1, "図形", "", "", "選ばれる"),
                                (10, 4, "図形", "", "", "人口 22,074")])
        kinds = [r[2] for r in d[1]]
        self.assertEqual(kinds[0], "グラフの題")
        self.assertIn("軸の題", kinds)
        self.assertEqual(d[1][0][:2], (5, 3))
        # 値は保存されたときのもの（openpyxl はグラフに値を保存しないので、この見本では値の行は無い）
        with open(src, "rb") as f:
            data = f.read()
        p = self.request([req("zaisei-r6-shiryoshu", url="https://www.town.shiga-hino.lg.jp/cmsfiles/a.xlsx")])
        htd.fetch(p, opener=lambda url: data)
        xd = os.path.join(htd.TEXT_DIR, "zaisei-r6-shiryoshu")
        self.assertEqual(sorted(os.listdir(xd)), ["01-zukei.tsv", "01.tsv", "02-zukei.tsv", "02.tsv", "sheets.tsv"])
        z = read_text(os.path.join(xd, "01-zukei.tsv")).splitlines()
        self.assertEqual(z[0], "行\t列\t種類\t系列\t項目\t文字")
        self.assertEqual(z[3], "10\t4\t図形\t\t\t人口 22,074")
        idx = read_text(os.path.join(xd, "sheets.tsv")).splitlines()
        self.assertTrue(idx[0].endswith("\t図形とグラフの字の行の数"))
        self.assertTrue(idx[1].endswith("\t3"))

    def test_chart_rows(self):
        xml = """<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><c:chart>
<c:title><c:tx><c:rich><a:p><a:r><a:t>人件費</a:t></a:r></a:p></c:rich></c:tx></c:title>
<c:plotArea><c:barChart><c:ser><c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>当該団体(円)</c:v></c:pt></c:strCache></c:strRef></c:tx>
<c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v> H26</c:v></c:pt><c:pt idx="1"><c:v> H27</c:v></c:pt></c:strCache></c:strRef></c:cat>
<c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>60780</c:v></c:pt><c:pt idx="1"><c:v>55487</c:v></c:pt></c:numCache></c:numRef></c:val>
</c:ser></c:barChart><c:valAx><c:title><c:tx><c:rich><a:p><a:r><a:t>（円）</a:t></a:r></a:p></c:rich></c:tx></c:title></c:valAx></c:plotArea></c:chart></c:chartSpace>"""
        self.assertEqual(htd.chart_rows(xml.encode("utf-8")), [("グラフの題", "", "", "人件費"), ("軸の題", "", "", "（円）"),
                                               ("グラフの値", "当該団体(円)", " H26", "60780"),
                                               ("グラフの値", "当該団体(円)", " H27", "55487")])


PAGE = """<html><head><title>x</title></head><body><div id="pankuzu"><a href="/">ホーム</a></div>
<time itemprop="datePublished" datetime="2025-09-16">x</time><time itemprop="dateModified" datetime="2025-09-17">x</time>
<div class="mol_contents"><div class="mol_textblock"><h2>【報告】自治会活動に関する意見交換会</h2>
<p>人口減少や少子高齢化が進む今、これまでの自治会活動を維持していくのが難しくなってきています。<br>安心して住み続けられる地域をつくるために、意見交換会を開催しました。</p>
<script>var x = 1;</script><table><tr><th>回</th><th>日</th></tr><tr><td>第1回</td><td>9月6日</td></tr></table>
<ul><li><a href="/a.pdf">報告資料 (PDF形式、1.2MB)</a></li></ul></div></div>
<div class="kiji_aside">このページは役に立ちましたか？</div></body></html>"""


class TestHtml(Tmp):
    def test_fetch_page(self):
        url = "https://www.town.shiga-hino.lg.jp/0000008381.html"
        p = self.request([req("chiiki-jichikai-page", url=url, page_url=url, fiscal_year="2025")])
        htd.fetch(p, opener=lambda u: PAGE.encode("utf-8"))
        r = htd.read_sources()[0]
        self.assertEqual((r["format"], r["check"], r["text_method"], r["pages"], r["date"]),
                         ("html", "ok", "html", "", "2025-09-16"))
        self.assertEqual(r["note"], "ページの更新日 2025-09-17")
        t = read_text(os.path.join(htd.TEXT_DIR, "chiiki-jichikai-page.txt"))
        self.assertTrue(t.startswith("【報告】自治会活動に関する意見交換会\n人口減少"))
        self.assertIn("難しくなってきています。\n安心して", t)       # <br> で改行
        self.assertIn("回\t日\n第1回\t9月6日", t)                     # 表のセルはタブ
        self.assertIn("報告資料 (PDF形式、1.2MB)", t)                  # リンクは文字だけ
        self.assertNotIn("var x", t)
        self.assertNotIn("ホーム", t)
        self.assertNotIn("役に立ちましたか", t)
        self.assertNotIn("\n\n", t)
        self.assertFalse(os.path.exists(os.path.join(htd.TEXT_RAW_DIR, "chiiki-jichikai-page.txt")))
        # extract でやり直しても、更新日の注記は重ならない
        htd.extract(["chiiki-jichikai-page"])
        self.assertEqual(htd.read_sources()[0]["note"], "ページの更新日 2025-09-17")

    def test_no_body_is_ng(self):
        url = "https://www.town.shiga-hino.lg.jp/0000000001.html"
        p = self.request([req("kaiken-20260225-page", url=url, page_url=url)])
        htd.fetch(p, opener=lambda u: b"<html><body><div id='other'>hello</div></body></html>")
        r = htd.read_sources()[0]
        self.assertEqual((r["check"], r["text_method"]), ("ng", "html"))
        self.assertFalse(os.path.exists(os.path.join(htd.TEXT_DIR, "kaiken-20260225-page.txt")))


@unittest.skipUnless(HAS_POPPLER and HAS_GIT, "pdftoppm・git が無い")
class TestOcr(Tmp):
    def setUp(self):
        super().setUp()
        nd = os.path.join(self.d, "ndlocr")
        os.makedirs(os.path.join(nd, "src"))
        with open(os.path.join(nd, "src", "ocr.py"), "w", encoding="utf-8") as f:
            f.write(FAKE_OCR)
        g = ["git", "-C", nd, "-c", "user.name=t", "-c", "user.email=t@example.com"]
        for cmd in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "fake"]):
            subprocess.run(g + cmd, check=True, capture_output=True)
        htd.NDLOCR_DIR, htd.NDLOCR_PYTHON = nd, sys.executable
        p = self.request([req("yosan-r8-gaiyou"), req("zaisei-r6-zaimu", url="https://www.town.shiga-hino.lg.jp/cmsfiles/a.xlsx")])
        htd.fetch(p, opener=lambda url: make_pdf([LINE, "", LINE]) if url.endswith(".pdf") else b"PK\x03\x04dummy")

    def test_ocr_writes_pages_in_order(self):
        os.environ["FAKE_EMPTY"] = "p0002"
        done, method = htd.ocr(today=date(2026, 10, 22), log=lambda s: None)
        self.assertEqual([r["id"] for r in done], ["yosan-r8-gaiyou"])            # PDF だけ
        self.assertRegex(method, r"^ndlocr-lite@[0-9a-f]{7}$")
        text = read_text(os.path.join(htd.OCR_DIR, "yosan-r8-gaiyou.txt"))
        self.assertEqual(text, "読んだ p0001\f\f読んだ p0003\f")                  # ページの順。字の無いページは空
        rows = {r["id"]: r for r in htd.read_sources()}
        self.assertEqual((rows["yosan-r8-gaiyou"]["ocr_method"], rows["yosan-r8-gaiyou"]["ocr_date"]), (method, "2026-10-22"))
        self.assertEqual(rows["zaisei-r6-zaimu"]["ocr_method"], "")
        self.assertFalse(os.path.exists(os.path.join(htd.OCR_RAW_DIR, "yosan-r8-gaiyou", "img")))   # 画像は消す
        self.assertTrue(os.path.exists(os.path.join(htd.OCR_RAW_DIR, "yosan-r8-gaiyou", "out", "p0001.json")))
        # もう一度動かすと、済んだものは読まない
        done, _ = htd.ocr(log=lambda s: None)
        self.assertEqual(done, [])

    def test_ocr_stops_when_output_missing(self):
        os.environ["FAKE_SKIP"] = "p0003"
        with self.assertRaises(htd.Stop) as cm:
            htd.ocr(log=lambda s: None)
        self.assertIn("3ページの NDLOCR-Lite の出力が無い", str(cm.exception))
        self.assertEqual(htd.read_sources()[0]["ocr_method"], "")
        self.assertFalse(os.path.exists(os.path.join(htd.OCR_DIR, "yosan-r8-gaiyou.txt")))

    def test_ocr_stops_when_ndlocr_changed(self):
        with open(os.path.join(htd.NDLOCR_DIR, "src", "ocr.py"), "a", encoding="utf-8") as f:
            f.write("# 書き足し\n")
        with self.assertRaises(htd.Stop) as cm:
            htd.ocr(log=lambda s: None)
        self.assertIn("作業ツリーに変更がある", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
