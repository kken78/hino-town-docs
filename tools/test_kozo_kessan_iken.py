"""tools/kozo_kessan_iken.py の検査（DOCS_SCHEMA.md §13.16・13.10）。ネットワークと PDF は使わない。

実行: python3 -B -m unittest discover -s tools
見本の原典の文字（text と text_raw）は、この検査の中で一時の場所に作る（data/ には入れない）。
見本は、決算審査意見書の字の形をまねて作ったもので、原典の値ではない。
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402
import kozo  # noqa: E402
import kozo_kessan_iken as ki  # noqa: E402

# data/text_raw（書かれた順）。ページの番号の行は、ページの初めに出ることがある
RAW = """令 和 ６ 年 度
日野町各会計決算審査意見書
1
１．一般会計
１． 決算の概要
見本の決算の概要の文である。
9
４．まとめ
以上が令和６年度一般会計の決算審査の概要である。今回の決算審査の実施により、次のと
おり意見を申し上げる。
① 令和６年度は、見本の計画の４年目であり、各種の事務事業が執行されたものと見受け
られる。
② 見本の職員の配置について、適正な人員配置に努められたい。
令和７年度は、見本の計画の５年目である。
10
③ 審査の結果、予算は適正に執行されたと認められる。適正な事務の執行に努められたい。
令和６年度は、見本の経済の状況が回復基調となった。
見本の行財政運営に努められたい。
11
２．国民健康保険特別会計
１． 決算の概要
見本の国民健康保険の決算の文である。
４．まとめ
以上が、令和６年度国民健康保険特別会計の決算審査の概要である。
見本の国民健康保険の財政の基盤強化に努められたい。
３．歳出決算について （単位：円、％）
総務費 163,788 1.5
12
３．西山財産区会計
見本の西山財産区の決算の文である。
13
４．健全化判断比率
見本の健全化判断比率の文である。
以上が、令和６年度の健全化判断比率である。
14
慎重な判断による財政運営に努められたい。
５．資金不足比率
資金不足は生じていなかった。
経営健全化基準は、20％となっている。
15"""

# data/text（既定の取り出し）。番号は行に1字だけで出て、空の行が入る。ページの番号はページの終わり
TEXT = """令 和 ６ 年 度
日野町各会計決算審査意見書

1
\f１．一般会計
１． 決算の概要
見本の決算の概要の文である。

9
\f４．まとめ
以上が令和６年度一般会計の決算審査の概要である。今回の決算審査の実施により、次のと
おり意見を申し上げる。
①

令和６年度は、見本の計画の４年目であり、各種の事務事業が執行されたものと見受け
られる。
②

見本の職員の配置について、適正な人員配置に努められたい。
令和７年度は、見本の計画の５年目である。

10
\f③

審査の結果、予算は適正に執行されたと認められる。適正な事務の執行に努められたい。
令和６年度は、見本の経済の状況が回復基調となった。
見本の行財政運営に努められたい。

11
\f２．国民健康保険特別会計
１． 決算の概要
３．歳出決算について （単位：円、％）
総務費 163,788 1.5
見本の国民健康保険の決算の文である。
４．まとめ
以上が、令和６年度国民健康保険特別会計の決算審査の概要である。
見本の国民健康保険の財政の基盤強化に努められたい。

12
\f３．西山財産区会計
見本の西山財産区の決算の文である。

13
\f４．健全化判断比率
見本の健全化判断比率の文である。
1.3

14
\f以上が、令和６年度の健全化判断比率である。
慎重な判断による財政運営に努められたい。

５．資金不足比率
資金不足は生じていなかった。
経営健全化基準は、20％となっている。

