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

KEKKA_COLS = ["doc", "shurui", "kokuji", "kohyo_bi", "kansa_bi", "chakugan", "shiteki", "kekka_from", "kekka_to", "note"]
AITE_COLS = ["doc", "n", "yakuwari", "name", "jikan", "basho", "note"]
KANKEI_COLS = ["doc", "n", "gyo", "oya", "meimoku", "shurui_ji", "kubun", "nendo_ji", "nendo_basho", "retsu", "gaku",
               "gaku_raw", "note"]
JIKO_COLS = ["doc", "n", "jiko", "note"]
TABLE_COLS = {"kansa-kekka": KEKKA_COLS, "kansa-aite": AITE_COLS, "kansa-kankei": KANKEI_COLS, "kansa-jiko": JIKO_COLS}
for _t, _c in TABLE_COLS.items():
    kozo.TABLES.setdefault(_t, (_t + ".tsv", _c))

# 検収の対象の列（13.15）
AUDIT_COLS_OF = {
    "kansa-kekka": ["shurui", "kokuji", "kohyo_bi", "kansa_bi", "chakugan", "shiteki", "kekka_from", "kekka_to"],
    "kansa-aite": ["yakuwari", "name", "jikan", "basho"],
    "kansa-kankei": ["gyo", "oya", "meimoku", "shurui_ji", "kubun", "nendo_ji", "nendo_basho", "retsu", "gaku", "gaku_raw"],
    "kansa-jiko": ["jiko"],
}
# 道具が読む列（空なら読めなかった列として数える）
READ_COLS = ["shurui", "kokuji", "kohyo_bi", "kansa_bi", "chakugan", "shiteki", "kekka_from", "kekka_to", "aite"]
DANTAI_NOTE = "要人確認:団体の監査 全件（相手の区切りと yakuwari、関係の shurui_ji・kubun は道具の候補）"

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
    """text_raw の行から、報告の値・相手・事項・関係を読む。読めない値は空にし、notes に理由を書く。"""
    out = {c: "" for c in KEKKA_COLS}
    notes, unread, jiko, aite, kankei = [], [], [], [], []
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
        body = parse_teiki(lines, out, notes, unread, jiko, aite)
    elif out["shurui"] == "dantai":
        body = parse_dantai(lines, out, notes, unread, aite, kankei)
    return dict(out=out, jiko=jiko, aite=aite, kankei=kankei, body=body, notes=notes, unread=unread)


def parse_teiki(lines, out, notes, unread, jiko, aite):
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
        notes.append("相手（監査対象機関）を読めなかった（「監査対象機関」の行に値が無い）")
        unread.append("aite")
    else:
        cont = lines[i3 + 1:i4] if i4 is not None else []
        n = []
        if any(cont):
            n.append("折り返しをつないだ")
        if flat(m3.group(1)) == "期間":
            n.append("見出しの字は「監 査 対 象 期 間」（原典の字）")
        aite.append(dict(yakuwari="ka", name=join_lines([m3.group(2)] + cont), jikan="", basho="",
                         note=join_note("「３．監査対象機関」の値", *n)))
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
            jiko.append(dict(jiko=join_lines(it["lines"]), note=join_note(*n)))
        if not items:
            notes.append("事項を読めなかった（○・〇の行が無い）")
    i6, m6 = find(lines, RE_KEKKA6, (i5 or 0) + 1)
    if i6 is None:
        notes.append("監査の結果の見出しが無い")
        return None
    return flat(m6.group(1) + "".join(lines[i6 + 1:]))


RE_GLUED = re.compile(r"(?<=\S)(?=[ウエ]\s*[．.]\s*(?:監査|その他))")
RE_YEN = re.compile(r"[0-9０-９][0-9０-９,，]*\s*円")
RE_NENDO = re.compile(r"令和\s*[0-9０-９元]+\s*年度")
RE_PAREN = re.compile(r"^[（(](.*)[）)]$")
KA_TAIL = ("課", "室", "担当", "課長", "図書館")
SHURUI_JI = ("指定管理料", "補助金", "交付金", "負担金", "貸付金", "損失補償", "利子補給", "委託料", "委託業務", "出資金")
ENJO = ("補助金", "交付金", "負担金", "貸付金", "損失補償", "利子補給")
KAKIKATA = {"jikoku": "時刻・相手・場所の行", "tandoku": "団体だけ", "toten": "読点「、」で区切った行",
            "nakaguro": "中点「・」で区切った行"}


def yakuwari_of(name):
    """名前の末尾が町の組織の名前なら ka、ほかは dantai（道具の候補）。"""
    return "ka" if name.endswith(KA_TAIL) else "dantai"


