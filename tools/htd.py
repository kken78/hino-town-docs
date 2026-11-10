"""hino-town-docs の取得と検収の道具（DOCS_SCHEMA.md §2・§4〜§6）。

  python3 -B tools/htd.py fetch <依頼.tsv>   依頼の資料を町のサイトから取り、文字を取り出して、台帳とテキストを書く
  python3 -B tools/htd.py extract [<id> ...]  取得済みの原典（sources_raw/）から、文字の取り出しと検収をやり直す（ネットワークを使わない）
  python3 -B tools/htd.py ocr [<id> ...]      原典の全ページを NDLOCR-Lite で読み、OCR の下書き data/ocr/<id>.txt を作る（§10。ネットワークを使わない）
  python3 -B tools/htd.py raw [<id> ...]      取得済みの原典から、書かれた順の取り出し data/text_raw/<id>.txt を作り直す（§5.1。台帳は変えない。ネットワークを使わない）

依頼の TSV（UTF-8、タブ区切り、1行目は見出し）の列：id・category・title・fiscal_year・date・page_url・url・size_label
（size_label は掲載ページに書かれた大きさ。例「1.23MB」。無ければ空）。最後に archive_url の列を足してもよい。
archive_url は、町のサイトから外された資料を Webアーカイブの保存版から取るときだけ書く（§6。
https://web.archive.org/web/<14桁の保存日時>id_/<url> の形）。台帳の url は町の元の URL のままとし、note に取得元を書く。依頼の id が台帳にすでにあれば、何もせずに止める。
取得は1秒に1件以内（Webアーカイブの保存版から取るときも同じ）。町のサイトか Webアーカイブから断られたとき（HTTP のエラー、
つながらない）は、そこで止めて報告する。
止めるまでに取得したものは、台帳とテキストに書く。
原典は sources_raw/<id>.<拡張子> に置く（リポジトリに入れない）。テキストは data/text/<id>.txt（改ページ \\f を残す）。
fetch と extract は、テキストを置く資料について、書かれた順の取り出し（pdftotext -raw）も data/text_raw/<id>.txt に置く（§5.1）。
xlsx はシートごとの TSV を data/text/<id>/ に（§5.2）、町のサイトのページ（.html）は本文の文字を data/text/<id>.txt に置く（§5.3）。
xlsx を読むには openpyxl が要る。

ocr は、id を指定しなければ、OCR の下書きがまだ無いか、ocr_method がいまの NDLOCR-Lite の版と違う PDF の資料だけを読む
（途中で止めても、もう一度動かせば続きから進む。台帳は資料1本ごとに書く）。
NDLOCR-Lite の場所は環境変数 HTD_NDLOCR（既定 ~/ocr/ndlocr-lite）、その Python は HTD_NDLOCR_PYTHON（既定 ~/ocr/venv-ndlocr/bin/python）。
"""
import csv
import datetime as dt
import hashlib
import html.parser
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "data", "sources.tsv")
TEXT_DIR = os.path.join(ROOT, "data", "text")
TEXT_RAW_DIR = os.path.join(ROOT, "data", "text_raw")   # §5.1：pdftotext -raw
OCR_DIR = os.path.join(ROOT, "data", "ocr")
OCR_RAW_DIR = os.path.join(ROOT, "ocr_raw")
NDLOCR_DIR = os.path.expanduser(os.environ.get("HTD_NDLOCR", "~/ocr/ndlocr-lite"))
NDLOCR_PYTHON = os.path.expanduser(os.environ.get("HTD_NDLOCR_PYTHON", "~/ocr/venv-ndlocr/bin/python"))
OCR_DPI = 300   # §10
RAW_DIR = os.path.join(ROOT, "sources_raw")
COLUMNS = ["id", "category", "title", "fiscal_year", "date", "page_url", "url", "format", "bytes", "sha256", "pages",
           "text_chars", "text_method", "ocr_method", "ocr_date", "fetched", "check",
           "removed", "removed_judgment", "archive_url", "note"]
REMOVED_COLUMNS = ("removed", "removed_judgment", "archive_url")   # §4：外れた資料の扱い（2026-11-10 に足した）
COLUMNS_V2 = [c for c in COLUMNS if c not in REMOVED_COLUMNS]   # 2026-11-10 より前の台帳。読むときに3列を空で足す
COLUMNS_V1 = [c for c in COLUMNS_V2 if c not in ("ocr_method", "ocr_date")]   # 0c64af5 より前の台帳。読むときに2列を空で足す
REMOVED_JUDGMENTS = ("", "replaced", "withheld")
REQUEST_COLUMNS = ["id", "category", "title", "fiscal_year", "date", "page_url", "url", "size_label"]
REQUEST_COLUMNS_ARCHIVE = REQUEST_COLUMNS + ["archive_url"]   # §6：Webアーカイブの保存版から取るとき
ARCHIVE_RE = re.compile(r"https://web\.archive\.org/web/([0-9]{14})id_/(.+)")
CATEGORIES = ("yosan", "kessan", "zaisei", "kofukin", "kansa", "kyoi", "koho", "jinji", "gyokaku", "shingi", "jigyo", "bosai",
              "kaiken", "hyoka", "chiiki", "tokei")
