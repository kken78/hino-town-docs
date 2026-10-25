# データのライセンス

このリポジトリには、作者が整理したものと、日野町の公開資料から写したものが含まれており、それぞれ扱いが異なります。

| 対象 | 該当するもの | 扱い |
|---|---|---|
| プログラム | tools/ | GNU Affero General Public License v3.0 以降（AGPL-3.0-or-later、LICENSE） |
| 台帳と一覧の構造 | data/sources.tsv と data/inventory/ の列の作り方、id、区分、年度、取得日・sha256・ページ数・文字数などの検収の記録、DOCS_SCHEMA.md に定めた決まり | CC BY 4.0 |
| 原典から写した部分 | data/inventory/ と data/sources.tsv の資料名・掲載ページの名前、data/inventory/ のメモのうち掲載ページから写した見出しなど、data/text/・data/text_raw/・data/ocr/ の文字 | このリポジトリのライセンスの対象外 |

原典から写した部分の著作権は、日野町など各資料の発行者にあります。この部分を利用する場合は、各発行者に確かめてください。data/text/ と data/text_raw/ の文字は、原典の PDF から機械で取り出したもので、読み取りの誤りや欠けが含まれることがあります。data/ocr/ の文字は、原典のページの画像を NDLOCR-Lite（国立国会図書館）で機械で読んだもので、数字の桁の違いを含む読み違いがあります。内容は原典で確かめてください。原典の URL は data/sources.tsv にあります。

台帳と一覧の構造を利用する場合は、「hino-town-docs（https://github.com/kken78/hino-town-docs）をもとに作成」のように出典を表示し、CC BY 4.0 であることを示してください。改変した場合は、その旨もあわせて書いてください。
