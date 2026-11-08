"""hino-town-docs の構造化の層の道具（DOCS_SCHEMA.md §13）。標準ライブラリだけで動く。

  python3 -B tools/kozo.py hosei-kian <年度>    補正予算の概要の原典の文字から、その年度の補正予算の表を起票する（13.6・13.9）
  python3 -B tools/kozo.py hosei-check <年度>   補正予算の表を原典の文字で照合・検算し、検収の台帳 data/struct/audit/hosei-<年度>.tsv を書く（13.10 段1）
  python3 -B tools/kozo.py selftest             段1 の道具に、誤りを入れた表を読ませて、誤りを見つけることを確かめる（13.10、補遺 L）

年度は r6 の形。起票は data/text_raw/ を基にし、data/text/ は企業会計の表の会計の名前にだけ使う。
読めないもの・決まりで決まらないものは空にして note に書く。決まりに無いものが出たら止める（終了コード 2）。
hosei-check は、検算が合わない回があれば終了コード 1 で終わる（台帳は書き、その回の行の checked を空にする）。
hosei-check は、段4 の抜き取り（13.10 の H2・H4）の行の note を「要人確認:」で始める。
"""
import copy
import csv
import hashlib
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

ROOT = htd.ROOT

KAI_COLS = ["id", "fiscal_year", "kaikei", "gou", "title", "prev", "prev_from", "label_mae", "label_hosei", "note"]
SHIRYO_COLS = ["kai", "doc", "part", "pages", "note"]
AMT9 = ["tosho", "tosho_ippan", "mae", "mae_ippan", "hosei", "hosei_ippan", "hoseigo", "hoseigo_ippan", "taitosho"]
KAN_COLS = ["kai", "key", "kan_no", "name"] + AMT9 + ["unit", "doc", "page", "note"]
MARU_COLS = ["kai", "key", "kind", "mark", "name", "amount", "unit", "line", "desc", "sub", "doc", "page", "note"]
JIKO_COLS = ["kai", "key", "kind", "kubun_raw", "kubun", "kaikei_raw", "kaikei", "name", "amount", "unit", "line", "doc", "page",
             "note"]
AMT4 = ["tosho", "mae", "hosei", "hoseigo"]
KAIKEI_COLS = ["kai", "key", "table", "name", "kaikei", "flow", "part"] + AMT4 + ["unit", "doc", "page", "note"]
AUDIT_COLS = ["key", "table", "row", "cols", "value", "unit", "source", "checked", "reviewer", "date", "note"]
ISSUE_COLS = ["no", "kind", "page", "where", "issue", "evidence", "confidence", "status", "action"]

TABLES = {   # 表の名前 → (ファイル, 列)
    "hosei-kai": ("hosei-kai.tsv", KAI_COLS),
    "hosei-shiryo": ("hosei-shiryo.tsv", SHIRYO_COLS),
    "hosei-kan": ("hosei-kan.tsv", KAN_COLS),
    "hosei-maru": ("hosei-maru.tsv", MARU_COLS),
    "hosei-jiko": ("hosei-jiko.tsv", JIKO_COLS),
    "hosei-kaikei": ("hosei-kaikei.tsv", KAIKEI_COLS),
}
PART_ORDER = ["gaiyou", "kan", "toku", "kigyo", "kaiken"]

# 13.3 会計の略（名前は字の間の空白を除いた形）
KAIKEI = [("ippan", "一般会計"), ("kokuho", "国民健康保険特別会計"), ("kaigo", "介護保険特別会計"),
          ("koki", "後期高齢者医療特別会計"), ("kansui", "簡易水道特別会計"), ("noshu", "農業集落排水事業特別会計"),
          ("koshita", "公共下水道事業特別会計"), ("nishiyama", "西山財産区会計"), ("suido", "水道事業会計"),
          ("gesuido", "下水道事業会計")]
KIGYO = ("suido", "gesuido")
KUBUN = {"追加": "tsuika", "変更": "henko", "廃止": "haishi"}
JIKO_KIND = [("繰越明許費", "kurikoshi"), ("債務負担行為", "saimu"), ("地方債", "chihosai")]

TODAY = date.today().isoformat()


class Stop(Exception):
    """決まりに無いものが出たので止める。"""


# ---- 字の扱い ----