ID_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
PAGE_MIN = 50          # §6：文字のあるページの下限（空白と改ページを除いた文字数）
OK_RATE, IMAGE_RATE = 0.8, 0.2
SIZE_TOLERANCE = 0.1   # §6：掲載ページの大きさと1割以上違えば note に書く
WAIT = 1.0             # §6：1秒に1件以内
USER_AGENT = "hino-town-docs (+https://github.com/kken78/hino-town-docs)"
# §6：日本語の文書で使う字の範囲。この外の字（部首の字を除く）を「想定外の字」として数える
ALLOW = [
    (0x09, 0x0A), (0x0C, 0x0D), (0x20, 0x7E),
    (0xA7, 0xA7), (0xB0, 0xB1), (0xB4, 0xB4), (0xB6, 0xB6), (0xD7, 0xD7), (0xF7, 0xF7),
    (0x2010, 0x2027), (0x2030, 0x2030), (0x2032, 0x2033), (0x203B, 0x203B), (0x2070, 0x209F),
    (0x2100, 0x218F), (0x2190, 0x23FF), (0x2460, 0x27BF),
    (0x3000, 0x30FF), (0x31F0, 0x31FF), (0x3200, 0x33FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF), (0xFE10, 0xFE19), (0xFE30, 0xFE4F), (0xFF00, 0xFFEF), (0x20000, 0x2FFFF),
]
RADICAL = (0x2E80, 0x2FDF)   # §5：部首の字（CJK 部首補助・康熙部首）。想定外の字には数えない
AUTO_NOTE = re.compile(r"(^|。)((文字の少ないページ|想定外の字のあるページ|行の終わりの「-」を戻した)：[^。]*|ページの更新日 [0-9-]+)")   # 検収のたびに道具が書き直す注記
HTML_BODY = ("mol_contents", "main_naka")   # §5.3：本文の枠（id か class）。前のものを先に探す
HTML_BLOCK = {"p", "div", "li", "ul", "ol", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section",
              "article", "blockquote", "pre", "caption", "figcaption"}
HTML_VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "col", "area", "base", "source"}


class Stop(Exception):
    pass


# ---- 台帳 ----

def read_tsv(path, columns):
    with open(path, encoding="utf-8", newline="") as f:
        data = f.read()
    if data.startswith("﻿") or "\r" in data:
        raise Stop(f"{path}：BOM か CR がある（UTF-8・LF にする）")
    rows = list(csv.DictReader(data.splitlines(), delimiter="\t"))
    head = data.splitlines()[0].split("\t") if data else []
    if head != columns:
        raise Stop(f"{path}：見出しが {columns} でない（{head}）")
    return rows


def read_sources():
    if not os.path.exists(SOURCES):
        return []
    with open(SOURCES, encoding="utf-8", newline="") as f:
        head = f.readline().rstrip("\n").split("\t")
    if head == COLUMNS_V1:
        rows = read_tsv(SOURCES, COLUMNS_V1)
        for r in rows:
            r["ocr_method"] = r["ocr_date"] = ""
    elif head == COLUMNS_V2:
        rows = read_tsv(SOURCES, COLUMNS_V2)
    else:
        rows = read_tsv(SOURCES, COLUMNS)
    for r in rows:
        for c in REMOVED_COLUMNS:
            r.setdefault(c, "")
        check_removed(r)
    return rows


def check_removed(r):
    """§4：removed・removed_judgment・archive_url の形。合わなければ止める。"""
    if r["removed"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["removed"]):
        raise Stop(f"{r['id']} の removed が YYYY-MM-DD でない（{r['removed']}）")
    if r["removed_judgment"] not in REMOVED_JUDGMENTS:
        raise Stop(f"{r['id']} の removed_judgment が replaced・withheld・空のどれでもない（{r['removed_judgment']}）")
    if r["removed_judgment"] and not r["removed"]:
        raise Stop(f"{r['id']} は removed が空なのに removed_judgment がある")
    if r["archive_url"]:
        m = ARCHIVE_RE.fullmatch(r["archive_url"])
        if not m or m.group(2) != r["url"]:
            raise Stop(f"{r['id']} の archive_url が url の保存版の形でない（{r['archive_url']}）")


