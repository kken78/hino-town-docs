"""tools/watch_archive.py の検査（DOCS_SCHEMA.md §14）。ネットワークは使わない（偽の Webアーカイブに問い合わせる）。

実行: python3 -B -m unittest discover -s tools
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import watch_archive as w  # noqa: E402

T = "https://www.town.shiga-hino.lg.jp/cmsfiles/contents/0000000/"


def src(id_, url, fetched="2026-10-21", removed=""):
    return {"id": id_, "url": url, "fetched": fetched, "removed": removed}


def plan(id_, file, url, fetched=""):
    return {"id": id_, "file": file, "url": url, "fetched": fetched}


class FakeArchive:
    """cdx：{url: 保存日時の並び}。save_code：{url: HTTP の状態}（既定 200）。"""

    def __init__(self, cdx=None, save_code=None, cdx_fail=()):
        self.cdx, self.save_code, self.cdx_fail = cdx or {}, save_code or {}, set(cdx_fail)
        self.saved, self.asked = [], []

    def get(self, url, timeout):
        self.asked.append(url)
        if url.startswith(w.CDX):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            u = q["url"][0]
            if u in self.cdx_fail:
                return 503, "", {}
            ts = self.cdx.get(u, [])
            if not ts:
                return 200, "", {}
            return 200, json.dumps([["timestamp", "statuscode"]] + [[t, "200"] for t in ts]), {}
        if url.startswith(w.SAVE):
            u = url[len(w.SAVE):]
            self.saved.append(u)
            code = self.save_code.get(u, 200)
            return code, "", ({"Content-Location": f"/web/20261201000000/{u}"} if code == 200 else {})
        raise AssertionError(url)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run(sources, plans=(), prev=(), fake=None, **kw):
    c = Clock()
    fake = fake or FakeArchive()
    rows, errs = w.run(list(sources), list(plans), list(prev), "2026-12-01", get=fake.get, clock=c, sleep=c.sleep, **kw)
    return {r["url"]: r for r in rows}, errs, fake, c


class TestTargets(unittest.TestCase):
    def test_merge_and_since(self):
        t = w.targets([src("yosan-r8-fuhyo", T + "a.pdf", "2026-10-21"), src("x", T + "b.pdf", "2026-11-05")],
                      [plan("fuhyo-r8", "r8.pdf", T + "a.pdf", "2026-10-10"), plan("kodomo", "honpen.pdf", T + "c.pdf"),
                       plan("kanbatsu", "", "")])
        self.assertEqual(sorted(t), [T + "a.pdf", T + "b.pdf", T + "c.pdf"])            # file の無い行は入れない
        self.assertEqual(t[T + "a.pdf"]["src"], ["yosan-r8-fuhyo", "plans:fuhyo-r8/r8.pdf"])  # 同じ URL は1つ
        self.assertEqual(t[T + "a.pdf"]["since"], "2026-10-21")                             # 遅いほう
        self.assertEqual(t[T + "c.pdf"]["since"], "")                                       # 取得日が空なら境なし

    def test_removed_rows_are_not_targets(self):
        t = w.targets([src("jigyo-x", T + "gone.pdf", removed="2026-10-23")], [plan("youho", "old.pdf", T + "gone.pdf")])
        self.assertEqual(t, {})


class TestRun(unittest.TestCase):
    def test_ok_needs_snapshot_after_fetched(self):
        fake = FakeArchive(cdx={T + "new.pdf": ["20251001000000", "20261021090000"], T + "old.pdf": ["20250301000000"],
                                T + "plan.pdf": ["20200101000000"]})
        rows, _, fake, _ = run([src("a", T + "new.pdf"), src("b", T + "old.pdf"), src("c", T + "none.pdf")],
                               [plan("p", "h.pdf", T + "plan.pdf")], fake=fake)
        self.assertEqual(rows[T + "new.pdf"]["status"], "ok")            # 取得日と同じ日の保存版でよい
        self.assertEqual(rows[T + "new.pdf"]["latest"], "20261021090000")
        self.assertEqual(rows[T + "plan.pdf"]["status"], "ok")           # 境なしは古い保存版でよい
        self.assertEqual(rows[T + "old.pdf"]["status"], "requested")     # 取得日より前の保存版しか無い
        self.assertEqual(rows[T + "none.pdf"]["status"], "requested")
        self.assertEqual(sorted(fake.saved), [T + "none.pdf", T + "old.pdf"])
        self.assertIn("20261201000000", rows[T + "none.pdf"]["note"])

    def test_pacing(self):
        _, _, fake, c = run([src(str(i), T + f"{i}.pdf") for i in range(3)])
        # 一覧3件は1秒おき、保存3件は20秒おき。最初の1件は待たない
        self.assertGreaterEqual(c.t, 2 * w.CDX_INTERVAL + 2 * w.SAVE_INTERVAL)

    def test_seconds_limit_moves_rest_to_pending(self):
        rows, _, fake, _ = run([src(str(i), T + f"{i}.pdf") for i in range(5)], seconds=30)
        st = [rows[T + f"{i}.pdf"]["status"] for i in range(5)]
        self.assertEqual(st.count("requested"), 2)                       # 0秒と20秒に頼み、40秒で上限を超える
        self.assertEqual(st.count("pending"), 3)
        self.assertIn("時間の上限", rows[T + "4.pdf"]["note"])

    def test_throttled_stops_requests(self):
        fake = FakeArchive(save_code={T + "1.pdf": 429})
        rows, _, fake, _ = run([src(str(i), T + f"{i}.pdf") for i in range(4)], fake=fake)
        self.assertEqual(fake.saved, [T + "0.pdf", T + "1.pdf"])          # 断られたら残りは頼まない
        self.assertEqual([rows[T + f"{i}.pdf"]["status"] for i in range(4)], ["requested", "pending", "pending", "pending"])

    def test_failed_counts_months(self):
        fake = FakeArchive(save_code={T + "big.pdf": 504})
        prev = [{"url": T + "big.pdf", "fails": "2"}, {"url": T + "ok.pdf", "fails": "3"}]
        rows, _, _, _ = run([src("a", T + "big.pdf"), src("b", T + "ok.pdf")], prev=prev, fake=fake)
        self.assertEqual((rows[T + "big.pdf"]["status"], rows[T + "big.pdf"]["fails"]), ("failed", "3"))
        self.assertEqual((rows[T + "ok.pdf"]["status"], rows[T + "ok.pdf"]["fails"]), ("requested", "0"))

    def test_dry_run_does_not_save(self):
        rows, _, fake, _ = run([src("a", T + "a.pdf")], dry_run=True)
        self.assertEqual(fake.saved, [])
        self.assertEqual(rows[T + "a.pdf"]["status"], "pending")

    def test_cdx_failures(self):
        urls = [T + f"{i}.pdf" for i in range(40)]
        fake = FakeArchive(cdx={u: ["20261201000000"] for u in urls}, cdx_fail=[urls[0]])
        rows, errs, fake, _ = run([src(str(i), u) for i, u in enumerate(urls)], fake=fake)
        self.assertEqual(errs, [urls[0]])                                # 1/40 は5%未満なので続ける
        self.assertEqual(rows[urls[0]]["status"], "pending")
        self.assertEqual(fake.saved, [])                                 # 確かめられないものは頼まない
        fake = FakeArchive(cdx_fail=urls[:2])                            # 2/40 は5%以上
        with self.assertRaises(w.Fail):
            run([src(str(i), u) for i, u in enumerate(urls)], fake=fake)
        self.assertEqual(fake.saved, [])


class TestMain(unittest.TestCase):
    def test_files(self):
        d = tempfile.mkdtemp()
        s = os.path.join(d, "sources.tsv")
        with open(s, "w", encoding="utf-8") as f:
            f.write("id\turl\tfetched\tremoved\na\t" + T + "a.pdf\t2026-10-21\t\n")
        p = os.path.join(d, "plans.tsv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("id\tfile\turl\tfetched\n")
        out = os.path.join(d, "out")
        orig = w.http_get
        fake = FakeArchive()
        w.http_get = fake.get
        try:
            code = w.main(["--sources", s, "--plans", p, "--prev", os.path.join(d, "none.tsv"), "--out", out,
                           "--json", os.path.join(d, "a.json"), "--dry-run", "--date", "2026-12-01"])
        finally:
            w.http_get = orig
        self.assertEqual(code, 0)
        with open(os.path.join(out, "archive.tsv"), encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0].split("\t"), w.COLUMNS)
        self.assertEqual(lines[1].split("\t")[5], "pending")


if __name__ == "__main__":
    unittest.main()