def shurui_ji_of(text):
    """名目の字の中で、いちばん後ろに出る語（同じ位置なら長い語）。無ければ空。"""
    best = None
    for w in SHURUI_JI:
        k = text.rfind(w)
        if k >= 0:
            e = k + len(w)
            if best is None or e > best[0] or (e == best[0] and len(w) > len(best[1])):
                best = (e, w)
    return best[1] if best else ""


def kubun_of(w):
    if w in ENJO:
        return "zaisei-enjo"
    if w == "指定管理料":
        return "shitei-kanri"
    return ""


def parse_dantai(lines, out, notes, unread, aite, kankei):
    lines = [y.strip() for x in lines for y in RE_GLUED.split(x)]
    ia, _ = find(lines, RE_D_HEAD)
    ii, mi = find(lines, RE_D_JIKO, (ia or 0) + 1)
    if ia is None or ii is None:
        notes.append("kansa_bi と相手を読めなかった（「ア．監査等の実施期間および監査対象課等」か「イ．」の見出しが無い）")
        unread += ["kansa_bi", "aite"]
    else:
        between = [x for x in lines[ia + 1:ii] if x]
        if between and RE_WAREKI.match(between[0]):
            out["kansa_bi"] = between[0]
        else:
            notes.append("kansa_bi を読めなかった（見出しの次の行が日付でない）")
            unread.append("kansa_bi")
        read_aite(between[1:], aite, notes, unread)
    ie, me = find(lines, re.compile(r"^エ\s*[．.]\s*その他監査等の目的、着眼点\s*(.*)$"), (ii or 0) + 1)
    ik, _ = find(lines, RE_D_KEKKA, (ii or 0) + 1)
    if ie is None or ik is None or ik < ie:
        notes.append("chakugan を読めなかった（「エ．その他監査等の目的、着眼点」の見出しが無い）")
        unread.append("chakugan")
    else:
        out["chakugan"] = join_lines([me.group(1)] + lines[ie + 1:ik])
        if not out["chakugan"]:
            notes.append("chakugan を読めなかった（見出しの後に字が無い）")
            unread.append("chakugan")
    if ii is not None:
        i_end, _ = find(lines, RE_D_STOP, ii + 1)
        read_kankei(lines[ii + 1:(len(lines) if i_end is None else i_end)], mi.group(1).strip(), out["chakugan"],
                    kankei, notes)
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


def read_aite(rest, aite, notes, unread):
    """日付の行と「イ．」の見出しの間の行から、相手の候補を作る（13.15。区切りは候補で、人が確かめる）。"""
    if not rest:
        notes.append("相手を読めなかった（日付と「イ．」の見出しの間に行が無い）")
        unread.append("aite")
        return
    if all(RE_JIKAN.match(x) for x in rest):
        kind = "jikoku"
        for x in rest:
            tk = x.split()
            if len(tk) < 2:
                aite.append(dict(yakuwari="", name="", jikan="", basho="", note=f"行を分けられない：{x}"))
                unread.append("aite")
                continue
            aite.append(dict(yakuwari=yakuwari_of(tk[1]), name=tk[1], jikan=tk[0], basho=" ".join(tk[2:]),
                             note=""))
    elif len(rest) == 1:
        x = rest[0]
        if "、" in x:
            kind, parts = "toten", x.split("、")
        elif "・" in x:
            kind, parts = "nakaguro", x.split("・")
        else:
            kind, parts = "tandoku", [x]
        for p in parts:
            aite.append(dict(yakuwari=yakuwari_of(p.strip()), name=p.strip(), jikan="", basho="", note=""))
    else:
        notes.append(f"相手を読めなかった（日付と「イ．」の見出しの間の行が{len(rest)}つで、時刻の行でない）")
        unread.append("aite")
        return
    for a in aite:
        a["note"] = join_note(a["note"], f"書き方：{KAKIKATA[kind]}", "yakuwari は名前の末尾による候補")
    notes.append(f"相手の書き方：{KAKIKATA[kind]}")