def write_atomic(path, write):
    """path を一度に置き換える。write(f) で同じディレクトリの一時ファイルに書き、書き終えてから os.replace で置き換える
    （書いている途中で止まっても、前のファイルが残る）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_sources(rows):
    for r in rows:
        for c in COLUMNS:
            if "\t" in str(r.get(c, "")) or "\n" in str(r.get(c, "")):
                raise Stop(f"{r['id']} の {c} にタブか改行がある")

    def write(f):
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(COLUMNS)
        for r in sorted(rows, key=lambda r: r["id"]):
            w.writerow([r.get(c, "") for c in COLUMNS])
    write_atomic(SOURCES, write)


def check_request(reqs, existing_ids):
    """依頼の行を確かめる。誤りは全件を示して止める。"""
    errs, seen = [], set()
    for n, r in enumerate(reqs, 2):
        i = r["id"]
        if not ID_RE.fullmatch(i):
            errs.append(f"{n}行目：id の形が違う（{i!r}）")
        if i in seen:
            errs.append(f"{n}行目：id が依頼の中で重なっている（{i}）")
        if i in existing_ids:
            errs.append(f"{n}行目：id が台帳にすでにある（{i}）")
        seen.add(i)
        if r["category"] not in CATEGORIES:
            errs.append(f"{n}行目：区分が §3 に無い（{r['category']!r}）")
        if not i.startswith(r["category"] + "-"):
            errs.append(f"{n}行目：id が区分で始まらない（{i}）")
        if r["fiscal_year"] and not re.fullmatch(r"(19|20)[0-9]{2}", r["fiscal_year"]):
            errs.append(f"{n}行目：fiscal_year が西暦の年度でない（{r['fiscal_year']!r}）")
        if r["category"] == "koho":
            if r["date"] and not re.fullmatch(r"[0-9]{4}-[0-9]{2}", r["date"]):
                errs.append(f"{n}行目：広報ひのの date が YYYY-MM でない（{r['date']!r}）")
        elif r["date"] and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", r["date"]):
            errs.append(f"{n}行目：date が YYYY-MM-DD でない（{r['date']!r}）")
        if not r["url"].startswith("https://www.town.shiga-hino.lg.jp/"):
            errs.append(f"{n}行目：url が町のサイトでない（{r['url']}）")
        a = r.get("archive_url") or ""
        if a:
            m = ARCHIVE_RE.fullmatch(a)
            if not m or m.group(2) != r["url"]:
                errs.append(f"{n}行目：archive_url が url の保存版の形でない（{a}）")
    if errs:
        raise Stop(f"依頼の誤り {len(errs)} 件\n" + "\n".join(f"  {e}" for e in errs))


# ---- 大きさ・検収 ----

def size_bytes(label):
    """掲載ページの大きさの表記（例「1.23MB」「443.03KB」）をバイトにする。読めなければ None。"""
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMG])B", label or "", re.I)
    if not m:
        return None
    return float(m.group(1)) * {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[m.group(2).upper()]


def classify(page_chars):
    """§6 の検収：ページごとの文字数（空白と改ページを除く）から ok・partial・image を決める。"""
    if not page_chars:
        return "ng"
    rate = sum(1 for c in page_chars if c >= PAGE_MIN) / len(page_chars)
    if rate >= OK_RATE:
        return "ok"
    if rate >= IMAGE_RATE:
        return "partial"
    return "image"


def count_chars(text):
    return len(re.sub(r"\s", "", text))


def unexpected(text):
    """§6：想定外の字の数（ALLOW の外の字。部首の字は数えない）。"""
    n = 0
    for ch in text:
        c = ord(ch)
        if RADICAL[0] <= c <= RADICAL[1] or any(a <= c <= b for a, b in ALLOW):
            continue
        n += 1
    return n


def unexpected_note(text):
    """§6：想定外の字のあるページの注記（無ければ空）。例「想定外の字のあるページ：1（11字）・6（1字）」"""
    parts = text.split("\f")
    found = [f"{i + 1}（{n}字）" for i, n in enumerate(unexpected(p) for p in parts) if n]
    return f"想定外の字のあるページ：{'・'.join(found)}" if found else ""


def extract_pdf(path):
    """（テキスト、ページ数、ページごとの文字数）。pdftotext のテキストは改ページ（\\f）で区切られる。"""
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True)
    if info.returncode != 0:
        raise Stop(f"{os.path.basename(path)}：pdfinfo が終了コード {info.returncode}（{info.stderr.strip()}）")
    m = re.search(r"^Pages:\s+([0-9]+)", info.stdout, re.M)
    pages = int(m.group(1)) if m else 0
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True)
    if r.returncode != 0:
        raise Stop(f"{os.path.basename(path)}：pdftotext が終了コード {r.returncode}")
    text = r.stdout.decode("utf-8")
    parts = text.split("\f")
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    if len(parts) != pages:
        raise Stop(f"{os.path.basename(path)}：ページ数（{pages}）と改ページで区切った数（{len(parts)}）が合わない")
    text, back = restore_hyphens(path, text)
    parts = text.split("\f")[:pages]
    chars = [count_chars(p) for p in parts]
    return text, pages, chars, back


# ---- 行の終わりの「-」を戻す（§5.4） ----

NS_X = "{http://www.w3.org/1999/xhtml}"
BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")
HYPHEN_IGNORE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff\u202a-\u202e\u2066-\u2069"
                           "\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufefe]")   # §5.4：突き合わせで数えない字
WSPACE = re.compile(r"\s")


HYPHEN_KINDS = "-\u2010\u2011\u2012\u2013\u2014\u2212\uff0d"   # §5.4：「-」の類（既定の取り出しが行の終わりで消すことのある字。版によって違う）


def bbox_lines(path):
    """§5.4：-bbox-layout のページごとの [(行の文字, 行の終わりが「-」の類で、次の行か次の段がある行か)]。"""
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", "-bbox-layout", path, "-"], capture_output=True)
    if r.returncode != 0:
        raise Stop(f"{os.path.basename(path)}：pdftotext -bbox-layout が終了コード {r.returncode}")
    out = BAD_XML.sub("", re.sub(r"<!DOCTYPE[^>]*>", "", r.stdout.decode("utf-8")))
    try:
        root = ET.fromstring(out)
    except ET.ParseError as e:
        raise Stop(f"{os.path.basename(path)}：-bbox-layout の出力を読めない（{e}）")
    pages = []
    for page in root.iter(NS_X + "page"):
        lines = []
        for flow in page.findall(NS_X + "flow"):
            blocks = flow.findall(NS_X + "block")
            for j, block in enumerate(blocks):
                ls = block.findall(NS_X + "line")
                for k, line in enumerate(ls):
                    t = "".join((w.text or "") for w in line.findall(NS_X + "word"))
                    lines.append((t, bool(t) and t[-1] in HYPHEN_KINDS and (k + 1 < len(ls) or j + 1 < len(blocks))))
        pages.append(lines)
    return pages


def restore_page(d, lines):
    """§5.4：1ページの既定の取り出し d に、消された「-」の類と改行を戻す。
    候補の行（終わりが「-」の類）ごとに、その字が既定の取り出しに残っている（keep）か消された（cut）かを、
    空白を除いた字の並びが行の順に既定の取り出しと一致するように決める（両方あり得るときは残っているほうを取る）。
    （戻した後の文字、戻した字の数）。どう決めても一致しなければ None。"""
    a = WSPACE.sub("", HYPHEN_IGNORE.sub("", d))
    bodies = [(WSPACE.sub("", HYPHEN_IGNORE.sub("", t)), cand) for t, cand in lines]
    failed = set()                       # 戻ってやり直した (行, 位置)
    stack = [(0, 0, [])]                 # (次の行, 既定の取り出しの位置, cuts)
    cuts = None
    while stack:
        i, pos, cs = stack.pop()
        if (i, pos) in failed:
            continue
        if i == len(bodies):
            if pos == len(a):
                cuts = cs
                break
            failed.add((i, pos))
            continue
        body, cand = bodies[i]
        nxt = []
        if cand and body and a.startswith(body[:-1], pos):      # 消された（あとで試す）
            nxt.append((i + 1, pos + len(body) - 1, cs + [(pos + len(body) - 1, body[-1])]))
        if a.startswith(body, pos):                              # 残っている（先に試す）
            nxt.append((i + 1, pos + len(body), cs))
        if not nxt:
            failed.add((i, pos))
        stack.extend(nxt)                                        # 後ろに積んだものから試す
    if cuts is None:
        # 行の順で一致しなくても、字の数が同じなら消された字は無い（並びの違いは §5.4 の対象外）
        return (d, 0) if len(a) == sum(len(b) for b, _ in bodies) else None
    if not cuts:
        return d, 0
    out, n, ci = [], 0, 0
    for ch in d:
        if not (WSPACE.match(ch) or HYPHEN_IGNORE.match(ch)):
            while ci < len(cuts) and cuts[ci][0] == n:
                out.append(cuts[ci][1] + "\n")
                ci += 1
            n += 1
        out.append(ch)
    while ci < len(cuts) and cuts[ci][0] == n:   # ページの終わり
        out.append(cuts[ci][1] + "\n")
        ci += 1
    return ("".join(out), len(cuts)) if ci == len(cuts) else None


def restore_hyphens(path, text):
    """§5.4：（戻した後のテキスト、戻した「-」の類の数）。確かめが合わなければ止める。"""
    name = os.path.basename(path)
    dp = text.split("\f")
    bp = bbox_lines(path)
    if len(dp) - 1 != len(bp):
        raise Stop(f"{name}：既定の取り出し（{len(dp) - 1}ページ）と -bbox-layout（{len(bp)}ページ）のページの数が違う")
    res, back = [], 0
    for k, lines in enumerate(bp):
        r = restore_page(dp[k], lines)
        if r is None:
            raise Stop(f"{name}：{k + 1}ページで、既定の取り出しと -bbox-layout の字の並びが合わない（§5.4）")
        res.append(r[0])
        back += r[1]
    res.append(dp[-1])
    out = "\f".join(res)
    for h in HYPHEN_KINDS:
        want = sum(t.count(h) for lines in bp for t, _ in lines)
        if out.count(h) != want:
            raise Stop(f"{name}：戻した後の「{h}」（{out.count(h)}）が -bbox-layout の「{h}」（{want}）と合わない（§5.4）")
    if re.sub(f"[{re.escape(HYPHEN_KINDS)}]\n", "", out) != re.sub(f"[{re.escape(HYPHEN_KINDS)}]\n", "", text):
        raise Stop(f"{name}：戻した「-」の類と改行を除いても、既定の取り出しに戻らない（§5.4）")
    return out, back


def extract_raw(path, pages):
    """pdftotext -raw のテキスト（§5.1）。改ページで区切った数がページ数と合わなければ止める。"""
    r = subprocess.run(["pdftotext", "-raw", "-enc", "UTF-8", path, "-"], capture_output=True)
    if r.returncode != 0:
        raise Stop(f"{os.path.basename(path)}：pdftotext -raw が終了コード {r.returncode}")
    text = r.stdout.decode("utf-8")
    parts = text.split("\f")
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    if len(parts) != pages:
        raise Stop(f"{os.path.basename(path)}：ページ数（{pages}）と -raw の改ページで区切った数（{len(parts)}）が合わない")
    return text


def evaluate(row, path):
    """取得済みの原典から、台帳の format・bytes・sha256・pages・text_chars・text_method・check を埋め、テキストを返す。"""
    with open(path, "rb") as f:
        data = f.read()
    row["bytes"] = str(len(data))
    row["sha256"] = hashlib.sha256(data).hexdigest()
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    row["format"] = ext
    text = None
    if ext == "pdf":
        text, pages, chars, back = extract_pdf(path)
        row["pages"] = str(pages)
        row["text_chars"] = str(sum(chars))
        row["text_method"] = "pdftotext+hyphen"
        row["check"] = classify(chars)
        if row["check"] == "partial":
            low = [str(i + 1) for i, c in enumerate(chars) if c < PAGE_MIN]
            row["note"] = join_note(row.get("note"), f"文字の少ないページ：{'・'.join(low)}")
        if row["check"] in ("image", "ng"):
            text = None
        else:
            row["note"] = join_note(row.get("note"), unexpected_note(text))
            if back:
                row["note"] = join_note(row["note"], f"行の終わりの「-」を戻した：{back}")
    elif ext == "xlsx":
        sheets = extract_xlsx(path)
        row["text_method"] = "openpyxl"
        if sheets is None:
            row["pages"] = row["text_chars"] = ""
            row["check"] = "ng"
        else:
            row["pages"] = str(len(sheets))
            row["text_chars"] = str(sum(count_chars(sh["text"]) + sum(count_chars(r[5]) for r in sh["zukei"]) for sh in sheets))
            row["check"] = "ok" if any(sh["rows"] or sh["zukei"] for sh in sheets) else "ng"
            text = sheets if row["check"] == "ok" else None
    elif ext in ("html", "htm"):
        body, posted, updated = extract_html(data)
        row["pages"] = ""
        row["text_method"] = "html"
        row["format"] = "html"
        if body is None or count_chars(body) < PAGE_MIN:
            row["text_chars"] = str(count_chars(body)) if body else ""
            row["check"] = "ng"
        else:
            row["text_chars"] = str(count_chars(body))
            row["check"] = "ok"
            text = body
            row["note"] = join_note(row.get("note"), unexpected_note(body))
        if posted and not row.get("date"):
            row["date"] = posted
        if updated:
            row["note"] = join_note(row.get("note"), f"ページの更新日 {updated}")
    else:
        row["pages"] = row["text_chars"] = ""
        row["text_method"] = "none"
        row["check"] = "notext"
    return text


# ---- Excel（§5.2） ----

def cell_text(v):
    """§5.2：セルの値の書き方。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    return re.sub(r"[\t\r\n]", " ", str(v))


