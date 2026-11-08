"""tools/kozo.py の検査（DOCS_SCHEMA.md §13）。ネットワークと PDF は使わない。

実行: python3 -B -m unittest discover -s tools
見本の原典の文字（text_raw・text）と台帳は、この検査の中で一時の場所に作る（data/ には入れない）。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402
import kozo  # noqa: E402

SAMPLE_YEAR = "r9"

# 見本 A：令和9年度 一般会計 第1号。規模の表、○の行、特別会計の主な内容、事項（区分 → 会計の順）、特別会計の表
SAMPLE_A = [
    """- 1 -
令和９年度６月補正予算の概要について
１ 補正予算の規模
現計予算額② 今回補正額
一般会計 180 180 5 185 2.8 2.8
特別会計 1,010 1,010 7 1,017 0.7 0.7
企業会計 630 630 0 630 0.0 0.0
合計 1,820 1,820 12 1,832 0.7 0.7
２ 一般会計補正予算（第１号）の概要
（１） 補正予算編成の基本的な考え方
今回の補正予算の考え方です。
（３） 歳出の主な内容
○甲事業 5千円
甲事業の説明の
つづきです。
〇乙事業 1千円
○丙事業 △ 1千円
乙事業と丙事業の説明です。
- 2 -
""",
    """- 2 -
３ 特別会計補正予算の概要
（１） 補正予算の主な内容
① 国民健康保険特別会計補正予算（第１号） 7 千円
国保の説明です。
４ その他
（１） 繰越明許費の内容
①追加
【一般会計】
・ 甲事業 12 千円
（２） 地方債の内容
① 変更
・ 乙事業債（とても長い名前の事業の債）
△3 千円
""",
    """令和９年度日野町一般会計補正予算（第１号）
【 歳 入 】
（単位：千円）
(A) 一般財源 (B) 一般財源 (C) 一般財源 (D) 一般財源 (D-A)/A
1 町 税 100 100 100 100 10 10 110 110 10.0
2 地 方 譲 与 税 50 50 50 50 0 0 50 50 0.0
甲 譲 与 税 30 30 30 30 0 0 30 30 0.0
乙 譲 与 税 20 20 20 20 0 0 20 20 0.0
3 町 債 30 10 30 10 △ 5 △ 5 25 5 △ 16.7
うち臨時財政対策債 10 10 10 10 △ 5 △ 5 5 5 △ 50.0
180 160 180 160 5 5 185 165 2.8
現計予算額
当初予算額
補正後予算額
歳入合計
今回補正額
""",
    """【 歳 出 】
（単位：千円）
1 議 会 費 80 70 80 70 5 5 85 75 6.3
2 総 務 費 100 90 100 90 0 0 100 90 0.0
180 160 180 160 5 5 185 165 2.8
1 義 務 的 経 費 180 160 180 160 5 5 185 165 2.8
人 件 費 120 100 120 100 5 5 125 105 4.2
扶 助 費 60 60 60 60 0 0 60 60 0.0
180 160 180 160 5 5 185 165 2.8
歳出合計（目的別）
歳出合計（性質別）
現計予算額
当初予算額
今回補正額 補正後予算額
""",
    """令和９年度日野町特別会計補正予算
（単位：千円）
当初予算額 現計予算額 今回補正額 補正後予算額
国 民 健 康 保 険 特 別 会 計 1,000 1,000 7 1,007 0.7 0.7
西 山 財 産 区 会 計 10 10 0 10 0.0 0.0
1,010 1,010 7 1,017 0.7 0.7
会 計 名
合 計
""",
]
# 見本 B：令和9年度 一般会計 第2号。見出し「第1号補正後予算額」、企業会計、事項（会計 → 区分の順）
SAMPLE_B = [
    """- 1 -
