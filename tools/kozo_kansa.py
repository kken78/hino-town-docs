"""監査の結果の構造化（DOCS_SCHEMA.md §13.15・13.9・13.10）。標準ライブラリだけで動く。tools/kozo.py から呼ぶ。

  python3 -B tools/kozo.py kansa-kian       台帳の check が ok の kansa の資料から、kansa-kekka・kansa-jiko を起票する
  python3 -B tools/kozo.py kansa-check      表を原典の文字で照合・検算し、検収の台帳 data/struct/audit/kansa.tsv を書く（段1）
  python3 -B tools/kozo.py kansa-selftest   段1 の道具に、誤りを入れた表を読ませて、誤りを見つけることを確かめる

起票は data/text_raw/（書かれた順の取り出し）を基にする。見出しの字で値の位置を決め、読めない値は空にして note に書く。
推定で埋めない。動かし直すとき、道具が値を出せなかった列に人が書いた値があれば保つ（13.9）。
kansa-check は、段1 で合わない値を「要人確認:段1」として台帳に書く。selftest は誤りを見つけ損ねたら終了コード 1。
"""
import copy
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import htd  # noqa: E402
import kozo  # noqa: E402
from kozo import Stop, join_note, row_key, write_table, struct_dir, AUDIT_COLS  # noqa: E402

ROOT = htd.ROOT
TODAY = kozo.TODAY

KEKKA_COLS = ["doc", "shurui", "kokuji", "kohyo_bi", "kansa_bi", "taisho", "shiteki", "kekka_from", "kekka_to", "note"]
JIKO_COLS = ["doc", "n", "jiko", "komoku", "gaku", "gaku_raw", "note"]
kozo.TABLES.setdefault("kansa-kekka", ("kansa-kekka.tsv", KEKKA_COLS))
kozo.TABLES.setdefault("kansa-jiko", ("kansa-jiko.tsv", JIKO_COLS))

# 検収の対象の列（13.15）
KEKKA_AUDIT = ["shurui", "kokuji", "kohyo_bi", "kansa_bi", "taisho", "shiteki", "kekka_from", "kekka_to"]
JIKO_AUDIT = ["jiko", "komoku", "gaku", "gaku_raw"]
# 道具が読む列（空なら読めなかった列として数える）。shiteki は定期監査では空が正しい
READ_COLS = ["shurui", "kokuji", "kohyo_bi", "kansa_bi", "taisho", "shiteki", "kekka_from", "kekka_to"]

SHURUI = {"teiki": "定期監査結果", "dantai": "財政援助団体等に対する監査"}
MARKS = "○〇"
WEEK = "月火水木金土日"


def sp(word):
    """字の間に空白が入ってもよい形の正規表現。"""
    return r"\s*".join(re.escape(c) for c in word)


NUM = r"(?:[0-9０-９]+\s*[．.]\s*)"
RE_KOKUJI = re.compile(r"^日野町監査委員告示第[0-9０-９]+号$")
RE_WAREKI = re.compile(r"^令和\s*[0-9０-９元]+\s*年\s*[0-9０-９]+\s*月\s*[0-9０-９]+\s*日")
RE_NICHIJI = re.compile(r"^" + NUM + "?" + sp("監査日時および") + r"\s*(.*)$")
RE_BASHO = re.compile(r"^" + sp("監査場所") + r"(?:\s|$)")
RE_KIKAN = re.compile(r"^" + NUM + "?" + sp("監査対象") + r"\s*(機\s*関|期\s*間)\s*(.*)$")
RE_TAISHO4 = re.compile(r"^" + NUM + "?" + sp("監査対象") + r"(?!\s*(?:機\s*関|期\s*間))\s*(.*)$")
RE_SHUTARU = re.compile(r"^主たる(監査|審査)事項\s*(.*)$")
RE_TETSUZUKI = re.compile(r"^" + NUM + "?" + sp("監査手続"))
RE_KEKKA6 = re.compile(r"^" + NUM + "?" + sp("監査の結果") + r"\s*(.*)$")
RE_D_HEAD = re.compile(r"^ア\s*[．.]\s*監査等の実施(期間|日時)および監査対象(課|団体)等\s*$")
RE_D_JIKO = re.compile(r"^イ\s*[．.]\s*監査等の対象とした事項および範囲\s*(.*)$")
RE_D_STOP = re.compile(r"^(ウ|エ)\s*[．.]")
RE_D_KEKKA = re.compile(r"^[4４]\s*[．.]\s*" + sp("監査結果") + r"\s*$")
RE_D_SHITEKI = re.compile(r"^イ\s*[．.]\s*指摘事項\s*(.*)$")
RE_GAKU = re.compile(r"^(?P<body>.*?)\s*(?P<raw>[0-9０-９][0-9０-９,，]*\s*円)(?P<rest>.*)$")
RE_JIKAN = re.compile(r"^(午前|午後|正午)")
HEAD_FLAT = {"teiki": re.compile(r"^(?:[0-9０-９]+[．.])?監査の結果"), "dantai": re.compile(r"^[4４][．.]監査結果")}


