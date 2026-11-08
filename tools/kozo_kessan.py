"""決算の構造化（DOCS_SCHEMA.md §13.7・13.9・13.10）。標準ライブラリだけで動く。tools/kozo.py から呼ぶ。

  python3 -B tools/kozo.py kessan-kian       決算審査意見書・決算の概要・財政状況資料集のシート03 から、決算の3つの表を起票する
  python3 -B tools/kozo.py kessan-check      決算の表を原典の文字で照合し、選ばなかった出典と突き合わせて、検収の台帳 data/struct/audit/kessan.tsv を書く（段1）
  python3 -B tools/kozo.py kessan-selftest   段1 の道具に、誤りを入れた表を読ませて、誤りを見つけることを確かめる

起票は data/text_raw/ を基にする（xlsx は data/text/<id>/03.tsv）。読めない数字は、原典の画像で確かめた値（IMAGE_READ）を書き、
note に「画像で確かめた：<読んだ字>」と書く。決まりに無い会計の名前が出たら止める（終了コード 2）。
kessan-check は、合わない検算があれば一覧を出して終了コード 1 で終わる（台帳は書く）。
"""
import copy
import csv
import os
import re
import shutil
import sys
import tempfile
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402
import kozo  # noqa: E402
from kozo import Stop, join_note, row_key, write_table, struct_dir, AUDIT_COLS, ISSUE_COLS  # noqa: E402

ROOT = htd.ROOT
TODAY = kozo.TODAY

KAI_COLS = ["id", "fiscal_year", "kaikei", "name", "note"]
GAKU_COLS = ["kai", "key", "value", "unit", "raw", "doc", "page", "cell", "note"]
UCHI_COLS = ["kai", "key", "group", "name", "value", "unit", "ratio", "raw", "doc", "page", "note"]
kozo.TABLES.setdefault("kessan-kai", ("kessan-kai.tsv", KAI_COLS))
kozo.TABLES.setdefault("kessan-gaku", ("kessan-gaku.tsv", GAKU_COLS))
kozo.TABLES.setdefault("kessan-uchiwake", ("kessan-uchiwake.tsv", UCHI_COLS))

# 13.3 会計の略（kozo.KAIKEI に、決算の概要にだけ出る2会計を足したもの）
KAIKEI = kozo.KAIKEI + [("roken", "介護老人保健施設特別会計"), ("jutaku", "住宅新築資金等貸付事業特別会計")]
KIGYO = ("suido", "gesuido")
# 13.7 出典の会計の名前の字と略の対応（13.3 の決まりで当たらない字だけ）
ALIAS = {
    ("kessan-h26-gaiyou", "西山財産区特別会計"): "nishiyama",
    ("kessan-h27-gaiyou", "西山財産区特別会計"): "nishiyama",
    ("kessan-h28-gaiyou", "国民健康保険"): "kokuho",
    ("kessan-h28-gaiyou", "介護保険"): "kaigo",
    ("kessan-h28-gaiyou", "後期高齢者医療"): "koki",
    ("kessan-h28-gaiyou", "公共下水道事業"): "koshita",
    ("kessan-h28-gaiyou", "農業集落排水事業"): "noshu",
    ("kessan-h28-gaiyou", "簡易水道事業"): "kansui",
    ("kessan-h28-gaiyou", "西山財産区"): "nishiyama",
}
# 13.7 のキーの表の順
KEYS = ["sainyu", "saishutsu", "sashihiki", "jisshitsu",
        "sainyu-hoken", "saishutsu-hoken", "sashihiki-hoken", "jisshitsu-hoken",
        "sainyu-service", "saishutsu-service", "sashihiki-service", "jisshitsu-service",
        "shueki-shunyu", "shueki-shishutsu", "shihon-shunyu", "shihon-shishutsu", "soshueki", "sohiyo", "junsoneki"]
UNIT_YEN = {"円": 1, "万円": 10000, "百万円": 1000000}
# シート03 と合わないときに段2 に回す会計（13.10）
SHEET03_DAN2 = ("kansui", "koshita", "noshu")

# 原典の文字で読めない数字（13.9）。原典の画像で確かめ、取りまとめ役が決めた値。(資料, 原典の字) → 万円の値
IMAGE_READ = {("kessan-r3-gaiyou", "20億9.533万円"): 209533}
# 内訳の group を原典の画像で決めた行（13.7。平成27・28年度の2ページ）。(資料, ページ, 額の字のある行の番号) → group
IMAGE_GROUP = {
    ("kessan-h27-gaiyou", 2, 61): "mokuteki",        # その他 2億1,155万円（2.6％）：目的別の円グラフ
    ("kessan-h27-gaiyou", 2, 64): "seishitsu",       # その他 1億1,728万円（1.4％）：性質別
    ("kessan-h27-gaiyou", 2, 91): "seishitsu",       # 公債費 5億9,280万円（7.2％）：性質別（同じ字が目的別にもある）
    ("kessan-h27-gaiyou", 2, 795): "mokuteki",       # 公債費 5億9,280万円（7.2％）：目的別
    ("kessan-h27-gaiyou", 2, 789): "seishitsu",      # 投資的経費 8億5,691万円：性質別の外側（内側の輪は無い）
    ("kessan-h28-gaiyou", 2, 3): "mokuteki",         # その他 2億1,217万円（2.5％）
    ("kessan-h28-gaiyou", 2, 5): "seishitsu",        # その他 6,040万円（0.7％）
    ("kessan-h28-gaiyou", 2, 81): "seishitsu",       # 公債費 5億9,197万円（6.8％）：性質別
    ("kessan-h28-gaiyou", 2, 84): "mokuteki",        # 公債費 5億9,197万円（6.9％）：目的別
    ("kessan-h28-gaiyou", 2, 51): "seishitsu-naka",  # 投資的 経費 12億2,664万円：性質別の内側の輪（小さい字）
    ("kessan-h28-gaiyou", 2, 75): "seishitsu",       # 投資的経費 12億2,664万円：性質別の外側
}
IMAGE_GROUP_NOTE = "group は原典の画像で決めた"
DAN2_NOTE = "段2 に回す"

MOKUTEKI = {"議会費", "総務費", "民生費", "衛生費", "労働費", "農林水産業費", "商工費", "土木費", "消防費", "教育費", "災害復旧費",
            "公債費", "諸支出金"}
SEISHITSU = {"人件費", "扶助費", "公債費", "物件費", "維持補修費", "維持補修経費", "補助費", "補助費等", "普通建設事業費", "災害復旧費",
             "繰出金", "積立金", "その他経費", "投資的経費"}
