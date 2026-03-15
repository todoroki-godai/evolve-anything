## ADDED Requirements

### Requirement: SKILL.md integration detection
Active pitfall の Root-cause キーワードと対象スキルの SKILL.md 本文を Jaccard 突合し、統合済み候補を検出する（SHALL）。Root-cause 文字列を「—」（em dash）で分割し、後半部分を単語分割・ストップワード除外してキーワード集合を得る（SHALL）。SKILL.md の YAML frontmatter を除外し、セクション（`##` 見出し）単位でトークン集合を生成する（SHALL）。各セクションとの Jaccard 係数がいずれか ≥ INTEGRATION_JACCARD_THRESHOLD（0.3）であれば統合済みと判定する（SHALL）。

#### Scenario: Pitfall integrated into SKILL.md
- **WHEN** Active pitfall の Root-cause が `action — CDK deploy パラメータ不足` で、SKILL.md に「CDK deploy 時は必ずパラメータを確認」という手順がある
- **THEN** Jaccard 類似度 ≥ 0.3 と判定され、`integration_detected: true` フラグが付与される

#### Scenario: Pitfall not yet integrated
- **WHEN** Active pitfall の Root-cause に対応する記述が SKILL.md に見つからない
- **THEN** `integration_detected: false` のまま通常のライフサイクルを継続する

### Requirement: References integration detection
Active pitfall の Root-cause キーワードと同スキルの `references/` 配下ファイル（pitfalls.md 自体を除く）を突合する（SHALL）。各ファイルについてセクション単位で Jaccard 計算を行い、最初に閾値超マッチしたファイルを `integration_target` に記録する（SHALL）。

#### Scenario: Pitfall knowledge in references
- **WHEN** `references/best-practices.md` に pitfall の Root-cause と同一内容が記述されている
- **THEN** `integration_detected: true` フラグが付与され、`integration_target: "references/best-practices.md"` が記録される

#### Scenario: References file matched (pitfalls.md excluded)
- **WHEN** `references/` 配下に `pitfalls.md` と `best-practices.md` があり、`best-practices.md` が閾値超マッチする
- **THEN** `pitfalls.md` は突合対象外とし、`best-practices.md` を `integration_target` に記録する

### Requirement: Auto-graduation proposal
integration_detected が true の Active pitfall に対して、卒業提案を自動生成する（SHALL）。卒業は提案のみで、実行にはユーザー確認が必須。

#### Scenario: Graduation proposal generated
- **WHEN** pitfall_hygiene() で integration_detected=true の Active pitfall が見つかった
- **THEN** `graduation_proposals` に `{pitfall_title, integration_target, confidence}` が追加される

#### Scenario: User confirms graduation
- **WHEN** ユーザーが卒業提案を承認した
- **THEN** pitfall が Graduated に移行し、統合先が記録される
