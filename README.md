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
| `data/inventory/` | 町のサイトで調べた資料の一覧（調べた日ごと。取得する前の一覧） |
| `data/sources.tsv` | 取得した資料の台帳 |
| `data/text/` | 原典から取り出した文字（pdftotext） |
| `data/text_raw/` | 同じ原典から、PDF の中で書かれた順に取り出した文字（pdftotext -raw。表や縦書きの段で文がつながりやすい） |
| `data/ocr/` | ページの画像から機械で読んだ文字（OCR の下書き。数字の桁の違いを含む読み違いがあり、数字の出典には使わない） |

文字は、原典の文字・OCR の下書き・校正済みの読みの3つの層に分けて置く（[DOCS_SCHEMA.md](DOCS_SCHEMA.md) §9）。OCR の下書きは、国立国会図書館の [NDLOCR-Lite](https://github.com/ndl-lab/ndlocr-lite)（CC BY 4.0。同梱の学習済みモデルは DINOv3 License に従う）で作る。

## ライセンス

対象ごとに扱いが異なる。詳しくは [LICENSE-DATA.md](LICENSE-DATA.md) を参照。

- プログラム（tools/）：GNU Affero General Public License v3.0 以降（AGPL-3.0-or-later、[LICENSE](LICENSE)）
- 台帳と一覧の構造（列の作り方、id、検収の記録）：CC BY 4.0
- 原典の資料名・本文から写したもの（一覧と台帳の資料名、`data/text/`・`data/text_raw/`・`data/ocr/` の文字）：このリポジトリのライセンスの対象外（著作権は日野町など各発行者にある）

Copyright (C) 2026 Kenjiro HIGASHI