def extract_xlsx(path):
    """§5.2：シートごとの {name, state, rows（行ごとのセルの文字のリスト）, ncols, missing, text}。開けなければ None。"""
    try:
        import openpyxl
    except ImportError:
        raise Stop("openpyxl が無い（pip install openpyxl）")
    try:
        wb_v = openpyxl.load_workbook(path, data_only=True)
        wb_f = openpyxl.load_workbook(path, data_only=False)
    except Exception:
        return None
    out = []
    for ws_v, ws_f in zip(wb_v.worksheets, wb_f.worksheets):
        rows, ncols, missing = [], 0, 0
        for r_v, r_f in zip(ws_v.iter_rows(values_only=True), ws_f.iter_rows(values_only=True)):
            cells = [cell_text(v) for v in r_v]
            for v, f in zip(r_v, r_f):
                if v is None and isinstance(f, str) and f.startswith("="):
                    missing += 1
            while cells and cells[-1] == "":
                cells.pop()
            rows.append(cells)
            ncols = max(ncols, len(cells))
        while rows and not rows[-1]:
            rows.pop()
        text = "\n".join("\t".join(c) for c in rows) + ("\n" if rows else "")
        out.append({"name": ws_v.title, "state": ws_v.sheet_state, "rows": rows, "ncols": ncols, "missing": missing,
                    "text": text})
    try:
        drawings = xlsx_drawings(path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, AttributeError) as e:
        raise Stop(f"{os.path.basename(path)}：図形とグラフを読めない（{e}）")
    if len(drawings) != len(out):
        raise Stop(f"{os.path.basename(path)}：シートの数（{len(out)}）と、図形を読んだシートの数（{len(drawings)}）が違う")
    for sh, z in zip(out, drawings):
        sh["zukei"] = z
        sh["zukei_text"] = ("\t".join(ZUKEI_COLUMNS) + "\n" +
                            "".join("\t".join("" if v is None else str(v) for v in r) + "\n" for r in z)) if z else ""
    return out


