"""hino-town-docs の構造化の層の道具のうち、財政指標（財政状況資料集）の部分（DOCS_SCHEMA.md §13.8・13.10）。
標準ライブラリだけで動く。命令は tools/kozo.py から呼ぶ。

  python3 -B tools/kozo.py zaisei-kian       財政状況資料集の xlsx の値と図形の字から、zaisei-shihyo・zaisei-futsu・zaisei-ichi を起票する
  python3 -B tools/kozo.py zaisei-check      表をセルの値と図形の字で照合・検算し、検収の台帳 data/struct/audit/zaisei.tsv を書く（段1）
  python3 -B tools/kozo.py zaisei-selftest   段1 の道具に、誤りを入れた表と原典を読ませて、誤りを見つけることを確かめる

値の位置は見出しの字で決める（行と列の番号では決めない）。当たらない見出しや、図形が1つに決まらないときは、
値を空にして note に書く。推定で埋めない。
zaisei-check は、セルの値の照合と検算が合わない行があれば終了コード 1 で終わる（台帳は書く）。
図形の字で一致しなかった行は、checked を空にして note に「段2 に回す」と書く（終了コードには数えない）。
"""
import copy
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402
import kozo  # noqa: E402
from kozo import Stop, join_note, row_key, write_table  # noqa: E402

ROOT = htd.ROOT
TODAY = kozo.TODAY

# ---- 13.8 のキーの表 ----

SOUKATSU = [  # (key, 総括表の見出しの字)
    ("sainyu", "歳入総額"), ("saishutsu", "歳出総額"), ("sashihiki", "歳入歳出差引"),
    ("yokunendo-zaigen", "翌年度に繰越すべき財源"), ("jisshitsu", "実質収支"), ("tannendo", "単年度収支"),
    ("jisshitsu-tannendo", "実質単年度収支"), ("kijun-shunyu", "基準財政収入額"), ("kijun-juyo", "基準財政需要額"),
    ("chihosai", "地方債現在高"), ("saimu-futan", "債務負担行為額（支出予定額）"), ("zaicho", "財政調整基金"),
    ("gensai", "減債基金"), ("tokutei", "その他特定目的基金"), ("jisshitsu-hiritsu", "実質収支比率"),
    ("keijo", "経常収支比率"), ("keijo-1", "(※1)"), ("hyojun", "標準財政規模"), ("zaiseiryoku", "財政力指数"),
    ("kosaihi-futan", "公債費負担比率"), ("jisshitsu-akaji", "実質赤字比率"), ("renketsu-akaji", "連結実質赤字比率"),
    ("jisshitsu-kosaihi", "実質公債費比率"), ("shorai-futan", "将来負担比率"), ("shikin-fusoku", "資金不足比率 (※4)"),
]
# 13.8 のキーの表の「指標」の欄の名前のうち、総括表の見出しの字と違うもの（unit の決まりに使う）
SOUKATSU_SHIHYO = {"keijo-1": "経常収支比率（減収補塡債（特例分）と臨時財政対策債を除いたもの）",
                   "shikin-fusoku": "資金不足比率"}
KEIJO_1_PARENT = "経常収支比率"   # (※1) は、この見出しのすぐ下の行のセル
RATIOS = [("jisshitsu-akaji", "実質赤字比率"), ("renketsu-akaji", "連結実質赤字比率"),
          ("jisshitsu-kosaihi", "実質公債費比率"), ("shorai-futan", "将来負担比率")]
KENZENKA_HEAD = "健全化判断比率"
KIJUN = [("soki", "早期健全化基準"), ("saisei", "財政再生基準")]
HIKAKU_IND = [  # (指標の略, 指標の名前の図形の字)
    ("zaiseiryoku", "財政力指数"), ("keijo", "経常収支比率"), ("shokuin", "人口1,000人当たり職員数"),
    ("jisshitsu-kosaihi", "実質公債費比率"), ("shorai-futan", "将来負担比率"), ("rasupairesu", "ラスパイレス指数"),
    ("jinkenhi", "人口1人当たり人件費・物件費等決算額"),
]
# 指標の名前の図形の字が年度で違うもの（13.8 のキーの表）。どの字でも探す
HIKAKU_ALT = {"shokuin": ["人口千人当たり職員数"]}
HIKAKU_ITEMS = [("juni", "類似団体内順位"), ("zenkoku", "全国平均"), ("ken", "滋賀県平均")]
HIKAKU_ITEMS_RASU = [("juni", "類似団体内順位"), ("zenkoku-shi", "全国市平均"), ("zenkoku-choson", "全国町村平均")]

FUTSU = [  # (表の略, 表の題, [(列の略, 列の見出しの字)])
    ("sainyu", "歳入の状況（単位 千円・％）",
     [("gaku", "決算額"), ("kosei", "構成比"), ("keijo", "経常一般財源等"), ("keijo_kosei", "構成比")]),
    ("chihozei", "地方税の状況（単位 千円・％）", [("gaku", "収入済額"), ("kosei", "構成比"), ("choka", "超過課税分")]),
    ("mokuteki", "目的別歳出の状況（単位 千円・％）",
     [("gaku", "決算額 (A)"), ("kosei", "構成比"), ("futsuken", "(A)のうち普通建設事業費"), ("ippan", "(A)のうち充当一般財源等")]),
    ("seishitsu", "性質別歳出の状況（単位 千円・％）",
     [("gaku", "決算額"), ("kosei", "構成比"), ("ippan", "充当一般財源等"), ("keijo", "経常経費充当一般財源等"),
      ("keijo_hiritsu", "経常収支比率")]),
]

SHEET_SOUKATSU, SHEET_FUTSU, SHEET_KENZENKA, SHEET_HIKAKU = "01", "02", "03", "04"
ICHI_COLS = ["key", "col", "year", "doc", "sheet", "cell", "label_cell", "src", "note"]
AUDIT_COLS = kozo.AUDIT_COLS
FILES = {"zaisei-shihyo": "zaisei-shihyo.tsv", "zaisei-futsu": "zaisei-futsu.tsv", "zaisei-ichi": "zaisei-ichi.tsv"}


def shihyo_keys():
    """13.8 のキーの表の順の (key, table, 指標の名前など)。"""
    out = [(k, "soukatsu", h) for k, h in SOUKATSU]
    for pre, _ in KIJUN:
        for rk, _ in RATIOS:
            out.append((f"{pre}-{rk}", "kenzenka", None))
    for ind, _ in HIKAKU_IND:
        items = HIKAKU_ITEMS_RASU if ind == "rasupairesu" else HIKAKU_ITEMS
        out.append((f"hikaku-{ind}-tokai", "hikaku", None))
        for it, _ in items:
            out.append((f"hikaku-{ind}-{it}", "hikaku", None))
    return out


def shihyo_cols(years):
    return ["key", "table", "name", "unit"] + years + ["note"]


def futsu_cols(years):
    return ["key", "col", "name", "col_name", "unit"] + years + ["note"]


# ---- 字と年度 ----

def ws(s):
    """空白（全角の空白を含む）を除いた字。13.8 の「空白を除いた字の完全な一致」に使う。"""
    return re.sub(r"\s+", "", s or "")


def year_num(y):
    m = re.fullmatch(r"([hr])([0-9]+)", y)
    if not m:
        raise Stop(f"年度は h27・r6 の形で書く（{y}）")
    return (1988 if m.group(1) == "h" else 2018) + int(m.group(2))


