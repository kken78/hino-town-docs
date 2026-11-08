"""tools/kozo_kessan.py の検査（DOCS_SCHEMA.md §13.7・13.10）。ネットワークと PDF は使わない。

実行: python3 -B -m unittest discover -s tools
見本の原典の文字（text_raw とシート03 の TSV）は、この検査の中で一時の場所に作る（data/ には入れない）。
見本の数は、令和3年度の資料の字の形をまねて作ったもので、原典の値ではない。
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kozo  # noqa: E402
import kozo_kessan as kk  # noqa: E402

# 意見書（r3）。1ページ目は表紙、2ページ目に一般会計、3ページ目に国民健康保険特別会計、4ページ目に介護保険の2つの勘定
IKENSHO = [
    "令 和 ３ 年 度\n日野町各会計決算審査意見書\n",
    """3
１．一般会計
１．決算の概要
歳入歳出決算の状況 （単位：円、％）
決 歳入総額 11,091,156,343 11,845,707,215
算 歳出総額 10,335,965,287 11,292,112,299
額 差 引 額 755,191,056 553,594,916
本年度実質収支額 722,495,056 円から前年度実質収支額 473,355,916 円を差し引く単年度収支額
""",
    """14
２．国民健康保険特別会計
決 歳入総額 12,400,000 10,000,000 2,400,000
算 歳出総額 10,100,000 9,000,000 1,100,000
額 差 引 額 2,300,000 1,000,000 1,300,000
本年度実質収支額 2,300,000 円から前年度実質収支額 1,000,000 円を差し引く
""",
    """22
３．介護保険特別会計
保険事業勘定
決 歳入総額 2,000,400,000 1,900,000,000
算 歳出総額 1,900,000,000 1,850,000,000
額 差 引 額 100,400,000 50,000,000
本年度実質収支額 100,400,000 円から前年度実質収支額 50,000,000 円を差し引く
介護サービス事業勘定
決 歳入総額 6,000,000 5,000,000 1,000,000
算 歳出総額 5,000,000 4,000,000 1,000,000
額 差 引 額 1,000,000 1,000,000 0
本年度実質収支額 1,000,000 円から前年度実質収支額 1,000,000 円を差し引く
""",
]
# 概要（r3）。1ページ目が歳入、2ページ目が歳出（性質別）。円グラフの値が2回出る項目がある
GAIYOU = [
    """一般会計
歳入
110 億 9,116 万円
町税
37億2,204万円
（33.5%）
町税
37億2,204万円
（33.5%）
その他
73億6,912万円
（66.5%）
自主財源
46億9,057万円
（42.3%）
依存財源
64億59万円
（57.7%）
""",
    """一般会計
