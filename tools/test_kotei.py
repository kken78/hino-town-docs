"""tools/kotei.py の検査（DOCS_SCHEMA.md §13.14・13.10）。ネットワークは使わない。

実行: python3 -B -m unittest discover -s tools
見本の固定資産台帳（§5.2 の TSV）と財務書類の原典の文字は、この検査の中で一時の場所に作る（data/ には入れない）。
見本の見出しは、実物と違う列の順にし、使わない列も混ぜる（列を見出しの字で決めることを確かめるため）。
"""
import collections
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402
import kotei  # noqa: E402

SAMPLE_DOC = "zaisei-r6-kotei"
SAMPLE_ZAIMU = "zaisei-r6-zaimu"
HEADS = ["予備1", "財産番号", "財産枝番", "資産名称", "施設", "施設名称", "所在地", "補助科目名称", "会計名称", "主管課名称",
         "目的別資産区分", "資産登録種別", "償却対象区分", "備忘価額区分", "資産用途", "資産構造", "耐用年数", "数量", "単位",
         "面積", "階数", "取得年度", "取得日", "取得価額", "減価償却累計額(参考額)", "期首簿価", "減価償却累計額",
         "当期取得額", "当期減価償却額", "当期除売却", "当期評価増減等", "期末簿価", "売却・除却年度", "売却・除却日"]


def asset(no, eda, name, hojo, moku, sk, bb, nendo, date, a, kishu, ruikei, toki_sh, toki_gen, kimatsu, toki_jo="0"):
    return {"財産番号": no, "財産枝番": eda, "資産名称": name, "補助科目名称": hojo, "会計名称": "一般会計",
            "主管課名称": "総務課", "所在地": "松尾", "目的別資産区分": moku, "資産登録種別": "開始資産", "償却対象区分": sk,
            "備忘価額区分": bb, "取得年度": nendo, "取得日": date, "取得価額": a, "期首簿価": kishu,
            "減価償却累計額": ruikei, "減価償却累計額(参考額)": "999", "当期取得額": toki_sh, "当期減価償却額": toki_gen,
            "当期除売却": toki_jo, "当期評価増減等": "0", "期末簿価": kimatsu, "数量": "1", "単位": "筆", "予備1": "x"}


def sample_assets():
    return [
        asset("土地_0000001_1", "0", "役場　敷地", "土地", "総務", "償却対象でない", "", "昭和元年度", "1927/03/31", "1", "1", "0", "0", "0", "1"),
        asset("土地_0000002_2", "0", "町道", "その他（公共土地）", "生活インフラ・国土保全", "償却対象でない", "", "明治38年度", "1905/04/01", "1", "1", "0", "0", "0", "1"),
        asset("工作物_0000003_1", "0", "〇〇宅前　有蓋水槽", "工作物", "総務", "償却対象", "1円残しする", "平成元年度", "1990/03/31", "1", "1", "0", "0", "0", "1"),
        asset("建物_0000004_1", "0", "西公民館", "建物", "教育", "償却対象", "1円残しする", "令和元年度", "2019/05/01", "5000", "2", "4999", "0", "1", "1"),
        asset("建物_0000004_1", "1", "西公民館　空調", "建物", "教育", "償却対象", "1円残しする", "令和6年度", "2025/03/31", "10000", "0", "500", "10000", "500", "9500"),
        asset("物品_0000005_1", "0", "消防車", "物品", "総務", "償却対象", "1円残しする", "平成27年度", "2016/03/31", "0", "0", "0", "0", "0", "0"),
        asset("ソフト_0000006_1", "0", "システム", "ソフトウェア", "総務", "償却対象", "1円残ししない", "令和2年度", "2020/04/01", "300", "100", "260", "0", "60", "40"),
        asset("工作物_0000007_1", "0", "町道1号線　舗装", "その他（公共工作物）", "生活インフラ・国土保全", "償却対象", "1円残しする", "平成10年度", "1998/06/01", "700", "120", "600", "0", "20", "100"),
    ]