def read_kankei(sec, head_rest, chakugan, kankei, notes):
    """「イ．監査等の対象とした事項および範囲」の行から、関係の行（kobetsu・gokei・uchiwake）を作る。"""
    hm = RE_PAREN.match(head_rest)
    midashi = hm.group(1) if hm else head_rest
    retsu, oya, pending = None, "", []

    def add(**kw):
        r = dict(gyo="", oya="", meimoku="", shurui_ji="", kubun="", nendo_ji="", nendo_basho="", retsu="",
                 gaku="", gaku_raw="", note="")
        r.update(kw)
        r["n"] = str(len(kankei) + 1)
        kankei.append(r)
        return r

    for x in sec:
        if not x:
            continue
        fx = flat(x)
        if fx.startswith("（内訳）"):
            retsu = x.split()[1:] if len(x.split()) > 1 else []
            oya = next((r["n"] for r in reversed(kankei) if r["gyo"] == "kobetsu"), "")
            continue
        yens = list(RE_YEN.finditer(x))
        if retsu is not None:   # 内訳の表
            if not yens:
                pending.append(x)
                continue
            name = join_lines(pending + [x[:yens[0].start()]])
            joined = len(pending) > 0 or False
            pending = []
            gyo = "gokei" if flat(name).startswith("合計") else "uchiwake"
            for k, m in enumerate(yens):
                n = []
                if joined:
                    n.append("折り返しをつないだ")
                if len(yens) != len(retsu):
                    n.append(f"額の数 {len(yens)} と列の名前の数 {len(retsu)} が違う")
                add(gyo=gyo, oya=oya, meimoku=name, retsu=retsu[k] if len(yens) == len(retsu) else "",
                    gaku=read_gaku(m.group(0)), gaku_raw=m.group(0), note=join_note(*n))
            continue
        if not yens:
            if kankei:
                kankei[-1]["note"] = join_note(kankei[-1]["note"], f"次の行の字：{x}")
            else:
                notes.append(f"関係の行と読めない行：{x}")
            continue
        body = x[:yens[0].start()].strip()
        after = x[yens[-1].end():].strip()
        n = []
        if body.startswith("・"):
            body = body[1:].strip()
            n.append("頭の「・」を除いた")
        if len(yens) != 1 or not body:
            add(gyo="kobetsu", meimoku=body, note=join_note(*n, f"行を読めない：{x}"))
            continue
        raw = yens[0].group(0)
        if flat(body).startswith("合計"):
            add(gyo="gokei", meimoku=body, gaku=read_gaku(raw), gaku_raw=raw, note=join_note(*n))
            continue
        nendo = []
        m = RE_NENDO.search(body)
        if m:
            nendo.append((m.group(0), "atama" if m.start() == 0 else "naka"))
        pm = RE_PAREN.match(after)
        if after:
            if pm and RE_NENDO.search(pm.group(1)):
                nendo.append((pm.group(1), "gaku-ato"))
            else:
                n.append(f"額の後ろの字：{after}")
        if midashi:
            if RE_NENDO.search(midashi):
                nendo.append((midashi, "midashi"))
            else:
                n.append(f"見出しの後ろの字：{head_rest}")
        if len(nendo) > 1:
            n.append("年度の字がほかにもある：" + "・".join(f"{a}（{b}）" for a, b in nendo[1:]))
        w = shurui_ji_of(body)
        if not w:
            cands = sorted({v for v in SHURUI_JI if v in chakugan})
            if len(cands) == 1:
                w = cands[0]
                n.append("shurui_ji は着眼点の字から")
        kb = kubun_of(w)
        if w and not kb:
            n.append(f"条文の列挙に無い語（{w}）")
        add(gyo="kobetsu", meimoku=body, shurui_ji=w, kubun=kb, nendo_ji=nendo[0][0] if nendo else "",
            nendo_basho=nendo[0][1] if nendo else "", gaku=read_gaku(raw), gaku_raw=raw,
            note=join_note(*n, "shurui_ji・kubun は道具の候補"))
    if pending:
        notes.append("内訳の表の終わりに額の無い行：" + " ".join(pending))
    if not kankei:
        notes.append("関係の行を読めなかった（額の行が無い）")


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
    t = {k: [] for k in TABLE_COLS}
    report = {}
    for doc in kansa_docs(root):
        lines = raw_lines(os.path.join(root, "data", "text_raw", doc + ".txt"))
        p = parse_raw(lines)
        out, notes, unread = p["out"], p["notes"], p["unread"]
        out["doc"] = doc
        if out["shurui"]:
            tlines = read_lines(os.path.join(root, "data", "text", doc + ".txt"))
            a, b, why = locate_kekka(tlines, p["body"], out["shurui"])
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
        t["kansa-kekka"].append(out)
        for name in ("jiko", "aite", "kankei"):
            for n, r in enumerate(p[name], 1):
                r = dict(r)
                r["doc"], r["n"] = doc, r.get("n", str(n))
                t["kansa-" + name].append(r)
        report[doc] = unread
    return t, report