def flat(s):
    return re.sub(r"\s+", "", s)


def join_lines(lines):
    """13.15 の行のつなぎ方：各行の前後の空白を除き、つなぎ目に空白を入れずにつなぐ。行の中の空白はそのまま。"""
    return "".join(x.strip() for x in lines if x.strip())


def read_gaku(raw):
    t = unicodedata.normalize("NFKC", raw).replace("円", "").replace(",", "").replace(" ", "")
    if not re.fullmatch(r"[0-9]+", t):
        raise ValueError(raw)
    return str(int(t))


def read_lines(path):
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def raw_lines(path):
    """書かれた順の取り出しの行（改ページは行の区切りとして扱う）。前後の空白を除く。"""
    with open(path, encoding="utf-8") as f:
        s = f.read()
    return [x.strip() for x in s.replace("\f", "\n").split("\n")]


def kansa_docs(root):
    path = os.path.join(root, "data", "sources.tsv")
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
    rows = htd.read_tsv(path, head)
    return sorted(r["id"] for r in rows if r["category"] == "kansa" and r["check"] == "ok")


# ---- 起票（data/text_raw を読む） ----

def find(lines, rx, start=0, end=None):
    end = len(lines) if end is None else end
    for i in range(start, end):
        m = rx.match(lines[i])
        if m:
            return i, m
    return None, None


def parse_raw(lines):
    """text_raw の行から、報告の値と事項を読む。読めない値は空にし、notes に理由を書く。"""
    out = {c: "" for c in KEKKA_COLS}
    notes = []
    unread = []
    jiko = []
    body = None
    flats = [flat(x) for x in lines]
    if "定期監査結果" in flats:
        out["shurui"] = "teiki"
    elif any(SHURUI["dantai"] in x for x in flats):
        out["shurui"] = "dantai"
    else:
        notes.append("種類を読めなかった（「定期監査結果」の行も「財政援助団体等に対する監査」の字も無い）")
        unread.append("shurui")
    ne = [x for x in lines if x]
    if ne and RE_KOKUJI.match(ne[0]):
        out["kokuji"] = ne[0]
    else:
        notes.append("kokuji を読めなかった（最初の行が告示の番号の形でない）")
        unread.append("kokuji")
    i_pub = next((i for i, x in enumerate(lines) if "公表する" in x), None)
    if i_pub is not None:
        j, _ = find(lines, re.compile(r"^令和\S*年\S*月\S*日$"), i_pub + 1, i_pub + 4)
        if j is not None:
            out["kohyo_bi"] = lines[j]
    if not out["kohyo_bi"]:
        notes.append("kohyo_bi を読めなかった（公表の文の後に日付だけの行が無い）")
        unread.append("kohyo_bi")
    if out["shurui"] == "teiki":
        body = parse_teiki(lines, out, notes, unread, jiko)
    elif out["shurui"] == "dantai":
        body = parse_dantai(lines, out, notes, unread, jiko)
    return out, jiko, body, notes, unread


def parse_teiki(lines, out, notes, unread, jiko):
    i1, m1 = find(lines, RE_NICHIJI)
    i_basho, _ = find(lines, RE_BASHO, (i1 or 0) + 1)
    if i1 is None or not m1.group(1).strip():
        notes.append("kansa_bi を読めなかった（「監査日時および」の行に日時が無い）")
        unread.append("kansa_bi")
    else:
        cont = lines[i1 + 1:i_basho] if i_basho is not None else []
        out["kansa_bi"] = join_lines([m1.group(1)] + cont)
        if any(cont):
            notes.append("kansa_bi は折り返しをつないだ")
    i3, m3 = find(lines, RE_KIKAN)
    i4, m4 = find(lines, RE_TAISHO4, (i3 or 0) + 1)
    if i3 is None or not m3.group(2).strip():
        notes.append("taisho を読めなかった（「監査対象機関」の行に値が無い）")
        unread.append("taisho")
    else:
        cont = lines[i3 + 1:i4] if i4 is not None else []
        out["taisho"] = join_lines([m3.group(2)] + cont)
        if any(cont):
            notes.append("taisho は折り返しをつないだ")
        if flat(m3.group(1)) == "期間":
            notes.append("見出しの字は「監 査 対 象 期 間」（原典の字）")
    i5, _ = find(lines, RE_TETSUZUKI, (i4 or 0) + 1)
    if i4 is None or i5 is None:
        notes.append("事項を読めなかった（「４．監査対象」か「５．監査手続」の行が無い）")
    else:
        sec = [(i4, m4.group(1))] + [(i, lines[i]) for i in range(i4 + 1, i5)]
        shutaru = None
        items = []
        for i, x in sec:
            m = RE_SHUTARU.match(x)
            if m:
                shutaru = i
                x = m.group(2)
            x = x.strip()
            if not x:
                continue
            if x[0] in MARKS:
                items.append(dict(lines=[x[1:].strip()], before=shutaru is None))
            elif items:
                items[-1]["lines"].append(x)
        if shutaru is None:
            notes.append("「主たる監査事項」の見出しが無い。「４．監査対象」の下の○の行を事項とした")
        for it in items:
            n = []
            if len(it["lines"]) > 1:
                n.append("折り返しをつないだ")
            if it["before"] and shutaru is not None:
                n.append("「主たる」の見出しの前の「４．監査対象」の行の○")
            jiko.append(dict(jiko=join_lines(it["lines"]), komoku="", gaku="", gaku_raw="", note=join_note(*n)))
        if not items:
            notes.append("事項を読めなかった（○・〇の行が無い）")
    i6, m6 = find(lines, RE_KEKKA6, (i5 or 0) + 1)
    if i6 is None:
        notes.append("監査の結果の見出しが無い")
        return None
    return flat(m6.group(1) + "".join(lines[i6 + 1:]))


