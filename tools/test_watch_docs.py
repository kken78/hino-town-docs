"""tools/watch_docs.py の検査（DOCS_SCHEMA.md §12）。ネットワークは使わない（偽の町のサイトをたどる）。

実行: python3 -B -m unittest discover -s tools
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import watch_docs as w  # noqa: E402

S = w.SITE


def cat(code):
    return f"{S}category/{code}.html"


def page_html(title, crumb, files, updated="2026-11-01"):
    links = "".join(f'<a href="{u}">{n}</a>' for u, n in files)
    trail = "".join(f'<a href="/x{i}">{c}</a>' for i, c in enumerate(["ホーム"] + crumb))
    return (f"<html><head><title>{title} | 日野町</title></head><body><div class=\"topicpath\">{trail}</div>"
            f'<time itemprop="datePublished" datetime="2020-01-01">x</time>'
            f'<time itemprop="dateModified" datetime="{updated}">x</time>{links}</body></html>')


def fake_site(extra=None, missing=()):
    """トップの分類8つ。27 の下に分類 27-1 があり、ページ 0000000001（PDF 2本）と 0000000002（本文だけ）が載る。"""
    site = {}
    for n in range(27, 35):
        site[cat(f"{n}-0-0-0-0-0-0-0-0-0")] = f"<title>分類{n} | 日野町</title>"
    site[cat("27-0-0-0-0-0-0-0-0-0")] = '<title>くらし | 日野町</title><a href="/category/27-1-0-0-0-0-0-0-0-0.html">防災</a>'
    site[cat("27-1-0-0-0-0-0-0-0-0")] = '<title>防災 | 日野町</title><a href="/0000000001.html">a</a><a href="/0000000002.html">b</a>'
    site[f"{S}0000000001.html"] = page_html("避難所の一覧", ["くらし", "防災"], [
        ("/cmsfiles/contents/a/hinanjo.pdf?v=2", "指定避難所の一覧(.pdf サイズ：1.2MB)"),
        ("/cmsfiles/contents/a/map.pdf", "地図 (PDF形式、3.4MB)"),
        ("https://example.com/other.pdf", "外のサイト")])
    site[f"{S}0000000002.html"] = page_html("記者会見", ["町政情報", "町長室"], [])
    site.update(extra or {})
    for u in missing:
        site.pop(u, None)

    def fetch(url, method="GET"):
        return (200, site[url]) if url in site else (404, "")
    return fetch


def crawl(fetch):
    st = w.new_state()
    assert w.crawl(st, fetch=fetch)
    return st


class TestCrawl(unittest.TestCase):
    def test_tables(self):
        st = crawl(fake_site())
        self.assertEqual(w.check_crawl(st), [])
        docs, pages = w.tables(st)
        self.assertEqual([d["資料の URL"] for d in docs],
                         [f"{S}cmsfiles/contents/a/hinanjo.pdf", f"{S}cmsfiles/contents/a/map.pdf"])  # 外のサイトは入れない。? は落とす
        self.assertEqual(docs[0]["資料の名前"], "指定避難所の一覧")
        self.assertEqual(docs[0]["大きさ"], "1.2MB")
        self.assertEqual(docs[1]["大きさ"], "3.4MB")
        self.assertEqual(docs[0]["起点"], "くらし > 防災")
        self.assertEqual(docs[0]["年度または日付"], "2026-11-01")
        self.assertEqual([p["資料の数"] for p in pages], ["2", "0"])
        self.assertEqual(pages[1]["ページの名前"], "記者会見")

    def test_resume(self):
        st = w.new_state()
        self.assertFalse(w.crawl(st, fetch=fake_site(), seconds=0))
        self.assertTrue(w.crawl(st, fetch=fake_site()))
        self.assertEqual(len(st["pages_done"]), 2)

    def test_fail_top(self):
        st = crawl(fake_site(missing=[cat("30-0-0-0-0-0-0-0-0-0")]))
        self.assertTrue(any("トップの分類" in b for b in w.check_crawl(st)))

    def test_fail_pages(self):
        st = crawl(fake_site(missing=[f"{S}0000000002.html"]))  # 2件のうち1件 = 50%
        self.assertTrue(any("取れないページ" in b for b in w.check_crawl(st)))


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.docs, self.pages = w.tables(crawl(fake_site()))

    def src(self, i, url, page, note=""):
        return {"id": i, "title": i, "url": url, "page_url": page, "note": note}

    def test_nothing_new(self):
        f = w.compare(self.docs, self.pages, self.docs, self.pages, [], [])
        self.assertEqual(f, {"new": [], "gone": [], "updated": []})

    def test_new_doc_and_page(self):
        f = w.compare(self.docs, self.pages, self.docs[:1], self.pages[:1], [], [])
        self.assertEqual([(x["kind"], x["url"]) for x in f["new"]],
                         [("資料", f"{S}cmsfiles/contents/a/map.pdf"), ("ページ", f"{S}0000000002.html")])

    def test_ledger_counts_as_known(self):
        s = [self.src("bosai-a", f"{S}cmsfiles/contents/a/map.pdf", f"{S}0000000001.html")]
        f = w.compare(self.docs, self.pages, self.docs[:1], self.pages, s, [])
        self.assertEqual(f["new"], [])

    def test_ignore(self):
        # 前の一覧が空なら、資料2本とページ2件の4件が新しい
        self.assertEqual(len(w.compare(self.docs, self.pages, [], [], [], [])["new"]), 4)
        for kind, value, left in [("origin", "くらし > 防", 1), ("page", f"{S}0000000001.html", 1),
                                  ("url", f"{S}cmsfiles/contents/a/m", 3), ("name", "^地図$", 3), ("name", "記者", 3)]:
            f = w.compare(self.docs, self.pages, [], [], [], [{"kind": kind, "value": value, "note": "試し"}])
            self.assertEqual(len(f["new"]), left, (kind, value))

    def test_gone(self):
        s = [self.src("a", f"{S}cmsfiles/contents/a/old.pdf", f"{S}0000000001.html"),
             self.src("b", f"{S}cmsfiles/contents/a/arch.pdf", f"{S}0000000009.html", "取得元：Webアーカイブの保存版（…）"),
             self.src("c", f"{S}cmsfiles/contents/a/moved.pdf", f"{S}0000000001.html")]
        f = w.compare(self.docs, self.pages, self.docs, self.pages, s, [], head=lambda u: 404 if "old" in u else 200)
        self.assertEqual([x["id"] for x in f["gone"]], ["a", "c"])
        self.assertTrue(f["gone"][0]["status"].startswith("町のサイトから無くなった"))
        self.assertTrue(f["gone"][1]["status"].startswith("掲載ページから外れた"))

    def test_updated(self):
        prev = [dict(p) for p in self.pages]
        prev[0]["更新日"] = "2026-10-01"
        s = [self.src("a", f"{S}cmsfiles/contents/a/hinanjo.pdf", f"{S}0000000001.html")]
        f = w.compare(self.docs, self.pages, self.docs, prev, s, [])
        self.assertEqual([(x["was"], x["now"], x["ids"]) for x in f["updated"]], [("2026-10-01", "2026-11-01", ["a"])])
        f = w.compare(self.docs, self.pages, self.docs, self.pages[1:], s, [])  # 前の一覧に無いページはくらべない
        self.assertEqual(f["updated"], [])


class TestIgnoreFile(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "watch_ignore.tsv")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_ok(self):
        p = self.write("kind\tvalue\tnote\norigin\tくらし > 申請書ダウンロード\t様式\n")
        self.assertEqual(len(w.load_ignore(p)), 1)

    def test_errors(self):
        p = self.write("kind\tvalue\tnote\nfoo\tx\ty\nname\t(\t正規表現\norigin\tくらし\t\n")
        with self.assertRaises(w.Fail) as e:
            w.load_ignore(p)
        self.assertIn("3 件", str(e.exception))

    def test_main_end_to_end(self):
        d = tempfile.mkdtemp()
        inv = os.path.join(d, "inventory")
        os.makedirs(inv)
        src = os.path.join(d, "sources.tsv")
        with open(src, "w", encoding="utf-8") as f:
            f.write("id\ttitle\turl\tpage_url\tnote\n")
        ign = self.write("kind\tvalue\tnote\n")
        orig = w.request
        w.request = fake_site()
        try:
            code = w.main(["--out", os.path.join(d, "out"), "--inventory", inv, "--sources", src, "--ignore", ign,
                           "--date", "2026-12-01", "--json", os.path.join(d, "r.json")])
        finally:
            w.request = orig
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(d, "out", "town-docs-inventory-2026-12-01.tsv")))
        self.assertEqual(len(w.read_tsv(os.path.join(d, "out", "town-pages-2026-12-01.tsv"))), 2)
        self.assertFalse(__import__("json").load(open(os.path.join(d, "r.json"), encoding="utf-8"))["same_day"])
        # 同じ日の一覧が data/inventory/ にすでにあるとき
        import shutil
        shutil.copy(os.path.join(d, "out", "town-pages-2026-12-01.tsv"), inv)
        w.request = fake_site()
        try:
            code = w.main(["--out", os.path.join(d, "out"), "--inventory", inv, "--sources", src, "--ignore", ign,
                           "--date", "2026-12-01", "--json", os.path.join(d, "r.json")])
        finally:
            w.request = orig
        self.assertEqual(code, 0)
        self.assertTrue(__import__("json").load(open(os.path.join(d, "r.json"), encoding="utf-8"))["same_day"])


if __name__ == "__main__":
    unittest.main()