def keep_human(t, old):
    """道具が値を出せなかった列に、既存の行に人が書いた値があれば保つ（13.9）。行を1つも読めなかった本は既存の行を保つ。"""
    kept = []
    docs = {r["doc"] for r in t["kansa-kekka"]}
    for table, cols in TABLE_COLS.items():
        keyof = (lambda r: r["doc"]) if table == "kansa-kekka" else (lambda r: (r["doc"], r["n"]))
        om = {keyof(r): r for r in old.get(table, [])}
        for r in t[table]:
            o = om.get(keyof(r))
            if not o:
                continue
            for c in cols:
                if c in ("doc", "n", "note") or r[c] or not o[c]:
                    continue
                r[c] = o[c]
                kept.append(f"{table} {'/'.join(keyof(r)) if table != 'kansa-kekka' else r['doc']} {c}")
        if table != "kansa-kekka":
            have = {r["doc"] for r in t[table]}
            for o in old.get(table, []):
                if o["doc"] in docs and o["doc"] not in have:
                    t[table].append(dict(o))
                    kept.append(f"{table} {o['doc']}/{o['n']}")
            t[table].sort(key=lambda r: (r["doc"], int(r["n"])))
    return kept


def kian(root=ROOT, out=print):
    t, report = build(root)
    old = {}
    for table in TABLE_COLS:
        try:
            old[table] = kozo.load_table(root, table)
        except Stop:
            old[table] = []   # 列の違う前の形の表は読まない
    kept = keep_human(t, old)
    for table, cols in TABLE_COLS.items():
        for r in t[table]:
            for v in r.values():
                if '"' in str(v):
                    raise Stop(f"{r['doc']} の値に「\"」がある（TSV の読み書きで崩れる）")
        write_table(os.path.join(struct_dir(root), table + ".tsv"), cols, t[table])
    out("、".join(f"{k} {len(v)} 行" for k, v in t.items()))
    cnt = {}
    for doc, cols in report.items():
        for c in cols:
            cnt.setdefault(c, []).append(doc)
    out("読めなかった列と本")
    for c in READ_COLS:
        ds = sorted(set(cnt.get(c, [])))
        out(f"  {c}\t{len(ds)}\t{' '.join(ds)}")
    nd = sorted({d for ds in cnt.values() for d in ds})
    out(f"読めなかった列のある本 {len(nd)}")
    if kept:
        out(f"人が書いた値を保った {len(kept)}：" + " ".join(kept))
    return t, report


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


def rows_of(t, table, doc):
    return [r for r in t[table] if r["doc"] == doc]