# ---- Excel の図形とグラフの字（§5.2） ----

XNS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
ZUKEI_COLUMNS = ["行", "列", "種類", "系列", "項目", "文字"]


def _q(tag):
    p, t = tag.split(":")
    return "{" + XNS[p] + "}" + t


def _one_line(t):
    return re.sub(r"[\t\r\n]", " ", t or "")


def _rels(z, part):
    """part（例 xl/worksheets/sheet1.xml）の関係ファイルの {Id: 部品の名前}。"""
    d, b = os.path.split(part)
    rp = f"{d}/_rels/{b}.rels"
    if rp not in z.namelist():
        return {}
    out = {}
    for r in ET.fromstring(z.read(rp)).findall(_q("pr:Relationship")):
        t = r.get("Target")
        if r.get("TargetMode") == "External":
            continue
        t = t.lstrip("/") if t.startswith("/") else os.path.normpath(os.path.join(d, t)).replace(os.sep, "/")
        out[r.get("Id")] = t
    return out


def _para_text(el):
    """txBody などの中の段落を空白1つでつないだ字。"""
    paras = []
    for p in el.iter(_q("a:p")):
        t = "".join((x.text or "") for x in p.iter() if x.tag in (_q("a:t"),))
        if t.strip():
            paras.append(t.strip())
    return _one_line(" ".join(paras)).strip()


def _choose(el):
    """mc:AlternateContent は Choice の中身（無ければ Fallback）だけを見る。"""
    out = []
    for ch in el:
        if ch.tag == _q("mc:AlternateContent"):
            pick = ch.find(_q("mc:Choice"))
            if pick is None:
                pick = ch.find(_q("mc:Fallback"))
            if pick is not None:
                out += _choose(pick)
        else:
            out.append(ch)
    return out


def _strs(el):
    """strRef・numRef・strLit などの保存された値の {idx: 字}。"""
    out = {}
    if el is None:
        return out
    for pt in el.iter(_q("c:pt")):
        v = pt.find(_q("c:v"))
        out[int(pt.get("idx", "0"))] = _one_line(v.text if v is not None else "")
    if not out:
        v = el.find(_q("c:v"))
        if v is not None:
            out[0] = _one_line(v.text)
    return out


def _title(el):
    if el is None:
        return ""
    rich = el.find(".//" + _q("c:rich"))
    if rich is not None:
        return _para_text(rich)
    tx = el.find(_q("c:tx"))
    return " ".join(_strs(tx).values()).strip() if tx is not None else ""


def chart_rows(xml):
    """グラフの部品から（種類、系列、項目、文字）の並び。"""
    root = ET.fromstring(xml)
    rows = []
    chart = root.find(_q("c:chart"))
    if chart is None:
        return rows
    t = _title(chart.find(_q("c:title")))
    if t:
        rows.append(("グラフの題", "", "", t))
    plot = chart.find(_q("c:plotArea"))
    if plot is None:
        return rows
    for ax in plot:
        if ax.tag in (_q("c:valAx"), _q("c:catAx"), _q("c:dateAx"), _q("c:serAx")):
            t = _title(ax.find(_q("c:title")))
            if t:
                rows.append(("軸の題", "", "", t))
    for ser in plot.iter(_q("c:ser")):
        tx = ser.find(_q("c:tx"))
        name = " ".join(_strs(tx).values()).strip() if tx is not None else ""
        cat = _strs(ser.find(_q("c:cat"))) or _strs(ser.find(_q("c:xVal")))
        val = _strs(ser.find(_q("c:val"))) or _strs(ser.find(_q("c:yVal")))
        for i in sorted(val):
            rows.append(("グラフの値", name, cat.get(i, ""), val[i]))
    return rows


