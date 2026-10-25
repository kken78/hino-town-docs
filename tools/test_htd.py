"""tools/htd.py の検査（DOCS_SCHEMA.md §2・§4〜§6）。ネットワークは使わない。

実行: python3 -B -m unittest discover -s tools
見本の PDF はこの検査の中で作る（pdftotext・pdfinfo が要る。無ければ PDF の検査は飛ばす）。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402

HAS_POPPLER = bool(shutil.which("pdftotext") and shutil.which("pdfinfo") and shutil.which("pdftoppm"))
HAS_GIT = bool(shutil.which("git"))

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
                      htd.NDLOCR_DIR, htd.NDLOCR_PYTHON)
        htd.SOURCES = os.path.join(self.d, "data", "sources.tsv")
        htd.TEXT_DIR = os.path.join(self.d, "data", "text")
        htd.RAW_DIR = os.path.join(self.d, "sources_raw")
        htd.OCR_DIR = os.path.join(self.d, "data", "ocr")
        htd.OCR_RAW_DIR = os.path.join(self.d, "ocr_raw")
        htd.WAIT = 0

    def tearDown(self):
        (htd.SOURCES, htd.TEXT_DIR, htd.RAW_DIR, htd.WAIT, htd.OCR_DIR, htd.OCR_RAW_DIR,
         htd.NDLOCR_DIR, htd.NDLOCR_PYTHON) = self.saved
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
        self.assertEqual(len(htd.COLUMNS), 18)


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

    def test_non_pdf(self):
        p = self.request([req("zaisei-r6-zaimu", url="https://www.town.shiga-hino.lg.jp/cmsfiles/a.xlsx")])
        htd.fetch(p, opener=lambda url: b"PK\x03\x04dummy")
        r = htd.read_sources()[0]
        self.assertEqual((r["format"], r["check"], r["text_method"], r["pages"]), ("xlsx", "notext", "none", ""))


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
