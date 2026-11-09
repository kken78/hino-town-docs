# hino-town-docs（日野町の公開資料の台帳）

滋賀県日野町のホームページで公開されている資料のうち、計画と議会以外のもの（予算・決算・財政、監査、教育委員会の会議録、広報ひのなど）と、施設や事業ごとの構想・計画のうち shiga-hino-plans で扱わないもの、shiga-hino-plans が持たない計画の前の版（[DOCS_SCHEMA.md](DOCS_SCHEMA.md) §3 の jigyo）、防災の資料のうち計画でないもの（指定避難所の一覧など。§3 の bosai）について、原典の所在と中身を確認できる形で記録する（非公式）。

- 原典の PDF などは、このリポジトリに入れない。町のサイトの URL と、取得したときの sha256 を台帳に記録する
- 原典から機械で取り出した文字（テキスト）を入れる。論点ノート（hino-ronten）などが、版を固定して読むため
- 計画は shiga-hino-plans、議会（会議録・議会だより）は hino-council-search と council-dashboard で扱う。ここでは扱わない（施設や事業ごとの構想・計画で shiga-hino-plans の計画の一覧に無いものと、shiga-hino-plans が持たない計画の前の版は、jigyo としてここで扱う）

| 文書 | 内容 |
| --- | --- |
| [DOCS_SCHEMA.md](DOCS_SCHEMA.md) | 資料の id、台帳の列、テキストの形、取得と検収の決まり |
| [CLAUDE.md](CLAUDE.md) | 作業の手順と制約 |
| [LICENSE-DATA.md](LICENSE-DATA.md) | データの扱い |

## 中身

| 場所 | 内容 |
| --- | --- |
| `data/inventory/` | 町のサイトで調べた資料の一覧とページの一覧（調べた日ごと。取得する前の一覧） |
| `data/watch_ignore.tsv` | 巡回の Issue に出さないものの表（様式・入札・議会など） |
| `data/sources.tsv` | 取得した資料の台帳 |
| `data/text/` | 原典から取り出した文字（PDF は pdftotext。Excel はシートごとの TSV を `data/text/<id>/` に、町のサイトのページは本文の文字） |
| `data/text_raw/` | 同じ原典から、PDF の中で書かれた順に取り出した文字（pdftotext -raw。表や縦書きの段で文がつながりやすい） |
| `data/ocr/` | ページの画像から機械で読んだ文字（OCR の下書き。数字の桁の違いを含む読み違いがあり、数字の出典には使わない） |
| `data/struct/` | 補正予算・決算・財政指標の数字と名前を、回と行のキーで持つ表と、その検収の台帳（構造化の層。下の「構造化の層」。いまは文書だけで、表はまだ無い） |