def _shapes(el):
    """図形（sp）の字を、グループ（grpSp）の中まで順に集める。"""
    out = []
    for ch in _choose(el):
        if ch.tag == _q("xdr:sp"):
            tb = ch.find(_q("xdr:txBody"))
            t = _para_text(tb) if tb is not None else ""
            if t:
                out.append(t)
        elif ch.tag == _q("xdr:grpSp"):
            out += _shapes(ch)
    return out


def _charts(el):
    out = []
    for ch in _choose(el):
        if ch.tag == _q("xdr:graphicFrame"):
            c = ch.find(".//" + _q("c:chart"))
            if c is not None:
                out.append(c.get(_q("r:id")))
        elif ch.tag == _q("xdr:grpSp"):
            out += _charts(ch)
    return out


def xlsx_drawings(path):
    """§5.2：ブックの中のシートの順に、図形とグラフの字の行 [(行, 列, 種類, 系列, 項目, 文字)] のリスト。"""
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        wrels = _rels(z, "xl/workbook.xml")
        result = []
        for sh in wb.find(_q("m:sheets")).findall(_q("m:sheet")):
            part = wrels.get(sh.get(_q("r:id")), "")
            rows = []
            for rid, dpart in _rels(z, part).items() if part else []:
                if "/drawings/" not in dpart or not dpart.endswith(".xml") or dpart not in z.namelist():
                    continue
                drels = _rels(z, dpart)
                dr = ET.fromstring(z.read(dpart))
                for anchor in _choose(dr):
                    fr = anchor.find(_q("xdr:from"))
                    if fr is not None:
                        r0, c0 = int(fr.find(_q("xdr:row")).text) + 1, int(fr.find(_q("xdr:col")).text) + 1
                    else:
                        r0 = c0 = None
                    for t in _shapes(anchor):
                        rows.append((r0, c0, "図形", "", "", t))
                    for cid in _charts(anchor):
                        cpart = drels.get(cid)
                        if cpart and cpart in z.namelist():
                            for kind, ser, item, t in chart_rows(z.read(cpart)):
                                rows.append((r0, c0, kind, ser, item, t))
            # 行・列の順（位置の無いものは後ろ）。同じ位置はファイルの中の順（sorted は安定）
            rows = sorted(rows, key=lambda x: (x[0] is None, x[0] or 0, x[1] or 0))
            result.append(rows)
    return result


# ---- 町のサイトのページ（§5.3） ----

class _Body(html.parser.HTMLParser):
    """本文の枠（id か class が want の要素）の中の文字を集める。"""

    def __init__(self, want):
        super().__init__(convert_charrefs=True)
        self.want, self.depth, self.done, self.skip = want, 0, False, 0
        self.out, self.cell = [], False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = dict(attrs)
        if self.depth == 0:
            if a.get("id") == self.want or self.want in (a.get("class") or "").split():
                if tag not in HTML_VOID:
                    self.depth = 1
            return
        if tag not in HTML_VOID:
            self.depth += 1
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in HTML_BLOCK or tag == "br":
            self.out.append("\n")
        elif tag in ("td", "th"):
            if self.cell:
                self.out.append("\t")
            self.cell = True
        if tag == "tr":
            self.cell = False

    def handle_endtag(self, tag):
        if self.done or self.depth == 0 or tag in HTML_VOID:
            return
        self.depth -= 1
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in HTML_BLOCK:
            self.out.append("\n")
        if self.depth == 0:
            self.done = True

    def handle_data(self, d):
        if self.depth and not self.done and not self.skip:
            self.out.append(re.sub(r"[ \t\r\n\u3000]+", " ", d) if d.strip() else (" " if d else ""))

    def text(self):
        lines = []
        for ln in "".join(self.out).split("\n"):
            ln = "\t".join(c.strip() for c in ln.split("\t")).strip(" ")
            if ln.strip("\t "):   # 空の行は置かない
                lines.append(ln)
        return "\n".join(lines) + "\n" if lines else ""


def extract_html(data):
    """§5.3：（本文の文字か None、公開日、更新日）。"""
    s = data.decode("utf-8", errors="replace")
    posted = re.search(r'itemprop="datePublished"[^>]*datetime="([0-9]{4}-[0-9]{2}-[0-9]{2})', s)
    updated = re.search(r'itemprop="dateModified"[^>]*datetime="([0-9]{4}-[0-9]{2}-[0-9]{2})', s)
    body = None
    for want in HTML_BODY:
        p = _Body(want)
        p.feed(s)
        p.close()
        if p.depth or p.done:
            body = p.text()
            break
    return body, posted.group(1) if posted else "", updated.group(1) if updated else ""


def join_note(a, b):
    return "。".join(x for x in (a, b) if x)


# ---- 取得 ----

def ext_of(url):
    e = os.path.splitext(url.split("?")[0])[1].lower().lstrip(".")
    return e if e in ("pdf", "xlsx", "xls", "docx", "doc", "html", "htm", "csv") else "bin"


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return res.read()
    except Exception as e:  # HTTP のエラーもつながらないのも、ここで止める（§6）
        raise Stop(f"取得できない（{url}）：{e}")


def write_text(i, text):
    write_atomic(os.path.join(TEXT_DIR, f"{i}.txt"), lambda f: f.write(text))