def check(t, src):
    """段1。行ごとに {fails:[(列, 理由)], why:[難しさの理由], pts}。キーは (表, row)。"""
    res = {}
    for r in t["kansa-kekka"]:
        doc = r["doc"]
        st = res[("kansa-kekka", doc)] = dict(fails=[], why=[], pts=0)
        subs = {}
        for table in ("kansa-jiko", "kansa-aite", "kansa-kankei"):
            subs[table] = rows_of(t, table, doc)
            for x in subs[table]:
                res[(table, f"{doc}/{x['n']}")] = dict(fails=[], why=[], pts=0)
        f = st["fails"]
        if not src.text(doc):
            f.append(("出典", "data/text が無い"))
            continue
        ft = src.flat_text(doc)
        p = parse_raw(src.raw_lines(doc))
        po = p["out"]
        # (1) 読み直し
        for c in ("shurui", "kokuji", "kohyo_bi", "kansa_bi", "chakugan", "shiteki"):
            if po[c] and r[c] != po[c]:
                f.append((c, f"text_raw から読み直した値「{po[c]}」と違う"))
        for table, key in (("kansa-jiko", "jiko"), ("kansa-aite", "aite"), ("kansa-kankei", "kankei")):
            pr, rows = p[key], subs[table]
            if pr and len(pr) != len(rows):
                f.append((table, f"行の数 {len(rows)} が text_raw から読み直した数 {len(pr)} と違う"))
            for k, x in enumerate(rows):
                if k < len(pr):
                    for c in AUDIT_COLS_OF[table]:
                        if pr[k].get(c) and x[c] != pr[k][c]:
                            res[(table, f"{doc}/{x['n']}")]["fails"].append(
                                (c, f"text_raw から読み直した値「{pr[k][c]}」と違う"))
        # (5) 形と (2) data/text の字
        if r["shurui"] not in SHURUI:
            f.append(("shurui", "読めない"))
        else:
            rl = src.raw_lines(doc)
            if r["shurui"] == "teiki" and "定期監査結果" not in [flat(x) for x in rl]:
                f.append(("shurui", "text_raw に「定期監査結果」の行が無い"))
            if r["shurui"] == "dantai" and not any(SHURUI["dantai"] in flat(x) for x in rl):
                f.append(("shurui", "text_raw に「財政援助団体等に対する監査」の字が無い"))
        dantai = r["shurui"] == "dantai"
        for c in ("kokuji", "kohyo_bi", "kansa_bi", "chakugan", "shiteki"):
            v = r[c]
            if not v:
                if c not in ("chakugan", "shiteki") or dantai:
                    f.append((c, "空"))
                continue
            if not dantai and c in ("chakugan", "shiteki"):
                f.append((c, "定期監査で値がある"))
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
        kb = wareki_date(r["kansa_bi"]) if r["kansa_bi"] else None
        pb = wareki_date(r["kohyo_bi"]) if r["kohyo_bi"] else None
        if kb and pb and kb[0] > pb[0]:
            f.append(("kohyo_bi", "公表の日が監査の日より前"))
        # (3) 本文の範囲
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
            elif not same_body(flat("".join(tl[a - 1:b])), p["body"] or "", r["shurui"]):
                f.append(("kekka_from", "範囲の字が text_raw の本文の字と合わない"))
            elif len(set(pages_of(tl, a, b))) > 1:
                st["why"].append("本文が2ページにまたがる")
                st["pts"] += 2
        if not subs["kansa-aite"]:
            f.append(("相手", "相手の行が無い"))
        if not dantai and not subs["kansa-jiko"]:
            f.append(("事項", "事項の行が無い"))
        if dantai and subs["kansa-jiko"]:
            f.append(("事項", "団体の監査に事項の行がある"))
        if dantai and not subs["kansa-kankei"]:
            f.append(("関係", "関係の行が無い"))
        if not dantai and subs["kansa-kankei"]:
            f.append(("関係", "定期監査に関係の行がある"))
        # 相手
        for x in subs["kansa-aite"]:
            g = res[("kansa-aite", f"{doc}/{x['n']}")]["fails"]
            if x["yakuwari"] not in ("ka", "dantai"):
                g.append(("yakuwari", "ka・dantai でない"))
            if not dantai and x["yakuwari"] != "ka":
                g.append(("yakuwari", "定期監査の相手が ka でない"))
            if not x["name"]:
                g.append(("name", "空"))
            for c in ("name", "jikan", "basho"):
                if x[c] and flat(x[c]) not in ft:
                    g.append((c, f"「{x[c]}」が data/text に無い"))
        # 事項
        for x in subs["kansa-jiko"]:
            sj = res[("kansa-jiko", f"{doc}/{x['n']}")]
            if not x["jiko"]:
                sj["fails"].append(("jiko", "空"))
            elif flat(x["jiko"]) not in ft:
                sj["fails"].append(("jiko", f"「{x['jiko']}」が data/text に無い"))
            if "折り返しをつないだ" in x["note"]:
                sj["why"].append("折り返しをつないだ")
                sj["pts"] += 2
        # 関係
        check_kankei(doc, subs["kansa-kankei"], ft, res)
        if "期 間" in r["note"] + "".join(x["note"] for x in subs["kansa-aite"]) or "見出しが無い" in r["note"]:
            st["why"].append("見出しの字が違うか見出しが無い")
            st["pts"] += 1
        if "と本文の始まりが同じ行" in r["note"]:
            st["why"].append("data/text で見出しと本文が同じ行")
            st["pts"] += 1
        if any("折り返しをつないだ" in x["note"] for x in subs["kansa-aite"]) or "kansa_bi は折り返し" in r["note"]:
            st["why"].append("値の折り返しをつないだ")
            st["pts"] += 2
        if f:
            st["pts"] += 3
            st["why"].insert(0, "段1 で合わない値がある")
        for table in ("kansa-jiko", "kansa-aite", "kansa-kankei"):
            for x in subs[table]:
                sx = res[(table, f"{doc}/{x['n']}")]
                if sx["fails"]:
                    sx["pts"] += 3
                    sx["why"].insert(0, "段1 で合わない値がある")
    return res