def year_label(y):
    """h27 → 平成27年度、r1 → 令和元年度（資料の見出しの字の形）。"""
    m = re.fullmatch(r"([hr])([0-9]+)", y)
    era = "平成" if m.group(1) == "h" else "令和"
    n = int(m.group(2))
    return f"{era}{'元' if n == 1 else n}年度"


def prev_year(y, years):
    i = years.index(y)
    return years[i - 1] if i > 0 else None


def cell_id(r, c):
    return f"R{r}C{c}"


def parse_cell(s):
    m = re.fullmatch(r"R([0-9]+)C([0-9]+)", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def doc_years(root):
    """data/sources.tsv の zaisei-<年度>-shiryoshu。年度の古い順の [(年度, 資料の id)]。"""
    path = os.path.join(root, "data", "sources.tsv")
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
    out = []
    for r in htd.read_tsv(path, head):
        m = re.fullmatch(r"zaisei-([hr][0-9]+)-shiryoshu", r["id"])
        if m:
            out.append((m.group(1), r["id"]))
    return sorted(out, key=lambda x: year_num(x[0]))


# ---- 原典（§5.2 の TSV）を読む ----

class Source:
    """data/text/<id>/<シート>.tsv と <シート>-zukei.tsv を読む。override は selftest で原典を差し替えるのに使う。"""

    def __init__(self, root):
        self.root = root
        self.cache = {}

    def grid(self, doc, sheet):
        k = ("g", doc, sheet)
        if k not in self.cache:
            p = os.path.join(self.root, "data", "text", doc, sheet + ".tsv")
            if not os.path.exists(p):
                raise Stop(f"{os.path.relpath(p, self.root)} が無い")
            with open(p, encoding="utf-8", newline="") as f:
                self.cache[k] = [ln.split("\t") for ln in f.read().split("\n")]
        return self.cache[k]

    def figs(self, doc, sheet):
        k = ("z", doc, sheet)
        if k not in self.cache:
            p = os.path.join(self.root, "data", "text", doc, sheet + "-zukei.tsv")
            if not os.path.exists(p):
                raise Stop(f"{os.path.relpath(p, self.root)} が無い")
            rows = htd.read_tsv(p, ["行", "列", "種類", "系列", "項目", "文字"])
            out = []
            for r in rows:
                if r["種類"] != "図形" or not r["行"] or not r["列"]:
                    continue
                out.append(dict(r=int(r["行"]), c=int(r["列"]), text=r["文字"]))
            self.cache[k] = out
        return self.cache[k]

    def at(self, doc, sheet, r, c):
        g = self.grid(doc, sheet)
        if r < 1 or r > len(g):
            return ""
        row = g[r - 1]
        return row[c - 1] if 1 <= c <= len(row) else ""


def find_cells(grid, pred):
    return [(i + 1, j + 1) for i, row in enumerate(grid) for j, x in enumerate(row) if x != "" and pred(x)]


# ---- 総括表 ----

def year_columns(grid, ylab):
    """R3 の「<年度>(千円)」「<年度>(千円･％)」の見出しのセル。[(行, 列, 字)]。"""
    pat = re.compile(re.escape(ylab) + r"\((千円|千円･％)\)")
    return [(r, c, grid[r - 1][c - 1]) for r, c in find_cells(grid, lambda x: bool(pat.fullmatch(x.strip())))]


def locate_soukatsu(grid, key, head, ylab):
    """戻り値 dict(cell=(r,c) か None, label=(r,c) か None, note=字)。"""
    if key == "keijo-1":
        par = find_cells(grid, lambda x: ws(x) == ws(KEIJO_1_PARENT))
        if len(par) != 1:
            return dict(cell=None, label=None, note=f"見出し「{KEIJO_1_PARENT}」のセルが {len(par)} か所")
        r, c = par[0]
        if ws(grid[r][c - 1] if c <= len(grid[r]) else "") != ws(head):
            return dict(cell=None, label=None, note=f"見出し「{KEIJO_1_PARENT}」のすぐ下の行に「(※1)」のセルが無い")
        lab = (r + 1, c)
    else:
        hits = find_cells(grid, lambda x: ws(x) == ws(head))
        if len(hits) != 1:
            return dict(cell=None, label=None, note=f"見出し「{head}」のセルが {len(hits)} か所")
        lab = hits[0]
    cols = [x for x in year_columns(grid, ylab) if x[1] > lab[1]]
    if not cols:
        return dict(cell=None, label=lab, note=f"見出しの右に「{ylab}(千円)」の列が無い")
    vc = min(x[1] for x in cols)
    return dict(cell=(lab[0], vc), label=lab, note="", head=[x for x in cols if x[1] == vc][0])


def prev_column(grid, vcol, ylab, plab):
    """当年度の列 vcol の見出しと同じ単位の、前年度の列の番号（無ければ None）。"""
    cur = [x for x in year_columns(grid, ylab) if x[1] == vcol]
    if not cur:
        return None
    suffix = cur[0][2].strip()[len(ylab):]
    hits = find_cells(grid, lambda x: x.strip() == plab + suffix)
    hits = [c for r, c in hits if c > vcol]
    return min(hits) if hits else None


def soukatsu_unit(head_text, ylab, key):
    """13.8 の unit の箇条。「(千円)」の列は千円。「(千円･％)」の列は、13.8 のキーの表の「指標」の欄の名前に
    「比率」を含めば ％、「指数」を含めば空（無単位）、ほかは千円。"""
    suffix = head_text.strip()[len(ylab):]
    if suffix == "(千円)":
        return "千円"
    name = SOUKATSU_SHIHYO.get(key, dict(SOUKATSU)[key])
    if "比率" in name:
        return "％"
    if "指数" in name:
        return ""
    return "千円"


# ---- 健全化判断比率（シート03） ----

def locate_kenzenka(grid, ratio_name, kijun_head):
    hs = find_cells(grid, lambda x: ws(x) == KENZENKA_HEAD)
    if len(hs) != 1:
        return dict(cell=None, label=None, note=f"見出し「{KENZENKA_HEAD}」のセルが {len(hs)} か所")
    hr, hc = hs[0]
    rows = [r for r in range(hr + 1, hr + 5) if r <= len(grid) and hc <= len(grid[r - 1]) and ws(grid[r - 1][hc - 1]) == ratio_name]
    if len(rows) != 1:
        return dict(cell=None, label=None, note=f"見出し「{KENZENKA_HEAD}」の下の4行に「{ratio_name}」が {len(rows)} 行")
    cols = [j + 1 for j, x in enumerate(grid[hr - 1]) if ws(x) == kijun_head]
    if len(cols) != 1:
        return dict(cell=None, label=(rows[0], hc), note=f"見出しの行に「{kijun_head}」が {len(cols)} か所")
    return dict(cell=(rows[0], cols[0]), label=(rows[0], hc), head=(hr, hc), note="")


def kenzenka_year_col(grid, ylab):
    hs = find_cells(grid, lambda x: ws(x) == KENZENKA_HEAD)
    if len(hs) != 1:
        return None
    hr, _ = hs[0]
    cols = [j + 1 for j, x in enumerate(grid[hr - 1]) if ws(x) == ylab]
    return (hr, cols[0]) if len(cols) == 1 else None


# ---- 財政比較分析表の図形 ----

BRACKET = re.compile(r"^\[(.*)\]$")


def read_fig_value(item, text):
    """図形の字から値を読む。戻り値 (値, note)。読めなければ ("", 理由)。"""
    t = text.strip()
    if item == "tokai":
        m = BRACKET.match(t)
        if not m:
            return "", f"当該団体の値の図形の字「{t}」が「[ ]」の形でない"
        body = re.sub(r"[%人円]$", "", m.group(1))
        try:
            return kozo.read_num(body), ""
        except ValueError:
            return "", f"図形の字「{t}」から「[」「]」と単位の字を除いた「{body}」を数に読めない"
    if item == "juni":
        if re.fullmatch(r"[0-9]+/[0-9]+", t):
            return t, ""
        return "", f"類似団体内順位の図形の字「{t}」が「20/37」の形でない"
    if t in kozo.WORDS:
        return t, ""
    try:
        return kozo.read_num(t), ""
    except ValueError:
        return "", f"図形の字「{t}」を数に読めない"


def fig_unit(item, text):
    """単位。当該団体の値は「[ ]」の中の数の後の字（% は ％）。ほかは図形に無いので空。"""
    if item != "tokai":
        return ""
    m = BRACKET.match(text.strip())
    if not m:
        return ""
    u = m.group(1)[-1:] if m.group(1)[-1:] in ("%", "人", "円") else ""
    return "％" if u == "%" else u


def ind_names(ind_name):
    """指標の名前の図形の字（年度で違う字を含む）。"""
    ind = [k for k, n in HIKAKU_IND if n == ind_name][0]
    return [ind_name] + HIKAKU_ALT.get(ind, [])


def is_ind_name(text, ind_name):
    return ws(text) in {ws(n) for n in ind_names(ind_name)}


def locate_hikaku(figs, ind_name, item, head):
    """13.8 の図形の位置の決まり。戻り値 dict(status, cell, label, text, nextrow, note)。
    status は ok・none（当たらない）・multi（1つに決まらない）。"""
    names = [f for f in figs if is_ind_name(f["text"], ind_name)]
    if len(names) != 1:
        return dict(status="none" if not names else "multi", cell=None, label=None, text="", nextrow=False,
                    note=f"指標の名前の図形「{ind_name}」が {len(names)} 個")
    nr, nc = names[0]["r"], names[0]["c"]
    heads_all = HIKAKU_ITEMS_RASU if ind_name == dict(HIKAKU_IND)["rasupairesu"] else HIKAKU_ITEMS
    hpos = {}
    for it, h in heads_all:
        cand = [f for f in figs if ws(f["text"]) == ws(h) and f["r"] in (nr - 1, nr) and f["c"] > nc]
        if not cand:
            hpos[it] = None
            continue
        mc = min(f["c"] for f in cand)
        near = [f for f in cand if f["c"] == mc]
        hpos[it] = near[0] if len(near) == 1 else "multi"
    if item == "tokai":
        found = [v for v in hpos.values() if isinstance(v, dict)]
        if not found:
            return dict(status="none", cell=None, label=(nr, nc), text="", nextrow=False,
                        note="見出しの図形（類似団体内順位など）が指標の名前の図形の行か前の行に無い")
        end = min(f["c"] for f in found)
        cand = [f for f in figs if f["r"] in (nr, nr + 1) and nc < f["c"] < end and BRACKET.match(f["text"].strip())]
        if len(cand) != 1:
            return dict(status="none" if not cand else "multi", cell=None, label=(nr, nc), text="", nextrow=False,
                        note=f"指標の名前の図形と同じ行か次の行の「[ ]」の形の図形が {len(cand)} 個")
        v = cand[0]
        return dict(status="ok", cell=(v["r"], v["c"]), label=(nr, nc), text=v["text"], nextrow=v["r"] != nr, note="")
    hf = hpos.get(item)
    if hf is None:
        return dict(status="none", cell=None, label=None, text="", nextrow=False,
                    note=f"見出しの図形「{head}」が指標の名前の図形の行か前の行に無い")
    if hf == "multi":
        return dict(status="multi", cell=None, label=None, text="", nextrow=False,
                    note=f"見出しの図形「{head}」が1つに決まらない")
    cand = [f for f in figs if f["r"] in (nr, nr + 1) and f["c"] == hf["c"] and f is not hf]
    if len(cand) != 1:
        return dict(status="none" if not cand else "multi", cell=None, label=(hf["r"], hf["c"]), text="", nextrow=False,
                    note=f"見出しの図形「{head}」と列が同じ図形が、指標の名前の図形の行と次の行に {len(cand)} 個")
    v = cand[0]
    return dict(status="ok", cell=(v["r"], v["c"]), label=(hf["r"], hf["c"]), text=v["text"], nextrow=v["r"] != nr, note="")


def hikaku_spec(key):
    """hikaku-<指標>-<項目> → (指標の名前, 項目, 見出しの字)。"""
    for ind, name in HIKAKU_IND:
        pre = f"hikaku-{ind}-"
        if key.startswith(pre):
            item = key[len(pre):]
            items = HIKAKU_ITEMS_RASU if ind == "rasupairesu" else HIKAKU_ITEMS
            head = dict(items).get(item, "")
            return name, item, head
    raise Stop(f"財政比較分析表の key でない（{key}）")


# ---- 普通会計の状況（シート02） ----

def parse_futsu(grid, title, cols):
    """1つの表を読む。戻り値 dict(ok, note, krow, heads={略: (列, 字)}, rows=[dict(name, label=(r,c), extra=[...], vals={略: (r,c)})])。"""
    ts = find_cells(grid, lambda x: ws(x) == ws(title))
    if len(ts) != 1:
        return dict(ok=False, note=f"表の題「{title}」のセルが {len(ts)} か所", rows=[], heads={})
    tr, tc = ts[0]
    krow = None
    for r in range(tr + 1, len(grid) + 1):
        if tc <= len(grid[r - 1]) and ws(grid[r - 1][tc - 1]) == "区分":
            krow = r
            break
    if krow is None:
        return dict(ok=False, note=f"表の題「{title}」の下に「区分」の行が無い", rows=[], heads={})
    row = grid[krow - 1]
    # 表の右の端：題の行で、題より右にある次の字のセル（隣の表の題）の手前まで
    nxt = [j + 1 for j in range(tc, len(grid[tr - 1])) if grid[tr - 1][j] != ""]
    cend = min(nxt) - 1 if nxt else len(row)
    found = []
    for j in range(tc, min(len(row), cend)):
        x = row[j]
        if x == "":
            continue
        if ws(x) == "区分":
            break
        found.append((j + 1, x))
    if len(found) != len(cols) or any(ws(x) != ws(h) for (_, x), (_, h) in zip(found, cols)):
        return dict(ok=False, note=f"表「{title}」の列の見出しが 13.8 の字と合わない（{'・'.join(x for _, x in found)}）",
                    rows=[], heads={})
    heads = {a: f for (a, _), f in zip(cols, found)}
    lend = found[0][0] - 1
    rows = []
    r = krow + 1
    while r <= len(grid):
        g = grid[r - 1]
        labs = [(j + 1, g[j]) for j in range(tc - 1, min(lend, len(g))) if g[j] != ""]
        if not labs:
            break
        lc, name = labs[-1]
        vals = {a: (r, heads[a][0]) for a in heads}
        rows.append(dict(name=name, label=(r, lc), extra=[(r, j) for j, _ in labs[:-1]], vals=vals))
        r += 1
    names = [x["name"] for x in rows]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise Stop(f"表「{title}」に同じ区分の字の行が2つ以上ある（{'・'.join(dup)}）。区分の字で年度をまたいで当てられない")
    return dict(ok=True, note="", krow=krow, heads=heads, rows=rows)


# ---- 表の読み書き ----

def sdir(root):
    return kozo.struct_dir(root)


def read_any(path):
    """見出しの行をそのまま列にして読む（年度の列は表によって増える）。"""
    if not os.path.exists(path):
        return None, []
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
    return head, htd.read_tsv(path, head)


def load_tables(root):
    out = {}
    for name, fn in FILES.items():
        head, rows = read_any(os.path.join(sdir(root), fn))
        out[name] = rows
        out[name + ":head"] = head
    return out


# ---- 起票 ----

def build(root, src=None):
    """原典を読んで、表3つの新しい行を作る。戻り値 dict(years, shihyo, futsu, ichi, problems)。
    futsu の key はここでは付けない（_t・name で持ち、kian で既存の key と合わせる）。"""
    src = src or Source(root)
    dys = doc_years(root)
    years = [y for y, _ in dys]
    if not dys:
        raise Stop("data/sources.tsv に zaisei-<年度>-shiryoshu の行が無い")
    shihyo = {}
    for key, table, _ in shihyo_keys():
        shihyo[key] = dict(key=key, table=table, name="", unit="", note="", **{y: "" for y in years})
    ichi = []
    problems = []
    units = {}

    def add_ichi(key, col, y, doc, sheet, cell, label, srckind, note=""):
        ichi.append(dict(key=key, col=col, year=y, doc=doc, sheet=sheet, cell=cell_id(*cell) if cell else "",
                         label_cell=cell_id(*label) if label else "", src=srckind, note=note))
        if note:
            problems.append(f"{key}{'/' + col if col else ''}/{y}：{note}")

    futsu_obs = []   # (年度, 資料, 表の略, 区分の字, 行の読み)
    for y, doc in dys:
        ylab = year_label(y)
        g1 = src.grid(doc, SHEET_SOUKATSU)
        for key, head in SOUKATSU:
            loc = locate_soukatsu(g1, key, head, ylab)
            if loc["cell"] is None:
                add_ichi(key, "", y, doc, SHEET_SOUKATSU, None, loc["label"], "cell", loc["note"])
                continue
            v = src.at(doc, SHEET_SOUKATSU, *loc["cell"])
            note = "" if v != "" else "原典で空欄"
            shihyo[key][y] = v
            shihyo[key]["name"] = src.at(doc, SHEET_SOUKATSU, *loc["label"])
            units.setdefault(key, {})[y] = soukatsu_unit(loc["head"][2], ylab, key)
            ichi.append(dict(key=key, col="", year=y, doc=doc, sheet=SHEET_SOUKATSU, cell=cell_id(*loc["cell"]),
                             label_cell=cell_id(*loc["label"]), src="cell", note=note))
        g3 = src.grid(doc, SHEET_KENZENKA)
        for pre, khead in KIJUN:
            for rk, rname in RATIOS:
                key = f"{pre}-{rk}"
                loc = locate_kenzenka(g3, rname, khead)
                if loc["cell"] is None:
                    add_ichi(key, "", y, doc, SHEET_KENZENKA, None, loc["label"], "cell", loc["note"])
                    continue
                v = src.at(doc, SHEET_KENZENKA, *loc["cell"])
                shihyo[key][y] = v
                shihyo[key]["name"] = src.at(doc, SHEET_KENZENKA, *loc["label"]) + " " + src.at(doc, SHEET_KENZENKA, loc["head"][0], loc["cell"][1])
                ichi.append(dict(key=key, col="", year=y, doc=doc, sheet=SHEET_KENZENKA, cell=cell_id(*loc["cell"]),
                                 label_cell=cell_id(*loc["label"]), src="cell", note="" if v != "" else "原典で空欄"))
        figs = src.figs(doc, SHEET_HIKAKU)
        for key, table, _ in shihyo_keys():
            if table != "hikaku":
                continue
            ind_name, item, head = hikaku_spec(key)
            loc = locate_hikaku(figs, ind_name, item, head)
            if loc["status"] != "ok":
                add_ichi(key, "", y, doc, SHEET_HIKAKU, None, loc["label"], "zukei", loc["note"])
                continue
            v, why = read_fig_value(item, loc["text"])
            shihyo[key][y] = v
            nfig = [f for f in figs if is_ind_name(f["text"], ind_name)][0]["text"]
            shihyo[key]["name"] = nfig if item == "tokai" else f"{nfig} {head}"
            if item == "tokai":
                units.setdefault(key, {})[y] = fig_unit(item, loc["text"])
            ichi.append(dict(key=key, col="", year=y, doc=doc, sheet=SHEET_HIKAKU, cell=cell_id(*loc["cell"]),
                             label_cell=cell_id(*loc["label"]), src="zukei", note=why))
            if why:
                problems.append(f"{key}/{y}：{why}")
        g2 = src.grid(doc, SHEET_FUTSU)
        for t, title, cols in FUTSU:
            p = parse_futsu(g2, title, cols)
            if not p["ok"]:
                problems.append(f"futsu {t}/{y}：{p['note']}")
                futsu_obs.append((y, doc, t, None, p))
                continue
            for rw in p["rows"]:
                futsu_obs.append((y, doc, t, rw["name"], dict(p, row=rw)))
    # 単位（13.8 の unit の箇条）
    for key, row in shihyo.items():
        us = units.get(key, {})
        vals = sorted(set(us.values()))
        if row["table"] == "soukatsu":
            if len(vals) != 1:
                row["note"] = join_note(row["note"], "単位の読みが年度で違う（" + "・".join(f"{y} {u or '無し'}" for y, u in sorted(us.items())) + "）")
            else:
                row["unit"] = vals[0]
        elif row["table"] == "kenzenka":
            row["unit"] = "％"
    for key, row in shihyo.items():
        if row["table"] != "hikaku":
            continue
        _, item, _ = hikaku_spec(key)
        if item == "juni":
            continue
        tk = key.rsplit("-", 1)[0] if item != "tokai" else key
        if item in ("zenkoku-shi", "zenkoku-choson"):
            tk = key[: -len("-" + item)]
        tk = tk if tk.endswith("-tokai") else tk + "-tokai"
        us = units.get(tk, {})
        vals = sorted(set(us.values()))
        if len(vals) == 1:
            row["unit"] = vals[0]
        elif vals:
            row["note"] = join_note(row["note"], "当該団体の値の単位の字が年度で違う（" + "・".join(f"{y} {u or '無し'}" for y, u in sorted(us.items())) + "）")
    # 空の値の注記（年度をまとめる）
    for key, row in shihyo.items():
        empt = [y for y in years if row[y] == ""]
        if empt:
            row["note"] = join_note(row["note"], "、".join(empt) + " は値が空（zaisei-ichi の note）")
    return dict(years=years, docs=dict(dys), shihyo=[shihyo[k] for k, _, _ in shihyo_keys()], futsu_obs=futsu_obs,
                ichi=ichi, problems=problems)


FUTSU_UNIT = {"gaku": "千円", "keijo": "千円", "ippan": "千円", "futsuken": "千円", "choka": "千円",
              "kosei": "％", "keijo_kosei": "％", "keijo_hiritsu": "％"}


def kian(root, out=print, src=None):
    src = src or Source(root)
    b = build(root, src)
    years = b["years"]
    out(f"資料 {len(years)} 本：" + " ".join(b["docs"][y] for y in years))
    sd = sdir(root)
    os.makedirs(sd, exist_ok=True)
    old = load_tables(root)
    kept = []
    # 財政指標
    oldmap = {r["key"]: r for r in old["zaisei-shihyo"]}
    shihyo = []
    for r in b["shihyo"]:
        o = oldmap.get(r["key"])
        if o:
            r["note"] = o.get("note", "")
            for y in years:
                if r[y] == "" and o.get(y, ""):
                    r[y] = o[y]
                    kept.append(f"zaisei-shihyo {r['key']}/{y}")
        shihyo.append(r)
    for k, o in oldmap.items():
        if k not in {r["key"] for r in shihyo}:
            raise Stop(f"zaisei-shihyo.tsv に 13.8 のキーの表に無い key がある（{k}）")
    # 普通会計の状況。key は区分の字（空白を除かない）で、既存の key を保つ
    keymap = {}
    nextno = {}
    for o in old["zaisei-futsu"]:
        t = o["key"].rsplit("-", 1)[0]
        keymap.setdefault((t, o["name"]), o["key"])
        nextno[t] = max(nextno.get(t, 0), int(o["key"].rsplit("-", 1)[1]))
    first = {}
    for y, doc, t, name, p in b["futsu_obs"]:
        if name is None:
            continue
        if (t, name) not in keymap:
            n = nextno.get(t, 0) + 1
            nextno[t] = n
            keymap[(t, name)] = f"{t}-{n:03d}"
        first.setdefault((t, name), y)
    futsu = {}
    present = {}
    colnames = {}
    for t, _, cols in FUTSU:
        for a, h in cols:
            colnames[(t, a)] = h
    ichi = list(b["ichi"])
    for y, doc, t, name, p in b["futsu_obs"]:
        if name is None:
            continue
        key = keymap[(t, name)]
        present.setdefault(key, []).append(y)
        rw = p["row"]
        for a, _ in dict((x[0], x) for x in FUTSU)[t][2]:
            fr = futsu.setdefault((key, a), dict(key=key, col=a, name=name, col_name="", unit="", note="",
                                                 **{yy: "" for yy in years}))
            fr["name"] = name   # いちばん新しい年度の字（同じ key は同じ字）
            fr["unit"] = FUTSU_UNIT[a]
            fr["col_name"] = p["heads"][a][1]
            r0, c0 = rw["vals"][a]
            from_src = src.at(doc, SHEET_FUTSU, r0, c0)
            fr[y] = from_src
            note = "原典で空欄" if from_src == "" else ""
            if rw["extra"]:
                note = join_note(note, "区分の列にほかの字もある（" + "、".join(
                    cell_id(*e) for e in rw["extra"]) + "）")
            ichi.append(dict(key=key, col=a, year=y, doc=doc, sheet=SHEET_FUTSU, cell=cell_id(r0, c0),
                             label_cell=cell_id(*rw["label"]), src="cell", note=note))
    torder = [t for t, _, _ in FUTSU]
    oldf = {(o["key"], o["col"]): o for o in old["zaisei-futsu"]}
    for k, fr in futsu.items():
        o = oldf.get(k)
        fr["note"] = ""
        if o:
            fr["note"] = o.get("note", "")
            for y in years:
                if fr[y] == "" and o.get(y, ""):
                    fr[y] = o[y]
                    kept.append(f"zaisei-futsu {k[0]}/{k[1]}/{y}")
        else:
            miss = [y for y in years if y not in present[fr["key"]]]
            notes = []
            if miss:
                notes.append("、".join(miss) + " は区分の行が無い")
            empt = [y for y in present[fr["key"]] if fr[y] == ""]
            if empt:
                notes.append("、".join(empt) + " は原典で空欄")
            fr["note"] = join_note(*notes)
    for k, o in oldf.items():
        if k not in futsu:
            futsu[k] = o
            kept.append(f"zaisei-futsu {k[0]}/{k[1]}（今回の読みに無い行を残した）")
    corder = {(t, a): i for t, _, cols in FUTSU for i, (a, _) in enumerate(cols)}
    futsu_rows = sorted(futsu.values(), key=lambda r: (torder.index(r["key"].rsplit("-", 1)[0]), r["key"],
                                                       corder.get((r["key"].rsplit("-", 1)[0], r["col"]), 99)))
    # 値の位置
    oldi = {(o["key"], o["col"], o["year"]): o for o in old["zaisei-ichi"]}
    korder = {k: i for i, (k, _, _) in enumerate(shihyo_keys())}
    for r in ichi:
        o = oldi.get((r["key"], r["col"], r["year"]))
        if o:
            r["note"] = o["note"]

    def iord(r):
        if r["key"] in korder:
            return (0, korder[r["key"]], 0, "", years.index(r["year"]))
        t = r["key"].rsplit("-", 1)[0]
        return (1, torder.index(t), r["key"], corder.get((t, r["col"]), 99), years.index(r["year"]))
    ichi.sort(key=iord)
    write_table(os.path.join(sd, FILES["zaisei-shihyo"]), shihyo_cols(years), shihyo)
    write_table(os.path.join(sd, FILES["zaisei-futsu"]), futsu_cols(years), futsu_rows)
    write_table(os.path.join(sd, FILES["zaisei-ichi"]), ICHI_COLS, ichi)
    out("表ごとの行の数")
    out(f"  zaisei-shihyo\t{len(shihyo)}（soukatsu {sum(r['table'] == 'soukatsu' for r in shihyo)}・"
        f"kenzenka {sum(r['table'] == 'kenzenka' for r in shihyo)}・hikaku {sum(r['table'] == 'hikaku' for r in shihyo)}）")
    out(f"  zaisei-futsu\t{len(futsu_rows)}（" + "・".join(
        f"{t} {len({r['key'] for r in futsu_rows if r['key'].startswith(t + '-')})}区分" for t in torder) + "）")
    out(f"  zaisei-ichi\t{len(ichi)}")
    out("年度ごとの値の数（入った数・空の数）")
    for y in years:
        a = sum(1 for r in shihyo if r[y] != "") + sum(1 for r in futsu_rows if r[y] != "")
        e = sum(1 for r in shihyo if r[y] == "") + sum(1 for r in futsu_rows if r[y] == "")
        out(f"  {y}\t入った {a}\t空 {e}")
    out(f"当たらない見出し・1つに決まらない図形・読めない字 {len(b['problems'])}")
    for x in b["problems"]:
        out("  " + x)
    if kept:
        out(f"既存の値を保った（または残した）もの {len(kept)}")
        for x in kept:
            out("  " + x)
    return dict(shihyo=shihyo, futsu=futsu_rows, ichi=ichi, problems=b["problems"])



# ---- 段1 ----

def value_of(t, r):
    """ichi の行に当たる表の値と、行の dict。"""
    if r["col"]:
        for x in t["zaisei-futsu"]:
            if x["key"] == r["key"] and x["col"] == r["col"]:
                return x.get(r["year"], ""), x
        return None, None
    for x in t["zaisei-shihyo"]:
        if x["key"] == r["key"]:
            return x.get(r["year"], ""), x
    return None, None


def run_check(t, src, years, issues=()):
    """13.10 段1 の財政指標の項目。戻り値 dict(fails=[...], fig=[...], info={row: dict})。
    fail は dict(kind, rows, msg)。fig は図形の字の行で一致にならなかったもの dict(row, msg)。"""
    fails = []
    fig = []
    info = {}

    def fail(kind, rows, msg):
        fails.append(dict(kind=kind, rows=tuple(rows), msg=msg))

    def rowid(r):
        return f"{r['key']}/{r['col']}/{r['year']}" if r["col"] else f"{r['key']}/{r['year']}"

    heads = dict(SOUKATSU)
    shmap = {x["key"]: x for x in t["zaisei-shihyo"]}
    futsu_parse = {}
    for r in t["zaisei-ichi"]:
        rid = rowid(r)
        val, tr = value_of(t, r)
        info[rid] = dict(r=r, table="zaisei-futsu" if r["col"] else "zaisei-shihyo", tr=tr, value=val or "",
                         dan2=False, nextrow=False)
        if tr is None:
            fail("表の行が無い", [rid], f"{rid}：zaisei-ichi の行に当たる表の行が無い")
            continue
        cell = parse_cell(r["cell"])
        if cell is None:
            if r["src"] == "zukei":
                info[rid]["dan2"] = True
                fig.append(dict(row=rid, msg=f"{rid}：値の位置が無い"))
            else:
                fail("位置が無い", [rid], f"{rid}：値の位置が無い（{r['note']}）")
            continue
        lab = parse_cell(r["label_cell"])
        if r["src"] == "cell":
            sv = src.at(r["doc"], r["sheet"], *cell)
            if sv != val:
                fail("セルの値", [rid], f"{rid}：表の値「{val}」≠ {r['doc']} {r['sheet']}!{r['cell']} の「{sv}」")
            lt = src.at(r["doc"], r["sheet"], *lab) if lab else ""
            if r["col"]:
                tname = r["key"].rsplit("-", 1)[0]
                if lt != tr["name"]:
                    fail("見出し", [rid], f"{rid}：label_cell {r['label_cell']} の字「{lt}」≠ 区分の字「{tr['name']}」")
                k = (r["doc"], tname)
                if k not in futsu_parse:
                    ft = [x for x in FUTSU if x[0] == tname][0]
                    futsu_parse[k] = parse_futsu(src.grid(r["doc"], r["sheet"]), ft[1], ft[2])
                p = futsu_parse[k]
                hc = p["heads"].get(r["col"]) if p["ok"] else None
                if not hc or hc[0] != cell[1] or ws(hc[1]) != ws(tr["col_name"]):
                    fail("見出し", [rid], f"{rid}：値の列 C{cell[1]} が列の見出し「{tr['col_name']}」の列でない")
                if lab and lab[0] != cell[0]:
                    fail("見出し", [rid], f"{rid}：label_cell と値のセルの行が違う")
            elif r["sheet"] == SHEET_SOUKATSU:
                want = heads.get(r["key"])
                if ws(lt) != ws(want):
                    fail("見出し", [rid], f"{rid}：label_cell {r['label_cell']} の字「{lt}」≠ 見出しの字「{want}」")
                if r["key"] == "keijo-1" and lab and ws(src.at(r["doc"], r["sheet"], lab[0] - 1, lab[1])) != ws(KEIJO_1_PARENT):
                    fail("見出し", [rid], f"{rid}：「(※1)」のすぐ上の行が「{KEIJO_1_PARENT}」でない")
                g = src.grid(r["doc"], r["sheet"])
                ylab = year_label(r["year"])
                if not [x for x in year_columns(g, ylab) if x[1] == cell[1]]:
                    fail("見出し", [rid], f"{rid}：値の列 C{cell[1]} の見出しが「{ylab}(千円)」の形でない")
                if lab and lab[0] != cell[0]:
                    fail("見出し", [rid], f"{rid}：label_cell と値のセルの行が違う")
            elif r["sheet"] == SHEET_KENZENKA:
                pre, rk = r["key"].split("-", 1)
                rname = dict(RATIOS)[rk]
                khead = dict(KIJUN)[pre]
                if ws(lt) != rname:
                    fail("見出し", [rid], f"{rid}：label_cell {r['label_cell']} の字「{lt}」≠「{rname}」")
                loc = locate_kenzenka(src.grid(r["doc"], r["sheet"]), rname, khead)
                if loc["cell"] != cell:
                    fail("見出し", [rid], f"{rid}：「{KENZENKA_HEAD}」の表の「{khead}」の列のセルでない")
        else:
            ind_name, item, head = hikaku_spec(r["key"])
            figs = src.figs(r["doc"], r["sheet"])
            loc = locate_hikaku(figs, ind_name, item, head)
            info[rid]["nextrow"] = loc.get("nextrow", False)
            at = [f for f in figs if (f["r"], f["c"]) == cell]
            msgs = []
            if loc["status"] != "ok" or loc["cell"] != cell:
                msgs.append("13.8 の位置の決まりで当たった図形がこの1つでない")
            if len(at) != 1:
                msgs.append(f"{r['cell']} の図形が {len(at)} 個")
            else:
                v, _ = read_fig_value(item, at[0]["text"])
                if v == "" or v != val:
                    msgs.append(f"表の値「{val}」≠ 図形の字「{at[0]['text']}」の読み「{v}」")
            labf = [f for f in figs if lab and (f["r"], f["c"]) == lab]
            want = ind_name if item == "tokai" else head
            if len(labf) != 1 or not (is_ind_name(labf[0]["text"], ind_name) if item == "tokai" else ws(labf[0]["text"]) == ws(want)):
                msgs.append(f"label_cell {r['label_cell']} の図形が「{want}」でない")
            if msgs:
                info[rid]["dan2"] = True
                fig.append(dict(row=rid, msg=f"{rid}：" + "。".join(msgs)))
    # 総括表の前年度の列＝前の年の資料の当年度の列
    byk = {}
    for r in t["zaisei-ichi"]:
        if r["sheet"] == SHEET_SOUKATSU and r["src"] == "cell" and not r["col"]:
            byk.setdefault(r["key"], {})[r["year"]] = r
    for key, ys in byk.items():
        for y, r in ys.items():
            py = prev_year(y, years)
            if py is None:
                continue
            cell = parse_cell(r["cell"])
            if cell is None:
                continue
            g = src.grid(r["doc"], r["sheet"])
            pc = prev_column(g, cell[1], year_label(y), year_label(py))
            rid, prid = f"{key}/{y}", f"{key}/{py}"
            if pc is None:
                fail("前年度", [rid], f"{rid}：前年度（{year_label(py)}）の列が見出しの字で見つからない")
                continue
            pv = src.at(r["doc"], r["sheet"], cell[0], pc)
            mine = shmap.get(key, {}).get(py, "")
            if pv != mine:
                fail("前年度", [rid, prid], f"{rid}：{r['doc']} の前年度の列 {cell_id(cell[0], pc)}「{pv}」≠ {prid} の値「{mine}」")
    # シート03 の健全化判断比率＝総括表の値
    for y in years:
        docs = {r["doc"] for r in t["zaisei-ichi"] if r["year"] == y and r["sheet"] == SHEET_KENZENKA}
        for doc in docs:
            g = src.grid(doc, SHEET_KENZENKA)
            yc = kenzenka_year_col(g, year_label(y))
            for rk, rname in RATIOS:
                rid = f"{rk}/{y}"
                loc = locate_kenzenka(g, rname, KIJUN[0][1])
                if yc is None or loc["label"] is None:
                    fail("健全化", [rid], f"{rid}：シート03 の「{KENZENKA_HEAD}」の表に {year_label(y)} の列か「{rname}」の行が無い")
                    continue
                sv = src.at(doc, SHEET_KENZENKA, loc["label"][0], yc[1])
                mine = shmap.get(rk, {}).get(y, "")
                if sv != mine:
                    fail("健全化", [rid], f"{rid}：シート03 {cell_id(loc['label'][0], yc[1])}「{sv}」≠ 総括表の値「{mine}」")
    for f in fails:
        f["excused"] = []
        for doc, no, where in issues:
            for rw in f["rows"]:
                if re.search(re.escape(rw) + r"(?![0-9a-z-])", where):
                    f["excused"].append((doc, no))
                    break
    return dict(fails=fails, fig=fig, info=info)


def difficulty(inf):
    """13.10 の難しさの点数（財政指標の図形の字）。戻り値 (点数, 理由)。"""
    pts, why = 0, []
    if inf["dan2"]:
        pts += 3
        why.append("段2 に回った")
    if inf["nextrow"]:
        pts += 2
        why.append("次の行で当てた")
    v = inf["value"]
    if v == "":
        pts += 1
        why.append("値が空")
    elif not re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", v):
        pts += 1
        why.append("値が数でない字")
    if inf["r"]["key"].endswith("-tokai"):
        pts += 1
        why.append("当該団体の値")
    return pts, why


def pick_marks(res):
    """H2（位置の無い行）と H4（年度と表の組ごとに1行。図形の字の行）の印。{row: note}。"""
    marks = {}
    for rid, inf in res["info"].items():
        if not inf["r"]["cell"]:
            marks[rid] = join_note(marks.get(rid, ""), kozo.H2_NOTE)
    groups = {}
    for rid, inf in res["info"].items():
        if inf["r"]["src"] == "zukei":
            groups.setdefault((inf["r"]["year"], "hikaku"), []).append(rid)
    for g, rids in sorted(groups.items()):
        best = None
        for rid in rids:
            pts, why = difficulty(res["info"][rid])
            if best is None or pts > best[0] or (pts == best[0] and rid < best[1]):
                best = (pts, rid, why)
        pts, rid, why = best
        marks[rid] = join_note(marks.get(rid, ""), f"要人確認:H4 {pts}点" + (" " + "・".join(why) if why else ""))
    return marks


def audit_rows(t, res, old_ledger, today=TODAY, marks=None):
    marks = marks or {}
    rowfail, rowexc = {}, {}
    for f in res["fails"]:
        for rw in f["rows"]:
            if f["excused"]:
                for doc, no in f["excused"]:
                    rowexc.setdefault(rw, set()).add(f"段1 不一致（原典）：issues/{doc}.tsv no.{no}")
            else:
                rowfail.setdefault(rw, set()).add(f["kind"])
    figbad = {x["row"] for x in res["fig"]}
    old = {r["key"]: r for r in old_ledger}
    out = []
    stats = {}
    for rid, inf in res["info"].items():
        r, tr = inf["r"], inf["tr"]
        table = inf["table"]
        unit = tr["unit"] if tr else ""
        source = f"{r['doc']} {r['sheet']}!{r['cell']}"
        value = inf["value"]
        key = row_key(table, rid, r["year"], value, unit, source)
        notes = sorted(rowexc.get(rid, []))
        if r["src"] == "zukei":
            ok = rid not in figbad and rid not in rowfail
            reviewer = "auto:pdftext"
            if not ok:
                notes.append("段2 に回す")
        else:
            ok = rid not in rowfail
            reviewer = "auto:check"
            if not ok:
                notes.append("段1 不一致：" + "・".join(sorted(rowfail[rid])))
        new = dict(key=key, table=table, row=rid, cols=r["year"], value=value, unit=unit, source=source,
                   checked="✓" if ok else "", reviewer=reviewer, date=today, note=join_note(marks.get(rid, ""), *notes))
        o = old.get(key)
        if o:
            human = o["reviewer"] and not o["reviewer"].startswith("auto:")
            rest = [x for x in o["note"].split("。") if x and not x.startswith(("要人確認:H2", "要人確認:H4"))]
            other_mark = any(x.startswith("要人確認:") for x in rest)
            if human:
                for c in ("checked", "reviewer", "date", "note"):
                    new[c] = o[c]
            elif other_mark:
                for c in ("checked", "reviewer", "date"):
                    new[c] = o[c]
                new["note"] = join_note(marks.get(rid, ""), *rest)
            elif o["checked"] == new["checked"] and o["reviewer"] == new["reviewer"]:
                new["date"] = o["date"]
        out.append(new)
        st = stats.setdefault((table, r["src"]), [0, 0])
        st[0 if new["checked"] == "✓" else 1] += 1
    return out, stats


def check(root, out=print, today=TODAY, src=None):
    t = load_tables(root)
    if not t["zaisei-ichi"]:
        raise Stop("data/struct/zaisei-ichi.tsv が無いか空（先に zaisei-kian）")
    years = [c for c in t["zaisei-shihyo:head"] if re.fullmatch(r"[hr][0-9]+", c)]
    src = src or Source(root)
    docs = {r["doc"] for r in t["zaisei-ichi"]}
    res = run_check(t, src, years, kozo.load_issues(root, docs))
    path = os.path.join(sdir(root), "audit", "zaisei.tsv")
    old = htd.read_tsv(path, AUDIT_COLS) if os.path.exists(path) else []
    marks = pick_marks(res)
    rows, stats = audit_rows(t, res, old, today, marks)
    write_table(path, AUDIT_COLS, rows)
    bad = [f for f in res["fails"] if not f["excused"]]
    exc = [f for f in res["fails"] if f["excused"]]
    out("表と値の種類ごとの行の数・通った行・通らなかった行")
    for (table, s), (a, b2) in sorted(stats.items()):
        out(f"  {table}（{s}）\t{a + b2}\t通った {a}\t通らなかった {b2}")
    if exc:
        out(f"不一致（原典）として通した項目 {len(exc)}")
        for f in exc:
            out(f"  [{f['kind']}] {f['msg']}  ← " + "・".join(f"issues/{d}.tsv no.{n}" for d, n in f["excused"]))
    out(f"合わなかった項目 {len(bad)}")
    for f in bad:
        out(f"  [{f['kind']}] {f['msg']}")
    out(f"段2 に回す図形の字の行 {len(res['fig'])}")
    for x in res["fig"]:
        out("  " + x["msg"])
    for h, name in (("H2", "H2（値の位置の無い行）"), ("H4", "H4（年度と表の組ごとの抜き取り）")):
        hs = [r for r in rows if f"要人確認:{h}" in r["note"]]
        out(f"{name} {len(hs)}")
        for r in hs:
            m = re.search(r"要人確認:H4 ([0-9]+)点 ?([^。]*)", r["note"]) if h == "H4" else None
            out("  " + "\t".join([r["table"], r["row"]] + ([m.group(1) + "点", m.group(2)] if m else [])))
    code = 1 if bad else 0
    out(f"台帳：{os.path.relpath(path, root)}（{len(rows)} 行）。終了コード {code}")
    return code, res, rows


# ---- 段1 の道具の確かめ ----

MUTATIONS = ["セルの値を変える", "前年度の列を変える", "図形の字を変える", "label_cell を変える"]
PER_KIND = 5


class PatchedSource(Source):
    """原典の1つのセルか図形の字を差し替えて読む。"""

    def __init__(self, base, cell=None, fig=None):
        self.base = base
        self.cell = cell   # (doc, sheet, r, c, 値)
        self.fig = fig     # (doc, sheet, r, c, 字)
        self.cache = {}

    def grid(self, doc, sheet):
        g = self.base.grid(doc, sheet)
        if self.cell and self.cell[:2] == (doc, sheet):
            g = [list(x) for x in g]
            _, _, r, c, v = self.cell
            while len(g[r - 1]) < c:
                g[r - 1].append("")
            g[r - 1][c - 1] = v
        return g

    def figs(self, doc, sheet):
        fs = self.base.figs(doc, sheet)
        if self.fig and self.fig[:2] == (doc, sheet):
            _, _, r, c, v = self.fig
            fs = [dict(f, text=v) if (f["r"], f["c"]) == (r, c) else f for f in fs]
        return fs

    def at(self, doc, sheet, r, c):
        g = self.grid(doc, sheet)
        if r < 1 or r > len(g):
            return ""
        row = g[r - 1]
        return row[c - 1] if 1 <= c <= len(row) else ""


def bump_text(v):
    if re.fullmatch(r"-?[0-9]+", v or ""):
        return str(int(v) + 1)
    if re.fullmatch(r"-?[0-9]+\.[0-9]+", v or ""):
        a, b = v.split(".")
        return a + "." + str((int(b) + 1) % (10 ** len(b))).zfill(len(b)) if int(b) + 1 < 10 ** len(b) else str(int(a) + 1) + "." + "0" * len(b)
    return "9" if v != "9" else "8"


def spread(rows, n=PER_KIND):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def selftest(root=ROOT, out=print):
    tmp = tempfile.mkdtemp(prefix="kozo-zaisei-selftest-")
    try:
        if load_tables(root)["zaisei-ichi"]:
            data_root = root
            out("data/struct/ の表で確かめる（表は書き換えない）")
        else:
            import test_kozo_zaisei
            test_kozo_zaisei.make_sample(tmp)
            kian(tmp, out=lambda *a: None, src=Source(tmp))
            data_root = tmp
            out("data/struct/ に表が無いので、tools/test_kozo_zaisei.py の見本で確かめる")
        t = load_tables(data_root)
        years = [c for c in t["zaisei-shihyo:head"] if re.fullmatch(r"[hr][0-9]+", c)]
        base_src = Source(data_root)
        base = run_check(t, base_src, years)
        base_bad = {rw for f in base["fails"] for rw in f["rows"]} | {x["row"] for x in base["fig"]}

        def flagged(res, rid):
            return any(rid in f["rows"] for f in res["fails"]) or any(x["row"] == rid for x in res["fig"])

        cell_rows = [r for r in t["zaisei-ichi"] if r["src"] == "cell" and r["cell"]]
        fig_rows = [r for r in t["zaisei-ichi"] if r["src"] == "zukei" and r["cell"]]

        def rid(r):
            return f"{r['key']}/{r['col']}/{r['year']}" if r["col"] else f"{r['key']}/{r['year']}"
        total = [0] * len(MUTATIONS)
        found = [0] * len(MUTATIONS)
        missed = []
        # 1. 表のセルの値を変える
        for r in spread([r for r in cell_rows if rid(r) not in base_bad]):
            t2 = copy.deepcopy(t)
            tname = "zaisei-futsu" if r["col"] else "zaisei-shihyo"
            x = [x for x in t2[tname] if x["key"] == r["key"] and x.get("col", "") == r["col"]][0]
            x[r["year"]] = bump_text(x[r["year"]])
            total[0] += 1
            if flagged(run_check(t2, base_src, years), rid(r)):
                found[0] += 1
            else:
                missed.append(f"{MUTATIONS[0]}：{rid(r)}")
        # 2. 原典の前年度の列を変える
        sk = [r for r in cell_rows if r["sheet"] == SHEET_SOUKATSU and prev_year(r["year"], years) and rid(r) not in base_bad]
        for r in spread(sk):
            cell = parse_cell(r["cell"])
            g = base_src.grid(r["doc"], r["sheet"])
            pc = prev_column(g, cell[1], year_label(r["year"]), year_label(prev_year(r["year"], years)))
            if pc is None:
                continue
            ps = PatchedSource(base_src, cell=(r["doc"], r["sheet"], cell[0], pc, bump_text(base_src.at(r["doc"], r["sheet"], cell[0], pc))))
            total[1] += 1
            res = run_check(t, ps, years)
            if any(f["kind"] == "前年度" and rid(r) in f["rows"] for f in res["fails"]):
                found[1] += 1
            else:
                missed.append(f"{MUTATIONS[1]}：{rid(r)}")
        # 3. 原典の図形の字を変える
        for r in spread([r for r in fig_rows if rid(r) not in base_bad]):
            cell = parse_cell(r["cell"])
            f0 = [f for f in base_src.figs(r["doc"], r["sheet"]) if (f["r"], f["c"]) == cell][0]
            txt = re.sub(r"[0-9](?=[^0-9]*$)", lambda m: str((int(m.group(0)) + 1) % 10), f0["text"])
            ps = PatchedSource(base_src, fig=(r["doc"], r["sheet"], cell[0], cell[1], txt))
            total[2] += 1
            if flagged(run_check(t, ps, years), rid(r)):
                found[2] += 1
            else:
                missed.append(f"{MUTATIONS[2]}：{rid(r)}")
        # 4. label_cell を変える（1つ下の行に）
        for r in spread([r for r in t["zaisei-ichi"] if r["label_cell"] and r["cell"] and rid(r) not in base_bad]):
            t2 = copy.deepcopy(t)
            x = [x for x in t2["zaisei-ichi"] if (x["key"], x["col"], x["year"]) == (r["key"], r["col"], r["year"])][0]
            lr, lc = parse_cell(x["label_cell"])
            x["label_cell"] = cell_id(lr + 1, lc)
            total[3] += 1
            if flagged(run_check(t2, base_src, years), rid(r)):
                found[3] += 1
            else:
                missed.append(f"{MUTATIONS[3]}：{rid(r)}")
        code = 0
        out("誤りの種類\t入れた数\t見つけた数")
        for i, name in enumerate(MUTATIONS):
            out(f"  {name}\t{total[i]}\t{found[i]}")
            if total[i] == 0 or found[i] < total[i]:
                code = 1
        for m in missed:
            out("  見つけられなかった：" + m)
        out(f"終了コード {code}")
        return code
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    try:
        if argv[1] == "zaisei-kian":
            kian(ROOT)
            return 0
        if argv[1] == "zaisei-check":
            return check(ROOT)[0]
        if argv[1] == "zaisei-selftest":
            return selftest()
    except Stop as e:
        print(f"止めた：{e}", file=sys.stderr)
        return 2
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