def write_texts(row, path, text):
    """§5 のテキストと §5.1 の -raw のテキストを置く。text が None（image・ng・PDF 以外）なら、どちらも置かない（あれば消す）。
    xlsx は §5.2 のシートごとの TSV、html は §5.3 の本文だけ（-raw は作らない）。"""
    tp = os.path.join(TEXT_DIR, f"{row['id']}.txt")
    rp = os.path.join(TEXT_RAW_DIR, f"{row['id']}.txt")
    xd = os.path.join(TEXT_DIR, row["id"])
    if os.path.isdir(xd) and (text is None or row["format"] != "xlsx"):
        shutil.rmtree(xd)
    if text is None or row["format"] in ("xlsx", "html"):
        for q in (tp, rp):
            if os.path.exists(q):
                os.remove(q)
    if text is None:
        return
    if row["format"] == "xlsx":
        if os.path.isdir(xd):
            shutil.rmtree(xd)
        os.makedirs(xd)
        index = ["順\tシートの名前\t状態\t行の数\t列の数\t計算結果の無いセルの数\t図形とグラフの字の行の数"]
        for k, sh in enumerate(text, 1):
            write_atomic(os.path.join(xd, f"{k:02d}.tsv"), lambda f, t=sh["text"]: f.write(t))
            if sh["zukei"]:
                write_atomic(os.path.join(xd, f"{k:02d}-zukei.tsv"), lambda f, t=sh["zukei_text"]: f.write(t))
            name = re.sub(r"[\t\r\n]", " ", sh["name"])
            index.append(f"{k:02d}\t{name}\t{sh['state']}\t{len(sh['rows'])}\t{sh['ncols']}\t{sh['missing']}\t{len(sh['zukei'])}")
        write_atomic(os.path.join(xd, "sheets.tsv"), lambda f: f.write("\n".join(index) + "\n"))
        return
    if row["format"] == "html":
        write_text(row["id"], text)
        return
    raw = extract_raw(path, int(row["pages"]))
    write_text(row["id"], text)
    write_atomic(rp, lambda f: f.write(raw))


def read_request(path):
    with open(path, encoding="utf-8", newline="") as f:
        head = f.readline().rstrip("\n").split("\t")
    return read_tsv(path, REQUEST_COLUMNS_ARCHIVE if head == REQUEST_COLUMNS_ARCHIVE else REQUEST_COLUMNS)


def archive_note(a):
    t = ARCHIVE_RE.fullmatch(a).group(1)
    return f"取得元：Webアーカイブの保存版（{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:]} UTC、{a}）"


def fetch(req_path, opener=download, today=None):
    reqs = read_request(req_path)
    rows = read_sources()
    check_request(reqs, {r["id"] for r in rows})
    os.makedirs(RAW_DIR, exist_ok=True)
    done, stop = [], None
    for n, q in enumerate(reqs):
        if n:
            time.sleep(WAIT)
        try:
            data = opener(q.get("archive_url") or q["url"])
        except Stop as e:
            stop = str(e)
            break
        path = os.path.join(RAW_DIR, f"{q['id']}.{ext_of(q['url'])}")
        with open(path, "wb") as f:
            f.write(data)
        row = {c: q.get(c, "") for c in COLUMNS}
        row["fetched"] = (today or date.today()).isoformat()
        row["note"] = archive_note(q["archive_url"]) if q.get("archive_url") else ""
        if q.get("archive_url"):   # §6：保存版から取るときは、町のサイトに無いことを確かめてある。removed_judgment は依頼者が書く
            row["archive_url"] = q["archive_url"]
            row["removed"] = row["fetched"]
        want = size_bytes(q.get("size_label"))
        if want and abs(len(data) - want) / want >= SIZE_TOLERANCE:
            row["note"] = join_note(row["note"], f"掲載ページの大きさ {q['size_label']}・取得したファイル {len(data):,} バイト")
        text = evaluate(row, path)
        write_texts(row, path, text)
        rows.append(row)
        done.append(row)
    write_sources(rows)
    return done, stop, len(reqs)


def extract(ids=None):
    rows = read_sources()
    targets = [r for r in rows if not ids or r["id"] in ids]
    missing = [i for i in (ids or []) if i not in {r["id"] for r in rows}]
    if missing:
        raise Stop(f"台帳に無い id：{'・'.join(missing)}")
    for r in targets:
        paths = [os.path.join(RAW_DIR, f) for f in os.listdir(RAW_DIR) if os.path.splitext(f)[0] == r["id"]] \
            if os.path.isdir(RAW_DIR) else []
        if len(paths) != 1:
            raise Stop(f"{r['id']}：sources_raw/ に原典が無いか、2つ以上ある")
        old_sha = r["sha256"]
        r["note"] = AUTO_NOTE.sub("", r.get("note") or "").strip("。")
        text = evaluate(r, paths[0])
        if old_sha and r["sha256"] != old_sha:
            raise Stop(f"{r['id']}：sources_raw/ の原典の sha256 が台帳と違う（差し替えは新しい id で取る。§2）")
        write_texts(r, paths[0], text)
    write_sources(rows)
    return targets


