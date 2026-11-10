"""町のサイトの巡回（DOCS_SCHEMA.md §12）。

町のサイトの分類を全部たどって、資料の一覧とページの一覧（§7）を作り、data/inventory/ のいちばん新しい一覧と、
台帳 data/sources.tsv、除外の表 data/watch_ignore.tsv とくらべて、見つけたものを Markdown（と JSON）で出す。
台帳・テキスト・data/inventory/ は変えない。作った一覧は --out のフォルダ（既定 build/watch/）に置く。

  python3 -B tools/watch_docs.py                          たどってくらべる（約40分。1秒に1件以内）
  python3 -B tools/watch_docs.py --json out.json          結果を JSON にも書く（Actions の Issue づくりに使う）
  python3 -B tools/watch_docs.py --state s.json --seconds 150
      たどった途中の状態を s.json に残し、150秒で区切る。たどり終わるまで、くり返し動かす（終了コード 3 は「途中」）。
      たどり終わったら、くらべて結果を出す
  python3 -B tools/watch_docs.py --state s.json --no-head  台帳の資料の掲載が終わったときの HEAD と保存版の確かめを省く（試しのとき）

終了コード：0 巡回できた／2 巡回に失敗した（トップの分類が取れない、取れないページが5%以上、通信の失敗）／3 たどる途中（--seconds）
"""
import argparse
import csv
import glob
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://www.town.shiga-hino.lg.jp/"
HOST = "www.town.shiga-hino.lg.jp"
USER_AGENT = "hino-town-docs-watch/1.0 (+https://github.com/kken78/hino-town-docs)"
TOP = [f"{n}-0-0-0-0-0-0-0-0-0" for n in range(27, 35)]
INTERVAL = 1.0
TIMEOUT = 30
JST = timezone(timedelta(hours=9))
FAIL_RATIO = 0.05

DOC_COLUMNS = ["起点", "掲載ページの名前", "掲載ページの URL", "資料の名前", "資料の URL", "形式", "大きさ", "年度または日付", "メモ"]
PAGE_COLUMNS = ["分類", "ページの名前", "URL", "公開日", "更新日", "資料の数"]
IGNORE_COLUMNS = ["kind", "value", "note"]
IGNORE_KINDS = {"origin", "page", "url", "name"}

FILE_RE = re.compile(r"\.(pdf|xlsx|xls|xlsm|csv|docx|doc|pptx|ppt|zip)$", re.I)
PAGE_RE = re.compile(r"^https://www\.town\.shiga-hino\.lg\.jp/(\d{10})\.html$")
CAT_RE = re.compile(r"/category/(\d+(?:-\d+){9})\.html$")
HREF_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)
CRUMB_RE = re.compile(r"(?is)<(?:nav|div|ol|ul)[^>]*(?:topicpath|breadcrumb|pankuzu)[^>]*>(.*?)</(?:nav|div|ol|ul)>")
TITLE_RE = re.compile(r"(?is)<title>(.*?)</title>")
UPDATED_RE = re.compile(r'itemprop="dateModified"[^>]*datetime="(\d{4}-\d{2}-\d{2})')
POSTED_RE = re.compile(r'itemprop="datePublished"[^>]*datetime="(\d{4}-\d{2}-\d{2})')
SIZE_RE = re.compile(r"([0-9][0-9.,]*\s*[KMG]?B)", re.I)


class Fail(Exception):
    pass


# ---- 取得 ----

_last = [0.0]


def _wait():
    rest = INTERVAL - (time.monotonic() - _last[0])
    if rest > 0:
        time.sleep(rest)
    _last[0] = time.monotonic()


def request(url, method="GET"):
    """(HTTP の状態, 本文の文字)。つながらないときは (-1, 理由)。試し直さない（不変の制約5）。"""
    _wait()
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, ("" if method == "HEAD" else r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return -1, str(e)


# ---- ページの読み取り ----

def clean(t):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))).strip()


def norm(u):
    p = urllib.parse.urlsplit(u.strip())
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def links(base, text):
    out = []
    for h, t in HREF_RE.findall(text):
        out.append((norm(urllib.parse.urljoin(base, html.unescape(h.strip()))), clean(t)))
    return out


