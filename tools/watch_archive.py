"""保存版の確かめ（DOCS_SCHEMA.md §14）。

台帳 data/sources.tsv の url と、shiga-hino-plans の原典の台帳 data/sources_index.tsv の url について、
Webアーカイブ（web.archive.org）に取得日以後の保存版があるかを一覧（CDX）に問い合わせ、無いものは
Save Page Now に保存を頼む（アカウントは使わない）。結果を archive.tsv と Markdown（と JSON）で出す。
台帳・テキストは変えない。data/archive.tsv も変えない（取り込みの日に、作った archive.tsv をコミットする）。

  python3 -B tools/watch_archive.py                       確かめて、保存版の無いものは頼む（頼むのは --seconds まで）
  python3 -B tools/watch_archive.py --dry-run             確かめるだけで、保存は頼まない
  python3 -B tools/watch_archive.py --plans <ファイル>    shiga-hino-plans の sources_index.tsv を手元のファイルから読む
                                                         （既定は GitHub の main の版）
  python3 -B tools/watch_archive.py --json out.json       結果を JSON にも書く（Actions の Issue づくりに使う）

終了コード：0 確かめられた／2 失敗（一覧への問い合わせの失敗が5%以上、台帳が読めない）
"""
import argparse
import csv
import io
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
USER_AGENT = "hino-town-docs-archive/1.0 (+https://github.com/kken78/hino-town-docs)"
PLANS_URL = "https://raw.githubusercontent.com/kken78/shiga-hino-plans/main/data/sources_index.tsv"
CDX = "https://web.archive.org/cdx/search/cdx"
SAVE = "https://web.archive.org/save/"
CDX_INTERVAL = 1.0      # §14：一覧への問い合わせは1秒に1件以内
SAVE_INTERVAL = 20.0    # §14：保存の依頼は20秒に1件以内（1分に3件以内）
SAVE_SECONDS = 4 * 3600  # §14：1回の実行で頼むのは4時間分まで
CDX_TIMEOUT = 60
SAVE_TIMEOUT = 180
FAIL_RATIO = 0.05
JST = timezone(timedelta(hours=9))
COLUMNS = ["url", "src", "latest", "since", "checked", "status", "fails", "note"]
STATUSES = ("ok", "requested", "failed", "pending")
TS_RE = re.compile(r"/web/(\d{14})")


class Fail(Exception):
    pass


# ---- 通信（試験では差し替える） ----

