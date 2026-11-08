"""tools/kozo_zaisei.py の検査（DOCS_SCHEMA.md §13.8・13.10）。ネットワークは使わない。

実行: python3 -B -m unittest discover -s tools
見本の財政状況資料集（§5.2 の TSV）は、この検査の中で一時の場所に作る（data/ には入れない）。
見本は2年度（h28・h29）。総括表は h29 で行が1つ下にずれ、普通会計の状況は h29 で区分が1つ増え、
財政比較分析表の図形の列は h28 と h29 で違う（実物の平成27・28年度と平成29年度以降の違いをまねる）。
"""
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402
import kozo_zaisei as kz  # noqa: E402

YEARS = ["h28", "h29"]
SEN = ["sainyu", "saishutsu", "sashihiki", "yokunendo-zaigen", "jisshitsu", "tannendo", "jisshitsu-tannendo",
       "kijun-shunyu", "kijun-juyo", "chihosai", "saimu-futan", "zaicho", "gensai", "tokutei"]
RATIO = ["jisshitsu-hiritsu", "keijo", "keijo-1", "hyojun", "zaiseiryoku", "kosaihi-futan", "jisshitsu-akaji",
         "renketsu-akaji", "jisshitsu-kosaihi", "shorai-futan", "shikin-fusoku"]
HEAD = dict(kz.SOUKATSU)


def sval(key, y):
    """見本の総括表の値。y は年度（前年度の列にも使う）。"""
    yi = {"h27": 0, "h28": 1, "h29": 2}[y]
    if key in SEN:
        return str(1000 + SEN.index(key) * 10 + yi)
    if key in ("jisshitsu-akaji", "renketsu-akaji"):
        return "-"
    if key == "shikin-fusoku":
        return ""
    return f"{RATIO.index(key) + 1}.{yi}"


