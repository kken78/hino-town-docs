"""hino-town-docs の構造化の層の道具のうち、固定資産台帳の部分（DOCS_SCHEMA.md §13.14・13.10）。
標準ライブラリだけで動く。

  python3 -B tools/kotei.py mei [<資料の id>]
      資産名称に「宅」「氏」の字のある行（個人の名前らしい字の候補）を一覧にするだけ。表の値は変えない
  python3 -B tools/kotei.py kian [<資料の id>]
      原典の文字 data/text/<資料の id>/01.tsv から kotei-shisan.tsv を起票する（全行。資産名称もすべて写す）。
      取得年度の西暦の読み替えと、1円の行の区分（kubun_1en）を付ける。検算（期首簿価から期末簿価、取得日の年度と
      取得年度、5区分、行のキー）が1行でも合わなければ、何も書かずに、その行を示して終了コード 1。
      施設の表・対応表・対応なしの表が無ければ見出しだけで作る
  python3 -B tools/kotei.py check
      kotei-shisan を原典の文字で照合・検算し（段1）、施設の表・対応表・対応なしの表の形を確かめてから、
      検収の台帳 data/struct/audit/kotei.tsv を書く。表の形が合わなければ何も書かずに止まる。
      段1 で合わない行があれば終了コード 1（台帳は書く）
  python3 -B tools/kotei.py zaimu [<資料の id>] [--zaimu <財務書類の資料の id>]
      A 財務書類との照合。kotei-shisan の一般会計の行を補助科目名称と目的別資産区分で足し、一般会計等の附属明細書の
      「①有形固定資産の明細」「②有形固定資産の行政目的別明細」の額と比べる。目的別資産区分が空の枝番の行は
      枝番0 の区分で、空の行は「その他」の列でまとめる。原典の誤りの記録（issues/<資料の id>.tsv）の where に
      書いた差だけを許し、差と記録の額が合わなければ差を示して終了コード 1
  python3 -B tools/kotei.py selftest
      段1 の道具に、誤りを入れた表と原典を読ませて、誤りを見つけることを確かめる

原典の字と数を写すだけで、直さず、推定で埋めない。列は見出しの字で決め、列の番号では決めない。
"""
import collections
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
from kozo import Stop, ival, join_note, row_key, write_table  # noqa: E402

ROOT = htd.ROOT
TODAY = kozo.TODAY
DOC = "zaisei-r6-kotei"
ZAIMU = {"zaisei-r6-kotei": "zaisei-r6-zaimu"}   # 台帳と同じ年度の財務書類4表
SHEET = "01"
TITLE_ROWS = 2   # 1行目は表の題と作成日、2行目は見出し。3行目からが資産の行

# ---- 13.14 の列 ----

# (列, 原典の見出しの字)。見出しの字が None の列は補い
SHISAN = [
    ("doc", None), ("page", None), ("row", None),
    ("zaisan_no", "財産番号"), ("edaban", "財産枝番"), ("name", "資産名称"), ("kaikei_name", "会計名称"),
    ("shukan", "主管課名称"), ("shozai", "所在地"), ("hojo", "補助科目名称"), ("mokuteki", "目的別資産区分"),
    ("toroku", "資産登録種別"), ("shokyaku", "償却対象区分"), ("biboh", "備忘価額区分"), ("yoto", "資産用途"),
    ("kozo", "資産構造"), ("kaisu", "階数"), ("taiyo", "耐用年数"), ("suryo", "数量"), ("tani", "単位"),
    ("menseki", "面積"), ("shutoku_nendo", "取得年度"), ("shutoku_nendo_y", None), ("shutoku_date", "取得日"),
    ("shutoku_kagaku", "取得価額"), ("kishu", "期首簿価"), ("gensho_ruikei", "減価償却累計額"),
    ("toki_shutoku", "当期取得額"), ("toki_gensho", "当期減価償却額"), ("toki_jobaikyaku", "当期除売却"),
    ("toki_hyoka", "当期評価増減等"), ("kimatsu", "期末簿価"), ("jobaikyaku_nendo", "売却・除却年度"),
    ("jobaikyaku_date", "売却・除却日"), ("kubun_1en", None), ("unit", None), ("note", None),
]
SHISAN_COLS = [c for c, _ in SHISAN]
HEAD = {c: h for c, h in SHISAN if h}
AUDIT_VALUE_COLS = [c for c in SHISAN_COLS if c not in ("doc", "page", "row", "zaisan_no", "edaban", "unit", "note")]
SHISETSU_COLS = ["key", "name", "name_source", "note"]
TAIO_COLS = ["doc", "zaisan_no", "edaban", "name", "shisetsu", "shisetsu_name", "kakunin", "konkyo", "kettei",
             "kettei_date", "moto_doc", "note"]
NASHI_COLS = ["doc", "shisetsu", "atama", "kettei", "kettei_date", "note"]
AUDIT_COLS = kozo.AUDIT_COLS
FILES = {"kotei-shisan": ("kotei-shisan.tsv", SHISAN_COLS), "kotei-shisetsu": ("kotei-shisetsu.tsv", SHISETSU_COLS),
         "kotei-taio": ("kotei-taio.tsv", TAIO_COLS), "kotei-nashi": ("kotei-nashi.tsv", NASHI_COLS)}
SHISETSU_HEADS = ("施設", "施設名称")   # 町が埋めたら止める（13.14 B）
KAKUNIN = ("施設ごと", "行ごと", "引き継ぎ")
KONKYO = ("名前", "別名", "年度", "構造", "所在地", "棟数と面積の和", "組", "資料")
ATAMA_NASHI = ("建物", "土地", "工作物")
HIKITSUGI_COLS = ("name", "shutoku_nendo", "shutoku_date", "shutoku_kagaku")   # 次の年度の台帳とのつなぎ

UNIT_NOTE = "金額の列の見出しと1行目に単位の字が無い"
MEI_CHARS = ("宅", "氏")