SEISHITSU_NAKA = {"義務的経費", "一般行政経費", "その他の経費", "投資的経費"}
SAINYU_NAKA = {"自主財源", "依存財源"}
JOIN_HEAD = {"普通建設", "分担金及び", "投資的"}   # 項目の名前が2行に分かれるときの前の行の字
NOT_ITEM = {"前年度比"}


# ---- 年度と資料 ----

def yinfo(y):
    m = re.fullmatch(r"([hr])([0-9]+)", y)
    if not m:
        raise Stop(f"年度は r6・h27 の形で書く（{y}）")
    n = int(m.group(2))
    return 1988 + n if m.group(1) == "h" else 2018 + n


def yorder(y):
    return yinfo(y)


def yname(fy):
    return f"h{fy - 1988}" if fy <= 2018 else f"r{fy - 2018}"


def abbr_of(doc, name):
    """会計の名前の字から略。13.3 の決まり（空白とかっこ書きを除いて同じ）か、13.7 の対応の表。どちらでもなければ None。"""
    n = re.sub(r"\([^)]*\)", "", kozo.norm(name))
    for a, full in KAIKEI:
        if n == full:
            return a
    return ALIAS.get((doc, n))


# ---- 数の読み ----

def read_yen(tok):
    """円の額の字（「11,091,156,343」「△ 743,343」）を整数に。"""
    return int(kozo.read_num(tok, integer=True))


def read_man(raw):
    """億・万の字の額を万円の整数に（13.5）。「110 億 9,116 万円」→1109116、「３億８千５６５万円」→38565。
    読めなければ ValueError。"""
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw))
    m = re.fullmatch(r"(?:([0-9]+)億)?(?:([0-9])千)?([0-9]{1,3}(?:,[0-9]{3})*|[0-9]*)万円", s)
    if not m or not any(m.groups()) or (m.group(2) and "," in m.group(3)):
        raise ValueError(raw)
    oku = int(m.group(1) or 0)
    sen = int(m.group(2) or 0)
    rest = int(m.group(3).replace(",", "") or 0)
    if sen and rest >= 1000:
        raise ValueError(raw)
    if rest >= 10000:
        raise ValueError(raw)
    return oku * 10000 + sen * 1000 + rest


def flat(s):
    return re.sub(r"\s+", "", s)


AMT = r"(?:[0-9０-９]+億[0-9０-９,，千.]*万円|[0-9０-９][0-9０-９,，千]*万円)"
DOTS = r"[…・･:：]*"


# ---- 原典の文字 ----

class Src:
    """原典の文字を読む（同じファイルは1回だけ）。"""

    def __init__(self, root):
        self.root = root
        self.t = kozo.Texts(root)
        self.x = {}

    def pages(self, doc):
        return self.t.raw(doc)

    def flatpage(self, doc, page):
        return flat("".join(self.t.page("raw", doc, page)))

    def sheet(self, doc):
        if doc not in self.x:
            p = os.path.join(self.root, "data", "text", doc, "03.tsv")
            rows = []
            if os.path.exists(p):
                with open(p, encoding="utf-8", newline="") as f:
                    rows = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
            self.x[doc] = rows
        return self.x[doc]

    def cell(self, doc, r, c):
        rows = self.sheet(doc)
        if 1 <= r <= len(rows) and 1 <= c <= len(rows[r - 1]):
            return rows[r - 1][c - 1]
        return ""


def cand(kai_y, abbr, key, value, unit, raw, doc, page, cell="", note="", name=""):
    return dict(y=kai_y, abbr=abbr, key=key, value=value, unit=unit, raw=raw, doc=doc, page=str(page), cell=cell,
                note=note, name=name)


# ---- 決算審査意見書 ----

N = r"(△?\s*[0-9][0-9,]*)"
IK_HEAD = re.compile(r"^[0-9０-９]+[．.]\s*(\S+会計)$")
IK_ROW = {
    "sainyu": re.compile(r"^(?:[決算額]\s+)?歳入総額\s+" + N + r"\s+" + N),
    "saishutsu": re.compile(r"^(?:[決算額]\s+)?歳出総額\s+" + N + r"\s+" + N),
    "sashihiki": re.compile(r"^(?:[決算額]\s+)?差\s*引\s*額\s+" + N + r"\s+" + N),
}
IK_KIGYO = {
    "shueki-shunyu": (re.compile(r"^(?:水道|下水道)事業収益\s*合計\s+" + N + r"\s+" + N),
                      re.compile(r"^前年度(?:水道|下水道)事業収益\s+" + N + r"\s+" + N)),
    "shueki-shishutsu": (re.compile(r"^(?:水道|下水道)事業費用\s*合計\s+" + N + r"\s+" + N),
                         re.compile(r"^前年度(?:水道|下水道)事業費用\s+" + N + r"\s+" + N)),
    "shihon-shunyu": (re.compile(r"^資本的収入\s*合計\s+" + N + r"\s+" + N),
                      re.compile(r"^前年度資本的収入\s+" + N + r"\s+" + N)),
    "shihon-shishutsu": (re.compile(r"^資本的支出\s*合計\s+" + N + r"\s+" + N),
                         re.compile(r"^前年度資本的支出\s+" + N + r"\s+" + N)),
}
IK_JIS = re.compile(r"実質収支額([0-9][0-9,]*)円から前年度実質収支額([0-9][0-9,]*)円")


