"""tools/kozo_kansa.py の検査（DOCS_SCHEMA.md §13.15・13.10）。ネットワークと PDF は使わない。

実行: python3 -B -m unittest discover -s tools
見本の原典の文字（text と text_raw）は、この検査の中で一時の場所に作る（data/ には入れない）。
見本は、監査等の結果の字の形をまねて作ったもので、原典の値ではない。
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
import kozo_kansa as kk  # noqa: E402

# 定期監査（令和4年度の形。見出しは「監 査 対 象 期 間」、事項の1つめは折り返す）
T1_RAW = """日野町監査委員告示第１６号
地方自治法第１９９条第１項および第２項に基づき、令和４年度に実施した定期監査
結果を下記のとおり公表する。
令和４年１２月２８日
日野町代表監査委員 甲 一郎
定期監査結果
１．監査日時および 令和４年１０月３１日（月）午前９時００分～午前１０時００分
監 査 場 所 日野町役場 ４階 委員会室
２．実 施 監 査 委 員 甲 一郎 ・ 乙 二郎
３．監 査 対 象 期 間 見本課
４．監 査 対 象 見本課の分掌する事務全般について
主たる監査事項 ○見本の事業の取り組みについて（令和３年度の成果と令和４年度
の概要）
〇見本の計画について
５．監 査 手 続 令和４年度監査計画に基づき資料の提出を求め、説
明を受け、質疑応答を交え実施した。
６．監 査 の 結 果 見本の事業は適正に執行されていることを確認し
た。引き続き、事務の改善に努められたい。"""

# data/text（既定の取り出し）。見出しと値が別の行に分かれ、本文の前に別の字の行が入る
T1_TEXT = """日野町監査委員告示第１６号
地方自治法第１９９条第１項および第２項に基づき、令和４年度に実施した定期監査
結果を下記のとおり公表する。
令和４年１２月２８日
日野町代表監査委員

甲

一郎

定期監査結果
１．監査日時および
監

査

令和４年１０月３１日（月）午前９時００分～午前１０時００分

３．監 査 対 象 期 間

見本課

４．監
見本課の分掌する事務全般について
主たる監査事項
○見本の事業の取り組みについて（令和３年度の成果と令和４年度
の概要）
〇見本の計画について
６．監 査 の 結 果

明を受け、質疑応答を交え実施した。
見本の事業は適正に執行されていることを確認し
た。引き続き、事務の改善に努められたい。
"""

# 定期監査（「主たる」の見出しが無い。data/text では見出しと本文の始まりが同じ行）
T2_RAW = """日野町監査委員告示第４号
地方自治法第１９９条第１項および第２項に基づき、令和３年度に実施した定期監査結
果を下記のとおり公表する。
令和４年３月１６日
日野町代表監査委員 甲 一郎
定期監査結果
１．監査日時および 令和４年２月２５日（金）午前９時３０分～午前１０時２０分
監 査 場 所 日野町役場 ４階 委員会室
２．実 施 監 査 委 員 甲 一郎 ・ 乙 二郎
３．監 査 対 象 機 関 見本福祉課
４．監 査 対 象 見本福祉課の分掌する事務全般および下記の事業について
○見本の福祉計画について
５．監 査 手 続 令和３年度監査計画に基づき資料の提出を求め実施した。
６．監 査 の 結 果 見本の計画は基本的な計画である。
推進されたい。"""

T2_TEXT = """日野町監査委員告示第４号
地方自治法第１９９条第１項および第２項に基づき、令和３年度に実施した定期監査結
果を下記のとおり公表する。
令和４年３月１６日
定期監査結果
１．監査日時および 令和４年２月２５日（金）午前９時３０分～午前１０時２０分
３．監 査 対 象 機 関 見本福祉課
４．監 査 対 象 見本福祉課の分掌する事務全般および下記の事業について
○見本の福祉計画について
５．監 査 手 続 令和３年度監査計画に基づき資料の提出を求め実施した。
６．監 査 の 結 果 見本の計画は基本的な
計画である。
推進されたい。
"""

# 財政援助団体等の監査（額に付いた名前、合計の行、指摘事項。2ページ）
D1_RAW = """日野町監査委員告示第２９号
地方自治法第１９９条第７項に基づき、令和７年度に実施した財政援助団体等の監査結果
を下記のとおり公表する。
令和７年１１月２５日
日野町代表監査委員 甲 一郎
監査結果
１．実施監査委員 甲 一郎 ・ 丙 三郎
２．監査の種類 財政援助団体等に対する監査
３．監査等の概要
ア．監査等の実施期間および監査対象課等
令和７年９月２９日（月）
見本組合・見本農林課
イ．監査等の対象とした事項および範囲（令和６年度決算）
見本の森指定管理料 令和６年度指定管理料 9,100,000 円
・見本補助金 １，０００，０００円（令和６年度決算）
（見本の補助金を含む）
合 計 10,100,000 円
ウ．監査手続
関係証拠書類の提出を求め、説明を受け、質疑応答により実施
\f４．監査結果
ア．監査等による事務の執行、事業の管理状況についての意見
町および団体における管理に誤りのないことを認めます。
イ．指摘事項
指定管理料については算出根拠を
再考されたい。"""

D1_TEXT = """日野町監査委員告示第２９号
地方自治法第１９９条第７項に基づき、令和７年度に実施した財政援助団体等の監査結果
を下記のとおり公表する。
令和７年１１月２５日
監査結果
２．監査の種類