def parse_dantai(lines, out, notes, unread, jiko):
    ia, _ = find(lines, RE_D_HEAD)
    ii, mi = find(lines, RE_D_JIKO, (ia or 0) + 1)
    if ia is None or ii is None:
        notes.append("kansa_bi・taisho を読めなかった（「ア．監査等の実施期間および監査対象課等」か「イ．」の見出しが無い）")
        unread += ["kansa_bi", "taisho"]
    else:
        between = [x for x in lines[ia + 1:ii] if x]
        if between and RE_WAREKI.match(between[0]):
            out["kansa_bi"] = between[0]
        else:
            notes.append("kansa_bi を読めなかった（見出しの次の行が日付でない）")
            unread.append("kansa_bi")
        rest = between[1:]
        if len(rest) == 1 and not RE_JIKAN.match(rest[0]):
            out["taisho"] = rest[0]
        else:
            notes.append(f"taisho を読めなかった（日付の次が時刻と場所の行{len(rest)}つ）" if rest and all(RE_JIKAN.match(x) for x in rest)
                         else f"taisho を読めなかった（日付と「イ．」の見出しの間の行が{len(rest)}つ）")
            unread.append("taisho")
    if ii is not None:
        head_rest = mi.group(1).strip()
        i_end, _ = find(lines, RE_D_STOP, ii + 1)
        i_end = len(lines) if i_end is None else i_end
        uchiwake = False
        for x in lines[ii + 1:i_end]:
            if not x:
                continue
            if flat(x).startswith("（内訳）"):
                uchiwake = True
                break
            if flat(x).startswith("合計"):
                m = RE_GAKU.match(x)
                if m:
                    jiko_total = m.group("raw")
                    notes.append(f"合計の行：{x}")
                    out["_total"] = jiko_total
                continue
            body = x[1:].strip() if x[0] == "・" else x
            m = RE_GAKU.match(body)
            if not m or not m.group("body").strip():
                if jiko:
                    jiko[-1]["note"] = join_note(jiko[-1]["note"], f"次の行の字：{x}")
                else:
                    notes.append(f"事項の行と読めない行：{x}")
                continue
            parts = m.group("body").split()
            n = []
            if x[0] == "・":
                n.append("頭の「・」を除いた")
            if len(parts) == 1:
                name, komoku = parts[0], ""
            elif len(parts) == 2:
                name, komoku = parts
            else:
                name, komoku = "", ""
                n.append(f"名目と額に付いた名前を分けられない：{m.group('body')}")
            if m.group("rest").strip():
                n.append(f"額の後ろの字：{m.group('rest').strip()}")
            if head_rest:
                n.append(f"見出しのかっこ書き：{head_rest}")
            raw = m.group("raw")
            jiko.append(dict(jiko=name, komoku=komoku, gaku=read_gaku(raw), gaku_raw=raw, note=join_note(*n)))
        if uchiwake:
            notes.append("「イ．監査等の対象とした事項および範囲」の（内訳）の表は持たない")
        if not jiko:
            notes.append("事項を読めなかった（額の行が無い）")
    ik, _ = find(lines, RE_D_KEKKA, (ii or 0) + 1)
    if ik is None:
        notes.append("監査結果の見出しが無い")
        unread.append("shiteki")
        return None
    isd, msd = find(lines, RE_D_SHITEKI, ik + 1)
    if isd is None:
        notes.append("shiteki を読めなかった（「イ．指摘事項」の見出しが無い）")
        unread.append("shiteki")
    else:
        out["shiteki"] = join_lines([msd.group(1)] + lines[isd + 1:])
        if not out["shiteki"]:
            notes.append("shiteki を読めなかった（「イ．指摘事項」の後に字が無い）")
            unread.append("shiteki")
    return flat("".join(lines[ik + 1:]))