def read_ikensho(src, y):
    """意見書から (当年度の候補, 前年度の候補)。前年度の候補は r3 の意見書のときだけ使う。"""
    doc = f"kessan-{y}-ikensho"
    pages = src.pages(doc)
    if not pages:
        return [], []
    py = yname(yinfo(y) - 1)
    secs = []   # (abbr, kanjo, name, [(page, line)])
    cur = None
    for pi, lines in enumerate(pages, 1):
        for ln in lines:
            m = IK_HEAD.match(ln)
            if m:
                a = abbr_of(doc, m.group(1))
                if a is None:
                    raise Stop(f"{doc} p.{pi}：13.3 に無い会計「{m.group(1)}」")
                cur = [a, "", m.group(1), []]
                secs.append(cur)
                continue
            if cur and ln in ("保険事業勘定", "介護サービス事業勘定"):
                if cur[1] or cur[3]:
                    cur = [cur[0], "", cur[2], []]
                    secs.append(cur)
                cur[1] = "hoken" if ln == "保険事業勘定" else "service"
                continue
            if cur:
                cur[3].append((pi, ln))
    now, prev = [], []
    for a, kanjo, name, lines in secs:
        suf = ("-" + kanjo) if kanjo else ""
        if a == "kaigo" and not kanjo:
            raise Stop(f"{doc}：介護保険特別会計の勘定の見出しが見つからない")
        found = set()
        if a in KIGYO:
            for key, (rc, rp) in IK_KIGYO.items():
                for pi, ln in lines:
                    m = rc.match(ln)
                    if m and key not in found:
                        found.add(key)
                        now.append(cand(y, a, key, read_yen(m.group(2)), "円", m.group(2), doc, pi, name=name))
                    m = rp.match(ln)
                    if m and key + "@prev" not in found:
                        found.add(key + "@prev")
                        prev.append(cand(py, a, key, read_yen(m.group(2)), "円", m.group(2), doc, pi,
                                         note="r3 意見書の前年度の列" if y == "r3" else "", name=name))
        else:
            for key, rc in IK_ROW.items():
                for pi, ln in lines:
                    m = rc.match(ln)
                    if m:
                        now.append(cand(y, a, key + suf, read_yen(m.group(1)), "円", m.group(1), doc, pi, name=name))
                        prev.append(cand(py, a, key + suf, read_yen(m.group(2)), "円", m.group(2), doc, pi,
                                         note="r3 意見書の前年度の列", name=name))
                        found.add(key)
                        break
            byp = {}
            for pi, ln in lines:
                byp.setdefault(pi, []).append(ln)
            for pi in sorted(byp):
                m = IK_JIS.search(flat("".join(byp[pi])))
                if m:
                    now.append(cand(y, a, "jisshitsu" + suf, read_yen(m.group(1)), "円", m.group(1), doc, pi, name=name))
                    prev.append(cand(py, a, "jisshitsu" + suf, read_yen(m.group(2)), "円", m.group(2), doc, pi,
                                     note="r3 意見書の前年度の列", name=name))
                    found.add("jisshitsu")
                    break
            miss = {"sainyu", "saishutsu", "sashihiki", "jisshitsu"} - found
            if miss:
                raise Stop(f"{doc}：{name}{('（' + kanjo + '）') if kanjo else ''} の {'・'.join(sorted(miss))} が見つからない")
    return now, prev


# ---- 決算の概要 ----

HEAD_G = re.compile(r"[●・]([^●・。、]{2,24}?(?:特別会計|事業会計|財産区会計))(?![やへのに])")


def gaiyou_flat(src, doc):
    """ページの字をつないだもの（空白と改行を除く）と、位置からページを引く表。"""
    pages = src.pages(doc)
    parts, starts = [], []
    pos = 0
    for lines in pages:
        s = flat("".join(lines))
        starts.append(pos)
        parts.append(s)
        pos += len(s)
    whole = "".join(parts)

    def page_of(i):
        p = 1
        for k, st in enumerate(starts, 1):
            if i >= st:
                p = k
        return p
    return whole, page_of


def gaiyou_man(raw, doc):
    if (doc, raw) in IMAGE_READ:
        return IMAGE_READ[(doc, raw)], f"画像で確かめた：{raw}"
    return read_man(raw), ""


def read_gaiyou(src, y):
    """概要から額の候補（万円）。"""
    doc = f"kessan-{y}-gaiyou"
    if not src.pages(doc):
        return []
    whole, page_of = gaiyou_flat(src, doc)
    out = []

    def add(a, key, raw, i, name):
        v, note = gaiyou_man(raw, doc)
        out.append(cand(y, a, key, v, "万円", raw, doc, page_of(i), note=note, name=name))

    # 一般会計
    if y.startswith("h"):
        for key, pat in (("sainyu", "歳入が"), ("saishutsu", "歳出が"), ("sashihiki", "差引(?:の|した)"),
                         ("jisshitsu", "実質収支は")):
            m = re.search(pat + "(" + AMT + ")", whole)
            if m:
                add("ippan", key, m.group(1), m.start(1), "一般会計")
    else:
        for key, pat in (("sainyu", "一般会計歳入"), ("saishutsu", "一般会計歳出")):
            m = re.search(pat + "(" + AMT + ")", whole)
            if m:
                add("ippan", key, m.group(1), m.start(1), "一般会計")
    # 特別会計・企業会計（平成24〜27年度の「●○○会計 … 歳入…」の形）
    heads = []
    for m in (HEAD_G.finditer(whole) if y in ("h24", "h25", "h26", "h27") else ()):
        nm = m.group(1)
        if nm.startswith("他にも") or re.search(r"[0-9０-９：:]", nm):
            continue
        a = abbr_of(doc, nm)
        if a is None:
            raise Stop(f"{doc} p.{page_of(m.start())}：13.3 に無い会計「{nm}」")
        heads.append((m.end(), a, nm))
    for k, (st, a, nm) in enumerate(heads):
        en = heads[k + 1][0] - len(heads[k + 1][2]) - 1 if k + 1 < len(heads) else len(whole)
        reg = whole[st:en]
        if a in KIGYO:
            pairs = []
            for m in re.finditer(r"（(収益的|資本的)(?:収支|支出)）", reg):
                pairs.append((m.group(1), m.end()))
            for n, (kind, p0) in enumerate(pairs):
                p1 = pairs[n + 1][1] if n + 1 < len(pairs) else len(reg)
                sub = reg[p0:p1]
                pre = "shueki" if kind == "収益的" else "shihon"
                for key, w in ((pre + "-shunyu", "収入"), (pre + "-shishutsu", "支出")):
                    m = re.search(w + DOTS + "(" + AMT + ")", sub)
                    if m:
                        add(a, key, m.group(1), st + p0 + m.start(1), nm)
        else:
            for key, w in (("sainyu", "歳入"), ("saishutsu", "歳出")):
                m = re.search(w + DOTS + "(" + AMT + ")", reg)
                if m:
                    add(a, key, m.group(1), st + m.start(1), nm)
    # 平成28年度の形（国保・介護の「歳入額：」、その他の特別会計の表、水道の表）
    if y == "h28":
        for nm in ("国民健康保険", "介護保険"):
            m = re.search(nm + "歳入額：(" + AMT + ")歳出額：(" + AMT + ")差引額：(" + AMT + ")", whole)
            if m:
                for g, key in ((1, "sainyu"), (2, "saishutsu"), (3, "sashihiki")):
                    add(abbr_of(doc, nm), key, m.group(g), m.start(g), nm)
        i0 = whole.find("会計区分歳入額歳出額差引額")
        if i0 >= 0:
            seg = whole[i0:whole.find("事業収益", i0)]
            for m in re.finditer(r"([^\d万円億]+?)(" + AMT + ")(" + AMT + ")(" + AMT + ")", seg):
                nm = m.group(1).replace("会計区分歳入額歳出額差引額", "")
                a = abbr_of(doc, nm)
                if a is None:
                    raise Stop(f"{doc}：表の会計「{nm}」が 13.3 にも 13.7 の対応の表にも無い")
                for g, key in ((2, "sainyu"), (3, "saishutsu"), (4, "sashihiki")):
                    add(a, key, m.group(g), i0 + m.start(g), nm)
        for key, w in (("soshueki", "事業収益"), ("sohiyo", "事業費用"), ("shihon-shunyu", "資本的収入"),
                       ("shihon-shishutsu", "資本的支出")):
            m = re.search(w + "(" + AMT + ")", whole)
            if m:
                add("suido", key, m.group(1), m.start(1), "水道事業会計")
    return out