def write_grid(path, cells):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    nr = max(r for r, _ in cells)
    lines = []
    for r in range(1, nr + 1):
        row = {c: v for (rr, c), v in cells.items() if rr == r and v != ""}
        if not row:
            lines.append("")
            continue
        lines.append("\t".join(row.get(c, "") for c in range(1, max(row) + 1)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def sheet01(y, py, shift):
    ylab, plab = kz.year_label(y), kz.year_label(py)
    c = {(3, 51): "区分", (3, 66): f"{ylab}(千円)", (3, 74): f"{plab}(千円)", (3, 82): "区分",
         (3, 98): f"{ylab}(千円･％)", (3, 106): f"{plab}(千円･％)"}
    for i, k in enumerate(SEN):
        r = 4 + i + (shift if i >= 9 else 0)   # 地方債現在高からあとが、年度で1行ずれる
        c[(r, 51)] = HEAD[k] if k not in ("zaicho", "gensai", "tokutei") else ""
        if k in ("zaicho", "gensai", "tokutei"):
            c[(r, 55)] = HEAD[k]
        c[(r, 66)] = sval(k, y)
        c[(r, 74)] = sval(k, py)
    r = 4
    for k in RATIO:
        lab = HEAD[k]
        if k == "keijo-1":
            lab = "　　(※1)"
        if k in ("jisshitsu-akaji", "renketsu-akaji", "jisshitsu-kosaihi", "shorai-futan"):
            lab = "　" + lab
        c[(r, 82)] = lab
        c[(r, 98)] = sval(k, y)
        c[(r, 106)] = sval(k, py)
        r += 1
    return c


def sheet02(y):
    c = {(3, 2): "歳入の状況（単位 千円・％）", (4, 2): "区分", (4, 18): "決算額", (4, 26): "構成比", (4, 30): "経常一般財源等",
         (4, 38): "構成比", (3, 42): "地方税の状況（単位 千円・％）", (4, 42): "区分", (4, 59): "収入済額", (4, 67): "構成比",
         (4, 71): "超過課税分", (3, 82): "歳出の状況（単位 千円・％）", (4, 82): "目的別歳出の状況（単位 千円・％）",
         (5, 82): "区分", (5, 96): "決算額 (A)", (5, 104): "構成比", (5, 108): "(A)のうち普通建設事業費",
         (5, 121): "(A)のうち充当一般財源等", (12, 82): "性質別歳出の状況（単位 千円・％）", (13, 82): "区分",
         (13, 96): "決算額", (13, 104): "構成比", (13, 108): "充当一般財源等", (13, 116): "経常経費充当一般財源等",
         (13, 127): "経常収支比率"}
    sai = ["地方税", "地方特例交付金", "歳入合計"] if y == "h28" else ["地方税", "分離課税所得割交付金", "地方特例交付金", "歳入合計"]
    for i, n in enumerate(sai):
        c[(5 + i, 2)] = n
        for j, col in enumerate((18, 26, 30, 38)):
            c[(5 + i, col)] = str(100 * (i + 1) + j) if n != "分離課税所得割交付金" else "-"
    for i, n in enumerate(["普通税", "合計"]):
        c[(5 + i, 42)] = n
        for j, col in enumerate((59, 67, 71)):
            c[(5 + i, col)] = str(50 + i + j)
    for i, n in enumerate(["議会費", "歳出合計"]):
        c[(6 + i, 82)] = n
        for j, col in enumerate((96, 104, 108, 121)):
            c[(6 + i, col)] = str(70 + i + j)
    rows = [("　人件費", None), ("元利償還金", "内訳"), ("歳出合計", None)]
    for i, (n, pre) in enumerate(rows):
        if pre:
            c[(14 + i, 82)] = pre
            c[(14 + i, 84)] = n
        else:
            c[(14 + i, 82)] = n
        for j, col in enumerate((96, 104, 108, 116, 127)):
            c[(14 + i, col)] = str(90 + i + j)
    return c


def sheet03(y, shift):
    hr = 10 + shift
    c = {(hr, 50): "健全化判断比率", (hr, 58): kz.year_label(y), (hr, 65): "早期健全化基準", (hr, 72): "財政再生基準"}
    for i, (rk, rn) in enumerate(kz.RATIOS):
        c[(hr + 1 + i, 50)] = rn
        c[(hr + 1 + i, 58)] = sval(rk, y)
        c[(hr + 1 + i, 65)] = str(10 + i)
        c[(hr + 1 + i, 72)] = str(20 + i) if rk != "shorai-futan" else ""
    return c


# 図形の列（左の欄・右の欄）：名前、当該団体の値、順位、全国、県
COLS_OLD = ((3, 5, 9, 12, 14), (20, 23, 27, 29, 31))
COLS_NEW = ((9, 16, 29, 37, 44), (66, 74, 86, 94, 101))


def zukei(y):
    L, R = COLS_OLD if y == "h28" else COLS_NEW
    yi = YEARS.index(y)
    f = []

    def add(r, c, t):
        f.append((r, c, t))

    def panel(cols, r_head, r_name, r_val, name, tokai, heads=("類似団体内順位", "全国平均", "滋賀県平均"), vals=None):
        nm, tc, rc, zc, kc = cols
        for col, h in zip((rc, zc, kc), heads):
            add(r_head, col, h)
        add(r_name, nm, name)
        add(r_name, tc, tokai)
        for col, v in zip((rc, zc, kc), vals):
            add(r_val, col, v)

    panel(R, 9, 10, 10, "将来負担比率", f"[{sval('shorai-futan', y)}%]", vals=(f"3{yi}/49", "33.7", "14.1"))
    panel(L, 31, 32, 32, "財政力指数", f"[{sval('zaiseiryoku', y)}]", vals=(f"2{yi}/49", "0.51", "0.70"))
    panel(R, 31, 32, 32, "実質公債費比率", f"[{sval('jisshitsu-kosaihi', y)}%]", vals=(f"1{yi}/49", "6.4", "6.7"))
    panel(L, 53, 54, 55, "経常収支比率", f"[{sval('keijo', y)}%]", vals=(f"4{yi}/49", "92.8", "91.2"))
    panel(R, 53, 54, 55, "人口千人当たり職員数" if y == "h28" else "人口1,000人当たり職員数", "[9.17人]", vals=(f"3{yi}/49", "7.91", "7.09"))
    panel(L, 76, 76, 77, "人口1人当たり人件費・物件費等決算額", "[129,100円]", vals=(f"2{yi}/49", "131,654", "121,791"))
    panel(R, 76, 76, 77, "ラスパイレス指数", "[98.5]", heads=("類似団体内順位", "全国市平均", "全国町村平均"),
          vals=(f"3{yi}/49", "99.1", "96.4"))
    add(33, 1, "1.80")        # 目盛りの字（取らない）
    add(36, L[2] + 1, "財政力指数は0.69となり")   # 分析欄の文（取らない）
    f.sort(key=lambda x: (x[0], x[1]))
    return f


def write_zukei(path, figs):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("行\t列\t種類\t系列\t項目\t文字\n")
        for r, c, t in figs:
            fh.write(f"{r}\t{c}\t図形\t\t\t{t}\n")


def make_sample(root):
    """見本の原典（data/sources.tsv と data/text/zaisei-<年度>-shiryoshu/）を root に作る。"""
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(os.path.join(root, "data", "sources.tsv"), "w", encoding="utf-8") as f:
        f.write("id\tcategory\n")
        for y in YEARS:
            f.write(f"zaisei-{y}-shiryoshu\tzaisei\n")
    for i, y in enumerate(YEARS):
        d = os.path.join(root, "data", "text", f"zaisei-{y}-shiryoshu")
        py = {"h28": "h27", "h29": "h28"}[y]
        write_grid(os.path.join(d, "01.tsv"), sheet01(y, py, i))
        write_grid(os.path.join(d, "02.tsv"), sheet02(y))
        write_grid(os.path.join(d, "03.tsv"), sheet03(y, i))
        write_grid(os.path.join(d, "04.tsv"), {(1, 2): "見本"})
        write_zukei(os.path.join(d, "04-zukei.tsv"), zukei(y))


def quiet(*a):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test-kozo-zaisei-")
        make_sample(self.tmp)
        self.res = kz.kian(self.tmp, out=quiet, src=kz.Source(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def table(self, name):
        return kz.load_tables(self.tmp)[name]

    def ichi(self, key, y, col=""):
        return [r for r in self.table("zaisei-ichi") if (r["key"], r["col"], r["year"]) == (key, col, y)][0]

    def shihyo(self, key):
        return [r for r in self.table("zaisei-shihyo") if r["key"] == key][0]


class TestNames(unittest.TestCase):
    def test_year_label(self):
        self.assertEqual(kz.year_label("h27"), "平成27年度")
        self.assertEqual(kz.year_label("r1"), "令和元年度")
        self.assertEqual(kz.year_label("r6"), "令和6年度")

    def test_keys(self):
        ks = kz.shihyo_keys()
        self.assertEqual(sum(1 for k in ks if k[1] == "soukatsu"), 25)
        self.assertEqual(sum(1 for k in ks if k[1] == "kenzenka"), 8)
        self.assertEqual(sum(1 for k in ks if k[1] == "hikaku"), 28)
        self.assertIn(("hikaku-rasupairesu-zenkoku-choson", "hikaku", None), ks)

    def test_fig_value(self):
        self.assertEqual(kz.read_fig_value("tokai", "[0.66]"), ("0.66", ""))
        self.assertEqual(kz.read_fig_value("tokai", "[16.3%]"), ("16.3", ""))
        self.assertEqual(kz.read_fig_value("tokai", "[182,036円]"), ("182036", ""))
        self.assertEqual(kz.read_fig_value("tokai", "[9.31人]"), ("9.31", ""))
        self.assertEqual(kz.fig_unit("tokai", "[9.31人]"), "人")
        self.assertEqual(kz.fig_unit("tokai", "[126,433円]"), "円")
        self.assertEqual(kz.fig_unit("tokai", "[0.66]"), "")
        self.assertEqual(kz.read_fig_value("tokai", "[0.66千]")[0], "")   # 決まりに無い単位の字は読まない
        self.assertEqual(kz.read_fig_value("juni", "20/37"), ("20/37", ""))
        self.assertEqual(kz.read_fig_value("zenkoku", "0.0"), ("0.0", ""))
        self.assertEqual(kz.read_fig_value("zenkoku", "169,281"), ("169281", ""))
        self.assertEqual(kz.fig_unit("tokai", "[16.3%]"), "％")


class TestKian(Base):
    def test_counts(self):
        self.assertEqual(len(self.table("zaisei-shihyo")), 61)

    def test_row_shift(self):
        """地方債現在高は h28 と h29 で行が違うが、見出しの字で当てる。"""
        a, b = self.ichi("chihosai", "h28"), self.ichi("chihosai", "h29")
        self.assertNotEqual(kz.parse_cell(a["cell"])[0], kz.parse_cell(b["cell"])[0])
        self.assertEqual(self.shihyo("chihosai")["h28"], sval("chihosai", "h28"))
        self.assertEqual(self.shihyo("chihosai")["h29"], sval("chihosai", "h29"))
        self.assertEqual(a["cell"], "R13C66")
        self.assertEqual(b["cell"], "R14C66")
        self.assertEqual(self.shihyo("chihosai")["unit"], "千円")

    def test_kome(self):
        """「(※1)」は経常収支比率のすぐ下の行、「資金不足比率 (※4)」は (※4) まで含めて当てる。"""
        r = self.ichi("keijo-1", "h29")
        k = self.ichi("keijo", "h29")
        self.assertEqual(kz.parse_cell(r["label_cell"])[0], kz.parse_cell(k["label_cell"])[0] + 1)
        self.assertEqual(self.shihyo("keijo-1")["h29"], sval("keijo-1", "h29"))
        s = self.ichi("shikin-fusoku", "h28")
        self.assertTrue(s["cell"])
        self.assertEqual(self.shihyo("shikin-fusoku")["h28"], "")
        self.assertIn("原典で空欄", s["note"])
        self.assertEqual(self.shihyo("jisshitsu-akaji")["h28"], "-")

    def test_kenzenka(self):
        self.assertEqual(self.shihyo("soki-jisshitsu-kosaihi")["h29"], "12")
        self.assertEqual(self.shihyo("saisei-shorai-futan")["h29"], "")
        self.assertNotEqual(self.ichi("soki-jisshitsu-kosaihi", "h28")["cell"], self.ichi("soki-jisshitsu-kosaihi", "h29")["cell"])

    def test_futsu_rows_change(self):
        """区分が年度で増える。字で当て、無い年度は空。"""
        f = self.table("zaisei-futsu")
        keys = {r["name"]: r["key"] for r in f if r["key"].startswith("sainyu-")}
        self.assertEqual(keys["地方税"], "sainyu-001")
        self.assertEqual(keys["地方特例交付金"], "sainyu-002")
        self.assertEqual(keys["分離課税所得割交付金"], "sainyu-004")
        r = [x for x in f if x["key"] == keys["分離課税所得割交付金"] and x["col"] == "gaku"][0]
        self.assertEqual(r["h28"], "")
        self.assertEqual(r["h29"], "-")
        self.assertIn("h28 は区分の行が無い", r["note"])
        r = [x for x in f if x["key"] == "sainyu-002" and x["col"] == "gaku"][0]
        self.assertEqual((r["h28"], r["h29"]), ("200", "300"))   # h29 は1行下
        self.assertEqual(self.ichi("sainyu-002", "h29", "gaku")["cell"], "R7C18")
        cols = [x["col"] for x in f if x["key"] == "seishitsu-001"]
        self.assertEqual(cols, ["gaku", "kosei", "ippan", "keijo", "keijo_hiritsu"])
        # 地方税の状況の列の見出しは、隣の表の題の手前まで
        self.assertEqual([x["col"] for x in f if x["key"] == "chihozei-001"], ["gaku", "kosei", "choka"])
        # 「内訳」と同じ行の区分
        g = [x for x in f if x["key"].startswith("seishitsu-") and x["name"] == "元利償還金"]
        self.assertTrue(g)
        self.assertIn("区分の列にほかの字もある", self.ichi(g[0]["key"], "h28", "gaku")["note"])

    def test_zukei_columns(self):
        """図形の列は h28 と h29 で違うが、名前と見出しの図形で当てる。"""
        a, b = self.ichi("hikaku-zaiseiryoku-juni", "h28"), self.ichi("hikaku-zaiseiryoku-juni", "h29")
        self.assertEqual(a["cell"], "R32C9")
        self.assertEqual(b["cell"], "R32C29")
        self.assertEqual(self.shihyo("hikaku-zaiseiryoku-juni")["h28"], "20/49")
        self.assertEqual(self.shihyo("hikaku-zaiseiryoku-tokai")["h29"], sval("zaiseiryoku", "h29"))
        # 次の行で当てる
        self.assertEqual(self.ichi("hikaku-keijo-zenkoku", "h29")["cell"], "R55C37")
        self.assertEqual(self.shihyo("hikaku-keijo-zenkoku")["h29"], "92.8")
        self.assertEqual(self.shihyo("hikaku-rasupairesu-zenkoku-choson")["h28"], "96.4")
        self.assertEqual(self.shihyo("hikaku-jinkenhi-zenkoku")["h28"], "131654")
        # 単位の字を除いて読み、unit に書く。平均は tokai と同じ単位、順位は空
        self.assertEqual(self.shihyo("hikaku-jinkenhi-tokai")["h28"], "129100")
        self.assertEqual(self.shihyo("hikaku-jinkenhi-tokai")["unit"], "円")
        self.assertEqual(self.shihyo("hikaku-jinkenhi-zenkoku")["unit"], "円")
        self.assertEqual(self.shihyo("hikaku-jinkenhi-juni")["unit"], "")
        self.assertEqual(self.shihyo("hikaku-shorai-futan-ken")["unit"], "％")
        self.assertEqual(self.shihyo("hikaku-rasupairesu-zenkoku-choson")["unit"], "")
        self.assertEqual(self.shihyo("hikaku-zaiseiryoku-tokai")["unit"], "")
        # 指標の名前の図形の字が年度で違う（h28 は「人口千人当たり職員数」）。name はいちばん新しい年度の字
        self.assertEqual(self.shihyo("hikaku-shokuin-ken")["h28"], "7.09")
        self.assertEqual(self.shihyo("hikaku-shokuin-tokai")["h28"], "9.17")
        self.assertEqual(self.shihyo("hikaku-shokuin-tokai")["unit"], "人")
        self.assertEqual(self.shihyo("hikaku-shokuin-tokai")["name"], "人口1,000人当たり職員数")

    def test_units(self):
        """13.8 の unit の箇条。"""
        self.assertEqual(self.shihyo("chihosai")["unit"], "千円")
        self.assertEqual(self.shihyo("hyojun")["unit"], "千円")
        self.assertEqual(self.shihyo("jisshitsu-hiritsu")["unit"], "％")
        self.assertEqual(self.shihyo("keijo-1")["unit"], "％")   # キーの表の指標の名前「経常収支比率（…）」で決める
        self.assertEqual(self.shihyo("keijo-1")["note"], "")
        self.assertEqual(self.shihyo("shikin-fusoku")["unit"], "％")
        self.assertEqual(self.shihyo("zaiseiryoku")["unit"], "")
        self.assertEqual(self.shihyo("soki-shorai-futan")["unit"], "％")
        f = {(r["key"], r["col"]): r["unit"] for r in self.table("zaisei-futsu")}
        self.assertEqual(f[("sainyu-001", "gaku")], "千円")
        self.assertEqual(f[("sainyu-001", "keijo_kosei")], "％")
        self.assertEqual(f[("seishitsu-001", "keijo_hiritsu")], "％")
        self.assertEqual(f[("chihozei-001", "choka")], "千円")

    def test_keep_on_rerun(self):
        """2回目の起票で、key と note を保つ。"""
        p = os.path.join(kz.sdir(self.tmp), "zaisei-futsu.tsv")
        head, rows = kz.read_any(p)
        rows[0]["note"] = "人の注記"
        kz.write_table(p, head, rows)
        kz.kian(self.tmp, out=quiet, src=kz.Source(self.tmp))
        head2, rows2 = kz.read_any(p)
        self.assertEqual(rows2[0]["note"], "人の注記")
        self.assertEqual([r["key"] for r in rows2], [r["key"] for r in rows])


class TestCheck(Base):
    def run_check(self):
        return kz.check(self.tmp, out=quiet, today="2026-11-08", src=kz.Source(self.tmp))

    def test_pass(self):
        code, res, rows = self.run_check()
        self.assertEqual(code, 0, [f["msg"] for f in res["fails"]])
        led = {r["row"]: r for r in rows}
        self.assertEqual(led["chihosai/h29"]["checked"], "✓")
        self.assertEqual(led["chihosai/h29"]["reviewer"], "auto:check")
        self.assertEqual(led["chihosai/h29"]["source"], "zaisei-h29-shiryoshu 01!R14C66")
        self.assertEqual(led["chihosai/h29"]["cols"], "h29")
        self.assertEqual(led["sainyu-001/gaku/h28"]["table"], "zaisei-futsu")
        self.assertEqual(led["hikaku-zaiseiryoku-juni/h28"]["reviewer"], "auto:pdftext")
        self.assertEqual(led["hikaku-zaiseiryoku-juni/h28"]["checked"], "✓")
        self.assertEqual(led["hikaku-jinkenhi-tokai/h28"]["checked"], "✓")
        self.assertEqual(led["hikaku-shokuin-tokai/h28"]["checked"], "✓")
        self.assertEqual(res["fig"], [])
        h4 = [r for r in rows if "要人確認:H4" in r["note"]]
        self.assertEqual(sorted(r["row"].rsplit("/", 1)[1] for r in h4), ["h28", "h29"])   # 年度ごとに1行

    def test_value_changed(self):
        p = os.path.join(kz.sdir(self.tmp), "zaisei-shihyo.tsv")
        head, rows = kz.read_any(p)
        [r for r in rows if r["key"] == "hyojun"][0]["h28"] = "999"
        kz.write_table(p, head, rows)
        code, res, rows = self.run_check()
        self.assertEqual(code, 1)
        kinds = {f["kind"] for f in res["fails"]}
        self.assertIn("セルの値", kinds)
        self.assertIn("前年度", kinds)   # h29 の資料の前年度の列とも合わなくなる
        led = {r["row"]: r for r in rows}
        self.assertEqual(led["hyojun/h28"]["checked"], "")
        self.assertEqual(led["hyojun/h29"]["checked"], "")

    def test_prev_year_mismatch(self):
        """前年度の列が前の年の資料の当年度の列と合わない。"""
        p = os.path.join(self.tmp, "data", "text", "zaisei-h29-shiryoshu", "01.tsv")
        with open(p, encoding="utf-8") as f:
            g = [ln.split("\t") for ln in f.read().split("\n")]
        g[3][73] = "1"   # R4 C74（歳入総額の前年度の列）
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join("\t".join(x) for x in g))
        code, res, _ = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any(f["kind"] == "前年度" and "sainyu/h29" in f["rows"] for f in res["fails"]))

    def test_kenzenka_mismatch(self):
        p = os.path.join(self.tmp, "data", "text", "zaisei-h28-shiryoshu", "03.tsv")
        with open(p, encoding="utf-8") as f:
            g = [ln.split("\t") for ln in f.read().split("\n")]
        g[12][57] = "9.9"   # 実質公債費比率の行（R13）の当年度の値
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join("\t".join(x) for x in g))
        code, res, _ = self.run_check()
        self.assertTrue(any(f["kind"] == "健全化" and "jisshitsu-kosaihi/h28" in f["rows"] for f in res["fails"]))

    def test_label_changed(self):
        p = os.path.join(kz.sdir(self.tmp), "zaisei-ichi.tsv")
        rows = htd.read_tsv(p, kz.ICHI_COLS)
        r = [x for x in rows if (x["key"], x["year"]) == ("zaicho", "h28")][0]
        r["label_cell"] = "R1C1"
        kz.write_table(p, kz.ICHI_COLS, rows)
        code, res, _ = self.run_check()
        self.assertEqual(code, 1)
        self.assertTrue(any(f["kind"] == "見出し" and "zaicho/h28" in f["rows"] for f in res["fails"]))

    def test_fig_ambiguous(self):
        """同じ列に図形が2つあれば、1つに決まらないので段2 に回す。"""
        p = os.path.join(self.tmp, "data", "text", "zaisei-h29-shiryoshu", "04-zukei.tsv")
        with open(p, "a", encoding="utf-8") as f:
            f.write("33\t37\t図形\t\t\t0.55\n")
        code, res, rows = self.run_check()
        self.assertEqual(code, 0)
        self.assertIn("hikaku-zaiseiryoku-zenkoku/h29", {x["row"] for x in res["fig"]})

    def test_issue_excuses(self):
        p = os.path.join(kz.sdir(self.tmp), "zaisei-shihyo.tsv")
        head, rows = kz.read_any(p)
        [r for r in rows if r["key"] == "gensai"][0]["h28"] = "1"
        kz.write_table(p, head, rows)
        ip = os.path.join(kz.sdir(self.tmp), "issues", "zaisei-h28-shiryoshu.tsv")
        kz.write_table(ip, kz.kozo.ISSUE_COLS, [dict(no="1", kind="原典の食い違い", page="01 R16C66", where="総括表 減債基金 gensai/h28",
                                                     issue="見本", evidence="見本", confidence="高", status="原典のまま持つ", action="")])
        code, res, rows = self.run_check()
        self.assertEqual(code, 0)
        led = {r["row"]: r for r in rows}
        self.assertIn("段1 不一致（原典）：issues/zaisei-h28-shiryoshu.tsv no.1", led["gensai/h28"]["note"])

    def test_human_kept(self):
        code, res, rows = self.run_check()
        p = os.path.join(kz.sdir(self.tmp), "audit", "zaisei.tsv")
        rows = htd.read_tsv(p, kz.AUDIT_COLS)
        r = [x for x in rows if "要人確認:H4" in x["note"]][0]
        r.update(reviewer="kken78", note="人が見た")
        kz.write_table(p, kz.AUDIT_COLS, rows)
        _, _, rows2 = self.run_check()
        r2 = [x for x in rows2 if x["key"] == r["key"]][0]
        self.assertEqual((r2["reviewer"], r2["note"]), ("kken78", "人が見た"))


class TestDifficulty(unittest.TestCase):
    def inf(self, key, value, dan2=False, nextrow=False):
        return dict(r=dict(key=key), value=value, dan2=dan2, nextrow=nextrow)

    def test_points(self):
        self.assertEqual(kz.difficulty(self.inf("hikaku-keijo-zenkoku", "93.8")), (0, []))
        self.assertEqual(kz.difficulty(self.inf("hikaku-keijo-zenkoku", "93.8", nextrow=True))[0], 2)
        self.assertEqual(kz.difficulty(self.inf("hikaku-keijo-juni", "30/37"))[0], 1)
        self.assertEqual(kz.difficulty(self.inf("hikaku-keijo-tokai", "93.5"))[0], 1)
        self.assertEqual(kz.difficulty(self.inf("hikaku-jinkenhi-tokai", "", dan2=True)), (5, ["段2 に回った", "値が空", "当該団体の値"]))

    def test_pick_one_per_year(self):
        info = {
            "hikaku-a-zenkoku/h28": dict(r=dict(key="hikaku-a-zenkoku", year="h28", src="zukei", cell="R1C1"), value="1", dan2=False, nextrow=False),
            "hikaku-a-juni/h28": dict(r=dict(key="hikaku-a-juni", year="h28", src="zukei", cell="R1C2"), value="1/2", dan2=False, nextrow=False),
            "hikaku-b-juni/h28": dict(r=dict(key="hikaku-b-juni", year="h28", src="zukei", cell="R1C3"), value="1/2", dan2=False, nextrow=False),
        }
        m = kz.pick_marks(dict(info=info))
        self.assertEqual(list(m), ["hikaku-a-juni/h28"])   # 同点なら row の文字コード順で先
        self.assertTrue(m["hikaku-a-juni/h28"].startswith("要人確認:H4 1点 値が数でない字"))


class TestSelftest(unittest.TestCase):
    def test_selftest_sample(self):
        tmp = tempfile.mkdtemp(prefix="test-kozo-zaisei-st-")
        try:
            os.makedirs(os.path.join(tmp, "data", "struct"))
            out = []
            self.assertEqual(kz.selftest(root=tmp, out=out.append), 0, "\n".join(out))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