def locate_kekka(tlines, body, shurui):
    """data/text の行で、監査の結果の本文の始まりと終わりの行（1始まり）。本文の字（空白を除く）がその範囲の字と同じになる範囲。"""
    if not body:
        return None, None, "本文の字が無い"
    head = HEAD_FLAT[shurui]
    found = []
    for i, x in enumerate(tlines):
        fx = flat(x)
        if not fx:
            continue
        pre = ""
        if not body.startswith(fx):
            m = head.match(fx)
            if not (m and m.end() < len(fx) and body.startswith(fx[m.end():])):
                continue
            pre = fx[:m.end()]
        acc = ""
        for j in range(i, len(tlines)):
            acc += flat(tlines[j])
            if acc == pre + body:
                found.append((i + 1, j + 1, pre))
                break
            if not (pre + body).startswith(acc):
                break
    if len(found) != 1:
        return None, None, ("data/text に本文の字と同じ字の並びの行の範囲が無い" if not found
                            else f"data/text に本文の字と同じ範囲が{len(found)}つある")
    a, b, pre = found[0]
    return a, b, (f"data/text では見出しの字「{pre}」と本文の始まりが同じ行" if pre else "")


def build(root):
    rows, jrows, report = [], [], {}
    for doc in kansa_docs(root):
        lines = raw_lines(os.path.join(root, "data", "text_raw", doc + ".txt"))
        out, jiko, body, notes, unread = parse_raw(lines)
        out["doc"] = doc
        total = out.pop("_total", "")
        if out["shurui"]:
            tlines = read_lines(os.path.join(root, "data", "text", doc + ".txt"))
            a, b, why = locate_kekka(tlines, body, out["shurui"])
            if a is None:
                notes.append(f"kekka_from・kekka_to を決められなかった（{why}）")
                unread += ["kekka_from", "kekka_to"]
            else:
                out["kekka_from"], out["kekka_to"] = str(a), str(b)
                if why:
                    notes.append(why)
        if os.path.exists(os.path.join(struct_dir(root), "issues", doc + ".tsv")):
            notes.append(f"原典の誤りの記録：issues/{doc}.tsv")
        out["note"] = join_note(*notes)
        out["_total"] = total
        rows.append(out)
        for n, j in enumerate(jiko, 1):
            jrows.append(dict(doc=doc, n=str(n), **j))
        report[doc] = unread
    return rows, jrows, report


def keep_human(new_rows, new_jiko, old_rows, old_jiko):
    """道具が値を出せなかった列に、既存の行に人が書いた値があれば保つ（13.9）。事項を1つも読めなかった報告は既存の行を保つ。"""
    kept = []
    old = {r["doc"]: r for r in old_rows}
    for r in new_rows:
        o = old.get(r["doc"])
        if not o:
            continue
        for c in KEKKA_COLS:
            if c in ("doc", "note") or r[c] or not o[c]:
                continue
            r[c] = o[c]
            kept.append(f"{r['doc']} {c}")
        if o["note"] != r["note"]:
            r["note"] = join_note(r["note"], *[x for x in o["note"].split("。") if x.startswith("人が")])
    have = {j["doc"] for j in new_jiko}
    oj = {(j["doc"], j["n"]): j for j in old_jiko}
    out = []
    for j in new_jiko:
        o = oj.get((j["doc"], j["n"]))
        if o:
            for c in ("jiko", "komoku", "gaku", "gaku_raw"):
                if not j[c] and o[c]:
                    j[c] = o[c]
                    kept.append(f"{j['doc']}/{j['n']} {c}")
        out.append(j)
    for j in old_jiko:
        if j["doc"] not in have and j["doc"] in {r["doc"] for r in new_rows}:
            out.append(j)
            kept.append(f"{j['doc']}/{j['n']}")
    out.sort(key=lambda j: (j["doc"], int(j["n"])))
    return out, kept


def kian(root=ROOT, out=print):
    rows, jrows, report = build(root)
    for r in rows:
        r.pop("_total", None)
    old_rows = kozo.load_table(root, "kansa-kekka")
    old_jiko = kozo.load_table(root, "kansa-jiko")
    jrows, kept = keep_human(rows, jrows, old_rows, old_jiko)
    for r in rows + jrows:
        for v in r.values():
            if '"' in str(v):
                raise Stop(f"{r['doc']} の値に「\"」がある（TSV の読み書きで崩れる）")
    write_table(os.path.join(struct_dir(root), "kansa-kekka.tsv"), KEKKA_COLS, rows)
    write_table(os.path.join(struct_dir(root), "kansa-jiko.tsv"), JIKO_COLS, jrows)
    out(f"kansa-kekka {len(rows)} 行、kansa-jiko {len(jrows)} 行")
    cnt = {}
    for doc, cols in report.items():
        for c in cols:
            cnt.setdefault(c, []).append(doc)
    out("読めなかった列と本")
    for c in READ_COLS:
        ds = cnt.get(c, [])
        out(f"  {c}\t{len(ds)}\t{' '.join(ds)}")
    nd = sorted({d for ds in cnt.values() for d in ds})
    out(f"読めなかった列のある本 {len(nd)}")
    if kept:
        out(f"人が書いた値を保った {len(kept)}：" + " ".join(kept))
    return rows, jrows, report