# ---- 一般会計の内訳（概要の円グラフ） ----

AMT_LINE = re.compile(r"^([0-9０-９]+\s*億\s*)?[0-9０-９][0-9０-９,，.\s]*万円$")
OKU_LINE = re.compile(r"^[0-9０-９]+\s*億$")
NAME_AMT = re.compile(r"^(\S+?)\s*((?:[0-9０-９]+\s*億\s*)?[0-9０-９][0-9０-９,，.\s]*万円)$")
NAME_AMT_PC = re.compile(r"^(\S+?)\s*((?:[0-9０-９]+\s*億\s*)?[0-9０-９][0-9０-９,，.\s]*万円)\s*[（(]?\s*([0-9.]+)\s*[%％]\s*[）)]?$")
PC_LINE = re.compile(r"^[（(]?\s*([0-9.]+)\s*[%％]\s*[）)]?(?:\s+\S+)?$")
PC_HEAD = re.compile(r"^[0-9.]+\s*[%％]\s+")


def group_of(doc, page, line, name):
    """(group, 画像で決めたか)。決まらなければ (None, False)。"""
    g = IMAGE_GROUP.get((doc, page, line))
    if g:
        return g, True
    if page == 1:
        return ("sainyu-naka" if name in SAINYU_NAKA else "sainyu"), False
    y = doc.split("-")[1]
    if y in ("h24", "h25", "h26"):
        return ("mokuteki" if name in MOKUTEKI or name == "その他" else None), False   # 歳出は目的別の円グラフだけ
    if y.startswith("r"):   # 令和2〜6年度は性質別の円グラフだけ（内側の輪あり）
        if name in SEISHITSU_NAKA:
            return "seishitsu-naka", False
        return ("seishitsu" if name in SEISHITSU else None), False
    if name in SEISHITSU_NAKA and name not in SEISHITSU:
        return "seishitsu-naka", False
    m, s = name in MOKUTEKI, name in SEISHITSU
    if m and not s:
        return "mokuteki", False
    if s and not m:
        return "seishitsu", False
    return None, False


def read_uchiwake(src, y):
    """概要の円グラフの項目。[(group, name, raw, ratio, page, line, 出た回数, 画像で決めた group か)]"""
    doc = f"kessan-{y}-gaiyou"
    pages = src.pages(doc)
    out = []
    for pi in (1, 2):
        if pi > len(pages):
            continue
        L = pages[pi - 1]
        found = []
        for j, ln in enumerate(L):
            nm = raw = pc = None
            m = NAME_AMT_PC.match(ln)
            if m and not AMT_LINE.match(ln):
                nm, raw, pc = m.group(1), m.group(2), m.group(3)
            elif j + 1 < len(L) and PC_LINE.match(L[j + 1]):
                pc = PC_LINE.match(L[j + 1]).group(1)
                if AMT_LINE.match(ln) and j > 0:
                    raw, k = ln, j - 1
                    if OKU_LINE.match(L[k]):
                        raw, k = L[k] + raw, k - 1
                    nm = L[k] if k >= 0 else ""
                    if k > 0 and L[k - 1] in JOIN_HEAD:
                        nm = L[k - 1] + nm
                else:
                    m = NAME_AMT.match(ln)
                    if m and not AMT_LINE.match(ln):
                        nm, raw = m.group(1), m.group(2)
            if nm is None:
                continue
            nm = PC_HEAD.sub("", nm).strip()
            if nm in NOT_ITEM or not nm or re.search(r"[0-9]", nm):
                continue
            found.append((nm, raw, pc, j + 1))
        seen = {}
        for nm, raw, pc, line in found:
            g, img = group_of(doc, pi, line, nm)
            if g is None:
                raise Stop(f"{doc} p.{pi} {line}行：「{nm}」の group が名前で決まらず、IMAGE_GROUP にも無い")
            k = (g, nm, flat(raw), pc)
            if k in seen:
                seen[k][6] += 1
                continue
            seen[k] = [g, nm, raw, pc, pi, line, 1, img]
            out.append(seen[k])
    return [tuple(x) for x in out]


# ---- 財政状況資料集のシート03 ----

S03_IPPAN = {17: "sainyu", 22: "saishutsu", 27: "sashihiki", 32: "jisshitsu"}
S03_TOKU = {17: "sainyu", 22: "saishutsu", 27: "sashihiki", 32: "jisshitsu"}
S03_KIGYO = {17: "soshueki", 22: "sohiyo", 27: "junsoneki"}
S03_HEAD = {("ippan", 17): "歳入", ("ippan", 22): "歳出", ("ippan", 27): "形式収支", ("ippan", 32): "実質収支",
            ("koei", 17): "総収益(歳入)", ("koei", 22): "総費用(歳出)", ("koei", 27): "純損益(形式収支)",
            ("koei", 32): "資金剰余額/不足額(実質収支)"}


def read_sheet03(src, y):
    doc = f"zaisei-{y}-shiryoshu"
    rows = src.sheet(doc)
    if not rows:
        return []
    out = []

    def add(r, c, a, key, name):
        v = src.cell(doc, r, c)
        if re.fullmatch(r"-?[0-9]+", v):
            out.append(cand(y, a, key, int(v), "百万円", "", doc, "03", f"R{r}C{c}", name=name))

    if kozo.norm(src.cell(doc, 7, 2)) == "一般会計":
        for c, key in S03_IPPAN.items():
            add(7, c, "ippan", key, src.cell(doc, 7, 2))
    for r in range(28, len(rows) + 1):
        nm = src.cell(doc, r, 2)
        if not src.cell(doc, r, 1).strip().isdigit():
            break   # 通し番号の行が終わったら（「計」の行の前）止める
        if not nm:
            continue
        a = abbr_of(doc, nm)
        if a is None:
            raise Stop(f"{doc} 03 R{r}：13.3 に無い会計「{nm}」")
        for c, key in (S03_KIGYO if a in KIGYO else S03_TOKU).items():
            add(r, c, a, key, nm)
    return out