def write_daicho(root, assets, heads=HEADS):
    path = os.path.join(root, "data", "text", SAMPLE_DOC, "01.tsv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["固定資産一覧表\t\t\t作成日:\t2026/03/10", "\t".join(heads)]
    for a in assets:
        lines.append("\t".join(a.get(h, "") for h in heads).rstrip("\t"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def meisai_text(assets, bump=None):
    """見本の台帳から、合う附属明細書の原典の文字を作る。bump=(区分, 列の番号, 足す数) で1つずらせる。"""
    agg = collections.defaultdict(lambda: [0] * 7)
    moku = collections.defaultdict(lambda: collections.Counter())
    for a in assets:
        if a["補助科目名称"] in kotei.NOT_TANGIBLE:
            continue
        k = kotei.HOJO[a["補助科目名称"]]
        d, e, g = int(a["取得価額"]), int(a["減価償却累計額"]), int(a["期末簿価"])
        for kk in {k, (k[0], ""), ("合計", "")}:
            v = agg[kk]
            v[0] += d
            v[3] += d
            v[4] += e
            v[6] += g
            moku[kk][a["目的別資産区分"]] += g
    cols = ["生活インフラ・国土保全", "教育", "総務", "その他"]
    rows1, rows2 = [], []
    for k in kotei.MEISAI_ROWS:
        v = list(agg[k])
        if bump and bump[0] == k:
            v[bump[1]] += bump[2]
            if bump[1] == 3:
                v[0] += bump[2]
                v[6] += bump[2]
        rows1.append(" ".join("-" if x == 0 else f"{x:,}" for x in v))
        m = [moku[k][c] for c in cols] + [v[6]]
        rows2.append(" ".join("-" if x == 0 else f"{x:,}" for x in m))
    head1 = ["①有形固定資産の明細 （単位：円）", "前年度末残高", "（A）", "差引本年度末残高", "（G）"]
    head2 = ["②有形固定資産の行政目的別明細 （単位：円）", "生活インフラ・", "国土保全", "教育 総務 その他 合計"]
    return "\n".join(["附属明細書"] + head1 + rows1 + head2 + rows2 + ["事業用資産", "区分", "全体附属明細書"] + head1 + rows1
                     + ["連結附属明細書", "①有形固定資産の明細 （単位：円）", "全体会計 連結会計", "立木竹 - -"]) + "\n"


def write_meisai(root, text):
    path = os.path.join(root, "data", "text_raw", SAMPLE_ZAIMU + ".txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_mei(root, kime="写さない"):
    """見本の個人の名前らしい字の候補を、すべて同じ決めで書いた TSV。"""
    d = kotei.Daicho(root, SAMPLE_DOC)
    rows = kotei.mei_candidates(d)
    for r in rows:
        r.update(kime=kime, kettei="tester", kettei_date="2026-11-10")
    path = os.path.join(root, "mei.tsv")
    kotei.write_table(path, kotei.MEI_COLS, rows)
    return path


def make_sample(root):
    write_daicho(root, sample_assets())
    write_meisai(root, meisai_text(sample_assets()))
    write_mei(root)


def quiet(*a):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-kotei-")
        make_sample(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def shisan_path(self):
        return os.path.join(self.tmp, "data", "struct", "kotei-shisan.tsv")

    def kian(self, mei=True):
        return kotei.kian(self.tmp, SAMPLE_DOC, os.path.join(self.tmp, "mei.tsv") if mei else None, out=quiet)


class TestYomi(unittest.TestCase):
    def test_nendo(self):
        self.assertEqual(kotei.nendo_y("昭和55年度"), 1980)
        self.assertEqual(kotei.nendo_y("平成元年度"), 1989)
        self.assertEqual(kotei.nendo_y("令和元年度"), 2019)
        self.assertEqual(kotei.nendo_y("明治38年度"), 1905)
        self.assertEqual(kotei.nendo_y("大正元年度"), 1912)
        self.assertIsNone(kotei.nendo_y("昭和５５年度"))
        self.assertIsNone(kotei.nendo_y(""))

    def test_date_fy(self):
        self.assertEqual(kotei.date_fy("2016/03/31"), 2015)
        self.assertEqual(kotei.date_fy("2016/04/01"), 2016)
        self.assertIsNone(kotei.date_fy("2016-04-01"))
        self.assertIsNone(kotei.date_fy("2016/13/01"))

    def test_kubun(self):
        def k(sk, bb, a, m):
            return kotei.kubun_hits(dict(shokyaku=sk, biboh=bb, shutoku_kagaku=a, kimatsu=m))
        self.assertEqual(k("償却対象でない", "", "1", "1"), ["1円で載せた土地"])
        self.assertEqual(k("償却対象", "1円残しする", "1", "1"), ["1円で載せた償却資産"])
        self.assertEqual(k("償却対象", "1円残しする", "5000", "1"), ["償却を終えて1円を残した資産"])
        self.assertEqual(k("償却対象", "", "5000", "300"), ["価額のある資産"])
        self.assertEqual(k("償却対象", "1円残ししない", "300", "0"), ["価額が0の資産"])
        self.assertEqual(k("償却対象", "", "5000", "1"), [])                     # どれにも当たらない
        self.assertEqual(k("償却対象でない", "", "1", "0"), ["1円で載せた土地", "価額が0の資産"])   # 2つに当たる


class TestKian(Base):
    def test_ok(self):
        self.assertEqual(self.kian(), 0)
        rows = htd.read_tsv(self.shisan_path(), kotei.SHISAN_COLS)
        self.assertEqual(len(rows), 8)
        self.assertEqual([r["row"] for r in rows], [str(i) for i in range(3, 11)])
        by = {(r["zaisan_no"], r["edaban"]): r for r in rows}
        self.assertEqual(by[("土地_0000001_1", "0")]["shutoku_nendo_y"], "1926")
        self.assertEqual(by[("土地_0000001_1", "0")]["kubun_1en"], "1円で載せた土地")
        self.assertEqual(by[("建物_0000004_1", "0")]["kubun_1en"], "償却を終えて1円を残した資産")
        self.assertEqual(by[("建物_0000004_1", "1")]["kubun_1en"], "価額のある資産")
        self.assertEqual(by[("物品_0000005_1", "0")]["kubun_1en"], "価額が0の資産")
        self.assertEqual(by[("工作物_0000003_1", "0")]["kubun_1en"], "1円で載せた償却資産")
        # 減価償却累計額(参考額) ではなく 減価償却累計額 の列
        self.assertEqual(by[("建物_0000004_1", "1")]["gensho_ruikei"], "500")
        # 個人の名前らしい字の行は name を空にし、note に理由と決めた人と日
        h = by[("工作物_0000003_1", "0")]
        self.assertEqual(h["name"], "")
        self.assertIn(kotei.MEI_NOTE, h["note"])
        self.assertIn("tester が 2026-11-10 に決めた", h["note"])
        self.assertTrue(all(r["unit"] == "" and kotei.UNIT_NOTE in r["note"] for r in rows))
        for n in ("kotei-shisetsu.tsv", "kotei-taio.tsv", "kotei-nashi.tsv"):
            with open(os.path.join(self.tmp, "data", "struct", n), encoding="utf-8") as f:
                self.assertEqual(len(f.read().splitlines()), 1)

    def test_keep_name(self):
        write_mei(self.tmp, "写す")
        self.assertEqual(self.kian(), 0)
        rows = htd.read_tsv(self.shisan_path(), kotei.SHISAN_COLS)
        h = [r for r in rows if r["zaisan_no"] == "工作物_0000003_1"][0]
        self.assertEqual(h["name"], "〇〇宅前　有蓋水槽")
        self.assertIn(kotei.MEI_KEEP_NOTE, h["note"])

    def test_mei_undecided(self):
        with self.assertRaises(kotei.Stop):
            self.kian(mei=False)
        self.assertFalse(os.path.exists(self.shisan_path()))

    def test_mei_kept_from_table(self):
        """一度起票したあとは、--mei を渡さなくても、表の note から決めを読む。"""
        self.assertEqual(self.kian(), 0)
        self.assertEqual(self.kian(mei=False), 0)
        rows = htd.read_tsv(self.shisan_path(), kotei.SHISAN_COLS)
        self.assertEqual([r for r in rows if r["zaisan_no"] == "工作物_0000003_1"][0]["name"], "")

    def test_bad_boka(self):
        a = sample_assets()
        a[3]["期末簿価"] = "2"   # 期首簿価から期末簿価が合わない（区分も変わる）
        write_daicho(self.tmp, a)
        self.assertEqual(self.kian(), 1)
        self.assertFalse(os.path.exists(self.shisan_path()))

    def test_bad_nendo(self):
        a = sample_assets()
        a[0]["取得日"] = "1927/04/01"   # 取得日の年度が取得年度と違う
        write_daicho(self.tmp, a)
        self.assertEqual(self.kian(), 1)
        self.assertFalse(os.path.exists(self.shisan_path()))

    def test_kubun_none(self):
        a = sample_assets()
        a[6]["備忘価額区分"] = ""
        a[6].update({"期首簿価": "61", "期末簿価": "1"})   # 償却対象・期末1円だが「1円残しする」でない
        write_daicho(self.tmp, a)
        self.assertEqual(self.kian(), 1)
        self.assertFalse(os.path.exists(self.shisan_path()))

    def test_dup_key(self):
        a = sample_assets()
        a.append(dict(a[0]))
        write_daicho(self.tmp, a)
        self.assertEqual(self.kian(), 1)
        self.assertFalse(os.path.exists(self.shisan_path()))

    def test_shisetsu_filled(self):
        a = sample_assets()
        a[3]["施設名称"] = "西公民館"
        write_daicho(self.tmp, a)
        with self.assertRaises(kotei.Stop):
            self.kian()

    def test_head_missing(self):
        write_daicho(self.tmp, sample_assets(), [h for h in HEADS if h != "期末簿価"])
        with self.assertRaises(kotei.Stop):
            self.kian()


class TestCheck(Base):
    def test_ok_and_ledger(self):
        self.assertEqual(self.kian(), 0)
        self.assertEqual(kotei.check(self.tmp, out=quiet), 0)
        led = htd.read_tsv(os.path.join(self.tmp, "data", "struct", "audit", "kotei.tsv"), kotei.AUDIT_COLS)
        self.assertEqual(len(led), 8)
        r = led[0]
        self.assertEqual((r["table"], r["row"], r["source"], r["reviewer"], r["checked"]),
                         ("kotei-shisan", "zaisei-r6-kotei/土地_0000001_1/0", "zaisei-r6-kotei 01!R3", "auto:check", "✓"))
        self.assertEqual(r["cols"].split(","), kotei.AUDIT_VALUE_COLS)
        self.assertEqual(len(r["value"].split("|")), len(kotei.AUDIT_VALUE_COLS))

    def test_value_changed(self):
        self.assertEqual(self.kian(), 0)
        p = self.shisan_path()
        rows = htd.read_tsv(p, kotei.SHISAN_COLS)
        rows[1]["shozai"] = "日野"
        kotei.write_table(p, kotei.SHISAN_COLS, rows)
        self.assertEqual(kotei.check(self.tmp, out=quiet), 1)
        led = htd.read_tsv(os.path.join(self.tmp, "data", "struct", "audit", "kotei.tsv"), kotei.AUDIT_COLS)
        self.assertEqual(sum(1 for r in led if r["checked"] != "✓"), 1)

    def test_form(self):
        self.assertEqual(self.kian(), 0)
        sd = os.path.join(self.tmp, "data", "struct")
        kotei.write_table(os.path.join(sd, "kotei-shisetsu.tsv"), kotei.SHISETSU_COLS,
                          [dict(key="s0001", name="西公民館", name_source="計画 PDF p.30", note="")])
        ok = dict(doc=SAMPLE_DOC, zaisan_no="建物_0000004_1", edaban="0", name="西公民館", shisetsu="s0001",
                  shisetsu_name="西公民館", kakunin="行ごと", konkyo="名前・所在地", kettei="tester",
                  kettei_date="2026-11-10", moto_doc="", note="")
        kotei.write_table(os.path.join(sd, "kotei-taio.tsv"), kotei.TAIO_COLS, [ok])
        self.assertEqual(kotei.check(self.tmp, out=quiet), 0)
        bads = [dict(ok, name="西公民館X"), dict(ok, shisetsu="s0002"), dict(ok, konkyo="勘"), dict(ok, kettei=""),
                dict(ok, edaban="9")]
        for b in bads:
            kotei.write_table(os.path.join(sd, "kotei-taio.tsv"), kotei.TAIO_COLS, [b])
            with self.assertRaises(kotei.Stop):
                kotei.check(self.tmp, out=quiet)
        kotei.write_table(os.path.join(sd, "kotei-taio.tsv"), kotei.TAIO_COLS, [ok, dict(ok, shisetsu="s0001")])
        with self.assertRaises(kotei.Stop):   # 1つの資産の行が2回
            kotei.check(self.tmp, out=quiet)
        kotei.write_table(os.path.join(sd, "kotei-taio.tsv"), kotei.TAIO_COLS, [ok])
        kotei.write_table(os.path.join(sd, "kotei-nashi.tsv"), kotei.NASHI_COLS,
                          [dict(doc=SAMPLE_DOC, shisetsu="s0001", atama="建物", kettei="tester", kettei_date="2026-11-10", note="")])
        with self.assertRaises(kotei.Stop):   # 対応表と対応なしの表の両方にある組
            kotei.check(self.tmp, out=quiet)

    def test_selftest(self):
        self.assertEqual(self.kian(), 0)
        self.assertEqual(kotei.selftest(self.tmp, out=quiet), 0)


class TestZaimu(Base):
    def zaimu(self, **kw):
        return kotei.zaimu(self.tmp, SAMPLE_DOC, SAMPLE_ZAIMU, out=quiet, **kw)

    def test_ok(self):
        self.assertEqual(self.kian(), 0)
        code, diffs = self.zaimu()
        self.assertEqual((code, diffs), (0, []))
        self.assertEqual(self.zaimu(meisai="全体")[0], 0)

    def test_diff(self):
        self.assertEqual(self.kian(), 0)
        write_meisai(self.tmp, meisai_text(sample_assets(), bump=(("事業用資産", "建物"), 4, 7)))
        with self.assertRaises(kotei.Stop):   # 明細の中の検算（G＝D−E）が合わない読みは止める
            self.zaimu()
        a = sample_assets()
        a[3]["目的別資産区分"] = ""
        write_meisai(self.tmp, meisai_text(sample_assets()))
        write_daicho(self.tmp, a)
        self.assertEqual(self.kian(), 0)
        code, diffs = self.zaimu()
        self.assertEqual(code, 1)
        self.assertIn(("②", "（空）", 1, (1,)), diffs)

    def test_renketsu_not_read(self):
        self.assertEqual(self.kian(), 0)
        with self.assertRaises(kotei.Stop):
            self.zaimu(meisai="連結")


if __name__ == "__main__":
    unittest.main()