財政援助団体等に対する監査

ア．監査等の実施期間および監査対象課等
令和７年９月２９日（月）
見本組合・見本農林課
イ．監査等の対象とした事項および範囲（令和６年度決算）
見本の森指定管理料

令和６年度指定管理料

9,100,000 円

・見本補助金 １，０００，０００円（令和６年度決算）
（見本の補助金を含む）
合 計 10,100,000 円
ウ．監査手続
\f４．監査結果
ア．監査等による事務の執行、事業の管理状況についての意見
町および団体における管理に誤りのないことを認めます。
イ．指摘事項
指定管理料については算出根拠を
再考されたい。
"""

# 財政援助団体等の監査（日付の次が時刻と場所の行。対象を読めない）
D2_RAW = """日野町監査委員告示第１７号
地方自治法第１９９条第７項に基づき、令和４年度に実施した財政援助団体等の監査結果
を下記のとおり公表する。
令和４年１２月２８日
日野町代表監査委員 甲 一郎
監 査 結 果
１．実施監査委員 甲 一郎 ・ 乙 二郎
２．監査の種類 財政援助団体等に対する監査
３．監査等の概要
ア．監査等の実施期間および監査対象課等
令和４年１１月２８日（月）
午前１０時００分～午前１０時３０分 見本課 委員会室
午前１０時３０分～午前１１時００分 見本改良区 事務所
イ．監査等の対象とした事項および範囲
見本運営事業補助金 2,600,000 円
（内訳） 甲 乙
見本加算 100 円 200 円
ウ．監査方法
関係証拠書類の提出を求めた。
４．監査結果
ア．監査等による事務の執行、事業の管理状況についての意見
なし
イ．指摘事項 なし"""

D2_TEXT = D2_RAW + "\n"

DOCS = [("kansa-r3-14", T2_RAW, T2_TEXT), ("kansa-r4-10", T1_RAW, T1_TEXT),
        ("kansa-r4-17", D2_RAW, D2_TEXT), ("kansa-r7-18", D1_RAW, D1_TEXT)]


def make_sample(root):
    os.makedirs(os.path.join(root, "data", "text"), exist_ok=True)
    os.makedirs(os.path.join(root, "data", "text_raw"), exist_ok=True)
    rows = ["id\tcategory\ttitle\tcheck"]
    for doc, raw, text in DOCS:
        rows.append(f"{doc}\tkansa\t見本\tok")
        with open(os.path.join(root, "data", "text_raw", doc + ".txt"), "w", encoding="utf-8") as f:
            f.write(raw)
        with open(os.path.join(root, "data", "text", doc + ".txt"), "w", encoding="utf-8") as f:
            f.write(text)
    rows.append("kansa-r1-01\tkansa\t画像だけの本\timage")
    with open(os.path.join(root, "data", "sources.tsv"), "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-kozo-kansa-")
        make_sample(self.tmp)
        kk.kian(self.tmp, out=lambda *a: None)
        self.t = kk.load_all(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def row(self, doc):
        return next(r for r in self.t["kansa-kekka"] if r["doc"] == doc)

    def jiko(self, doc):
        return [j for j in self.t["kansa-jiko"] if j["doc"] == doc]


class TestKian(Base):
    def test_only_ok_docs(self):
        self.assertEqual([r["doc"] for r in self.t["kansa-kekka"]], [d for d, _, _ in DOCS])

    def test_teiki(self):
        r = self.row("kansa-r4-10")
        self.assertEqual(r["shurui"], "teiki")
        self.assertEqual(r["kokuji"], "日野町監査委員告示第１６号")
        self.assertEqual(r["kohyo_bi"], "令和４年１２月２８日")
        self.assertEqual(r["kansa_bi"], "令和４年１０月３１日（月）午前９時００分～午前１０時００分")
        self.assertEqual(r["taisho"], "見本課")
        self.assertEqual(r["shiteki"], "")
        self.assertIn("監 査 対 象 期 間", r["note"])
        js = self.jiko("kansa-r4-10")
        self.assertEqual([j["jiko"] for j in js],
                         ["見本の事業の取り組みについて（令和３年度の成果と令和４年度の概要）", "見本の計画について"])
        self.assertIn("折り返しをつないだ", js[0]["note"])
        self.assertEqual([j["gaku"] for j in js], ["", ""])

    def test_kekka_range_skips_other_lines(self):
        r = self.row("kansa-r4-10")
        lines = T1_TEXT.split("\n")
        self.assertEqual(lines[int(r["kekka_from"]) - 1], "見本の事業は適正に執行されていることを確認し")
        self.assertEqual(lines[int(r["kekka_to"]) - 1], "た。引き続き、事務の改善に努められたい。")

    def test_heading_same_line(self):
        r = self.row("kansa-r3-14")
        lines = T2_TEXT.split("\n")
        self.assertTrue(lines[int(r["kekka_from"]) - 1].startswith("６．監 査 の 結 果"))
        self.assertIn("見出しの字「６．監査の結果」と本文の始まりが同じ行", r["note"])
        self.assertIn("「主たる監査事項」の見出しが無い", r["note"])
        self.assertEqual([j["jiko"] for j in self.jiko("kansa-r3-14")], ["見本の福祉計画について"])

    def test_dantai(self):
        r = self.row("kansa-r7-18")
        self.assertEqual(r["shurui"], "dantai")
        self.assertEqual(r["kansa_bi"], "令和７年９月２９日（月）")
        self.assertEqual(r["taisho"], "見本組合・見本農林課")
        self.assertEqual(r["shiteki"], "指定管理料については算出根拠を再考されたい。")
        js = self.jiko("kansa-r7-18")
        self.assertEqual([(j["jiko"], j["komoku"], j["gaku"], j["gaku_raw"]) for j in js],
                         [("見本の森指定管理料", "令和６年度指定管理料", "9100000", "9,100,000 円"),
                          ("見本補助金", "", "1000000", "１，０００，０００円")])
        self.assertIn("額の後ろの字：（令和６年度決算）", js[1]["note"])
        self.assertIn("次の行の字：（見本の補助金を含む）", js[1]["note"])
        self.assertIn("見出しのかっこ書き：（令和６年度決算）", js[0]["note"])
        lines = D1_TEXT.split("\n")
        self.assertEqual(lines[int(r["kekka_from"]) - 1], "ア．監査等による事務の執行、事業の管理状況についての意見")
        self.assertEqual(lines[int(r["kekka_to"]) - 1], "再考されたい。")

    def test_dantai_unreadable_taisho(self):
        r = self.row("kansa-r4-17")
        self.assertEqual(r["taisho"], "")
        self.assertIn("taisho を読めなかった", r["note"])
        self.assertIn("（内訳）の表は持たない", r["note"])
        self.assertEqual(r["shiteki"], "なし")
        self.assertEqual([j["jiko"] for j in self.jiko("kansa-r4-17")], ["見本運営事業補助金"])

    def test_keep_human_value(self):
        path = os.path.join(kozo.struct_dir(self.tmp), "kansa-kekka.tsv")
        rows = htd.read_tsv(path, kk.KEKKA_COLS)
        for r in rows:
            if r["doc"] == "kansa-r4-17":
                r["taisho"] = "見本改良区・見本課"
        kozo.write_table(path, kk.KEKKA_COLS, rows)
        kk.kian(self.tmp, out=lambda *a: None)
        t = kk.load_all(self.tmp)
        self.assertEqual(next(r for r in t["kansa-kekka"] if r["doc"] == "kansa-r4-17")["taisho"], "見本改良区・見本課")


class TestCheck(Base):
    def run_check(self, t=None):
        return kk.check(t or self.t, kk.Src(self.tmp))

    def test_clean(self):
        res = self.run_check()
        bad = {k: v["fails"] for k, v in res.items() if v["fails"]}
        self.assertEqual(list(bad), [("kansa-kekka", "kansa-r4-17")])
        self.assertEqual(bad[("kansa-kekka", "kansa-r4-17")], [("taisho", "空")])

    def test_total(self):
        for j in self.t["kansa-jiko"]:
            if j["doc"] == "kansa-r7-18" and j["n"] == "2":
                j["gaku"], j["gaku_raw"] = "1000001", "１，０００，００１円"
        res = self.run_check()
        cols = [c for c, _ in res[("kansa-kekka", "kansa-r7-18")]["fails"]]
        self.assertIn("事項", cols)
        self.assertTrue(res[("kansa-jiko", "kansa-r7-18/2")]["fails"])

    def test_weekday(self):
        self.row("kansa-r4-10")["kansa_bi"] = "令和４年１０月３１日（火）午前９時００分～午前１０時００分"
        res = self.run_check()
        self.assertTrue(any("曜日" in m for _, m in res[("kansa-kekka", "kansa-r4-10")]["fails"]))

    def test_kekka_range(self):
        self.row("kansa-r7-18")["kekka_to"] = str(int(self.row("kansa-r7-18")["kekka_to"]) - 1)
        res = self.run_check()
        self.assertIn("kekka_from", [c for c, _ in res[("kansa-kekka", "kansa-r7-18")]["fails"]])

    def test_audit_and_rerun(self):
        code, _, rows, stats = kk.kansa_check(self.tmp, out=lambda *a: None, today="2026-11-12")
        self.assertEqual(code, 0)
        self.assertEqual(len(rows), len(self.t["kansa-kekka"]) + len(self.t["kansa-jiko"]))
        ng = [r for r in rows if r["note"].startswith("要人確認:段1")]
        self.assertEqual([r["row"] for r in ng], ["kansa-r4-17"])
        self.assertEqual(ng[0]["checked"], "")
        # H4 は年度と shurui の組ごとに1本（r3 teiki・r4 teiki・r4 dantai・r7 dantai）
        h4 = sorted({r["row"].split("/")[0] for r in rows if "要人確認:H4" in r["note"]})
        self.assertEqual(len(h4), 4)
        # 人の印を付けて動かし直すと保つ。ほかの行は変わらない
        path = os.path.join(kozo.struct_dir(self.tmp), "audit", "kansa.tsv")
        led = htd.read_tsv(path, kozo.AUDIT_COLS)
        led[0]["reviewer"], led[0]["date"], led[0]["note"] = "someone", "2026-11-13", "H4 0点。人が確認：一致（2026-11-13）"
        kozo.write_table(path, kozo.AUDIT_COLS, led)
        with open(path, encoding="utf-8") as f:
            before = f.read()
        kk.kian(self.tmp, out=lambda *a: None)
        kk.kansa_check(self.tmp, out=lambda *a: None, today="2026-11-14")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_selftest(self):
        self.assertEqual(kk.selftest(self.tmp, out=lambda *a: None), 0)


if __name__ == "__main__":
    unittest.main()