def sheet03_label_ok(src, doc, cell):
    """セルの行の会計名と列の見出しの字が、その位置の項目に合うか。"""
    m = re.fullmatch(r"R([0-9]+)C([0-9]+)", cell)
    r, c = int(m.group(1)), int(m.group(2))
    sect = "ippan" if r == 7 else "koei"
    hr = 5 if sect == "ippan" else 26
    want = S03_HEAD.get((sect, c))
    return want is not None and kozo.norm(src.cell(doc, hr, c)) == want


# ---- 候補を集め、出典を選ぶ ----

YEARS_GAIYOU = ["h24", "h25", "h26", "h27", "h28", "r2", "r3", "r4", "r5", "r6"]
YEARS_IKENSHO = ["r3", "r4", "r5", "r6", "r7"]
YEARS_S03 = ["h27", "h28", "h29", "h30", "r1", "r2", "r3", "r4", "r5", "r6"]
RANK = {"ikensho": 0, "ikensho-prev": 1, "gaiyou": 2, "sheet03": 3}


def present(src, years, fmt, sheet=False):
    """原典の文字のある年度だけ（検査の見本では一部の年度しか無い）。"""
    if sheet:
        return [y for y in years if src.sheet(fmt.format(y))]
    return [y for y in years if src.pages(fmt.format(y))]


def collect(src):
    """{(年度, 略, key): [(出典の種類, 候補)]}。出典の種類の順は 13.7 の出典の選び方の順。"""
    allc = {}

    def put(kind, cs):
        for c in cs:
            allc.setdefault((c["y"], c["abbr"], c["key"]), []).append((kind, c))
    for y in present(src, YEARS_IKENSHO, "kessan-{}-ikensho"):
        now, prev = read_ikensho(src, y)
        put("ikensho", now)
        if y == "r3":
            put("ikensho-prev", prev)
    for y in present(src, YEARS_GAIYOU, "kessan-{}-gaiyou"):
        put("gaiyou", read_gaiyou(src, y))
    for y in present(src, YEARS_S03, "zaisei-{}-shiryoshu", sheet=True):
        put("sheet03", read_sheet03(src, y))
    for k in allc:
        allc[k].sort(key=lambda kc: RANK[kc[0]])
    return allc


def split_kaigo(allc):
    """意見書のある年度の介護保険は勘定ごとのキーで持つので、勘定に分けない値（シート03）は突き合わせだけに使う。"""
    years = {k[0] for k in allc if k[1] == "kaigo" and k[2].endswith(("-hoken", "-service"))}
    return {k for k in allc if k[1] == "kaigo" and k[0] in years and not k[2].endswith(("-hoken", "-service"))}


def choose(allc):
    """出典を選んだ行の並び（年度・略・key）。"""
    skip = split_kaigo(allc)
    out = {}
    for k, cs in allc.items():
        if k in skip:
            continue
        out[k] = cs[0]
    return out


# ---- 起票 ----

def kai_id(y, a):
    return f"{y}-{a}"


def kian(root=ROOT, out=print):
    src = Src(root)
    allc = collect(src)
    chosen = choose(allc)
    kais = {}
    for (y, a, key), (kind, c) in chosen.items():
        kid = kai_id(y, a)
        if kid not in kais:
            kais[kid] = dict(id=kid, fiscal_year=str(yinfo(y)), kaikei=a, name="", note="", _rank=9)
    # name は額の行の主な出典の字（いちばん前の出典の種類の行の会計名）
    for (y, a, key), (kind, c) in sorted(chosen.items(), key=lambda kv: (RANK[kv[1][0]], KEYS.index(kv[0][2]))):
        k = kais[kai_id(y, a)]
        if RANK[kind] < k["_rank"]:
            k["name"], k["_rank"] = c["name"], RANK[kind]
    gaku = []
    for (y, a, key), (kind, c) in chosen.items():
        gaku.append(dict(kai=kai_id(y, a), key=key, value=str(c["value"]), unit=c["unit"], raw=c["raw"], doc=c["doc"],
                         page=c["page"], cell=c["cell"], note=c["note"]))
    uchi = []
    for y in present(src, YEARS_GAIYOU, "kessan-{}-gaiyou"):
        doc = f"kessan-{y}-gaiyou"
        n = 0
        for g, nm, raw, pc, pi, line, times, img in read_uchiwake(src, y):
            n += 1
            v, note = gaiyou_man(raw, doc)
            if img:
                note = join_note(note, IMAGE_GROUP_NOTE)
            uchi.append(dict(kai=kai_id(y, "ippan"), key=f"u{n:03d}", group=g, name=nm, value=str(v), unit="万円",
                             ratio=pc or "", raw=raw, doc=doc, page=str(pi), note=note))
    sd = struct_dir(root)
    os.makedirs(sd, exist_ok=True)
    report = []
    kai_rows = merge_kai(kozo.load_table(root, "kessan-kai"), list(kais.values()))
    gaku = merge_gaku(kozo.load_table(root, "kessan-gaku"), gaku, report)
    uchi = merge_uchi(kozo.load_table(root, "kessan-uchiwake"), uchi, report)
    kai_rows.sort(key=lambda r: r["id"])
    order = {k["id"]: i for i, k in enumerate(sorted(kai_rows, key=lambda r: r["id"]))}
    gaku.sort(key=lambda r: (order.get(r["kai"], 9999), KEYS.index(r["key"]) if r["key"] in KEYS else 99))
    uchi.sort(key=lambda r: (order.get(r["kai"], 9999), r["key"]))
    write_table(os.path.join(sd, "kessan-kai.tsv"), KAI_COLS, kai_rows)
    write_table(os.path.join(sd, "kessan-gaku.tsv"), GAKU_COLS, gaku)
    write_table(os.path.join(sd, "kessan-uchiwake.tsv"), UCHI_COLS, uchi)
    out(f"回 {len(kai_rows)}・額 {len(gaku)}・内訳 {len(uchi)}")
    for x in report:
        out("  " + x)
    noted = [r for r in gaku + uchi if r["note"]]
    out(f"note を付けた行 {len(noted)}")
    for r in noted:
        out(f"  {r['kai']}/{r['key']}\t{r['note']}")
    return kai_rows, gaku, uchi


