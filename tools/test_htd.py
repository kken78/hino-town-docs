"""tools/htd.py の検査（DOCS_SCHEMA.md §2・§4〜§6）。ネットワークは使わない。

実行: python3 -B -m unittest discover -s tools
見本の PDF はこの検査の中で作る（pdftotext・pdfinfo が要る。無ければ PDF の検査は飛ばす）。
"""
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402

HAS_POPPLER = bool(shutil.which("pdftotext") and shutil.which("pdfinfo"))
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
        self.saved = (htd.SOURCES, htd.TEXT_DIR, htd.RAW_DIR, htd.WAIT)
        htd.SOURCES = os.path.join(self.d, "data", "sources.tsv")
        htd.TEXT_DIR = os.path.join(self.d, "data", "text")
        htd.RAW_DIR = os.path.join(self.d, "sources_raw")
        htd.WAIT = 0

    def tearDown(self):
        htd.SOURCES, htd.TEXT_DIR, htd.RAW_DIR, htd.WAIT = self.saved
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

    def test_non_pdf(self):
        p = self.request([req("zaisei-r6-zaimu", url="https://www.town.shiga-hino.lg.jp/cmsfiles/a.xlsx")])
        htd.fetch(p, opener=lambda url: b"PK\x03\x04dummy")
        r = htd.read_sources()[0]
        self.assertEqual((r["format"], r["check"], r["text_method"], r["pages"]), ("xlsx", "notext", "none", ""))


if __name__ == "__main__":
    unittest.main()