def check_kankei(doc, rows, ft, res):
    """関係の行の照合と、合計・内訳の検算（13.15）。"""
    def fail(x, c, m):
        res[("kansa-kankei", f"{doc}/{x['n']}")]["fails"].append((c, m))
    byn = {x["n"]: x for x in rows}
    for x in rows:
        if x["gyo"] not in ("kobetsu", "gokei", "uchiwake"):
            fail(x, "gyo", "kobetsu・gokei・uchiwake でない")
        if not x["meimoku"]:
            fail(x, "meimoku", "空")
        elif flat(x["meimoku"]) not in ft:
            fail(x, "meimoku", f"「{x['meimoku']}」が data/text に無い")
        if not x["gaku_raw"]:
            fail(x, "gaku_raw", "空")
        else:
            if flat(x["gaku_raw"]) not in ft:
                fail(x, "gaku_raw", f"「{x['gaku_raw']}」が data/text に無い")
            try:
                if read_gaku(x["gaku_raw"]) != x["gaku"]:
                    fail(x, "gaku", f"gaku_raw「{x['gaku_raw']}」の読みと gaku {x['gaku']} が違う")
            except ValueError:
                fail(x, "gaku", f"gaku_raw「{x['gaku_raw']}」が読めない")
        if x["oya"] and (x["oya"] not in byn or byn[x["oya"]]["gyo"] != "kobetsu"):
            fail(x, "oya", "oya が個別の関係の行でない")
        if x["gyo"] == "uchiwake" and not x["oya"]:
            fail(x, "oya", "内訳の行に oya が無い")
        if x["gyo"] == "kobetsu" and (x["oya"] or x["retsu"]):
            fail(x, "oya", "個別の関係の行に oya か retsu がある")
        if x["gyo"] != "kobetsu" and (x["shurui_ji"] or x["kubun"] or x["nendo_ji"]):
            fail(x, "kubun", "合計・内訳の行に shurui_ji・kubun・nendo_ji がある")
        if x["kubun"] not in ("", "zaisei-enjo", "shitei-kanri"):
            fail(x, "kubun", "区分の値でない")
        if x["shurui_ji"] and kubun_of(x["shurui_ji"]) != x["kubun"]:
            fail(x, "kubun", f"shurui_ji「{x['shurui_ji']}」の区分と違う")
        if x["shurui_ji"] and x["shurui_ji"] not in flat(x["meimoku"]) and "着眼点の字から" not in x["note"]:
            fail(x, "shurui_ji", "名目の字に無い")
        if x["nendo_ji"] and x["nendo_basho"] not in ("atama", "naka", "gaku-ato", "midashi"):
            fail(x, "nendo_basho", "場所の値でない")
        if x["nendo_basho"] in ("atama", "naka") and flat(x["nendo_ji"]) not in flat(x["meimoku"]):
            fail(x, "nendo_ji", "名目の字に無い")
        if x["nendo_ji"] and flat(x["nendo_ji"]) not in ft:
            fail(x, "nendo_ji", f"「{x['nendo_ji']}」が data/text に無い")

    def iv(x):
        return int(x["gaku"]) if re.fullmatch(r"[0-9]+", x["gaku"] or "") else None
    # 合計の行（oya が空）＝個別の行の和
    kob = [x for x in rows if x["gyo"] == "kobetsu"]
    for g in [x for x in rows if x["gyo"] == "gokei" and not x["oya"]]:
        vals = [iv(x) for x in kob]
        if None in vals or iv(g) is None or sum(vals) != iv(g):
            fail(g, "検算", f"個別の行の和 {sum(v for v in vals if v is not None)} が合計 {g['gaku']} と合わない")
    # 内訳：列ごとに内訳の行の和＝その列の合計の行。合計の行の和＝親の額
    for p in {x["oya"] for x in rows if x["oya"]}:
        ch = [x for x in rows if x["oya"] == p]
        gk = [x for x in ch if x["gyo"] == "gokei"]
        for c in {x["retsu"] for x in ch}:
            us = [iv(x) for x in ch if x["gyo"] == "uchiwake" and x["retsu"] == c]
            gs = [x for x in gk if x["retsu"] == c]
            if len(gs) != 1 or None in us or iv(gs[0]) is None or sum(us) != iv(gs[0]):
                for x in (gs or [x for x in ch if x["retsu"] == c][:1]):
                    fail(x, "検算", f"列「{c}」の内訳の和 {sum(u for u in us if u is not None)} が合計の行と合わない")
        if gk and p in byn:
            if None in [iv(x) for x in gk] or sum(iv(x) for x in gk) != iv(byn[p]):
                for x in gk:
                    fail(x, "検算", f"内訳の合計の行の和が親の行 n={p} の額 {byn[p]['gaku']} と合わない")
    for x in rows:
        sx = res[("kansa-kankei", f"{doc}/{x['n']}")]
        if "折り返しをつないだ" in x["note"]:
            sx["why"].append("折り返しをつないだ")
            sx["pts"] += 2


