"""hino-town-docs の取得と検収の道具（DOCS_SCHEMA.md §2・§4〜§6）。

  python3 -B tools/htd.py fetch <依頼.tsv>   依頼の資料を町のサイトから取り、文字を取り出して、台帳とテキストを書く
  python3 -B tools/htd.py extract [<id> ...]  取得済みの原典（sources_raw/）から、文字の取り出しと検収をやり直す（ネットワークを使わない）

依頼の TSV（UTF-8、タブ区切り、1行目は見出し）の列：id・category・title・fiscal_year・date・page_url・url・size_label
（size_label は掲載ページに書かれた大きさ。例「1.23MB」。無ければ空）。依頼の id が台帳にすでにあれば、何もせずに止める。
取得は1秒に1件以内。町のサイトから断られたとき（HTTP のエラー、つながらない）は、そこで止めて報告する。
止めるまでに取得したものは、台帳とテキストに書く。
原典は sources_raw/<id>.<拡張子> に置く（リポジトリに入れない）。テキストは data/text/<id>.txt（改ページ \\f を残す）。
"""
import csv
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "data", "sources.tsv")
TEXT_DIR = os.path.join(ROOT, "data", "text")
RAW_DIR = os.path.join(ROOT, "sources_raw")
COLUMNS = ["id", "category", "title", "fiscal_year", "date", "page_url", "url", "format", "bytes", "sha256", "pages",
           "text_chars", "text_method", "fetched", "check", "note"]
REQUEST_COLUMNS = ["id", "category", "title", "fiscal_year", "date", "page_url", "url", "size_label"]
CATEGORIES = ("yosan", "kessan", "zaisei", "kofukin", "kansa", "kyoi", "koho", "jinji", "gyokaku")
ID_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
PAGE_MIN = 50          # §6：文字のあるページの下限（空白と改ページを除いた文字数）
OK_RATE, IMAGE_RATE = 0.8, 0.2
SIZE_TOLERANCE = 0.1   # §6：掲載ページの大きさと1割以上違えば note に書く
WAIT = 1.0             # §6：1秒に1件以内
USER_AGENT = "hino-town-docs (+https://github.com/kken78/hino-town-docs)"


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
    return read_tsv(SOURCES, COLUMNS) if os.path.exists(SOURCES) else []


def write_sources(rows):
    os.makedirs(os.path.dirname(SOURCES), exist_ok=True)
    for r in rows:
        for c in COLUMNS:
            if "\t" in str(r.get(c, "")) or "\n" in str(r.get(c, "")):
                raise Stop(f"{r['id']} の {c} にタブか改行がある")
    with open(SOURCES, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(COLUMNS)
        for r in sorted(rows, key=lambda r: r["id"]):
            w.writerow([r.get(c, "") for c in COLUMNS])


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
        if r["date"] and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", r["date"]):
            errs.append(f"{n}行目：date が YYYY-MM-DD でない（{r['date']!r}）")
        if not r["url"].startswith("https://www.town.shiga-hino.lg.jp/"):
            errs.append(f"{n}行目：url が町のサイトでない（{r['url']}）")
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
    chars = [count_chars(p) for p in parts]
    if len(chars) != pages:
        raise Stop(f"{os.path.basename(path)}：ページ数（{pages}）と改ページで区切った数（{len(chars)}）が合わない")
    return text, pages, chars


def evaluate(row, path):
    """取得済みの原典から、台帳の format・bytes・sha256・pages・text_chars・text_method・check を埋め、テキストを返す。"""
    data = open(path, "rb").read()
    row["bytes"] = str(len(data))
    row["sha256"] = hashlib.sha256(data).hexdigest()
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    row["format"] = ext
    text = None
    if ext == "pdf":
        text, pages, chars = extract_pdf(path)
        row["pages"] = str(pages)
        row["text_chars"] = str(sum(chars))
        row["text_method"] = "pdftotext"
        row["check"] = classify(chars)
        if row["check"] == "partial":
            low = [str(i + 1) for i, c in enumerate(chars) if c < PAGE_MIN]
            row["note"] = join_note(row.get("note"), f"文字の少ないページ：{'・'.join(low)}")
        if row["check"] in ("image", "ng"):
            text = None
    else:
        row["pages"] = row["text_chars"] = ""
        row["text_method"] = "none"
        row["check"] = "notext"
    return text


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
    os.makedirs(TEXT_DIR, exist_ok=True)
    with open(os.path.join(TEXT_DIR, f"{i}.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(text)


def fetch(req_path, opener=download, today=None):
    reqs = read_tsv(req_path, REQUEST_COLUMNS)
    rows = read_sources()
    check_request(reqs, {r["id"] for r in rows})
    os.makedirs(RAW_DIR, exist_ok=True)
    done, stop = [], None
    for n, q in enumerate(reqs):
        if n:
            time.sleep(WAIT)
        try:
            data = opener(q["url"])
        except Stop as e:
            stop = str(e)
            break
        path = os.path.join(RAW_DIR, f"{q['id']}.{ext_of(q['url'])}")
        with open(path, "wb") as f:
            f.write(data)
        row = {c: q.get(c, "") for c in COLUMNS}
        row["fetched"] = (today or date.today()).isoformat()
        row["note"] = ""
        want = size_bytes(q.get("size_label"))
        if want and abs(len(data) - want) / want >= SIZE_TOLERANCE:
            row["note"] = f"掲載ページの大きさ {q['size_label']}・取得したファイル {len(data):,} バイト"
        text = evaluate(row, path)
        if text is not None:
            write_text(q["id"], text)
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
        note = re.sub(r"(^|。)文字の少ないページ：[0-9・]+", "", r.get("note") or "").strip("。")
        r["note"] = note
        text = evaluate(r, paths[0])
        if old_sha and r["sha256"] != old_sha:
            raise Stop(f"{r['id']}：sources_raw/ の原典の sha256 が台帳と違う（差し替えは新しい id で取る。§2）")
        tp = os.path.join(TEXT_DIR, f"{r['id']}.txt")
        if text is None:
            if os.path.exists(tp):
                os.remove(tp)
        else:
            write_text(r["id"], text)
    write_sources(rows)
    return targets


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
    except Stop as e:
        print(f"停止：{e}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
