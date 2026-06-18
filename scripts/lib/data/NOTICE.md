# scripts/lib/data — 同梱データの出典

## common_english_words.txt

- 出典: [google-10000-english](https://github.com/first20hours/google-10000-english)
  の `google-10000-english-no-swears.txt`
- 由来: Google Web Trillion Word Corpus（最も頻出する英単語 ~10,000 語）
- ライセンス: public domain
- 用途: `glossary_drift.py` の jargon 候補から「一般英単語」を辞書ベースで除外する
  （`.lower()` が本リストに含まれる token は PJ 固有 jargon ではないと判定）。
  stoplist の手動 denylist 個別列挙（モグラ叩き）を卒業するためのデータ（#567）。
- 形式: 1 行 1 語・すべて小文字。

更新する場合は再取得:

    curl -sSL "https://raw.githubusercontent.com/first20hours/google-10000-english/master/google-10000-english-no-swears.txt" -o common_english_words.txt
