# ADR-043: hardcoded_value の doc 文脈 URL/ARN は allowlist 拡張でなく文脈抑制で落とす

- Status: Accepted
- Date: 2026-06-08
- Issue: #359（hardcoded_value がドキュメント本文の URL/ARN を過剰検出）
- Related: #352（正規 API URL の FP 除外 / `_OFFICIAL_API_URL_RE` 導入）, #337（Slack doc ID 除外）

## 背景（症状）

evolve の `hardcoded_value` 検出が、SKILL.md の手順説明や例示コマンド中の
URL・ARN を「抽出すべきハードコード設定値」として proposable に挙げる。

実 FP（sys-bots 実 evolve, proposable 9件中の大半）:

- `https://api.slack.com/apps`（手順「1. ... にアクセス」）→ service_url
- `https://slack.com/oauth/v2/authorize` → service_url
- 例示 curl/aws コマンド中の `arn:aws:secretsmanager:...` → aws_arn

散文・手順例の URL/ARN は設定値ではなく参照・例示。高 confidence の
`service_url`(0.55) / `aws_arn`(0.75) が proposable 上位を占め、本来抽出すべき
設定値ハードコードを埋没させる（#359 の主訴）。

## 根本原因

`_should_exclude` の FP 除外が pattern_type ごとに非対称で、`numeric_id` だけ
version/timestamp/arithmetic 等の手厚い除外を持つ一方、`service_url`/`aws_arn` は
`_is_safe_url`（localhost/example.com + #352 の公式 API URL）以外を素通りさせる。
**「値が秘匿らしいか」だけ見て「行が設定値の文脈か（散文・手順・例示か）」を見ない**
ため、doc 文脈の URL/ARN が漏れる。

## 検討した選択肢

### A 単独. allowlist 拡張のみ（却下）

公式・公開エンドポイント（`api.slack.com/`, `slack.com/oauth/`, …）を
`_OFFICIAL_API_URL_RE` に列挙し続ける。

却下理由:
- **モグラ叩き**。`slack.com/apps` を足しても次に `developer.slack.com` /
  `slack.com/help` が出る。公式ドメインの個別パス列挙は終わらない。
- **ARN FP に一切効かない**。例示 ARN は allowlist 対象外。しかも ARN は
  confidence 0.75 で service_url より上位＝埋没を最も悪化させる種類を残す。

### C. 代入文脈のみ検出に反転（却下）

`KEY=val` / `key: val` 右辺にあるときだけ検出。

却下理由:
- recall を大きく削る破壊的変更。
- URL/ARN は自身が `:` を多数含むため「代入区切りの `:`」と「値内の `:`」の
  分離が正規表現で脆く、誤爆源になる。

### A+B. allowlist 最小拡張 + doc 文脈ブラックリスト抑制（採用）

除外理由を 2 つに**直交分離**する:

- **A（ドメインが秘匿でないと確定）**: `_OFFICIAL_API_URL_RE` に
  `api.slack.com/`・`slack.com/oauth/` を追加（公開・非秘匿のみ。以後の個別パス
  列挙はしない方針を docstring に明記）。
- **B（行の文脈が散文）**: `_is_doc_prose_context(line)` =
  手順番号行 `^\s*\d+\.` ＋ 例示コマンド行（行頭 `$`/`>` プロンプト・
  `curl`/`wget`・`aws <subcommand>`）。該当行の `service_url`/`aws_arn` を抑制。

## 決定

**A+B を採用。** doc 文脈の判別シグナルは **手順番号 + 例示コマンド行頭のみ**を
使い、**markdown bullet（`- *`）と非代入判定は採らない**。

理由:
- bullet は `- webhook: https://hooks.slack.com/services/...` 形式の本物 secret を
  取りこぼす FN リスクがある。実 FP は全て「手順番号 or 例示コマンド」に収まる
  ので、カバーに必要な最小集合だけ採る。
- 手順番号行・例示コマンド行は `key: value` 代入と**構文的に交わらない**ため、
  `resource: arn:aws:lambda:...`（test_aws_arn）や `webhook: https://hooks.slack...`
  （test_service_url）の代入文脈検出を構造的に壊さない。

precision 優先。文脈フィルタは高 confidence の `service_url`/`aws_arn` のみに適用し
（`_DOC_CONTEXT_SUPPRESSED`）、`api_key`（本物 token は文脈無関係に秘匿）と低
confidence の `numeric_id` には適用しない。proposable は confidence でソートされ
人間は上位 N 件しか見ないため、上位を FP が占める＝「検出はしているが届かない」
実質 FN を防ぐことが effective recall も上げる。

## トレードオフ / 既知の限界

- 例示コマンド行に含まれる **裸の 12 桁 account 番号は `numeric_id`(0.45) として
  なお検出されうる**（doc 文脈抑制を numeric_id に広げていない）。低 confidence で
  proposable 上位に来ないため本 fix のスコープ外とする。
- 例示コマンドの判定は行内に `curl`/`wget`/`aws <sub>` を含むかで見る。継続行
  （`--secret-id arn:...` のみの折返し行）に ARN がある稀なケースは取り逃す。実害が
  出れば signal を緩める（別 issue）。
- 手順番号行に本物の webhook secret を書く稀な doc は抑制される（FN）。設定値を
  手順番号で書くのは異例なので許容する。