ERA = {"明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018}
NENDO_RE = re.compile(r"^(明治|大正|昭和|平成|令和)(元|[0-9]+)年度$")
DATE_RE = re.compile(r"^([0-9]{4})/([0-9]{2})/([0-9]{2})$")
ZAISAN_RE = re.compile(r"^(.+)_([0-9]{7})_([0-9]{1,4})$")
INT_RE = re.compile(r"^-?[0-9]+$")

# 1円の行の区分（13.14。表の順）
KUBUN = ["1円で載せた土地", "1円で載せた償却資産", "償却を終えて1円を残した資産", "価額のある資産", "価額が0の資産"]


def kubun_hits(r):
    """kubun_1en の条件に当たる区分の一覧（償却対象区分・備忘価額区分・取得価額・期末簿価の4つの列だけで決める）。"""
    sk, bb = r["shokyaku"], r["biboh"]
    try:
        a, k = int(r["shutoku_kagaku"]), int(r["kimatsu"])
    except ValueError:
        return []
    out = []
    if sk == "償却対象でない" and a == 1:
        out.append(KUBUN[0])
    if sk == "償却対象" and a == 1:
        out.append(KUBUN[1])
    if sk == "償却対象" and bb == "1円残しする" and a > 1 and k == 1:
        out.append(KUBUN[2])
    if a > 1 and k > 1:
        out.append(KUBUN[3])
    if a == 0 or k == 0:
        out.append(KUBUN[4])
    return out


def nendo_y(s):
    """取得年度の和暦の字を西暦の年度に読み替える。読めなければ None。"""
    m = NENDO_RE.match(s)
    if not m:
        return None
    n = 1 if m.group(2) == "元" else int(m.group(2))
    return ERA[m.group(1)] + n


def date_fy(s):
    """取得日（YYYY/MM/DD の字）の年度（4月から翌年3月まで）。読めなければ None。"""
    m = DATE_RE.match(s)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12:
        return None
    return y if mo >= 4 else y - 1


def atama(zaisan_no):
    m = ZAISAN_RE.match(zaisan_no)
    return m.group(1) if m else ""


def edaban_num(v):
    return int(v) if INT_RE.match(v) else 10 ** 9


# ---- 原典の文字 ----

def sdir(root):
    return os.path.join(root, "data", "struct")


class Daicho:
    """固定資産台帳の原典の文字（§5.2 の TSV）。行は Excel の行の番号で引く。"""

    def __init__(self, root, doc):
        self.doc = doc
        path = os.path.join(root, "data", "text", doc, SHEET + ".tsv")
        if not os.path.exists(path):
            raise Stop(f"{os.path.relpath(path, root)} が無い")
        with open(path, encoding="utf-8", newline="") as f:
            lines = f.read().split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        self.grid = [ln.split("\t") for ln in lines]
        if len(self.grid) < TITLE_ROWS:
            raise Stop(f"{doc}：見出しの行が無い")
        head = self.grid[1]
        self.col = {}
        for c, h in list(HEAD.items()) + [(x, x) for x in SHISETSU_HEADS]:
            hits = [i for i, x in enumerate(head) if x == h]
            if len(hits) != 1:
                raise Stop(f"{doc}：2行目の見出し「{h}」が {len(hits)} 回ある（1回でない）")
            self.col[c] = hits[0]

    def value(self, excel_row, c):
        r = self.grid[excel_row - 1]
        i = self.col[c]
        return r[i] if i < len(r) else ""

    def rows(self):
        """資産の行の Excel の行の番号（3行目から最後まで。値の無い行は除く）。"""
        return [i + 1 for i in range(TITLE_ROWS, len(self.grid)) if any(x != "" for x in self.grid[i])]


def mei_candidates(d):
    """資産名称に「宅」「氏」の字のある行（13.14 原典の字の扱い）。"""
    out = []
    for er in d.rows():
        nm = d.value(er, "name")
        hit = "".join(ch for ch in MEI_CHARS if ch in nm)
        if hit:
            zn = d.value(er, "zaisan_no")
            out.append(dict(row=str(er), zaisan_no=zn, edaban=d.value(er, "edaban"), atama=atama(zn), hit=hit, name=nm))
    return out


# ---- 表の読み書き ----

def load(root, name):
    fn, cols = FILES[name]
    path = os.path.join(sdir(root), fn)
    return htd.read_tsv(path, cols) if os.path.exists(path) else []


def ensure_empty_tables(root, out=print):
    """施設の表・対応表・対応なしの表が無ければ、見出しだけで作る（13.14 あとでやること 1）。"""
    for name in ("kotei-shisetsu", "kotei-taio", "kotei-nashi"):
        fn, cols = FILES[name]
        path = os.path.join(sdir(root), fn)
        if not os.path.exists(path):
            write_table(path, cols, [])
            out(f"{os.path.relpath(path, root)} を見出しだけで作った")


# ---- 起票 ----

def build(root, doc, d=None):
    """資産の行の値（kotei-shisan）を作る。資産名称もすべて写す（13.14 原典の字の扱い）。"""
    d = d or Daicho(root, doc)
    rows = []
    for er in d.rows():
        r = dict(doc=doc, page=SHEET, row=str(er))
        for c in HEAD:
            r[c] = d.value(er, c)
        notes = [UNIT_NOTE]
        y = nendo_y(r["shutoku_nendo"])
        r["shutoku_nendo_y"] = str(y) if y is not None else ""
        if y is None:
            notes.append(f"取得年度「{r['shutoku_nendo']}」は西暦の年度に読み替えられない")
        hits = kubun_hits(r)
        r["kubun_1en"] = hits[0] if len(hits) == 1 else ""
        r["unit"] = ""
        r["note"] = join_note(*notes)
        rows.append(r)
    return rows


def run_checks(rows, d):
    """kian の検算と、段1 の規則（13.14 検収）。不一致の一覧 [(row の id, 種類, 説明)] を返す。"""
    fails = []
    shisetsu = [er for er in d.rows() if any(d.value(er, x) for x in SHISETSU_HEADS)]
    if shisetsu:
        raise Stop(f"{d.doc}：台帳の「施設」「施設名称」の列に値のある行が {len(shisetsu)} ある（Excel の行 "
                   + "・".join(map(str, shisetsu[:10])) + "）。13.14 B のとおり止めて報告する")
    by_key = collections.defaultdict(list)
    for er in d.rows():
        by_key[(d.value(er, "zaisan_no"), d.value(er, "edaban"))].append(er)
    for (zn, eb), ers in by_key.items():
        if not zn or not eb:
            fails.append((f"{d.doc}/{zn}/{eb}", "キー", f"財産番号か財産枝番が空（Excel の行 {ers}）"))
        elif not ZAISAN_RE.match(zn) or not INT_RE.match(eb):
            fails.append((f"{d.doc}/{zn}/{eb}", "キー", f"財産番号か財産枝番の形が違う（Excel の行 {ers}）"))
        if len(ers) > 1:
            fails.append((f"{d.doc}/{zn}/{eb}", "キー", f"組が原典に {len(ers)} 回ある（Excel の行 {ers}）"))
    seen = collections.Counter((r["doc"], r["zaisan_no"], r["edaban"]) for r in rows)
    counts = collections.Counter()
    for r in rows:
        rid = f"{r['doc']}/{r['zaisan_no']}/{r['edaban']}"
        if seen[(r["doc"], r["zaisan_no"], r["edaban"])] > 1:
            fails.append((rid, "キー", "表に組が2回以上ある"))
        ers = by_key.get((r["zaisan_no"], r["edaban"]), [])
        if len(ers) != 1 or str(ers[0]) != r["row"]:
            fails.append((rid, "キー", f"組が原典の行 {r['row']} に1回だけある、になっていない（原典の行 {ers}）"))
            continue
        er = ers[0]
        for c in HEAD:
            if r[c] != d.value(er, c):
                fails.append((rid, "値", f"{c}「{r[c]}」が原典の「{d.value(er, c)}」と違う"))
        y, fy = nendo_y(d.value(er, "shutoku_nendo")), date_fy(d.value(er, "shutoku_date"))
        if r["shutoku_nendo_y"] != (str(y) if y is not None else "") or y is None:
            fails.append((rid, "年度", f"shutoku_nendo_y「{r['shutoku_nendo_y']}」が取得年度「{d.value(er, 'shutoku_nendo')}」の読み替え（{y}）と違う"))
        if fy is None or y != fy:
            fails.append((rid, "年度", f"取得日「{d.value(er, 'shutoku_date')}」の年度（{fy}）が取得年度の読み替え（{y}）と違う"))
        src = {c: d.value(er, c) for c in HEAD}
        hits = kubun_hits(src)
        if len(hits) != 1:
            fails.append((rid, "区分", f"1円の行の区分の条件に {len(hits)} 当たる（{'・'.join(hits) or 'なし'}）"))
        elif r["kubun_1en"] != hits[0]:
            fails.append((rid, "区分", f"kubun_1en「{r['kubun_1en']}」が条件で決まる区分「{hits[0]}」と違う"))
        else:
            counts[hits[0]] += 1
        try:
            k = ival(src["kishu"]) + ival(src["toki_shutoku"]) - ival(src["toki_gensho"]) - ival(src["toki_jobaikyaku"]) + ival(src["toki_hyoka"])
            if k != ival(src["kimatsu"]):
                fails.append((rid, "簿価", f"期首簿価＋当期取得額−当期減価償却額−当期除売却＋当期評価増減等＝{k} が期末簿価 {src['kimatsu']} と違う"))
        except ValueError as e:
            fails.append((rid, "簿価", f"金額の列に整数でない値「{e}」がある"))
        if r["unit"] != "":
            fails.append((rid, "値", "unit が空でない"))
    in_rows = {(r["zaisan_no"], r["edaban"]) for r in rows if r["doc"] == d.doc}
    for (zn, eb), ers in by_key.items():
        if (zn, eb) not in in_rows:
            fails.append((f"{d.doc}/{zn}/{eb}", "キー", f"原典の行 {ers} が表に無い"))
    n = sum(1 for r in rows if r["doc"] == d.doc)
    if sum(counts.values()) != n or n != len(d.rows()):
        fails.append((f"{d.doc}", "区分", f"5つの区分の行の合計 {sum(counts.values())}、表の行 {n}、原典の資産の行 {len(d.rows())} が同じでない"))
    return fails, counts


def show_fails(fails, out, limit=50):
    kinds = collections.Counter(k for _, k, _ in fails)
    out("合わなかった項目 " + str(len(fails)) + ("（" + "・".join(f"{k} {v}" for k, v in sorted(kinds.items())) + "）" if fails else ""))
    for rid, k, msg in fails[:limit]:
        out(f"  [{k}] {rid}：{msg}")
    if len(fails) > limit:
        out(f"  ほか {len(fails) - limit}")


def kian(root, doc=DOC, out=print):
    d = Daicho(root, doc)
    rows = build(root, doc, d)
    fails, counts = run_checks(rows, d)
    out(f"{doc}：資産の行 {len(rows)}")
    if fails:
        show_fails(fails, out)
        out("検算が合わないので、何も書いていない。終了コード 1")
        return 1
    others = [r for r in load(root, "kotei-shisan") if r["doc"] != doc]
    allrows = sorted(others + rows, key=lambda r: (r["doc"], int(r["row"])))
    path = os.path.join(sdir(root), FILES["kotei-shisan"][0])
    write_table(path, SHISAN_COLS, allrows)
    ensure_empty_tables(root, out)
    out("1円の行の区分")
    for k in KUBUN:
        out(f"  {k}\t{counts[k]}")
    out(f"  計\t{sum(counts.values())}")
    out("頭の字ごとの行")
    for k, v in collections.Counter(atama(r["zaisan_no"]) for r in rows).most_common():
        out(f"  {k}\t{v}")
    out(f"{os.path.relpath(path, root)}（{len(allrows)} 行）を書いた。終了コード 0")
    return 0


# ---- 施設の表・対応表・対応なしの表の形（13.14 検収） ----

def form_fails(t):
    fails = []
    shisan = {(r["doc"], r["zaisan_no"], r["edaban"]): r for r in t["kotei-shisan"]}
    shisetsu = {}
    for r in t["kotei-shisetsu"]:
        if not re.fullmatch(r"s[0-9]{4}", r["key"]):
            fails.append(f"施設の表：キー「{r['key']}」が s と4桁の形でない")
        if r["key"] in shisetsu:
            fails.append(f"施設の表：キー {r['key']} が2回ある")
        if not r["name"] or not r["name_source"]:
            fails.append(f"施設の表：{r['key']} の name か name_source が空")
        shisetsu[r["key"]] = r
    seen = {}
    for r in t["kotei-taio"]:
        k = (r["doc"], r["zaisan_no"], r["edaban"])
        w = f"対応表：{'/'.join(k)}"
        s = shisan.get(k)
        if not s:
            fails.append(f"{w} の kotei-shisan の行が無い")
        elif s["name"] != r["name"]:
            fails.append(f"{w} の name が kotei-shisan の name と違う")
        if k in seen:
            fails.append(f"{w} が2つ以上の施設に対応する（{seen[k]}・{r['shisetsu']}）")
        seen[k] = r["shisetsu"]
        f = shisetsu.get(r["shisetsu"])
        if not f:
            fails.append(f"{w} の施設のキー {r['shisetsu']} が施設の表に無い")
        elif f["name"] != r["shisetsu_name"]:
            fails.append(f"{w} の shisetsu_name が施設の表の name と違う")
        for c in ("kakunin", "konkyo", "kettei", "kettei_date"):
            if not r[c]:
                fails.append(f"{w} の {c} が空")
        if r["kakunin"] and r["kakunin"] not in KAKUNIN:
            fails.append(f"{w} の kakunin「{r['kakunin']}」が決まりに無い")
        if r["konkyo"] and any(x not in KONKYO for x in r["konkyo"].split("・")):
            fails.append(f"{w} の konkyo「{r['konkyo']}」に決まりに無い字がある")
        if r["kettei_date"] and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", r["kettei_date"]):
            fails.append(f"{w} の kettei_date が YYYY-MM-DD でない")
        if r["kakunin"] == "引き継ぎ":
            m = [x for x in t["kotei-taio"] if (x["doc"], x["zaisan_no"], x["edaban"]) == (r["moto_doc"], r["zaisan_no"], r["edaban"])]
            mo = shisan.get((r["moto_doc"], r["zaisan_no"], r["edaban"]))
            if len(m) != 1 or m[0]["shisetsu"] != r["shisetsu"]:
                fails.append(f"{w} の引き継ぎの元（{r['moto_doc']}）に同じ組・同じ施設の対応の行が無い")
            elif not s or not mo or any(s[c] != mo[c] for c in HIKITSUGI_COLS):
                fails.append(f"{w} の引き継ぎで、資産名称・取得年度・取得日・取得価額の4つが元の台帳と同じでない")
        elif r["moto_doc"]:
            fails.append(f"{w} は引き継ぎでないのに moto_doc がある")
    taio_set = {(r["doc"], r["shisetsu"], atama(r["zaisan_no"])) for r in t["kotei-taio"]}
    nseen = set()
    for r in t["kotei-nashi"]:
        k = (r["doc"], r["shisetsu"], r["atama"])
        w = f"対応なしの表：{'/'.join(k)}"
        if k in nseen:
            fails.append(f"{w} が2回ある")
        nseen.add(k)
        if r["atama"] not in ATAMA_NASHI:
            fails.append(f"{w} の atama が建物・土地・工作物でない")
        if r["shisetsu"] not in shisetsu:
            fails.append(f"{w} の施設のキーが施設の表に無い")
        if not r["kettei"] or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", r["kettei_date"]):
            fails.append(f"{w} の kettei か kettei_date が無い")
        if k in taio_set:
            fails.append(f"{w} と同じ組の対応表の行がある")
    return fails


# ---- 段1 ----

def load_all(root):
    return {name: load(root, name) for name in FILES}


def audit_rows(rows, fails, old_ledger, today=TODAY):
    bad = collections.defaultdict(set)
    for rid, k, _ in fails:
        bad[rid].add(k)
    old = {r["key"]: r for r in old_ledger}
    out = []
    for r in rows:
        rid = f"{r['doc']}/{r['zaisan_no']}/{r['edaban']}"
        cols = ",".join(AUDIT_VALUE_COLS)
        value = "|".join(r[c] for c in AUDIT_VALUE_COLS)
        source = f"{r['doc']} {r['page']}!R{r['row']}"
        key = row_key("kotei-shisan", rid, cols, value, r["unit"], source)
        ok = rid not in bad
        new = dict(key=key, table="kotei-shisan", row=rid, cols=cols, value=value, unit=r["unit"], source=source,
                   checked="✓" if ok else "", reviewer="auto:check", date=today,
                   note="" if ok else "段1 不一致：" + "・".join(sorted(bad[rid])))
        o = old.get(key)
        if o:
            if o["reviewer"] and not o["reviewer"].startswith("auto:"):
                for c in ("checked", "reviewer", "date", "note"):
                    new[c] = o[c]
            elif o["checked"] == new["checked"] and o["reviewer"] == new["reviewer"]:
                new["date"] = o["date"]
        out.append(new)
    return out


def stage1(root, t, sources=None):
    """段1 の照合と検算。(fails, counts の辞書) を返す。sources は {資料の id: Daicho}。"""
    sources = sources or {}
    fails, counts = [], {}
    for doc in sorted({r["doc"] for r in t["kotei-shisan"]}):
        d = sources.get(doc) or Daicho(root, doc)
        rows = [r for r in t["kotei-shisan"] if r["doc"] == doc]
        f, c = run_checks(rows, d)
        fails += f
        counts[doc] = c
    return fails, counts


def check(root, out=print, today=TODAY):
    t = load_all(root)
    if not t["kotei-shisan"]:
        raise Stop("data/struct/kotei-shisan.tsv が無いか空（先に kian）")
    ff = form_fails(t)
    if ff:
        raise Stop("施設の表・対応表・対応なしの表の形が合わない（何も書いていない）：" + "／".join(ff[:20]))
    fails, counts = stage1(root, t)
    path = os.path.join(sdir(root), "audit", "kotei.tsv")
    old = htd.read_tsv(path, AUDIT_COLS) if os.path.exists(path) else []
    rows = audit_rows(t["kotei-shisan"], fails, old, today)
    write_table(path, AUDIT_COLS, rows)
    ok = sum(1 for r in rows if r["checked"] == "✓")
    for doc, c in counts.items():
        out(f"{doc}：1円の行の区分 " + "、".join(f"{k} {c[k]}" for k in KUBUN) + f"（計 {sum(c.values())}）")
    out(f"施設の表 {len(t['kotei-shisetsu'])}・対応表 {len(t['kotei-taio'])}・対応なしの表 {len(t['kotei-nashi'])}：表の形は合う")
    show_fails(fails, out)
    code = 1 if fails else 0
    out(f"台帳：{os.path.relpath(path, root)}（{len(rows)} 行。通った {ok}、通らなかった {len(rows) - ok}）。終了コード {code}")
    return code


# ---- A 財務書類との照合（13.14） ----

HOJO = {   # 補助科目名称 → 明細の区分
    "土地": ("事業用資産", "土地"), "建物": ("事業用資産", "建物"), "工作物": ("事業用資産", "工作物"),
    "建設仮勘定": ("事業用資産", "建設仮勘定"),
    "その他（公共土地）": ("インフラ資産", "土地"), "道路（公共土地）": ("インフラ資産", "土地"),
    "公園（公共土地）": ("インフラ資産", "土地"), "その他（公共建物）": ("インフラ資産", "建物"),
    "その他（公共工作物）": ("インフラ資産", "工作物"), "公共用財産建設仮勘定": ("インフラ資産", "建設仮勘定"),
    "物品": ("物品", ""),
}
NOT_TANGIBLE = ("ソフトウェア",)   # 有形固定資産でないので比べない
# 附属明細書（様式第5号）の有形固定資産の明細と行政目的別明細の行の順（統一的な基準の様式）
MEISAI_ROWS = [("事業用資産", ""), ("事業用資産", "土地"), ("事業用資産", "立木竹"), ("事業用資産", "建物"),
               ("事業用資産", "工作物"), ("事業用資産", "船舶"), ("事業用資産", "浮標等"), ("事業用資産", "航空機"),
               ("事業用資産", "その他"), ("事業用資産", "建設仮勘定"), ("インフラ資産", ""), ("インフラ資産", "土地"),
               ("インフラ資産", "建物"), ("インフラ資産", "工作物"), ("インフラ資産", "その他"), ("インフラ資産", "建設仮勘定"),
               ("物品", ""), ("合計", "")]
NON_DEPR = ("土地", "立木竹", "建設仮勘定")   # 減価償却しない区分（明細の E が「-」）
SEC_TITLE = {"附属明細書": "一般会計等", "全体附属明細書": "全体", "連結附属明細書": "連結"}
TOK_RE = re.compile(r"^(-|[0-9]{1,3}(?:,[0-9]{3})*)$")


def tok_num(tok):
    return 0 if tok == "-" else int(tok.replace(",", ""))


def num_line(ln, n):
    toks = ln.split()
    if len(toks) == n and all(TOK_RE.match(x) for x in toks):
        return toks
    return None


def label(k):
    return k[0] + (" " + k[1] if k[1] else "")


def read_meisai(root, zdoc):
    """財務書類の原典の文字（data/text_raw/）から、附属明細書の①（会計の範囲ごと）と②（一般会計等）を読む。
    {"①": {範囲: {区分: (A,B,C,D,E,F,G)}}, "②": {"cols": [...], "rows": {区分: [...]}}}"""
    path = os.path.join(root, "data", "text_raw", zdoc + ".txt")
    if not os.path.exists(path):
        raise Stop(f"{os.path.relpath(path, root)} が無い")
    with open(path, encoding="utf-8") as f:
        lines = [x.strip() for x in f.read().replace("\f", "\n").split("\n")]
    out = {"①": {}, "②": None, "skip": []}
    sec = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln in SEC_TITLE:
            sec = SEC_TITLE[ln]
        if ln.startswith("①有形固定資産の明細") and "（単位：円）" in ln and sec == "連結":
            out["skip"].append("連結（団体・会計ごとの列の形で、D・E・G の列が無い）")
        elif ln.startswith("①有形固定資産の明細") and "（単位：円）" in ln:
            j = i + 1
            while j < len(lines) and num_line(lines[j], 7) is None:
                j += 1
            vals = []
            while j < len(lines) and len(vals) < len(MEISAI_ROWS):
                t = num_line(lines[j], 7)
                if t is None:
                    break
                vals.append([tok_num(x) for x in t])
                j += 1
            if sec is None or len(vals) != len(MEISAI_ROWS):
                raise Stop(f"{zdoc}：{i + 1}行目の①有形固定資産の明細の数の行が {len(vals)}（{len(MEISAI_ROWS)} でない）か、会計の範囲が分からない")
            out["①"][sec] = dict(zip(MEISAI_ROWS, vals))
            i = j
            continue
        if ln.startswith("②有形固定資産の行政目的別明細") and "（単位：円）" in ln:
            if sec != "一般会計等":
                raise Stop(f"{zdoc}：{i + 1}行目の②行政目的別明細が一般会計等の附属明細書の中に無い")
            j, head = i + 1, []
            while j < len(lines) and not any(TOK_RE.match(x) for x in lines[j].split()[:1]):
                head.append(lines[j])
                j += 1
            cols = " ".join(head).replace("・ ", "・").split()
            vals = []
            while j < len(lines) and len(vals) < len(MEISAI_ROWS):
                t = num_line(lines[j], len(cols))
                if t is None:
                    break
                vals.append([tok_num(x) for x in t])
                j += 1
            if not cols or cols[-1] != "合計" or len(vals) != len(MEISAI_ROWS):
                raise Stop(f"{zdoc}：{i + 1}行目の②行政目的別明細の列の見出し（{cols}）か数の行（{len(vals)}）が読めない")
            out["②"] = {"cols": cols, "rows": dict(zip(MEISAI_ROWS, vals))}
            i = j
            continue
        i += 1
    return out


def meisai_fails(m):
    """明細の中の検算（行の順の読みの確かめ）。D＝A＋B−C、G＝D−E、区分の和＝親、E が「-」の区分、②の合計の列＝①の G。
    ②の目的の列の和と合計の列の食い違いは、読みの誤りでなく原典の中の食い違いでありうるので、ここでは止めず zaimu が示す。"""
    bad = []
    for sec, rows in m["①"].items():
        for k, v in rows.items():
            a, b, c, d, e, f, g = v
            if a + b - c != d or d - e != g:
                bad.append(f"{sec} {label(k)}：D＝A＋B−C か G＝D−E が合わない")
            if k[1] in NON_DEPR and (e or f):
                bad.append(f"{sec} {label(k)}：減価償却しない区分なのに E か F が 0 でない")
        for parent in ("事業用資産", "インフラ資産"):
            sub = [v for k, v in rows.items() if k[0] == parent and k[1]]
            if [sum(x) for x in zip(*sub)] != rows[(parent, "")]:
                bad.append(f"{sec} {parent}：内訳の和が合わない")
        tot = [x + y + z for x, y, z in zip(rows[("事業用資産", "")], rows[("インフラ資産", "")], rows[("物品", "")])]
        if tot != rows[("合計", "")]:
            bad.append(f"{sec} 合計：事業用資産＋インフラ資産＋物品が合わない")
    if m["②"]:
        g1 = m["①"].get("一般会計等", {})
        for k, v in m["②"]["rows"].items():
            if g1 and g1[k][6] != v[-1]:
                bad.append(f"② {label(k)}：合計が①の差引本年度末残高（G）と合わない")
    return bad


KAIKEI = "一般会計"          # 比べる行の会計名称（13.14 範囲）
MEISAI = "一般会計等"        # 比べる附属明細書
SONOTA = "その他"            # 目的別資産区分が空の行を比べる②の列（13.14）
ISSUE_HEAD = "A 財務書類との照合 "
ISSUE_STATUS = ("未判断", "原典のまま持つ")
COL1 = ("取得価額", "減価償却累計額", "期末簿価")
ISSUE_ITEM_RE = re.compile(r"^(①|②)(.+?) 差 (-?[0-9][0-9,]*)$")


def mokuteki_of(r, base):
    """②でまとめる目的。空の枝番の行は枝番0 の区分、空の行は「その他」（13.14）。"""
    m = r["mokuteki"]
    if m == "" and r["edaban"] != "0":
        b = base.get((r["doc"], r["zaisan_no"]))
        if b is not None:
            m = b["mokuteki"]
    return m or SONOTA


def read_zaimu_issues(root, doc, shisan_ids):
    """原典の誤りの記録のうち A の照合の差を許す行を読む。{区分: [(no, 額)]} と、形の誤りの一覧を返す。"""
    path = os.path.join(sdir(root), "issues", doc + ".tsv")
    allow, bad = collections.defaultdict(list), []
    if not os.path.exists(path):
        return allow, bad
    for r in htd.read_tsv(path, kozo.ISSUE_COLS):
        if r["status"] not in ISSUE_STATUS or not r["where"].startswith(ISSUE_HEAD):
            continue
        body, _, tail = r["where"][len(ISSUE_HEAD):].partition("。")
        m = re.fullmatch(r"kotei-shisan (\S+)", tail.strip())
        if not m:
            bad.append(f"issues/{doc}.tsv no.{r['no']}：where の「。」のあとに kotei-shisan の row が無い")
        elif m.group(1) not in shisan_ids:
            bad.append(f"issues/{doc}.tsv no.{r['no']}：row {m.group(1)} が kotei-shisan に無い")
        for item in body.split("、"):
            x = ISSUE_ITEM_RE.match(item.strip())
            if not x:
                bad.append(f"issues/{doc}.tsv no.{r['no']}：「{item}」が「<区分> 差 <額>」の形でない")
                continue
            if x.group(1) == "①":
                lab, _, col = x.group(2).rpartition(" ")
                if col not in COL1 or not any(label(k) == lab and k[1] for k in MEISAI_ROWS):
                    bad.append(f"issues/{doc}.tsv no.{r['no']}：「{item}」の①の区分が明細の内訳の行と列でない")
                    continue
            allow[x.group(1) + x.group(2)].append((r["no"], int(x.group(3).replace(",", ""))))
    return allow, bad


def zaimu(root, doc=DOC, zdoc=None, out=print):
    zdoc = zdoc or ZAIMU.get(doc)
    if not zdoc:
        raise Stop(f"{doc} と比べる財務書類の資料の id が決まっていない（--zaimu で渡す）")
    t = load_all(root)
    alldoc = [r for r in t["kotei-shisan"] if r["doc"] == doc]
    if not alldoc:
        raise Stop(f"data/struct/kotei-shisan.tsv に {doc} の行が無い（先に kian）")
    rows = [r for r in alldoc if r["kaikei_name"] == KAIKEI]
    m = read_meisai(root, zdoc)
    mb = meisai_fails(m)
    if mb:
        raise Stop(f"{zdoc} の明細の読みが検算で合わない：" + "／".join(mb))
    if MEISAI not in m["①"] or not m["②"]:
        raise Stop(f"{zdoc} に{MEISAI}の①有形固定資産の明細か②行政目的別明細が無い")
    unknown = sorted({r["hojo"] for r in rows if r["hojo"] not in HOJO and r["hojo"] not in NOT_TANGIBLE})
    if unknown:
        raise Stop(f"補助科目名称 {unknown} の明細の区分が 13.14 の表に無い")
    allow, ibad = read_zaimu_issues(root, doc, {f"{r['doc']}/{r['zaisan_no']}/{r['edaban']}" for r in alldoc})
    if ibad:
        raise Stop("原典の誤りの記録の形が合わない：" + "／".join(ibad))
    tang = [r for r in rows if r["hojo"] not in NOT_TANGIBLE]
    agg = collections.defaultdict(lambda: [0, 0, 0, 0])

    def add(k, r):
        s = agg[k]
        s[0] += 1
        s[1] += ival(r["shutoku_kagaku"])
        s[2] += ival(r["gensho_ruikei"])
        s[3] += ival(r["kimatsu"])
    for r in tang:
        k = HOJO[r["hojo"]]
        add(k, r)
        if k[1]:
            add((k[0], ""), r)
        add(("合計", ""), r)

    def allowed(cell):
        return sum(v for _, v in allow.get(cell, []))

    def allowed1(k, ci):
        """①の区分 k・列 ci の許す差。親の行は内訳の行の和。"""
        if k[1]:
            return allowed(f"①{label(k)} {COL1[ci]}")
        kids = [x for x in MEISAI_ROWS if x[1] and (k[0] == "合計" or x[0] == k[0])]
        return sum(allowed(f"①{label(x)} {COL1[ci]}") for x in kids)

    used = set()
    diffs = []
    out(f"A 財務書類との照合：{doc}（{KAIKEI}の行。ソフトウェア {len(rows) - len(tang)} 行を除く {len(tang)} 行。"
        f"ほかの会計の行 {len(alldoc) - len(rows)} は比べない）と {zdoc} の{MEISAI}の附属明細書")
    out("①有形固定資産の明細（台帳は取得価額・減価償却累計額・期末簿価の和、明細は D・E・G。差＝台帳−明細。許す差は原典の誤りの記録の額）")
    out("  区分\t行\t台帳 取得価額\t明細 D\t差\t台帳 減価償却累計額\t明細 E\t差\t台帳 期末簿価\t明細 G\t差\t許す差（D・E・G）")
    for k, v in m["①"][MEISAI].items():
        s = agg.get(k, [0, 0, 0, 0])
        dd = (s[1] - v[3], s[2] - v[4], s[3] - v[6])
        al = tuple(allowed1(k, ci) for ci in range(3))
        if k[1]:
            used.update(f"①{label(k)} {c}" for c in COL1)
        if s[0] == 0 and not any(v) and not any(al):
            continue
        out(f"  {label(k)}\t{s[0]}\t{s[1]:,}\t{v[3]:,}\t{dd[0]:,}\t{s[2]:,}\t{v[4]:,}\t{dd[1]:,}\t{s[3]:,}\t{v[6]:,}\t{dd[2]:,}"
            + (f"\t{al[0]:,}・{al[1]:,}・{al[2]:,}" if any(al) else ""))
        if dd != al:
            diffs.append(("①", label(k), s[0], tuple(a - b for a, b in zip(dd, al))))
    cols = m["②"]["cols"]
    tot = m["②"]["rows"][("合計", "")]
    base = {(r["doc"], r["zaisan_no"]): r for r in alldoc if r["edaban"] == "0"}
    mk = collections.defaultdict(lambda: [0, 0])
    moved = 0
    for r in tang:
        g = mokuteki_of(r, base)
        if r["mokuteki"] == "" and r["edaban"] != "0" and g != SONOTA:
            moved += 1
        mk[g][0] += 1
        mk[g][1] += ival(r["kimatsu"])
    for k, v in m["②"]["rows"].items():
        if sum(v[:-1]) != v[-1]:
            out(f"注：②の {label(k)} の行で、目的の列の和 {sum(v[:-1]):,} が合計の列 {v[-1]:,} と {v[-1] - sum(v[:-1]):,} 違う（明細の中の食い違い）")
    empty = sum(1 for r in tang if mokuteki_of(r, base) == SONOTA and r["mokuteki"] == "")
    out(f"②有形固定資産の行政目的別明細（台帳は目的ごとの期末簿価の和、明細は合計の行。差＝台帳−明細。"
        f"空の枝番の行で枝番0 の区分に入れた行 {moved}、空のまま「{SONOTA}」に入れた行 {empty}）")
    out("  目的\t行\t台帳 期末簿価\t明細\t差\t許す差")
    names = list(cols[:-1]) + sorted(x for x in mk if x not in cols)
    for c in names:
        n, s = mk.get(c, [0, 0])
        v = tot[cols.index(c)] if c in cols else 0
        al = allowed("②" + c)
        used.add("②" + c)
        out(f"  {c}\t{n}\t{s:,}\t{v:,}\t{s - v:,}\t{al:,}" + ("" if c in cols else "\t明細に列が無い"))
        if s - v != al:
            diffs.append(("②", c, n, (s - v - al,)))
    s_all = sum(x[1] for x in mk.values())
    al = allowed1(("合計", ""), 2)
    out(f"  合計\t{sum(x[0] for x in mk.values())}\t{s_all:,}\t{tot[-1]:,}\t{s_all - tot[-1]:,}\t{al:,}")
    if s_all - tot[-1] != al:
        diffs.append(("②", "合計", len(tang), (s_all - tot[-1] - al,)))
    for cell in sorted(set(allow) - used):
        diffs.append(("記録", cell, 0, (sum(v for _, v in allow[cell]),)))
        out(f"  記録の区分「{cell}」が明細の区分と目的に無い（" + "・".join(f"no.{n}" for n, _ in allow[cell]) + "）")
    if allow:
        out("原典の誤りの記録で許した差：" + "、".join(f"{c} {sum(v for _, v in xs):,}（" + "・".join(f"issues/{doc}.tsv no.{n}" for n, _ in xs) + "）"
                                        for c, xs in sorted(allow.items())))
    code = 1 if diffs else 0
    out(f"合わなかった区分 {len(diffs)}（差は許す差を引いた残り）。終了コード {code}")
    for d in diffs:
        out(f"  {d[0]} {d[1]}：行 {d[2]}、残りの差 " + "・".join(f"{x:,}" for x in d[3]))
    return code, diffs


# ---- 段1 の道具の確かめ ----

MUTATIONS = ["表の値を変える", "原典の値を変える", "取得年度の読み替えを変える", "区分を変える", "行の番号を変える", "表の行を消す"]
PER_KIND = 5


def spread(rows, n=PER_KIND):
    if len(rows) <= n:
        return list(rows)
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


def bump(v):
    if INT_RE.match(v):
        return str(int(v) + 1)
    return v + "X"


def selftest(root=ROOT, out=print):
    tmp = tempfile.mkdtemp(prefix="kotei-selftest-")
    try:
        if load(root, "kotei-shisan"):
            data_root = root
            out("data/struct/kotei-shisan.tsv で確かめる（表は書き換えない）")
        else:
            import test_kotei
            test_kotei.make_sample(tmp)
            kian(tmp, test_kotei.SAMPLE_DOC, out=lambda *a: None)
            data_root = tmp
            out("data/struct/ に表が無いので、tools/test_kotei.py の見本で確かめる")
        t = load_all(data_root)
        docs = sorted({r["doc"] for r in t["kotei-shisan"]})
        src = {doc: Daicho(data_root, doc) for doc in docs}
        base, _ = stage1(data_root, t, src)
        base_bad = {rid for rid, _, _ in base}
        rows = [r for r in t["kotei-shisan"] if f"{r['doc']}/{r['zaisan_no']}/{r['edaban']}" not in base_bad]
        rid = lambda r: f"{r['doc']}/{r['zaisan_no']}/{r['edaban']}"  # noqa: E731
        total = [0] * len(MUTATIONS)
        found = [0] * len(MUTATIONS)
        missed = []

        def flagged(t2, s2, r):
            f, _ = stage1(data_root, t2, s2)
            return any(x[0] == rid(r) for x in f)

        def find(t2, r):
            return [x for x in t2["kotei-shisan"] if rid(x) == rid(r)][0]
        cols = [c for c in HEAD if c != "name"]
        for i, r in enumerate(spread(rows, PER_KIND * 3)):   # 0. 表の値（列を順に変える）
            c = cols[i % len(cols)]
            t2 = copy.deepcopy(t)
            x = find(t2, r)
            x[c] = bump(x[c])
            total[0] += 1
            if flagged(t2, src, r):
                found[0] += 1
            else:
                missed.append(f"{MUTATIONS[0]}：{rid(r)} の {c}")
        for i, r in enumerate(spread(rows, PER_KIND * 3)):   # 1. 原典の値
            c = cols[(i * 7) % len(cols)]
            s2 = dict(src)
            d2 = copy.copy(src[r["doc"]])
            d2.grid = [list(g) for g in src[r["doc"]].grid]
            er = int(r["row"])
            line = d2.grid[er - 1]
            ci = d2.col[c]
            while len(line) <= ci:
                line.append("")
            line[ci] = bump(line[ci])
            s2[r["doc"]] = d2
            total[1] += 1
            if flagged(t, s2, r):
                found[1] += 1
            else:
                missed.append(f"{MUTATIONS[1]}：{rid(r)} の {c}")
        for r in spread(rows):   # 2. 取得年度の読み替え
            t2 = copy.deepcopy(t)
            x = find(t2, r)
            x["shutoku_nendo_y"] = bump(x["shutoku_nendo_y"])
            total[2] += 1
            if flagged(t2, src, r):
                found[2] += 1
            else:
                missed.append(f"{MUTATIONS[2]}：{rid(r)}")
        for r in spread(rows):   # 3. 区分
            t2 = copy.deepcopy(t)
            x = find(t2, r)
            x["kubun_1en"] = KUBUN[(KUBUN.index(x["kubun_1en"]) + 1) % len(KUBUN)]
            total[3] += 1
            if flagged(t2, src, r):
                found[3] += 1
            else:
                missed.append(f"{MUTATIONS[3]}：{rid(r)}")
        for r in spread(rows):   # 4. 行の番号
            t2 = copy.deepcopy(t)
            x = find(t2, r)
            x["row"] = str(int(x["row"]) + 1)
            total[4] += 1
            if flagged(t2, src, r):
                found[4] += 1
            else:
                missed.append(f"{MUTATIONS[4]}：{rid(r)}")
        for r in spread(rows):   # 5. 表の行を消す
            t2 = copy.deepcopy(t)
            t2["kotei-shisan"] = [x for x in t2["kotei-shisan"] if rid(x) != rid(r)]
            total[5] += 1
            if flagged(t2, src, r):
                found[5] += 1
            else:
                missed.append(f"{MUTATIONS[5]}：{rid(r)}")
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


# ---- 個人の名前らしい字の候補の一覧 ----

def mei(root, doc=DOC, out=print):
    """候補の行を一覧にするだけ（13.14。資産名称は写す。施設へのまとめや別の資料との照合で個人と結び付けない）。"""
    d = Daicho(root, doc)
    cands = mei_candidates(d)
    by = collections.Counter((c["atama"], c["hit"]) for c in cands)
    out(f"{doc}：資産名称に「宅」「氏」の字のある行 {len(cands)}（候補の一覧。表の値は変えない）")
    for (a, h), n in sorted(by.items()):
        out(f"  {a}\t「{h}」\t{n}")
    for c in cands:
        out("\t".join([c["row"], c["zaisan_no"], c["edaban"], c["hit"], c["name"]]))
    return 0


def main(argv):
    args = argv[1:]
    if not args:
        print(__doc__)
        return 2
    cmd, rest = args[0], args[1:]
    opts, pos = {}, []
    multi = set()
    i = 0
    while i < len(rest):
        if rest[i].startswith("--"):
            if i + 1 >= len(rest):
                print(__doc__)
                return 2
            if rest[i] in multi:
                opts.setdefault(rest[i], []).append(rest[i + 1])
            else:
                opts[rest[i]] = rest[i + 1]
            i += 2
        else:
            pos.append(rest[i])
            i += 1
    doc = pos[0] if pos else DOC
    if any(k != "--zaimu" or cmd != "zaimu" for k in opts):
        print(__doc__)
        return 2
    if cmd == "mei":
        return mei(ROOT, doc)
    if cmd == "kian":
        return kian(ROOT, doc)
    if cmd == "check" and not pos:
        return check(ROOT)
    if cmd == "zaimu":
        return zaimu(ROOT, doc, opts.get("--zaimu"))[0]
    if cmd == "selftest" and not pos:
        return selftest()
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Stop as e:
        print(f"止めた：{e}", file=sys.stderr)
        sys.exit(2)