def doc_points(doc, t, res):
    pts = res[("kansa-kekka", doc)]["pts"]
    why = list(res[("kansa-kekka", doc)]["why"])
    for table in ("kansa-jiko", "kansa-aite"):
        for j in rows_of(t, table, doc):
            s_ = res[(table, f"{doc}/{j['n']}")]
            pts += s_["pts"]
            why += [w for w in s_["why"] if w not in why]
    return pts, why


def audit(t, res, old_ledger, today=TODAY):
    """検収の台帳の行。定期監査は H4 を年度ごとに1本、団体の監査は全件を人が見る（13.15）。key が同じ行の人の印は保つ。"""
    shurui = {r["doc"]: r["shurui"] for r in t["kansa-kekka"]}
    groups = {}
    for r in t["kansa-kekka"]:
        if r["shurui"] == "teiki":
            groups.setdefault(r["doc"].split("-")[1], []).append(r["doc"])
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
    stats = dict(ok=0, ng=0, kept=0)
    for table in ("kansa-kekka", "kansa-aite", "kansa-kankei", "kansa-jiko"):
        cols = AUDIT_COLS_OF[table]
        for r in t[table]:
            doc = r["doc"]
            rw = doc if table == "kansa-kekka" else f"{doc}/{r['n']}"
            st = res[(table, rw)]
            value = "|".join(r[c] for c in cols)
            unit = "円" if table == "kansa-kankei" and r["gaku"] else ""
            key = row_key(table, rw, ",".join(cols), value, unit, r["_source"])
            good = not st["fails"]
            notes = []
            if not good:
                notes.append("要人確認:段1 " + "・".join(f"{c}：{m}" for c, m in st["fails"]))
            if shurui.get(doc) == "dantai":
                notes.append(DANTAI_NOTE)
            elif doc in h4 and table in ("kansa-kekka", "kansa-jiko"):
                notes.append(h4[doc])
            new = dict(key=key, table=table, row=rw, cols=",".join(cols), value=value, unit=unit, source=r["_source"],
                       checked="✓" if good else "", reviewer="auto:pdftext", date=today, note=join_note(*notes))
            o = old.get(key)
            if o:
                human = o["reviewer"] and not o["reviewer"].startswith("auto:")
                if human:
                    for c in ("checked", "reviewer", "date", "note"):
                        new[c] = o[c]
                    stats["kept"] += 1
                elif o["checked"] == new["checked"] and o["reviewer"] == new["reviewer"]:
                    new["date"] = o["date"]
            out.append(new)
            stats["ok" if good else "ng"] += 1
    return out, stats


def add_sources(t, src):
    """出典のページ。報告は本文の範囲のページ（2ページにまたがれば p.1-2）、ほかはその行の字のあるページ。"""
    for r in t["kansa-kekka"]:
        tl = src.text(r["doc"])
        try:
            ps = sorted(set(pages_of(tl, int(r["kekka_from"]), int(r["kekka_to"]))))
        except ValueError:
            ps = [1]
        r["_source"] = f"{r['doc']} p.{ps[0]}" + (f"-{ps[-1]}" if len(ps) > 1 else "")
    for table, c in (("kansa-jiko", "jiko"), ("kansa-aite", "name"), ("kansa-kankei", "meimoku")):
        for j in t[table]:
            j["_source"] = f"{j['doc']} p.{page_of_value(src.text(j['doc']), j[c])}"


def load_all(root):
    return {n: kozo.load_table(root, n) for n in TABLE_COLS}


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
    out(f"台帳の行 {len(rows)}：段1 で一致 {stats['ok']}、要人確認:段1 {stats['ng']}、人の印を保った {stats['kept']}")
    for r in rows:
        if r["note"].startswith("要人確認:段1"):
            out(f"  {r['table']}\t{r['row']}\t{r['note']}")
    hs = [r for r in rows if "要人確認:H4" in r["note"]]
    out(f"要人確認:H4 の行 {len(hs)}（本 {len({r['row'].split('/')[0] for r in hs})}）")
    for r in hs:
        if r["table"] == "kansa-kekka":
            m = re.search(r"要人確認:H4 [^。]*", r["note"])
            out(f"  {r['row']}\t{m.group(0)}")
    ds = [r for r in rows if "要人確認:団体の監査" in r["note"]]
    out(f"要人確認:団体の監査 の行 {len(ds)}（本 {len({r['row'].split('/')[0] for r in ds})}）")
    lost = [o for o in old if o["reviewer"] and not o["reviewer"].startswith("auto:")
            and o["key"] not in {r["key"] for r in rows}]
    out(f"前の台帳の人の印のうち、key が無くなって保てなかった行 {len(lost)}")
    for o in lost:
        out(f"  {o['table']}\t{o['row']}\t{o['cols']}")
    out(f"台帳：{os.path.relpath(path, root)}。終了コード 0")
    return 0, res, rows, stats