def human_row(r):
    return r["note"].startswith("画像で確かめた") or IMAGE_GROUP_NOTE in r["note"]


def merge_kai(old, new):
    o = {r["id"]: r for r in old}
    out = []
    for r in new:
        x = {c: r[c] for c in KAI_COLS}
        if r["id"] in o and o[r["id"]]["note"]:
            x["note"] = o[r["id"]]["note"]
        out.append(x)
    ids = {r["id"] for r in new}
    out += [r for r in old if r["id"] not in ids]
    return out


def merge_gaku(old, new, report):
    """既存の行の値と note は保つ（人が書いた行。画像で確かめた行）。読みに無い既存の行は残して報告する。"""
    o = {(r["kai"], r["key"]): r for r in old}
    out = []
    for r in new:
        p = o.pop((r["kai"], r["key"]), None)
        if p and human_row(p):
            r = dict(r, value=p["value"], note=p["note"])
        out.append(r)
    for r in o.values():
        report.append(f"今回の読みに無い既存の額の行を残した：{r['kai']}/{r['key']}")
        out.append(r)
    return out


def merge_uchi(old, new, report):
    """既存の行と (kai, group, name, raw) で合わせて key を保つ。合わない新しい行はその回の最大の番号の次。"""
    def mk(r):
        return (r["kai"], r["group"], r["name"], flat(r["raw"]))
    o = {mk(r): r for r in old}
    mx = {}
    for r in old:
        mx[r["kai"]] = max(mx.get(r["kai"], 0), int(r["key"][1:]))
    out, fresh = [], []
    for r in new:
        p = o.pop(mk(r), None)
        if p:
            r = dict(r, key=p["key"])
            if human_row(p):
                r = dict(r, value=p["value"], note=p["note"])
            out.append(r)
        else:
            fresh.append(r)
    for r in fresh:
        if r["kai"] in mx:
            mx[r["kai"]] += 1
            r = dict(r, key=f"u{mx[r['kai']]:03d}")
        out.append(r)
    for r in o.values():
        report.append(f"今回の読みに無い既存の内訳の行を残した：{r['kai']}/{r['key']} {r['name']}")
        out.append(r)
    return out


# ---- 段1 ----

def within(a, ua, b, ub):
    """単位の丸めの範囲（粗いほうの単位で差の絶対値が1未満）。(合うか, 粗いほうの単位での差)"""
    coarse = max(UNIT_YEN[ua], UNIT_YEN[ub])
    d = (a * UNIT_YEN[ua] - b * UNIT_YEN[ub]) / coarse
    return abs(d) < 1, d


def fmt(d):
    s = f"{abs(d):.2f}".rstrip("0").rstrip(".")
    return s


def others_for(allc, y, a, key, chosen_kind):
    """選ばなかった出典の同じ項目の値（突き合わせの相手）。[(種類, 値, 単位, 説明)]"""
    out = []
    for kind, c in allc.get((y, a, key), []):
        if kind == chosen_kind:
            continue
        if a == "ippan" and kind == "sheet03":
            continue   # 13.10：一般会計の行はシート03 と突き合わせない
        out.append((kind, c["value"], c["unit"], c["doc"]))
    if a == "kaigo" and key.endswith(("-hoken", "-service")):
        base = key.rsplit("-", 1)[0]
        other = base + ("-service" if key.endswith("-hoken") else "-hoken")
        mine = [c for k2, c in allc.get((y, a, key), []) if k2 == chosen_kind]
        pair = [c for k2, c in allc.get((y, a, other), []) if k2 == chosen_kind]
        for kind, c in allc.get((y, a, base), []):
            if kind == "sheet03" and mine and pair:
                out.append(("sheet03-wa", None, c["unit"], c["doc"], mine[0]["value"] + pair[0]["value"], c["value"]))
    return out


