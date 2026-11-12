"""決算審査の意見の構造化（DOCS_SCHEMA.md §13.16・13.9・13.10）。標準ライブラリだけで動く。tools/kozo.py から呼ぶ。

  python3 -B tools/kozo.py iken-kian       各会計決算審査意見書（kessan-r3-ikensho〜）から、kessan-iken を起票する
  python3 -B tools/kozo.py iken-check      表を原典の文字で照合し、検収の台帳 data/struct/audit/kessan-iken.tsv を書く（段1）
  python3 -B tools/kozo.py iken-selftest   段1 の道具に、誤りを入れた表を読ませて、誤りを見つけることを確かめる

起票は data/text_raw/（書かれた順の取り出し）で部分と意見の区切りを読み、data/text/ の行の番号に当てる。
意見の文は表に写さない。読めない値は空にして note に書く。推定で埋めない。
動かし直すとき、道具が値を出せなかった列に人が書いた値があれば保つ（13.9）。
iken-check は、段1 で合わない行を「要人確認:段1」として台帳に書く。selftest は誤りを見つけ損ねたら終了コード 1。
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
from kozo import Stop, join_note, row_key, write_table, struct_dir, AUDIT_COLS  # noqa: E402

ROOT = htd.ROOT
TODAY = kozo.TODAY

COLS = ["doc", "kaikei", "bubun", "shurui", "bango", "n", "page", "insatsu_page", "line_from", "line_to", "midashi", "note"]
AUDIT = ["bubun", "kaikei", "shurui", "bango", "n", "page", "insatsu_page", "line_from", "line_to", "midashi"]
kozo.TABLES.setdefault("kessan-iken", ("kessan-iken.tsv", COLS))

SECTIONS = {"健全化判断比率": "kenzenka", "資金不足比率": "shikin"}
NASHI = "nashi"          # 会計でない部分（健全化判断比率・資金不足比率の節）の kaikei
MARKS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
EDGE = 20                # 段1 で比べる最初と最後の字の数

RE_CHAPTER = re.compile(r"^[０-９0-9]+\s*[．.]\s*(\S+)$")
RE_MATOME = re.compile(r"^(?:[０-９0-9]+\s*[．.]\s*)?まとめ(?:（[^）]*）)?$")
RE_SUB = re.compile(r"^[０-９0-9]+\s*[．.]\s*[^\s0-9０-９]")
RE_NUMONLY = re.compile(r"^[0-9０-９.,．，△ ]+$")
RE_PAGENO = re.compile(r"^[0-9]{1,3}$")
RE_IKEN = re.compile(r"意見を申し上げる。$")


def flat(s):
    return re.sub(r"\s+", "", s)


def numonly(x):
    """数だけの行（ページの番号と、図の数の字）。部分の字の並びから除く。"""
    return bool(RE_NUMONLY.match(x.strip()))


def raw_lines(path):
    with open(path, encoding="utf-8") as f:
        s = f.read()
    return [x.strip() for x in s.replace("\f", "\n").split("\n")]


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def ikensho_docs(root):
    path = os.path.join(root, "data", "sources.tsv")
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
    rows = htd.read_tsv(path, head)
    return sorted((r["id"] for r in rows if re.fullmatch(r"kessan-r[0-9]+-ikensho", r["id"])),
                  key=lambda d: int(d.split("-")[1][1:]))


def fiscal_digits(doc):
    """資料の id の年度の数（令和の年）。"""
    return int(doc.split("-")[1][1:])


def wareki_year_re(n):
    """「令和３年度」「令和 3 年度」の形（全角・半角を問わない）。"""
    z = "".join(chr(ord("０") + int(c)) for c in str(n))
    return re.compile(r"^令和\s*(?:" + str(n) + "|" + z + r")\s*年度")


# ---- 部分と意見を読む（data/text_raw） ----

def read_parts(lines, doc):
    """text_raw の行から、部分（まとめ・健全化判断比率の節・資金不足比率の節）と、その中の行を読む。
    部分ごとに dict(bubun, kaikei, heading, ended, rows=[dict(shurui, bango, lines, notes)], notes)。
    数だけの行（ページの番号など）と空の行は lines に入れない。"""
    heads = []
    for i, x in enumerate(lines):
        m = RE_CHAPTER.match(x)
        if not m:
            continue
        name = flat(m.group(1))
        ab = kozo.kaikei_abbr(name)
        if ab:
            heads.append((i, "matome", ab, x))
        elif name in SECTIONS:
            heads.append((i, SECTIONS[name], NASHI, x))
        elif name.endswith("会計"):
            raise Stop(f"{doc} の {i + 1} 行目の会計「{x}」が 13.3 の表に無い")
    parts, nomatome = [], []
    for k, (i, bubun, ab, hx) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        if bubun != "matome":
            parts.append(dict(bubun=bubun, kaikei=ab, heading=hx, start=i + 1, end=end, ended="節の終わり"))
            continue
        ms = [j for j in range(i + 1, end) if RE_MATOME.match(lines[j])]
        if not ms:
            nomatome.append(ab)
            continue
        if len(ms) > 1:
            raise Stop(f"{doc} の {ab} の章に「まとめ」の見出しが {len(ms)} つある")
        j = ms[0]
        e = next((t for t in range(j + 1, end) if RE_SUB.match(lines[t])), None)
        parts.append(dict(bubun="matome", kaikei=ab, heading=lines[j], start=j + 1, end=e if e is not None else end,
                          ended="次の章の見出し" if e is None else f"番号の付いた見出し「{lines[e]}」"))
    for p in parts:
        body = [x for x in lines[p["start"]:p["end"]] if x and not numonly(x)]
        p["rows"], p["notes"] = split_rows(body, doc)
    return parts, nomatome


def split_rows(body, doc):
    """部分の行を、maegaki・iken・musubi・zentai の行に分ける（13.16）。"""
    notes = []
    marks = [i for i, x in enumerate(body) if x[:1] in MARKS]
    ik = [i for i, x in enumerate(body) if RE_IKEN.search(x)]
    if not marks and not ik:
        return [dict(shurui="zentai", bango="", lines=body, notes=[])], notes
    if not marks or len(ik) != 1 or ik[0] >= marks[0]:
        notes.append("形が決まりに無い（「意見を申し上げる。」の行と番号の付いた意見の並びが 13.16 の形でない）")
        return [dict(shurui="zentai", bango="", lines=body, notes=["形が決まりに無い"])], notes
    rows = [dict(shurui="maegaki", bango="", lines=body[:ik[0] + 1], notes=[])]
    if marks[0] != ik[0] + 1:
        rows[0]["notes"].append("「意見を申し上げる。」の行と①の間に字がある")
        rows[0]["lines"] = body[:marks[0]]
    # 最後の意見と締めの文の境：最後の意見の始まりより後で、前の行が「。」で終わり、
    # 「令和<決算の年度>年度」で始まる行（13.16）
    yr = wareki_year_re(fiscal_digits(doc))
    last = marks[-1]
    cands = [i for i in range(last + 1, len(body)) if yr.match(body[i]) and body[i - 1].endswith("。")]
    mus = cands[0] if cands else None
    if len(cands) > 1:
        notes.append(f"締めの文の始まりの候補が {len(cands)} つある（最初のものを当てた）")
    bounds = marks + [mus if mus is not None else len(body)]
    for a, b in zip(bounds, bounds[1:]):
        x = body[a]
        rows.append(dict(shurui="iken", bango=x[0], lines=body[a:b], notes=[]))
    if mus is not None:
        rows[-1]["notes"].append("締めの文との境は字の並びの決まりで決めた")
        rows.append(dict(shurui="musubi", bango="", lines=body[mus:],
                         notes=["最後の意見との境は字の並びの決まりで決めた"]))
    else:
        notes.append("締めの文が無い（「令和<決算の年度>年度」で始まる行が無い）")
    return rows, notes


# ---- data/text の行に当てる ----

class TextDoc:
    """data/text の行と、数だけの行・空の行を除いた字の並び（1字ごとに行の番号を持つ）。"""

    def __init__(self, path):
        self.lines = read_lines(path)
        chars, own = [], []
        page = 1
        self.page_of = []
        for i, x in enumerate(self.lines):
            page += x.count("\f")
            self.page_of.append(page)
            if not x.strip() or numonly(x):
                continue
            for c in flat(x):
                chars.append(c)
                own.append(i + 1)
        self.s = "".join(chars)
        self.own = own
        self.printed = self._printed()

    def _printed(self):
        """ページごとの印刷のページ番号。ページの最後の空でない3行のうち、1〜3桁の数字だけの行の最後のもの。"""
        pages = "\n".join(self.lines).split("\f")
        out = {}
        for k, p in enumerate(pages, 1):
            ne = [x.strip() for x in p.split("\n") if x.strip()]
            got = [x for x in ne[-3:] if RE_PAGENO.match(x)]
            out[k] = got[-1] if got else ""
        return out

    def find(self, fs):
        """字の並びの位置。1つだけ当たれば位置、無いか2つ以上なら None と数。"""
        n = self.s.count(fs) if fs else 0
        return (self.s.find(fs) if n == 1 else None), n

    def flat_range(self, a, b):
        return flat("".join(x for x in self.lines[a - 1:b] if x.strip() and not numonly(x)))

    def numonly_in(self, a, b):
        """範囲の中の数だけの行で、ページの番号でないもの（図の数の字など）。"""
        out = []
        for i in range(a, b + 1):
            x = self.lines[i - 1].strip()
            if x and numonly(x) and x != self.printed.get(self.page_of[i - 1], None):
                out.append(x)
        return out


def locate(td, part):
    """部分の行を data/text の行の範囲に当てる。返す値は行ごとの (line_from, line_to) と、部分の理由。"""
    fl = [flat("".join(r["lines"])) for r in part["rows"]]
    pos, n = td.find("".join(fl))
    if pos is None:
        return None, (f"data/text に部分の字の並びが {n} 回ある" if n else "data/text に部分の字の並びが無い")
    out = []
    for f in fl:
        if not f:
            out.append((None, None))
            continue
        out.append((td.own[pos], td.own[pos + len(f) - 1]))
        pos += len(f)
    return out, ""


def build(root):
    rows, report = [], dict(parts={}, nomatome={}, headings={}, ended={}, notes={})
    for doc in ikensho_docs(root):
        lines = raw_lines(os.path.join(root, "data", "text_raw", doc + ".txt"))
        td = TextDoc(os.path.join(root, "data", "text", doc + ".txt"))
        parts, nomatome = read_parts(lines, doc)
        report["nomatome"][doc] = nomatome
        report["parts"][doc] = [(p["bubun"], p["kaikei"], [r["shurui"] for r in p["rows"]]) for p in parts]
        report["headings"][doc] = [p["heading"] for p in parts if p["bubun"] == "matome"]
        report["ended"][doc] = [(p["kaikei"], p["ended"]) for p in parts]
        report["notes"][doc] = [(p["kaikei"], x) for p in parts for x in p["notes"]]
        for p in parts:
            loc, why = locate(td, p)
            for k, r in enumerate(p["rows"]):
                notes = list(r["notes"]) + (p["notes"] if k == 0 else [])
                out = dict(doc=doc, kaikei=p["kaikei"], bubun=p["bubun"], shurui=r["shurui"], bango=r["bango"],
                           n=str(k + 1), page="", insatsu_page="", line_from="", line_to="", midashi="")
                if loc is None or loc[k][0] is None:
                    notes.append(f"line_from・line_to を決められなかった（{why or '字が無い'}）")
                else:
                    a, b = loc[k]
                    out["line_from"], out["line_to"] = str(a), str(b)
                    pa, pb = td.page_of[a - 1], td.page_of[b - 1]
                    out["page"] = str(pa)
                    out["insatsu_page"] = td.printed.get(pa, "")
                    if not out["insatsu_page"]:
                        notes.append(f"insatsu_page を読めなかった（{pa} ページの終わりに番号の行が無い）")
                    if pb != pa:
                        ib = td.printed.get(pb, "")
                        notes.append(f"ページをまたぐ（{pa}〜{pb} ページ。印刷のページ {out['insatsu_page'] or '?'}〜{ib or '?'}）")
                    if k > 0 and loc[k - 1][1] == a:
                        notes.append("data/text では前の行の終わりと同じ行に始まる")
                    extra = td.numonly_in(a, b)
                    if extra:
                        notes.append("data/text の範囲に数だけの行がある（" + "・".join(f"「{x}」" for x in extra) + "）")
                out["note"] = join_note(*notes)
                rows.append(out)
    return rows, report


def keep_human(rows, old_rows):
    """道具が値を出せなかった列に既存の行の人が書いた値があれば保つ（13.9）。行は doc・bubun・kaikei・n で合わせる。"""
    kept = []
    old = {(r["doc"], r["bubun"], r["kaikei"], r["n"]): r for r in old_rows}
    for r in rows:
        o = old.get((r["doc"], r["bubun"], r["kaikei"], r["n"]))
        if not o:
            continue
        for c in COLS:
            if c in ("doc", "kaikei", "bubun", "n", "note") or r[c] or not o[c]:
                continue
            r[c] = o[c]
            kept.append(f"{r['doc']}/{r['bubun']}/{r['kaikei']}/{r['n']} {c}")
        r["note"] = join_note(r["note"], *[x for x in o["note"].split("。") if x.startswith("人が")])
    return kept


def kian(root=ROOT, out=print):
    rows, report = build(root)
    kept = keep_human(rows, kozo.load_table(root, "kessan-iken"))
    write_table(os.path.join(struct_dir(root), "kessan-iken.tsv"), COLS, rows)
    out(f"kessan-iken {len(rows)} 行")
    for doc in report["parts"]:
        out(f"{doc}：部分 {len(report['parts'][doc])}、まとめの無い章 {' '.join(report['nomatome'][doc]) or 'なし'}")
        out("  まとめの見出しの字：" + " / ".join(sorted(set(report["headings"][doc]))))
        cut = [f"{k}（{e}）" for k, e in report["ended"][doc] if e.startswith("番号")]
        if cut:
            out("  まとめの終わりを番号の付いた見出しで決めた：" + " ".join(cut))
        for k, x in report["notes"][doc]:
            out(f"  {k}：{x}")
    unread = [r for r in rows if not r["line_from"]]
    out(f"line_from を決められなかった行 {len(unread)}")
    out(f"insatsu_page を読めなかった行 {sum(1 for r in rows if r['line_from'] and not r['insatsu_page'])}")
    if kept:
        out(f"人が書いた値を保った {len(kept)}：" + " ".join(kept))
    return rows, report


# ---- 段1 の照合 ----

class Src:
    def __init__(self, root):
        self.root = root
        self.c = {}

    def td(self, doc):
        k = ("t", doc)
        if k not in self.c:
            self.c[k] = TextDoc(os.path.join(self.root, "data", "text", doc + ".txt"))
        return self.c[k]

    def parts(self, doc):
        k = ("p", doc)
        if k not in self.c:
            self.c[k] = read_parts(raw_lines(os.path.join(self.root, "data", "text_raw", doc + ".txt")), doc)[0]
        return self.c[k]


def rid(r):
    return f"{r['doc']}/{r['bubun']}/{r['kaikei']}/{r['n']}"


def ival(v):
    try:
        return int(v)
    except ValueError:
        return None


def check(rows, src):
    """段1。行ごとに {fails:[(列, 理由)], why:[難しさの理由], pts}。"""
    res = {}
    groups = {}
    for r in rows:
        groups.setdefault((r["doc"], r["bubun"], r["kaikei"]), []).append(r)
    for (doc, bubun, kaikei), rs in groups.items():
        td = src.td(doc)
        part = next((p for p in src.parts(doc) if p["bubun"] == bubun and p["kaikei"] == kaikei), None)
        sts = []
        for r in rs:
            st = res[rid(r)] = dict(fails=[], why=[], pts=0)
            sts.append(st)
        if part is None:
            for st in sts:
                st["fails"].append(("bubun", "text_raw から読み直した部分に無い"))
        else:
            want = part["rows"]
            if len(want) != len(rs):
                for st in sts:
                    st["fails"].append(("n", f"行の数 {len(rs)} が text_raw から読み直した数 {len(want)} と違う"))
            # 番号の並び：① から順に欠けなく
            ik = [r for r in rs if r["shurui"] == "iken"]
            for k, r in enumerate(ik):
                if r["bango"] != MARKS[k]:
                    res[rid(r)]["fails"].append(("bango", f"番号が ① から順に並ばない（{k + 1} つめが「{r['bango']}」）"))
            for k, r in enumerate(rs):
                st = res[rid(r)]
                if r["n"] != str(k + 1):
                    st["fails"].append(("n", "n が 1 から順でない"))
                if k < len(want):
                    w = want[k]
                    for c in ("shurui", "bango"):
                        if r[c] != w[c]:
                            st["fails"].append((c, f"text_raw から読み直した値「{w[c]}」と違う"))
                    a, b = ival(r["line_from"]), ival(r["line_to"])
                    if a is None or b is None or not (1 <= a <= b <= len(td.lines)):
                        st["fails"].append(("line_from", "行の番号が空か data/text の範囲の外"))
                        continue
                    fw = flat("".join(w["lines"]))
                    fr = td.flat_range(a, b)
                    if fr[:EDGE] != fw[:EDGE]:
                        st["fails"].append(("line_from", f"最初の字「{fr[:EDGE]}」が text_raw の「{fw[:EDGE]}」と合わない"))
                    if fr[-EDGE:] != fw[-EDGE:]:
                        st["fails"].append(("line_to", f"最後の字「{fr[-EDGE:]}」が text_raw の「{fw[-EDGE:]}」と合わない"))
                    pa, pb = td.page_of[a - 1], td.page_of[b - 1]
                    if r["page"] != str(pa):
                        st["fails"].append(("page", f"line_from のページは {pa}"))
                    if r["insatsu_page"] != td.printed.get(pa, ""):
                        st["fails"].append(("insatsu_page", f"{pa} ページの番号の行は「{td.printed.get(pa, '')}」"))
                    if pb != pa:
                        st["why"].append("ページをまたぐ")
                        st["pts"] += 2
            # 範囲が重ならず、部分の初めから終わりまでをすき間なく覆う
            fall = flat("".join(x for w in want for x in w["lines"]))
            pos, n = td.find(fall)
            if pos is None:
                for st in sts:
                    st["fails"].append(("line_from", f"data/text に部分の字の並びが {n} 回ある" if n else "data/text に部分の字の並びが無い"))
            else:
                first, last = td.own[pos], td.own[pos + len(fall) - 1]
                prev_b = None
                for k, r in enumerate(rs):
                    st = res[rid(r)]
                    a, b = ival(r["line_from"]), ival(r["line_to"])
                    if a is None or b is None:
                        continue
                    if k == 0 and a != first:
                        st["fails"].append(("line_from", f"部分の初めの行 {first} と違う"))
                    if k == len(rs) - 1 and b != last:
                        st["fails"].append(("line_to", f"部分の終わりの行 {last} と違う"))
                    if prev_b is not None:
                        if a <= prev_b:
                            st["fails"].append(("line_from", "前の行の範囲と重なる" + ("（data/text で同じ行）" if a == prev_b else "")))
                        else:
                            gap = [td.lines[i - 1] for i in range(prev_b + 1, a)]
                            if any(x.strip() and not numonly(x) for x in gap):
                                st["fails"].append(("line_from", "前の行の範囲との間に字の行がある"))
                    prev_b = b
        for r in rs:
            st = res[rid(r)]
            if "字の並びの決まりで決めた" in r["note"]:
                st["why"].append("締めの文との境を字の並びで決めた")
                st["pts"] += 1
            if "数だけの行がある" in r["note"]:
                st["why"].append("範囲に数だけの行がある")
                st["pts"] += 1
            if st["fails"]:
                st["pts"] += 3
                st["why"].insert(0, "段1 で合わない値がある")
    return res


def part_key(r):
    return (r["doc"], r["bubun"], r["kaikei"])


def pick_h4(rows, res):
    """年度ごとに、一般会計のまとめ・ほかの会計のまとめ1つ・健全化判断比率か資金不足比率の節1つ（13.16）。"""
    pts = {}
    for r in rows:
        k = part_key(r)
        s = res[rid(r)]
        p, w = pts.get(k, (0, []))
        pts[k] = (p + s["pts"], w + [x for x in s["why"] if x not in w])
    chosen = {}
    docs = sorted({r["doc"] for r in rows})
    for doc in docs:
        ks = [k for k in pts if k[0] == doc]
        for kind in ("ippan", "other", "section"):
            if kind == "ippan":
                cs = [k for k in ks if k[1] == "matome" and k[2] == "ippan"]
            elif kind == "other":
                cs = [k for k in ks if k[1] == "matome" and k[2] != "ippan"]
            else:
                cs = [k for k in ks if k[1] != "matome"]
            if not cs:
                continue
            best = sorted(cs, key=lambda k: (-pts[k][0], "/".join(k)))[0]
            chosen[best] = f"要人確認:H4 {pts[best][0]}点" + (" " + "・".join(pts[best][1]) if pts[best][1] else "")
    return chosen


def source_of(r, td):
    a, b = ival(r["line_from"]), ival(r["line_to"])
    if a is None or b is None or not (1 <= a <= b <= len(td.lines)):
        return f"{r['doc']} p.{r['page'] or '?'}"
    pa, pb = td.page_of[a - 1], td.page_of[b - 1]
    return f"{r['doc']} p.{pa}" + (f"-{pb}" if pb != pa else "")


def audit(rows, res, src, old_ledger, today=TODAY):
    h4 = pick_h4(rows, res)
    old = {r["key"]: r for r in old_ledger}
    out = []
    stats = dict(ok=0, ng=0)
    for r in rows:
        st = res[rid(r)]
        value = "|".join(r[c] for c in AUDIT)
        source = source_of(r, src.td(r["doc"]))
        key = row_key("kessan-iken", rid(r), ",".join(AUDIT), value, "", source)
        good = not st["fails"]
        notes = []
        if part_key(r) in h4:
            notes.append(h4[part_key(r)])
        if not good:
            notes.insert(0, "要人確認:段1 " + "・".join(f"{c}：{m}" for c, m in st["fails"]))
        new = dict(key=key, table="kessan-iken", row=rid(r), cols=",".join(AUDIT), value=value, unit="", source=source,
                   checked="✓" if good else "", reviewer="auto:pdftext", date=today, note=join_note(*notes))
        o = old.get(key)
        if o:
            if o["reviewer"] and not o["reviewer"].startswith("auto:"):
                for c in ("checked", "reviewer", "date", "note"):
                    new[c] = o[c]
            elif o["checked"] == new["checked"] and o["reviewer"] == new["reviewer"]:
                new["date"] = o["date"]
        out.append(new)
        stats["ok" if good else "ng"] += 1
    return out, stats, h4


def iken_check(root=ROOT, out=print, today=TODAY):
    rows = kozo.load_table(root, "kessan-iken")
    if not rows:
        raise Stop("data/struct/kessan-iken.tsv が無い（先に iken-kian）")
    src = Src(root)
    res = check(rows, src)
    path = os.path.join(struct_dir(root), "audit", "kessan-iken.tsv")
    old = htd.read_tsv(path, AUDIT_COLS) if os.path.exists(path) else []
    led, stats, h4 = audit(rows, res, src, old, today)
    write_table(path, AUDIT_COLS, led)
    out(f"台帳の行 {len(led)}：段1 で一致 {stats['ok']}、要人確認:段1 {stats['ng']}")
    for r in led:
        if r["note"].startswith("要人確認:段1"):
            out(f"  {r['row']}\t{r['note']}")
    out(f"要人確認:H4 の部分 {len(h4)}（行 {sum(1 for r in led if '要人確認:H4' in r['note'])}）")
    for k, v in sorted(h4.items()):
        out(f"  {'/'.join(k)}\t{v}")
    out(f"台帳：{os.path.relpath(path, root)}。終了コード 0")
    return 0, res, led, stats


# ---- 段1 の道具の確かめ ----

MUTATIONS = ["line_from をずらす", "line_to をずらす", "bango を変える", "shurui を変える", "page を変える",
             "insatsu_page を変える", "行を抜く", "隣の行と範囲を入れ替える"]


def spread(rows, n=8):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def selftest(root=ROOT, out=print):
    tmp = tempfile.mkdtemp(prefix="kozo-iken-selftest-")
    try:
        if kozo.load_table(root, "kessan-iken"):
            data_root = root
            out("data/struct/ の表で確かめる")
        else:
            import test_kozo_kessan_iken
            test_kozo_kessan_iken.make_sample(tmp)
            kian(tmp, out=lambda *a: None)
            data_root = tmp
            out("data/struct/ に表が無いので、tools/test_kozo_kessan_iken.py の見本で確かめる")
        rows = kozo.load_table(data_root, "kessan-iken")
        src = Src(data_root)
        base = check(rows, src)
        clean = [r for r in rows if not base[rid(r)]["fails"]]
        total = [0] * len(MUTATIONS)
        found = [0] * len(MUTATIONS)
        missed = []

        def run(n, rows2, target, cols):
            total[n] += 1
            res = check(rows2, src)
            fails = [c for k in target for c, _ in res.get(k, {}).get("fails", [])]
            if any(c in cols for c in fails):
                found[n] += 1
            else:
                missed.append(f"{MUTATIONS[n]}：{target[0]}")

        def mut(n, cands, col, fn, cols):
            for r in spread(cands):
                rows2 = copy.deepcopy(rows)
                i = rows.index(r)
                rows2[i][col] = fn(rows2[i][col])
                run(n, rows2, [rid(r)], cols)

        mut(0, [r for r in clean if r["line_from"]], "line_from", lambda v: str(int(v) + 1), ("line_from",))
        mut(1, [r for r in clean if r["line_to"]], "line_to", lambda v: str(int(v) - 1), ("line_to", "line_from"))
        mut(2, [r for r in clean if r["bango"]], "bango", lambda v: MARKS[(MARKS.index(v) + 1) % len(MARKS)], ("bango",))
        mut(3, clean, "shurui", lambda v: "iken" if v != "iken" else "maegaki", ("shurui",))
        mut(4, [r for r in clean if r["page"]], "page", lambda v: str(int(v) + 1), ("page",))
        mut(5, [r for r in clean if r["insatsu_page"]], "insatsu_page", lambda v: str(int(v) + 1), ("insatsu_page",))
        # 行を抜く：残った行のどれかで見つける
        for r in spread([r for r in clean if r["n"] != "1"]):
            rows2 = [x for x in copy.deepcopy(rows) if rid(x) != rid(r)]
            same = [rid(x) for x in rows2 if part_key(x) == part_key(r)]
            run(6, rows2, same, ("n", "line_from", "line_to", "bango", "shurui"))
        # 隣の行と範囲を入れ替える
        pairs = [(a, b) for a, b in zip(clean, clean[1:]) if part_key(a) == part_key(b)]
        for a, b in spread(pairs):
            rows2 = copy.deepcopy(rows)
            i, j = rows.index(a), rows.index(b)
            for c in ("line_from", "line_to"):
                rows2[i][c], rows2[j][c] = rows2[j][c], rows2[i][c]
            run(7, rows2, [rid(a), rid(b)], ("line_from", "line_to"))
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
        if len(argv) == 2 and argv[1] == "iken-kian":
            kian()
            return 0
        if len(argv) == 2 and argv[1] == "iken-check":
            return iken_check()[0]
        if len(argv) == 2 and argv[1] == "iken-selftest":
            return selftest()
    except Stop as e:
        print(f"止めた：{e}", file=sys.stderr)
        return 2
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
