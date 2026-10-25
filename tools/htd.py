"""hino-town-docs の取得と検収の道具（DOCS_SCHEMA.md §2・§4〜§6）。

  python3 -B tools/htd.py fetch <依頼.tsv>   依頼の資料を町のサイトから取り、文字を取り出して、台帳とテキストを書く
  python3 -B tools/htd.py extract [<id> ...]  取得済みの原典（sources_raw/）から、文字の取り出しと検収をやり直す（ネットワークを使わない）
  python3 -B tools/htd.py ocr [<id> ...]      原典の全ページを NDLOCR-Lite で読み、OCR の下書き data/ocr/<id>.txt を作る（§10。ネットワークを使わない）

依頼の TSV（UTF-8、タブ区切り、1行目は見出し）の列：id・category・title・fiscal_year・date・page_url・url・size_label
（size_label は掲載ページに書かれた大きさ。例「1.23MB」。無ければ空）。最後に archive_url の列を足してもよい。
archive_url は、町のサイトから外された資料を Webアーカイブの保存版から取るときだけ書く（§6。
https://web.archive.org/web/<14桁の保存日時>id_/<url> の形）。台帳の url は町の元の URL のままとし、note に取得元を書く。依頼の id が台帳にすでにあれば、何もせずに止める。
取得は1秒に1件以内。町のサイトから断られたとき（HTTP のエラー、つながらない）は、そこで止めて報告する。
止めるまでに取得したものは、台帳とテキストに書く。
原典は sources_raw/<id>.<拡張子> に置く（リポジトリに入れない）。テキストは data/text/<id>.txt（改ページ \\f を残す）。

ocr は、id を指定しなければ、OCR の下書きがまだ無いか、ocr_method がいまの NDLOCR-Lite の版と違う PDF の資料だけを読む
（途中で止めても、もう一度動かせば続きから進む。台帳は資料1本ごとに書く）。
NDLOCR-Lite の場所は環境変数 HTD_NDLOCR（既定 ~/ocr/ndlocr-lite）、その Python は HTD_NDLOCR_PYTHON（既定 ~/ocr/venv-ndlocr/bin/python）。
"""
import csv
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "data", "sources.tsv")
TEXT_DIR = os.path.join(ROOT, "data", "text")
OCR_DIR = os.path.join(ROOT, "data", "ocr")
OCR_RAW_DIR = os.path.join(ROOT, "ocr_raw")
NDLOCR_DIR = os.path.expanduser(os.environ.get("HTD_NDLOCR", "~/ocr/ndlocr-lite"))
NDLOCR_PYTHON = os.path.expanduser(os.environ.get("HTD_NDLOCR_PYTHON", "~/ocr/venv-ndlocr/bin/python"))
OCR_DPI = 300   # §10
RAW_DIR = os.path.join(ROOT, "sources_raw")
COLUMNS = ["id", "category", "title", "fiscal_year", "date", "page_url", "url", "format", "bytes", "sha256", "pages",
           "text_chars", "text_method", "ocr_method", "ocr_date", "fetched", "check", "note"]
COLUMNS_V1 = [c for c in COLUMNS if c not in ("ocr_method", "ocr_date")]   # 0c64af5 より前の台帳。読むときに2列を空で足す
REQUEST_COLUMNS = ["id", "category", "title", "fiscal_year", "date", "page_url", "url", "size_label"]
REQUEST_COLUMNS_ARCHIVE = REQUEST_COLUMNS + ["archive_url"]   # §6：Webアーカイブの保存版から取るとき
ARCHIVE_RE = re.compile(r"https://web\.archive\.org/web/([0-9]{14})id_/(.+)")
CATEGORIES = ("yosan", "kessan", "zaisei", "kofukin", "kansa", "kyoi", "koho", "jinji", "gyokaku", "shingi", "jigyo")
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
AUTO_NOTE = re.compile(r"(^|。)(文字の少ないページ|想定外の字のあるページ)：[^。]*")   # 検収のたびに道具が書き直す注記


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
        return rows
    return read_tsv(SOURCES, COLUMNS)


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
    chars = [count_chars(p) for p in parts]
    if len(chars) != pages:
        raise Stop(f"{os.path.basename(path)}：ページ数（{pages}）と改ページで区切った数（{len(chars)}）が合わない")
    return text, pages, chars


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
            row["note"] = join_note(row.get("note"), unexpected_note(text))
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
    write_atomic(os.path.join(TEXT_DIR, f"{i}.txt"), lambda f: f.write(text))


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
        want = size_bytes(q.get("size_label"))
        if want and abs(len(data) - want) / want >= SIZE_TOLERANCE:
            row["note"] = join_note(row["note"], f"掲載ページの大きさ {q['size_label']}・取得したファイル {len(data):,} バイト")
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
        r["note"] = AUTO_NOTE.sub("", r.get("note") or "").strip("。")
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