def check(t, src, allc, out=print):
    """段1。戻り値 dict(rows={(table,row): 結果}, fails=[...])。結果は ok・dan2・reasons・approx・times。"""
    res = {}
    fails = []

    def fail(table, rw, kind, msg):
        fails.append(dict(table=table, row=rw, kind=kind, msg=msg))
        res[(table, rw)]["ok"] = False

    for r in t["kessan-gaku"]:
        rw = f"{r['kai']}/{r['key']}"
        st = res[("kessan-gaku", rw)] = dict(ok=True, dan2=[], notes=[], approx=False, oku=False, times=1)
        if not r["doc"] or not r["page"]:
            fail("kessan-gaku", rw, "出典", "出典の資料やページが無い")
            continue
        if r["cell"]:
            v = src.cell(r["doc"], *map(int, re.fullmatch(r"R([0-9]+)C([0-9]+)", r["cell"]).groups()))
            if v != r["value"]:
                fail("kessan-gaku", rw, "値", f"{r['doc']} {r['cell']} の値「{v}」と value「{r['value']}」が違う")
            if not sheet03_label_ok(src, r["doc"], r["cell"]):
                fail("kessan-gaku", rw, "見出し", f"{r['doc']} {r['cell']} の列の見出しの字が項目と合わない")
            rr = int(r["cell"][1:].split("C")[0])
            nm = src.cell(r["doc"], rr, 2)
            if abbr_of(r["doc"], nm) != r["kai"].split("-", 1)[1]:
                fail("kessan-gaku", rw, "見出し", f"{r['doc']} R{rr} の会計名「{nm}」が回の会計と合わない")
        else:
            page = src.flatpage(r["doc"], r["page"])
            if not r["raw"] or flat(r["raw"]) not in page:
                fail("kessan-gaku", rw, "raw", f"raw「{r['raw']}」が {r['doc']} p.{r['page']} の原典の文字に無い")
            if r["note"].startswith("画像で確かめた"):
                st["dan2"].append("画像で確かめた数字")
            else:
                try:
                    v = read_yen(r["raw"]) if r["unit"] == "円" else read_man(r["raw"])
                    if str(v) != r["value"]:
                        fail("kessan-gaku", rw, "値", f"raw「{r['raw']}」の読み {v} と value {r['value']} が違う")
                except ValueError:
                    fail("kessan-gaku", rw, "値", f"raw「{r['raw']}」が読めない")
            if r["unit"] == "万円" and re.search(r"[億万]", r["raw"]):
                st["oku"] = True
        y, a = r["kai"].split("-", 1)
        kind = "sheet03" if r["cell"] else (
            "ikensho-prev" if "前年度の列" in r["note"] else ("ikensho" if "ikensho" in r["doc"] else "gaiyou"))
        try:
            mine = int(r["value"])
        except ValueError:
            continue
        for o in others_for(allc, y, a, r["key"], kind):
            if o[0] == "sheet03-wa":
                # 介護保険の2つの勘定の和とシート03（和は選んだ出典の読みで出す）
                wa, sv = o[4], o[5]
                ok, d = within(wa, "円", sv, o[2])
                lab = f"2つの勘定の和とシート03（{o[3]}）"
                st["approx"] = st["approx"] or d != 0
            else:
                ok, d = within(mine, r["unit"], o[1], o[2])
                lab = f"{o[0]}（{o[3]}）"
                st["approx"] = st["approx"] or d != 0
            if not ok:
                if o[0].startswith("sheet03") and a in SHEET03_DAN2:
                    st["dan2"].append(f"シート03 と {fmt(d)} 百万円違う")
                else:
                    fail("kessan-gaku", rw, "別の出典", f"{lab} と丸めの範囲で合わない（差 {fmt(d)}）")
    for r in t["kessan-uchiwake"]:
        rw = f"{r['kai']}/{r['key']}"
        st = res[("kessan-uchiwake", rw)] = dict(ok=True, dan2=[], notes=[], approx=False, oku=True, times=1)
        if not r["doc"] or not r["page"]:
            fail("kessan-uchiwake", rw, "出典", "出典の資料やページが無い")
            continue
        page = src.flatpage(r["doc"], r["page"])
        if flat(r["raw"]) not in page:
            fail("kessan-uchiwake", rw, "raw", f"raw「{r['raw']}」が {r['doc']} p.{r['page']} の原典の文字に無い")
        if flat(r["name"]) not in page:
            fail("kessan-uchiwake", rw, "name", f"name「{r['name']}」が {r['doc']} p.{r['page']} の原典の文字に無い")
        st["times"] = page.count(flat(r["name"]) + flat(r["raw"])) if r["name"] else 1
        if r["ratio"] and not re.search(re.escape(r["ratio"]) + "[%％]", page):
            fail("kessan-uchiwake", rw, "ratio", f"ratio「{r['ratio']}」が {r['doc']} p.{r['page']} の原典の文字に無い")
        if r["note"].startswith("画像で確かめた"):
            st["dan2"].append("画像で確かめた数字")
        else:
            try:
                if str(read_man(r["raw"])) != r["value"]:
                    fail("kessan-uchiwake", rw, "値", f"raw「{r['raw']}」の読みと value {r['value']} が違う")
            except ValueError:
                fail("kessan-uchiwake", rw, "値", f"raw「{r['raw']}」が読めない")
        if IMAGE_GROUP_NOTE in r["note"]:
            st["dan2"].append(IMAGE_GROUP_NOTE)
    return dict(rows=res, fails=fails)


def difficulty(table, r, st):
    pts, why = 0, []
    if st["dan2"]:
        pts += 3
        why.append("段2 に回った")
    if st["oku"]:
        pts += 2
        why.append("億・万の字を万円に読んだ")
    if st["approx"]:
        pts += 1
        why.append("別の出典と丸めの範囲でしか合わない")
    v = r["value"]
    if re.fullmatch(r"-?[0-9]", v):
        pts += 1
        why.append("値が0か1桁")
    elif st["times"] >= 2:
        pts += 1
        why.append("円グラフの値が文字に2回出る")
    return pts, why


def audit(t, res, old_ledger, today=TODAY):
    marks = {}
    for table in ("kessan-gaku", "kessan-uchiwake"):
        groups = {}
        for r in t[table]:
            rw = f"{r['kai']}/{r['key']}"
            if not r["doc"] or not r["page"]:
                marks[(table, rw)] = kozo.H2_NOTE
            groups.setdefault(r["kai"], []).append(r)
        for kai, rs in sorted(groups.items()):
            best = None
            for r in rs:
                rw = f"{r['kai']}/{r['key']}"
                pts, why = difficulty(table, r, res["rows"][(table, rw)])
                if best is None or pts > best[0] or (pts == best[0] and rw < best[1]):
                    best = (pts, rw, why)
            pts, rw, why = best
            marks[(table, rw)] = join_note(marks.get((table, rw), ""),
                                           f"要人確認:H4 {pts}点" + (" " + "・".join(why) if why else ""))
    old = {r["key"]: r for r in old_ledger}
    out = []
    stats = {}
    for table, cols in (("kessan-gaku", ["value"]), ("kessan-uchiwake", ["name", "value"])):
        st_t = stats.setdefault(table, dict(ok=0, ng=0, dan2=0))
        for r in t[table]:
            rw = f"{r['kai']}/{r['key']}"
            st = res["rows"][(table, rw)]
            value = "|".join(r[c] for c in cols)
            if r.get("cell"):
                source = f"{r['doc']} {r['page']}!{r['cell']}"
            else:
                source = f"{r['doc']} p.{r['page']}"
            key = row_key(table, rw, ",".join(cols), value, r["unit"], source)
            notes = []
            if not st["ok"]:
                notes.append("段1 不一致：" + "・".join(sorted({f["kind"] for f in res["fails"] if f["row"] == rw
                                                                and f["table"] == table})))
            elif st["dan2"]:
                notes.append(DAN2_NOTE)
                notes += st["dan2"]
            good = st["ok"] and not st["dan2"]
            new = dict(key=key, table=table, row=rw, cols=",".join(cols), value=value, unit=r["unit"], source=source,
                       checked="✓" if good else "", reviewer="auto:pdftext", date=today,
                       note=join_note(marks.get((table, rw), ""), *notes))
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
                    new["note"] = join_note(marks.get((table, rw), ""), *rest)
                elif o["checked"] == new["checked"] and o["reviewer"] == new["reviewer"]:
                    new["date"] = o["date"]
            out.append(new)
            if new["checked"] == "✓":
                st_t["ok"] += 1
            elif st["ok"]:
                st_t["dan2"] += 1
            else:
                st_t["ng"] += 1
    return out, stats


def load_all(root):
    return {n: kozo.load_table(root, n) for n in ("kessan-kai", "kessan-gaku", "kessan-uchiwake")}


