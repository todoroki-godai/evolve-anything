---
date: 2026-05-29
status: accepted
---
# pitfall-curate は「フォーマット収束」路線（seed + normalize）を採る — 寛容パーサの際限ない拡張はしない

## Context

[ADR-026](026-pitfall-curate-vs-pitfall-manager.md) で pitfall-curate を PJ非依存スキルとして新設後、
実 PJ でドッグフードしたところ、pitfalls.md のフォーマットが PJ ごとに大きく断片化していることが判明した:

- **atlas-breeaders** (`atlas-browser`): `### N.` 番号付きエントリ + `## Active` / `## New` セクション +
  `**Last-seen**: … | **Pre-flight**: …` インラインパイプ + `- **症状**/**対策**` 内容バレット。日本語主体。
- **sys-bots** (`aws-deploy`): `## N.` 番号付き H2 エントリ（ライフサイクルセクション無し、各エントリが
  `**Status**: Active` を自前保持）+ インラインパイプ・メタdata + 散文/コード本文。既に5カテゴリファイルに手分割。
- **docs-platform**: 正準フォーマット（`## Active Pitfalls` / `### [タイトル]` / `- **Status**:`）だが
  中身は `<!-- -->` コメント内テンプレートのみの空ひな型。
- **figma-to-code**: 既に Level2 index + Level3 カテゴリファイルの3段階開示へ進化済み（別構造）。

正準スキーマ前提の初期実装は、これら実ファイルで dedup が機能せず（合成 fixture の緑テストは
false confidence だった）、目的（重複排除・配布版生成）を果たせなかった。

## Decision

**「あらゆる形式を飲み込む寛容パーサを際限なく拡張する」のではなく、正準フォーマット1つへ収束させる**路線を採る。

1. **パーサの耐性は足切りラインを設ける**（実 PJ で頻出する範囲まで）:
   - セクション見出しの fuzzy match（`## Active` / `## New`→Candidate / `## Graduated`）
   - `## N.` 番号付き H2 エントリ（番号なしの構造見出しは拾わない）
   - メタdata 2形式: `- **K**: v` バレット / `**K**: v | **K**: v` インラインパイプ
   - `<!-- -->` コメントブロックのスキップ（空ひな型を phantom エントリ化しない）
   - 日本語 dedup: 空白区切りトークン + CJK 文字 bigram、`Root-cause` 不在時は本文 fallback
2. **これを超える形式差は `normalize` で正準形へ寄せてから curate する**。`normalize` は構造
   （見出しレベル・セクション・メタdataのバレット化）だけを揃え、本文の散文/コードは保持する冪等変換。
3. **新規PJは `seed` で正準ひな型を配る**（docs-platform が実績を作っている方式）。実エントリが
   溜まれば無改修で dedup/distill/sync が回る。

## Alternatives considered

- **寛容パーサを際限なく拡張**: PJ ごとの bespoke 形式（今日 sys-bots、明日また別形式）を追い続ける
  イタチごっこになり、パーサが肥大化・脆弱化する。却下。
- **正準フォーマット限定（retrofit しない）**: 既存 PJ の pitfalls がそのまま使えず、「全PJで使える型」
  という出発点（ADR-026 Context）を裏切る。却下。
- 収束路線は両者の中間: 足切りまでは飲み込み、それ以上は `normalize` で1回寄せる。

## Consequences

- `seed` / `normalize` サブコマンドを追加。`normalize` は冪等（正準→正準で不変）。
- フォーマット I/O 層を `scripts/parse.py`（parse/seed/normalize）に分離、curate ロジックは
  `scripts/core.py`。file-size-budget 遵守（core.py 569→373行）。
- 実機検証: sys-bots `pitfalls-infra.md` 17件を normalize→正準再パース→dedup で2件の重複候補抽出。
- 日本語コーパスは CJK bigram jaccard で 0.1-0.2 帯に分布するため dedup デフォルト閾値を 0.12 に設定
  （英単語主体は 0.3-0.4 が適切、`--threshold` で調整）。
- in-place 上書き前に diff をユーザーに提示する運用とする（破壊的変換のため）。
- **`normalize` は H1 タイトルの説明文とファイル先頭のプリアンブル散文を保持する**。初期実装は
  両者を捨てていた（実 PJ sys-bots/atlas で消失を発見、合成 fixture は preamble/説明的 H1 を
  持たず round-trip 緑のまま見逃した — false confidence の典型）。`_split_header` で抽出して再付与する。
- normalize の既知の制限: セクション見出しの注釈（`## New（未検証 — 再発で Active 昇格）` の
  括弧書き）は正準セクション名が固定のため失われる。
- **`### サブ見出し`問題**: atlas は番号付き `### N.` をエントリに、番号なし `### 真の原因` 等を
  1エントリ内の小見出しに使う。初期実装は後者もエントリ扱いし4つの幽霊エントリを生んだ（22→18）。
  `_demote_subsection_headings` で「番号付き `### N.` が在る文書に限り、番号なし `### ` を `#### `
  へ降格」する。番号は保持されるので冪等（正準形＝番号なしエントリのみは降格しない）。番号なし `## `
  の非セクション pitfall（atlas のボトムシート項）は依然足切りされ前エントリへ折り込まれる → 手動で
  番号付きエントリ化する（足切りを超える形式は normalize でなく手動整形、の原則どおり）。
- **normalize の wipe ガード**: sys-bots の `pitfalls.md` はエントリでなくインデックス/TOC
  （テーブル + category ファイルへのリンク）。これに normalize をかけるとテーブルが全足切りされ
  空セクションへ wipe される事故が判明。`_count_orphan_content_lines` で「エントリ0件 & 実質
  コンテンツ > 3行」を検出し `ValueError` で中断する（空ひな型はプレースホルダ/コメントのみで
  0行に近く誤検出しない）。インデックスは normalize 対象外、category ファイルにだけ掛ける運用。
- **dedup は recall 寄り**: atlas で 6 ペア検出したが全て「クリック不能」の語彙を共有する別個の
  pitfall（対象要素・原因・対策が別）で、真の重複は0だった。閾値 0.12 は人間レビュー用候補出しで
  あり、語彙重複ドメインでは偽陽性が出る前提で運用する（supersede は agent が精読して判断）。
- figma の既存 TS / 3段階開示構造の置換は依然スコープ外（[ADR-026](026-pitfall-curate-vs-pitfall-manager.md) 踏襲）。

## References

- 実装: `skills/pitfall-curate/scripts/{core.py,parse.py,pitfall_curate.py}`, `SKILL.md`
- 関連 ADR: [026 pitfall-curate vs pitfall_manager](026-pitfall-curate-vs-pitfall-manager.md)
- 学習: 合成 fixture の false confidence（実コーパスでドッグフード必須）