歳出
103 億 3,597 万円
人件費
20億7,984万円
（20.1%）
扶助費
20億9.533万円
（20.3%）
その他経費
62億6,080万円
（60.6%）
義務的経費
41億7,517万円
（40.4%）
一般行政経費
61億6,080万円
（59.6%）
""",
]
# シート03（r3）。値は百万円
SHEET = {
    (5, 17): "歳入", (5, 22): "歳出", (5, 27): "形式収支", (5, 32): "実質収支",
    (7, 1): "1", (7, 2): "一般会計", (7, 17): "11090", (7, 22): "10335", (7, 27): "755", (7, 32): "722",
    (26, 17): "総収益 （歳入）", (26, 22): "総費用 （歳出）", (26, 27): "純損益 （形式収支）",
    (26, 32): "資金剰余額 /不足額 （実質収支）",
    (28, 1): "1", (28, 2): "国民健康保険特別会計", (28, 17): "12", (28, 22): "10", (28, 27): "2", (28, 32): "2",
    (29, 1): "2", (29, 2): "介護保険特別会計", (29, 17): "2006", (29, 22): "1905", (29, 27): "101", (29, 32): "101",
    (30, 1): "計", (30, 2): "公営企業会計等",
}


def make_sample(root):
    for d, name, pages in (("text_raw", "kessan-r3-ikensho", IKENSHO), ("text_raw", "kessan-r3-gaiyou", GAIYOU)):
        os.makedirs(os.path.join(root, "data", d), exist_ok=True)
        with open(os.path.join(root, "data", d, name + ".txt"), "w", encoding="utf-8") as f:
            f.write("\f".join(pages) + "\f")
    sd = os.path.join(root, "data", "text", "zaisei-r3-shiryoshu")
    os.makedirs(sd, exist_ok=True)
    nr = max(r for r, _ in SHEET)
    with open(os.path.join(sd, "03.tsv"), "w", encoding="utf-8") as f:
        for r in range(1, nr + 1):
            cs = [c for rr, c in SHEET if rr == r]
            row = [SHEET.get((r, c), "") for c in range(1, max(cs) + 1)] if cs else []
            f.write("\t".join(row) + "\n")


def quiet(*a, **k):
    pass


class TestRead(unittest.TestCase):
    def test_man(self):
        self.assertEqual(kk.read_man("110 億 9,116 万円"), 1109116)
        self.assertEqual(kk.read_man("３億８千５６５万円"), 38565)
        self.assertEqual(kk.read_man("25億８３２万円"), 250832)
        self.assertEqual(kk.read_man("１千２８３万円"), 1283)
        self.assertEqual(kk.read_man("１億80万円"), 10080)
        self.assertEqual(kk.read_man("6億0,084万円"), 60084)
        self.assertEqual(kk.read_man("１０３億２,５４７万円"), 1032547)
        self.assertEqual(kk.read_man("２５４万円"), 254)
        for bad in ("20億9.533万円", "億万円", "12億3,4567万円", "5千1,234万円"):
            with self.assertRaises(ValueError, msg=bad):
                kk.read_man(bad)

    def test_yen(self):
        self.assertEqual(kk.read_yen("11,091,156,343"), 11091156343)
        self.assertEqual(kk.read_yen("△ 743,343"), -743343)

    def test_abbr(self):
        self.assertEqual(kk.abbr_of("x", "国 民 健 康 保 険 特 別 会 計"), "kokuho")
        self.assertEqual(kk.abbr_of("x", "介護老人保健施設特別会計"), "roken")
        self.assertEqual(kk.abbr_of("kessan-h28-gaiyou", "西山財産区"), "nishiyama")
        self.assertIsNone(kk.abbr_of("kessan-r3-ikensho", "西山財産区"))   # 対応の表は資料ごと


class TestRound(unittest.TestCase):
    def test_within(self):
        ok, d = kk.within(11091156343, "円", 1109116, "万円")
        self.assertTrue(ok)
        self.assertTrue(kk.within(2131481728, "円", 2131, "百万円")[0])
        self.assertFalse(kk.within(29915551, "円", 35, "百万円")[0])     # 差 5.08 百万円
        self.assertFalse(kk.within(1000000, "円", 2, "百万円")[0])       # ちょうど1は外
        self.assertTrue(kk.within(1500000, "円", 2, "百万円")[0])


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="kozo-kessan-test-")
        make_sample(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def rows(self, name):
        return {(r["kai"], r["key"]): r for r in kozo.load_table(self.root, name)}

    def test_choose(self):
        kai, gaku, uchi = kk.kian(self.root, out=quiet)
        g = self.rows("kessan-gaku")
        # 意見書を主に選ぶ（円）。概要（万円）とシート03（百万円）は突き合わせだけ
        self.assertEqual(g[("r3-ippan", "sainyu")]["unit"], "円")
        self.assertEqual(g[("r3-ippan", "sainyu")]["doc"], "kessan-r3-ikensho")
        # 介護保険は勘定ごと。勘定に分けない値（シート03）の行は作らない
        self.assertIn(("r3-kaigo", "sainyu-hoken"), g)
        self.assertIn(("r3-kaigo", "jisshitsu-service"), g)
        self.assertNotIn(("r3-kaigo", "sainyu"), g)
        # r2 は r3 意見書の前年度の列
        self.assertEqual(g[("r2-ippan", "sainyu")]["value"], "11845707215")
        self.assertEqual(g[("r2-ippan", "sainyu")]["note"], "r3 意見書の前年度の列")
        names = {r["id"]: r["name"] for r in kozo.load_table(self.root, "kessan-kai")}
        self.assertEqual(names["r3-kokuho"], "国民健康保険特別会計")

    def test_uchiwake(self):
        kk.kian(self.root, out=quiet)
        u = list(self.rows("kessan-uchiwake").values())
        self.assertEqual([r["name"] for r in u if r["name"] == "町税"], ["町税"])   # 2回出ても1行
        fujo = [r for r in u if r["name"] == "扶助費"][0]
        self.assertEqual(fujo["value"], "209533")
        self.assertEqual(fujo["note"], "画像で確かめた：20億9.533万円")
        self.assertEqual({r["group"] for r in u}, {"sainyu", "sainyu-naka", "seishitsu", "seishitsu-naka"})

    def test_check_pass(self):
        kk.kian(self.root, out=quiet)
        code, res, rows, stats = kk.kessan_check(self.root, out=quiet, today="2026-11-08")
        self.assertEqual(code, 0)
        by = {r["row"]: r for r in rows}
        self.assertEqual(by["r3-ippan/sainyu"]["checked"], "✓")
        self.assertEqual(by["r3-ippan/sainyu"]["source"], "kessan-r3-ikensho p.2")
        fujo = [r for r in rows if r["table"] == "kessan-uchiwake" and r["value"].startswith("扶助費|")][0]
        self.assertIn("段2 に回す。画像で確かめた数字", fujo["note"])   # 扶助費は段2
        self.assertEqual(fujo["checked"], "")
        # H4 は年度と表の組に1行（13.10。2026-11-08 の依頼者の決定）
        h4 = [r for r in rows if "要人確認:H4" in r["note"]]
        groups = {(r["table"], r["row"].split("-")[0]) for r in rows}
        self.assertEqual(len(h4), len(groups))

    def test_check_fail(self):
        kk.kian(self.root, out=quiet)
        p = os.path.join(self.root, "data", "struct", "kessan-gaku.tsv")
        rows = kozo.load_table(self.root, "kessan-gaku")
        for r in rows:
            if (r["kai"], r["key"]) == ("r3-kokuho", "sainyu"):
                r["value"] = "12400001"
            if (r["kai"], r["key"]) == ("r3-kokuho", "saishutsu"):
                r["raw"] = "10,100,009"
        kozo.write_table(p, kk.GAKU_COLS, rows)
        code, res, rows2, _ = kk.kessan_check(self.root, out=quiet)
        self.assertEqual(code, 1)
        kinds = {(f["row"], f["kind"]) for f in res["fails"]}
        self.assertIn(("r3-kokuho/sainyu", "値"), kinds)
        self.assertIn(("r3-kokuho/saishutsu", "raw"), kinds)

    def test_cross_source(self):
        # シート03 の国保の歳入を 15 にすると、意見書の 12,400,000 円と丸めの範囲で合わない
        sp = os.path.join(self.root, "data", "text", "zaisei-r3-shiryoshu", "03.tsv")
        with open(sp, encoding="utf-8") as f:
            lines = f.read().split("\n")
        cells = lines[27].split("\t")
        cells[16] = "15"
        lines[27] = "\t".join(cells)
        with open(sp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        kk.kian(self.root, out=quiet)
        code, res, _, _ = kk.kessan_check(self.root, out=quiet)
        self.assertEqual(code, 1)
        self.assertIn(("r3-kokuho/sainyu", "別の出典"), {(f["row"], f["kind"]) for f in res["fails"]})

    def test_kaigo_sum(self):
        # 介護保険の2つの勘定の和（2,006,400,000 円）とシート03 の 2006 百万円は合う
        kk.kian(self.root, out=quiet)
        code, res, _, _ = kk.kessan_check(self.root, out=quiet)
        self.assertEqual(code, 0)

    def test_keep_human(self):
        kk.kian(self.root, out=quiet)
        p = os.path.join(self.root, "data", "struct", "kessan-uchiwake.tsv")
        rows = kozo.load_table(self.root, "kessan-uchiwake")
        keys = {r["name"]: r["key"] for r in rows}
        kk.kian(self.root, out=quiet)
        self.assertEqual({r["name"]: r["key"] for r in kozo.load_table(self.root, "kessan-uchiwake")}, keys)
        self.assertTrue(os.path.exists(p))

    def test_selftest(self):
        self.assertEqual(kk.selftest(self.root, out=quiet), 0)


if __name__ == "__main__":
    unittest.main()