# ---- 段1 の照合と検算 ----

class Src:
    def __init__(self, root):
        self.root = root
        self.c = {}

    def text(self, doc):
        if doc not in self.c:
            p = os.path.join(self.root, "data", "text", doc + ".txt")
            self.c[doc] = read_lines(p) if os.path.exists(p) else []
        return self.c[doc]

    def flat_text(self, doc):
        return flat("\n".join(self.text(doc)))

    def raw_lines(self, doc):
        k = ("raw", doc)
        if k not in self.c:
            p = os.path.join(self.root, "data", "text_raw", doc + ".txt")
            self.c[k] = raw_lines(p) if os.path.exists(p) else []
        return self.c[k]


def wareki_date(s):
    """「令和７年９月２９日（月）」→ (date, 曜日の字)。読めなければ None。"""
    t = unicodedata.normalize("NFKC", s)
    m = re.match(r"^令和\s*([0-9]+|元)\s*年\s*([0-9]+)\s*月\s*([0-9]+)\s*日\s*(?:\(\s*(.)\s*\))?", t)
    if not m:
        return None
    y = 1 if m.group(1) == "元" else int(m.group(1))
    try:
        d = date(2018 + y, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return d, m.group(4) or ""


def same_body(fr, body, shurui):
    """範囲の字（空白を除く）が本文の字と同じ。範囲の頭に見出しの字があるときは、それを除いて比べる。"""
    if fr == body:
        return True
    m = HEAD_FLAT.get(shurui, re.compile("^$")).match(fr)
    return bool(m and m.end() > 0 and fr[m.end():] == body)


def pages_of(tlines, a, b):
    """data/text の行の番号（1始まり）から PDF のページ。"""
    p = 1
    out = []
    for i, x in enumerate(tlines, 1):
        p += x.count("\f")
        if a <= i <= b:
            out.append(p)
    return out


def page_of_value(tlines, v):
    """値の字（空白を除く）が始まるページ。見つからなければ 1。"""
    fv = flat(v)
    pages = "\n".join(tlines).split("\f")
    for i, p in enumerate(pages, 1):
        if fv and fv[:20] in flat(p):
            return i
    return 1


def check(t, src):
    """段1。行ごとに {fails:[(列, 理由)], why:[難しさの理由], pts}。"""
    res = {}
    jby = {}
    for j in t["kansa-jiko"]:
        jby.setdefault(j["doc"], []).append(j)
    for r in t["kansa-kekka"]:
        doc = r["doc"]
        st = res[("kansa-kekka", doc)] = dict(fails=[], why=[], pts=0)
        f = st["fails"]
        ft = src.flat_text(doc)
        if not src.text(doc):
            f.append(("出典", "data/text が無い"))
            continue
        if r["shurui"] not in SHURUI:
            f.append(("shurui", "読めない"))
        else:
            rl = src.raw_lines(doc)
            if r["shurui"] == "teiki" and "定期監査結果" not in [flat(x) for x in rl]:
                f.append(("shurui", "text_raw に「定期監査結果」の行が無い"))
            if r["shurui"] == "dantai" and not any(SHURUI["dantai"] in flat(x) for x in rl):
                f.append(("shurui", "text_raw に「財政援助団体等に対する監査」の字が無い"))
        po, pj, body, _, _ = parse_raw(src.raw_lines(doc))
        for c in ("shurui", "kokuji", "kohyo_bi", "kansa_bi", "taisho", "shiteki"):
            if po[c] and r[c] != po[c]:
                f.append((c, f"text_raw から読み直した値「{po[c]}」と違う"))
        for c in ("kokuji", "kohyo_bi", "kansa_bi", "taisho", "shiteki"):
            v = r[c]
            if not v:
                if c != "shiteki" or r["shurui"] == "dantai":
                    f.append((c, "空"))
                continue
            if flat(v) not in ft:
                f.append((c, f"「{v}」が data/text に無い"))
        if r["kokuji"] and not RE_KOKUJI.match(r["kokuji"]):
            f.append(("kokuji", "告示の番号の形でない"))
        for c in ("kohyo_bi", "kansa_bi"):
            if r[c]:
                w = wareki_date(r[c])
                if not w:
                    f.append((c, "日付として読めない"))
                elif w[1] and WEEK[w[0].weekday()] != w[1]:
                    f.append((c, f"曜日が日付と合わない（{w[0].isoformat()} は {WEEK[w[0].weekday()]}）"))
        kb, pb = wareki_date(r["kansa_bi"]) if r["kansa_bi"] else None, wareki_date(r["kohyo_bi"]) if r["kohyo_bi"] else None
        if kb and pb and kb[0] > pb[0]:
            f.append(("kohyo_bi", "公表の日が監査の日より前"))
        # 監査の結果の範囲：data/text のその範囲の字（空白を除く）が、text_raw の本文の字と同じ
        if not r["kekka_from"] or not r["kekka_to"]:
            f.append(("kekka_from", "空"))
        else:
            tl = src.text(doc)
            try:
                a, b = int(r["kekka_from"]), int(r["kekka_to"])
            except ValueError:
                a, b = 0, -1
            if not (1 <= a <= b <= len(tl)):
                f.append(("kekka_from", "行の番号が data/text の範囲の外"))
            elif not same_body(flat("".join(tl[a - 1:b])), body or "", r["shurui"]):
                f.append(("kekka_from", "範囲の字が text_raw の本文の字と合わない"))
            else:
                ps = sorted(set(pages_of(tl, a, b)))
                if len(ps) > 1:
                    st["why"].append("本文が2ページにまたがる")
                    st["pts"] += 2
        js = jby.get(doc, [])
        if not js:
            f.append(("事項", "事項の行が無い"))
        # 合計の行（13.15）：事項の額の和＝合計
        rl = src.raw_lines(doc) if r["shurui"] == "dantai" else []
        tot = [x for x in rl if flat(x).startswith("合計") and RE_GAKU.match(x)]
        if r["shurui"] == "dantai" and not any(flat(x).startswith("（内訳）") for x in rl) and tot:
            try:
                want = int(read_gaku(RE_GAKU.match(tot[0]).group("raw")))
                if sum(int(j["gaku"] or 0) for j in js) != want:
                    f.append(("事項", f"事項の額の和が合計 {want} と合わない"))
            except ValueError:
                f.append(("事項", "合計の額が読めない"))
        if "期 間" in r["note"] or "見出しが無い" in r["note"]:
            st["why"].append("見出しの字が違うか見出しが無い")
            st["pts"] += 1
        if "と本文の始まりが同じ行" in r["note"]:
            st["why"].append("data/text で見出しと本文が同じ行")
            st["pts"] += 1
        if "折り返しをつないだ" in r["note"]:
            st["why"].append("値の折り返しをつないだ")
            st["pts"] += 2
        if r["shurui"] == "dantai" and r["shiteki"] and r["shiteki"] != "なし":
            st["why"].append("指摘事項がある")
            st["pts"] += 1
        if f:
            st["pts"] += 3
            st["why"].insert(0, "段1 で合わない値がある")
        ft_doc = ft
        if pj and len(pj) != len(js):
            f.append(("事項", f"事項の行の数 {len(js)} が text_raw から読み直した数 {len(pj)} と違う"))
        for j in js:
            sj = res[("kansa-jiko", f"{doc}/{j['n']}")] = dict(fails=[], why=[], pts=0)
            g = sj["fails"]
            k = int(j["n"]) - 1 if j["n"].isdigit() else -1
            if 0 <= k < len(pj):
                for c in ("jiko", "komoku", "gaku", "gaku_raw"):
                    if pj[k][c] and j[c] != pj[k][c]:
                        g.append((c, f"text_raw から読み直した値「{pj[k][c]}」と違う"))
            if not j["jiko"]:
                g.append(("jiko", "空"))
            elif flat(j["jiko"]) not in ft_doc:
                g.append(("jiko", f"「{j['jiko']}」が data/text に無い"))
            if j["komoku"] and flat(j["komoku"]) not in ft_doc:
                g.append(("komoku", f"「{j['komoku']}」が data/text に無い"))
            if r["shurui"] == "dantai":
                if not j["gaku_raw"]:
                    g.append(("gaku_raw", "空"))
                else:
                    if flat(j["gaku_raw"]) not in ft_doc:
                        g.append(("gaku_raw", f"「{j['gaku_raw']}」が data/text に無い"))
                    try:
                        if read_gaku(j["gaku_raw"]) != j["gaku"]:
                            g.append(("gaku", f"gaku_raw「{j['gaku_raw']}」の読みと gaku {j['gaku']} が違う"))
                    except ValueError:
                        g.append(("gaku", f"gaku_raw「{j['gaku_raw']}」が読めない"))
                # 名目・額に付いた名前・額が text_raw の1つの行に並ぶ
                line_ok = any(flat(j["jiko"]) + flat(j["komoku"]) + flat(j["gaku_raw"]) in flat(x).lstrip("・")
                              for x in src.raw_lines(doc))
                if not line_ok:
                    g.append(("jiko", "名目と額が text_raw の1つの行に並ばない"))
            elif j["gaku"] or j["gaku_raw"] or j["komoku"]:
                g.append(("gaku", "定期監査の事項に額か額に付いた名前がある"))
            if "折り返しをつないだ" in j["note"]:
                sj["why"].append("折り返しをつないだ")
                sj["pts"] += 2
            if j["komoku"]:
                sj["why"].append("額に付いた名前がある")
                sj["pts"] += 1
            if "次の行の字" in j["note"] or "額の後ろの字" in j["note"]:
                sj["why"].append("額の前後に字がある")
                sj["pts"] += 1
            if g:
                sj["pts"] += 3
                sj["why"].insert(0, "段1 で合わない値がある")
    return res


def doc_points(doc, t, res):
    pts = res[("kansa-kekka", doc)]["pts"]
    why = list(res[("kansa-kekka", doc)]["why"])
    for j in t["kansa-jiko"]:
        if j["doc"] == doc:
            s = res[("kansa-jiko", f"{doc}/{j['n']}")]
            pts += s["pts"]
            why += [w for w in s["why"] if w not in why]
    return pts, why


def audit(t, res, old_ledger, today=TODAY):
    """検収の台帳の行。H4 は年度と shurui の組ごとに1本（13.15）。人の印と段1 以外の要人確認は保つ。"""
    groups = {}
    for r in t["kansa-kekka"]:
        y = r["doc"].split("-")[1]
        groups.setdefault((y, r["shurui"]), []).append(r["doc"])
    h4 = {}
    for g, docs in sorted(groups.items()):
        best = None
        for d in docs:
            pts, why = doc_points(d, t, res)
            if best is None or pts > best[0]:
                best = (pts, d, why)
        h4[best[1]] = f"要人確認:H4 {best[0]}点" + (" " + "・".join(best[2]) if best[2] else "")
    old = {r["key"]: r for r in old_ledger}
    out = []
    stats = dict(ok=0, ng=0)

    def add(table, rw, cols, r, unit, source, doc):
        st = res[(table, rw)]
        value = "|".join(r[c] for c in cols)
        key = row_key(table, rw, ",".join(cols), value, unit, source)
        good = not st["fails"]
        notes = []
        if doc in h4:
            notes.append(h4[doc])
        if not good:
            notes.insert(0, "要人確認:段1 " + "・".join(f"{c}：{m}" for c, m in st["fails"]))
        new = dict(key=key, table=table, row=rw, cols=",".join(cols), value=value, unit=unit, source=source,
                   checked="✓" if good else "", reviewer="auto:pdftext", date=today,
                   note=join_note(*notes))
        o = old.get(key)
        if o:
            human = o["reviewer"] and not o["reviewer"].startswith("auto:")
            if human:
                for c in ("checked", "reviewer", "date", "note"):
                    new[c] = o[c]
            elif o["checked"] == new["checked"] and o["reviewer"] == new["reviewer"]:
                new["date"] = o["date"]
        out.append(new)
        stats["ok" if good else "ng"] += 1

    for r in t["kansa-kekka"]:
        add("kansa-kekka", r["doc"], KEKKA_AUDIT, r, "", r.get("_source") or f"{r['doc']} p.1", r["doc"])
    for j in t["kansa-jiko"]:
        add("kansa-jiko", f"{j['doc']}/{j['n']}", JIKO_AUDIT, j, "円" if j["gaku"] else "",
            j.get("_source") or f"{j['doc']} p.1", j["doc"])
    return out, stats


def add_sources(t, src):
    """出典のページ。kekka は本文の範囲のページ（2ページにまたがれば p.1-2）、事項はその名目の字のあるページ。"""
    for r in t["kansa-kekka"]:
        tl = src.text(r["doc"])
        try:
            ps = sorted(set(pages_of(tl, int(r["kekka_from"]), int(r["kekka_to"]))))
        except ValueError:
            ps = [1]
        r["_source"] = f"{r['doc']} p.{ps[0]}" + (f"-{ps[-1]}" if len(ps) > 1 else "")
    for j in t["kansa-jiko"]:
        j["_source"] = f"{j['doc']} p.{page_of_value(src.text(j['doc']), j['jiko'])}"


def load_all(root):
    return {n: kozo.load_table(root, n) for n in ("kansa-kekka", "kansa-jiko")}


def kansa_check(root=ROOT, out=print, today=TODAY):
    t = load_all(root)
    if not t["kansa-kekka"]:
        raise Stop("data/struct/kansa-kekka.tsv が無い（先に kansa-kian）")
    src = Src(root)
    res = check(t, src)
    add_sources(t, src)
    path = os.path.join(struct_dir(root), "audit", "kansa.tsv")
    old = htd.read_tsv(path, AUDIT_COLS) if os.path.exists(path) else []
    rows, stats = audit(t, res, old, today)
    write_table(path, AUDIT_COLS, rows)
    out(f"台帳の行 {len(rows)}：段1 で一致 {stats['ok']}、要人確認:段1 {stats['ng']}")
    for r in rows:
        if r["note"].startswith("要人確認:段1"):
            out(f"  {r['table']}\t{r['row']}\t{r['note']}")
    hs = [r for r in rows if "要人確認:H4" in r["note"]]
    out(f"要人確認:H4 の行 {len(hs)}（本 {len({r['row'].split('/')[0] for r in hs})}）")
    for r in hs:
        if r["table"] == "kansa-kekka":
            m = re.search(r"要人確認:H4 [^。]*", r["note"])
            out(f"  {r['row']}\t{m.group(0)}")
    out(f"台帳：{os.path.relpath(path, root)}。終了コード 0")
    return 0, res, rows, stats


# ---- 段1 の道具の確かめ ----

MUTATIONS = ["kokuji の字を変える", "kansa_bi の字を変える", "taisho の字を変える", "kekka_to をずらす",
             "jiko の字を変える", "gaku を変える", "曜日を変える", "shurui を変える", "taisho をほかの本の値にする"]


def swap_char(s):
    for k in range(len(s) - 1, -1, -1):
        ch = s[k]
        if ch.isdigit():
            rep = "1" if ch not in "1１" else "2"
            return s[:k] + rep + s[k + 1:]
    return "■" + s[1:] if s else s


def selftest(root=ROOT, out=print):
    tmp = tempfile.mkdtemp(prefix="kozo-kansa-selftest-")
    try:
        if kozo.load_table(root, "kansa-kekka"):
            data_root = root
            out("data/struct/ の表で確かめる")
        else:
            import test_kozo_kansa
            test_kozo_kansa.make_sample(tmp)
            kian(tmp, out=lambda *a: None)
            data_root = tmp
            out("data/struct/ に表が無いので、tools/test_kozo_kansa.py の見本で確かめる")
        t = load_all(data_root)
        src = Src(data_root)
        base = check(t, src)
        clean = [r for r in t["kansa-kekka"] if not res_fail(base, "kansa-kekka", r["doc"])]
        cleanj = [j for j in t["kansa-jiko"] if not res_fail(base, "kansa-jiko", f"{j['doc']}/{j['n']}")]
        total = [0] * len(MUTATIONS)
        found = [0] * len(MUTATIONS)
        missed = []

        def run(n, table, rw, t2, col):
            total[n] += 1
            res = check(t2, src)
            if any(c == col or (col == "kekka_to" and c == "kekka_from") for c, _ in res[(table, rw)]["fails"]):
                found[n] += 1
            else:
                missed.append(f"{MUTATIONS[n]}：{rw}")

        def mut(n, rows, table, col, fn, key=lambda r: r["doc"]):
            for r in spread(rows):
                t2 = copy.deepcopy(t)
                i = t[table].index(r)
                t2[table][i][col] = fn(r[col])
                run(n, table, key(r), t2, col if col != "kansa_bi_w" else "kansa_bi")

        mut(0, [r for r in clean if r["kokuji"]], "kansa-kekka", "kokuji", swap_char)
        mut(1, [r for r in clean if r["kansa_bi"]], "kansa-kekka", "kansa_bi", swap_char)
        mut(2, [r for r in clean if r["taisho"]], "kansa-kekka", "taisho", lambda v: "■" + v[1:])
        mut(3, [r for r in clean if r["kekka_to"]], "kansa-kekka", "kekka_to", lambda v: str(int(v) - 1))
        mut(4, [j for j in cleanj if j["jiko"]], "kansa-jiko", "jiko", lambda v: "■" + v[1:],
            key=lambda j: f"{j['doc']}/{j['n']}")
        mut(5, [j for j in cleanj if j["gaku"]], "kansa-jiko", "gaku", lambda v: str(int(v) + 1),
            key=lambda j: f"{j['doc']}/{j['n']}")

        def week(v):
            return re.sub(r"[（(](.)[）)]", lambda m: "（" + WEEK[(WEEK.index(m.group(1)) + 1) % 7] + "）"
                          if m.group(1) in WEEK else m.group(0), v, count=1)
        wk = [r for r in clean if re.search(r"[（(][月火水木金土日][）)]", r["kansa_bi"])]
        for r in spread(wk):
            t2 = copy.deepcopy(t)
            i = t["kansa-kekka"].index(r)
            t2["kansa-kekka"][i]["kansa_bi"] = week(r["kansa_bi"])
            run(6, "kansa-kekka", r["doc"], t2, "kansa_bi")
        mut(7, clean, "kansa-kekka", "shurui", lambda v: "dantai" if v == "teiki" else "teiki")
        names = sorted({r["taisho"] for r in clean if r["taisho"]})
        mut(8, [r for r in clean if r["taisho"]], "kansa-kekka", "taisho",
            lambda v: next((x for x in names if x != v and x not in v and v not in x), "■"))
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


def res_fail(res, table, rw):
    return bool(res.get((table, rw), {}).get("fails"))


def spread(rows, n=8):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def main(argv):
    try:
        return _main(argv)
    except Stop as e:
        print(f"止めた：{e}", file=sys.stderr)
        return 2


def _main(argv):
    if len(argv) == 2 and argv[1] == "kansa-kian":
        kian()
        return 0
    if len(argv) == 2 and argv[1] == "kansa-check":
        return kansa_check()[0]
    if len(argv) == 2 and argv[1] == "kansa-selftest":
        return selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