def page_title(text):
    m = TITLE_RE.search(text)
    return html.unescape(m.group(1).strip()).split(" | ")[0].strip() if m else ""


def crumbs(text):
    m = CRUMB_RE.search(text)
    if not m:
        return []
    return [clean(t) for _, t in HREF_RE.findall(m.group(1))]


def crumb_trail(cr):
    return " > ".join(c for c in cr if c and c != "ホーム")


def split_label(label):
    """リンクの文字から、資料の名前と大きさを分ける（「名前(.pdf サイズ：1.2MB)」「名前 (PDF形式、1.2MB)」など）。"""
    size = ""
    m = re.search(r"サイズ[：:]\s*([0-9][0-9.,]*\s*[KMG]?B)", label) or re.search(r"(?:PDF|Word|Excel|形式)[^、,]*[、,]\s*([0-9][0-9.,]*\s*[KMG]?B)", label)
    if m:
        size = m.group(1).replace(" ", "")
    name = re.sub(r"\s*[（(]\s*(?:\.?[a-z]{2,4}\s*)?(?:サイズ|PDF形式|Word形式|Excel形式|形式)[^）)]*[）)]\s*", " ", label, flags=re.I)
    name = re.sub(r"\s*[（(]\s*\.?(pdf|docx?|xlsx?|csv|zip|pptx?)\s*$", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(), size


def read_page(url, text):
    files, seen = [], set()
    for u, label in links(url, text):
        p = urllib.parse.urlsplit(u)
        if p.netloc == HOST and FILE_RE.search(p.path) and u not in seen:
            seen.add(u)
            name, size = split_label(label)
            files.append([u, name, size])
    up, po = UPDATED_RE.search(text), POSTED_RE.search(text)
    return {"title": page_title(text), "posted": po.group(1) if po else "", "updated": up.group(1) if up else "",
            "crumbs": crumbs(text), "files": files}


# ---- たどる ----

def new_state():
    return {"cats_todo": TOP[:], "cats_done": {}, "pages_todo": [], "pages_done": {}, "errors": []}


def crawl(st, fetch=None, seconds=None):
    """分類とページをたどる。seconds があれば、その秒数で区切って False を返す（続きは同じ st で）。たどり終えれば True。"""
    fetch = fetch or request
    t0 = time.monotonic()
    while st["cats_todo"] or st["pages_todo"]:
        if seconds is not None and time.monotonic() - t0 >= seconds:
            return False
        if st["cats_todo"]:
            c = st["cats_todo"].pop(0)
            if c in st["cats_done"]:
                continue
            url = f"{SITE}category/{c}.html"
            code, text = fetch(url)
            info = {"code": code, "title": page_title(text) if code == 200 else "", "subcats": [], "pages": []}
            if code == 200:
                for u, _ in links(url, text):
                    m = CAT_RE.search(u)
                    if m and urllib.parse.urlsplit(u).netloc == HOST:
                        k = m.group(1)
                        if k != c and k not in st["cats_done"] and k not in st["cats_todo"]:
                            st["cats_todo"].append(k)
                            info["subcats"].append(k)
                    m = PAGE_RE.match(u)
                    if m:
                        pid = m.group(1)
                        if pid not in info["pages"]:
                            info["pages"].append(pid)
                        if pid not in st["pages_done"] and pid not in st["pages_todo"]:
                            st["pages_todo"].append(pid)
            else:
                st["errors"].append({"kind": "category", "id": c, "code": code, "reason": text[:200] if code == -1 else ""})
            st["cats_done"][c] = info
        else:
            pid = st["pages_todo"].pop(0)
            if pid in st["pages_done"]:
                continue
            url = f"{SITE}{pid}.html"
            code, text = fetch(url)
            if code == 200:
                st["pages_done"][pid] = dict(read_page(url, text), code=200)
            else:
                st["pages_done"][pid] = {"code": code, "title": "", "posted": "", "updated": "", "crumbs": [], "files": []}
                st["errors"].append({"kind": "page", "id": pid, "code": code, "reason": text[:200] if code == -1 else ""})
    return True


def check_crawl(st):
    """§12 の巡回の失敗。失敗なら理由の一覧、そうでなければ空。"""
    bad = []
    for c in TOP:
        info = st["cats_done"].get(c)
        if not info or info["code"] != 200:
            bad.append(f"トップの分類が取れない：{c}（{info['code'] if info else '未取得'}）")
    pages = st["pages_done"]
    errs = [p for p in pages.values() if p["code"] != 200]
    if pages and len(errs) / len(pages) >= FAIL_RATIO:
        bad.append(f"取れないページが {len(errs)}／{len(pages)} 件（5%以上）")
    if not pages:
        bad.append("ページが1件も無い")
    return bad


# ---- 一覧 ----

def tables(st):
    """§7 の資料の一覧とページの一覧の行。ページの URL の順、ページの中はリンクの順。"""
    docs, pages = [], []
    for pid in sorted(st["pages_done"]):
        p = st["pages_done"][pid]
        if p["code"] != 200:
            continue
        url = f"{SITE}{pid}.html"
        trail = crumb_trail(p["crumbs"])
        pages.append({"分類": trail, "ページの名前": p["title"], "URL": url, "公開日": p["posted"], "更新日": p["updated"],
                      "資料の数": str(len(p["files"]))})
        for u, name, size in p["files"]:
            ext = os.path.splitext(urllib.parse.urlsplit(u).path)[1].lstrip(".").lower()
            docs.append({"起点": trail, "掲載ページの名前": p["title"], "掲載ページの URL": url, "資料の名前": name, "資料の URL": u,
                         "形式": ext, "大きさ": size, "年度または日付": p["updated"], "メモ": ""})
    return docs, pages


def read_tsv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def write_tsv(path, columns, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([str(r.get(c, "")).replace("\t", " ").replace("\n", " ") for c in columns])


def latest(prefix, inventory_dir):
    paths = sorted(glob.glob(os.path.join(inventory_dir, f"{prefix}-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].tsv")))
    return paths[-1] if paths else None


# ---- 除外の表 ----

def load_ignore(path):
    if not os.path.exists(path):
        return []
    rows = read_tsv(path)
    errs = []
    for n, r in enumerate(rows, 2):
        if set(IGNORE_COLUMNS) - set(r):
            raise Fail(f"{path} の列が {IGNORE_COLUMNS} でない")
        if r["kind"] not in IGNORE_KINDS:
            errs.append(f"{n}行目：kind が {sorted(IGNORE_KINDS)} のどれでもない（{r['kind']!r}）")
        if not r["value"]:
            errs.append(f"{n}行目：value が空")
        if not r["note"]:
            errs.append(f"{n}行目：note（除く理由）が空")
        if r["kind"] == "name":
            try:
                re.compile(r["value"])
            except re.error as e:
                errs.append(f"{n}行目：正規表現の誤り（{e}）")
    if errs:
        raise Fail(f"{path} の誤り {len(errs)} 件\n" + "\n".join("  " + e for e in errs))
    return rows


def ignored(rules, origin="", page_url="", url="", name="", page_name=""):
    for r in rules:
        k, v = r["kind"], r["value"]
        if k == "origin" and origin.startswith(v):
            return True
        if k == "page" and page_url == v:
            return True
        if k == "url" and (url.startswith(v) or page_url.startswith(v)):
            return True
        if k == "name" and (re.search(v, name or "") or re.search(v, page_name or "")):
            return True
    return False


# ---- くらべる ----

def compare(docs, pages, prev_docs, prev_pages, sources, rules, head=None, archive=None):
    """§12 の3種類の見つけたもの。head(url) は HEAD の状態を返す関数（None なら確かめない）。
    archive(url) は、Webアーカイブのいちばん新しい保存版の保存日時（無ければ ""、確かめられなければ None）を返す関数
    （None なら確かめない。§12・§14）。台帳の removed がある行は、掲載が終わった・掲載ページの更新の対象にしない。"""
    known = {r["資料の URL"] for r in prev_docs} | {r["url"] for r in sources}
    known_pages = {r["URL"] for r in prev_pages}
    page_names = {r["URL"]: r["ページの名前"] for r in pages}
    found = {"new": [], "gone": [], "updated": []}

    seen = set()
    for r in docs:
        u = r["資料の URL"]
        if u in known or u in seen:
            continue
        seen.add(u)
        if ignored(rules, r["起点"], r["掲載ページの URL"], u, r["資料の名前"], r["掲載ページの名前"]):
            continue
        found["new"].append({"kind": "資料", "origin": r["起点"], "page": r["掲載ページの URL"], "page_name": r["掲載ページの名前"],
                             "url": u, "name": r["資料の名前"], "size": r["大きさ"], "updated": r["年度または日付"]})
    for r in pages:
        if r["URL"] in known_pages or ignored(rules, r["分類"], r["URL"], r["URL"], "", r["ページの名前"]):
            continue
        found["new"].append({"kind": "ページ", "origin": r["分類"], "page": r["URL"], "page_name": r["ページの名前"],
                             "url": r["URL"], "name": "", "size": "", "updated": r["更新日"], "files": r["資料の数"]})

    linked = {r["資料の URL"] for r in docs} | {r["URL"] for r in pages}
    for s in sources:
        if (s.get("removed") or "").strip() or s["url"] in linked:
            continue
        x = {"id": s["id"], "title": s["title"], "url": s["url"], "page": s["page_url"], "status": "", "archive": ""}
        if head:
            code = head(s["url"])
            x["status"] = {404: "町のサイトから無くなった（HTTP 404）", 410: "町のサイトから無くなった（HTTP 410）",
                           200: "掲載ページからのリンクが無くなった（ファイルは取れる）"}.get(code, f"確かめられない（HTTP {code}）")
        else:
            x["status"] = "掲載ページからのリンクが無くなった（HEAD は確かめていない）"
        if archive:
            ts = archive(s["url"])
            x["archive"] = (f"https://web.archive.org/web/{ts}id_/{s['url']}" if ts
                            else "保存版は無い" if ts == "" else "保存版を確かめられなかった")
        found["gone"].append(x)

    prev_up = {r["URL"]: r["更新日"] for r in prev_pages}
    now_up = {r["URL"]: r["更新日"] for r in pages}
    ids = {}
    for s in sources:
        if not (s.get("removed") or "").strip():
            ids.setdefault(s["page_url"], []).append(s["id"])
    for pu in sorted(ids):
        was, now = prev_up.get(pu), now_up.get(pu)
        if was is not None and now is not None and was != now:
            found["updated"].append({"page": pu, "page_name": page_names.get(pu, ""), "was": was, "now": now, "ids": ids[pu]})
    return found


# ---- 報告 ----

def report(rep):
    f = rep["findings"]
    L = [f"町のサイトを {rep['date']} に巡回した結果。分類 {rep['categories']} 件、ページ {rep['pages']} 件、資料 {rep['docs']} 件。"
         f"くらべた一覧：{rep['prev_docs'] or '無し'}・{rep['prev_pages'] or '無し'}。", ""]
    L.append(f"## 新しい資料とページ（{len(f['new'])}件）")
    if not f["new"]:
        L.append("- なし")
    by = {}
    for x in f["new"]:
        by.setdefault(x["origin"] or "（起点なし）", []).append(x)
    for origin in sorted(by):
        L.append(f"### {origin}（{len(by[origin])}件）")
        for x in by[origin]:
            if x["kind"] == "資料":
                L.append(f"- 資料：{x['name'] or '（リンクの文字なし）'}{'（' + x['size'] + '）' if x['size'] else ''}　{x['url']}"
                         f"　掲載ページ「{x['page_name']}」{x['page']}（更新日 {x['updated'] or '不明'}）")
            else:
                L.append(f"- ページ：{x['page_name']}　{x['url']}（更新日 {x['updated'] or '不明'}、資料 {x['files']} 件）")
    L += ["", f"## 台帳の資料の掲載が終わった（{len(f['gone'])}件）"]
    L += [f"- {x['id']}：{x['title']}。{x['status']}。{x['url']}（掲載ページ {x['page']}）"
          f"{'。保存版の候補：' + x['archive'] if x.get('archive') else ''}" for x in f["gone"]] or ["- なし"]
    L += ["", f"## 台帳の掲載ページの更新（{len(f['updated'])}件）"]
    L += [f"- {x['page_name'] or x['page']}：更新日 {x['was'] or '（空）'} → {x['now'] or '（空）'}。{x['page']}（台帳の id：{'・'.join(x['ids'])}）"
          for x in f["updated"]] or ["- なし"]
    if rep["errors"]:
        L += ["", f"## 取れなかった分類とページ（{len(rep['errors'])}件）"]
        L += [f"- {e['kind']} {e['id']}：HTTP {e['code']} {e.get('reason', '')}".rstrip() for e in rep["errors"]]
    L += ["", f"作った一覧：{rep['out_docs']}・{rep['out_pages']}"]
    if rep.get("same_day"):
        L.append("同じ日の一覧が data/inventory/ にすでにあるので、この一覧はコミットしない（DOCS_SCHEMA.md §12）")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", help="たどった途中の状態の JSON（続きから動かすとき）")
    ap.add_argument("--seconds", type=int, help="たどるのをこの秒数で区切る（--state と一緒に使う）")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "watch"), help="作った一覧を置くフォルダ")
    ap.add_argument("--json", help="結果の JSON")
    ap.add_argument("--no-head", action="store_true", help="台帳の資料の掲載が終わったときの HEAD と保存版（CDX）の確かめを省く")
    ap.add_argument("--inventory", default=os.path.join(ROOT, "data", "inventory"))
    ap.add_argument("--sources", default=os.path.join(ROOT, "data", "sources.tsv"))
    ap.add_argument("--ignore", default=os.path.join(ROOT, "data", "watch_ignore.tsv"))
    ap.add_argument("--date", help="一覧の日付（既定は今日。JST）")
    a = ap.parse_args(argv)
    if a.seconds is not None and not a.state:
        ap.error("--seconds は --state と一緒に使う")
    try:
        rules = load_ignore(a.ignore)
        sources = read_tsv(a.sources)
        st = new_state()
        if a.state and os.path.exists(a.state):
            with open(a.state, encoding="utf-8") as f:
                st = json.load(f)
        done = crawl(st, seconds=a.seconds)
        if a.state:
            tmp = a.state + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, a.state)
        if not done:
            print(f"たどる途中：分類 {len(st['cats_done'])} 件（残り {len(st['cats_todo'])}）、ページ {len(st['pages_done'])} 件"
                  f"（残り {len(st['pages_todo'])}）。同じ指定でもう一度動かす")
            return 3
        bad = check_crawl(st)
        if bad:
            raise Fail("巡回に失敗した：" + "。".join(bad))
        docs, pages = tables(st)
        day = a.date or datetime.now(JST).strftime("%Y-%m-%d")
        out_docs = os.path.join(a.out, f"town-docs-inventory-{day}.tsv")
        out_pages = os.path.join(a.out, f"town-pages-{day}.tsv")
        write_tsv(out_docs, DOC_COLUMNS, docs)
        write_tsv(out_pages, PAGE_COLUMNS, pages)
        pd, pp = latest("town-docs-inventory", a.inventory), latest("town-pages", a.inventory)
        prev_docs = read_tsv(pd) if pd else []
        prev_pages = read_tsv(pp) if pp else []
        head = None if a.no_head else (lambda u: request(u, "HEAD")[0])
        archive = None
        if not a.no_head:
            import watch_archive

            def archive(u):
                _wait()   # Webアーカイブへの問い合わせも1秒に1件以内（§14）
                return watch_archive.cdx_latest(u)
        found = compare(docs, pages, prev_docs, prev_pages, sources, rules, head, archive)
        rep = {"date": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"), "categories": len(st["cats_done"]),
               "pages": len(pages), "docs": len(docs), "prev_docs": os.path.basename(pd) if pd else "",
               "prev_pages": os.path.basename(pp) if pp else "", "out_docs": out_docs, "out_pages": out_pages,
               "findings": found, "errors": st["errors"],
               "same_day": any(os.path.exists(os.path.join(a.inventory, os.path.basename(x))) for x in (out_docs, out_pages))}
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=1)
        print(report(rep))
        return 0
    except Fail as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