令和９年度９月補正予算の概要について
１ 補正予算の規模
第１号補正後
一般会計 180 185 △ 1 184 2.2 △ 0.5
特別会計 1,010 1,017 0 1,017 0.7 0.0
企業会計 630 630 3 633 0.5 0.5
合計 1,820 1,832 2 1,834 0.8 0.1
２ 一般会計補正予算（第２号）の概要
（３） 歳出の主な内容
○丁事業 △1千円
丁事業の説明です。
４ 企業会計補正予算の概要
水道事業会計補正予算（第 1 号） 3 千円
水道の説明です。
※【収入】一般会計補助金 3 千円【支出】改良費 3 千円
５ その他
（１） 繰越明許費の内容
【一般会計】
③ 廃止
・丁事業 4 千円
""",
    """令和９年度日野町一般会計補正予算（第２号）
【 歳 入 】
1 町 税 100 100 110 110 △ 1 △ 1 109 109 9.0
2 地 方 譲 与 税 50 50 50 50 0 0 50 50 0.0
甲 譲 与 税 30 30 30 30 0 0 30 30 0.0
乙 譲 与 税 20 20 20 20 0 0 20 20 0.0
3 町 債 30 10 25 5 0 0 25 5 △ 16.7
うち臨時財政対策債 10 10 5 5 0 0 5 5 △ 50.0
180 160 185 165 △ 1 △ 1 184 164 2.2
第1号補正後予算額
歳入合計
今回補正額
""",
    """【 歳 出 】
1 議 会 費 80 70 85 75 △ 1 △ 1 84 74 5.0
2 総 務 費 100 90 100 90 0 0 100 90 0.0
180 160 185 165 △ 1 △ 1 184 164 2.2
1 義 務 的 経 費 180 160 185 165 △ 1 △ 1 184 164 2.2
人 件 費 120 100 125 105 △ 1 △ 1 124 104 3.3
扶 助 費 60 60 60 60 0 0 60 60 0.0
180 160 185 165 △ 1 △ 1 184 164 2.2
歳出合計（目的別）
歳出合計（性質別）
第1号補正後予算額
今回補正額 補正後予算額
""",
    """【 収 入 】
200 50 200 50 0 3 200 53
300 60 300 60 0 0 300 60
500 110 500 110 0 3 500 113
【 支 出 】
190 70 190 70 0 3 190 73
290 80 290 80 0 0 290 80
480 150 480 150 0 3 480 153
水 道 事 業 会 計
下 水 道 事 業 会 計
合 計
令和９年度日野町企業会計補正予算
当初予算額 補正前予算額 今回補正額 補正後予算額
""",
]
# 見本 B の企業会計の表の既定の取り出し（会計の名前が数の前に出る）
SAMPLE_B_TEXT_KIGYO = """令和９年度日野町企業会計補正予算
【 収 入 】
水 道 事 業 会 計

200

50

200

50

0

3

200

53

下 水 道 事 業 会 計

300

60

300

60

0

0

300

60

500 110 500 110 0 3 500 113
合

計
【 支 出 】
水 道 事 業 会 計

190

70

190

70

0

3

190

73

下 水 道 事 業 会 計

290 80 290 80 0 0 290 80

480 150 480 150 0 3 480 153
合