# ---- 段1 の道具の確かめ ----

MUTATIONS = ["kokuji の字を変える", "kansa_bi の字を変える", "相手の name の字を変える", "kekka_to をずらす",
             "事項の字を変える", "関係の gaku を変える", "曜日を変える", "shurui を変える", "相手の name をほかの本の値にする",
             "chakugan の字を変える", "関係の kubun を変える", "合計の行の額と字を変える"]


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
        total = [0] * len(MUTATIONS)
        found = [0] * len(MUTATIONS)
        missed = []

        def rw(table, r):
            return r["doc"] if table == "kansa-kekka" else f"{r['doc']}/{r['n']}"

        def clean(table, cond=lambda r: True):
            return [r for r in t[table] if not res_fail(base, table, rw(table, r)) and cond(r)]

        def run(n, table, r, col, val, check_rows=None):
            t2 = copy.deepcopy(t)
            t2[table][t[table].index(r)][col] = val
            total[n] += 1
            res = check(t2, src)
            targets = check_rows or [(table, rw(table, r))]
            if any(res[k]["fails"] for k in targets):
                found[n] += 1
            else:
                missed.append(f"{MUTATIONS[n]}：{rw(table, r)}")

        for r in spread(clean("kansa-kekka", lambda r: r["kokuji"])):
            run(0, "kansa-kekka", r, "kokuji", swap_char(r["kokuji"]))
        for r in spread(clean("kansa-kekka", lambda r: r["kansa_bi"])):
            run(1, "kansa-kekka", r, "kansa_bi", swap_char(r["kansa_bi"]))
        for r in spread(clean("kansa-aite", lambda r: r["name"])):
            run(2, "kansa-aite", r, "name", "■" + r["name"][1:])
        for r in spread(clean("kansa-kekka", lambda r: r["kekka_to"])):
            run(3, "kansa-kekka", r, "kekka_to", str(int(r["kekka_to"]) - 1))
        for r in spread(clean("kansa-jiko", lambda r: r["jiko"])):
            run(4, "kansa-jiko", r, "jiko", "■" + r["jiko"][1:])
        for r in spread(clean("kansa-kankei", lambda r: r["gaku"])):
            run(5, "kansa-kankei", r, "gaku", str(int(r["gaku"]) + 1))
        for r in spread(clean("kansa-kekka", lambda r: re.search(r"[（(][月火水木金土日][）)]", r["kansa_bi"]))):
            v = re.sub(r"[（(](.)[）)]", lambda m: "（" + WEEK[(WEEK.index(m.group(1)) + 1) % 7] + "）", r["kansa_bi"], count=1)
            run(6, "kansa-kekka", r, "kansa_bi", v)
        for r in spread(clean("kansa-kekka")):
            run(7, "kansa-kekka", r, "shurui", "dantai" if r["shurui"] == "teiki" else "teiki")
        names = sorted({r["name"] for r in t["kansa-aite"] if r["name"]})
        for r in spread(clean("kansa-aite", lambda r: r["name"])):
            v = next((x for x in names if x != r["name"] and x not in r["name"] and r["name"] not in x), "■")
            run(8, "kansa-aite", r, "name", v)
        for r in spread(clean("kansa-kekka", lambda r: r["chakugan"])):
            run(9, "kansa-kekka", r, "chakugan", "■" + r["chakugan"][1:])
        for r in spread(clean("kansa-kankei", lambda r: r["gyo"] == "kobetsu")):
            run(10, "kansa-kankei", r, "kubun", "shitei-kanri" if r["kubun"] != "shitei-kanri" else "zaisei-enjo")
        for r in spread(clean("kansa-kankei", lambda r: r["gyo"] == "gokei")):
            raw = swap_char(r["gaku_raw"])
            t2 = copy.deepcopy(t)
            i = t["kansa-kankei"].index(r)
            t2["kansa-kankei"][i]["gaku_raw"], t2["kansa-kankei"][i]["gaku"] = raw, read_gaku(raw)
            total[11] += 1
            res = check(t2, src)
            if any(c == "検算" for c, _ in res[("kansa-kankei", rw("kansa-kankei", r))]["fails"]):
                found[11] += 1
            else:
                missed.append(f"{MUTATIONS[11]}：{rw('kansa-kankei', r)}")
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