def kessan_check(root=ROOT, out=print, today=TODAY):
    t = load_all(root)
    if not t["kessan-gaku"]:
        raise Stop("data/struct/kessan-gaku.tsv が無い（先に kessan-kian）")
    src = Src(root)
    allc = collect(src)
    res = check(t, src, allc)
    path = os.path.join(struct_dir(root), "audit", "kessan.tsv")
    old = htd.read_tsv(path, AUDIT_COLS) if os.path.exists(path) else []
    rows, stats = audit(t, res, old, today)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_table(path, AUDIT_COLS, rows)
    out("表ごとの行の数・通った行・段2 に回す行・通らなかった行")
    for table, s in stats.items():
        out(f"  {table}\t{s['ok'] + s['dan2'] + s['ng']}\t通った {s['ok']}\t段2 {s['dan2']}\t通らなかった {s['ng']}")
    d2 = [r for r in rows if not r["checked"] and DAN2_NOTE in r["note"]]
    out(f"段2 に回す行 {len(d2)}")
    for r in d2:
        out(f"  {r['table']}\t{r['row']}\t{r['note']}")
    out(f"合わなかった項目 {len(res['fails'])}")
    for f in res["fails"]:
        out(f"  [{f['kind']}] {f['table']} {f['row']}：{f['msg']}")
    for h in ("H2", "H4"):
        hs = [r for r in rows if f"要人確認:{h}" in r["note"]]
        out(f"{h} {len(hs)}")
        for r in hs:
            m = re.search(r"要人確認:H4 ([0-9]+)点 ?([^。]*)", r["note"])
            out("  " + "\t".join([r["table"], r["row"]] + ([m.group(1) + "点", m.group(2)] if m else [])))
    code = 1 if res["fails"] else 0
    out(f"台帳：{os.path.relpath(path, root)}（{len(rows)} 行）。終了コード {code}")
    return code, res, rows, stats


# ---- 段1 の道具の確かめ ----

MUTATIONS = [("value を変える", {"値"}), ("raw の字を変える", {"raw", "値"}), ("内訳の name を変える", {"name"}),
             ("別の出典との不一致", {"別の出典"})]
PER_KIND = 8


def spread(rows, n=PER_KIND):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def selftest(root=ROOT, out=print):
    tmp = tempfile.mkdtemp(prefix="kozo-kessan-selftest-")
    try:
        if kozo.load_table(root, "kessan-gaku"):
            data_root = root
            shutil.copytree(struct_dir(root), struct_dir(tmp))
            out("data/struct/ の表を一時の場所に写して確かめる")
        else:
            import test_kozo_kessan
            test_kozo_kessan.make_sample(tmp)
            kian(tmp, out=lambda *a: None)
            data_root = tmp
            out("data/struct/ に表が無いので、tools/test_kozo_kessan.py の見本で確かめる")
        t = load_all(tmp)
        src = Src(data_root)
        allc = collect(src)
        base = check(t, src, allc)
        bad = {(f["table"], f["row"]) for f in base["fails"]}
        found = [0] * len(MUTATIONS)
        total = [0] * len(MUTATIONS)
        missed = []

        def run(n, desc, t2, allc2, table, rw):
            total[n] += 1
            res = check(t2, src, allc2)
            if any(f["kind"] in MUTATIONS[n][1] and f["row"] == rw and f["table"] == table for f in res["fails"]):
                found[n] += 1
            else:
                missed.append(f"{MUTATIONS[n][0]}：{desc}")

        gk = [r for r in t["kessan-gaku"] if ("kessan-gaku", f"{r['kai']}/{r['key']}") not in bad]
        uw = [r for r in t["kessan-uchiwake"] if ("kessan-uchiwake", f"{r['kai']}/{r['key']}") not in bad
              and not r["note"].startswith("画像で確かめた")]
        for r in spread([r for r in gk if not r["note"].startswith("画像")]) + spread(uw, 4):
            table = "kessan-gaku" if "cell" in r else "kessan-uchiwake"
            t2 = copy.deepcopy(t)
            i = t[table].index(r)
            t2[table][i]["value"] = str(int(r["value"]) + 1)
            run(0, f"{table} {r['kai']}/{r['key']}", t2, allc, table, f"{r['kai']}/{r['key']}")
        for r in spread([r for r in gk if r["raw"] and not r["note"].startswith("画像")]) + spread(uw, 4):
            table = "kessan-gaku" if "cell" in r else "kessan-uchiwake"
            t2 = copy.deepcopy(t)
            i = t[table].index(r)
            raw = r["raw"]
            k = max(i2 for i2, ch in enumerate(raw) if ch.isdigit())
            ch = raw[k]
            rep = "1" if ch not in "1１" else "2"
            t2[table][i]["raw"] = raw[:k] + rep + raw[k + 1:]
            run(1, f"{table} {r['kai']}/{r['key']}", t2, allc, table, f"{r['kai']}/{r['key']}")
        for r in spread(uw):
            t2 = copy.deepcopy(t)
            i = t["kessan-uchiwake"].index(r)
            t2["kessan-uchiwake"][i]["name"] = "■" + r["name"][1:]
            run(2, f"{r['kai']}/{r['key']}", t2, allc, "kessan-uchiwake", f"{r['kai']}/{r['key']}")
        # 別の出典との不一致：相手の出典の値を1000倍ずらす（選んだ行の表は変えない）
        cross = []
        for r in gk:
            y, a = r["kai"].split("-", 1)
            kind = "sheet03" if r["cell"] else (
                "ikensho-prev" if "前年度の列" in r["note"] else ("ikensho" if "ikensho" in r["doc"] else "gaiyou"))
            os_ = [o for o in others_for(allc, y, a, r["key"], kind) if o[0] != "sheet03-wa"]
            if os_ and not (a in SHEET03_DAN2 and os_[0][0] == "sheet03"):
                cross.append((r, os_[0][0]))
        for r, okind in spread(cross):
            y, a = r["kai"].split("-", 1)
            allc2 = copy.deepcopy(allc)
            for kd, c in allc2[(y, a, r["key"])]:
                if kd == okind:
                    c["value"] = c["value"] * 1000 + 7
            run(3, f"{r['kai']}/{r['key']} と {okind}", t, allc2, "kessan-gaku", f"{r['kai']}/{r['key']}")
        code = 0
        out("誤りの種類\t入れた数\t見つけた数")
        for i, (name, _) in enumerate(MUTATIONS):
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
        return _main(argv)
    except Stop as e:
        print(f"止めた：{e}", file=sys.stderr)
        return 2


def _main(argv):
    if len(argv) == 2 and argv[1] == "kessan-kian":
        kian()
        return 0
    if len(argv) == 2 and argv[1] == "kessan-check":
        return kessan_check()[0]
    if len(argv) == 2 and argv[1] == "kessan-selftest":
        return selftest()
    print(__doc__)
    return 2