15
"""

DOC = "kessan-r6-ikensho"


def make_sample(root, raw=RAW, text=TEXT):
    for d in ("text", "text_raw", "struct"):
        os.makedirs(os.path.join(root, "data", d), exist_ok=True)
    os.makedirs(os.path.join(root, "data", "struct", "audit"), exist_ok=True)
    with open(os.path.join(root, "data", "text_raw", DOC + ".txt"), "w", encoding="utf-8") as f:
        f.write(raw)
    with open(os.path.join(root, "data", "text", DOC + ".txt"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(root, "data", "sources.tsv"), "w", encoding="utf-8") as f:
        f.write("id\tcategory\ttitle\tcheck\n")
        f.write(f"{DOC}\tkessan\t見本\tok\n")
        f.write("kessan-r6-gaiyou\tkessan\t見本の概要\tok\n")


class Base(unittest.TestCase):
    raw = RAW
    text = TEXT

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-kozo-iken-")
        make_sample(self.tmp, self.raw, self.text)
        ki.kian(self.tmp, out=lambda *a: None)
        self.rows = kozo.load_table(self.tmp, "kessan-iken")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def res(self, rows=None):
        return ki.check(rows if rows is not None else self.rows, ki.Src(self.tmp))

    def get(self, bubun, kaikei, n):
        return next(r for r in self.rows if (r["bubun"], r["kaikei"], r["n"]) == (bubun, kaikei, str(n)))


class TestKian(Base):
    def test_rows(self):
        got = [(r["bubun"], r["kaikei"], r["shurui"], r["bango"]) for r in self.rows]
        self.assertEqual(got, [
            ("matome", "ippan", "maegaki", ""), ("matome", "ippan", "iken", "①"), ("matome", "ippan", "iken", "②"),
            ("matome", "ippan", "iken", "③"), ("matome", "ippan", "musubi", ""),
            ("matome", "kokuho", "zentai", ""),
            ("kenzenka", "nashi", "zentai", ""), ("shikin", "nashi", "zentai", "")])

    def test_iken_inside_year_line(self):
        # ② の中の「令和７年度は、」の行は締めの文にしない（決算の年度でない）
        r = self.get("matome", "ippan", 3)
        lines = ki.read_lines(os.path.join(self.tmp, "data", "text", DOC + ".txt"))
        self.assertTrue(lines[int(r["line_to"]) - 1].startswith("令和７年度は"))

    def test_ranges_and_pages(self):
        r1 = self.get("matome", "ippan", 1)
        self.assertEqual((r1["line_from"], r1["line_to"]), ("11", "12"))
        r4 = self.get("matome", "ippan", 4)
        self.assertEqual((r4["page"], r4["insatsu_page"]), ("4", "11"))
        k = self.get("kenzenka", "nashi", 1)
        self.assertIn("ページをまたぐ", k["note"])
        self.assertIn("「1.3」", k["note"])

    def test_matome_ends_at_numbered_heading(self):
        # まとめの後の「３．歳出決算について」の表は、まとめに入れない
        r = self.get("matome", "kokuho", 1)
        lines = ki.read_lines(os.path.join(self.tmp, "data", "text", DOC + ".txt"))
        self.assertTrue(lines[int(r["line_to"]) - 1].startswith("見本の国民健康保険の財政"))

    def test_clean(self):
        res = self.res()
        self.assertEqual({k: v["fails"] for k, v in res.items() if v["fails"]}, {})

    def test_keep_human_value(self):
        path = os.path.join(kozo.struct_dir(self.tmp), "kessan-iken.tsv")
        r = self.get("matome", "ippan", 2)
        r["midashi"] = "見本の見出し"
        r["note"] = "人が原典の画像で見出しを確かめた"
        kozo.write_table(path, ki.COLS, self.rows)
        ki.kian(self.tmp, out=lambda *a: None)
        rows = kozo.load_table(self.tmp, "kessan-iken")
        r2 = next(x for x in rows if (x["bubun"], x["kaikei"], x["n"]) == ("matome", "ippan", "2"))
        self.assertEqual(r2["midashi"], "見本の見出し")
        self.assertIn("人が原典の画像で見出しを確かめた", r2["note"])

    def test_unknown_kaikei_stops(self):
        make_sample(self.tmp, RAW.replace("３．西山財産区会計", "３．見本特別会計"), TEXT)
        with self.assertRaises(kozo.Stop):
            ki.kian(self.tmp, out=lambda *a: None)


class TestCheck(Base):
    def test_shift(self):
        r = self.get("matome", "ippan", 3)
        r["line_from"] = str(int(r["line_from"]) + 1)
        self.assertIn("line_from", [c for c, _ in self.res()[ki.rid(r)]["fails"]])

    def test_overlap(self):
        r = self.get("matome", "ippan", 3)
        r["line_from"] = self.get("matome", "ippan", 2)["line_to"]
        self.assertTrue(any("重なる" in m for _, m in self.res()[ki.rid(r)]["fails"]))

    def test_gap(self):
        r = self.get("matome", "ippan", 2)
        r["line_to"] = str(int(r["line_to"]) - 1)
        res = self.res()
        nxt = self.get("matome", "ippan", 3)
        self.assertTrue(res[ki.rid(r)]["fails"])
        self.assertTrue(any("間に字の行" in m for _, m in res[ki.rid(nxt)]["fails"]))

    def test_insatsu_page(self):
        r = self.get("matome", "ippan", 2)
        r["insatsu_page"] = "8"
        self.assertIn("insatsu_page", [c for c, _ in self.res()[ki.rid(r)]["fails"]])


class TestSkippedNumber(Base):
    # 原典の番号が飛ぶ見本（① の次が ③）。道具は直さず、段1 で要人確認にする
    raw = RAW.replace("② 見本の職員", "③ 見本の職員", 1).replace("③ 審査の結果", "④ 審査の結果", 1)
    text = TEXT.replace("②\n", "③\n", 1).replace("\f③\n", "\f④\n", 1)

    def test_bango(self):
        r = self.get("matome", "ippan", 3)
        self.assertEqual(r["bango"], "③")
        self.assertIn("bango", [c for c, _ in self.res()[ki.rid(r)]["fails"]])


class TestNoMusubi(Base):
    raw = RAW.replace("令和６年度は、見本の経済", "見本の経済").replace("見本の行財政運営に努められたい。\n", "")
    text = TEXT.replace("令和６年度は、見本の経済", "見本の経済").replace("見本の行財政運営に努められたい。\n", "")

    def test_last_iken_to_end(self):
        self.assertEqual([r["shurui"] for r in self.rows if r["kaikei"] == "ippan"], ["maegaki", "iken", "iken", "iken"])
        self.assertIn("締めの文が無い", self.get("matome", "ippan", 1)["note"])
        self.assertEqual({k: v["fails"] for k, v in self.res().items() if v["fails"]}, {})


class TestAudit(Base):
    def test_audit_and_rerun(self):
        code, _, led, stats = ki.iken_check(self.tmp, out=lambda *a: None, today="2026-11-12")
        self.assertEqual(code, 0)
        self.assertEqual(len(led), len(self.rows))
        self.assertEqual(stats["ng"], 0)
        # H4：一般会計のまとめ・ほかの会計のまとめ・節を1つずつ
        h4 = sorted({tuple(r["row"].split("/")[1:3]) for r in led if "要人確認:H4" in r["note"]})
        self.assertEqual(h4, [("kenzenka", "nashi"), ("matome", "ippan"), ("matome", "kokuho")])
        path = os.path.join(kozo.struct_dir(self.tmp), "audit", "kessan-iken.tsv")
        rows = htd.read_tsv(path, kozo.AUDIT_COLS)
        rows[0]["reviewer"], rows[0]["date"], rows[0]["note"] = "someone", "2026-11-13", "人が確認：一致"
        kozo.write_table(path, kozo.AUDIT_COLS, rows)
        with open(path, encoding="utf-8") as f:
            before = f.read()
        ki.kian(self.tmp, out=lambda *a: None)
        ki.iken_check(self.tmp, out=lambda *a: None, today="2026-11-14")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_selftest(self):
        self.assertEqual(ki.selftest(self.tmp, out=lambda *a: None), 0)


if __name__ == "__main__":
    unittest.main()