def http_get(url, timeout):
    """(HTTP の状態, 本文の文字, 見出し)。つながらないときは (-1, 理由, {})。試し直さない。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", dict(e.headers or {})
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return -1, str(e), {}


class Pacer:
    """間を空ける。clock と sleep は試験で差し替える。"""

    def __init__(self, interval, clock=time.monotonic, sleep=time.sleep):
        self.interval, self.clock, self.sleep, self.last = interval, clock, sleep, None

    def wait(self):
        if self.last is not None:
            rest = self.interval - (self.clock() - self.last)
            if rest > 0:
                self.sleep(rest)
        self.last = self.clock()


# ---- 一覧（CDX） ----

def cdx_latest(url, get=None):
    """状態が 200 の保存版の、いちばん新しい保存日時（14桁）。無ければ ""。問い合わせに失敗したら None。"""
    get = get or http_get
    q = urllib.parse.urlencode({"url": url, "output": "json", "fl": "timestamp,statuscode",
                                "filter": "statuscode:200", "limit": "-1"})
    code, body, _ = get(f"{CDX}?{q}", CDX_TIMEOUT)
    if code != 200:
        return None
    body = body.strip()
    if not body:
        return ""
    try:
        rows = json.loads(body)
    except ValueError:
        return None
    rows = [r for r in rows[1:] if r and re.fullmatch(r"\d{14}", r[0])]
    return max((r[0] for r in rows), default="")


def save(url, get=None):
    """('requested'|'failed'|'throttled', 保存日時 or "", note)"""
    get = get or http_get
    code, _, headers = get(SAVE + url, SAVE_TIMEOUT)
    if code == 429:
        return "throttled", "", "Webアーカイブに断られた（HTTP 429）"
    if code in (200, 201, 302):
        loc = headers.get("Content-Location") or headers.get("Location") or ""
        m = TS_RE.search(loc)
        return "requested", (m.group(1) if m else ""), ""
    return "failed", "", f"保存を頼めなかった（HTTP {code}）"


# ---- 台帳 ----

def read_tsv_text(text, name):
    if text.startswith("﻿") or "\r" in text:
        raise Fail(f"{name}：BOM か CR がある")
    return list(csv.DictReader(io.StringIO(text), delimiter="\t"))


def load_text(path_or_url, get=None):
    get = get or http_get
    if re.match(r"https?://", path_or_url):
        code, body, _ = get(path_or_url, CDX_TIMEOUT)
        if code != 200:
            raise Fail(f"{path_or_url} が読めない（HTTP {code}）")
        return body
    with open(path_or_url, encoding="utf-8", newline="") as f:
        return f.read()


def targets(sources, plans):
    """url ごとに1つ。{url: {"src": [...], "since": "YYYY-MM-DD" or ""}}。台帳の removed がある url は除く（§14）"""
    removed = {r["url"] for r in sources if (r.get("removed") or "").strip()}
    t = {}

    def add(url, src, since):
        if not url or url in removed:
            return
        x = t.setdefault(url, {"src": [], "since": None})
        x["src"].append(src)
        # 境の日：取得日。空の行は境なし。両方にあるときは遅いほう
        if since:
            x["since"] = max(x["since"] or "", since)
        elif x["since"] is None:
            x["since"] = ""
    for r in sources:
        add(r["url"], r["id"], r.get("fetched", ""))
    for r in plans:
        if r.get("file"):
            add(r["url"], f"plans:{r['id']}/{r['file']}", r.get("fetched", ""))
    for x in t.values():
        x["since"] = x["since"] or ""
    return t


def is_ok(latest, since):
    return bool(latest) and (not since or latest[:8] >= since.replace("-", ""))


# ---- 本体 ----

def run(sources, plans, prev, today, get=None, dry_run=False, seconds=SAVE_SECONDS,
        clock=time.monotonic, sleep=time.sleep, log=None):
    get = get or http_get
    t = targets(sources, plans)
    prev = {r["url"]: r for r in prev}
    cdx_pace, save_pace = Pacer(CDX_INTERVAL, clock, sleep), Pacer(SAVE_INTERVAL, clock, sleep)
    rows, cdx_errors = {}, []
    for n, url in enumerate(sorted(t)):
        cdx_pace.wait()
        latest = cdx_latest(url, get)
        x = t[url]
        row = {"url": url, "src": "・".join(x["src"]), "since": x["since"], "checked": today, "latest": "", "status": "",
               "fails": "", "note": ""}
        if latest is None:
            cdx_errors.append(url)
            p = prev.get(url, {})
            row.update(latest=p.get("latest", ""), status="pending", fails=p.get("fails", "0") or "0",
                       note="一覧（CDX）に問い合わせられなかった")
        else:
            row["latest"] = latest
            if is_ok(latest, x["since"]):
                row.update(status="ok", fails="0")
        rows[url] = row
        if log and (n + 1) % 100 == 0:
            log(f"一覧を確かめた：{n + 1} / {len(t)}")
    if t and len(cdx_errors) / len(t) >= FAIL_RATIO:
        raise Fail(f"一覧（CDX）への問い合わせの失敗が {len(cdx_errors)} / {len(t)} 件（5%以上）。保存は頼まずに終える")

    need = [u for u in sorted(rows) if not rows[u]["status"]]
    start, throttled = clock(), False
    for url in need:
        row, p = rows[url], prev.get(url, {})
        pf = int(p.get("fails") or 0)
        if dry_run:
            row.update(status="pending", fails=str(pf), note="保存を頼んでいない（--dry-run）")
            continue
        if not throttled:
            save_pace.wait()
        if throttled or clock() - start >= seconds:
            row.update(status="pending", fails=str(pf),
                       note="Webアーカイブに断られたので次の月に回した" if throttled else "時間の上限で次の月に回した")
            continue
        st, ts, note = save(url, get)
        if st == "throttled":
            throttled = True
            row.update(status="pending", fails=str(pf), note=note + "。次の月に回した")
        elif st == "requested":
            row.update(status="requested", fails="0", note=(f"保存日時 {ts}" if ts else ""))
        else:
            row.update(status="failed", fails=str(pf + 1), note=note)
    out = [rows[u] for u in sorted(rows)]
    return out, cdx_errors


def report(rows, cdx_errors, day, plans_from, dry_run):
    by = {s: [r for r in rows if r["status"] == s] for s in STATUSES}
    L = [f"保存版の確かめ（{day}）。対象 {len(rows)} 件（台帳と shiga-hino-plans の原典。台帳の removed がある行は除く）。"
         f"取得日以後の保存版がある {len(by['ok'])} 件、保存を頼んだ {len(by['requested'])} 件、"
         f"失敗 {len(by['failed'])} 件、次の月に回した {len(by['pending'])} 件。", f"shiga-hino-plans の台帳：{plans_from}"]
    if dry_run:
        L.append("--dry-run：保存は頼んでいない")
    L.append("")
    for s, title in (("requested", "保存を頼んだ"), ("failed", "頼んだが失敗した"), ("pending", "次の月に回した")):
        L.append(f"## {title}（{len(by[s])}件）")
        items = by[s]
        if s == "failed":
            items = sorted(items, key=lambda r: -int(r["fails"] or 0))
        L += [f"- {r['src']}：{r['url']}（保存版 {r['latest'] or 'なし'}、境の日 {r['since'] or 'なし'}"
              f"{'、続けて失敗 ' + r['fails'] + 'か月' if s == 'failed' else ''}）{('。' + r['note']) if r['note'] else ''}"
              for r in items[:300]] or ["- なし"]
        if len(items) > 300:
            L.append(f"- ほか {len(items) - 300} 件（全部は archive.tsv にある）")
        L.append("")
    if cdx_errors:
        L.append(f"一覧（CDX）に問い合わせられなかった url：{len(cdx_errors)} 件（5%未満なので続けた）")
    return "\n".join(L)


def write_tsv(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([str(r.get(c, "")).replace("\t", " ").replace("\n", " ") for c in COLUMNS])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default=os.path.join(ROOT, "data", "sources.tsv"))
    ap.add_argument("--plans", default=PLANS_URL, help="shiga-hino-plans の sources_index.tsv（ファイルか URL）")
    ap.add_argument("--prev", default=os.path.join(ROOT, "data", "archive.tsv"), help="前の月の archive.tsv（失敗の続いた月を数える）")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "archive"))
    ap.add_argument("--json", help="結果の JSON")
    ap.add_argument("--dry-run", action="store_true", help="確かめるだけで、保存は頼まない")
    ap.add_argument("--seconds", type=int, default=SAVE_SECONDS, help="保存を頼む時間の上限（秒）")
    ap.add_argument("--date", help="確かめた日（既定は今日。JST）")
    a = ap.parse_args(argv)
    day = a.date or datetime.now(JST).strftime("%Y-%m-%d")
    try:
        with open(a.sources, encoding="utf-8", newline="") as f:
            sources = read_tsv_text(f.read(), a.sources)
        plans = read_tsv_text(load_text(a.plans), a.plans)
        prev = []
        if os.path.exists(a.prev):
            with open(a.prev, encoding="utf-8", newline="") as f:
                prev = read_tsv_text(f.read(), a.prev)
        rows, cdx_errors = run(sources, plans, prev, day, dry_run=a.dry_run, seconds=a.seconds,
                               log=lambda m: print(m, file=sys.stderr))
    except Fail as e:
        print(str(e), file=sys.stderr)
        return 2
    out_tsv = os.path.join(a.out, "archive.tsv")
    write_tsv(out_tsv, rows)
    if a.json:
        by = {s: [r for r in rows if r["status"] == s] for s in STATUSES}
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"date": day, "total": len(rows), "counts": {s: len(v) for s, v in by.items()},
                       "items": [r for r in rows if r["status"] != "ok"], "cdx_errors": cdx_errors,
                       "out": out_tsv}, f, ensure_ascii=False, indent=1)
    print(report(rows, cdx_errors, day, a.plans, a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