計
"""
DOC_A = "yosan-r9-hosei-06"
DOC_B = "yosan-r9-hosei-09"


def make_sample(root):
    """root に見本の data/sources.tsv・data/text_raw・data/text を作る。"""
    d = os.path.join(root, "data")
    os.makedirs(os.path.join(d, "text_raw"), exist_ok=True)
    os.makedirs(os.path.join(d, "text"), exist_ok=True)
    with open(os.path.join(d, "sources.tsv"), "w", encoding="utf-8") as f:
        f.write("\t".join(htd.COLUMNS) + "\n")
        for doc in (DOC_A, DOC_B):
            f.write("\t".join(doc if c == "id" else "yosan" if c == "category" else "" for c in htd.COLUMNS) + "\n")
    pages = {DOC_A: SAMPLE_A, DOC_B: SAMPLE_B}
    for doc, ps in pages.items():
        with open(os.path.join(d, "text_raw", doc + ".txt"), "w", encoding="utf-8") as f:
            f.write("\f".join(ps) + "\f")
    text_b = list(SAMPLE_B)
    text_b[3] = SAMPLE_B_TEXT_KIGYO
    with open(os.path.join(d, "text", DOC_A + ".txt"), "w", encoding="utf-8") as f:
        f.write("\f".join(SAMPLE_A) + "\f")
    with open(os.path.join(d, "text", DOC_B + ".txt"), "w", encoding="utf-8") as f:
        f.write("\f".join(text_b) + "\f")


def pages_of(text):
    return [[ln.strip() for ln in p.split("\n")] for p in text]


def quiet(*a):
    pass


class TestNum(unittest.TestCase):
    def test_read_num(self):
        cases = {"△ 1,531": "-1531", "△1,531": "-1531", "-1,531": "-1531", "1,531": "1531", "0": "0", "－": "－",
                 "皆増": "皆増", "皆減": "皆減", "0.0": "0.0", "△ 11.5": "-11.5", "▲2,238": "-2238", "１２，３４５": "12345",
                 "3486.5": "3486.5"}
        for k, v in cases.items():
            self.assertEqual(kozo.read_num(k), v, k)
        with self.assertRaises(ValueError):
            kozo.read_num("－", integer=True)
        with self.assertRaises(ValueError):
            kozo.read_num("1.5", integer=True)
        with self.assertRaises(ValueError):
            kozo.read_num("千円")

    def test_split_nums(self):
        head, toks = kozo.split_nums("10 地 方 特 例 交 付 金 125,000 125,000 125,000 125,000 △ 12,295 △ 12,295 112,705 "
                                     "112,705 △ 9.8", words=True)
        self.assertEqual(head, "10 地 方 特 例 交 付 金")
        self.assertEqual(toks[4], "△ 12,295")
        self.assertEqual(len(toks), 9)
        head, toks = kozo.split_nums("そ の 他 の 繰 入 金 0 0 0 0 0 0 0 0 －", words=True)
        self.assertEqual((head, toks[-1], len(toks)), ("そ の 他 の 繰 入 金", "－", 9))
        head, toks = kozo.split_nums("一般会計 9,935,000 9,944,500 183,449 10,127,949 1.9% 1.8%", pct=True)
        self.assertEqual((head, toks), ("一般会計", ["9,935,000", "9,944,500", "183,449", "10,127,949", "1.9", "1.8"]))


class TestRead(unittest.TestCase):
    def test_kan_keys(self):
        k = kozo.read_kan(pages_of(SAMPLE_A))
        self.assertEqual((k["year"], k["gou"], k["page"], k["pages"]), (9, 1, 3, [3, 4]))
        keys = [r["key"] for r in k["rows"]]
        self.assertEqual(keys, ["in-01", "in-02", "in-02-1", "in-02-2", "in-03", "in-03-u", "in-total", "out-01", "out-02",
                                "out-total", "sei-1", "sei-1-1", "sei-1-2", "sei-total"])
        rows = {r["key"]: r for r in k["rows"]}
        self.assertEqual((rows["in-01"]["kan_no"], rows["in-01"]["name"]), ("1", "町 税"))
        self.assertEqual(rows["in-02-1"]["name"], "甲 譲 与 税")
        self.assertEqual(rows["in-03"]["hosei"], "-5")
        self.assertEqual(rows["in-03-u"]["taitosho"], "-50.0")
        self.assertEqual(rows["out-total"]["name"], "歳出合計（目的別）")
        self.assertEqual(rows["sei-total"]["name"], "歳出合計（性質別）")
        self.assertEqual(rows["in-total"]["kan_no"], "")
        self.assertEqual((k["label_mae"], k["label_hosei"]), (["現計予算額"], ["今回補正額"]))

    def test_kan_blank_cell(self):
        p = [list(x) for x in pages_of(SAMPLE_A)]
        i = p[2].index("1 町 税 100 100 100 100 10 10 110 110 10.0")
        p[2][i] = "1 町 税 100 100 100 100 10 110 110 10.0"
        k = kozo.read_kan(p)
        r = k["rows"][0]
        self.assertEqual([r[c] for c in kozo.AMT9], [""] * 9)
        self.assertIn("数の字が8個", r["note"])

    def test_maru_desc(self):
        b = kozo.read_body(pages_of(SAMPLE_A))
        m = b["maru"]
        self.assertEqual([x["line"] for x in m], ["○甲事業 5千円", "〇乙事業 1千円", "○丙事業 △ 1千円"])
        self.assertEqual(m[0]["desc"], "甲事業の説明のつづきです。")
        self.assertEqual(m[1]["desc"], "")
        self.assertEqual(m[1]["desc_empty"], "next")
        self.assertEqual(m[2]["desc"], "乙事業と丙事業の説明です。")
        self.assertEqual(kozo.item_fields(m[2]), ("○", "丙事業", "△ 1"))
        self.assertEqual(b["last"], 2)

    def test_toku_line(self):
        b = kozo.read_body(pages_of(SAMPLE_B))
        t = b["toku"][0]
        self.assertEqual(t["toku"], ("", "水道事業会計補正予算（第 1 号）", "水道事業会計", 1, "3"))
        self.assertEqual(t["desc"], "水道の説明です。")
        self.assertEqual(t["sub"], "※【収入】一般会計補助金 3 千円【支出】改良費 3 千円")
        self.assertIsNone(kozo.read_toku_line("○甲事業 5千円"))
        self.assertEqual(kozo.read_toku_line("④ 後期高齢者医療特別会計（第１号） 4,186 千円")[2:4], ("後期高齢者医療特別会計", 1))

    def test_jiko_heads_both_orders(self):
        a = kozo.read_body(pages_of(SAMPLE_A))["jiko"]
        self.assertEqual([(x["kind"], x["kubun_raw"], x["kaikei_raw"]) for x in a],
                         [("kurikoshi", "①追加", "【一般会計】"), ("chihosai", "① 変更", "")])
        self.assertEqual(a[1]["line"], "・ 乙事業債（とても長い名前の事業の債） △3 千円")
        b = kozo.read_body(pages_of(SAMPLE_B))["jiko"]
        self.assertEqual([(x["kind"], x["kubun_raw"], x["kaikei_raw"]) for x in b], [("kurikoshi", "③ 廃止", "【一般会計】")])

    def test_kaikei_tables(self):
        t = kozo.read_kaikei_tables(pages_of(SAMPLE_A), None)["toku"]
        self.assertEqual([(r["name"], r["kaikei"], r["hosei"]) for r in t["rows"]],
                         [("国 民 健 康 保 険 特 別 会 計", "kokuho", "7"), ("西 山 財 産 区 会 計", "nishiyama", "0"),
                          ("合 計", "", "7")])
        text_b = list(SAMPLE_B)
        text_b[3] = SAMPLE_B_TEXT_KIGYO
        k = kozo.read_kaikei_tables(pages_of(SAMPLE_B), pages_of(text_b))["kigyo"]
        got = [(r["kaikei"], r["flow"], r["part"], r["tosho"], r["hosei"]) for r in k["rows"]]
        self.assertEqual(got[:4], [("suido", "shunyu", "shueki", "200", "0"), ("suido", "shunyu", "shihon", "50", "3"),
                                   ("gesuido", "shunyu", "shueki", "300", "0"), ("gesuido", "shunyu", "shihon", "60", "0")])
        self.assertEqual([r["name"] for r in k["rows"]][4:6], ["合 計", "合 計"])
        self.assertEqual(len(k["rows"]), 12)
        self.assertIn("会計の名前は data/text/ による", k["rows"][0]["note"])
        # data/text/ が無ければ名前は決まらない
        k2 = kozo.read_kaikei_tables(pages_of(SAMPLE_B), None)["kigyo"]
        self.assertTrue(all(r["kaikei"] == "" for r in k2["rows"]))

    def test_kaikei_abbr(self):
        self.assertEqual(kozo.kaikei_abbr("国 民 健 康 保 険 特 別 会 計"), "kokuho")
        self.assertEqual(kozo.kaikei_abbr("後期高齢者医療特別会計（第１号）"), "koki")
        self.assertIsNone(kozo.kaikei_abbr("駐車場事業特別会計"))


class TestPrev(unittest.TestCase):
    def kai(self, gou, label):
        return dict(id=f"r3-ippan-{gou:02d}", kaikei="ippan", gou=gou, label_mae=label, note="")

    def test_heading_and_order(self):
        ks = kozo.decide_prev([self.kai(1, "現計予算額"), self.kai(2, "補正前予算額"), self.kai(3, "第２号補正後予算額")])
        self.assertEqual([(k["prev"], k["prev_from"]) for k in ks],
                         [("", ""), ("r3-ippan-01", "号の順"), ("r3-ippan-02", "見出し")])

    def test_proviso(self):
        # 13.6 のただし書きの例（令和3年度の第6号〜第9号）
        ks = kozo.decide_prev([self.kai(6, "現計予算額"), self.kai(7, "8号補正後予算額"), self.kai(8, "6号補正後予算額"),
                               self.kai(9, "現計予算額")])
        got = {k["id"]: (k["prev"], k["prev_from"]) for k in ks}
        self.assertEqual(got["r3-ippan-07"], ("r3-ippan-08", "見出し"))
        self.assertEqual(got["r3-ippan-08"], ("r3-ippan-06", "見出し"))
        self.assertEqual(got["r3-ippan-09"], ("r3-ippan-07", "号の順"))
        self.assertIn("ただし書き", next(k for k in ks if k["gou"] == 9)["note"])


class TestKianCheck(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-kozo-")
        make_sample(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def table(self, name):
        return kozo.load_table(self.root, name)

    def test_kian(self):
        r = kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        self.assertEqual(r["counts"], {"hosei-kai": 4, "hosei-shiryo": 8, "hosei-kan": 28, "hosei-maru": 6, "hosei-jiko": 3,
                                       "hosei-kaikei": 15})
        kai = {k["id"]: k for k in self.table("hosei-kai")}
        self.assertEqual(sorted(kai), ["r9-ippan-01", "r9-ippan-02", "r9-kokuho-01", "r9-suido-01"])
        self.assertEqual((kai["r9-ippan-02"]["prev"], kai["r9-ippan-02"]["prev_from"]), ("r9-ippan-01", "見出し"))
        self.assertEqual(kai["r9-ippan-02"]["label_mae"], "第1号補正後予算額")
        self.assertEqual(kai["r9-suido-01"]["label_mae"], "補正前予算額")
        self.assertEqual(kai["r9-ippan-01"]["fiscal_year"], "2027")
        jiko = self.table("hosei-jiko")
        self.assertEqual([(j["kai"], j["key"], j["kubun"], j["kaikei"], j["amount"]) for j in jiko],
                         [("r9-ippan-01", "z001", "tsuika", "ippan", "12"), ("r9-ippan-01", "z002", "henko", "", "-3"),
                          ("r9-ippan-02", "z001", "haishi", "ippan", "4")])
        maru = self.table("hosei-maru")
        self.assertEqual([(m["kai"], m["key"], m["kind"]) for m in maru],
                         [("r9-ippan-01", "m001", "maru"), ("r9-ippan-01", "m002", "maru"), ("r9-ippan-01", "m003", "maru"),
                          ("r9-ippan-02", "m001", "maru"), ("r9-kokuho-01", "t001", "toku"), ("r9-suido-01", "t001", "toku")])
        shiryo = self.table("hosei-shiryo")
        self.assertIn(dict(kai="r9-ippan-01", doc=DOC_A, part="kan", pages="3-4", note=""), shiryo)
        self.assertIn(dict(kai="r9-suido-01", doc=DOC_B, part="kigyo", pages="4", note=""), shiryo)

    def test_keep_keys(self):
        kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        path = os.path.join(self.root, "data", "struct", "hosei-maru.tsv")
        rows = self.table("hosei-maru")
        # 既存の key を付け替え、今回の読みに無い行と kaiken の資料の行を足す
        rows[0]["key"] = "m007"
        rows[0]["note"] = "人の注記"
        stale = dict(rows[1])
        stale.update(key="m008", line="○消えた事業 9千円")
        rows.append(stale)
        kozo.write_table(path, kozo.MARU_COLS, rows)
        sp = os.path.join(self.root, "data", "struct", "hosei-shiryo.tsv")
        srows = self.table("hosei-shiryo") + [dict(kai="r9-ippan-01", doc="kaiken-20270630-shiryo-01", part="kaiken", pages="2",
                                                    note="")]
        kozo.write_table(sp, kozo.SHIRYO_COLS, srows)
        r = kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        got = {(m["kai"], m["line"]): m for m in self.table("hosei-maru")}
        self.assertEqual(got[("r9-ippan-01", "○甲事業 5千円")]["key"], "m007")
        self.assertEqual(got[("r9-ippan-01", "○甲事業 5千円")]["note"], "人の注記")
        self.assertIn(("r9-ippan-01", "○消えた事業 9千円"), got)
        self.assertIn("hosei-maru r9-ippan-01/m008", r["left"])
        self.assertTrue(any(s["doc"].startswith("kaiken-") for s in self.table("hosei-shiryo")))
        # 見本の行が足されたら、番号は最大の次
        rows = [m for m in self.table("hosei-maru") if m["key"] != "m002"]
        kozo.write_table(path, kozo.MARU_COLS, rows)
        kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        got = {(m["kai"], m["line"]): m["key"] for m in self.table("hosei-maru")}
        self.assertEqual(got[("r9-ippan-01", "〇乙事業 1千円")], "m009")

    def test_kan_keep_blank_values(self):
        raw = os.path.join(self.root, "data", "text_raw", DOC_A + ".txt")
        with open(raw, encoding="utf-8") as f:
            s = f.read()
        with open(raw, "w", encoding="utf-8") as f:
            f.write(s.replace("1 町 税 100 100 100 100 10 10 110 110 10.0", "1 町 税 100 100 100 100 10 110 110 10.0"))
        kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        path = os.path.join(self.root, "data", "struct", "hosei-kan.tsv")
        rows = self.table("hosei-kan")
        for r in rows:
            if r["kai"] == "r9-ippan-01" and r["key"] == "in-01":
                r.update(tosho="100", tosho_ippan="100", mae="100", mae_ippan="100", hosei="10", hosei_ippan="",
                         hoseigo="110", hoseigo_ippan="110", taitosho="10.0", note="hosei_ippan は原典で空欄")
        kozo.write_table(path, kozo.KAN_COLS, rows)
        r = kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        self.assertEqual(r["kept"], ["r9-ippan-01/in-01"])
        row = next(x for x in self.table("hosei-kan") if x["kai"] == "r9-ippan-01" and x["key"] == "in-01")
        self.assertEqual((row["hosei"], row["hosei_ippan"]), ("10", ""))
        self.assertIn("hosei_ippan は原典で空欄", row["note"])

    def test_row_key(self):
        want = hashlib.sha256("\x1f".join(["hosei-kan", "r6-ippan-03/in-01", "tosho", "1", "千円", "d p.3"]).encode()).hexdigest()[:16]
        self.assertEqual(kozo.row_key("hosei-kan", "r6-ippan-03/in-01", "tosho", "1", "千円", "d p.3"), want)
        self.assertEqual(len(want), 16)

    def test_check_pass_and_fail(self):
        kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        code, res, rows, stats = kozo.hosei_check(self.root, SAMPLE_YEAR, out=quiet, today="2027-01-01")
        self.assertEqual(code, 0, [f["msg"] for f in res["fails"]])
        self.assertTrue(all(r["checked"] == "✓" and r["reviewer"] == "auto:pdftext" for r in rows))
        self.assertEqual(len(rows), 28 + 6 + 3 + 15)
        r0 = next(r for r in rows if r["row"] == "r9-ippan-01/m001")
        self.assertEqual((r0["cols"], r0["value"], r0["source"]), ("name,amount", "甲事業|5", f"{DOC_A} p.1"))
        # 人の印を付けて、値が同じなら保たれる
        path = os.path.join(self.root, "data", "struct", "audit", f"hosei-{SAMPLE_YEAR}.tsv")
        for r in rows:
            if r["row"] == "r9-ippan-01/m001":
                r.update(reviewer="kken78", date="2027-01-02")
        kozo.write_table(path, kozo.AUDIT_COLS, rows)
        # 款の行の額を変えると、その回の款の行の checked が空になる
        kp = os.path.join(self.root, "data", "struct", "hosei-kan.tsv")
        kan = self.table("hosei-kan")
        for r in kan:
            if r["kai"] == "r9-ippan-02" and r["key"] == "out-01":
                r["hosei"] = "-2"
        kozo.write_table(kp, kozo.KAN_COLS, kan)
        code, res, rows, stats = kozo.hosei_check(self.root, SAMPLE_YEAR, out=quiet, today="2027-01-03")
        self.assertEqual(code, 1)
        kinds = {f["kind"] for f in res["fails"]}
        self.assertTrue({"並び", "行の和", "款の和"} <= kinds, kinds)
        self.assertTrue(all(r["checked"] == "" for r in rows if r["row"].startswith("r9-ippan-02/") and r["table"] == "hosei-kan"))
        self.assertTrue(all(r["checked"] == "✓" for r in rows if r["row"].startswith("r9-ippan-01/") and r["table"] == "hosei-kan"))
        kept = next(r for r in rows if r["row"] == "r9-ippan-01/m001")
        self.assertEqual((kept["reviewer"], kept["date"]), ("kken78", "2027-01-02"))

    def test_check_issue_excuses(self):
        kozo.kian(self.root, SAMPLE_YEAR, out=quiet)
        kp = os.path.join(self.root, "data", "struct", "hosei-kan.tsv")
        kan = self.table("hosei-kan")
        for r in kan:   # 前の回の補正後と合わない値（原典の誤りの見立て）。原典の文字も同じ字にする
            if r["kai"] == "r9-ippan-02" and r["key"] == "in-03-u":
                r["mae_ippan"], r["hoseigo_ippan"] = "6", "6"
        kozo.write_table(kp, kozo.KAN_COLS, kan)
        raw = os.path.join(self.root, "data", "text_raw", DOC_B + ".txt")
        with open(raw, encoding="utf-8") as f:
            s = f.read()
        with open(raw, "w", encoding="utf-8") as f:
            f.write(s.replace("うち臨時財政対策債 10 10 5 5 0 0 5 5", "うち臨時財政対策債 10 10 5 6 0 0 5 6"))
        code, res, rows, _ = kozo.hosei_check(self.root, SAMPLE_YEAR, out=quiet)
        self.assertEqual(code, 1)
        idir = os.path.join(self.root, "data", "struct", "issues")
        os.makedirs(idir)
        with open(os.path.join(idir, DOC_B + ".tsv"), "w", encoding="utf-8") as f:
            f.write("\t".join(kozo.ISSUE_COLS) + "\n")
            f.write("1\t原典の食い違い\t2\t款の表 うち臨時財政対策債の2つめの列の一般財源 r9-ippan-02/in-03-u\t…\t…\t高\t原典のまま持つ\t\n")
        code, res, rows, _ = kozo.hosei_check(self.root, SAMPLE_YEAR, out=quiet)
        self.assertEqual(code, 0, [f["msg"] for f in res["fails"] if not f["excused"]])
        r = next(x for x in rows if x["row"] == "r9-ippan-02/in-03-u")
        self.assertEqual(r["checked"], "✓")
        self.assertIn(f"段1 不一致（原典）：issues/{DOC_B}.tsv no.1", r["note"])

    def test_selftest_sample(self):
        empty = tempfile.mkdtemp(prefix="test-kozo-empty-")
        try:
            self.assertEqual(kozo.selftest(root=empty, out=quiet), 0)
        finally:
            shutil.rmtree(empty, ignore_errors=True)


class TestReal(unittest.TestCase):
    PATH = os.path.join(kozo.ROOT, "data", "text_raw", "yosan-r6-hosei-03.txt")

    @unittest.skipUnless(os.path.exists(PATH), "data/text_raw/yosan-r6-hosei-03.txt が無い")
    def test_r6_hosei_03(self):
        pages = kozo.read_pages(self.PATH)
        k = kozo.read_kan(pages)
        self.assertEqual((k["gou"], len(k["rows"])), (10, 66))
        self.assertEqual(sum(1 for r in k["rows"] if r["key"].startswith("in-")), 31)
        b = kozo.read_body(pages)
        self.assertEqual((len(b["maru"]), len(b["toku"]), len(b["jiko"])), (8, 5, 24))
        self.assertEqual(kozo.read_kibo(pages)["一般会計"], ["9935000", "10844658", "354683", "11199341"])


if __name__ == "__main__":
    unittest.main()