def raw(ids=None):
    """§5.1：§5 のテキストがある資料について、-raw のテキストを作り直す。原典の sha256 が台帳と違えば止める。台帳は変えない。"""
    rows = read_sources()
    missing = [i for i in (ids or []) if i not in {r["id"] for r in rows}]
    if missing:
        raise Stop(f"台帳に無い id：{'・'.join(missing)}")
    targets = [r for r in rows if (not ids or r["id"] in ids) and r["format"] == "pdf" and r["check"] in ("ok", "partial")]
    for r in targets:
        path = os.path.join(RAW_DIR, f"{r['id']}.pdf")
        if not os.path.isfile(path):
            raise Stop(f"{r['id']}：sources_raw/ に原典が無い")
        with open(path, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        if sha != r["sha256"]:
            raise Stop(f"{r['id']}：sources_raw/ の原典の sha256 が台帳と違う（差し替えは新しい id で取る。§2）")
        text = extract_raw(path, int(r["pages"]))
        write_atomic(os.path.join(TEXT_RAW_DIR, f"{r['id']}.txt"), lambda f, t=text: f.write(t))
    return targets


# ---- OCR の下書き（§10） ----

def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise Stop(f"{os.path.basename(cmd[0])} が終了コード {r.returncode}（{' / '.join(tail)}）")
    return r.stdout


def ndlocr_method():
    """§4：ocr_method の値（ndlocr-lite@<コミットの先頭7桁>）。NDLOCR-Lite の作業ツリーに変更があれば止める。"""
    if not os.path.isfile(os.path.join(NDLOCR_DIR, "src", "ocr.py")):
        raise Stop(f"NDLOCR-Lite が無い（{NDLOCR_DIR}/src/ocr.py）。HTD_NDLOCR で場所を指定する")
    if not os.path.isfile(NDLOCR_PYTHON):
        raise Stop(f"NDLOCR-Lite の Python が無い（{NDLOCR_PYTHON}）。HTD_NDLOCR_PYTHON で場所を指定する")
    if run(["git", "-C", NDLOCR_DIR, "status", "--porcelain"]).strip():
        raise Stop(f"{NDLOCR_DIR} の作業ツリーに変更がある（版が決まらない）")
    return "ndlocr-lite@" + run(["git", "-C", NDLOCR_DIR, "rev-parse", "--short=7", "HEAD"]).strip()


def ocr_one(row, method, today):
    """1本の PDF の全ページを読み、data/ocr/<id>.txt と台帳の ocr_method・ocr_date を書く。"""
    i, pages = row["id"], int(row["pages"])
    pdf = os.path.join(RAW_DIR, f"{i}.pdf")
    if not os.path.isfile(pdf):
        raise Stop(f"{i}：sources_raw/ に原典が無い")
    with open(pdf, "rb") as f:
        if hashlib.sha256(f.read()).hexdigest() != row["sha256"]:
            raise Stop(f"{i}：sources_raw/ の原典の sha256 が台帳と違う")
    base = os.path.join(OCR_RAW_DIR, i)
    img, out = os.path.join(base, "img"), os.path.join(base, "out")
    if os.path.isdir(base):
        shutil.rmtree(base)
    os.makedirs(img)
    os.makedirs(out)
    for n in range(1, pages + 1):   # 画像の名前にページの番号を4桁で入れ、読む順にかかわらず番号で並べる
        run(["pdftoppm", "-r", str(OCR_DPI), "-gray", "-png", "-singlefile", "-f", str(n), "-l", str(n),
             pdf, os.path.join(img, f"p{n:04d}")])
    run([NDLOCR_PYTHON, "ocr.py", "--sourcedir", img, "--output", out], cwd=os.path.join(NDLOCR_DIR, "src"))
    texts = []
    for n in range(1, pages + 1):
        stem = os.path.join(out, f"p{n:04d}")
        if os.path.isfile(stem + ".txt"):
            with open(stem + ".txt", encoding="utf-8") as f:
                t = f.read()
        elif os.path.isfile(stem + ".json") or os.path.isfile(stem + ".xml"):
            t = ""   # 字の無いページ（テキストが出ないとき）
        else:
            raise Stop(f"{i}：{n}ページの NDLOCR-Lite の出力が無い")
        if "\f" in t:
            raise Stop(f"{i}：{n}ページの NDLOCR-Lite の出力に改ページがある")
        texts.append(t)
    extra = sorted(set(os.path.splitext(f)[0] for f in os.listdir(out)) - {f"p{n:04d}" for n in range(1, pages + 1)})
    if extra:
        raise Stop(f"{i}：ページに当たらない出力がある（{'・'.join(extra[:5])}）")
    joined = "".join(t + "\f" for t in texts)   # n 番目の改ページの前が n ページ目（§5・§10）
    write_atomic(os.path.join(OCR_DIR, f"{i}.txt"), lambda f: f.write(joined))
    shutil.rmtree(img)   # 画像は作り直せるので消す。座標つきの出力（out/）は残す
    row["ocr_method"], row["ocr_date"] = method, today.isoformat()


def ocr(ids=None, today=None, log=print):
    rows = read_sources()
    missing = [i for i in (ids or []) if i not in {r["id"] for r in rows}]
    if missing:
        raise Stop(f"台帳に無い id：{'・'.join(missing)}")
    method = ndlocr_method()
    today = today or date.today()
    if ids:
        targets = [r for r in rows if r["id"] in ids]
        bad = [r["id"] for r in targets if r["format"] != "pdf"]
        if bad:
            raise Stop(f"PDF でない資料：{'・'.join(bad)}")
    else:
        targets = [r for r in rows if r["format"] == "pdf" and
                   (r["ocr_method"] != method or not os.path.isfile(os.path.join(OCR_DIR, f"{r['id']}.txt")))]
    done = []
    for k, r in enumerate(targets, 1):
        t0 = time.time()
        ocr_one(r, method, today)
        write_sources(rows)   # 1本ごとに書く（途中で止めても続きから進める）
        done.append(r)
        log(f"{k}／{len(targets)}	{r['id']}	{r['pages']}ページ	{time.time() - t0:.0f}秒")
    return done, method


def summary(rows):
    from collections import Counter
    c = Counter(r["check"] for r in rows)
    return "・".join(f"{k} {n}" for k, n in sorted(c.items()))


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) == 2 and args[0] == "fetch":
            done, stop, total = fetch(args[1])
            print(f"取得 {len(done)}／{total}（{summary(done)}）")
            for r in done:
                print(f"  {r['id']}\t{r['check']}\t{r['pages']}ページ\t{r['text_chars']}字\t{r['note']}")
            if stop:
                print(f"停止：{stop}", file=sys.stderr)
                return 1
            return 0
        if args and args[0] == "extract":
            rows = extract(args[1:] or None)
            print(f"やり直し {len(rows)}（{summary(rows)}）")
            return 0
        if args and args[0] == "raw":
            rows = raw(args[1:] or None)
            print(f"-raw のテキスト {len(rows)}本")
            return 0
        if args and args[0] == "ocr":
            done, method = ocr(args[1:] or None, log=lambda s: print(s, flush=True))
            print(f"OCR {len(done)}本・{sum(int(r['pages']) for r in done)}ページ（{method}）")
            return 0
    except Stop as e:
        print(f"停止：{e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