def norm(s):
    """比べるときだけ使う形（NFKC、空白を除く）。表に書く字には使わない。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def kaikei_abbr(name):
    """会計の名前の字から 13.3 の略。字の間の空白とかっこ書きを除いて比べる。表に無ければ None。"""
    n = re.sub(r"\([^)]*\)", "", norm(name))
    for abbr, full in KAIKEI:
        if n == full:
            return abbr
    return None


NUM_RE = re.compile(r"^[△▲\-]?\s*[0-9０-９][0-9０-９,，]*(?:[.．][0-9０-９]+)?$")
WORDS = ("－", "―", "‐", "-", "−", "皆増", "皆減")


def read_num(tok, integer=False):
    """13.5 の数の読み。「△」「▲」「-」は負号、カンマを除く。小数は原典の桁のまま。
    「－」「皆増」「皆減」は字のまま返す。読めなければ ValueError。"""
    s = tok.strip()
    if s in WORDS:
        if integer:
            raise ValueError(tok)
        return s
    t = unicodedata.normalize("NFKC", s).replace(" ", "").replace(",", "")
    neg = False
    if t[:1] in ("△", "▲", "-"):
        neg, t = True, t[1:]
    if not re.fullmatch(r"[0-9]+(\.[0-9]+)?", t):
        raise ValueError(tok)
    if "." in t:
        if integer:
            raise ValueError(tok)
        return ("-" if neg else "") + t
    v = int(t)
    return str(-v if neg else v)


def split_nums(line, words=False, pct=False):
    """行の末尾から数の並びを取る。(数より前の字, [数の字])。数の字は原典の字のまま（「△ 1,531」）。
    words が真なら、最後の1つに「－」「皆増」「皆減」を許す。pct が真なら、数の末尾の % を除いて読む。"""
    spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", line)]
    out = []
    i = len(spans) - 1
    while i >= 0:
        a, b = spans[i]
        x = line[a:b]
        y = x[:-1] if pct and x[-1:] in ("%", "％") and len(x) > 1 else x
        if words and not out and x in WORDS:
            out.append((a, x))
            i -= 1
            continue
        if NUM_RE.match(y):
            if y[0] not in "△▲-" and i > 0 and line[spans[i - 1][0]:spans[i - 1][1]] in ("△", "▲"):
                a0 = spans[i - 1][0]
                out.append((a0, line[a0:a + len(y)]))
                i -= 2
                continue
            out.append((a, y))
            i -= 1
            continue
        break
    out.reverse()
    head = line[:out[0][0]].rstrip() if out else line.rstrip()
    return head.strip(), [t for _, t in out]


def read_pages(path):
    """改ページで分けたページ（1から）。各ページは前後の空白を除いた行の並び。"""
    with open(path, encoding="utf-8") as f:
        pages = f.read().split("\f")
    if pages and pages[-1].strip() == "":
        pages = pages[:-1]
    return [[ln.strip() for ln in p.split("\n")] for p in pages]


def join_note(*parts):
    out = []
    for p in parts:
        for x in (p or "").split("。"):
            if x and x not in out:
                out.append(x)
    return "。".join(out)


# ---- 款の表を読む ----

KAN_TITLE = re.compile(r"^令和([0-9]+)年度日野町一般会計補正予算(?:\(専決処分\))?\(第([0-9]+)号\)$")
TOKU_TITLE = re.compile(r"^令和([0-9]+)年度日野町特別会計補正予算$")
KIGYO_TITLE = re.compile(r"^令和([0-9]+)年度日野町企業会計補正予算$")
TOTAL_NAME = {"in-total": "歳入合計", "out-total": "歳出合計(目的別)", "sei-total": "歳出合計(性質別)"}


def find_label(lines):
    """2つめの列と今回補正額の列の見出しの字。(label_mae の候補, label_hosei の候補)"""
    mae, hosei = [], []
    for ln in lines:
        toks = ln.split()
        cands = [ln] if toks and all(len(t) == 1 for t in toks) else toks
        for t in cands:
            n = norm(t)
            if n.endswith("予算額") and n not in ("当初予算額", "補正後予算額") and not re.search(r"[①-⑳]", n):
                if norm(t) not in [norm(x) for x in mae]:
                    mae.append(t)
            if n.startswith("今回補正額"):
                if norm(t) not in [norm(x) for x in hosei]:
                    hosei.append(t)
    return mae, hosei


def read_kan(pages):
    """款の表。戻り値 dict(title, year, gou, page, rows, label_mae, label_hosei, notes)。表が無ければ None。
    rows の各行は KAN_COLS の列（kai・doc・unit は空）と、数の字の並び _toks。"""
    start = None
    for pi, lines in enumerate(pages, 1):
        for ln in lines:
            m = KAN_TITLE.match(norm(ln))
            if m:
                if start is not None:
                    raise Stop("一般会計の款の表の題が2つある（1本に一般会計の回が2つ）")
                start, title, year, gou = pi, ln, int(m.group(1)), int(m.group(2))
    if start is None:
        return None
    res = dict(title=title, year=year, gou=gou, page=start, rows=[], notes=[], pages=[])
    sec = phase = None
    cur_in = cur_sei = None
    k_in = k_sei = 0
    keys = set()
    for pi in range(start, len(pages) + 1):
        lines = pages[pi - 1]
        ns = [norm(x) for x in lines]
        if pi > start and not ("【歳入】" in ns or "【歳出】" in ns):
            break
        if any(TOKU_TITLE.match(x) or KIGYO_TITLE.match(x) for x in ns):
            break
        res["pages"].append(pi)
        for ln in lines:
            n = norm(ln)
            if n == "【歳入】":
                sec = "in"
                continue
            if n == "【歳出】":
                sec, phase = "out", "mokuteki"
                continue
            if sec is None:
                continue
            head, toks = split_nums(ln, words=True)
            if len(toks) < 4:
                continue
            m = re.match(r"^([0-9０-９]+)\s*(.*)$", head)
            row = dict((c, "") for c in KAN_COLS)
            row["page"] = str(pi)
            notes = []
            if sec == "in":
                if phase == "in-done":
                    raise Stop(f"p.{pi}：歳入合計の後に歳入の行がある：{ln}")
                if not head:
                    key = "in-total"
                    phase = "in-done"
                elif m:
                    cur_in = int(unicodedata.normalize("NFKC", m.group(1)))
                    k_in = 0
                    key = f"in-{cur_in:02d}"
                    row["kan_no"] = str(cur_in)
                    row["name"] = m.group(2).strip()
                else:
                    if cur_in is None:
                        raise Stop(f"p.{pi}：款の番号の前に内訳の行がある：{ln}")
                    if norm(head).startswith("うち"):
                        key = f"in-{cur_in:02d}-u"
                    else:
                        k_in += 1
                        key = f"in-{cur_in:02d}-{k_in}"
                    row["name"] = head
            else:
                if not head:
                    if phase == "mokuteki":
                        key, phase = "out-total", "sei"
                    elif phase == "sei":
                        key, phase = "sei-total", "out-done"
                    else:
                        raise Stop(f"p.{pi}：歳出の合計の行が3つ以上ある")
                elif phase == "mokuteki":
                    if not m:
                        raise Stop(f"p.{pi}：目的別の款に番号の無い行がある：{ln}")
                    key = f"out-{int(unicodedata.normalize('NFKC', m.group(1))):02d}"
                    row["kan_no"] = str(int(unicodedata.normalize("NFKC", m.group(1))))
                    row["name"] = m.group(2).strip()
                elif phase == "sei":
                    if m:
                        cur_sei = int(unicodedata.normalize("NFKC", m.group(1)))
                        k_sei = 0
                        key = f"sei-{cur_sei}"
                        row["kan_no"] = str(cur_sei)
                        row["name"] = m.group(2).strip()
                    else:
                        if cur_sei is None:
                            raise Stop(f"p.{pi}：性質別の区分の前に内訳の行がある：{ln}")
                        k_sei += 1
                        key = f"sei-{cur_sei}-{k_sei}"
                        row["name"] = head
                else:
                    raise Stop(f"p.{pi}：歳出合計（性質別）の後に行がある：{ln}")
            if key in keys:
                raise Stop(f"p.{pi}：款の行の key {key} が2つある")
            keys.add(key)
            row["key"] = key
            if key in TOTAL_NAME:
                hit = [x for x in lines if norm(x) == TOTAL_NAME[key]]
                if hit:
                    row["name"] = hit[0]
                else:
                    notes.append("合計の行の名前の字が同じページに無い")
            if len(toks) == 9:
                for c, t in zip(AMT9, toks):
                    try:
                        row[c] = read_num(t, integer=(c != "taitosho"))
                    except ValueError:
                        notes.append(f"{c}の字「{t}」を数に読めない")
            else:
                notes.append(f"数の字が{len(toks)}個で、空欄の列を原典の文字から決められない")
                row["_unplaced"] = True
            row["_toks"] = toks
            row["note"] = join_note(*notes)
            res["rows"].append(row)
    lab_mae, lab_hosei = find_label(pages[start - 1])
    res["label_mae"], res["label_hosei"] = lab_mae, lab_hosei
    for pi in res["pages"][1:]:
        m2, h2 = find_label(pages[pi - 1])
        if [norm(x) for x in m2] != [norm(x) for x in lab_mae] or [norm(x) for x in h2] != [norm(x) for x in lab_hosei]:
            res["notes"].append(f"p.{pi} の列の見出しの字が p.{start} と違う")
    return res


# ---- 概要の本文を読む（主な内容の行・事項） ----

FW_HEAD = re.compile(r"^[０-９]{1,2}[\s．.]")
SUB_HEAD = re.compile(r"^[（(][0-9０-９]{1,2}[）)]")
KA_HEAD = re.compile(r"^【[^】]*】$")
PAGE_NO = re.compile(r"^-\s*[0-9０-９]+\s*-$")
AMT_TAIL = r"([△▲\-]?\s*[0-9０-９][0-9０-９,，]*|－)\s*千円"
MARU_RE = re.compile(r"^([○〇◯●・])\s*(.*?)\s*" + AMT_TAIL + "$")
TOKU_RE = re.compile(r"^([①-⑳]?)\s*(.+?)\s*" + AMT_TAIL + "$")
TOKU_NAME = re.compile(r"^(.+?会計)(補正予算)?\(第([0-9]+)号\)$")
AMT_ONLY = re.compile(r"^" + AMT_TAIL + "$")
KUBUN_RE = re.compile(r"^[①-⑨]?\s*(追加|変更|廃止)$")


def body_pages(pages):
    """概要の本文のページ（表紙「計数資料」か款の表の題のページより前）。"""
    for pi, lines in enumerate(pages, 1):
        for ln in lines:
            n = norm(ln)
            if "計数資料" in n or KAN_TITLE.match(n):
                return pi - 1
    return len(pages)


def read_toku_line(ln):
    """特別会計・企業会計の主な内容の行。(mark, title, 会計の名前の字, gou, 金額の字) か None。"""
    m = TOKU_RE.match(ln)
    if not m:
        return None
    t = TOKU_NAME.match(norm(m.group(2)))
    if not t:
        return None
    # 会計の名前の字（原典の字）は、題の字の「補正予算」か「（第」より前
    title = m.group(2)
    cut = re.search(r"補\s*正\s*予\s*算|[（(]\s*第", title)
    return m.group(1), title, title[:cut.start()].rstrip(), int(t.group(3)), m.group(3)


def read_body(pages):
    """主な内容の行と事項。戻り値 dict(maru=[...], toku=[...], jiko=[...], last=本文の最後のページ)。
    行は dict（page・line・mark・name・amount の字・desc・sub・group など）。"""
    last = body_pages(pages)
    maru, toku, jiko = [], [], []
    sec = None
    jigyo_head = ka = None
    kind = kubun_raw = kaikei_raw = None
    cur = []          # 説明の文を待っている印の行（続いた行の組）
    desc = []
    sub = []
    gid = [0]

    def close():
        """cur の組の行に desc を書く。印の行が続いたあとの説明の文は、続いた行すべてのもの（13.6）。"""
        if cur:
            gid[0] += 1
            for it in cur:
                it["desc"] = "".join(desc)
                it["sub"] = "".join(sub)
                it["group"] = gid[0]
        cur.clear()
        desc.clear()
        sub.clear()

    def start(it):
        if desc or sub:
            close()
        cur.append(it)

    for pi in range(1, last + 1):
        if desc or sub:
            close()       # 説明の文はページの終わりまで。説明の文がまだ無い組は次のページへ続ける
        lines = pages[pi - 1]
        i = 0
        while i < len(lines):
            ln = lines[i]
            i += 1
            if not ln or PAGE_NO.match(ln):
                continue
            n = norm(ln)
            if FW_HEAD.match(ln):
                close()
                if re.search(r"(特別会計|企業会計)補正予算の概要", n):
                    sec = "toku"
                elif "その他" in n:
                    sec, kind, kubun_raw, kaikei_raw = "jiko", None, None, None
                else:
                    sec = None
                continue
            if SUB_HEAD.match(ln):
                close()
                if "歳出の主な内容" in n:
                    sec = "maru"
                elif "補正の事業内容" in n:
                    sec, jigyo_head, ka = "jigyo", ln, None
                elif sec == "jiko":
                    kind = next((k for w, k in JIKO_KIND if w in n), None)
                    kubun_raw = kaikei_raw = None
                    if kind is None:
                        sec = None
                elif sec == "toku":
                    pass
                else:
                    sec = None
                continue
            if sec == "jiko":
                m = KUBUN_RE.match(ln)
                if m:
                    kubun_raw = ln
                    continue
                if KA_HEAD.match(ln):
                    if kaikei_abbr(ln[1:-1]) is None:
                        raise Stop(f"p.{pi}：13.3 の表に無い会計の見出し：{ln}")
                    kaikei_raw = ln
                    continue
                if ln.startswith("・"):
                    line, page = ln, pi
                    if not re.search(r"千円$", ln) and i < len(lines) and AMT_ONLY.match(lines[i]):
                        line = ln + " " + lines[i]
                        i += 1
                    jiko.append(dict(page=page, line=line, kind=kind, kubun_raw=kubun_raw or "",
                                     kaikei_raw=kaikei_raw or ""))
                continue
            if KA_HEAD.match(ln):
                close()
                if sec == "jigyo":
                    ka = ln
                continue
            if sec == "maru" and ln[0] in "○〇◯●":
                it = dict(kind="maru", page=pi, line=ln)
                maru.append(it)
                start(it)
                continue
            if sec == "jigyo" and ln.startswith("・"):
                it = dict(kind="maru", page=pi, line=ln, jigyo=(jigyo_head, ka))
                maru.append(it)
                start(it)
                continue
            if sec == "toku":
                t = read_toku_line(ln)
                if t:
                    it = dict(kind="toku", page=pi, line=ln, toku=t)
                    toku.append(it)
                    start(it)
                    continue
            if cur:
                if sec == "toku" and ln.startswith("※"):
                    sub.append(ln)
                else:
                    desc.append(ln)
            elif sec in ("maru", "jigyo", "toku") and ln:
                # 印の行の前の、どの行にも付かない字（ページの頭で前のページの説明が続くもの）
                prev = (maru + toku)[-1] if (maru or toku) else None
                if prev is not None and prev["page"] == pi - 1:
                    prev.setdefault("cont", []).append(ln)
    close()
    return dict(maru=maru, toku=toku, jiko=jiko, last=last)


def item_fields(it):
    """主な内容の行・事項の行から mark・name・amount の字を取る。"""
    ln = it["line"]
    if it["kind"] == "toku":
        mark, title, _, _, amt = it["toku"]
        return mark, title, amt
    m = MARU_RE.match(ln)
    if not m:
        return ln[:1] if ln[:1] in "○〇◯●・" else "", "", None
    return m.group(1), m.group(2), m.group(3)


# ---- 会計の額の表を読む ----

def read_kaikei_tables(pages, text_pages):
    """特別会計の表と企業会計の表。戻り値 dict(toku=dict(page, rows, label_mae, label_hosei), kigyo=...)。"""
    out = {}
    for pi, lines in enumerate(pages, 1):
        ns = [norm(x) for x in lines]
        if any(TOKU_TITLE.match(x) for x in ns):
            out["toku"] = read_toku_table(pi, lines)
        if any(KIGYO_TITLE.match(x) for x in ns):
            tp = text_pages[pi - 1] if text_pages and pi <= len(text_pages) else None
            out["kigyo"] = read_kigyo_table(pi, lines, tp)
    return out


def read_toku_table(pi, lines):
    rows = []
    total_name = next((x for x in lines if norm(x) == "合計"), "")
    for ln in lines:
        head, toks = split_nums(ln, words=True, pct=True)
        if len(toks) < 4:
            continue
        row = dict((c, "") for c in KAIKEI_COLS)
        row.update(table="toku", page=str(pi))
        notes = []
        if head:
            row["name"] = head
            ab = kaikei_abbr(head)
            if ab is None:
                raise Stop(f"p.{pi}：13.3 の表に無い会計：{head}")
            row["kaikei"] = ab
        else:
            row["name"] = total_name
            row["_total"] = True
            if not total_name:
                notes.append("合計の行の名前の字が同じページに無い")
        if len(toks) != 6:
            notes.append(f"数の字が{len(toks)}個（当初・2つめ・今回補正・補正後・伸び率2つの6個でない）")
        for c, t in zip(AMT4, toks[:4]):
            try:
                row[c] = read_num(t, integer=True)
            except ValueError:
                notes.append(f"{c}の字「{t}」を数に読めない")
        row["_toks"] = toks
        row["note"] = join_note(*notes)
        rows.append(row)
    mae, hosei = find_label(lines)
    return dict(page=pi, rows=rows, label_mae=mae, label_hosei=hosei)


def kigyo_names(text_lines):
    """data/text/ の企業会計の表のページから、会計の名前の字と、その後の8つの数の字の組の並び。"""
    out = []
    if text_lines is None:
        return out
    toks = []
    for ln in text_lines:
        ab = kaikei_abbr(ln) if ln else None
        if ab in KIGYO:
            toks.append(("name", ln, ab))
            continue
        for t in re.findall(r"[△▲]\s*[0-9][0-9,]*|\S+", ln):
            toks.append(("tok", t, None))
    for i, (k, v, ab) in enumerate(toks):
        if k != "name":
            continue
        nums = []
        for k2, v2, _ in toks[i + 1:]:
            if k2 != "tok":
                break
            try:
                nums.append(read_num(v2, integer=True))
            except ValueError:
                break
            if len(nums) == 8:
                break
        out.append(dict(name=v, kaikei=ab, nums=nums if len(nums) == 8 else None))
    return out


def read_kigyo_table(pi, lines, text_lines):
    rows = []
    total_name = next((x for x in lines if norm(x) == "合計"), "")
    names = kigyo_names(text_lines)
    used = [False] * len(names)
    flow = None
    mae, hosei = find_label(lines)
    for ln in lines:
        n = norm(ln)
        if n == "【収入】":
            flow = "shunyu"
            continue
        if n == "【支出】":
            flow = "shishutsu"
            continue
        head, toks = split_nums(ln)
        if flow is None or head or len(toks) != 8:
            continue
        vals = []
        notes = []
        for t in toks:
            try:
                vals.append(read_num(t, integer=True))
            except ValueError:
                vals.append("")
                notes.append(f"数の字「{t}」を読めない")
        hit = None
        for j, e in enumerate(names):
            if not used[j] and e["nums"] == vals:
                hit = j
                break
        if hit is not None:
            used[hit] = True
            name, ab, total = names[hit]["name"], names[hit]["kaikei"], False
            notes.append("会計の名前は data/text/ による")
        else:
            name, ab, total = "", "", None
        for part, off in (("shueki", 0), ("shihon", 1)):
            row = dict((c, "") for c in KAIKEI_COLS)
            row.update(table="kigyo", page=str(pi), flow=flow, part=part, name=name, kaikei=ab or "")
            for c, v in zip(AMT4, vals[off::2]):
                row[c] = v
            row["_toks"] = toks
            row["_total"] = total
            row["note"] = join_note(*notes)
            rows.append(row)
    # 名前の当たらなかった行：流れ（収入・支出）ごとに1つだけなら合計の行
    for fl in ("shunyu", "shishutsu"):
        rest = [r for r in rows if r["flow"] == fl and r["_total"] is None]
        if len(rest) == 2 and total_name:
            for r in rest:
                r["name"], r["_total"] = total_name, True
        else:
            for r in rest:
                r["_total"] = False
                r["note"] = join_note(r["note"], "会計の名前を data/text/ で決められない")
    return dict(page=pi, rows=rows, label_mae=mae, label_hosei=hosei)


def read_kibo(pages):
    """1ページの「補正予算の規模」の表。{一般会計: [4つの額], ...}。無ければ {}。"""
    out = {}
    if not pages:
        return out
    for ln in pages[0]:
        head, toks = split_nums(ln, pct=True)
        if norm(head) in ("一般会計", "特別会計", "企業会計", "合計") and len(toks) >= 4:
            try:
                out[norm(head)] = [read_num(t, integer=True) for t in toks[:4]]
            except ValueError:
                pass
    return out


# ---- 表の読み書き ----

def struct_dir(root):
    return os.path.join(root, "data", "struct")


def load_table(root, name):
    fn, cols = TABLES[name]
    path = os.path.join(struct_dir(root), fn)
    if not os.path.exists(path):
        return []
    try:
        return htd.read_tsv(path, cols)
    except htd.Stop as e:
        raise Stop(str(e))


def write_table(path, cols, rows):
    for r in rows:
        for c in cols:
            v = str(r.get(c, ""))
            if "\t" in v or "\n" in v or "\r" in v:
                raise Stop(f"{os.path.basename(path)}：{r.get('kai', r.get('id', ''))}/{r.get('key', '')} の {c} にタブか改行がある")

    def w(f):
        cw = csv.writer(f, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, escapechar=None, quotechar=None)
        cw.writerow(cols)
        for r in rows:
            cw.writerow([r.get(c, "") for c in cols])
    htd.write_atomic(path, w)


def year_info(year):
    m = re.fullmatch(r"([rh])([0-9]+)", year)
    if not m:
        raise Stop(f"年度は r6 の形で書く（{year}）")
    n = int(m.group(2))
    return n, (2018 if m.group(1) == "r" else 1988) + n


def year_docs(root, year):
    path = os.path.join(root, "data", "sources.tsv")
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
    rows = htd.read_tsv(path, head)
    return sorted(r["id"] for r in rows
                  if r["id"].startswith(f"yosan-{year}-hosei-") or r["id"].startswith(f"yosan-{year}-tosho-hosei"))


# ---- 起票 ----

def kai_id(year, abbr, gou):
    return f"{year}-{abbr}-{gou:02d}"


def pick_label(cands, what, notes):
    if len(cands) == 1:
        return cands[0]
    notes.append(f"{what}の字を1つに決められない（{'・'.join(cands) if cands else '無い'}）")
    return ""


GOU_HEAD = re.compile(r"^第?([0-9]+)号補正後予算額$")


def decide_prev(kais):
    """13.6「回の順」。kais は同じ年度の回の dict のリスト（id・kaikei・gou・label_mae・note を持つ）。prev・prev_from を書く。"""
    by = {}
    for k in kais:
        by.setdefault(k["kaikei"], []).append(k)
    for lst in by.values():
        lst.sort(key=lambda k: k["gou"])
        gmap = {k["gou"]: k for k in lst}
        named = set()
        for k in lst:
            k["prev"], k["prev_from"] = "", ""
            m = GOU_HEAD.match(norm(k["label_mae"]))
            if m:
                n = int(m.group(1))
                if n in gmap:
                    k["prev"], k["prev_from"] = gmap[n]["id"], "見出し"
                    named.add(n)
                else:
                    k["note"] = join_note(k["note"], f"見出しの第{n}号の回が表に無い")
        rest = []
        for k in lst:
            if k["prev_from"] or k["gou"] == lst[0]["gou"]:
                continue
            g1 = k["gou"] - 1
            if g1 in gmap and g1 not in named:
                k["prev"], k["prev_from"] = gmap[g1]["id"], "号の順"
            elif g1 not in gmap:
                k["note"] = join_note(k["note"], "号の1つ前の回が表に無いので prev を決められない")
            else:
                rest.append(k)
        for k in rest:
            taken = {x["prev"] for x in lst if x["prev"]}
            cands = [x for x in lst if x["gou"] < k["gou"] and x["id"] not in taken and x is not k]
            if len(cands) == 1:
                k["prev"], k["prev_from"] = cands[0]["id"], "号の順"
                k["note"] = join_note(k["note"], "号の1つ前の回がほかの回の見出しで名指しされているので、13.6 のただし書きによった")
            else:
                k["note"] = join_note(k["note"], "13.6 のただし書きの候補が1つでないので prev を決められない")
    return kais


def read_doc(root, doc, ynum):
    """1本の資料を読む。"""
    rp = os.path.join(root, "data", "text_raw", doc + ".txt")
    if not os.path.exists(rp):
        raise Stop(f"{doc}：data/text_raw/{doc}.txt が無い")
    pages = read_pages(rp)
    tp = os.path.join(root, "data", "text", doc + ".txt")
    tpages = read_pages(tp) if os.path.exists(tp) else None
    kan = read_kan(pages)
    if kan is None:
        raise Stop(f"{doc}：一般会計の款の表の題が無い")
    if kan["year"] != ynum:
        raise Stop(f"{doc}：款の表の題の年度（令和{kan['year']}年度）が指定の年度と違う")
    body = read_body(pages)
    tables = read_kaikei_tables(pages, tpages)
    return dict(doc=doc, pages=pages, kan=kan, body=body, tables=tables)


def build(root, year):
    """その年度の資料を読み、表ごとの新しい行（key は仮）を作る。"""
    ynum, fy = year_info(year)
    docs = year_docs(root, year)
    reads = [read_doc(root, d, ynum) for d in docs]
    kai, shiryo, kan, maru, jiko, kaikei = [], [], [], [], [], []
    for rd in reads:
        doc, k = rd["doc"], rd["kan"]
        notes = list(k["notes"])
        ip = kai_id(year, "ippan", k["gou"])
        if any(x["id"] == ip for x in kai):
            raise Stop(f"{doc}：回 {ip} がほかの資料にもある")
        kai.append(dict(id=ip, fiscal_year=str(fy), kaikei="ippan", gou=k["gou"], title=k["title"],
                        label_mae=pick_label(k["label_mae"], "2つめの列の見出し", notes),
                        label_hosei=pick_label(k["label_hosei"], "今回補正額の列の見出し", notes),
                        note="", _doc=doc))
        kai[-1]["note"] = join_note(*notes)
        last = rd["body"]["last"]
        shiryo.append(dict(kai=ip, doc=doc, part="gaiyou", pages=f"1-{last}" if last > 1 else "1", note=""))
        kp = k["pages"]
        shiryo.append(dict(kai=ip, doc=doc, part="kan", pages=f"{kp[0]}-{kp[-1]}" if len(kp) > 1 else str(kp[0]), note=""))
        for r in k["rows"]:
            r.update(kai=ip, unit="千円", doc=doc)
            kan.append(r)
        # 主な内容の行
        for it in rd["body"]["maru"]:
            mark, name, amt = item_fields(it)
            row = dict((c, "") for c in MARU_COLS)
            row.update(kai=ip, kind="maru", mark=mark, name=name, unit="千円", line=it["line"], desc=it.get("desc", ""),
                       sub=it.get("sub", ""), doc=doc, page=str(it["page"]))
            notes = []
            if "jigyo" in it:
                h, ka = it["jigyo"]
                notes.append(f"「{h}」の{ka or ''}の下の行")
            fill_amount(row, amt, notes)
            desc_notes(it, notes)
            row["note"] = join_note(*notes)
            row["_group"] = (doc, it.get("group"))
            maru.append(row)
        tk_kai = {}
        tbl = rd["tables"]
        for it in rd["body"]["toku"]:
            mark, title, kname, gou, amt = it["toku"]
            ab = kaikei_abbr(kname)
            if ab is None:
                raise Stop(f"{doc} p.{it['page']}：13.3 の表に無い会計：{kname}")
            if ab == "ippan":
                raise Stop(f"{doc} p.{it['page']}：特別会計・企業会計の節に一般会計の行がある")
            kid = kai_id(year, ab, gou)
            if kid in tk_kai or any(x["id"] == kid for x in kai):
                raise Stop(f"{doc}：回 {kid} が2つある")
            notes = []
            tname = "kigyo" if ab in KIGYO else "toku"
            t = tbl.get(tname)
            if t:
                lm = pick_label(t["label_mae"], "2つめの列の見出し", notes)
                lh = pick_label(t["label_hosei"], "今回補正額の列の見出し", notes)
            else:
                lm = lh = ""
                notes.append("同じ資料に会計の額の表が無いので、列の見出しの字が無い")
            kai.append(dict(id=kid, fiscal_year=str(fy), kaikei=ab, gou=gou, title=title, label_mae=lm, label_hosei=lh,
                            note=join_note(*notes), _doc=doc))
            tk_kai[kid] = ab
            shiryo.append(dict(kai=kid, doc=doc, part="gaiyou", pages=str(it["page"]), note=""))
            if t:
                shiryo.append(dict(kai=kid, doc=doc, part=tname, pages=str(t["page"]), note=""))
            row = dict((c, "") for c in MARU_COLS)
            row.update(kai=kid, kind="toku", mark=mark, name=title, unit="千円", line=it["line"], desc=it.get("desc", ""),
                       sub=it.get("sub", ""), doc=doc, page=str(it["page"]))
            notes = []
            fill_amount(row, amt, notes)
            desc_notes(it, notes)
            row["note"] = join_note(*notes)
            row["_group"] = (doc, it.get("group"))
            maru.append(row)
        # 事項
        for it in rd["body"]["jiko"]:
            row = dict((c, "") for c in JIKO_COLS)
            notes = []
            if it["kind"] is None:
                raise Stop(f"{doc} p.{it['page']}：事項の種類の見出しより前に「・」の行がある：{it['line']}")
            kr = it["kaikei_raw"]
            if kr:
                ab = kaikei_abbr(kr[1:-1])
                kid = kai_id(year, ab, k["gou"]) if ab == "ippan" else next((x for x, a in tk_kai.items() if a == ab), None)
                if kid is None:
                    raise Stop(f"{doc} p.{it['page']}：会計の見出し {kr} の会計の回が同じ資料に無い")
            else:
                ab, kid = "", ip
            m = MARU_RE.match(it["line"])
            row.update(kai=kid, kind=it["kind"], kubun_raw=it["kubun_raw"],
                       kubun=KUBUN[KUBUN_RE.match(it["kubun_raw"]).group(1)] if it["kubun_raw"] else "",
                       kaikei_raw=kr, kaikei=ab, unit="千円", line=it["line"], doc=doc, page=str(it["page"]))
            if m and m.group(1) == "・":
                row["name"] = m.group(2)
                fill_amount(row, m.group(3), notes)
            else:
                row["name"] = re.sub(r"^・\s*", "", it["line"])
                notes.append("金額の字が行に無い")
            row["note"] = join_note(*notes)
            jiko.append(row)
        # 会計の額
        for tname in ("toku", "kigyo"):
            t = tbl.get(tname)
            if not t:
                continue
            for r in t["rows"]:
                r.update(kai=ip, unit="千円", doc=doc)
                kaikei.append(r)
    decide_prev(kai)
    return dict(docs=docs, kai=kai, shiryo=shiryo, kan=kan, maru=maru, jiko=jiko, kaikei=kaikei)


def desc_notes(it, notes):
    """説明の文が無い行と、ページの頭のどの行にも付かない字の注記。共有の注記は key が決まってから書く（kian）。"""
    if not it.get("desc"):
        notes.append("説明の文は原典に無い")
    if it.get("cont"):
        notes.append("desc はページの終わりで切った（次のページの頭に、どの行にも付かない字がある）")


def fill_amount(row, amt, notes):
    if amt is None:
        notes.append("金額の字が行に無い")
        return
    if amt.strip() == "－":
        notes.append("原典の金額は「－」")
        return
    try:
        row["amount"] = read_num(amt, integer=True)
    except ValueError:
        notes.append(f"金額の字「{amt}」を数に読めない")


def merge_kan(old, new, report):
    """款の行。key（回の id と key）で合わせる。"""
    oldmap = {(r["kai"], r["key"]): r for r in old}
    seen = set()
    out = []
    for i, r in enumerate(new):
        k = (r["kai"], r["key"])
        seen.add(k)
        o = oldmap.get(k)
        if r.get("_unplaced"):
            reread = [read_num(t) if NUM_RE.match(t) or t in WORDS else t for t in r["_toks"]]
            if o and [o[c] for c in AMT9 if o[c] != ""] == reread:
                for c in AMT9:
                    r[c] = o[c]
                report["kept_values"].append(f"{r['kai']}/{r['key']}")
        if o:
            r["note"] = o["note"]   # 既存の行の note はそのまま保つ（道具の note は新しい行にだけ付ける）
        r["_ord"] = i
        out.append(r)
    for k, o in oldmap.items():
        if k not in seen:
            o = dict(o)
            o["_ord"] = 10 ** 9
            out.append(o)
            report["left"].append(f"hosei-kan {k[0]}/{k[1]}")
    return out


def merge_keyed(old, new, prefix_of, match_of, table, report):
    """主な内容の行・事項・会計の額。（kai と種類と line か名前）で既存の行と合わせ、合う行は key を保つ。"""
    by_kai = {}
    for o in old:
        by_kai.setdefault(o["kai"], []).append(o)
    used = set()
    out = []
    nextno = {}
    for o in old:
        p = prefix_of(o)
        n = int(o["key"][1:]) if re.fullmatch(r"[a-z][0-9]{3}", o["key"]) else 0
        nextno[(o["kai"], p)] = max(nextno.get((o["kai"], p), 0), n)
    pending = []
    for i, r in enumerate(new):
        r["_ord"] = i
        hit = None
        for o in by_kai.get(r["kai"], []):
            if id(o) not in used and match_of(o) == match_of(r):
                hit = o
                break
        if hit is not None:
            used.add(id(hit))
            r["key"] = hit["key"]
            r["note"] = hit["note"]   # 既存の行の note はそのまま保つ
            r["_old"] = True
        else:
            pending.append(r)
        out.append(r)
    for r in pending:
        p = prefix_of(r)
        n = nextno.get((r["kai"], p), 0) + 1
        nextno[(r["kai"], p)] = n
        r["key"] = f"{p}{n:03d}"
    for o in old:
        if id(o) not in used:
            o = dict(o)
            o["_ord"] = 10 ** 9
            out.append(o)
            report["left"].append(f"{table} {o['kai']}/{o['key']}")
    return out


def sort_rows(rows):
    return sorted(rows, key=lambda r: (r["kai"], r.get("_ord", 10 ** 9), r["key"]))


def kian(root, year, out=print):
    """起票。表を書き、(表ごとの行の数, note の付いた行, 残した既存の行) を返す。"""
    ynum, fy = year_info(year)
    b = build(root, year)
    out(f"資料 {len(b['docs'])} 本：{' '.join(b['docs'])}")
    pre = year + "-"
    report = dict(left=[], kept_values=[])
    sd = struct_dir(root)
    os.makedirs(sd, exist_ok=True)

    def split_old(name, kcol="kai"):
        rows = load_table(root, name)
        return [r for r in rows if not r[kcol].startswith(pre)], [r for r in rows if r[kcol].startswith(pre)]

    # 回
    other, old = split_old("hosei-kai", "id")
    newids = {k["id"] for k in b["kai"]}
    kai_rows = []
    for k in b["kai"]:
        r = {c: str(k.get(c, "")) for c in KAI_COLS}
        o = next((x for x in old if x["id"] == r["id"]), None)
        if o:
            r["note"] = o["note"]
        kai_rows.append(r)
    for o in old:
        if o["id"] not in newids:
            kai_rows.append(o)
            report["left"].append(f"hosei-kai {o['id']}")
    write_table(os.path.join(sd, "hosei-kai.tsv"), KAI_COLS, sorted(other + kai_rows, key=lambda r: r["id"]))
    # 資料
    rows = load_table(root, "hosei-shiryo")
    keep = [r for r in rows if not (r["kai"].startswith(pre) and r["doc"].startswith("yosan-"))]
    oldmap = {(r["kai"], r["doc"], r["part"]): r for r in rows if r["kai"].startswith(pre) and r["doc"].startswith("yosan-")}
    srows = []
    for s in b["shiryo"]:
        o = oldmap.get((s["kai"], s["doc"], s["part"]))
        if o:
            s["note"] = o["note"]
        srows.append(s)
    allrows = keep + srows
    allrows.sort(key=lambda r: (r["kai"], r["doc"], PART_ORDER.index(r["part"]) if r["part"] in PART_ORDER else 99))
    write_table(os.path.join(sd, "hosei-shiryo.tsv"), SHIRYO_COLS, allrows)
    # 款の行
    other, old = split_old("hosei-kan")
    kan_rows = sort_rows(merge_kan(old, b["kan"], report))
    write_table(os.path.join(sd, "hosei-kan.tsv"), KAN_COLS, other + kan_rows)
    # 主な内容の行
    other, old = split_old("hosei-maru")
    maru_rows = merge_keyed(old, b["maru"], lambda r: "m" if r["kind"] == "maru" else "t",
                            lambda r: (r["kai"], r["kind"], r["line"]), "hosei-maru", report)
    groups = {}
    for r in maru_rows:
        g = r.get("_group")
        if g and g[1] is not None and r.get("desc"):
            groups.setdefault(g, []).append(r)
    for rs in groups.values():
        if len(rs) > 1:
            for r in rs:
                if r.get("_old"):
                    continue
                others = "、".join(x["key"] for x in rs if x is not r)
                r["note"] = join_note(r["note"], f"説明の文は {others} と共有")
    write_table(os.path.join(sd, "hosei-maru.tsv"), MARU_COLS, other + sort_rows(maru_rows))
    # 事項
    other, old = split_old("hosei-jiko")
    jiko_rows = merge_keyed(old, b["jiko"], lambda r: "z", lambda r: (r["kai"], r["kind"], r["line"]), "hosei-jiko", report)
    write_table(os.path.join(sd, "hosei-jiko.tsv"), JIKO_COLS, other + sort_rows(jiko_rows))
    # 会計の額
    other, old = split_old("hosei-kaikei")
    kk_rows = merge_keyed(old, b["kaikei"], lambda r: "a",
                          lambda r: (r["kai"], r["table"], r["doc"], r["name"], r["flow"], r["part"]), "hosei-kaikei", report)
    write_table(os.path.join(sd, "hosei-kaikei.tsv"), KAIKEI_COLS, other + sort_rows(kk_rows))

    counts = [("hosei-kai", len(kai_rows)), ("hosei-shiryo", len(srows)), ("hosei-kan", len(kan_rows)),
              ("hosei-maru", len(maru_rows)), ("hosei-jiko", len(jiko_rows)), ("hosei-kaikei", len(kk_rows))]
    noted = []
    for name, rs, kc in (("hosei-kai", kai_rows, "id"), ("hosei-shiryo", srows, "kai"), ("hosei-kan", kan_rows, "kai"),
                         ("hosei-maru", maru_rows, "kai"), ("hosei-jiko", jiko_rows, "kai"), ("hosei-kaikei", kk_rows, "kai")):
        for r in rs:
            if r.get("note"):
                noted.append((name, r[kc], r.get("key", r.get("part", "")), r["note"]))
    out("表ごとの行の数（この年度）")
    for name, n in counts:
        detail = ""
        if name == "hosei-maru":
            detail = f"（maru {sum(1 for r in maru_rows if r['kind'] == 'maru')}・toku {sum(1 for r in maru_rows if r['kind'] == 'toku')}）"
        if name == "hosei-jiko":
            detail = "（" + "・".join(f"{k} {sum(1 for r in jiko_rows if r['kind'] == k)}" for _, k in JIKO_KIND) + "）"
        if name == "hosei-kaikei":
            detail = f"（toku {sum(1 for r in kk_rows if r['table'] == 'toku')}・kigyo {sum(1 for r in kk_rows if r['table'] == 'kigyo')}）"
        out(f"  {name}\t{n}{detail}")
    out(f"note を付けた行 {len(noted)}")
    for x in noted:
        out("  " + "\t".join(x))
    if report["kept_values"]:
        out(f"既存の行の値を保った款の行 {len(report['kept_values'])}：{' '.join(report['kept_values'])}")
    out(f"既存にあって今回の読みに無い行（消さずに残した） {len(report['left'])}")
    for x in report["left"]:
        out("  " + x)
    return dict(counts=dict(counts), noted=noted, left=report["left"], kept=report["kept_values"])


# ---- 段1 の照合と検算 ----

def row_key(table, row, cols, value, unit, source):
    """検収の台帳の key（shiga-hino-plans tools/audit_worksheet.py の row_key と同じ作り方）。"""
    raw = "\x1f".join([table, row, cols, value, unit, source])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def ival(v):
    """検算に使う整数。空欄は0。数でなければ ValueError。"""
    if v == "":
        return 0
    if not re.fullmatch(r"-?[0-9]+", v):
        raise ValueError(v)
    return int(v)


class Texts:
    """原典の文字を読む（同じファイルは1回だけ読む）。"""

    def __init__(self, root):
        self.root = root
        self.cache = {}

    def raw(self, doc):
        return self._get("text_raw", doc)

    def text(self, doc):
        return self._get("text", doc)

    def _get(self, d, doc):
        k = (d, doc)
        if k not in self.cache:
            p = os.path.join(self.root, "data", d, doc + ".txt")
            self.cache[k] = read_pages(p) if os.path.exists(p) else []
        return self.cache[k]

    def page(self, kind, doc, page):
        ps = self.raw(doc) if kind == "raw" else self.text(doc)
        try:
            p = int(page)
        except ValueError:
            return []
        return ps[p - 1] if 1 <= p <= len(ps) else []


def load_year(root, year):
    pre = year + "-"
    t = {}
    for name in TABLES:
        rows = load_table(root, name)
        kc = "id" if name == "hosei-kai" else "kai"
        t[name] = [r for r in rows if r[kc].startswith(pre)]
    return t


def load_issues(root, docs):
    out = []
    for d in sorted(docs):
        p = os.path.join(struct_dir(root), "issues", d + ".tsv")
        if os.path.exists(p):
            for r in htd.read_tsv(p, ISSUE_COLS):
                if r["status"] == "原典のまま持つ":
                    out.append((d, r["no"], r["where"]))
    return out


def chain_order(kais, kaikei="ippan"):
    """prev をたどった回の順。"""
    ks = [k for k in kais if k["kaikei"] == kaikei]
    nxt = {}
    for k in ks:
        if k["prev"]:
            nxt.setdefault(k["prev"], []).append(k)
    heads = [k for k in ks if not k["prev"]]
    out = []
    seen = set()
    stack = sorted(heads, key=lambda k: int(k["gou"]))
    while stack:
        k = stack.pop(0)
        if k["id"] in seen:
            continue
        seen.add(k["id"])
        out.append(k)
        stack = sorted(nxt.get(k["id"], []), key=lambda k: int(k["gou"])) + stack
    return out


def run_check(t, texts, issues=()):
    """13.10 段1。t は load_year の表。戻り値 dict(fails=[...], rownotes={(table,row): [..]}, groups={行: group})。
    fail は dict(kind, group, rows, detail, msg)。"""
    fails = []
    rownotes = {}

    def fail(kind, group, rows, detail, msg):
        fails.append(dict(kind=kind, group=group, rows=tuple(rows), detail=detail, msg=msg))

    def rn(table, row, note):
        rownotes.setdefault((table, row), [])
        if note not in rownotes[(table, row)]:
            rownotes[(table, row)].append(note)

    kais = {k["id"]: k for k in t["hosei-kai"]}
    kan_by = {}
    for r in t["hosei-kan"]:
        kan_by.setdefault(r["kai"], []).append(r)
    kai_doc = {}
    for s in t["hosei-shiryo"]:
        if s["part"] == "kan":
            kai_doc[s["kai"]] = s["doc"]

    def num(r, c, group, rowid):
        try:
            return ival(r[c])
        except ValueError:
            fail("数でない", group, [rowid], c, f"{rowid} の {c}「{r[c]}」が整数でない")
            return 0

    # 款の行
    for kai, rows in kan_by.items():
        g = ("hosei-kan", kai)
        rid = lambda r: f"{kai}/{r['key']}"  # noqa: E731
        byk = {r["key"]: r for r in rows}
        pages_used = {}
        for r in rows:
            pages_used.setdefault((r["doc"], r["page"]), []).append(r)
            # (1) 並び
            lines = texts.page("raw", r["doc"], r["page"])
            vals = [r[c] for c in AMT9 if r[c] != ""]
            want_head = "" if r["key"] in TOTAL_NAME else norm(r["kan_no"] + r["name"])
            ok = False
            for ln in lines:
                head, toks = split_nums(ln, words=True)
                if norm(head) != want_head or len(toks) != len(vals):
                    continue
                try:
                    if [read_num(x) for x in toks] == vals:
                        ok = True
                        break
                except ValueError:
                    continue
            if r["key"] in TOTAL_NAME:
                if not r["name"] or norm(r["name"]) != TOTAL_NAME[r["key"]] or \
                        not any(norm(x) == norm(r["name"]) for x in lines):
                    ok = False
            if not ok:
                fail("並び", g, [rid(r)], "", f"{rid(r)}：行の値の並びが {r['doc']} p.{r['page']} の原典の文字の行に無い")
            if ok:
                for c in AMT9:
                    if r[c] == "":
                        rn("hosei-kan", rid(r), f"{c}は原典で空欄")
            # (5) 行の和
            for a, b2, c2 in (("mae", "hosei", "hoseigo"), ("mae_ippan", "hosei_ippan", "hoseigo_ippan")):
                if num(r, a, g, rid(r)) + num(r, b2, g, rid(r)) != num(r, c2, g, rid(r)):
                    fail("行の和", g, [rid(r)], c2, f"{rid(r)}：{a}＋{b2}≠{c2}（{r[a]}＋{r[b2]}≠{r[c2]}）")
        # 行の欠け：ページの数の行の数と表の行の数
        for (doc, page), rs in pages_used.items():
            lines = texts.page("raw", doc, page)
            n = sum(1 for ln in lines if len(split_nums(ln, words=True)[1]) >= 4)
            if n != len(rs):
                fail("行の欠け", g, [rid(x) for x in rs], f"{doc}:{page}",
                     f"{kai}：{doc} p.{page} の数の行は {n}、表の行は {len(rs)}")
        cols8 = AMT9[:8]

        def sum_check(kind, parent, children, label):
            if parent not in byk:
                fail(kind, g, [f"{kai}/{parent}"], parent, f"{kai}：{parent} の行が無い")
                return
            for c in cols8:
                s = sum(num(byk[k], c, g, f"{kai}/{k}") for k in children)
                p = num(byk[parent], c, g, f"{kai}/{parent}")
                if s != p:
                    fail(kind, g, [f"{kai}/{parent}"] + [f"{kai}/{k}" for k in children], f"{parent}:{c}",
                         f"{kai}：{label}の {c} {s}≠{parent} の {p}")
        # (2) 款の和
        sum_check("款の和", "in-total", [k for k in byk if re.fullmatch(r"in-[0-9]{2}", k)], "歳入の款の和")
        sum_check("款の和", "out-total", [k for k in byk if re.fullmatch(r"out-[0-9]{2}", k)], "目的別の款の和")
        # (3) 内訳の和
        for k in byk:
            m = re.fullmatch(r"(in-[0-9]{2}|sei-[0-9]+)", k)
            if m:
                ch = [x for x in byk if re.fullmatch(re.escape(k) + r"-[0-9]+", x)]
                if ch:
                    sum_check("内訳の和", k, ch, f"{k} の内訳の和")
        # (4)
        sum_check("区分の和", "sei-total", [k for k in byk if re.fullmatch(r"sei-[0-9]+", k)], "性質別の区分の和")
        for a, b2 in (("out-total", "sei-total"), ("in-total", "out-total")):
            if a in byk and b2 in byk:
                for c in cols8:
                    if num(byk[a], c, g, f"{kai}/{a}") != num(byk[b2], c, g, f"{kai}/{b2}"):
                        fail("合計", g, [f"{kai}/{a}", f"{kai}/{b2}"], c, f"{kai}：{a} の {c}≠{b2} の {c}")
        # (8) 規模の表
        doc = kai_doc.get(kai)
        kibo = read_kibo(texts.raw(doc)) if doc else {}
        if "一般会計" in kibo and "out-total" in byk:
            ot = byk["out-total"]
            for c, v in zip(AMT4, kibo["一般会計"]):
                if num(ot, c, g, f"{kai}/out-total") != int(v):
                    fail("規模", g, [f"{kai}/out-total"], c, f"{kai}：規模の表の一般会計の {c} {v}≠歳出合計（目的別）の {ot[c]}")
        elif doc:
            rn("hosei-kan", f"{kai}/out-total", "規模の表が無いので規模の表との検算を飛ばした")
    # (6) 当初の額、(7) 回のつながり
    order = chain_order(list(kais.values()))
    first = order[0]["id"] if order else None
    for k in order:
        kai = k["id"]
        g = ("hosei-kan", kai)
        rows = {r["key"]: r for r in kan_by.get(kai, [])}
        if first and kai != first:
            base = {r["key"]: r for r in kan_by.get(first, [])}
            for key, r in rows.items():
                b0 = base.get(key)
                for c in ("tosho", "tosho_ippan"):
                    bv = b0[c] if b0 else ""
                    if r[c] != bv:
                        fail("当初", g, [f"{kai}/{key}"], c, f"{kai}/{key}：{c} {r[c]} が {first} の {bv or '（行が無い）'} と違う")
        if k["prev"]:
            prow = {r["key"]: r for r in kan_by.get(k["prev"], [])}
            for key, r in rows.items():
                p = prow.get(key)
                if p is None:
                    rn("hosei-kan", f"{kai}/{key}", f"prev の回 {k['prev']} に同じ key の行が無いので0とみなした")
                for a, b2 in (("hoseigo", "mae"), ("hoseigo_ippan", "mae_ippan")):
                    pv = num(p, a, g, f"{k['prev']}/{key}") if p else 0
                    if pv != num(r, b2, g, f"{kai}/{key}"):
                        fail("つながり", g, [f"{kai}/{key}", f"{k['prev']}/{key}"], b2,
                             f"{kai}/{key}：{b2} {r[b2]}≠前の回 {k['prev']} の {a} {p[a] if p else '0'}")
    # 会計の額
    kk_by = {}
    for r in t["hosei-kaikei"]:
        kk_by.setdefault((r["kai"], r["table"], r["doc"]), []).append(r)
    for (kai, table, doc), rows in kk_by.items():
        g = ("hosei-kaikei", kai, table, doc)
        rid = lambda r: f"{kai}/{r['key']}"  # noqa: E731
        lines = texts.page("raw", doc, rows[0]["page"])
        for r in rows:
            lines = texts.page("raw", r["doc"], r["page"])
            vals = [r[c] for c in AMT4]
            total = norm(r["name"]) == "合計" and not r["kaikei"]
            ok = False
            for ln in lines:
                head, toks = split_nums(ln, words=True, pct=True)
                try:
                    rd = [read_num(x) for x in toks]
                except ValueError:
                    continue
                if table == "toku":
                    want = "" if total else norm(r["name"])
                    if norm(head) == want and rd[:4] == vals:
                        ok = True
                        break
                else:
                    if not head and len(rd) == 8 and rd[{"shueki": 0, "shihon": 1}.get(r["part"], 0)::2] == vals:
                        ok = True
                        break
            if total:
                ok = ok and any(norm(x) == "合計" for x in lines)
            elif table == "kigyo":
                tl = texts.page("text", r["doc"], r["page"])
                ok = ok and any(norm(x) == norm(r["name"]) for x in tl) and kaikei_abbr(r["name"]) == r["kaikei"]
            else:
                ok = ok and kaikei_abbr(r["name"]) == r["kaikei"]
            if not ok:
                fail("会計の並び", g, [rid(r)], "", f"{rid(r)}：行の値の並びが {r['doc']} p.{r['page']} の原典の文字の行に無い")
            if num(r, "mae", g, rid(r)) + num(r, "hosei", g, rid(r)) != num(r, "hoseigo", g, rid(r)):
                fail("会計の行の和", g, [rid(r)], "hoseigo", f"{rid(r)}：mae＋hosei≠hoseigo")
        # 会計の和＝合計
        if table == "toku":
            groups_ = [("", "")]
        else:
            groups_ = [(f, p) for f in ("shunyu", "shishutsu") for p in ("shueki", "shihon")]
        for fl, pt in groups_:
            rs = [r for r in rows if r["flow"] == fl and r["part"] == pt]
            tot = [r for r in rs if norm(r["name"]) == "合計" and not r["kaikei"]]
            acc = [r for r in rs if r["kaikei"]]
            if len(tot) != 1:
                fail("会計の和", g, [rid(r) for r in rs], f"{fl}:{pt}", f"{kai} {doc} {table} {fl} {pt}：合計の行が1つでない")
                continue
            for c in AMT4:
                s = sum(num(r, c, g, rid(r)) for r in acc)
                if s != num(tot[0], c, g, rid(tot[0])):
                    fail("会計の和", g, [rid(tot[0])] + [rid(r) for r in acc], f"{fl}:{pt}:{c}",
                         f"{kai} {doc} {table}：会計の和の {c} {s}≠合計 {tot[0][c]}")
        # 規模の表
        kibo = read_kibo(texts.raw(doc))
        if table == "toku" and "特別会計" in kibo:
            tot = [r for r in rows if not r["kaikei"]]
            if tot:
                for c, v in zip(AMT4, kibo["特別会計"]):
                    if num(tot[0], c, g, rid(tot[0])) != int(v):
                        fail("会計の規模", g, [rid(tot[0])], c, f"{kai} {doc}：規模の表の特別会計の {c} {v}≠特別会計の表の合計")
        if table == "kigyo" and "企業会計" in kibo:
            tot = [r for r in rows if not r["kaikei"] and r["flow"] == "shishutsu"]
            if len(tot) == 2:
                for c, v in zip(AMT4, kibo["企業会計"]):
                    if sum(num(x, c, g, rid(x)) for x in tot) != int(v):
                        fail("会計の規模", g, [rid(x) for x in tot], c, f"{kai} {doc}：規模の表の企業会計の {c} {v}≠支出の合計")
        if table in ("toku", "kigyo") and not kibo:
            rn("hosei-kaikei", rid(rows[0]), "規模の表が無いので規模の表との検算を飛ばした")
    # 会計ごとのつながり
    order_docs = [kai_doc.get(k["id"]) for k in order]
    series = {}
    for (kai, table, doc), rows in kk_by.items():
        for r in rows:
            series.setdefault((table, r["kaikei"] or "total", r["flow"], r["part"]), {})[doc] = r
    for sk, bydoc in series.items():
        prev = None
        for d in order_docs:
            if d in bydoc:
                r = bydoc[d]
                if prev is not None:
                    g = ("hosei-kaikei", r["kai"], r["table"], d)
                    if num(prev, "hoseigo", g, "") != num(r, "mae", g, ""):
                        fail("会計のつながり", g, [f"{r['kai']}/{r['key']}", f"{prev['kai']}/{prev['key']}"], "mae",
                             f"{r['kai']}/{r['key']}（{d}）：mae {r['mae']}≠前の資料 {prev['doc']} の hoseigo {prev['hoseigo']}")
                prev = r
    # 主な内容の行・事項
    for table, rows in (("hosei-maru", t["hosei-maru"]), ("hosei-jiko", t["hosei-jiko"])):
        for r in rows:
            rid = f"{r['kai']}/{r['key']}"
            g = (table, rid)
            raw_p = norm("".join(texts.page("raw", r["doc"], r["page"])))
            txt_p = norm("".join(texts.page("text", r["doc"], r["page"])))
            nl = norm(r["line"])
            if not nl or not (raw_p.count(nl) == 1 or txt_p.count(nl) == 1):
                fail("line", g, [rid], "", f"{rid}：line の字が {r['doc']} p.{r['page']} の原典の文字に1回だけ現れるのでない")
            m = re.search(AMT_TAIL + "$", r["line"])
            amt_tok = m.group(1) if m else None
            if amt_tok is None:
                fail("金額の読み", g, [rid], "", f"{rid}：line に金額の字が無い")
            else:
                if amt_tok.strip() == "－":
                    good = r["amount"] == ""
                else:
                    try:
                        good = read_num(amt_tok, integer=True) == r["amount"]
                    except ValueError:
                        good = False
                if not good:
                    fail("金額の読み", g, [rid], "", f"{rid}：amount {r['amount']} が line の金額の字「{amt_tok}」と合わない")
                mark = r.get("mark", "・" if table == "hosei-jiko" else "")
                if norm(mark + r["name"] + amt_tok + "千円") != nl:
                    fail("name", g, [rid], "", f"{rid}：印・name・金額をつないだ字が line と合わない")
            if table == "hosei-jiko":
                check_jiko_heads(r, texts, fail, g, rid)
            if table == "hosei-maru" and r["kind"] == "toku":
                k = kais.get(r["kai"])
                ab = k["kaikei"] if k else ""
                tname = "kigyo" if ab in KIGYO else "toku"
                cand = [x for x in t["hosei-kaikei"] if x["doc"] == r["doc"] and x["table"] == tname and x["kaikei"] == ab]
                if tname == "kigyo":
                    cand = [x for x in cand if x["flow"] == "shishutsu"]
                if not cand:
                    fail("主な内容の金額", g, [rid], "", f"{rid}：同じ資料の会計の額の表に {ab} の行が無い")
                elif r["amount"] == "":
                    rn(table, rid, "金額が空なので会計の額の表との検算を飛ばした")
                else:
                    s = sum(num(x, "hosei", g, rid) for x in cand)
                    if not re.fullmatch(r"-?[0-9]+", r["amount"]) or s != int(r["amount"]):
                        gk = ("hosei-kaikei", cand[0]["kai"], tname, r["doc"])
                        fail("主な内容の金額", g, [rid], "", f"{rid}：amount {r['amount']}≠会計の額の表の今回補正額 {s}")
                        fail("主な内容の金額", gk, [rid], "", f"{rid}：amount {r['amount']}≠会計の額の表の今回補正額 {s}")
    # issues に記録のある箇所
    for f in fails:
        f["excused"] = []
        for doc, no, where in issues:
            for rw in f["rows"]:
                if rw and re.search(re.escape(rw) + r"(?![0-9a-z-])", where):
                    f["excused"].append((doc, no))
                    break
    return dict(fails=fails, rownotes=rownotes)


def check_jiko_heads(r, texts, fail, g, rid):
    """事項の行の kind・kubun・kaikei を、原典の文字で、その行より上の見出しと比べる。"""
    pages = texts.raw(r["doc"])
    try:
        pg = int(r["page"])
    except ValueError:
        fail("見出し", g, [rid], "", f"{rid}：page が数でない")
        return
    seq = []
    for pi in range(1, min(pg, len(pages)) + 1):
        for ln in pages[pi - 1]:
            seq.append((pi, ln))
    pos = None
    for i, (pi, ln) in enumerate(seq):
        if pi == pg and ln and norm(r["line"]).startswith(norm(ln)):
            pos = i
            break
    if pos is None:
        fail("見出し", g, [rid], "", f"{rid}：原典の文字で行の位置が分からない")
        return
    kind = kubun = kaikei = None
    for pi, ln in reversed(seq[:pos]):
        if kubun is None and KUBUN_RE.match(ln):
            kubun = ln
        if kaikei is None and KA_HEAD.match(ln):
            kaikei = ln
        if SUB_HEAD.match(ln):
            kind = next((k for w, k in JIKO_KIND if w in norm(ln)), "")
            break
    if kind != r["kind"]:
        fail("見出し", g, [rid], "kind", f"{rid}：kind {r['kind']} が上の見出しの {kind} と違う")
    kb = kubun or ""
    if kb != r["kubun_raw"] or (KUBUN[KUBUN_RE.match(kb).group(1)] if kb else "") != r["kubun"]:
        fail("見出し", g, [rid], "kubun", f"{rid}：kubun_raw・kubun が上の区分の見出し「{kb}」と合わない")
    ka = kaikei or ""
    if ka != r["kaikei_raw"] or ((kaikei_abbr(ka[1:-1]) or "") if ka else "") != r["kaikei"]:
        fail("見出し", g, [rid], "kaikei", f"{rid}：kaikei_raw・kaikei が上の会計の見出し「{ka}」と合わない")


# ---- 段4 の抜き取り（13.10 の H2・H4） ----

H4_TABLES = ("hosei-maru", "hosei-jiko")
H2_NOTE = "要人確認:H2 出典の資料やページが無い"


def amount_count(tok, page_lines):
    """金額の字（カンマ付きの原典の形。符号と空白は除く）が、ページの原典の文字に現れる回数。"""
    body = re.sub(r"^[△▲\-]\s*", "", tok.strip())
    if not body or body == "－":
        return 0
    pat = r"(?<![0-9０-９,，.．])" + re.escape(body) + r"(?![0-9０-９,，.．])"
    return len(re.findall(pat, "\n".join(page_lines)))


def is_joined(line, page_lines):
    """line がページの2行をつないだものか（名前が長く金額が次の行に送られた事項）。"""
    ls = [x for x in page_lines if x]
    if line in ls:
        return False
    return any(a + " " + b == line for a, b in zip(ls, ls[1:]))


SHARE_NOTE = re.compile(r"説明の文は [a-z][0-9]{3}(?:、[a-z][0-9]{3})* と共有")


def difficulty(table, r, texts, dan2=frozenset()):
    """13.10 の難しさの点数。戻り値 (点数, 理由の並び)。"""
    rid = f"{r['kai']}/{r['key']}"
    pts, why = 0, []
    if rid in dan2:
        pts += 3
        why.append("段2 に回った")
    if table == "hosei-maru":
        if r["desc"] == "":
            pts += 2
            why.append("desc が空")
        elif SHARE_NOTE.search(r["note"]):   # 共有は 13.6 の note で見る（同じ字の別の説明は共有でない）
            pts += 2
            why.append("説明の文を共有")
    page = texts.page("raw", r["doc"], r["page"])
    m = re.search(AMT_TAIL + "$", r["line"])
    if m and amount_count(m.group(1), page) >= 3:
        pts += 2
        why.append("同じ金額の字がページに3回以上")
    if is_joined(r["line"], page):
        pts += 2
        why.append("line が2行をつないだもの")
    a = r["amount"]
    if a == "":
        pts += 1
        why.append("金額が空")
    elif re.fullmatch(r"-?[0-9]+", a) and int(a) == 0:
        pts += 1
        why.append("金額が0")
    elif re.fullmatch(r"-?[0-9]", a):
        pts += 1
        why.append("金額が1桁")
    return pts, why


def pick_marks(t, texts, result, dan2=None, h1=frozenset()):
    """要人確認の印。戻り値 {(table, row): note の字}。
    dan2 は段2 に回った行（None なら段1 で line が原典の文字に見つからなかった行）。h1 は段2 の結果で選んだ行と理由
    （段2 の取り込みを足したときに使う。{(table, row): 理由}）。"""
    if dan2 is None:
        dan2 = {rw for f in result["fails"] if f["kind"] == "line" for rw in f["rows"]}
    marks = {}

    def add(k, note):
        marks[k] = join_note(marks.get(k, ""), note)

    for (table, rw), why in sorted(dict(h1).items()):
        add((table, rw), f"要人確認:H1 {why}")
    for table in ("hosei-kan", "hosei-maru", "hosei-jiko", "hosei-kaikei"):
        for r in t[table]:
            if not r["doc"] or not r["page"]:
                add((table, f"{r['kai']}/{r['key']}"), H2_NOTE)
    for table in H4_TABLES:
        rows = t[table]
        groups = {}
        for r in rows:
            groups.setdefault(r["kai"], []).append(r)
        for kai, rs in sorted(groups.items()):
            best = None
            for r in rs:
                rw = f"{r['kai']}/{r['key']}"
                pts, why = difficulty(table, r, texts, dan2)
                if best is None or pts > best[0] or (pts == best[0] and rw < best[1]):
                    best = (pts, rw, why)
            pts, rw, why = best
            add((table, rw), f"要人確認:H4 {pts}点" + (" " + "・".join(why) if why else ""))
    return marks


def audit_rows(t, result, old_ledger, today=TODAY, marks=None):
    """検収の台帳の行。marks は pick_marks の結果（要人確認の印。note の頭に付ける）。"""
    marks = marks or {}
    fails = result["fails"]
    bad_groups = {f["group"] for f in fails if not f["excused"]}
    rowfail = {}
    rowexc = {}
    for f in fails:
        for rw in f["rows"]:
            if f["excused"]:
                for doc, no in f["excused"]:
                    rowexc.setdefault(rw, set()).add(f"段1 不一致（原典）：issues/{doc}.tsv no.{no}")
            else:
                rowfail.setdefault(rw, set()).add(f["kind"])
    old = {r["key"]: r for r in old_ledger}
    out = []
    spec = [("hosei-kan", t["hosei-kan"], AMT9, lambda r: ("hosei-kan", r["kai"])),
            ("hosei-maru", t["hosei-maru"], ["name", "amount"], lambda r: ("hosei-maru", f"{r['kai']}/{r['key']}")),
            ("hosei-jiko", t["hosei-jiko"], ["kind", "kubun", "kaikei", "name", "amount"],
             lambda r: ("hosei-jiko", f"{r['kai']}/{r['key']}")),
            ("hosei-kaikei", t["hosei-kaikei"], AMT4, lambda r: ("hosei-kaikei", r["kai"], r["table"], r["doc"]))]
    stats = {}
    for table, rows, cols, gof in spec:
        st = stats.setdefault(table, [0, 0])
        for r in rows:
            rw = f"{r['kai']}/{r['key']}"
            value = "|".join(r[c] for c in cols)
            source = f"{r['doc']} p.{r['page']}"
            key = row_key(table, rw, ",".join(cols), value, r["unit"], source)
            ok = gof(r) not in bad_groups
            notes = list(result["rownotes"].get((table, rw), []))
            notes += sorted(rowexc.get(rw, []))
            if rw in rowfail:
                notes.append("段1 不一致：" + "・".join(sorted(rowfail[rw])))
            elif not ok:
                notes.append("段1 不一致：同じ回か表のほかの行")
            new = dict(key=key, table=table, row=rw, cols=",".join(cols), value=value, unit=r["unit"], source=source,
                       checked="✓" if ok else "", reviewer="auto:pdftext", date=today,
                       note=join_note(marks.get((table, rw), ""), *notes))
            o = old.get(key)
            if o:
                human = o["reviewer"] and not o["reviewer"].startswith("auto:")
                # 道具の印（H2・H4）は選び直して付け直す。それ以外の要人確認（段2 の H1 など）は前のとおり保つ
                rest = [x for x in o["note"].split("。") if x and not x.startswith(("要人確認:H2", "要人確認:H4"))]
                other_mark = any(x.startswith("要人確認:") for x in rest)
                if human:   # 人の印の付いた行は変えない（13.10 の人の列を保つ）
                    for c in ("checked", "reviewer", "date", "note"):
                        new[c] = o[c]
                elif other_mark or o["reviewer"] == "auto:ai":
                    # 段2 の結果（auto:ai）と、段2 の H1 などの要人確認は、段1 を動かし直しても保つ。H2・H4 の印だけ付け直す
                    for c in ("checked", "reviewer", "date"):
                        new[c] = o[c]
                    new["note"] = join_note(marks.get((table, rw), ""), *rest)
                elif o["checked"] == new["checked"] and o["reviewer"] == new["reviewer"]:
                    new["date"] = o["date"]
            out.append(new)
            st[0 if new["checked"] == "✓" else 1] += 1
    return out, stats


def hosei_check(root, year, out=print, today=TODAY):
    year_info(year)
    t = load_year(root, year)
    if not t["hosei-kai"]:
        raise Stop(f"data/struct/hosei-kai.tsv に {year} の回が無い（先に hosei-kian {year}）")
    docs = {r["doc"] for name in ("hosei-kan", "hosei-maru", "hosei-jiko", "hosei-kaikei") for r in t[name]}
    texts = Texts(root)
    res = run_check(t, texts, load_issues(root, docs))
    path = os.path.join(struct_dir(root), "audit", f"hosei-{year}.tsv")
    old = htd.read_tsv(path, AUDIT_COLS) if os.path.exists(path) else []
    marks = pick_marks(t, texts, res)
    rows, stats = audit_rows(t, res, old, today, marks)
    write_table(path, AUDIT_COLS, rows)
    bad = [f for f in res["fails"] if not f["excused"]]
    exc = [f for f in res["fails"] if f["excused"]]
    out("表ごとの行の数・通った行・通らなかった行")
    for table, (a, b2) in stats.items():
        out(f"  {table}\t{a + b2}\t通った {a}\t通らなかった {b2}")
    if exc:
        out(f"不一致（原典）として通した項目 {len(exc)}")
        for f in exc:
            out(f"  [{f['kind']}] {f['msg']}  ← " + "・".join(f"issues/{d}.tsv no.{n}" for d, n in f["excused"]))
    out(f"合わなかった項目 {len(bad)}")
    for f in bad:
        out(f"  [{f['kind']}] {f['msg']}")
    for h, name in (("H2", "H2（出典の資料やページの無い行）"), ("H4", "H4（回と表の組ごとの抜き取り）")):
        hs = [r for r in rows if f"要人確認:{h}" in r["note"]]
        out(f"{name} {len(hs)}")
        for r in hs:
            m = re.search(r"要人確認:H4 ([0-9]+)点 ?([^。]*)", r["note"]) if h == "H4" else None
            out("  " + "\t".join([r["table"], r["row"]] + ([m.group(1) + "点", m.group(2)] if m else [])))
    code = 1 if bad else 0
    out(f"台帳：{os.path.relpath(path, root)}（{len(rows)} 行）。終了コード {code}")
    return code, res, rows, stats


# ---- 段1 の道具の確かめ ----

MUTATIONS = [
    ("款の行の額を1つ変える", {"並び"}),
    ("款の行を1つ消す", {"款の和", "内訳の和", "区分の和", "行の欠け"}),
    ("mae＋hosei≠hoseigo になるよう hoseigo を変える", {"行の和"}),
    ("prev の回の hoseigo を変える", {"つながり"}),
    ("主な内容の行の amount を変える", {"金額の読み"}),
    ("事項の name の字を1字変える", {"name"}),
    ("会計の額の合計を変える", {"会計の和"}),
    ("歳出合計の hosei を変える（規模の表との不一致）", {"規模"}),
]
PER_KIND = 5


def bump(v):
    return str(int(v) + 1) if re.fullmatch(r"-?[0-9]+", v or "") else "1"


def mutants(t, base_bad_kais, base_rows=frozenset(), kibo_kais=frozenset()):
    """(種類の番号, 説明, 誤りを入れた表) を返す。"""
    kais = {k["id"]: k for k in t["hosei-kai"]}
    ok_kan = [r for r in t["hosei-kan"] if r["kai"] not in base_bad_kais and f"{r['kai']}/{r['key']}" not in base_rows]

    def spread(rows, n=PER_KIND):
        if len(rows) <= n:
            return rows
        step = len(rows) / n
        return [rows[int(i * step)] for i in range(n)]

    def with_change(table, target, fn):
        t2 = copy.deepcopy(t)
        idx = t[table].index(target)
        fn(t2[table], idx)
        return t2

    for r in spread([r for r in ok_kan if r["kan_no"]]):
        yield 0, f"{r['kai']}/{r['key']} tosho", with_change("hosei-kan", r, lambda rs, i: rs[i].update(tosho=bump(rs[i]["tosho"])))
    for r in spread([r for r in ok_kan if r["kan_no"]]):
        yield 1, f"{r['kai']}/{r['key']} を消す", with_change("hosei-kan", r, lambda rs, i: rs.pop(i))
    for r in spread(ok_kan):
        yield 2, f"{r['kai']}/{r['key']} hoseigo", with_change("hosei-kan", r, lambda rs, i: rs[i].update(hoseigo=bump(rs[i]["hoseigo"])))
    nexts = {k["prev"] for k in kais.values() if k["prev"] and k["kaikei"] == "ippan" and k["id"] not in base_bad_kais}
    for r in spread([r for r in ok_kan if r["kai"] in nexts]):
        yield 3, f"{r['kai']}/{r['key']} hoseigo（次の回のつながり）", with_change(
            "hosei-kan", r, lambda rs, i: rs[i].update(hoseigo=bump(rs[i]["hoseigo"])))
    for r in spread([r for r in t["hosei-maru"] if r["amount"]]):
        yield 4, f"{r['kai']}/{r['key']} amount", with_change("hosei-maru", r, lambda rs, i: rs[i].update(amount=bump(rs[i]["amount"])))
    for r in spread([r for r in t["hosei-jiko"] if r["name"]]):
        def one(rs, i):
            n = rs[i]["name"]
            rs[i]["name"] = ("■" if n[0] != "■" else "□") + n[1:]
        yield 5, f"{r['kai']}/{r['key']} name", with_change("hosei-jiko", r, one)
    for r in spread([r for r in t["hosei-kaikei"] if not r["kaikei"]]):
        yield 6, f"{r['kai']}/{r['key']} 合計の hosei", with_change("hosei-kaikei", r, lambda rs, i: rs[i].update(hosei=bump(rs[i]["hosei"])))
    for r in spread([r for r in ok_kan if r["key"] == "out-total" and r["kai"] in kibo_kais]):
        yield 7, f"{r['kai']}/out-total hosei", with_change("hosei-kan", r, lambda rs, i: rs[i].update(hosei=bump(rs[i]["hosei"])))


def fid(f):
    return (f["kind"], f["group"], f["rows"], f["detail"])


def selftest(root=ROOT, out=print):
    tmp = tempfile.mkdtemp(prefix="kozo-selftest-")
    try:
        real = bool(load_table(root, "hosei-kai"))
        if real:
            shutil.copytree(struct_dir(root), struct_dir(tmp))
            data_root = root
            out("data/struct/ の表を一時の場所に写して確かめる")
        else:
            import test_kozo
            test_kozo.make_sample(tmp)
            kian(tmp, test_kozo.SAMPLE_YEAR, out=lambda *a: None)
            data_root = tmp
            out("data/struct/ に表が無いので、tools/test_kozo.py の見本で確かめる")
        years = sorted({k["id"].split("-")[0] for k in load_table(tmp, "hosei-kai")})
        found = [0] * len(MUTATIONS)
        total = [0] * len(MUTATIONS)
        missed = []
        texts = Texts(data_root)
        for y in years:
            t = load_year(tmp, y)
            docs = {r["doc"] for name in ("hosei-kan", "hosei-maru", "hosei-jiko", "hosei-kaikei") for r in t[name]}
            iss = load_issues(tmp, docs)
            base = run_check(t, texts, iss)
            base_ids = {fid(f) for f in base["fails"]}
            bad_kais = {f["group"][1] for f in base["fails"] if f["group"][0] == "hosei-kan" and not f["excused"]}
            base_rows = {rw for f in base["fails"] for rw in f["rows"]}
            kibo_kais = {s["kai"] for s in t["hosei-shiryo"] if s["part"] == "kan" and read_kibo(texts.raw(s["doc"]))}
            for n, desc, t2 in mutants(t, bad_kais, base_rows, kibo_kais):
                total[n] += 1
                res = run_check(t2, texts, iss)
                new = [f for f in res["fails"] if fid(f) not in base_ids and not f["excused"]]
                if any(f["kind"] in MUTATIONS[n][1] for f in new):
                    found[n] += 1
                else:
                    missed.append(f"{y} {MUTATIONS[n][0]}：{desc}")
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
    if len(argv) == 2 and argv[1] in ("zaisei-kian", "zaisei-check", "zaisei-selftest"):
        import kozo_zaisei
        return kozo_zaisei.main(argv)
    if len(argv) >= 2 and argv[1] == "selftest" and len(argv) == 2:
        return selftest()
    if len(argv) == 3 and argv[1] in ("hosei-kian", "hosei-check"):
        if argv[1] == "hosei-kian":
            kian(ROOT, argv[2])
            return 0
        return hosei_check(ROOT, argv[2])[0]
    if len(argv) == 2 and argv[1] in ("kessan-kian", "kessan-check", "kessan-selftest"):
        import kozo_kessan
        return kozo_kessan.main(argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Stop as e:
        print(f"止めた：{e}", file=sys.stderr)
        sys.exit(2)