文字は、原典の文字・OCR の下書き・校正済みの読みの3つの層に分けて置く（[DOCS_SCHEMA.md](DOCS_SCHEMA.md) §9）。OCR の下書きは、国立国会図書館の [NDLOCR-Lite](https://github.com/ndl-lab/ndlocr-lite)（CC BY 4.0。同梱の学習済みモデルは DINOv3 License に従う）で作る。

## 構造化の層

補正予算の概要・決算の資料・財政状況資料集の数字と名前を、回と行のキーで指せる表にして `data/struct/` に置く（[DOCS_SCHEMA.md](DOCS_SCHEMA.md) §13）。原典の文字の層とは別の層で、原典を見て起票し、行ごとに検収する。

| 表 | 中身 |
| --- | --- |
| `hosei-kai.tsv`・`hosei-shiryo.tsv` | 補正予算の回（年度・会計・号。例 `r6-ippan-03`）と、回と資料のページの対応 |
| `hosei-kan.tsv`・`hosei-maru.tsv`・`hosei-jiko.tsv`・`hosei-kaikei.tsv` | 款の表の行、歳出の主な内容の○の行と特別会計・企業会計の主な内容の行、繰越明許費・債務負担行為・地方債の事項、会計ごとの額 |
| `kessan-kai.tsv`・`kessan-gaku.tsv`・`kessan-uchiwake.tsv` | 決算の回（年度・会計。例 `r6-ippan`）と、会計ごとの歳入歳出の額、一般会計の内訳 |
| `zaisei-shihyo.tsv`・`zaisei-futsu.tsv`・`zaisei-ichi.tsv` | 財政状況資料集の指標（年度を列にする）、普通会計の状況、値の原典の中の位置 |
| `audit/`・`issues/` | 検収の台帳と、原典の誤りの記録 |

- `python3 -B tools/kozo.py hosei-kian r6`：令和6年度の補正予算の概要の原典の文字（`data/text_raw/`）から、補正予算の6つの表を起票する。既存の行の key は保つ。道具が値を出せなかった列で人が書いた値（回の表の label_mae・label_hosei、主な内容の行の amount・sub、事項の amount、款の行の額）も保つ（DOCS_SCHEMA.md §13.9・§13.11）
- `python3 -B tools/kozo.py hosei-check r6`：表を原典の文字で照合・検算し（段1）、検収の台帳 `data/struct/audit/hosei-r6.tsv` を書く。合わない項目があれば終了コード 1。H2・H4 の抜き取りの印も付ける
- `python3 -B tools/kozo.py selftest`：表を一時の場所に写して誤りを1つずつ入れ、段1 が見つけることを確かめる（表が無ければ検査の見本で確かめる）
- `python3 -B tools/kozo.py zaisei-kian`：財政状況資料集10本のセルの値と図形の字から、`zaisei-shihyo.tsv`・`zaisei-futsu.tsv`・`zaisei-ichi.tsv` を起票する。値の位置は見出しの字で決め、当たらないものは空にして note に書く
- `python3 -B tools/kozo.py zaisei-check`：財政指標の表をセルの値と図形の字で照合・検算し（段1）、検収の台帳 `data/struct/audit/zaisei.tsv` を書く。合わない項目があれば終了コード 1。図形の字で一致しなかった行は段2 に回し、H2・H4 の抜き取りの印も付ける
- `python3 -B tools/kozo.py zaisei-selftest`：セルの値・前年度の列・図形の字・label_cell に誤りを1つずつ入れ、段1 が見つけることを確かめる
- `python3 -B tools/kozo.py kessan-kian`：決算審査意見書・決算の概要・財政状況資料集のシート03 から、決算の回・額・一般会計の内訳の3つの表を起票する（tools/kozo_kessan.py）。既存の行の key と、画像で確かめた行の値と note は保つ
- `python3 -B tools/kozo.py kessan-check`：決算の表を原典の文字で照合し、選ばなかった出典と丸めの範囲で突き合わせ（段1）、検収の台帳 `data/struct/audit/kessan.tsv` を書く。合わない項目があれば終了コード 1。段2 に回す行と H2・H4 の抜き取りの印も付ける
- `python3 -B tools/kozo.py kessan-selftest`：決算の表を一時の場所に写して誤りを1つずつ入れ、段1 が見つけることを確かめる（表が無ければ検査の見本で確かめる）

検収は shiga-hino-plans の段1〜段4 を写して使う。論点ノート（hino-ronten）は、検収の済んだ行だけを、`hs:`・`hj:`・`hk:`・`hz:` のキーで指す。いまは文書だけを入れてあり、道具と表はこれから作る（令和6年度の補正予算から）。

## 町のサイトの巡回

毎月1日 7:00 JST に、GitHub Actions（`.github/workflows/watch.yml`）が町のサイトの全体をたどり（約40分。1秒に1件以内）、資料の一覧とページの一覧を作り直して、`data/inventory/` のいちばん新しい一覧と台帳 `data/sources.tsv` とくらべる。見つけたものは、種類ごとに Issue になる（[DOCS_SCHEMA.md](DOCS_SCHEMA.md) §12）。

| Issue | 意味 | すること |
| --- | --- | --- |
| 新しい資料とページ N件 | 前の一覧にも台帳にも無い資料とページ（除外の表 `data/watch_ignore.tsv` に当たるものは除く） | 取り込む（依頼の TSV → `python3 -B tools/htd.py fetch`）、除外の表に足す、今は決めない、のどれかを決める |
| 台帳の資料が外れた N件 | 台帳の url が、どのページからもリンクされていない | 差し替えの版を探す。元の行は消さず note に書く |
| 台帳の掲載ページの更新 N件 | 台帳の掲載ページの更新日が変わった | ページを見て、資料の版が変わっていれば新しい id で取り直す |
| 巡回に失敗した | トップの分類が取れない、取れないページが5%以上 | 手元で `python3 -B tools/watch_docs.py` を動かして原因を見る |

巡回は見つけるだけで、台帳・テキスト・一覧は変えない。作った一覧は実行の成果物（artifact、90日）に残る。毎月1日の巡回のあとを「取り込みの日」とし、Issue を片付けてから一覧を `data/inventory/` にコミットする（Issue に手順がある）。コミットした一覧が、次の巡回でくらべる相手になる。取り込んだら、同じ日に hino-ronten と hino-budget の inputs.lock.json を上げる。

必要な設定：Settings → Actions → General → Workflow permissions を「Read and write permissions」にする。手で動かすときは Actions の画面の watch から「Run workflow」。`keepalive.yml` が毎月1日に定期実行を有効化し直す（60日間動きが無いと定期実行が止まるため）。

## ライセンス

対象ごとに扱いが異なる。詳しくは [LICENSE-DATA.md](LICENSE-DATA.md) を参照。

- プログラム（tools/）：GNU Affero General Public License v3.0 以降（AGPL-3.0-or-later、[LICENSE](LICENSE)）
- 台帳と一覧の構造（列の作り方、id、検収の記録）：CC BY 4.0
- 原典の資料名・本文から写したもの（一覧と台帳の資料名、`data/text/`・`data/text_raw/`・`data/ocr/` の文字、`data/struct/` の表のうち原典から写した数と名前）：このリポジトリのライセンスの対象外（著作権は日野町など各発行者にある）。`data/struct/` のキーと列の作り方は、台帳の構造と同じく CC BY 4.0

Copyright (C) 2026 Kenjiro HIGASHI
